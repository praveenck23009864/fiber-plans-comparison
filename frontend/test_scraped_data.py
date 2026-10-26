import json
import math
import sqlite3
from collections import Counter
from contextlib import closing
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "jio_fiber.db"
FRONTEND = "http://127.0.0.1:5173"

PROVIDERS = {
    "JioFiber": "jio",
    "ACT Fibernet": "act",
    "Hathway": "hathway",
}

REQUIRED_COLUMNS = {
    "record_key",
    "provider",
    "plan_name",
    "price",
    "speed_mbps",
    "city",
    "pincode",
    "source_url",
    "availability_verified",
    "metadata_json",
    "raw_json",
    "last_checked",
}

failures = []
warnings = []


def fail(message):
    failures.append(message)
    print(f"FAIL | {message}")


def warn(message):
    warnings.append(message)
    print(f"WARN | {message}")


def finite_number(value):
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def missing(value):
    return (
        value is None
        or isinstance(value, str) and not value.strip()
    )


def parse_object(value, label):
    try:
        parsed = json.loads(value)
        if not isinstance(parsed, dict):
            raise ValueError("Expected a JSON object")
        return parsed
    except (ValueError, TypeError):
        fail(f"{label}: invalid JSON object")
        return {}


def test_frontend(provider, slug, rows):
    # ACT and Hathway frontend routes request Chennai.
    expected_rows = rows
    if slug in ("act", "hathway"):
        expected_rows = [
            row for row in rows
            if (row["city"] or "").strip().casefold() == "chennai"
        ]

    url = f"{FRONTEND}/api/plans/{slug}"

    try:
        with urlopen(url, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))

        if not isinstance(payload, dict):
            fail(f"{provider}: frontend response is not an object")
            return

        data = payload.get("data")
        if not isinstance(data, list):
            fail(f"{provider}: frontend data is not a list")
            return

        if payload.get("count") != len(data):
            fail(f"{provider}: response count differs from data length")

        if len(data) != len(expected_rows):
            fail(
                f"{provider}: database scope has {len(expected_rows)} plans, "
                f"frontend returned {len(data)}"
            )
            return

        if any(
            not isinstance(plan, dict)
            or plan.get("provider") != provider
            for plan in data
        ):
            fail(f"{provider}: frontend contains invalid provider records")
            return

        print(
            f"PASS | Frontend count: {len(data)} "
            f"matches database scope"
        )

        # Compare identifiers when the API exposes record_id.
        if all(plan.get("record_id") for plan in data):
            saved_ids = Counter(
                str(row["record_key"]) for row in expected_rows
            )
            returned_ids = Counter(
                str(plan["record_id"]) for plan in data
            )

            if saved_ids == returned_ids:
                print("PASS | Frontend record IDs match database")
            else:
                fail(f"{provider}: frontend record IDs differ")
        else:
            warn(
                f"{provider}: frontend record_id missing; "
                "count equality alone does not prove identical records"
            )

    except HTTPError as error:
        fail(f"{provider}: frontend returned HTTP {error.code}")
    except URLError:
        fail(
            f"{provider}: frontend unreachable. "
            "Keep FastAPI and npm run dev:local running."
        )
    except (TimeoutError, OSError, ValueError) as error:
        fail(f"{provider}: response check failed ({type(error).__name__})")


def test_provider(provider, slug, rows):
    print("\n" + "=" * 65)
    print(f"Provider: {provider}")
    print(f"Database plans: {len(rows)}")

    if not rows:
        fail(f"{provider}: no saved plans")
        return

    start_failures = len(failures)

    for row in rows:
        identity = str(row["record_key"])
        label = f"{provider} | {identity[:16]}"

        if missing(row["record_key"]):
            fail(f"{label}: record key missing")

        if missing(row["plan_name"]):
            fail(f"{label}: plan name missing")

        price = row["price"]
        if price is not None and (
            not finite_number(price) or price < 0
        ):
            fail(f"{label}: invalid price {price!r}")

        speed = row["speed_mbps"]
        if speed is not None and (
            not finite_number(speed) or speed <= 0
        ):
            fail(f"{label}: invalid speed {speed!r}")

        source = row["source_url"]
        if (
            not isinstance(source, str)
            or not source.startswith(("https://", "http://"))
        ):
            fail(f"{label}: missing or invalid source URL")

        if row["availability_verified"] not in (0, 1):
            fail(f"{label}: availability must be 0 or 1")

        metadata = parse_object(
            row["metadata_json"], f"{label} metadata"
        )
        raw = parse_object(row["raw_json"], f"{label} raw data")

        if not raw:
            warn(f"{label}: no raw source evidence saved")

        if missing(row["last_checked"]):
            warn(f"{label}: last_checked missing")

        # Unknown speed is allowed, especially for vouchers.
        # Missing terms are reported as coverage gaps, not invented.
        if row["availability_verified"] == 1 and missing(row["pincode"]):
            warn(
                f"{label}: availability marked verified without PIN code; "
                "review the verification evidence"
            )

        if not metadata:
            warn(f"{label}: metadata is empty")

    if len(failures) == start_failures:
        print("PASS | Saved record structure and numeric values")

    print("\nCoverage gaps:")
    for field in ("price", "speed_mbps", "city", "pincode", "last_checked"):
        count = sum(missing(row[field]) for row in rows)
        print(f"  {field}: {count}/{len(rows)} missing")

    unverified = sum(
        row["availability_verified"] == 0 for row in rows
    )
    print(f"  availability not verified: {unverified}/{len(rows)}")

    # Matching names/prices/speeds do not necessarily mean duplicates.
    groups = Counter(
        (row["plan_name"], row["price"], row["speed_mbps"])
        for row in rows
    )
    similar_groups = sum(count > 1 for count in groups.values())

    print(
        f"  repeated name/price/speed combinations: {similar_groups}"
        " (may be distinct durations or benefits)"
    )

    test_frontend(provider, slug, rows)


def main():
    print(f"Database: {DB_PATH}")
    print("Read-only audit. No data will be changed.")

    if not DB_PATH.is_file():
        fail("Database file not found")
        return

    with closing(
        sqlite3.connect(
            DB_PATH.as_uri() + "?mode=ro",
            uri=True,
            timeout=10,
        )
    ) as db:
        db.row_factory = sqlite3.Row

        integrity = [
            row[0] for row in db.execute("PRAGMA quick_check")
        ]

        if integrity == ["ok"]:
            print("PASS | SQLite quick check")
        else:
            fail(f"SQLite quick check: {integrity}")

        columns = {
            row["name"]
            for row in db.execute("PRAGMA table_info(broadband_plans)")
        }

        absent = REQUIRED_COLUMNS - columns
        if absent:
            fail(f"Missing database columns: {', '.join(sorted(absent))}")
            return

        rows = db.execute(
            "SELECT * FROM broadband_plans ORDER BY provider, price"
        ).fetchall()

    keys = Counter(row["record_key"] for row in rows)
    duplicates = [key for key, count in keys.items() if count > 1]

    if duplicates:
        fail(f"Duplicate record keys: {len(duplicates)}")
    else:
        print("PASS | No duplicate record keys")

    unexpected = set(row["provider"] for row in rows) - set(PROVIDERS)
    if unexpected:
        warn(f"Unexpected provider names: {unexpected}")

    for provider, slug in PROVIDERS.items():
        provider_rows = [
            row for row in rows if row["provider"] == provider
        ]
        test_provider(provider, slug, provider_rows)


if __name__ == "__main__":
    try:
        main()
    except sqlite3.Error as error:
        fail(f"Database error: {error}")

    print("\n" + "=" * 65)
    print(f"Failures: {len(failures)}")
    print(f"Warnings: {len(warnings)}")
    print("Database unchanged.")
    print(
        "This audit checks saved data and delivery, "
        "not current live website accuracy."
    )