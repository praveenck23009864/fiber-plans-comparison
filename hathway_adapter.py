import hashlib
import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from cloud_browser import ManagedBrowserbase as Browserbase
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright
BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / 'jio_fiber.db'
URL = 'https://www.hathway.com/Broadband/HomeBroadband/Chennai'
CITY = 'Chennai'
PROVIDER = 'Hathway'
load_dotenv(BASE_DIR / '.env')
EXTRACT = '\n(cards) => {\n    const clean = (value) =>\n        (value || "").replace(/\\s+/g, " ").trim();\n\n    const text = (card, selector) =>\n        clean(card.querySelector(selector)?.textContent);\n\n    return cards.map((card) => ({\n        plan_name: text(card, ".head"),\n        speed_text: text(card, ".planSpeed h3"),\n        price_text: text(card, ".planPrice"),\n        price_basis: text(card, ".planSpeed span"),\n        duration_options_text: text(card, ".planMonthlyOpions"),\n        installation_terms: text(card, ".planFooter"),\n        benefits: Array.from(\n            card.querySelectorAll(".technology li p")\n        ).map((item) => clean(item.textContent)).filter(Boolean),\n        raw_text: clean(card.textContent)\n    }));\n}\n'

def safe_error(error):
    """Avoid printing API keys or browser connection URLs."""
    message = str(error)
    for name in ('BROWSERBASE_API_KEY', 'API_KEY'):
        secret = os.getenv(name, '').strip()
        if secret:
            message = message.replace(secret, '[REDACTED]')
    message = re.sub('wss?://[^\\s\\"\'<>]+', '[browser connection URL hidden]', message)
    return message

def scrape():
    global LAST_COVERAGE
    LAST_COVERAGE = None
    api_key = os.getenv('BROWSERBASE_API_KEY', '').strip()
    project_id = os.getenv('BROWSERBASE_PROJECT_ID', '').strip()
    if not api_key or not project_id:
        raise RuntimeError('Add BROWSERBASE_API_KEY and BROWSERBASE_PROJECT_ID to your .env file.')
    client = Browserbase(api_key=api_key)
    session = None
    try:
        print('Creating Browserbase session...')
        session = client.sessions.create(project_id=project_id)
        print(f'Watch session: https://www.browserbase.com/sessions/{session.id}')
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(session.connect_url, timeout=60000)
            try:
                context = browser.contexts[0]
                page = context.new_page()
                page.set_viewport_size({'width': 1440, 'height': 1000})
                print('Opening Hathway Chennai plans...')
                response = page.goto(URL, wait_until='domcontentloaded', timeout=60000)
                if response is not None and response.status >= 400:
                    raise RuntimeError(f'Hathway returned HTTP {response.status}.')
                page.get_by_role('heading', name=re.compile('Explore Plans in Chennai', re.IGNORECASE)).wait_for(state='visible', timeout=30000)
                cards = page.locator('.bussinesPlan.homeuser')
                cards.first.wait_for(state='attached', timeout=30000)
                page.wait_for_function('() => {\n                        const cards = Array.from(\n                            document.querySelectorAll(\n                                ".bussinesPlan.homeuser"\n                            )\n                        );\n\n                        return cards.length > 0 && cards.every(card => {\n                            const text = selector =>\n                                card.querySelector(selector)\n                                    ?.textContent?.trim() || "";\n\n                            return text(".head")\n                                && /₹\\s*[\\d,]+/.test(text(".planPrice"))\n                                && /[\\d.]+\\s*(Mbps|Gbps)/i.test(\n                                    text(".planSpeed h3")\n                                );\n                        });\n                    }', timeout=30000)
                plans = cards.evaluate_all(EXTRACT)
                # Ensure lazy-loaded cards are observed and the scoped grid stabilizes.
                page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
                stable = 0
                for _ in range(20):
                    page.wait_for_timeout(1000)
                    snapshot = cards.evaluate_all(EXTRACT)
                    stable = stable + 1 if snapshot and snapshot == plans else 0
                    plans = snapshot
                    if stable >= 3:
                        break
                else:
                    raise RuntimeError('Hathway plan grid did not stabilize; saved plans retained')
                if page.get_by_role('button', name=re.compile(r'load more|show more plans', re.I)).count():
                    raise RuntimeError('Unexpanded Hathway pagination; completeness not verified')
                if not plans:
                    raise RuntimeError('No Hathway plan cards found. The website layout may have changed.')
                body_text = page.locator('body').inner_text()
                body_text = re.sub('\\s+', ' ', body_text)
                tax_match = re.search('Installation charges and taxes extra as applicable\\.?', body_text, re.IGNORECASE)
                tax_note = tax_match.group(0) if tax_match else None
                unique = {json.dumps(p, sort_keys=True) for p in plans}
                LAST_COVERAGE = {'complete': True, 'scope': 'Chennai', 'record_count': len(unique), 'method': 'Chennai heading verified; complete stable rendered grid', 'stable_checks': stable}
                return (plans, page.url, tax_note)
            finally:
                try:
                    browser.close()
                except Exception:
                    print('Browser disconnect could not be confirmed.')
    finally:
        if session is not None:
            try:
                client.sessions.update(session.id, project_id=project_id, status='REQUEST_RELEASE')
                print('Browserbase session released.')
            except Exception:
                print(f'Could not confirm session release. Check the session in your Browserbase dashboard: {session.id}')
