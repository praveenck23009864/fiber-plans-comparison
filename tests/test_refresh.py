import asyncio
import copy
import os
import sqlite3
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from fastapi import HTTPException
from refresh_plans import normalize, PROVIDERS
from refresh_store import migrate, identity, apply_batch, SCOPES
from refresh_service import RefreshService

def batch(provider='act', count=4):
    if provider == 'act':
        raw = [dict(plan_id=str(i), pack_name='Plan ' + str(i), price_text='₹ 500', speed_mbps=100, ott_labels_shown=['TV']) for i in range(count)]
        return normalize(provider, raw, 'https://www.actcorp.in/broadband/chennai/broadband-plans')
    if provider == 'hathway':
        raw = [dict(plan_name='Plan ' + str(i), price_text='₹ 500', speed_text='100 Mbps', duration_options_text='3, 6 and 12 months', price_basis='Effective Monthly Pricing', benefits=['Router']) for i in range(count)]
        return normalize(provider, raw, 'https://www.hathway.com/Broadband/HomeBroadband/Chennai')
    raw = [dict(plan_id='plan'+str(i),heading='₹ 500 +GST',category='Monthly',fields={'Speed':'100 Mbps','Validity':'30 days'},source_url='https://www.jio.com/selfcare/plans/fiber/fiber-prepaid-plans-list/',notes=['GST extra']) for i in range(count)]
    return normalize(provider,raw)

def evidence(provider, records, complete=True):
    return dict(complete=complete,scope=SCOPES[provider],record_count=len(records),method='fixture')

class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.path=Path(self.tmp.name)/'test.db'
        migrate(self.path); self.service=RefreshService(self.path)
    def tearDown(self): self.tmp.cleanup()
    def apply(self, provider, records, proof=None):
        queued=self.service.enqueue(provider); job=self.service.claim()
        return apply_batch(self.path,provider,records,proof or evidence(provider,records),job['job_id'])
    def rows(self, sql, args=()):
        with closing(self.service.connect()) as db: return [dict(r) for r in db.execute(sql,args)]

    def test_upsert_prices_speeds_validity_benefits_and_history(self):
        for provider in PROVIDERS:
            records=batch(provider)
            self.assertEqual(self.apply(provider,records)['added'],4)
            before=self.rows('SELECT * FROM broadband_plans WHERE provider=?',(PROVIDERS[provider],))
            changed=copy.deepcopy(records)
            changed[0]['price']=600; changed[0]['speed_mbps']=150
            changed[0]['_metadata']['benefits']=['New TV']; changed[0]['_metadata']['validity_text']='35 days'
            counts=self.apply(provider,changed)
            self.assertEqual(counts,dict(added=0,updated=1,unchanged=3,inactive=0))
            after=self.rows('SELECT * FROM broadband_plans WHERE provider=?',(PROVIDERS[provider],))
            self.assertEqual(len(after),4)
            self.assertEqual([r['record_key'] for r in before],[r['record_key'] for r in after])
            self.assertEqual(self.apply(provider,changed)['unchanged'],4)
            history=self.rows('SELECT * FROM catalog_price_history WHERE provider=?',(PROVIDERS[provider],))
            self.assertEqual(len(history),1)
            self.assertEqual((history[0]['old_price'],history[0]['new_price']),(500,600))
            self.assertEqual(history[0]['currency'],'INR')

    def test_partial_empty_duplicates_and_wrong_city_preserve_all(self):
        for kind in ['partial','empty','duplicate','city','wrong_source','count']:
            with self.subTest(kind=kind):
                tmp=Path(self.tmp.name)/(kind+'.db'); migrate(tmp); service=RefreshService(tmp)
                records=batch(); service.enqueue('act'); job=service.claim()
                apply_batch(tmp,'act',records,evidence('act',records),job['job_id'])
                with closing(service.connect()) as db: before=[tuple(r) for r in db.execute('SELECT * FROM broadband_plans')]
                candidate=copy.deepcopy(records); proof=evidence('act',candidate)
                if kind=='partial': candidate=candidate[:1]; proof=evidence('act',candidate)
                if kind=='empty': candidate=[]; proof=evidence('act',candidate)
                if kind=='duplicate': candidate.append(candidate[0]); proof=evidence('act',candidate)
                if kind=='city': candidate[0]['city']='Delhi'
                if kind=='wrong_source': candidate[0]['source_url']='https://example.com'
                if kind=='count': proof['record_count']=999
                service.enqueue('act'); job=service.claim()
                with self.assertRaises(ValueError): apply_batch(tmp,'act',candidate,proof,job['job_id'])
                with closing(service.connect()) as db: self.assertEqual(before,[tuple(r) for r in db.execute('SELECT * FROM broadband_plans')])

    def test_only_complete_missing_inactive_and_location_separation(self):
        records=batch(); self.apply('act',records)
        other=copy.deepcopy(records[0]); other['city']='Delhi'; other['state']='Delhi'
        key=identity(other)
        with closing(self.service.connect()) as db,db:
            columns=[r[1] for r in db.execute('PRAGMA table_info(broadband_plans)')]
            row=dict(db.execute('SELECT * FROM broadband_plans LIMIT 1').fetchone())
            row.update(city='Delhi',state='Delhi',stable_key=key,record_key=key)
            db.execute(f"INSERT INTO broadband_plans ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",[row[c] for c in columns])
        self.assertEqual(self.apply('act',records[:3])['inactive'],1)
        self.assertEqual(self.rows("SELECT is_active FROM broadband_plans WHERE city='Delhi'")[0]['is_active'],1)
        self.assertEqual(self.apply('act',records)['updated'],1)
        self.assertEqual(len(self.rows('SELECT * FROM broadband_plans')),5)

    def test_idempotent_migration_and_fallback_duration(self):
        records=batch('hathway'); self.apply('hathway',records)
        before=self.rows('SELECT * FROM broadband_plans'); migrate(self.path)
        self.assertEqual(before,self.rows('SELECT * FROM broadband_plans'))
        r=copy.deepcopy(records[0]); r['price']=900; r['speed_mbps']=500
        self.assertEqual(identity(r),identity(records[0]))
        r['_raw']['duration_options_text']='24 months'
        self.assertNotEqual(identity(r),identity(records[0]))

    def test_lease_and_duplicate_queue(self):
        other=RefreshService(self.path)
        self.assertTrue(self.service.lease()); self.assertFalse(other.lease())
        self.service.enqueue('act')
        with self.assertRaises(HTTPException): other.enqueue('act')
        with self.assertRaises(HTTPException): other.enqueue('all')
        self.assertEqual(len(self.rows('SELECT * FROM catalog_refresh_jobs')),1)

    def test_schedule_opt_in_and_persistent_due_time(self):
        self.service.schedule()
        self.assertEqual(self.rows('SELECT * FROM refresh_schedule'),[])
        self.service.enabled=True; self.service.schedule()
        self.assertEqual(len(self.rows('SELECT * FROM refresh_schedule')),1)
        with closing(self.service.connect()) as db,db: db.execute('UPDATE refresh_schedule SET next_run=?',(time.time()-1,))
        self.service.schedule(); self.service.schedule()
        self.assertEqual(len(self.rows('SELECT * FROM catalog_refresh_jobs')),3)

class ServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_completed_failed_and_incomplete_jobs(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'test.db'; migrate(path)
            records=batch()
            async def success(job): return dict(ok=True,records=records,evidence=evidence('act',records))
            invalidated=[]
            service=RefreshService(path,lambda: invalidated.append(True),success)
            service.enqueue('act'); await service.execute(service.claim())
            state=service.status()['providers'][1]
            self.assertEqual(state['job']['status'],'completed')
            stamp=state['last_success_at']; self.assertTrue(stamp); self.assertEqual(invalidated,[True])
            async def failure(job): return dict(ok=False,error='Fixture access denied')
            service.worker=failure; service.enqueue('act'); await service.execute(service.claim())
            state=service.status()['providers'][1]
            self.assertEqual(state['job']['status'],'failed'); self.assertEqual(state['last_success_at'],stamp)
            self.assertIn('access denied',state['last_error'])
            async def partial(job): return dict(ok=True,records=records,evidence=evidence('act',records,False))
            service.worker=partial; service.enqueue('act'); await service.execute(service.claim())
            self.assertEqual(service.status()['providers'][1]['job']['status'],'failed')
            with closing(service.connect()) as db: self.assertEqual(db.execute('SELECT COUNT(*) FROM broadband_plans').fetchone()[0],4)

if __name__=='__main__': unittest.main()
