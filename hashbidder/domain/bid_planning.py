"""Pure bid planning: diff a desired config against current bids."""

from dataclasses import dataclass
from enum import Enum

from hashbidder.domain.bid_config import BidConfig, SetBidsConfig
from hashbidder.domain.hashrate import Hashrate, HashratePrice, HashUnit
from hashbidder.domain.sats import Sats
from hashbidder.domain.time_unit import TimeUnit
from hashbidder.domain.upstream import Upstream
from hashbidder.domain.user_bid import BidStatus, UserBid

# Canonical units for price comparison.
_PRICE_HASH_UNIT = HashUnit.PH
_PRICE_TIME_UNIT = TimeUnit.DAY

MANAGEABLE_STATUSES = frozenset({BidStatus.ACTIVE, BidStatus.CREATED})


class CancelReason(Enum):
    """Why an existing bid is being canceled."""

    UNMATCHED = "no matching config entry"
    UPSTREAM_MISMATCH = "upstream mismatch (cannot edit upstream)"
    TOO_MANY_BIDS = "too many bids open, we want fewer"


@dataclass(frozen=True)
class EditAction:
    """An existing bid that needs field updates."""

    bid: UserBid
    new_price: HashratePrice
    new_speed_limit_ph: Hashrate

    @property
    def price_changed(self) -> bool:
        """Whether the price differs."""
        return self.bid.price.to(_PRICE_HASH_UNIT, _PRICE_TIME_UNIT).sats != (
            self.new_price.to(_PRICE_HASH_UNIT, _PRICE_TIME_UNIT).sats
        )

    @property
    def speed_limit_changed(self) -> bool:
        """Whether the speed limit differs."""
        return self.bid.speed_limit_ph != self.new_speed_limit_ph


@dataclass(frozen=True)
class CreateAction:
    """A new bid to create."""

    config: BidConfig
    amount: Sats
    upstream: Upstream
    replaces: UserBid | None = None


@dataclass(frozen=True)
class CancelAction:
    """An existing bid to cancel."""

    bid: UserBid
    reason: CancelReason


@dataclass(frozen=True)
class ReconciliationPlan:
    """The full set of changes needed to reach the desired state."""

    edits: tuple[EditAction, ...]
    creates: tuple[CreateAction, ...]
    cancels: tuple[CancelAction, ...]
    unchanged: tuple[UserBid, ...]


def _field_diff_count(bid: UserBid, config_entry: BidConfig) -> int:
    """Count how many fields differ between a bid and a config entry."""
    diffs = 0
    bid_price_sats = bid.price.to(_PRICE_HASH_UNIT, _PRICE_TIME_UNIT).sats
    config_price_sats = config_entry.price.to(_PRICE_HASH_UNIT, _PRICE_TIME_UNIT).sats
    if bid_price_sats != config_price_sats:
        diffs += 1
    if bid.speed_limit_ph != config_entry.speed_limit:
        diffs += 1
    return diffs


def plan_bid_changes(
    config: SetBidsConfig, current_bids: tuple[UserBid, ...]
) -> ReconciliationPlan:
    """Compute the minimal set of changes to reach the desired bid state.

    Args:
        config: The desired bid configuration.
        current_bids: The user's current bids from the API.

    Returns:
        A plan describing edits, creates, cancels, and unchanged bids.
    """
    manageable_bids = [b for b in current_bids if b.status in MANAGEABLE_STATUSES]
    manageable_bids.sort(
        key=lambda b: (
            b.amount_remaining_sat
            if b.amount_remaining_sat is not None
            else b.amount_sat
        ),
        reverse=True,
    )

    unmatched_configs = list(range(len(config.bids)))

    edits: list[EditAction] = []
    creates: list[CreateAction] = []
    cancels: list[CancelAction] = []
    unchanged: list[UserBid] = []

    paired_bid_to_config: dict[str, int] = {}

    # Greedy match: each bid picks the config entry with fewest diffs.
    for bid in manageable_bids:
        if not unmatched_configs:
            break
        best_idx = min(
            unmatched_configs, key=lambda ci: _field_diff_count(bid, config.bids[ci])
        )
        paired_bid_to_config[bid.id] = best_idx
        unmatched_configs.remove(best_idx)

    # Classify paired bids.
    for bid in manageable_bids:
        if bid.id not in paired_bid_to_config:
            cancels.append(CancelAction(bid=bid, reason=CancelReason.UNMATCHED))
            continue

        config_entry = config.bids[paired_bid_to_config[bid.id]]
        diffs = _field_diff_count(bid, config_entry)

        # Check upstream match.
        upstream_matches = bid.upstream == config.upstream

        if not upstream_matches:
            # Cannot edit upstream — cancel and recreate.
            cancels.append(CancelAction(bid=bid, reason=CancelReason.UPSTREAM_MISMATCH))
            creates.append(
                CreateAction(
                    config=config_entry,
                    amount=config.default_amount,
                    upstream=config.upstream,
                    replaces=bid,
                )
            )
            continue

        if diffs == 0:
            unchanged.append(bid)
            continue

        edits.append(
            EditAction(
                bid=bid,
                new_price=config_entry.price,
                new_speed_limit_ph=config_entry.speed_limit,
            )
        )

    # Unmatched config entries become creates.
    for ci in unmatched_configs:
        creates.append(
            CreateAction(
                config=config.bids[ci],
                amount=config.default_amount,
                upstream=config.upstream,
            )
        )

    return ReconciliationPlan(
        edits=tuple(edits),
        creates=tuple(creates),
        cancels=tuple(cancels),
        unchanged=tuple(unchanged),
    )
