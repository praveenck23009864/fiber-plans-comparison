import hashlib
import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from browserbase import Browserbase
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "jio_fiber.db"

URL = "https://www.hathway.com/Broadband/HomeBroadband/Chennai"
CITY = "Chennai"
PROVIDER = "Hathway"

load_dotenv(BASE_DIR / ".env")


# These selectors match the public Hathway plan grid.
# If Hathway changes its layout, they may need updating.
EXTRACT = r"""
(cards) => {
    const clean = (value) =>
        (value || "").replace(/\s+/g, " ").trim();

    const text = (card, selector) =>
        clean(card.querySelector(selector)?.textContent);

    return cards.map((card) => ({
        plan_name: text(card, ".head"),
        speed_text: text(card, ".planSpeed h3"),
        price_text: text(card, ".planPrice"),
        price_basis: text(card, ".planSpeed span"),
        duration_options_text: text(card, ".planMonthlyOpions"),
        installation_terms: text(card, ".planFooter"),
        benefits: Array.from(
            card.querySelectorAll(".technology li p")
        ).map((item) => clean(item.textContent)).filter(Boolean),
        raw_text: clean(card.textContent)
    }));
}
"""


def safe_error(error):
    """Avoid printing API keys or browser connection URLs."""
    message = str(error)

    for name in ("BROWSERBASE_API_KEY", "API_KEY"):
        secret = os.getenv(name, "").strip()
        if secret:
            message = message.replace(secret, "[REDACTED]")

    message = re.sub(
        r"wss?://[^\s\"'<>]+",
        "[browser connection URL hidden]",
        message,
    )

    return message


def scrape():
    api_key = os.getenv("BROWSERBASE_API_KEY", "").strip()
    project_id = os.getenv("BROWSERBASE_PROJECT_ID", "").strip()

    if not api_key or not project_id:
        raise RuntimeError(
            "Add BROWSERBASE_API_KEY and "
            "BROWSERBASE_PROJECT_ID to your .env file."
        )

    client = Browserbase(api_key=api_key)
    session = None

    try:
        print("Creating Browserbase session...")

        session = client.sessions.create(
            project_id=project_id,
        )

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

                print("Opening Hathway Chennai plans...")

                response = page.goto(
                    URL,
                    wait_until="domcontentloaded",
                    timeout=60000,
                )

                if response is not None and response.status >= 400:
                    raise RuntimeError(
                        f"Hathway returned HTTP {response.status}."
                    )

                page.get_by_role(
                    "heading",
                    name=re.compile(
                        r"Explore Plans in Chennai",
                        re.IGNORECASE,
                    ),
                ).wait_for(
                    state="visible",
                    timeout=30000,
                )

                cards = page.locator(".bussinesPlan.homeuser")

                cards.first.wait_for(
                    state="attached",
                    timeout=30000,
                )

                # Wait for actual names, prices and speeds,
                # rather than saving empty loading placeholders.
                page.wait_for_function(
                    r"""() => {
                        const cards = Array.from(
                            document.querySelectorAll(
                                ".bussinesPlan.homeuser"
                            )
                        );

                        return cards.length > 0 && cards.every(card => {
                            const text = selector =>
                                card.querySelector(selector)
                                    ?.textContent?.trim() || "";

                            return text(".head")
                                && /₹\s*[\d,]+/.test(text(".planPrice"))
                                && /[\d.]+\s*(Mbps|Gbps)/i.test(
                                    text(".planSpeed h3")
                                );
                        });
                    }""",
                    timeout=30000,
                )

                plans = cards.evaluate_all(EXTRACT)

                if not plans:
                    raise RuntimeError(
                        "No Hathway plan cards found. "
                        "The website layout may have changed."
                    )

                body_text = page.locator("body").inner_text()
                body_text = re.sub(r"\s+", " ", body_text)

                tax_match = re.search(
                    r"Installation charges and taxes extra "
                    r"as applicable\.?",
                    body_text,
                    re.IGNORECASE,
                )

                tax_note = (
                    tax_match.group(0) if tax_match else None
                )

                return plans, page.url, tax_note

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
                    "Could not confirm session release. "
                    "Check the session in your Browserbase dashboard: "
                    f"{session.id}"
                )


def prepare_records(plans, source_url, tax_note):
    """Validate everything before writing to SQLite."""
    checked_at = datetime.now(timezone.utc).isoformat()
    unique = {}

    for plan in plans:
        name = plan["plan_name"].strip()

        price_match = re.search(
            r"₹\s*([\d,]+(?:\.\d+)?)",
            plan["price_text"],
        )

        speed_match = re.search(
            r"([\d.]+)\s*(Mbps|Gbps)",
            plan["speed_text"],
            re.IGNORECASE,
        )

        if not name or not price_match or not speed_match:
            raise RuntimeError(
                "A plan could not be parsed. "
                "No database changes were made. "
                f"Card text: {plan['raw_text'][:250]}"
            )

        price = float(price_match.group(1).replace(",", ""))

        # Preserve whole-rupee values as integers.
        if price.is_integer():
            price = int(price)

        speed_mbps = float(speed_match.group(1))

        if speed_match.group(2).lower() == "gbps":
            speed_mbps *= 1000

        if speed_mbps.is_integer():
            speed_mbps = int(speed_mbps)

        # This is our own stable identifier, not a Hathway plan ID.
        identity = f"{PROVIDER}|{CITY}|{name}|{speed_mbps}"
        plan_id = hashlib.sha256(
            identity.encode("utf-8")
        ).hexdigest()[:24]

        # Keep the original duration text too, because offers may
        # describe bonus months separately.
        duration_text = plan["duration_options_text"]
        duration_options = None

        if re.search(r"\bmonths?\b", duration_text, re.IGNORECASE):
            duration_options = sorted({
                int(value)
                for value in re.findall(r"\b\d+\b", duration_text)
            })

        record = {
            **plan,
            "plan_id": plan_id,
            "provider": PROVIDER,
            "city": CITY,
            "speed_mbps": speed_mbps,
            "advertised_price_inr": price,
            "currency": "INR",
            "duration_options_months": duration_options,

            # Do not invent details missing from the public grid.
            "upfront_price_inr": None,
            "validity_days": None,
            "data_allowance": None,
            "included_ott_subscriptions": None,
            "tax_note": tax_note,
            "details_complete": False,

            "source_url": source_url,
            "last_checked": checked_at,
        }

        # The carousel may contain cloned cards.
        unique[plan_id] = record

    if not unique:
        raise RuntimeError(
            "No valid plans found. Database was not changed."
        )

    return list(unique.values())


def save(plans, source_url, tax_note):
    records = prepare_records(plans, source_url, tax_note)

    with sqlite3.connect(DB_PATH, timeout=30) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS hathway_plans (
                plan_id TEXT NOT NULL,
                city TEXT NOT NULL,
                advertised_price_inr NUMERIC NOT NULL,
                details_json TEXT NOT NULL,
                last_checked TEXT NOT NULL,
                PRIMARY KEY (plan_id, city)
            )
            """
        )

        connection.executemany(
            """
            INSERT INTO hathway_plans (
                plan_id,
                city,
                advertised_price_inr,
                details_json,
                last_checked
            )
            VALUES (?, ?, ?, ?, ?)

            ON CONFLICT(plan_id, city) DO UPDATE SET
                advertised_price_inr =
                    excluded.advertised_price_inr,
                details_json = excluded.details_json,
                last_checked = excluded.last_checked
            """,
            [
                (
                    record["plan_id"],
                    record["city"],
                    record["advertised_price_inr"],
                    json.dumps(record, ensure_ascii=False),
                    record["last_checked"],
                )
                for record in records
            ],
        )

    for record in sorted(
        records,
        key=lambda item: item["speed_mbps"],
    ):
        price_basis = (
            record["price_basis"] or "price basis not shown"
        )

        print(
            f"Saved: {record['plan_name']} | "
            f"{record['speed_mbps']} Mbps | "
            f"Rs {record['advertised_price_inr']} | "
            f"{price_basis}"
        )

    print()
    print(f"Saved or updated {len(records)} Hathway Chennai plans.")
    print(f"Database: {DB_PATH}")
    print("Table: hathway_plans")
    print(
        "Coverage: public plan grid. "
        "Upfront prices and detailed benefits may be incomplete."
    )
    print("Previously saved plans absent from this run are retained.")


def main():
    try:
        plans, source_url, tax_note = scrape()
        save(plans, source_url, tax_note)
    except Exception as error:
        print(f"Error: {safe_error(error)}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()