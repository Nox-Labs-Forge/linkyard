"""linkyard CLI: init, plan, apply, list, fee."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, TextIO
from urllib.parse import urlparse

from linkyard import __version__
from linkyard.fees import cents_to_dollars, net_cents, small_sku_warning, stripe_fee_cents
from linkyard.stripe_api import StripeClient, StripeError, Transport, key_mode, redact

SLUG_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
CURRENCY_RE = re.compile(r"^[a-z]{3}$")
DEFAULT_CATALOG = Path("catalog.json")
DEFAULT_STATE = Path(".linkyard/state.json")


@dataclass(frozen=True)
class Product:
    id: str
    name: str
    amount_cents: int
    currency: str = "usd"
    description: str = ""
    after_completion: str | None = None


@dataclass
class ProductState:
    stripe_product_id: str | None = None
    stripe_price_id: str | None = None
    stripe_payment_link_id: str | None = None
    payment_link_url: str | None = None
    name: str | None = None
    description: str | None = None
    amount_cents: int | None = None
    currency: str | None = None
    after_completion: str | None = None


@dataclass
class State:
    products: dict[str, ProductState] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "products": {
                local_id: {k: v for k, v in asdict(entry).items() if v is not None}
                for local_id, entry in self.products.items()
            }
        }


@dataclass
class PlanItem:
    product: Product
    create_product: bool = False
    update_product: bool = False
    create_price: bool = False
    archive_price_id: str | None = None
    create_link: bool = False
    update_link: bool = False
    deactivate_link_id: str | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def action_count(self) -> int:
        n = 0
        n += int(self.create_product) + int(self.update_product)
        n += int(self.create_price) + int(self.archive_price_id is not None)
        n += int(self.create_link) + int(self.update_link)
        n += int(self.deactivate_link_id is not None)
        return n


class CatalogError(Exception):
    pass


def load_catalog(path: Path) -> list[Product]:
    if not path.is_file():
        raise CatalogError(f"catalog not found: {path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CatalogError(f"catalog is not valid JSON: {path}: {exc}") from None
    if not isinstance(raw, dict) or not isinstance(raw.get("products"), list):
        raise CatalogError("catalog must be an object with a 'products' array")
    products: list[Product] = []
    seen: set[str] = set()
    for index, item in enumerate(raw["products"]):
        products.append(_parse_product(item, index))
        if products[-1].id in seen:
            raise CatalogError(f"duplicate product id: {products[-1].id}")
        seen.add(products[-1].id)
    return products


def _parse_product(item: Any, index: int) -> Product:
    loc = f"products[{index}]"
    if not isinstance(item, dict):
        raise CatalogError(f"{loc} must be an object")
    local_id = item.get("id")
    name = item.get("name")
    amount = item.get("amount_cents")
    if not isinstance(local_id, str) or not SLUG_RE.match(local_id):
        raise CatalogError(f"{loc}.id must be a slug [A-Za-z0-9_-] 1..64")
    if not isinstance(name, str) or not name.strip():
        raise CatalogError(f"{loc}.name must be a non-empty string")
    if not isinstance(amount, int) or isinstance(amount, bool) or amount < 1:
        raise CatalogError(f"{loc}.amount_cents must be an integer >= 1")
    currency = item.get("currency", "usd")
    if not isinstance(currency, str) or not CURRENCY_RE.match(currency):
        raise CatalogError(f"{loc}.currency must be a 3-letter code")
    description = item.get("description", "")
    if description is None:
        description = ""
    if not isinstance(description, str):
        raise CatalogError(f"{loc}.description must be a string")
    after = item.get("after_completion")
    if after is not None:
        if not isinstance(after, str) or urlparse(after).scheme not in ("http", "https"):
            raise CatalogError(f"{loc}.after_completion must be an http(s) URL")
        if not urlparse(after).netloc:
            raise CatalogError(f"{loc}.after_completion must be an http(s) URL")
    return Product(
        id=local_id,
        name=name.strip(),
        amount_cents=amount,
        currency=currency.lower(),
        description=description,
        after_completion=after,
    )


def load_state(path: Path) -> State:
    if not path.is_file():
        return State()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CatalogError(f"state is not valid JSON: {path}: {exc}") from None
    if not isinstance(raw, dict):
        raise CatalogError("state must be an object")
    products_raw = raw.get("products") or {}
    if not isinstance(products_raw, dict):
        raise CatalogError("state.products must be an object")
    state = State()
    for local_id, entry in products_raw.items():
        if not isinstance(entry, dict):
            continue
        state.products[str(local_id)] = ProductState(
            stripe_product_id=_opt_str(entry.get("stripe_product_id")),
            stripe_price_id=_opt_str(entry.get("stripe_price_id")),
            stripe_payment_link_id=_opt_str(entry.get("stripe_payment_link_id")),
            payment_link_url=_opt_str(entry.get("payment_link_url")),
            name=_opt_str(entry.get("name")),
            description=_opt_str(entry.get("description")),
            amount_cents=_opt_int(entry.get("amount_cents")),
            currency=_opt_str(entry.get("currency")),
            after_completion=_opt_str(entry.get("after_completion")),
        )
    return state


def save_state(path: Path, state: State) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(state.to_json(), indent=2, sort_keys=True) + "\n"
    path.write_text(text, encoding="utf-8")


def _opt_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _opt_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def plan_item(product: Product, existing: ProductState | None) -> PlanItem:
    item = PlanItem(product=product)
    warn = small_sku_warning(product.amount_cents)
    if warn:
        item.warnings.append(warn)
    if product.amount_cents <= stripe_fee_cents(product.amount_cents):
        item.warnings.append(
            f"estimated net is {cents_to_dollars(net_cents(product.amount_cents))} "
            "(fee >= price)"
        )
    prev = existing or ProductState()
    if not prev.stripe_product_id:
        item.create_product = True
    elif prev.name != product.name or (prev.description or "") != product.description:
        item.update_product = True
    price_changed = (
        not prev.stripe_price_id
        or prev.amount_cents != product.amount_cents
        or (prev.currency or "usd") != product.currency
    )
    if price_changed:
        item.create_price = True
        if prev.stripe_price_id:
            item.archive_price_id = prev.stripe_price_id
    if price_changed or not prev.stripe_payment_link_id:
        item.create_link = True
        if prev.stripe_payment_link_id and price_changed:
            item.deactivate_link_id = prev.stripe_payment_link_id
    elif (prev.after_completion or None) != (product.after_completion or None):
        item.update_link = True
    return item


def plan_catalog(products: list[Product], state: State) -> list[PlanItem]:
    return [plan_item(product, state.products.get(product.id)) for product in products]


def orphan_ids(products: list[Product], state: State) -> list[str]:
    catalog_ids = {p.id for p in products}
    return sorted(local_id for local_id in state.products if local_id not in catalog_ids)


def _need(value: str | None, what: str, local_id: str) -> str:
    if not value:
        raise StripeError(0, f"{local_id}: missing {what}")
    return value


def execute_item(item: PlanItem, state: State, client: StripeClient) -> ProductState:
    """Apply one catalog row. Caller persists state after this returns."""
    product = item.product
    entry = state.products.get(product.id) or ProductState()
    if item.create_product:
        created = client.create_product(
            name=product.name,
            description=product.description,
            local_id=product.id,
        )
        entry.stripe_product_id = created["id"]
    elif item.update_product:
        client.update_product(
            _need(entry.stripe_product_id, "product id", product.id),
            name=product.name,
            description=product.description,
        )
    if item.archive_price_id:
        client.archive_price(item.archive_price_id)
    if item.create_price:
        price = client.create_price(
            product_id=_need(entry.stripe_product_id, "product id", product.id),
            amount_cents=product.amount_cents,
            currency=product.currency,
            local_id=product.id,
        )
        entry.stripe_price_id = price["id"]
    if item.deactivate_link_id:
        client.deactivate_payment_link(item.deactivate_link_id)
    if item.create_link:
        link = client.create_payment_link(
            price_id=_need(entry.stripe_price_id, "price id", product.id),
            local_id=product.id,
            after_completion=product.after_completion,
        )
        entry.stripe_payment_link_id = link["id"]
        entry.payment_link_url = link.get("url")
    elif item.update_link:
        link = client.update_payment_link(
            _need(entry.stripe_payment_link_id, "payment link id", product.id),
            after_completion=product.after_completion or "",
        )
        entry.payment_link_url = link.get("url") or entry.payment_link_url
    entry.name = product.name
    entry.description = product.description
    entry.amount_cents = product.amount_cents
    entry.currency = product.currency
    entry.after_completion = product.after_completion
    state.products[product.id] = entry
    return entry


def execute_plan(
    items: list[PlanItem],
    state: State,
    client: StripeClient,
    *,
    persist=None,
) -> State:
    for item in items:
        if item.action_count == 0:
            continue
        execute_item(item, state, client)
        if persist is not None:
            persist(state)
    return state


def format_plan(items: list[PlanItem], state: State, orphans: list[str]) -> str:
    lines: list[str] = []
    for item in items:
        p = item.product
        entry = state.products.get(p.id) or ProductState()
        lines.append(f"{p.id}  {cents_to_dollars(p.amount_cents)} {p.currency}")
        lines.append("  product  " + _prod_action(item, entry))
        lines.append("  price    " + _price_action(item, entry, p))
        lines.append("  link     " + _link_action(item, entry))
        lines.append(
            f"  net      {cents_to_dollars(net_cents(p.amount_cents))} "
            f"after 2.9%+$0.30 (est. US card)"
        )
        for warn in item.warnings:
            lines.append(f"  warn     {warn}")
        lines.append("")
    if orphans:
        lines.append("orphans in state (left untouched): " + ", ".join(orphans))
        lines.append("")
    actions = sum(i.action_count for i in items)
    n = len(items)
    if actions == 0:
        lines.append(f"{n} product(s), 0 actions. Already in sync.")
    else:
        lines.append(f"{n} product(s), {actions} action(s). Dry-run: nothing sent to Stripe.")
    return "\n".join(lines).rstrip() + "\n"


def _prod_action(item: PlanItem, entry: ProductState) -> str:
    if item.create_product:
        return "CREATE"
    if item.update_product:
        return f"UPDATE  {entry.stripe_product_id}"
    return f"ok      {entry.stripe_product_id or '-'}"


def _price_action(item: PlanItem, entry: ProductState, product: Product) -> str:
    if item.create_price and item.archive_price_id:
        return (
            f"CREATE  {product.amount_cents} {product.currency} "
            f"(archive {item.archive_price_id})"
        )
    if item.create_price:
        return f"CREATE  {product.amount_cents} {product.currency}"
    return f"ok      {entry.stripe_price_id or '-'}  {product.amount_cents} {product.currency}"


def _link_action(item: PlanItem, entry: ProductState) -> str:
    if item.create_link and item.deactivate_link_id:
        return f"CREATE  (deactivate {item.deactivate_link_id})"
    if item.create_link:
        return "CREATE"
    if item.update_link:
        return f"UPDATE  {entry.stripe_payment_link_id}"
    return f"ok      {entry.payment_link_url or entry.stripe_payment_link_id or '-'}"


def format_list(products: list[Product], state: State, orphans: list[str]) -> str:
    lines: list[str] = []
    for product in products:
        entry = state.products.get(product.id) or ProductState()
        lines.append(f"{product.id}  {cents_to_dollars(product.amount_cents)} {product.currency}")
        lines.append(f"  name     {product.name}")
        if product.description:
            lines.append(f"  desc     {product.description}")
        lines.append(f"  product  {entry.stripe_product_id or '(not applied)'}")
        lines.append(f"  price    {entry.stripe_price_id or '(not applied)'}")
        lines.append(f"  link     {entry.payment_link_url or entry.stripe_payment_link_id or '(not applied)'}")
        lines.append(
            f"  net      {cents_to_dollars(net_cents(product.amount_cents))} est."
        )
        warn = small_sku_warning(product.amount_cents)
        if warn:
            lines.append(f"  warn     {warn}")
        lines.append("")
    if orphans:
        lines.append("orphans in state (left untouched): " + ", ".join(orphans))
        lines.append("")
    if not products:
        lines.append("catalog is empty.")
    return "\n".join(lines).rstrip() + "\n"


def format_fee_line(amount_cents: int, currency: str = "usd") -> str:
    fee = stripe_fee_cents(amount_cents)
    net = net_cents(amount_cents)
    lines = [
        f"amount:     {cents_to_dollars(amount_cents)} ({amount_cents} cents {currency})",
        f"stripe fee: {cents_to_dollars(fee)}  (2.9% + $0.30 US card)",
        f"net:        {cents_to_dollars(net)} ({net} cents)",
    ]
    warn = small_sku_warning(amount_cents)
    if warn:
        lines.append(f"warn:       {warn}")
    return "\n".join(lines) + "\n"


INIT_CATALOG = {
    "products": [
        {
            "id": "example",
            "name": "Example SKU",
            "description": "Replace this with a real product.",
            "amount_cents": 500,
            "currency": "usd",
        }
    ]
}


def cmd_init(args: argparse.Namespace, out: TextIO) -> int:
    catalog_path: Path = args.catalog
    state_path: Path = args.state
    if catalog_path.exists():
        out.write(f"catalog exists, leaving it: {catalog_path}\n")
    else:
        catalog_path.parent.mkdir(parents=True, exist_ok=True)
        catalog_path.write_text(json.dumps(INIT_CATALOG, indent=2) + "\n", encoding="utf-8")
        out.write(f"wrote {catalog_path}\n")
    if state_path.exists():
        out.write(f"state exists, leaving it: {state_path}\n")
    else:
        save_state(state_path, State())
        out.write(f"wrote {state_path} (IDs only; not secrets)\n")
    out.write("edit the catalog, then: linkyard plan\n")
    return 0


def cmd_plan(args: argparse.Namespace, out: TextIO) -> int:
    products = load_catalog(args.catalog)
    state = load_state(args.state)
    items = plan_catalog(products, state)
    out.write(f"catalog: {args.catalog}\nstate:   {args.state}\n\n")
    out.write(format_plan(items, state, orphan_ids(products, state)))
    return 0


def cmd_apply(
    args: argparse.Namespace,
    out: TextIO,
    err: TextIO,
    environ: dict[str, str],
    transport: Transport | None,
) -> int:
    products = load_catalog(args.catalog)
    state = load_state(args.state)
    items = plan_catalog(products, state)
    for item in items:
        for warn in item.warnings:
            err.write(f"warning: {item.product.id}: {warn}\n")
    key = (environ.get("STRIPE_SECRET_KEY") or "").strip()
    if not key:
        err.write(
            "error: STRIPE_SECRET_KEY is not set.\n"
            "Export a Stripe secret key (sk_test_… or sk_live_…). "
            "linkyard never logs the value.\n"
        )
        return 2
    mode = key_mode(key)
    if mode == "live":
        err.write("warning: LIVE Stripe key (sk_live_***). Creates real products and payment links.\n")
    elif mode == "test":
        err.write("using test-mode Stripe key (sk_test_***).\n")
    else:
        err.write("warning: STRIPE_SECRET_KEY is not sk_test_ / sk_live_ prefix.\n")
    if all(item.action_count == 0 for item in items):
        out.write(format_plan(items, state, orphan_ids(products, state)))
        out.write("nothing to apply.\n")
        return 0
    client = StripeClient(key, transport=transport)
    try:
        state = execute_plan(
            items, state, client, persist=lambda s: save_state(args.state, s)
        )
    except StripeError as exc:
        err.write(f"error: {redact(str(exc), key)}\n")
        return 1
    save_state(args.state, state)
    for product in products:
        entry = state.products[product.id]
        out.write(f"applied {product.id}\n")
        out.write(f"  product  {entry.stripe_product_id}\n")
        out.write(f"  price    {entry.stripe_price_id}\n")
        out.write(f"  link     {entry.payment_link_url or entry.stripe_payment_link_id}\n")
    out.write(f"wrote state {args.state}\n")
    return 0


def cmd_list(args: argparse.Namespace, out: TextIO) -> int:
    products = load_catalog(args.catalog)
    state = load_state(args.state)
    out.write(format_list(products, state, orphan_ids(products, state)))
    return 0


def cmd_fee(args: argparse.Namespace, out: TextIO) -> int:
    if args.amount_cents is not None:
        if args.amount_cents < 1:
            raise CatalogError("--amount-cents must be >= 1")
        out.write(format_fee_line(args.amount_cents, args.currency))
        return 0
    if not args.catalog.is_file():
        raise CatalogError("pass --amount-cents N or --catalog PATH")
    products = load_catalog(args.catalog)
    if not products:
        raise CatalogError("catalog has no products")
    for product in products:
        out.write(f"# {product.id}\n")
        out.write(format_fee_line(product.amount_cents, product.currency))
        if product is not products[-1]:
            out.write("\n")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="linkyard",
        description="Payment Link catalog-as-code. Sync a JSON catalog to Stripe.",
    )
    parser.add_argument("--version", action="version", version=f"linkyard {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_paths(p: argparse.ArgumentParser) -> None:
        p.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG, help="catalog JSON (default: catalog.json)")
        p.add_argument("--state", type=Path, default=DEFAULT_STATE, help="state JSON (default: .linkyard/state.json)")

    p_init = sub.add_parser("init", help="write catalog.json and empty state")
    add_paths(p_init)
    p_init.set_defaults(func="init")

    p_plan = sub.add_parser("plan", help="dry-run vs local state (no Stripe HTTP)")
    add_paths(p_plan)
    p_plan.set_defaults(func="plan")

    p_apply = sub.add_parser("apply", help="create/update Stripe objects; write state")
    add_paths(p_apply)
    p_apply.set_defaults(func="apply")

    p_list = sub.add_parser("list", help="show catalog merged with state IDs/URLs")
    add_paths(p_list)
    p_list.set_defaults(func="list")

    p_fee = sub.add_parser("fee", help="show net proceeds after US card fees")
    p_fee.add_argument("--amount-cents", type=int, default=None)
    p_fee.add_argument("--currency", default="usd")
    p_fee.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    p_fee.set_defaults(func="fee")
    return parser


def main(
    argv: list[str] | None = None,
    *,
    environ: dict[str, str] | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    transport: Transport | None = None,
) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    out = stdout or sys.stdout
    err = stderr or sys.stderr
    env = environ if environ is not None else os.environ
    try:
        if args.func == "init":
            return cmd_init(args, out)
        if args.func == "plan":
            return cmd_plan(args, out)
        if args.func == "apply":
            return cmd_apply(args, out, err, dict(env), transport)
        if args.func == "list":
            return cmd_list(args, out)
        if args.func == "fee":
            return cmd_fee(args, out)
        parser.error("unknown command")
        return 2
    except CatalogError as exc:
        err.write(f"error: {exc}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
