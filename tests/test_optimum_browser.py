"""Local Chrome fixtures: no Optimum or Browserbase requests are made."""
import asyncio
import json
import unittest
from playwright.async_api import async_playwright, expect
from optimum_adapter import extract, choices_from, navigate
from optimum_models import Address, LookupFailure

ADDRESS = dict(street='961 E 174TH ST', city='BRONX', state='NY', zip_code='10460', unit=None)
KEY = '11111111-1111-4111-8111-111111111111'
PLAN = dict(provider='Optimum', plan_name='500 Mbps Internet', speed_mbps=500, monthly_price_cents=2500, currency='USD', promotional_period='1-year price protection', conditions='w/ Auto Pay and bank account', supporting_card_text='500 Mbps Internet\n$25.00/mo.\nw/ Auto Pay and bank account\n1-year price protection')

class BrowserTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.pw = await async_playwright().start()
        self.browser = await self.pw.chromium.launch(channel='chrome', headless=True)
        self.page = await self.browser.new_page()

    async def asyncTearDown(self):
        await self.browser.close(); await self.pw.stop()

    async def test_real_dom_card_extraction(self):
        await self.page.set_content('''<main><article><h2>500 Mbps Internet</h2><p>$25.00/mo.</p><p>w/ Auto Pay and bank account</p><p>1-year price protection</p></article><article><h2>1 Gig Internet</h2><p>$45.00/mo.</p><p>w/ Auto Pay</p></article><section><h2>TV</h2>$30.00/mo.</section></main>''')
        records = await extract(self.page)
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]['monthly_price_cents'], 2500)
        self.assertEqual(records[1]['speed_mbps'], 1000)
        self.assertNotIn('TV', str(records))

    async def test_actual_dom_address_and_unit_choices(self):
        await self.page.set_content('''<label for="a">961 E 174TH ST, Apt 2A, BRONX, NY 10460</label><input type="radio" id="a"><label for="b">961 E 174TH ST, Apt 3B, BRONX, NY 10460</label><input type="radio" id="b">''')
        choices = await choices_from(self.page.get_by_role('radio'))
        self.assertEqual([c[2] for c in choices], ['a', 'b'])
        self.assertIn('Apt 3B', choices[1][1])

    async def test_navigation_full_address_acceptance_and_ambiguity(self):
        for with_unit in [False, True]:
            label = '961 E 174TH ST' + (', Unit 2A' if with_unit else '') + ', BRONX, NY 10460'
            html = '''<input role="combobox"><div role="option" onclick="this.remove()">961 E 174TH ST, BRONX, NY 10460</div>
                <button onclick="document.querySelector('#matches').hidden=false">Check availability</button>
                <div id="matches" hidden>''' + ('Select your unit' if with_unit else 'Confirm address') + f'''
                <label for="a">{label}</label><input id="a" type="radio" name="location">
                <label for="b">{label}</label><input id="b" type="radio" name="location">
                <button onclick="location.href='/buy-flow/list-quotes'">Continue</button></div>'''
            async def route_handler(route):
                body = f'<p>{label}</p><article><h2>500 Mbps Internet</h2><p>$25/mo.</p></article>' if '/list-quotes' in route.request.url else html
                await route.fulfill(content_type='text/html', body=body)
            await self.page.route('https://www.optimum.com/**', route_handler)
            chosen = []
            async def ask(kind, choices):
                chosen.append((kind, choices)); return 1
            verified, location_id = await navigate(self.page, Address(**ADDRESS), ask)
            self.assertEqual(location_id, 'b')
            self.assertEqual(len(chosen), 1)
            self.assertEqual(verified.unit, '2A' if with_unit else None)
            self.assertEqual(len(await extract(self.page)), 1)
            await self.page.unroute('https://www.optimum.com/**', route_handler)

    async def fixture(self, status='awaiting_selection'):
        self.calls = []
        self.state = dict(lookup_id=KEY, status=status, choices=[dict(choice_id='a', label='961 E 174TH ST, BRONX, NY 10460', service_location_id='location-a'), dict(choice_id='b', label='961 E 174TH ST, BRONX, NY 10460', service_location_id='location-b')], selection_kind='service_location', plans=[], cached=False)
        async def handler(route):
            request = route.request; self.calls.append((request.method, request.url))
            if request.method == 'POST' and request.url.endswith('/selection'):
                self.assertEqual(request.post_data_json, {'choice_id': 'b'})
                self.state.update(status='succeeded', choices=[], verified_address=ADDRESS, service_location_id='location-b', plans=[PLAN], captured_at='2026-10-26T00:00:00Z', source_url='https://www.optimum.com/check-availability')
            if request.method == 'DELETE':
                await route.fulfill(json={**self.state, 'status': 'cancelled', 'plans': []}); return
            await route.fulfill(json=self.state)
        await self.page.route('**/api/optimum/**', handler)
        await self.page.goto('http://127.0.0.1:5173')
        await self.page.get_by_label('Provider', exact=True).select_option('Optimum')

    async def fill(self):
        await self.page.get_by_label('Full service address', exact=True).fill('961 E 174TH ST, BRONX, NY 10460')

    async def test_validation_loading_explicit_selection_results_and_clear(self):
        await self.fixture()
        await expect(self.page.get_by_role('button', name='Check availability', exact=True)).to_be_disabled()
        self.assertEqual(self.calls, [])
        await self.fill()
        await self.page.get_by_role('button', name='Check availability', exact=True).click()
        await expect(self.page.get_by_role('button', name='Checking…', exact=True)).to_be_disabled()
        await expect(self.page.get_by_role('button', name='Continue with selected address')).to_be_disabled()
        self.assertEqual(await self.page.locator('input[name="optimum-choice"]:checked').count(), 0)
        await self.page.locator('input[value="b"]').check()
        await self.page.get_by_role('button', name='Continue with selected address').click()
        await expect(self.page.get_by_role('heading', name='Verified service address')).to_be_visible()
        await expect(self.page.get_by_text('25.00 USD / month')).to_be_visible()
        await expect(self.page.get_by_text('Pricing conditions: w/ Auto Pay and bank account')).to_be_visible()
        self.assertEqual(sum(m == 'POST' and not u.endswith('/selection') for m, u in self.calls), 1)
        await expect(self.page.get_by_role('button', name='Check availability', exact=True)).to_be_enabled(timeout=5000)
        self.state.update(status='running', plans=[], choices=[])
        await self.page.get_by_role('button', name='Check availability', exact=True).click()
        await expect(self.page.get_by_text('25.00 USD / month')).to_have_count(0)
        await self.page.get_by_role('button', name='Cancel search').click()
        await expect(self.page.get_by_role('button', name='Check availability', exact=True)).to_be_enabled()

    async def test_all_error_states(self):
        await self.fixture('running'); await self.fill()
        for code in ['service_unavailable', 'address_not_found', 'unit_required', 'access_denied', 'quota_exceeded', 'timeout', 'lookup_failed']:
            self.state.update(status='failed', choices=[], plans=[], code=code, message='Fixture ' + code)
            await self.page.get_by_role('button', name='Check availability', exact=True).click()
            await expect(self.page.get_by_role('alert').filter(has_text='Fixture ' + code)).to_be_visible()
            await expect(self.page.get_by_role('button', name='Check availability', exact=True)).to_be_enabled(timeout=5000)
        self.assertEqual(await self.page.get_by_role('heading', name='Verified service address').count(), 0)

    async def test_full_address_validation_and_responsive_layout(self):
        await self.fixture(); await self.page.set_viewport_size({'width': 1600, 'height': 1000})
        field = self.page.get_by_label('Full service address', exact=True)
        apt = self.page.get_by_label('Apartment or unit (optional)', exact=True)
        button = self.page.get_by_role('button', name='Check availability', exact=True)
        await expect(field).to_have_attribute('placeholder', '123 Main St, Anytown, USA 12345')
        await expect(apt).to_have_attribute('placeholder', 'Apt')
        await field.fill('123 Main St, Anytown, USA 12345'); await button.click()
        await expect(self.page.get_by_role('alert').filter(has_text='two-letter state')).to_be_visible()
        self.assertEqual(self.calls, [])
        boxes = [await el.bounding_box() for el in [field, apt, button]]
        self.assertTrue(all(abs(b['height'] - 64) < 1 for b in boxes))
        self.assertTrue(all(abs(b['y'] - boxes[0]['y']) < 1 for b in boxes))
        self.assertAlmostEqual(boxes[1]['x'] - boxes[0]['x'] - boxes[0]['width'], 16, delta=1)
        await self.page.set_viewport_size({'width': 390, 'height': 844})
        boxes = [await el.bounding_box() for el in [field, apt, button]]
        self.assertTrue(boxes[0]['y'] < boxes[1]['y'] < boxes[2]['y'])
        self.assertLessEqual(await self.page.evaluate('document.documentElement.scrollWidth'), 390)

    async def test_late_response_cannot_replace_new_search(self):
        await self.fixture('running'); await self.fill()
        received=asyncio.Event()
        async def delayed(route):
            received.set()
            await asyncio.sleep(3)
            await route.fulfill(json=dict(lookup_id=KEY,status='succeeded',choices=[],verified_address=ADDRESS,plans=[{**PLAN,'plan_name':'OLD RESPONSE'}],captured_at='2020-01-01T00:00:00Z'))
        await self.page.route('**/api/optimum/lookups/'+KEY,delayed)
        await self.page.get_by_role('button',name='Check availability',exact=True).click()
        await asyncio.wait_for(received.wait(),6)
        await self.page.get_by_role('button',name='Cancel search').click()
        self.state.update(lookup_id='22222222-2222-4222-8222-222222222222',status='failed',choices=[],plans=[],code='address_not_found',message='New search could not find address')
        await self.page.get_by_role('button',name='Check availability',exact=True).click()
        await expect(self.page.get_by_role('alert').filter(has_text='New search could not find address')).to_be_visible()
        await self.page.wait_for_timeout(3500)
        self.assertEqual(await self.page.get_by_text('OLD RESPONSE').count(),0)
        await expect(self.page.get_by_role('alert').filter(has_text='New search could not find address')).to_be_visible()

    async def test_existing_providers_and_comparison(self):
        await self.page.goto('http://127.0.0.1:5173')
        for provider in ['JioFiber', 'ACT Fibernet', 'Hathway']:
            await self.page.get_by_label('Provider', exact=True).select_option(provider)
            await expect(self.page.locator('.bc-grid').get_by_role('article').first).to_be_visible(timeout=20000)
        # Failed live-refresh warnings can coexist with a healthy saved catalog.
        self.assertEqual(await self.page.locator('main > .bc-error').count(), 0)

if __name__ == '__main__': unittest.main()
