# hashbidder

hashbidder is a small tool to manage bidding in [Braiins Hashpower](https://academy.braiins.com/en/braiins-hashpower/about/) market automatically. You declare a config file and hashbidder uses [Hashpower's API](https://hashpower.braiins.com/api/) to align your open bids with it.

## Disclaimer

hashbidder is severely under-tested and most probably has bugs. If used against the actual Braiins Hashpower market, it's going to use your money in a real market, and thus you can end up spending money in a way you don't want. You use hashbidder under at your own risk.

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

### Bid config file

`set-bids` reads a TOML file that declares your desired bids:

```toml
# Sats deposited per bid. If you will run this frequently, you can set small values here.
default_amount_sat = 100000

# Where purchased hashrate is pointed.
[upstream]
url = "stratum+tcp://203.0.113.10:23334"
identity = "brains.worker"

# Each [[bids]] entry becomes one bid on the marketplace.
[[bids]]
price_sat_per_ph_day = 45501   # price you're willing to pay
speed_limit_ph_s = 1.0         # max hashrate for this bid

[[bids]]
price_sat_per_ph_day = 46001
speed_limit_ph_s = 1.0

[[bids]]
price_sat_per_ph_day = 46401
speed_limit_ph_s = 2.

# You can set as many bids as you want
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

Use `-v` for debug logging or `--log-file path` to log to a file.

## Tests

```sh
make check    # format + lint + typecheck + test
make test     # tests only
```

