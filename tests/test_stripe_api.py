from linkyard.stripe_api import StripeClient, StripeError, flatten, redact
from tests.fake_stripe import MemoryStripe
from tests.helpers import SECRET


def test_flatten_nested_and_list():
    pairs = flatten(
        {
            "line_items": [{"price": "price_1", "quantity": 1}],
            "metadata": {"linkyard_id": "x"},
            "active": False,
        }
    )
    as_dict = dict(pairs)
    assert as_dict["line_items[0][price]"] == "price_1"
    assert as_dict["line_items[0][quantity]"] == "1"
    assert as_dict["metadata[linkyard_id]"] == "x"
    assert as_dict["active"] == "false"


def test_redact_masks_live_and_test():
    assert "sk_test_***" in redact("bad " + SECRET, SECRET)
    assert SECRET not in redact("bad " + SECRET, SECRET)
    live = "sk_live_abc"
    assert redact(live, live) == "sk_live_***"


def test_client_sends_bearer_and_form(monkeypatch):
    stripe = MemoryStripe()
    client = StripeClient(SECRET, transport=stripe)
    product = client.create_product(name="n", description="d", local_id="x")
    assert product["id"] == "prod_1"
    method, path, headers, fields = stripe.calls[0]
    assert method == "POST"
    assert path == "/v1/products"
    assert headers["Authorization"] == f"Bearer {SECRET}"
    assert headers["Idempotency-Key"] == "linkyard-prod-x"
    assert fields["name"] == "n"
    assert fields["metadata[linkyard_id]"] == "x"


def test_client_raises_stripe_error():
    stripe = MemoryStripe()
    stripe.fail_on["POST /v1/products"] = (400, "Nope")
    client = StripeClient(SECRET, transport=stripe)
    try:
        client.create_product(name="n", description="", local_id="x")
    except StripeError as exc:
        assert exc.status == 400
        assert "Nope" in exc.message
    else:
        raise AssertionError("expected StripeError")


def test_empty_key_rejected():
    try:
        StripeClient("   ")
    except StripeError:
        pass
    else:
        raise AssertionError("expected StripeError")
