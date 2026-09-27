"""Read and sanitize atomic initialization telemetry from a server-owned path."""

import json
from datetime import datetime, timezone
from pathlib import Path


MAX_PROGRESS_BYTES = 512_000
STATUSES = {"queued", "generating", "citation_checks_passed", "reused", "needs_review"}
DONE = {"citation_checks_passed", "reused"}


def _count(value: object) -> int:
    if type(value) is not int or value < 0:
        raise ValueError("invalid count")
    return value


def _stamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("invalid timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("invalid timestamp")
    return parsed.astimezone(timezone.utc)


def read_progress(path: str, stale_seconds: int, *, now: datetime | None = None) -> dict:
    """Return explicit availability and a safe, complete snapshot or no progress."""
    unavailable = {"availability": "unavailable", "progress": None}
    if not path:
        return unavailable
    try:
        with Path(path).open("rb") as source:
            raw = source.read(MAX_PROGRESS_BYTES + 1)
        if len(raw) > MAX_PROGRESS_BYTES:
            return unavailable
        source_data = json.loads(raw)
        if type(source_data) is not dict or source_data.get("schema_version") != 1:
            return unavailable
        run_id = source_data["run_id"]
        if not isinstance(run_id, str) or not 1 <= len(run_id) <= 100:
            return unavailable
        started = _stamp(source_data["started_at"])
        updated = _stamp(source_data["updated_at"])
        current = now or datetime.now(timezone.utc)
        if updated < started or (updated - current).total_seconds() > 60:
            return unavailable
        status = source_data["status"]
        if status not in {"running", "completed", "completed_with_failures"}:
            return unavailable
        counts = {name: _count(source_data[name]) for name in ("total", "workers", "completed", "failed", "active", "queued")}
        if counts["workers"] < 1 or counts["workers"] > 1000:
            return unavailable
        if _count(source_data["published"]) > counts["total"]:
            return unavailable
        if sum(counts[name] for name in ("completed", "failed", "active", "queued")) != counts["total"]:
            return unavailable
        products = source_data["products"]
        if type(products) is not dict or len(products) != counts["total"] or counts["total"] > 10_000:
            return unavailable
        items = []
        for product_id, product in products.items():
            if not isinstance(product_id, str) or not 1 <= len(product_id) <= 100 or type(product) is not dict:
                return unavailable
            title, product_status = product.get("title"), product.get("status")
            if not isinstance(title, str) or not 1 <= len(title) <= 500 or product_status not in STATUSES:
                return unavailable
            item = {"id": product_id, "title": title, "status": product_status}
            elapsed = product.get("elapsed_seconds")
            if elapsed is not None:
                if type(elapsed) not in (int, float) or not 0 <= elapsed <= 1_000_000:
                    return unavailable
                item["elapsed_seconds"] = elapsed
            items.append(item)
        if (sum(item["status"] in DONE for item in items) != counts["completed"] or
                sum(item["status"] == "needs_review" for item in items) != counts["failed"] or
                sum(item["status"] == "generating" for item in items) != counts["active"] or
                sum(item["status"] == "queued" for item in items) != counts["queued"]):
            return unavailable
        if status != "running" and (counts["active"] or counts["queued"]):
            return unavailable
        if status == "completed" and counts["failed"]:
            return unavailable
        items.sort(key=lambda item: item["id"])
        progress = {"run_id": run_id, "started_at": started.isoformat(), "updated_at": updated.isoformat(),
                    "status": status, **counts, "published": None, "products": items}
        elapsed = source_data.get("elapsed_seconds")
        if elapsed is not None:
            if type(elapsed) not in (int, float) or not 0 <= elapsed <= 1_000_000:
                return unavailable
            progress["elapsed_seconds"] = elapsed
        age = (current - updated).total_seconds()
        return {"availability": "stale" if age > stale_seconds else "available", "progress": progress}
    except (OSError, ValueError, TypeError, KeyError, UnicodeError):
        return unavailable
