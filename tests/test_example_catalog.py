from pathlib import Path

from linkyard.cli import load_catalog
from linkyard.fees import net_cents


def test_example_catalog_is_the_five_dollar_sku():
    root = Path(__file__).resolve().parents[1]
    products = load_catalog(root / "catalog.example.json")
    assert len(products) == 1
    p = products[0]
    assert p.id == "linkyard"
    assert p.amount_cents == 500
    assert p.currency == "usd"
    assert net_cents(p.amount_cents) > 100
