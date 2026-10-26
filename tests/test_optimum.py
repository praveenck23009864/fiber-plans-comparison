import asyncio
import copy
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import httpx
from fastapi import FastAPI, Security, HTTPException
from optimum_models import Address, FullAddressInput, LookupFailure, parse_address, same_address, verified_address, parse_card, page_failure
from optimum_store import migrate, save, read
from optimum_jobs import Jobs, install
from optimum_adapter import choose, scrape

ADDRESS = Address(street='961 E 174TH ST', city='BRONX', state='NY', zip_code='10460')
CARD = '500 Mbps Internet\n$25.00/mo.\nw/ Auto Pay and bank account\n1-year price protection'

def result():
    return dict(verified_address=ADDRESS.model_dump(), service_location_id='fixture-location',
                plans=[parse_card('500 Mbps Internet', CARD)], captured_at='2026-10-26T00:00:00+00:00',
                source_url='https://www.optimum.com/check-availability')

class Models(unittest.TestCase):
    def test_full_address_input_no_guessing(self):
        form = FullAddressInput(full_address='961 East 174th Street, The Bronx, NY 10460', unit='2A')
        self.assertEqual(form.to_address().unit, '2A')
        self.assertEqual(form.to_address().zip_code, '10460')
        for value in ['123 Main St, Anytown, USA 12345', '961 E 174TH ST, BRONX', '961 E 174TH ST, Unit 2A, BRONX, NY 10460']:
            with self.assertRaises(ValueError): FullAddressInput(full_address=value)

    def test_formats_and_identity(self):
        alternate = parse_address('961 East 174th Street, The Bronx, NY 10460, USA')
        self.assertTrue(same_address(ADDRESS, alternate))
        for field, value in [('street', '962 E 174TH ST'), ('city', 'Queens'), ('state', 'NJ'), ('zip_code', '10461'), ('unit', '2A')]:
            self.assertFalse(same_address(ADDRESS, ADDRESS.model_copy(update={field: value})))

    def test_full_page_address_required(self):
        self.assertEqual(verified_address('Header\n961 E 174TH ST, THE BRONX, NY 10460\nPlans', ADDRESS).zip_code, '10460')
        for text in ['961 E 174TH ST\nSomething else 10460', '962 E 174TH ST, BRONX, NY 10460']:
            with self.assertRaises(LookupFailure): verified_address(text, ADDRESS)
        with self.assertRaises(LookupFailure):
            verified_address('961 E 174TH ST, BRONX, NY 10460', ADDRESS.model_copy(update={'unit': '2A'}))

    def test_unit_and_zip(self):
        a = parse_address('961 E 174TH ST, Apt 2A, BRONX, NY 10460')
        self.assertEqual(a.unit, '2A')
        with self.assertRaises(ValueError): Address(street='961 E 174TH ST', city='Bronx', state='XX', zip_code='10460')
        with self.assertRaises(ValueError): Address(street='961 E 174TH ST Apt 2A', city='Bronx', state='NY', zip_code='10460')

    def test_card(self):
        p = parse_card('500 Mbps Internet', CARD)
        self.assertEqual(p['monthly_price_cents'], 2500)
        self.assertEqual(p['currency'], 'USD')
        self.assertEqual(p['promotional_period'], '1-year price protection')
        self.assertIn('Auto Pay', p['conditions'])
        self.assertEqual(parse_card('1 Gig Internet', '$45.00/mo.')['speed_mbps'], 1000)
        self.assertIsNone(parse_card('300 Mbps Internet', 'Details unavailable')['monthly_price_cents'])
        self.assertIsNone(parse_card('300 Mbps Internet', '$35/mo.')['conditions'])
        with self.assertRaises(LookupFailure): parse_card('500 Mbps Internet', '$25/mo. $30/mo.')

    def test_failures_distinct(self):
        for text, code in [('Service is not available at this address', 'service_unavailable'), ('Unable to find your address', 'address_not_found'), ('Access denied', 'access_denied'), ('Quota exceeded', 'quota_exceeded')]:
            with self.assertRaises(LookupFailure) as ctx: page_failure(text)
            self.assertEqual(ctx.exception.code, code)
        page_failure('See if Optimum is available for your home')

class Storage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'test.db'
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('CREATE TABLE broadband_plans (provider TEXT)')
            db.execute("INSERT INTO broadband_plans VALUES ('JioFiber')")
        migrate(self.path)

    def tearDown(self): self.tmp.cleanup()

    def test_additive_idempotent_migration_and_capture(self):
        migrate(self.path)
        save(self.path, 'one', ADDRESS, result())
        saved = read(self.path, 'one')
        self.assertTrue(saved['cached'])
        self.assertEqual(saved['verified_address'], ADDRESS.model_dump())
        self.assertEqual(saved['captured_at'], result()['captured_at'])
        self.assertEqual(saved['plans'][0]['monthly_price_cents'], 2500)
        with closing(sqlite3.connect(self.path)) as db, db:
            self.assertEqual(db.execute('SELECT * FROM broadband_plans').fetchall(), [('JioFiber',)])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM app_migrations').fetchone()[0], 1)
        self.assertIsNone(read(self.path, 'other'))

    def test_wrong_address_rejected_and_transaction_rollback(self):
        bad = result(); bad['verified_address']['zip_code'] = '10461'
        with self.assertRaises(LookupFailure): save(self.path, 'bad', ADDRESS, bad)
        self.assertIsNone(read(self.path, 'bad'))
        bad = result(); bad['plans'] *= 2
        with self.assertRaises(sqlite3.IntegrityError): save(self.path, 'duplicate', ADDRESS, bad)
        self.assertIsNone(read(self.path, 'duplicate'))

class AsyncTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'test.db'; migrate(self.path)

    async def asyncTearDown(self): self.tmp.cleanup()

    async def test_multiple_matches_never_first(self):
        ask = AsyncMock(return_value=1)
        items = [('first', ADDRESS.label(), 'id-1'), ('second', ADDRESS.label(), 'id-2')]
        chosen, _, sid = await choose(items, ADDRESS, ask, 'service_location')
        self.assertEqual((chosen, sid), ('second', 'id-2'))
        ask.assert_awaited_once()

    async def test_single_exact_match_and_explicit_unit(self):
        ask = AsyncMock(return_value=0)
        await choose([('one', ADDRESS.label(), 'id')], ADDRESS, ask, 'address')
        ask.assert_not_awaited()
        _, selected, _ = await choose([('unit', '961 E 174TH ST, Unit 2A, BRONX, NY 10460', 'id')], ADDRESS, ask, 'unit')
        self.assertEqual(selected.unit, '2A'); ask.assert_awaited_once()

    async def test_selection_and_storage(self):
        async def runner(address, ask):
            chosen = await ask('unit', [{'label': 'Unit 2A'}, {'label': 'Unit 3B'}])
            self.assertEqual(chosen, 1)
            r = result(); r['verified_address']['unit'] = '3B'; return r
        jobs = Jobs(self.path, runner)
        job = jobs.start(ADDRESS); key = job['lookup_id']
        for _ in range(5): await asyncio.sleep(0)
        state = jobs.get(key); self.assertEqual(state['status'], 'awaiting_selection')
        with self.assertRaises(HTTPException): jobs.select(key, 'forged')
        jobs.select(key, state['choices'][1]['choice_id'])
        with self.assertRaises(HTTPException): jobs.select(key, state['choices'][1]['choice_id'])
        await jobs.jobs[key]['_task']
        self.assertEqual(jobs.get(key)['status'], 'succeeded')
        self.assertEqual(read(self.path, key)['verified_address']['unit'], '3B')

    async def test_expiration_cancellation_timeout_cleanup(self):
        released = []
        async def waiting(address, ask):
            try: await ask('address', [{'label': 'Choice'}])
            finally: released.append(True)
        jobs = Jobs(self.path, waiting, selection_timeout=.01)
        job = jobs.start(ADDRESS); await jobs.jobs[job['lookup_id']]['_task']
        self.assertEqual(jobs.get(job['lookup_id'])['code'], 'selection_expired')
        self.assertEqual(released, [True])
        jobs = Jobs(self.path, waiting)
        job = jobs.start(ADDRESS)
        for _ in range(4): await asyncio.sleep(0)
        await jobs.cancel(job['lookup_id'])
        self.assertEqual(jobs.get(job['lookup_id'])['status'], 'cancelled')
        self.assertEqual(len(released), 2)
        jobs = Jobs(self.path, waiting, job_timeout=.01)
        job = jobs.start(ADDRESS); await jobs.jobs[job['lookup_id']]['_task']
        self.assertEqual(jobs.get(job['lookup_id'])['code'], 'timeout')

    async def test_technical_failures_no_cached_fallback(self):
        save(self.path, 'old', ADDRESS, result())
        for code in ['service_unavailable', 'unit_required', 'address_not_found', 'access_denied', 'quota_exceeded', 'timeout', 'lookup_failed']:
            async def runner(address, ask): raise LookupFailure(code, code)
            jobs = Jobs(self.path, runner); job = jobs.start(ADDRESS)
            await jobs.jobs[job['lookup_id']]['_task']
            state = jobs.get(job['lookup_id'])
            self.assertEqual(state['code'], code); self.assertEqual(state['plans'], [])
            self.assertIsNone(read(self.path, job['lookup_id']))
        self.assertTrue(read(self.path, 'old')['cached'])

    async def test_browser_release_on_connect_failure(self):
        client = MagicMock(); client.sessions.create.return_value = MagicMock(id='session', connect_url='wss://fixture')
        playwright = MagicMock(); playwright.chromium.connect_over_cdp = AsyncMock(side_effect=RuntimeError('private details'))
        manager = MagicMock(); manager.__aenter__ = AsyncMock(return_value=playwright); manager.__aexit__ = AsyncMock(return_value=False)
        with patch.dict('os.environ', {'BROWSERBASE_API_KEY': 'fixture', 'BROWSERBASE_PROJECT_ID': 'fixture'}), patch('optimum_adapter.Browserbase', return_value=client), patch('optimum_adapter.async_playwright', return_value=manager):
            with self.assertRaises(LookupFailure) as ctx: await scrape(ADDRESS, AsyncMock())
        self.assertEqual(ctx.exception.code, 'lookup_failed')
        client.sessions.update.assert_called_once_with('session', project_id='fixture', status='REQUEST_RELEASE')
        self.assertEqual(client.sessions.create.call_args.kwargs['api_timeout'], 600)

    async def test_api_validation_auth_and_duplicates(self):
        async def guard(): raise HTTPException(401)
        app = FastAPI(); install(app, self.path, [Security(guard)])
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
            self.assertEqual((await client.post('/api/optimum/lookups', json=ADDRESS.model_dump())).status_code, 401)
            app.dependency_overrides[guard] = lambda: None
            self.assertEqual((await client.post('/api/optimum/lookups', json={'street': 'bad'})).status_code, 422)
            async def waiting(address, ask): await asyncio.sleep(20)
            app.state.optimum_jobs.runner = waiting
            response = await client.post('/api/optimum/lookups', json={'full_address': ADDRESS.label(), 'unit': None})
            self.assertEqual(response.status_code, 202)
            self.assertEqual((await client.post('/api/optimum/lookups', json=ADDRESS.model_dump())).status_code, 409)
            key = response.json()['lookup_id']
            self.assertEqual((await client.get('/api/optimum/lookups/' + key)).status_code, 200)
            self.assertEqual((await client.delete('/api/optimum/lookups/' + key)).status_code, 200)
        await app.state.optimum_jobs.close()

if __name__ == '__main__': unittest.main()
