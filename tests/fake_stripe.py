"""In-memory Stripe stand-in. No network."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse


class MemoryStripe:
    def __init__(self):
        self.products: dict[str, dict] = {}
        self.prices: dict[str, dict] = {}
        self.links: dict[str, dict] = {}
        self.calls: list[tuple[str, str, dict, dict]] = []
        self._n = 0
        self.fail_on: dict[str, tuple[int, str]] = {}

    def _id(self, prefix: str) -> str:
        self._n += 1
        return f"{prefix}_{self._n}"

    def __call__(self, method: str, url: str, headers: dict[str, str], body: bytes | None):
        parsed = urlparse(url)
        path = parsed.path
        fields = {}
        if body:
            fields = {k: v[-1] for k, v in parse_qs(body.decode(), keep_blank_values=True).items()}
        elif parsed.query:
            fields = {k: v[-1] for k, v in parse_qs(parsed.query, keep_blank_values=True).items()}
        self.calls.append((method, path, headers, fields))
        key = f"{method} {path}"
        if key in self.fail_on:
            status, message = self.fail_on[key]
            return status, {"error": {"message": message}}
        if path == "/v1/products" and method == "POST":
            pid = self._id("prod")
            obj = {
                "id": pid,
                "object": "product",
                "name": fields.get("name"),
                "description": fields.get("description"),
                "metadata": {"linkyard_id": fields.get("metadata[linkyard_id]")},
                "active": True,
            }
            self.products[pid] = obj
            return 200, obj
        if path.startswith("/v1/products/") and method == "POST":
            pid = path.rsplit("/", 1)[-1]
            obj = self.products.get(pid)
            if not obj:
                return 404, {"error": {"message": f"No such product: {pid}"}}
            if "name" in fields:
                obj["name"] = fields["name"]
            if "description" in fields:
                obj["description"] = fields["description"]
            return 200, obj
        if path == "/v1/prices" and method == "POST":
            pid = self._id("price")
            obj = {
                "id": pid,
                "object": "price",
                "product": fields.get("product"),
                "unit_amount": int(fields["unit_amount"]),
                "currency": fields.get("currency"),
                "active": True,
                "metadata": {"linkyard_id": fields.get("metadata[linkyard_id]")},
            }
            self.prices[pid] = obj
            return 200, obj
        if path.startswith("/v1/prices/") and method == "POST":
            pid = path.rsplit("/", 1)[-1]
            obj = self.prices.get(pid)
            if not obj:
                return 404, {"error": {"message": f"No such price: {pid}"}}
            if fields.get("active") == "false":
                obj["active"] = False
            return 200, obj
        if path == "/v1/payment_links" and method == "POST":
            pid = self._id("plink")
            obj = {
                "id": pid,
                "object": "payment_link",
                "url": f"https://buy.stripe.com/test_{pid}",
                "active": True,
                "line_items": {
                    "data": [
                        {
                            "price": fields.get("line_items[0][price]"),
                            "quantity": int(fields.get("line_items[0][quantity]", "1")),
                        }
                    ]
                },
                "metadata": {"linkyard_id": fields.get("metadata[linkyard_id]")},
            }
            if fields.get("after_completion[type]"):
                obj["after_completion"] = {
                    "type": fields["after_completion[type]"],
                    "redirect": {"url": fields.get("after_completion[redirect][url]")},
                }
            self.links[pid] = obj
            return 200, obj
        if path.startswith("/v1/payment_links/") and method == "POST":
            pid = path.rsplit("/", 1)[-1]
            obj = self.links.get(pid)
            if not obj:
                return 404, {"error": {"message": f"No such payment_link: {pid}"}}
            if fields.get("active") == "false":
                obj["active"] = False
            if fields.get("after_completion[type]"):
                obj["after_completion"] = {
                    "type": fields["after_completion[type]"],
                    "redirect": {"url": fields.get("after_completion[redirect][url]")},
                }
            return 200, obj
        return 404, {"error": {"message": f"unhandled {method} {path}"}}
