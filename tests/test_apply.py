import json
from pathlib import Path

from linkyard.cli import load_state
from tests.fake_stripe import MemoryStripe
from tests.helpers import SECRET, run, sample_product, write_catalog

LIVE = "sk_live_DO_NOT_PRINT_THIS_LIVE_KEY"


def _paths(tmp_path: Path):
    catalog = tmp_path / "catalog.json"
    state = tmp_path / ".linkyard" / "state.json"
    return catalog, state


def test_apply_creates_product_price_link(tmp_path: Path):
    catalog, state = _paths(tmp_path)
    write_catalog(
        catalog,
        [
            sample_product(
                after_completion="https://github.com/Nox-Labs-Forge/linkyard"
            )
        ],
    )
    stripe = MemoryStripe()
    code, out, err = run(
        ["apply", "--catalog", str(catalog), "--state", str(state)],
        environ={"STRIPE_SECRET_KEY": SECRET},
        transport=stripe,
    )
    assert code == 0, err
    assert SECRET not in out
    assert SECRET not in err
    assert "sk_test_***" in err
    assert "applied linkyard" in out
    assert "https://buy.stripe.com/test_plink_3" in out
    assert len(stripe.products) == 1
    assert len(stripe.prices) == 1
    assert len(stripe.links) == 1
    saved = json.loads(state.read_text())
    entry = saved["products"]["linkyard"]
    assert entry["stripe_product_id"] == "prod_1"
    assert entry["stripe_price_id"] == "price_2"
    assert entry["stripe_payment_link_id"] == "plink_3"
    assert "sk_" not in state.read_text()
    methods = [c[0] + " " + c[1] for c in stripe.calls]
    assert methods == [
        "POST /v1/products",
        "POST /v1/prices",
        "POST /v1/payment_links",
    ]
    auth = stripe.calls[0][2]["Authorization"]
    assert auth == f"Bearer {SECRET}"
    link_fields = stripe.calls[2][3]
    assert link_fields["line_items[0][price]"] == "price_2"
    assert link_fields["after_completion[redirect][url]"].startswith("https://github.com/")


def test_apply_is_idempotent(tmp_path: Path):
    catalog, state = _paths(tmp_path)
    write_catalog(catalog, [sample_product()])
    stripe = MemoryStripe()
    args = ["apply", "--catalog", str(catalog), "--state", str(state)]
    env = {"STRIPE_SECRET_KEY": SECRET}
    code1, out1, err1 = run(args, environ=env, transport=stripe)
    assert code1 == 0, err1
    calls_after_first = len(stripe.calls)
    code2, out2, err2 = run(args, environ=env, transport=stripe)
    assert code2 == 0, err2
    assert len(stripe.calls) == calls_after_first
    assert "nothing to apply" in out2
    assert load_state(state).products["linkyard"].stripe_product_id == "prod_1"


def test_apply_updates_name_without_new_price(tmp_path: Path):
    catalog, state = _paths(tmp_path)
    write_catalog(catalog, [sample_product(name="linkyard")])
    stripe = MemoryStripe()
    env = {"STRIPE_SECRET_KEY": SECRET}
    args = ["apply", "--catalog", str(catalog), "--state", str(state)]
    assert run(args, environ=env, transport=stripe)[0] == 0
    write_catalog(catalog, [sample_product(name="linkyard CLI")])
    code, out, err = run(args, environ=env, transport=stripe)
    assert code == 0, err
    prod = next(iter(stripe.products.values()))
    assert prod["name"] == "linkyard CLI"
    assert len(stripe.prices) == 1
    assert any(c[1].startswith("/v1/products/prod_") for c in stripe.calls)


def test_apply_amount_change_rotates_price_and_link(tmp_path: Path):
    catalog, state = _paths(tmp_path)
    write_catalog(catalog, [sample_product(amount_cents=500)])
    stripe = MemoryStripe()
    env = {"STRIPE_SECRET_KEY": SECRET}
    args = ["apply", "--catalog", str(catalog), "--state", str(state)]
    assert run(args, environ=env, transport=stripe)[0] == 0
    old_price = next(iter(stripe.prices))
    old_link = next(iter(stripe.links))
    write_catalog(catalog, [sample_product(amount_cents=700)])
    code, out, err = run(args, environ=env, transport=stripe)
    assert code == 0, err
    assert stripe.prices[old_price]["active"] is False
    assert stripe.links[old_link]["active"] is False
    assert len(stripe.prices) == 2
    assert len(stripe.links) == 2
    new_state = load_state(state).products["linkyard"]
    assert new_state.amount_cents == 700
    assert new_state.stripe_price_id != old_price


def test_apply_live_key_warns_without_printing_secret(tmp_path: Path):
    catalog, state = _paths(tmp_path)
    write_catalog(catalog, [sample_product()])
    stripe = MemoryStripe()
    code, out, err = run(
        ["apply", "--catalog", str(catalog), "--state", str(state)],
        environ={"STRIPE_SECRET_KEY": LIVE},
        transport=stripe,
    )
    assert code == 0, err
    assert "LIVE" in err
    assert "sk_live_***" in err
    assert LIVE not in out
    assert LIVE not in err
    assert LIVE not in state.read_text()


def test_apply_tiny_sku_warning(tmp_path: Path):
    catalog, state = _paths(tmp_path)
    write_catalog(catalog, [sample_product(id="tiny", name="tiny", amount_cents=100)])
    stripe = MemoryStripe()
    code, out, err = run(
        ["apply", "--catalog", str(catalog), "--state", str(state)],
        environ={"STRIPE_SECRET_KEY": SECRET},
        transport=stripe,
    )
    assert code == 0
    assert "warning: tiny:" in err
    assert "200" in err


def test_apply_stripe_error_redacts_key(tmp_path: Path):
    catalog, state = _paths(tmp_path)
    write_catalog(catalog, [sample_product()])
    stripe = MemoryStripe()
    stripe.fail_on["POST /v1/products"] = (401, f"Invalid API Key provided: {SECRET}")
    code, out, err = run(
        ["apply", "--catalog", str(catalog), "--state", str(state)],
        environ={"STRIPE_SECRET_KEY": SECRET},
        transport=stripe,
    )
    assert code == 1
    assert SECRET not in err
    assert "sk_test_***" in err


def test_orphans_listed_not_deleted(tmp_path: Path):
    catalog, state_path = _paths(tmp_path)
    write_catalog(catalog, [sample_product(), sample_product(id="extra", name="extra")])
    stripe = MemoryStripe()
    env = {"STRIPE_SECRET_KEY": SECRET}
    args = ["apply", "--catalog", str(catalog), "--state", str(state_path)]
    assert run(args, environ=env, transport=stripe)[0] == 0
    write_catalog(catalog, [sample_product()])
    code, out, err = run(
        ["plan", "--catalog", str(catalog), "--state", str(state_path)]
    )
    assert code == 0
    assert "orphans" in out
    assert "extra" in out
    assert load_state(state_path).products["extra"].stripe_product_id
