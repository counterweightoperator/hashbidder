# hashbidder

hashbidder is a small tool to manage bidding in [Braiins Hashpower](https://academy.braiins.com/en/braiins-hashpower/about/) market automatically. You declare a config file and hashbidder uses [Hashpower's API](https://hashpower.braiins.com/api/) to align your open bids with it.

## Disclaimers

hashbidder is severely under-tested and most probably has bugs. If used against the actual Braiins Hashpower market, it's going to use your money in a real market, and thus you can end up spending money in a way you don't want. You use hashbidder under at your own risk.

hashbidder is currently overfit for someone who is mining at [OCEAN Pool](https://ocean.xyz/) running their own [DATUM gateway](https://github.com/OCEAN-xyz/datum_gateway). If your profile is different, parts of this tool might be awkward or not useful at all.

## Prerequisites

You will need `uv` installed: https://docs.astral.sh/uv/getting-started/installation/

## Configuration

### API Key

Copy the example env file and fill in your Braiins API key:

```sh
cp .env.example .env
```

The API key is required for authenticated commands (e.g. `bids`). Public commands like `ping` work without it.

Braiins provides two API keys: a read only one and an owner one. If you want hashbidder to be able to do bidding for you, you must provide the owner one. If you set the read only key, only read only commands will work.

### OCEAN Bitcoin address

`target-hashrate` mode additionally requires you to set your `OCEAN_ADDRESS` in `.env`. This is needed to fetch your last 24 hours hashrate, compare it to the target hashrate as per your config and adjust your bids accordingly.

### Bid config file

`set-bids` supports two modes: `manual` (default — declare exact bids) and `target-hashrate` (declare a target, let hashbidder plan bids against the live orderbook).

#### Manual mode

Each `[[bids]]` entry becomes one bid on the marketplace. Prices must be multiples of the market tick size (currently 1000 sat/EH/Day).

```toml
# Sats deposited per bid. If you will run this frequently, you can set small values here.
default_amount_sat = 100000

# Where purchased hashrate is pointed.
[upstream]
url = "stratum+tcp://203.0.113.10:23334"
identity = "brains.worker"

[[bids]]
price_sat_per_ph_day = 45000   # price you're willing to pay
speed_limit_ph_s = 1.0         # max hashrate for this bid

[[bids]]
price_sat_per_ph_day = 46000
speed_limit_ph_s = 1.0

[[bids]]
price_sat_per_ph_day = 46000
speed_limit_ph_s = 2.0

# You can set as many bids as you want
```

#### Target-hashrate mode

Declare a target hashrate and a max number of bids. hashbidder reads your current 24h Ocean hashrate, computes how much more it needs, picks a price by undercutting the cheapest served bid on the orderbook by one tick, and splits the needed hashrate across up to `max_bids_count` bids. Per-bid price/speed cooldowns are respected.

```toml
mode = "target-hashrate"

# Orders will be created with this budget. If you'll be running hashbidder frequently,
# since any order that gets completed will be quickly replaced by a new one.
default_amount_sat = 100000

# Your goal
target_hashrate_ph_s = 5.0

# How many bids you want to place in parallel at most. Why have multiple? Multiple 
# bids let hashbidder better deal with Braiins cooldown periods (see https://academy.braiins.com/en/braiins-hashpower/faqs/trading/?Pages_en%5Bquery%5D=cooldow#what-is-the-overbid-feature)
# If you will run this every 10 minutes, I would suggest to start with 5. If you will run less frequently, you can get away with less.
max_bids_count = 5

[upstream]
url = "stratum+tcp://203.0.113.10:23334"
identity = "brains.worker"
```

## How to use

Run commands via `uv run`:

```sh
uv run hashbidder --help
```

`uv` will automatically create a virtual environment and install dependencies on first run.

## Commands

```sh
# Simply fetch orderbook to verify market is reachable
$ uv run hashbidder ping
OK — order book: 70 bids, 8 asks

# Print your current bids
$ uv run hashbidder bids
B123456789        ACTIVE  price=500 sat/1 EH/Day  limit=5.0 PH/Second  ...

# Compute the current hashvalue from on-chain data
$ uv run hashbidder hashvalue
Hashvalue: 45469 sat/PH/Day

# Reconcile your open bids with a config file.
# --dry-run shows what would change without touching anything.
$ uv run hashbidder set-bids --bid-config bids.toml --dry-run
=== Changes ===
CREATE:
  price:       46001 sat/PH/Day
  speed_limit: 1.0 PH/s
  amount:      100000 sat
  upstream:    stratum+tcp://203.0.113.10:23334 / brains.worker

=== Expected Final State ===
BID  price=46001 sat/PH/Day  limit=1.0 PH/s  amount=100000 sat  (NEW)

# Without --dry-run, changes are applied for real.
$ uv run hashbidder set-bids --bid-config bids.toml
=== Executing Changes ===
CREATE 46001 sat/PH/Day 1.0 PH/s... OK → B987654321

=== Results ===
1 succeeded, 0 failed

=== Current Bids ===
B987654321  price=46001 sat/PH/Day  limit=1.0 PH/s  amount=100000 sat  ACTIVE
```

Use `-v` for debug logging or `--log-file path` to log to a file. For `set-bids` in target-hashrate mode, `-v` also prints a full planner trace (price scan, distribution math, cooldown decisions).

## Tests

```sh
make check    # format + lint + typecheck + test
make test     # tests only
```

