"""Bitcoin protocol constants."""

INITIAL_SUBSIDY = 5_000_000_000
"""Initial block subsidy in satoshis (50 BTC)."""

HALVING_INTERVAL = 210_000
"""Number of blocks between subsidy halvings."""

BLOCKS_PER_EPOCH = 2016
"""Number of blocks in a difficulty adjustment epoch."""

BLOCKS_PER_DAY = 144
"""Ideal number of blocks per day (86400 / 600)."""

BLOCK_TIME_SECONDS = 600
"""Target time between blocks in seconds."""
