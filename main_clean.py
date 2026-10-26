import json
import os
import re
import secrets
import sqlite3
import time

from collections import OrderedDict
from contextlib import closing
from pathlib import Path
from threading import Lock
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Security
from fastapi.responses import Response
from fastapi.security import APIKeyHeader


# --------------------------------------------------
# Configuration
# --------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "jio_fiber.db"

load_dotenv(BASE_DIR / ".env")

API_KEY = os.getenv("API_KEY", "").strip()

if not API_KEY:
    raise RuntimeError(
        "API_KEY is missing from the .env file beside main_clean.py."
    )

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

CACHE_TTL_SECONDS = 30
CACHE_MAX_ENTRIES = 128

CacheKey = tuple[str, str | None, str | None]

cache: OrderedDict[CacheKey, tuple[float, bytes]] = OrderedDict()
cache_lock = Lock()
catalog_revision = None


# --------------------------------------------------
# Application and authentication
# --------------------------------------------------

app = FastAPI(
    title="Broadband Plans API",
    version="2.3.0",
    description=(
        "Saved JioFiber, ACT Fibernet, and Hathway plans. "
        "Additional details are returned when available in saved records. "
        "Prices may use different billing periods. "
        "Service availability is not confirmed."
    ),
)

key_header = APIKeyHeader(
    name="X-API-Key",
    auto_error=False,
)


def verify_key(key: str | None = Security(key_header)) -> None:
    """Check authentication on every request, including cache hits."""
    if not key or not secrets.compare_digest(
        key.encode("utf-8"),
        API_KEY.encode("utf-8"),
    ):
        raise HTTPException(
            status_code=401,
            detail="Missing or invalid API key",
            headers={"Cache-Control": "no-store"},
        )


# --------------------------------------------------
# Data helpers
# --------------------------------------------------

def text_or_none(value: Any) -> str | None:
    if isinstance(value, str):
        return value.strip() or None

    return None


def normalize_filter(value: str | None) -> str | None:
    return text_or_none(value)


def text_list(value: Any) -> list[str]:
    if value is None:
        return []

    if not isinstance(value, list):
        raise ValueError("Expected a list in saved plan details")

    return [
        item.strip()
        for item in value
        if isinstance(item, str) and item.strip()
    ]


def read_json_object(value: str, label: str) -> dict[str, Any]:
    result = json.loads(value)

    if not isinstance(result, dict):
        raise ValueError(f"{label} must be a JSON object")

    return result


def parse_validity(
    validity_text: str | None,
) -> dict[str, Any]:
    """
    Parse explicit day counts only.

    Examples:
        30 days -> 30
        180 days + 15 days -> 195
        360 days + 30 days -> 390

    Calendar months and ambiguous text remain unconverted.
    """
    result: dict[str, Any] = {
        "validity_text": validity_text,
        "base_validity_days": None,
        "bonus_validity_days": None,
        "validity_days": None,
    }

    if not validity_text:
        return result

    if not re.fullmatch(
        r"\d+\s*days?(?:\s*\+\s*\d+\s*days?)*",
        validity_text,
        flags=re.IGNORECASE,
    ):
        return result

    parts = [
        int(value)
        for value in re.findall(
            r"(\d+)\s*days?",
            validity_text,
            flags=re.IGNORECASE,
        )
    ]

    result["base_validity_days"] = parts[0]
    result["bonus_validity_days"] = sum(parts[1:])
    result["validity_days"] = sum(parts)

    return result


def additional_details(
    row: sqlite3.Row,
    metadata: dict[str, Any],
    raw: dict[str, Any],
) -> dict[str, Any]:
    """Expose saved source evidence without inventing missing terms."""
    original_fields = raw.get("fields")

    if original_fields is None:
        original_fields = {}

    if not isinstance(original_fields, dict):
        raise ValueError("Source detail fields must be an object")

    normalized_fields = {
        str(key).strip().casefold(): value
        for key, value in original_fields.items()
    }

    def field(*names: str) -> str | None:
        for name in names:
            value = text_or_none(
                normalized_fields.get(name.casefold())
            )
            if value is not None:
                return value

        return None

    validity_text = (
        text_or_none(metadata.get("validity_text"))
        or field("Plan validity", "Validity")
    )

    validity = parse_validity(validity_text)

    price_basis = text_or_none(metadata.get("price_basis"))
    if price_basis and price_basis.casefold() == "unverified":
        price_basis = None

    tax_evidence = text_or_none(metadata.get("tax_status"))

    if tax_evidence in ("included", "excluded"):
        tax_status = tax_evidence
        tax_note = None
    else:
        tax_status = "unverified"
        tax_note = (
            tax_evidence
            if tax_evidence
            and tax_evidence.casefold() != "unverified"
            else None
        )

    unexpanded = raw.get("additional_benefits_not_expanded")
    if not isinstance(unexpanded, bool):
        unexpanded = None

    details: dict[str, Any] = {
        "record_id": row["record_key"],
        "provider_plan_id": text_or_none(raw.get("plan_id")),
        "category": (
            text_or_none(metadata.get("category"))
            or text_or_none(raw.get("category"))
        ),
        "last_checked": row["last_checked"],

        "price_basis": price_basis,

        # A displayed monthly rate does not necessarily mean
        # the customer can purchase one month.
        "billing_period": None,
        "duration_options_text": (
            text_or_none(metadata.get("duration_options_text"))
            or text_or_none(raw.get("duration_options_text"))
        ),

        "tax_status": tax_status,
        "tax_note": tax_note,
        "tax_note_scope": "page" if tax_note else None,
        "total_payable": None,

        "speed_text": (
            field("Speed")
            or text_or_none(raw.get("speed_text"))
        ),
        "data_allowance_text": field(
            "Total data",
            "Data allowance",
            "Data",
        ),
        "fup_limit_text": field(
            "FUP limit",
            "FUP",
            "Fair usage policy",
        ),
        "post_fup_speed_text": field(
            "Post FUP speed",
            "Speed after FUP",
            "Post FUP",
        ),
        "calling_benefits_text": field(
            "Voice",
            "Voice calls",
            "Calling",
            "Calls",
        ),
        "talktime_text": field("Talktime"),
        "isd_talktime_text": field("ISD Talktime"),
        "tv_benefits_text": field("On-demand TV"),
        "long_term_benefit_text": field("Long Term Plan Benefit"),

        # Keep conditional installation wording intact.
        "installation_terms": text_or_none(
            raw.get("installation_terms")
        ),
        "installation_charge": None,
        "router_charge": None,
        "security_deposit": None,

        "subscriptions": text_list(raw.get("subscriptions")),

        # Subscription conditions remain available in notes.
        # They are not assumed to apply to every subscription.
        "subscription_terms": None,
        "ott_labels_shown": text_list(raw.get("ott_labels_shown")),
        "benefits": text_list(metadata.get("benefits")),
        "notes": text_list(
            metadata.get("notes") or raw.get("notes")
        ),
        "description": text_or_none(raw.get("description")),

        "additional_benefits_not_expanded": unexpanded,
        "details_complete": False,

        # Preserve original labels and source text.
        "source_detail_fields": original_fields,
        "source_text": (
            text_or_none(raw.get("raw_details"))
            or text_or_none(raw.get("raw_text"))
        ),
    }

    details.update(validity)
    return details


# --------------------------------------------------
# Read saved plans
# --------------------------------------------------

def load_response_bytes(
    provider: str,
    city: str | None = None,
    category: str | None = None,
) -> bytes:
    if not DB_PATH.is_file():
        raise HTTPException(
            status_code=503,
            detail="Database missing; run refresh_plans.py",
        )

    selected_fields = list(FIELDS) + [
        "record_key",
        "metadata_json",
        "raw_json",
        "last_checked",
        "last_seen_at", "updated_at", "currency", "billing_period", "pricing_conditions",
    ]

    query = (
        "SELECT "
        + ",".join(selected_fields)
        + " FROM broadband_plans WHERE provider=? AND is_active=1"
    )
    parameters: list[Any] = [provider]

    if city:
        query += " AND city=? COLLATE NOCASE"
        parameters.append(city)

    query += " ORDER BY price, plan_name, record_key"

    try:
        with closing(
            sqlite3.connect(
                DB_PATH.as_uri() + "?mode=ro",
                uri=True,
                timeout=10,
            )
        ) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(query, parameters).fetchall()

        plans: list[dict[str, Any]] = []

        for row in rows:
            metadata = read_json_object(
                row["metadata_json"],
                "Metadata",
            )

            if category:
                saved_category = text_or_none(
                    metadata.get("category")
                )

                if (
                    saved_category is None
                    or saved_category.casefold() != category.casefold()
                ):
                    continue

            raw = read_json_object(
                row["raw_json"],
                "Raw data",
            )

            plan: dict[str, Any] = {
                name: row[name]
                for name in FIELDS
            }

            plan["availability_verified"] = bool(
                plan["availability_verified"]
            )

            plan.update(
                additional_details(row, metadata, raw)
            )
            plan.update({key: row[key] for key in ('last_seen_at','updated_at','currency','billing_period','pricing_conditions')})
            plans.append(plan)

        payload = {
            "provider": provider,
            "count": len(plans),
            "data": plans,
        }

        return json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")

    except sqlite3.Error as error:
        raise HTTPException(
            status_code=503,
            detail=(
                "Cleaned plan data is unavailable. "
                "Check the database and refresh_plans.py."
            ),
        ) from error

    except (ValueError, TypeError) as error:
        raise HTTPException(
            status_code=500,
            detail="Stored plan data or metadata is invalid.",
        ) from error


# --------------------------------------------------
# Response cache
# --------------------------------------------------

def read_plans(
    provider: str,
    city: str | None = None,
    category: str | None = None,
) -> Response:
    global catalog_revision
    city = normalize_filter(city)
    category = normalize_filter(category)

    cache_key: CacheKey = (
        provider,
        city,
        category.casefold() if category else None,
    )

    # Synchronous endpoints run in worker threads.
    # This lock prevents simultaneous duplicate cache fills.
    with cache_lock:
        # Observe commits from the CLI or another API worker before using memory cache.
        with closing(sqlite3.connect(DB_PATH, timeout=10)) as revision_db:
            revision = revision_db.execute("SELECT MAX(finished_at) FROM catalog_refresh_jobs WHERE status='completed'").fetchone()[0]
        if revision != catalog_revision:
            cache.clear()
            catalog_revision = revision
        now = time.monotonic()
        entry = cache.get(cache_key)

        if entry is not None and entry[0] > now:
            body = entry[1]
            cache.move_to_end(cache_key)
            cache_status = "HIT"

        else:
            cache.pop(cache_key, None)

            # Failed loads are never cached.
            body = load_response_bytes(
                provider,
                city=city,
                category=category,
            )

            now = time.monotonic()

            expired_keys = [
                key
                for key, (expires_at, _) in cache.items()
                if expires_at <= now
            ]

            for key in expired_keys:
                del cache[key]

            cache[cache_key] = (
                now + CACHE_TTL_SECONDS,
                body,
            )
            cache.move_to_end(cache_key)

            while len(cache) > CACHE_MAX_ENTRIES:
                cache.popitem(last=False)

            cache_status = "MISS"

    return Response(
        content=body,
        media_type="application/json",
        headers={
            "Cache-Control": "no-store",
            "X-Cache": cache_status,
        },
    )


# --------------------------------------------------
# Provider routes
# --------------------------------------------------

@app.get(
    "/api/jio/plans",
    tags=["JioFiber"],
    dependencies=[Security(verify_key)],
)
def jio_plans(category: str | None = None):
    return read_plans(
        "JioFiber",
        category=category,
    )


@app.get(
    "/api/act/plans",
    tags=["ACT Fibernet"],
    dependencies=[Security(verify_key)],
)
def act_plans(city: str = "Chennai"):
    return read_plans(
        "ACT Fibernet",
        city=city,
    )


@app.get(
    "/api/hathway/plans",
    tags=["Hathway"],
    dependencies=[Security(verify_key)],
)
def hathway_plans(city: str = "Chennai"):
    return read_plans(
        "Hathway",
        city=city,
    )


# Address-specific US lookups use separate additive tables and the same API-key guard.
from optimum_jobs import install as install_optimum

install_optimum(app, DB_PATH, [Security(verify_key)])

from refresh_service import install as install_refresh

def invalidate_catalog_cache():
    with cache_lock:
        cache.clear()

install_refresh(app, DB_PATH, [Security(verify_key)], invalidate_catalog_cache)
