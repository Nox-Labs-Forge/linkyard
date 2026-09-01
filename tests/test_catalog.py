import json
from pathlib import Path

import pytest

from linkyard.cli import CatalogError, load_catalog, load_state, plan_catalog, save_state, State


def test_load_valid_catalog(tmp_path: Path):
    p = tmp_path / "catalog.json"
    p.write_text(
        json.dumps(
            {
                "products": [
                    {
                        "id": "linkyard",
                        "name": "linkyard",
                        "amount_cents": 500,
                        "description": "cli",
                        "after_completion": "https://github.com/Nox-Labs-Forge/linkyard",
                    }
                ]
            }
        )
    )
    products = load_catalog(p)
    assert len(products) == 1
    assert products[0].id == "linkyard"
    assert products[0].currency == "usd"
    assert products[0].after_completion.endswith("/linkyard")


def test_reject_bad_slug(tmp_path: Path):
    p = tmp_path / "catalog.json"
    p.write_text(json.dumps({"products": [{"id": "has space", "name": "x", "amount_cents": 500}]}))
    with pytest.raises(CatalogError, match="slug"):
        load_catalog(p)


def test_reject_duplicate_ids(tmp_path: Path):
    p = tmp_path / "catalog.json"
    p.write_text(
        json.dumps(
            {
                "products": [
                    {"id": "a", "name": "A", "amount_cents": 500},
                    {"id": "a", "name": "B", "amount_cents": 500},
                ]
            }
        )
    )
    with pytest.raises(CatalogError, match="duplicate"):
        load_catalog(p)


def test_reject_non_https_after_completion(tmp_path: Path):
    p = tmp_path / "catalog.json"
    p.write_text(
        json.dumps(
            {
                "products": [
                    {
                        "id": "a",
                        "name": "A",
                        "amount_cents": 500,
                        "after_completion": "javascript:alert(1)",
                    }
                ]
            }
        )
    )
    with pytest.raises(CatalogError, match="http"):
        load_catalog(p)


def test_reject_amount_zero(tmp_path: Path):
    p = tmp_path / "catalog.json"
    p.write_text(json.dumps({"products": [{"id": "a", "name": "A", "amount_cents": 0}]}))
    with pytest.raises(CatalogError, match="amount_cents"):
        load_catalog(p)


def test_missing_catalog(tmp_path: Path):
    with pytest.raises(CatalogError, match="not found"):
        load_catalog(tmp_path / "nope.json")


def test_state_roundtrip(tmp_path: Path):
    from linkyard.cli import ProductState

    path = tmp_path / ".linkyard" / "state.json"
    state = State()
    state.products["linkyard"] = ProductState(
        stripe_product_id="prod_1",
        stripe_price_id="price_1",
        stripe_payment_link_id="plink_1",
        payment_link_url="https://buy.stripe.com/test_plink_1",
        amount_cents=500,
        currency="usd",
        name="linkyard",
    )
    save_state(path, state)
    raw = json.loads(path.read_text())
    assert "sk_" not in path.read_text()
    assert raw["products"]["linkyard"]["stripe_product_id"] == "prod_1"
    loaded = load_state(path)
    assert loaded.products["linkyard"].payment_link_url.endswith("plink_1")


def test_plan_create_then_noop(tmp_path: Path):
    from linkyard.cli import Product, ProductState

    product = Product(id="x", name="X", amount_cents=500, currency="usd", description="d")
    items = plan_catalog([product], State())
    assert items[0].create_product
    assert items[0].create_price
    assert items[0].create_link
    assert items[0].warnings == []

    state = State()
    state.products["x"] = ProductState(
        stripe_product_id="prod_1",
        stripe_price_id="price_1",
        stripe_payment_link_id="plink_1",
        payment_link_url="https://buy.stripe.com/x",
        name="X",
        description="d",
        amount_cents=500,
        currency="usd",
    )
    items = plan_catalog([product], state)
    assert items[0].action_count == 0


def test_plan_price_change_archives_and_recreates():
    from linkyard.cli import Product, ProductState, plan_item

    product = Product(id="x", name="X", amount_cents=700, currency="usd")
    prev = ProductState(
        stripe_product_id="prod_1",
        stripe_price_id="price_old",
        stripe_payment_link_id="plink_old",
        name="X",
        description="",
        amount_cents=500,
        currency="usd",
    )
    item = plan_item(product, prev)
    assert not item.create_product
    assert item.create_price
    assert item.archive_price_id == "price_old"
    assert item.create_link
    assert item.deactivate_link_id == "plink_old"


def test_plan_warns_tiny_sku():
    from linkyard.cli import Product, plan_item

    item = plan_item(Product(id="tiny", name="tiny", amount_cents=100), None)
    assert item.warnings
    assert "200" in item.warnings[0]
