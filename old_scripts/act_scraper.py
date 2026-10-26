import json
import math
import os
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from browserbase import Browserbase
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "jio_fiber.db"

URL = "https://www.actcorp.in/broadband/chennai/broadband-plans"
CITY = "Chennai"

load_dotenv(BASE_DIR / ".env")


EXTRACT = r"""
() => {
    const clean = el =>
        el ? el.textContent.replace(/\s+/g, " ").trim() : "";

    return Array.from(document.querySelectorAll(
        ".select-plans-table input[plan-speed]"
    )).map(input => {
        const cell = input.parentElement;
        const row = input.closest(".select-plans-table");
        const label = cell.querySelector(".desktop-price");

        if (!label || label.getClientRects().length === 0) {
            return null;
        }

        const images = Array.from(
            row.querySelectorAll(".otts-wrap img[alt]")
        ).filter(img => img.getClientRects().length > 0);

        const labels = [...new Set(
            images.map(img => img.alt.trim()).filter(Boolean)
        )];

        return {
            plan_id: input.getAttribute("data-nid"),
            pack_name: clean(row.querySelector("h4")),
            description: clean(row.querySelector("h5.small")),
            price_text: clean(label),
            speed_mbps: Number(input.getAttribute("plan-speed")),
            ott_labels_shown: labels.filter(
                name => name.toLowerCase() !== "more"
            ),
            additional_benefits_not_expanded: labels.some(
                name => name.toLowerCase() === "more"
            )
        };
    }).filter(Boolean);
}
"""


def safe_error(error):
    message = str(error)

    for name in ("BROWSERBASE_API_KEY", "API_KEY"):
        secret = os.getenv(name, "").strip()
        if secret:
            message = message.replace(secret, "[REDACTED]")

    return re.sub(
        r"wss?://[^\s\"'<>]+",
        "[browser connection URL hidden]",
        message,
    )


def scrape():
    api_key = os.getenv("BROWSERBASE_API_KEY", "").strip()
    project_id = os.getenv("BROWSERBASE_PROJECT_ID", "").strip()

    if not api_key or not project_id:
        raise RuntimeError(
            "BROWSERBASE_API_KEY or BROWSERBASE_PROJECT_ID "
            "is missing from .env."
        )

    client = Browserbase(api_key=api_key)
    session = None

    try:
        print("Creating Browserbase session...")
        session = client.sessions.create(project_id=project_id)

        print(
            "Watch session: "
            f"https://www.browserbase.com/sessions/{session.id}"
        )

        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(
                session.connect_url,
                timeout=60000,
            )

            try:
                context = browser.contexts[0]
                page = context.new_page()
                page.set_viewport_size({
                    "width": 1440,
                    "height": 1000,
                })

                print("Opening ACT Chennai plans...")

                response = page.goto(
                    URL,
                    wait_until="domcontentloaded",
                    timeout=60000,
                )

                if response is not None and response.status >= 400:
                    raise RuntimeError(
                        f"ACT returned HTTP {response.status}."
                    )

                page.locator(
                    ".select-plans-table .desktop-price"
                ).first.wait_for(
                    state="visible",
                    timeout=60000,
                )

                # Preserve the working scraper's stabilization check.
                previous = None
                stable_checks = 0
                plans = []

                for _ in range(30):
                    page.wait_for_timeout(1000)
                    plans = page.evaluate(EXTRACT)
                    signature = json.dumps(plans, sort_keys=True)

                    if plans and signature == previous:
                        stable_checks += 1
                    else:
                        stable_checks = 0

                    previous = signature

                    if stable_checks >= 5:
                        break
                else:
                    raise RuntimeError(
                        "ACT's plan grid did not stabilize. "
                        "Database was not changed."
                    )

                city = page.get_by_role(
                    "combobox",
                    name=CITY,
                    exact=True,
                )

                if not city.is_visible():
                    raise RuntimeError(
                        "Could not confirm Chennai as the city. "
                        "Database was not changed."
                    )

                print(f"Read {len(plans)} plan entries.")
                return plans, page.url

            finally:
                try:
                    browser.close()
                except Exception:
                    print("Browser disconnect could not be confirmed.")

    finally:
        if session is not None:
            try:
                client.sessions.update(
                    session.id,
                    project_id=project_id,
                    status="REQUEST_RELEASE",
                )
                print("Browserbase session released.")
            except Exception:
                print(
                    "Check session release in Browserbase: "
                    f"https://www.browserbase.com/sessions/{session.id}"
                )


def prepare_records(plans, source_url):
    checked_at = datetime.now(timezone.utc).isoformat()
    records = {}

    for plan in plans:
        match = re.search(
            r"₹\s*([\d,]+)",
            plan.get("price_text", ""),
        )

        plan_id = str(plan.get("plan_id") or "").strip()
        name = str(plan.get("pack_name") or "").strip()
        speed = plan.get("speed_mbps")

        if (
            not match
            or not plan_id
            or not name
            or not isinstance(speed, (int, float))
            or not math.isfinite(speed)
            or speed <= 0
        ):
            raise ValueError(
                "A plan has an invalid ID, name, price, or speed. "
                "Database was not changed."
            )

        price = int(match.group(1).replace(",", ""))

        if price <= 0:
            raise ValueError(
                "A plan has an invalid price. "
                "Database was not changed."
            )

        record = {
            **plan,
            "plan_id": plan_id,
            "provider": "ACT Fibernet",
            "city": CITY,
            "price_inr": price,
            "billing_period": None,
            "validity": None,
            "tax_status": "not verified",
            "data_allowance": None,
            "details_complete": False,
            "source_url": source_url,
            "last_checked": checked_at,
        }

        # Identical duplicate cards are harmless. Conflicting IDs
        # must be investigated before replacing the existing data.
        if plan_id in records and records[plan_id] != record:
            raise ValueError(
                f"Conflicting data for ACT plan ID {plan_id}. "
                "Database was not changed."
            )

        records[plan_id] = record

    if not records:
        raise ValueError(
            "No valid ACT plans found. Database was not changed."
        )

    return list(records.values())


def backup_database():
    if not DB_PATH.is_file():
        return None

    timestamp = datetime.now(timezone.utc).strftime(
        "%Y%m%d_%H%M%S_%f"
    )

    backup_path = BASE_DIR / f"jio_fiber_backup_{timestamp}.db"

    # SQLite backup also handles committed data in WAL files.
    with closing(
        sqlite3.connect(DB_PATH.as_uri() + "?mode=ro", uri=True)
    ) as source:
        with closing(sqlite3.connect(backup_path)) as destination:
            source.backup(destination)

    print(f"Backup created: {backup_path}")
    return backup_path


def save(plans, source_url):
    records = prepare_records(plans, source_url)
    backup_database()

    deleted_jio = {}
    old_act_count = 0

    with closing(sqlite3.connect(DB_PATH, timeout=30)) as db:
        # All replacements and deletions succeed or roll back together.
        with db:
            db.execute("BEGIN IMMEDIATE")

            db.execute("""
                CREATE TABLE IF NOT EXISTS act_plans (
                    plan_id TEXT NOT NULL,
                    city TEXT NOT NULL,
                    price_inr INTEGER NOT NULL,
                    details_json TEXT NOT NULL,
                    last_checked TEXT NOT NULL,
                    PRIMARY KEY (plan_id, city)
                )
            """)

            old_act_count = db.execute(
                """
                SELECT COUNT(*)
                FROM act_plans
                WHERE city = ? COLLATE NOCASE
                """,
                (CITY,),
            ).fetchone()[0]

            db.execute(
                """
                DELETE FROM act_plans
                WHERE city = ? COLLATE NOCASE
                """,
                (CITY,),
            )

            db.executemany(
                """
                INSERT INTO act_plans (
                    plan_id,
                    city,
                    price_inr,
                    details_json,
                    last_checked
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (
                        plan["plan_id"],
                        plan["city"],
                        plan["price_inr"],
                        json.dumps(plan, ensure_ascii=False),
                        plan["last_checked"],
                    )
                    for plan in records
                ],
            )

            existing_tables = {
                row[0]
                for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }

            # These are the Jio-only tables from this project's
            # previous scrapers. Keep their schemas, remove their rows.
            # Hathway's table is never modified.
            for table in ("plan_details", "plans"):
                if table in existing_tables:
                    count = db.execute(
                        f'SELECT COUNT(*) FROM "{table}"'
                    ).fetchone()[0]

                    db.execute(f'DELETE FROM "{table}"')
                    deleted_jio[table] = count

    for plan in sorted(
        records,
        key=lambda item: (item["price_inr"], item["pack_name"]),
    ):
        print(
            f"Saved: {plan['pack_name']} | "
            f"{plan['speed_mbps']} Mbps | "
            f"Rs {plan['price_inr']}"
        )

    print(f"\nReplaced {old_act_count} old ACT Chennai records.")
    print(f"Saved {len(records)} ACT Chennai plans.")

    for table, count in deleted_jio.items():
        print(f"Deleted {count} Jio records from {table}.")

    print("Hathway records preserved.")
    print(f"Database: {DB_PATH}")
    print("Coverage: public plan grid; detailed terms incomplete.")


if __name__ == "__main__":
    try:
        plans, source_url = scrape()
        save(plans, source_url)
    except Exception as error:
        print(f"Error: {safe_error(error)}")
        raise SystemExit(1)