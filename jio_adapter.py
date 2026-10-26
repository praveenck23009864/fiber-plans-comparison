import re
from contextlib import contextmanager
import os
from pathlib import Path
from cloud_browser import ManagedBrowserbase as Browserbase
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, expect

URL = 'https://www.jio.com/selfcare/plans/fiber/fiber-prepaid-plans-list/'
CATEGORIES = ['Monthly', 'Semi-Annual', 'Annual', 'ISD', 'Top-up', 'Data Sachet']
GROUP = re.compile(r'\(\d+\s+plans?\)$')
DETAIL = re.compile(r'^More info .* plan$')

@contextmanager
def cloud_page():
    load_dotenv(Path(__file__).resolve().parent / '.env')
    key = os.getenv('BROWSERBASE_API_KEY', '').strip()
    project = os.getenv('BROWSERBASE_PROJECT_ID', '').strip()
    if not key or not project:
        raise RuntimeError('Browserbase API key/project ID missing in .env')
    client = Browserbase(api_key=key)
    session = client.sessions.create(project_id=project)
    print(f'Watch: https://www.browserbase.com/sessions/{session.id}')
    try:
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(session.connect_url, timeout=60000)
            try:
                page = browser.contexts[0].new_page()
                page.set_viewport_size({'width': 1440, 'height': 1000})
                page.set_default_timeout(20000)
                yield page
            finally:
                try:
                    browser.close()
                except Exception:
                    pass
    finally:
        try:
            client.sessions.update(session.id, project_id=project, status='REQUEST_RELEASE')
        except Exception:
            print(f'Check session release in dashboard: {session.id}')

def collect(page, category, records, expected_count=None):
    buttons = page.get_by_role('button', name=DETAIL)
    visible = [i for i in range(buttons.count()) if buttons.nth(i).is_visible()]
    if not visible:
        raise RuntimeError(f'No detail cards in {category}')
    if expected_count is not None and len(visible) != expected_count:
        raise RuntimeError(f'Incomplete Jio group in {category}: expected {expected_count}, found {len(visible)}')
    for i in visible:
        buttons.nth(i).click()
        table = page.get_by_role('table', name='Plan details')
        table.wait_for(state='visible')
        popup = page.locator('.j-modal-content:visible')
        raw = popup.evaluate('''root => {
            const text = el => el ? el.innerText.trim() : '';
            const fields = {};
            for (const row of root.querySelectorAll('[role="table"][aria-label="Plan details"] [role="row"]')) {
                const key = row.querySelector('[role="rowheader"]');
                const val = row.querySelector('[role="cell"]');
                if (key && val) fields[text(key)] = text(val);
            }
            return {
                plan_id: root.querySelector('[id^="plan"]')?.id,
                heading: text(root.querySelector('h2')),
                fields,
                subscriptions: Array.from(root.querySelectorAll('ul[aria-label="Subscriptions"] li')).map(text),
                notes: Array.from(root.querySelectorAll('ul:not([aria-label="Subscriptions"]) li')).map(text),
                raw_details: text(root)
            };
        }''')
        if not raw.get('plan_id') or not raw['fields']:
            raise RuntimeError(f'Incomplete Jio detail in {category}')
        raw.update(category=category, source_url=page.url)
        identity = (category, raw['plan_id'])
        if identity in records and records[identity] != raw:
            raise RuntimeError(f'Conflicting Jio plan: {identity}')
        records[identity] = raw
        page.get_by_role('button', name='Close', exact=True).click()
        table.wait_for(state='hidden')
        page.wait_for_timeout(300)

def scrape():
    global LAST_COVERAGE
    LAST_COVERAGE = None
    records = {}
    # Separate session per category avoids consuming one long cloud session.
    # No database writes until all six categories complete successfully.
    for category in CATEGORIES:
        print(f'Collecting Jio: {category}')
        with cloud_page() as page:
            response = page.goto(URL, wait_until='domcontentloaded', timeout=60000)
            if response and response.status >= 400:
                raise RuntimeError(f'Jio HTTP {response.status}')
            tab = page.get_by_role('tab', name=category, exact=True)
            tab.click()
            expect(tab).to_have_attribute('aria-selected', 'true')
            page.get_by_role('heading', name=category, exact=True).wait_for(state='visible')
            page.get_by_role('button', name=GROUP).or_(page.get_by_role('button', name=DETAIL)).first.wait_for(state='visible')
            groups = page.get_by_role('button', name=GROUP)
            names = [groups.nth(i).get_attribute('aria-label') or groups.nth(i).inner_text() for i in range(groups.count())]
            if not names:
                collect(page, category, records)
            for name in names:
                group = page.get_by_role('button', name=name, exact=True)
                if 'active' not in (group.get_attribute('class') or '').split():
                    group.click()
                    expect(group).to_have_class(re.compile(r'\bactive\b'))
                expected = int(re.search(r'\((\d+)\s+plans?\)', name)[1])
                collect(page, category, records, expected)
    LAST_COVERAGE = {'complete': True, 'scope': 'general', 'record_count': len(records), 'categories': CATEGORIES, 'method': 'all six categories and each displayed group; group counts checked'}
    return list(records.values())
