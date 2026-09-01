# linkyard

Payment Link catalog-as-code.

A local Python 3.13 CLI. You keep a JSON catalog of products. `linkyard apply` creates or updates Stripe Products, Prices, and Payment Links. Reruns are idempotent via a local state file of **IDs, not secrets**.

Zero third-party runtime dependencies. Talks to Stripe over stdlib `urllib`. The `stripe` package is not required.

Nyx (Nox Labs) sells this CLI itself for **$5**. See [BUY.md](BUY.md).

## What it is not

- Not a hosted storefront.
- Not a Stripe Dashboard replacement.
- Not a webhook/fulfillment server. After payment, you send the buyer this repo (or your own URL in `after_completion`).
- Fee math is the **US card** estimate (2.9% + $0.30). International cards, AMEX, and Stripe Billing extras differ. It is not a payout guarantee.

## Install

Python 3.13+.

```bash
# from a clone, no extra packages
python3 -m linkyard --help
```

Or editable:

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
linkyard --help
```

## Catalog

`catalog.json` (see `catalog.example.json`):

```json
{
  "products": [
    {
      "id": "linkyard",
      "name": "linkyard",
      "description": "Payment Link catalog-as-code CLI.",
      "amount_cents": 500,
      "currency": "usd",
      "after_completion": "https://github.com/Nox-Labs-Forge/linkyard"
    }
  ]
}
```

`id` is your local slug (stable). `amount_cents` is an integer. `currency` defaults to `usd`. `after_completion` is optional; if set, Stripe redirects there after a successful payment.

**Tiny SKUs:** amounts under 200 cents ($2.00) get a warning. The $0.30 flat fee eats them. A $1 SKU nets about $0.67.

## Usage

```bash
export STRIPE_SECRET_KEY=sk_test_...   # never commit this; linkyard never logs it

linkyard init                          # catalog.json + .linkyard/state.json
linkyard plan                          # dry-run vs local state, no HTTP
linkyard apply                         # create/update on Stripe, write state
linkyard list                          # catalog merged with state IDs/URLs
linkyard fee --amount-cents 500        # net after 2.9% + $0.30
```

`--catalog PATH` and `--state PATH` override the defaults (`./catalog.json`, `./.linkyard/state.json`).

`plan` does not call Stripe. `apply` and anything that needs the key fail closed if `STRIPE_SECRET_KEY` is missing. `sk_live_` prints a one-line live-mode warning (prefix only).

State file fields: Stripe product / price / payment_link ids and the public Payment Link URL. No keys.

If a price changes, linkyard archives the old price, deactivates the old Payment Link, and creates new ones. Product records are updated in place.

## Buy

This tree is the product. **$5 one-time.** Live Payment Link is in [BUY.md](BUY.md). After payment, Stripe redirects here.

The catalog used to publish it is `catalog.example.json`.

## License

MIT. See [LICENSE](LICENSE).
