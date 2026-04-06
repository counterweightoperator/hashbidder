# Hashbidder

Hashbidder is a small tool to managed bidding in [Braiins Hashpower](https://academy.braiins.com/en/braiins-hashpower/about/) market automatically. You declare certain mining goals and hashbidder uses [Hashpower's API](https://hashpower.braiins.com/api/) to align your open bids with them.

Hashbidder is useful for ensuring you get the most hash for your sats, or that you obtain a certain amount of hashrate overpaying as little as possible. It will help you avoid going crazy visiting the Hashpower console every five minutes.

On the other hand, Hashbidder does not perform any sort of astrology to make you win money. The market is what it is and will determine what you get for what you are willing to pay.

## Prerequisites

You will need `uv` installed: https://docs.astral.sh/uv/getting-started/installation/

## How to use

Run commands via `uv run`:

```sh
uv run hashbidder --help
```

`uv` will automatically create a virtual environment and install dependencies on first run — no manual setup needed.

## Configuration

Copy the example env file and fill in your Braiins API key:

```sh
cp .env.example .env
```

The API key is required for authenticated commands (e.g. `bids`). Public commands like `ping` work without it.

## Commands

```sh
$ uv run hashbidder ping
OK — order book: 70 bids, 8 asks

$ uv run hashbidder bids
B123456789        ACTIVE  price=500 sat/1 EH/Day  limit=5.0 PH/Second  ...
```

Use `-v` for debug logging or `--log-file path` to log to a file.

## Tests

```sh
make check    # format + lint + typecheck + test
make test     # tests only
```

