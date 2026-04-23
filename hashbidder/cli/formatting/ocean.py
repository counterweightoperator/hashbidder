"""Formatters for Ocean account stats."""

from hashbidder.clients.ocean import AccountStats
from hashbidder.domain.btc_address import BtcAddress


def format_ocean_stats(stats: AccountStats, address: BtcAddress) -> str:
    """Format Ocean account stats for display.

    If all hashrate values are zero, returns an informative message
    instead of the stats table.
    """
    all_zero = all(w.hashrate.value == 0 for w in stats.windows)
    if all_zero:
        return f"No stats found for {address} on Ocean."

    lines = [f"Ocean stats for {address.truncated()}", ""]
    for w in stats.windows:
        display = w.hashrate.display_unit()
        label = w.window.value
        value_str = f"{display.value:.2f} {display.hash_unit.name}/s"
        lines.append(f"  {label:>6s}    {value_str}")

    return "\n".join(lines)
