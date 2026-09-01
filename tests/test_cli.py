from pathlib import Path

from linkyard.cli import load_catalog, load_state
from tests.fake_stripe import MemoryStripe
from tests.helpers import SECRET, run, sample_product, write_catalog


def test_fee_amount_cents():
    code, out, err = run(["fee", "--amount-cents", "500"])
    assert code == 0
    assert err == ""
    assert "net:" in out
    assert "$4.55" in out
    assert "455 cents" in out
    assert "$0.45" in out


def test_fee_warns_on_tiny_sku():
    code, out, err = run(["fee", "--amount-cents", "100"])
    assert code == 0
    assert "warn:" in out
    assert "$0.67" in out


def test_fee_requires_amount_or_catalog(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    code, out, err = run(["fee"])
    assert code == 2
    assert "amount-cents" in err


def test_init_then_plan(tmp_path: Path):
    catalog = tmp_path / "catalog.json"
    state = tmp_path / ".linkyard" / "state.json"
    code, out, err = run(["init", "--catalog", str(catalog), "--state", str(state)])
    assert code == 0
    assert catalog.is_file()
    assert state.is_file()
    products = load_catalog(catalog)
    assert products[0].amount_cents == 500
    code, out, err = run(["plan", "--catalog", str(catalog), "--state", str(state)])
    assert code == 0
    assert "CREATE" in out
    assert "Dry-run" in out
    assert "nothing sent to Stripe" in out


def test_init_does_not_overwrite(tmp_path: Path):
    catalog = tmp_path / "catalog.json"
    state = tmp_path / "state.json"
    write_catalog(catalog, [sample_product()])
    state.write_text("{}" + "\n")
    code, out, _ = run(["init", "--catalog", str(catalog), "--state", str(state)])
    assert code == 0
    assert "leaving it" in out
    assert load_catalog(catalog)[0].id == "linkyard"


def test_plan_does_not_call_transport(tmp_path: Path):
    catalog = tmp_path / "catalog.json"
    state = tmp_path / "state.json"
    write_catalog(catalog, [sample_product()])
    stripe = MemoryStripe()

    def boom(*_a, **_k):
        raise AssertionError("plan must not touch Stripe")

    code, out, err = run(
        ["plan", "--catalog", str(catalog), "--state", str(state)],
        transport=boom,
    )
    assert code == 0
    assert stripe.calls == []
    assert "CREATE" in out


def test_list_without_apply(tmp_path: Path):
    catalog = tmp_path / "catalog.json"
    write_catalog(catalog, [sample_product()])
    code, out, err = run(["list", "--catalog", str(catalog), "--state", str(tmp_path / "s.json")])
    assert code == 0
    assert "(not applied)" in out
    assert "linkyard" in out


def test_apply_without_key(tmp_path: Path):
    catalog = tmp_path / "catalog.json"
    write_catalog(catalog, [sample_product()])
    code, out, err = run(
        ["apply", "--catalog", str(catalog), "--state", str(tmp_path / "s.json")],
        environ={},
    )
    assert code == 2
    assert "STRIPE_SECRET_KEY" in err
    assert SECRET not in err
    assert SECRET not in out


def test_version_flag():
    import pytest

    with pytest.raises(SystemExit) as exc:
        run(["--version"])
    assert exc.value.code == 0
