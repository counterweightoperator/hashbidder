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

As a first proper command, you can try `hashbidder ping`. It will try to fetch the current state of the orderbook and count for you how many orders are active.

```sh
$ uv run hashbidder ping
OK — order book: 70 bids, 8 asks
```

