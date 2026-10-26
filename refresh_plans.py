"""Scrape, validate, back up, and refresh broadband plans."""

import argparse
import hashlib
import json
import math
import re
import sqlite3

from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "jio_fiber.db"

FIELDS: tuple[str, ...] = (
    "provider",
    "plan_name",
    "price",
    "speed_mbps",
    "state",
    "city",
    "pincode",
    "offer",
    "offer_price",
    "offer_expiry",
    "source_url",
    "availability_verified",
)

PROVIDERS = {
    "jio": "JioFiber",
    "act": "ACT Fibernet",
    "hathway": "Hathway",
}


def clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def money(text: str) -> float:
    match = re.search(r"₹\s*([\d,]+(?:\.\d+)?)", text)

    if not match:
        raise ValueError(
            f"Missing advertised rupee price: {text!r}"
        )

    value = float(match.group(1).replace(",", ""))

    if not math.isfinite(value) or value <= 0:
        raise ValueError("Invalid price")

    return value


def speed(text: str) -> float | None:
    match = re.search(
        r"(\d+(?:\.\d+)?)\s*(Mbps|Gbps)",
        text,
        re.IGNORECASE,
    )

    if not match:
        return None

    value = float(match.group(1))

    if match.group(2).lower() == "gbps":
        value *= 1000

    if not math.isfinite(value) or value <= 0:
        raise ValueError("Invalid speed")

    return value


def make_record(
    provider: str,
    name: Any,
    price: float,
    mbps: float | None,
    url: str,
    raw: dict[str, Any],
    identity: str,
    metadata: dict[str, Any],
    city: str | None = None,
) -> dict[str, Any]:
    name = clean(name)

    if (
        not name
        or not identity
        or not isinstance(url, str)
        or not url.startswith("https://")
    ):
        raise ValueError(
            "Missing name, source ID, or HTTPS source"
        )

    if not math.isfinite(price) or price <= 0:
        raise ValueError("Invalid price")

    if mbps is not None:
        if not math.isfinite(mbps) or mbps <= 0:
            raise ValueError("Invalid speed")

    # Explicit typing permits both public fields and internal keys.
    result: dict[str, Any] = {
        "provider": provider,
        "plan_name": name,
        "price": price,
        "speed_mbps": mbps,
        "state": "Tamil Nadu" if city == "Chennai" else None,
        "city": city,
        "pincode": None,
        "offer": None,
        "offer_price": None,
        "offer_expiry": None,
        "source_url": url,
        "availability_verified": False,
    }

    result["_key"] = hashlib.sha256(
        identity.encode("utf-8")
    ).hexdigest()

    result["_metadata"] = metadata
    result["_raw"] = raw

    return result


def normalize(
    provider: str,
    raw_plans: list[dict[str, Any]],
    url: str | None = None,
    tax_note: str | None = None,
) -> list[dict[str, Any]]:
    if provider not in PROVIDERS:
        raise ValueError(f"Unknown provider: {provider}")

    result: dict[str, dict[str, Any]] = {}

    for raw in raw_plans:
        if provider == "act":
            if not url:
                raise ValueError("ACT source URL missing")

            source_id = clean(raw.get("plan_id"))

            if not source_id:
                raise ValueError("ACT source ID missing")

            mbps = raw.get("speed_mbps")

            if (
                not isinstance(mbps, (int, float))
                or isinstance(mbps, bool)
            ):
                raise ValueError("ACT speed missing")

            record = make_record(
                provider=PROVIDERS[provider],
                name=raw.get("pack_name"),
                price=money(raw.get("price_text", "")),
                mbps=mbps,
                url=url,
                raw=raw,
                identity="act|Chennai|" + source_id,
                metadata={
                    "price_basis": "unverified",
                    "validity_days": None,
                    "tax_status": "unverified",
                    "benefits": raw.get("ott_labels_shown", []),
                    "details_complete": False,
                },
                city="Chennai",
            )

        elif provider == "hathway":
            if not url:
                raise ValueError("Hathway source URL missing")

            mbps = speed(raw.get("speed_text", ""))

            if mbps is None:
                raise ValueError("Hathway speed missing")

            price = money(raw.get("price_text", ""))

            # A price/speed change is an update, not a new identity.
            identity = json.dumps([
                clean(raw.get("plan_name")),
                clean(raw.get("duration_options_text")),
            ])

            record = make_record(
                provider=PROVIDERS[provider],
                name=raw.get("plan_name"),
                price=price,
                mbps=mbps,
                url=url,
                raw=raw,
                identity="hathway|Chennai|" + identity,
                metadata={
                    "price_basis": (
                        clean(raw.get("price_basis"))
                        or "unverified"
                    ),
                    "duration_options_text": raw.get(
                        "duration_options_text"
                    ),
                    "validity_days": None,
                    "tax_status": tax_note or "unverified",
                    "benefits": raw.get("benefits", []),
                    "details_complete": False,
                },
                city="Chennai",
            )

        else:
            price = money(raw["heading"])

            fields = {
                clean(key).lower(): clean(value)
                for key, value in raw["fields"].items()
            }

            mbps = speed(fields.get("speed", ""))

            validity = (
                fields.get("plan validity")
                or fields.get("validity")
            )

            day_match = re.fullmatch(
                r"(\d+)\s*days?",
                validity or "",
                re.IGNORECASE,
            )

            # This is a generated display label.
            # The provider's original heading remains in raw_json.
            name = (
                f"{raw['category']} - "
                f"{clean(raw['heading'])}"
            )

            heading_without_spaces = re.sub(
                r"\s+",
                "",
                raw["heading"],
            ).upper()

            record = make_record(
                provider=PROVIDERS[provider],
                name=name,
                price=price,
                mbps=mbps,
                url=raw["source_url"],
                raw=raw,
                identity=(
                    "jio|"
                    + raw["category"]
                    + "|"
                    + raw["plan_id"]
                ),
                metadata={
                    "category": raw["category"],
                    "plan_name_generated": True,
                    "price_basis": "plan voucher price",
                    "validity_text": validity,
                    "validity_days": (
                        int(day_match.group(1))
                        if day_match
                        else None
                    ),
                    "tax_status": (
                        "excluded"
                        if "+GST" in heading_without_spaces
                        else "unverified"
                    ),
                    "benefits": raw.get("subscriptions", []),
                    "notes": raw.get("notes", []),
                    "details_complete": False,
                },
            )

        previous = result.get(record["_key"])

        if previous is not None:
            # Ignore cloned raw DOM differences, but reject
            # conflicting normalized values or metadata.
            previous_values = {
                key: value
                for key, value in previous.items()
                if key != "_raw"
            }

            current_values = {
                key: value
                for key, value in record.items()
                if key != "_raw"
            }

            if previous_values != current_values:
                raise ValueError(
                    "Conflicting duplicate source plan; "
                    "refresh aborted"
                )

        result[record["_key"]] = record

    if not result:
        raise ValueError(
            "Empty extraction; old records retained"
        )

    return list(result.values())


def backup_database(path: Path) -> None:
    path = path.resolve()

    if not path.is_file():
        return

    stamp = datetime.now(timezone.utc).strftime(
        "%Y%m%d_%H%M%S_%f"
    )

    backup = path.with_name(
        f"{path.stem}_backup_{stamp}.db"
    )

    with closing(
        sqlite3.connect(
            path.as_uri() + "?mode=ro",
            uri=True,
        )
    ) as source:
        with closing(sqlite3.connect(backup)) as destination:
            source.backup(destination)

    print(f"Backup: {backup}")


def replace_provider(provider, records, path=DB_PATH, *, evidence=None, job_id=None):
    """Compatibility entry point; destructive replacement is no longer supported."""
    from refresh_store import apply_batch
    if not evidence or not job_id:
        raise ValueError("Use the refresh API/CLI: verified coverage and an owned refresh job are required")
    return apply_batch(path, provider, records, evidence, job_id)


def main():
    """CLI uses the same durable queue and lease as the backend."""
    import asyncio
    from refresh_service import RefreshService
    parser = argparse.ArgumentParser(description="Queue official-provider catalog refreshes.")
    parser.add_argument("--provider", choices=["all", *PROVIDERS], default="all")
    args = parser.parse_args()

    async def run():
        from dotenv import load_dotenv
        load_dotenv(BASE_DIR / ".env")
        service = RefreshService(DB_PATH)
        await service.start()
        try:
            batch = service.enqueue(args.provider)
            print("Queued refresh batch:", batch["batch_id"])
            while True:
                with closing(service.connect()) as db:
                    rows = db.execute("SELECT provider,status,error,added,updated,unchanged,inactive FROM catalog_refresh_jobs WHERE batch_id=?", (batch["batch_id"],)).fetchall()
                if rows and all(r["status"] in ("completed", "failed") for r in rows):
                    for row in rows:
                        print(dict(row))
                    return all(r["status"] == "completed" for r in rows)
                await asyncio.sleep(2)
        finally:
            await service.close()

    if not asyncio.run(run()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
