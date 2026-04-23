"""Ocean account stats use case."""

from hashbidder.clients.ocean import AccountStats, OceanSource
from hashbidder.domain.btc_address import BtcAddress


def get_ocean_account_stats(ocean: OceanSource, address: BtcAddress) -> AccountStats:
    """Fetch Ocean account hashrate stats for the given address.

    Args:
        ocean: The Ocean data source to use.
        address: The Bitcoin address to query.

    Returns:
        The account's hashrate stats across all time windows.
    """
    return ocean.get_account_stats(address)
