import json
import math
import os
import re
from pathlib import Path

from cloud_browser import ManagedBrowserbase as Browserbase
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright


BASE_DIR = Path(__file__).resolve().parent

URL = "https://www.actcorp.in/broadband/chennai/broadband-plans"
CITY = "Chennai"

load_dotenv(BASE_DIR / ".env")


EXTRACT = r"""
() => {
    const clean = element =>
        element
            ? element.textContent.replace(/\s+/g, " ").trim()
            : "";

    const visible = element =>
        element && element.getClientRects().length > 0;

    return Array.from(
        document.querySelectorAll(
            ".select-plans-table input[plan-speed]"
        )
    ).map(input => {
        const cell = input.parentElement;
        const row = input.closest(".select-plans-table");

        if (!cell || !row) {
            return null;
        }

        const price = cell.querySelector(".desktop-price");

        // Ignore hidden mobile templates.
        if (!visible(price)) {
            return null;
        }

        const images = Array.from(
            row.querySelectorAll(".otts-wrap img[alt]")
        ).filter(visible);

        const labels = [...new Set(
            images
                .map(image => image.alt.trim())
                .filter(Boolean)
        )];

        return {
            plan_id: input.getAttribute("data-nid"),
            pack_name: clean(row.querySelector("h4")),
            description: clean(row.querySelector("h5.small")),
            price_text: clean(price),
            speed_mbps: Number(
                input.getAttribute("plan-speed")
            ),

            ott_labels_shown: labels.filter(
                label => label.toLowerCase() !== "more"
            ),

            more_badge_present: labels.some(
                label => label.toLowerCase() === "more"
            ),

            // An image labelled "More" does not establish
            // whether an expandable benefit list exists.
            additional_benefits_not_expanded: null,

            benefit_images: images.map(image => ({
                label: image.alt.trim(),
                source_url: image.src
            })),

            // A row contains several speed/price variants.
            // Retain it as shared row evidence only.
            row_text: row.innerText.trim()
        };
    }).filter(Boolean);
}
"""


def safe_error(error):
    """Redact credentials and browser connection URLs."""
    message = str(error)

    for name in (
        "BROWSERBASE_API_KEY",
        "API_KEY",
        "BROADBAND_API_KEY",
    ):
        secret = os.getenv(name, "").strip()

        if secret:
            message = message.replace(secret, "[REDACTED]")

    return re.sub(
        r"""wss?://[^\s"'<>]+""",
        "[browser connection URL hidden]",
        message,
    )


def validate_plans(plans):
    if not isinstance(plans, list) or not plans:
        raise ValueError("No ACT plans were extracted.")

    records = {}

    for plan in plans:
        plan_id = str(plan.get("plan_id") or "").strip()
        name = str(plan.get("pack_name") or "").strip()
        price_text = str(plan.get("price_text") or "")
        speed = plan.get("speed_mbps")

        if not plan_id or not name:
            raise ValueError("An ACT plan is missing its ID or name.")

        price_match = re.search(
            r"₹\s*([\d,]+(?:\.\d+)?)",
            price_text,
        )

        if not price_match:
            raise ValueError(
                f"Price missing for ACT plan ID {plan_id}."
            )

        price = float(
            price_match.group(1).replace(",", "")
        )

        if not math.isfinite(price) or price <= 0:
            raise ValueError(
                f"Invalid price for ACT plan ID {plan_id}."
            )

        if (
            isinstance(speed, bool)
            or not isinstance(speed, (int, float))
            or not math.isfinite(speed)
            or speed <= 0
        ):
            raise ValueError(
                f"Invalid speed for ACT plan ID {plan_id}."
            )

        previous = records.get(plan_id)

        if previous is not None and previous != plan:
            raise ValueError(
                f"Conflicting ACT records for plan ID {plan_id}."
            )

        records[plan_id] = plan

    return list(records.values())


def scrape():
    global LAST_COVERAGE
    LAST_COVERAGE = None
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

        session = client.sessions.create(
            project_id=project_id
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
                if not browser.contexts:
                    raise RuntimeError(
                        "Browserbase returned no browser context."
                    )

                context = browser.contexts[0]
                page = context.new_page()

                page.set_viewport_size({
                    "width": 1440,
                    "height": 1000,
                })
                page.set_default_timeout(20000)

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

                # The page replaces its initial plan grid.
                # Wait for repeated identical, nonempty snapshots.
                previous_signature = None
                stable_checks = 0
                plans = []

                for _ in range(30):
                    page.wait_for_timeout(1000)

                    plans = page.evaluate(EXTRACT)

                    signature = json.dumps(
                        plans,
                        sort_keys=True,
                        ensure_ascii=False,
                    )

                    if plans and signature == previous_signature:
                        stable_checks += 1
                    else:
                        stable_checks = 0

                    previous_signature = signature

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

                plans = validate_plans(plans)

                print(f"Read {len(plans)} unique plan entries.")
                print(
                    "Coverage: visible desktop plan grid and "
                    "benefit-image evidence."
                )
                print(
                    "Billing duration, taxes, FUP, and installation "
                    "charges are not verified by this extractor."
                )

                LAST_COVERAGE = {'complete': True, 'scope': 'Chennai', 'record_count': len(plans), 'method': 'stable complete desktop grid; Chennai selector verified', 'stable_checks': stable_checks}
                return plans, page.url

            finally:
                try:
                    browser.close()
                except Exception:
                    print(
                        "Browser disconnect could not be confirmed."
                    )

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


if __name__ == "__main__":
    # Direct execution creates an inspection export only.
    # refresh_plans.py handles database backups and replacement.
    try:
        extracted_plans, source_url = scrape()

        output_path = BASE_DIR / "act_extracted_details.json"

        output_path.write_text(
            json.dumps(
                {
                    "provider": "ACT Fibernet",
                    "city": CITY,
                    "source_url": source_url,
                    "count": len(extracted_plans),
                    "data": extracted_plans,
                },
                indent=2,
                ensure_ascii=False,
                allow_nan=False,
            ),
            encoding="utf-8",
        )

        print(f"Export saved: {output_path}")
        print("Database unchanged.")

    except Exception as error:
        print(f"FAILED: {safe_error(error)}")
        raise SystemExit(1)
