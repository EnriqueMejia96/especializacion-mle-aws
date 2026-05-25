from __future__ import annotations

import json
from typing import Any


def output_fn(prediction: list[dict[str, Any]], accept: str = "application/json") -> tuple[str, str]:
    normalized_accept = (accept or "application/json").split(";")[0].strip().lower()
    if normalized_accept not in {"application/json", "*/*", "text/html", ""}:
        if "application/json" not in (accept or "").lower():
            raise ValueError(f"Unsupported accept type: {accept}")
    if len(prediction) == 1:
        body: Any = prediction[0]
    else:
        body = prediction
    return json.dumps(body, sort_keys=True), "application/json"
