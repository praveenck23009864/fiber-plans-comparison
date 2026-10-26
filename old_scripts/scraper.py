import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright, expect

URL = (
    "https://www.jio.com/selfcare/plans/fiber/"
    "fiber-prepaid-plans-list/"
)

DB_PATH = Path(__file__).with_name("jio_fiber.db")

CATEGORIES = [
    "Monthly",
    "Semi-Annual",
    "Annual",
    "ISD",
    "Top-up",
    "Data Sachet",
]

# Group buttons have names such as "150 Mbps (1 plan)".
GROUP_PATTERN = re.compile(r"\(\d+\s+plans?\)$")

DETAIL_PATTERN = re.compile(r"^More info .* plan$")


def prepare_database():
    with sqlite3.connect(DB_PATH) as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS plan_details (
                plan_id TEXT NOT NULL,
                category TEXT NOT NULL,
                price_inr INTEGER NOT NULL,
                details_json TEXT NOT NULL,
                source_url TEXT NOT NULL,
                last_checked TEXT NOT NULL,
                PRIMARY KEY (plan_id, category)
            )
        """)


def save_plan(plan):
    with sqlite3.connect(DB_PATH) as db:
        db.execute("""
            INSERT INTO plan_details (
                plan_id, category, price_inr,
                details_json, source_url, last_checked
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(plan_id, category)
            DO UPDATE SET
                price_inr = excluded.price_inr,
                details_json = excluded.details_json,
                source_url = excluded.source_url,
                last_checked = excluded.last_checked
        """, (
            plan["plan_id"],
            plan["category"],
            plan["price_inr"],
            json.dumps(plan, ensure_ascii=False),
            plan["source_url"],
            plan["last_checked"],
        ))


def collect_visible_plans(page, category, collected):
    buttons = page.get_by_role("button", name=DETAIL_PATTERN)

    # Only collect cards that are currently expanded and visible.
    visible_indices = [
        i for i in range(buttons.count())
        if buttons.nth(i).is_visible()
    ]

    if not visible_indices:
        raise RuntimeError("No visible plan detail buttons found.")

    for i in visible_indices:
        button = buttons.nth(i)
        label = button.get_attribute("aria-label")
        button.click()

        table = page.get_by_role("table", name="Plan details")
        table.wait_for(state="visible", timeout=20000)

        popup = page.locator(".j-modal-content:visible")

        # Read the actual detail table and subscription names.
        plan = popup.evaluate("""
            root => {
                const text = el => el ? el.innerText.trim() : "";
                const fields = {};

                for (const row of root.querySelectorAll(
                    '[role="table"][aria-label="Plan details"] [role="row"]'
                )) {
                    const key = row.querySelector('[role="rowheader"]');
                    const value = row.querySelector('[role="cell"]');
                    if (key && value) {
                        fields[text(key)] = text(value);
                    }
                }

                const subscriptions = Array.from(
                    root.querySelectorAll(
                        'ul[aria-label="Subscriptions"] li'
                    )
                ).map(text);

                const notes = Array.from(
                    root.querySelectorAll(
                        'ul:not([aria-label="Subscriptions"]) li'
                    )
                ).map(text);

                const links = Array.from(
                    root.querySelectorAll('a[href]')
                ).map(a => ({
                    text: text(a),
                    url: a.href
                }));

                const heading = text(root.querySelector("h2"));
                const idElement = root.querySelector('[id^="plan"]');

                return {
                    plan_id: idElement ? idElement.id : null,
                    heading,
                    fields,
                    subscriptions,
                    notes,
                    reference_links: links,
                    raw_details: text(root)
                };
            }
        """)

        if not plan["plan_id"] or not plan["fields"]:
            raise RuntimeError(f"Incomplete detail extraction: {label}")

        price_match = re.search(r"₹\\s*([\\d,]+)", plan["heading"])
        if not price_match:
            # Normal Python regex for the displayed price.
            price_match = re.search(r"₹\s*([\d,]+)", plan["heading"])

        if not price_match:
            raise RuntimeError(f"Price missing: {label}")

        plan["price_inr"] = int(price_match.group(1).replace(",", ""))
        plan["category"] = category
        plan["source_url"] = page.url
        plan["last_checked"] = datetime.now(timezone.utc).isoformat()
        plan["tax_status"] = (
            "excluded"
            if "+GST" in plan["heading"].replace(" ", "").upper()
            else "check notes"
        )

        save_plan(plan)
        collected.add((category, plan["plan_id"]))

        print(
            f"Saved: {category} | Rs {plan['price_inr']} | "
            f"{len(plan['subscriptions'])} subscriptions"
        )

        page.get_by_role("button", name="Close", exact=True).click()
        table.wait_for(state="hidden")


prepare_database()
collected = set()
failures = []

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    page = browser.new_page()
    page.set_default_timeout(20000)

    try:
        for category in CATEGORIES:
            print(f"\\nCollecting {category}...")

            try:
                # Start from a clean list page for each category.
                page.goto(
                    URL,
                    wait_until="domcontentloaded",
                    timeout=60000,
                )

                tab = page.get_by_role(
                    "tab", name=category, exact=True
                )
                tab.wait_for(state="visible")
                tab.click()

                expect(tab).to_have_attribute("aria-selected", "true")
                page.get_by_role(
                    "heading", name=category, exact=True
                ).wait_for(state="visible")

                # Wait for either groups or direct plan cards.
                page.get_by_role(
                    "button", name=GROUP_PATTERN
                ).or_(
                    page.get_by_role("button", name=DETAIL_PATTERN)
                ).first.wait_for(state="visible")

                groups = page.get_by_role(
                    "button", name=GROUP_PATTERN
                )
                group_names = [
                    groups.nth(i).get_attribute("aria-label")
                    for i in range(groups.count())
                ]

                if not group_names:
                    collect_visible_plans(page, category, collected)
                    continue

                for name in group_names:
                    group = page.get_by_role(
                        "button", name=name, exact=True
                    )

                    # Jio marks an expanded group with "active".
                    classes = (group.get_attribute("class") or "").split()
                    if "active" not in classes:
                        group.click()
                        expect(group).to_have_class(
                            re.compile(r"\\bactive\\b".replace("\\\\", "\\"))
                        )

                    collect_visible_plans(page, category, collected)

            except Exception as error:
                failures.append(category)
                print(f"FAILED: {category}: {error}")

    finally:
        browser.close()

print(f"\\nSaved {len(collected)} unique category/plan records.")
print(f"Database: {DB_PATH}")

if failures:
    print("Incomplete categories:", ", ".join(failures))
    print("The run is partial; do not treat it as the complete catalog.")
else:
    print("Finished visiting all configured categories.")