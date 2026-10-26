"""Manual, single live lookup authorized for the Bronx address; never picks a choice.
Run only intentionally: this consumes one Browserbase session.
"""
import json
import sqlite3
import sys
import time
from contextlib import closing
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]

def main():
    sys.stdout.reconfigure(encoding='utf-8')
    output = ROOT / '.runtime'; output.mkdir(exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel='chrome', headless=True)
        page = browser.new_page(viewport={'width': 1600, 'height': 1000})
        page.goto('http://127.0.0.1:5173')
        page.get_by_label('Provider', exact=True).select_option('Optimum')
        page.get_by_label('Full service address', exact=True).fill('961 E 174TH ST, BRONX, NY 10460')
        with page.expect_response(lambda r: r.request.method == 'POST' and r.url.endswith('/api/optimum/lookups')) as response:
            page.get_by_role('button', name='Check availability', exact=True).click()
        initial = response.value.json()
        if response.value.status != 202:
            print(json.dumps({'http_status': response.value.status, 'response': initial})); browser.close(); return
        lookup_id = initial['lookup_id']
        print('Live lookup: ' + lookup_id, flush=True)
        last = None
        try:
            deadline = time.monotonic() + 240
            while time.monotonic() < deadline:
                state = page.request.get('http://127.0.0.1:5173/api/optimum/lookups/' + lookup_id).json()
                if state['status'] != last:
                    print('Status: ' + state['status'], flush=True); last = state['status']
                if state['status'] in ('succeeded', 'failed', 'awaiting_selection', 'expired', 'cancelled'):
                    break
                page.wait_for_timeout(1000)
            if state['status'] == 'awaiting_selection':
                expect(page.get_by_role('region', name='Address choices')).to_be_visible(timeout=10000)
                state['frontend_choices_verified'] = page.locator('input[name="optimum-choice"]:checked').count() == 0
            elif state['status'] == 'succeeded':
                expect(page.get_by_role('heading', name='Verified service address')).to_be_visible(timeout=10000)
                with closing(sqlite3.connect(ROOT / 'jio_fiber.db')) as db:
                    row = db.execute('SELECT verified_address_json, service_location_id FROM optimum_lookups WHERE lookup_id=?', (lookup_id,)).fetchone()
                    count = db.execute('SELECT COUNT(*) FROM optimum_address_plans WHERE lookup_id=?', (lookup_id,)).fetchone()[0]
                state['storage_verified'] = bool(row and json.loads(row[0]) == state['verified_address'] and count == len(state['plans']))
                state['frontend_plans_verified'] = page.locator('.optimum-plan').count() == len(state['plans'])
            elif state['status'] in ('failed', 'expired'):
                expect(page.get_by_role('alert').filter(has_text=state['message'])).to_be_visible(timeout=10000)
                state['frontend_error_verified'] = True
            page.screenshot(path=str(output / 'optimum-live.png'), full_page=True)
            (output / 'optimum-live.json').write_text(json.dumps(state, indent=2), encoding='utf-8')
            print(json.dumps(state, indent=2), flush=True)
        finally:
            # Pending selections are reported, never guessed; release immediately.
            page.request.delete('http://127.0.0.1:5173/api/optimum/lookups/' + lookup_id)
            browser.close()

if __name__ == '__main__': main()
