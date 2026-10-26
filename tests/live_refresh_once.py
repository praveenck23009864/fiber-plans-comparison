"""Manual: one real refresh per provider via the frontend's Refresh all button."""
import json
import sqlite3
import sys
import time
from contextlib import closing
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

ROOT=Path(__file__).resolve().parents[1]
def counts():
    with closing(sqlite3.connect(ROOT/'jio_fiber.db')) as db:
        return {name:dict(total=total,active=active) for name,total,active in db.execute('SELECT provider,COUNT(*),SUM(is_active) FROM broadband_plans GROUP BY provider')}

def main():
    sys.stdout.reconfigure(encoding='utf-8')
    before=counts(); print('Before: '+json.dumps(before),flush=True)
    output=ROOT/'.runtime'; output.mkdir(exist_ok=True)
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel='chrome',headless=True)
        page=browser.new_page(viewport={'width':1600,'height':1100})
        page.goto('http://127.0.0.1:5173')
        if '--observe' not in sys.argv:
            button=page.get_by_role('button',name='Refresh all providers',exact=True)
            expect(button).to_be_enabled(timeout=20000)
            button.click()
            # Read durable status rather than relying on a browser response body
            # that a development-server reload can discard. Never resubmit.
            page.wait_for_timeout(2000)
        previous=None
        deadline=time.monotonic()+3000
        while time.monotonic()<deadline:
            payload=page.request.get('http://127.0.0.1:5173/api/refresh').json()
            states={p['provider']:p['job']['status'] if p['job'] else None for p in payload['providers']}
            if states!=previous: print(json.dumps(states),flush=True); previous=states
            if all(value in ('completed','failed') for value in states.values()): break
            page.wait_for_timeout(1500)
        for p in payload['providers']:
            region=page.get_by_role('region',name=p['name']+' refresh',exact=True)
            expect(region.get_by_role('status')).to_have_text(p['job']['status'].capitalize(),timeout=10000)
            if p['job']['status']=='failed': expect(region.get_by_role('alert')).to_contain_text('Saved data may be stale')
            with closing(sqlite3.connect(ROOT/'jio_fiber.db')) as db:
                p['saved_audit_verified']=db.execute('SELECT status FROM catalog_refresh_jobs WHERE job_id=?',(p['job']['job_id'],)).fetchone()[0]==p['job']['status']
                if p['job']['status']=='completed':
                    p['records_verified']=db.execute('SELECT COUNT(*) FROM broadband_plans WHERE provider=? AND is_active=1 AND last_seen_at=?',(p['name'],p['last_success_at'])).fetchone()[0]==sum(p['job'][k] for k in ('added','updated','unchanged'))
        report=dict(before=before,after=counts(),result=payload,frontend_statuses_verified=True)
        (output/'catalog-live-refresh.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        page.screenshot(path=str(output/'catalog-live-refresh.png'),full_page=True)
        print(json.dumps(report,indent=2),flush=True)
        browser.close()

if __name__=='__main__': main()
