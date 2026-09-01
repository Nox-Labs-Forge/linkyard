from __future__ import annotations

import io
import json
from pathlib import Path

from linkyard.cli import main

SECRET = "sk_test_DO_NOT_PRINT_THIS_SECRET_KEY"


def run(argv, *, environ=None, transport=None):
    out, err = io.StringIO(), io.StringIO()
    code = main(argv, environ=environ or {}, stdout=out, stderr=err, transport=transport)
    return code, out.getvalue(), err.getvalue()


def write_catalog(path: Path, products: list[dict]) -> Path:
    path.write_text(json.dumps({"products": products}, indent=2) + "\n")
    return path


def sample_product(**overrides) -> dict:
    base = {
        "id": "linkyard",
        "name": "linkyard",
        "description": "Payment Link catalog-as-code CLI.",
        "amount_cents": 500,
        "currency": "usd",
    }
    base.update(overrides)
    return base
