"""Durable refresh queue and one leased dispatcher/scheduler per SQLite database."""
import asyncio
import json
import os
import sqlite3
import sys
import time
from contextlib import asynccontextmanager, closing, suppress
from pathlib import Path
from uuid import uuid4
from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, ConfigDict
from refresh_plans import PROVIDERS
from refresh_store import SCOPES, migrate, now, apply_batch
from cloud_browser import error_message

ROOT = Path(__file__).resolve().parent

class RefreshRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    provider: str

class RefreshService:
    def __init__(self, path, invalidate=lambda: None, worker=None):
        self.path = Path(path)
        self.invalidate = invalidate
        self.owner = str(uuid4())
        self.worker = worker or self.scrape_process
        self.loop_task = None
        self.active_task = None
        self.enabled = os.getenv('REFRESH_SCHEDULE_ENABLED', 'false').lower() == 'true'
        self.interval = max(1, float(os.getenv('REFRESH_INTERVAL_HOURS', '24'))) * 3600
        self.timeout = min(1800, max(60, int(os.getenv('REFRESH_TIMEOUT_SECONDS', '900'))))

    def connect(self):
        db = sqlite3.connect(self.path, timeout=20); db.row_factory = sqlite3.Row
        return db

    def enqueue(self, provider):
        selected = list(PROVIDERS) if provider == 'all' else [provider]
        if any(p not in PROVIDERS for p in selected): raise HTTPException(422, 'Unknown provider')
        batch, stamp = str(uuid4()), now()
        jobs = []
        try:
            with closing(self.connect()) as db, db:
                db.execute('BEGIN IMMEDIATE')
                for p in selected:
                    job_id = str(uuid4())
                    db.execute('''INSERT INTO catalog_refresh_jobs (job_id,batch_id,provider,scope,status,created_at) VALUES (?,?,?,?,'queued',?)''', (job_id,batch,p,SCOPES[p],stamp))
                    jobs.append(job_id)
        except sqlite3.IntegrityError:
            raise HTTPException(409, 'A requested provider already has a queued or running refresh.') from None
        return dict(batch_id=batch, job_ids=jobs, status='queued')

    def status(self):
        with closing(self.connect()) as db:
            states = []
            for provider in PROVIDERS:
                state = db.execute('SELECT * FROM provider_refresh_state WHERE provider=? AND scope=?', (provider,SCOPES[provider])).fetchone()
                job = db.execute('SELECT * FROM catalog_refresh_jobs WHERE provider=? AND scope=? ORDER BY created_at DESC,rowid DESC LIMIT 1', (provider,SCOPES[provider])).fetchone()
                states.append(dict(provider=provider, name=PROVIDERS[provider], scope=SCOPES[provider],
                                   last_attempt_at=state['last_attempt_at'] if state else None,
                                   last_success_at=state['last_success_at'] if state else None,
                                   last_error=state['last_error'] if state else None,
                                   job=dict(job) if job else None))
            schedule = db.execute("SELECT next_run FROM refresh_schedule WHERE name='catalog'").fetchone()
            return dict(providers=states, schedule_enabled=self.enabled, interval_hours=self.interval/3600,
                        next_scheduled_at=schedule['next_run'] if self.enabled and schedule else None)

    def lease(self):
        with closing(self.connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT * FROM refresh_leases WHERE name='dispatcher'").fetchone()
            if row and row['owner'] != self.owner and row['expires_at'] > time.time(): return False
            db.execute("INSERT INTO refresh_leases VALUES ('dispatcher',?,?) ON CONFLICT(name) DO UPDATE SET owner=excluded.owner,expires_at=excluded.expires_at", (self.owner,time.time()+30))
            return True

    def claim(self):
        with closing(self.connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            # An interrupted worker may still hold a browser until its 10-minute TTL.
            # Do not reclaim it or launch another job until timeout + TTL have elapsed.
            for row in db.execute("SELECT * FROM catalog_refresh_jobs WHERE status='running'").fetchall():
                from datetime import datetime
                if time.time() - datetime.fromisoformat(row['started_at']).timestamp() > self.timeout + 660:
                    self.fail_in_db(db, row, 'Interrupted refresh expired; previous records retained.')
                else:
                    return None
            row = db.execute("SELECT * FROM catalog_refresh_jobs WHERE status='queued' ORDER BY created_at,rowid LIMIT 1").fetchone()
            if row:
                stamp = now()
                db.execute("UPDATE catalog_refresh_jobs SET status='running',started_at=? WHERE job_id=?", (stamp,row['job_id']))
                db.execute('''INSERT INTO provider_refresh_state (provider,scope,last_attempt_at,last_job_id) VALUES (?,?,?,?) ON CONFLICT(provider,scope) DO UPDATE SET last_attempt_at=excluded.last_attempt_at,last_job_id=excluded.last_job_id''', (row['provider'],row['scope'],stamp,row['job_id']))
                return dict(row)

    def schedule(self):
        if not self.enabled: return
        with closing(self.connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            db.execute("INSERT OR IGNORE INTO refresh_schedule VALUES ('catalog',?)", (time.time()+self.interval,))
            due = db.execute("SELECT next_run FROM refresh_schedule WHERE name='catalog'").fetchone()[0]
            if due <= time.time() and not db.execute("SELECT 1 FROM catalog_refresh_jobs WHERE status IN ('queued','running') LIMIT 1").fetchone():
                batch, stamp = str(uuid4()), now()
                for provider in PROVIDERS:
                    db.execute("INSERT INTO catalog_refresh_jobs(job_id,batch_id,provider,scope,status,created_at) VALUES (?,?,?,?,'queued',?)", (str(uuid4()),batch,provider,SCOPES[provider],stamp))
                db.execute("UPDATE refresh_schedule SET next_run=? WHERE name='catalog'", (time.time()+self.interval,))

    @staticmethod
    def fail_in_db(db, job, error):
        db.execute("UPDATE catalog_refresh_jobs SET status='failed',finished_at=?,error=? WHERE job_id=? AND status IN ('queued','running')", (now(),error,job['job_id']))
        db.execute('''INSERT INTO provider_refresh_state(provider,scope,last_attempt_at,last_job_id,last_error) VALUES (?,?,?,?,?) ON CONFLICT(provider,scope) DO UPDATE SET last_error=excluded.last_error,last_job_id=excluded.last_job_id''', (job['provider'],job['scope'],now(),job['job_id'],error))

    async def execute(self, job):
        try:
            result = await self.worker(job)
            if not result.get('ok'): raise ValueError(result.get('error') or 'Incomplete scrape')
            apply_batch(self.path, job['provider'], result['records'], result['evidence'], job['job_id'])
            self.invalidate()
        except asyncio.CancelledError:
            with closing(self.connect()) as db, db: self.fail_in_db(db, job, 'Refresh cancelled during shutdown; saved plans retained.')
            raise
        except Exception as exc:
            with closing(self.connect()) as db, db: self.fail_in_db(db, job, error_message(exc))

    async def scrape_process(self, job):
        folder = ROOT / '.runtime' / 'refresh'; folder.mkdir(parents=True, exist_ok=True)
        output = folder / (job['job_id'] + '.json')
        registry = folder / (job['job_id'] + '.sessions.json')
        env = {**os.environ, 'REFRESH_SESSION_REGISTRY': str(registry), 'PYTHONIOENCODING':'utf-8'}
        process = await asyncio.create_subprocess_exec(sys.executable, str(ROOT / 'refresh_worker.py'), job['provider'], str(output), cwd=ROOT, env=env, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        try:
            await asyncio.wait_for(process.wait(), self.timeout)
            if process.returncode != 0 or not output.exists():
                return {'ok': False, 'error': 'Scraper process failed; previous plans retained.'}
            return json.loads(output.read_text(encoding='utf-8'))
        except asyncio.TimeoutError:
            return {'ok': False, 'error': 'Provider scrape timed out; previous plans retained.'}
        finally:
            if process.returncode is None:
                process.terminate()
                await process.wait()
            if registry.exists():
                ids = json.loads(registry.read_text(encoding='utf-8'))
                if ids:
                    await asyncio.to_thread(self.release, ids)

    @staticmethod
    def release(ids):
        from browserbase import Browserbase
        with Browserbase(api_key=os.getenv('BROWSERBASE_API_KEY'), timeout=15, max_retries=1) as client:
            for session_id in ids:
                try: client.sessions.update(session_id, project_id=os.getenv('BROWSERBASE_PROJECT_ID'), status='REQUEST_RELEASE')
                except Exception:
                    import logging
                    logging.getLogger(__name__).error('Could not confirm Browserbase release; hard session TTL is 600 seconds.')

    async def loop(self):
        while True:
            try:
                if self.lease():
                    self.schedule()
                    if self.active_task is None or self.active_task.done():
                        if self.active_task: await self.active_task
                        job = self.claim()
                        self.active_task = asyncio.create_task(self.execute(job)) if job else None
                elif self.active_task and not self.active_task.done():
                    self.active_task.cancel()
                    with suppress(asyncio.CancelledError): await self.active_task
            except asyncio.CancelledError: raise
            except Exception:
                import logging
                logging.getLogger(__name__).error('Refresh dispatcher encountered a database error; will check again.')
            await asyncio.sleep(2)

    async def start(self):
        migrate(self.path)
        self.loop_task = asyncio.create_task(self.loop())

    async def close(self):
        for task in (self.loop_task,self.active_task):
            if task and not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError): await task
        with closing(self.connect()) as db, db:
            db.execute("DELETE FROM refresh_leases WHERE name='dispatcher' AND owner=?", (self.owner,))

def install(app, path, dependencies, invalidate):
    service = RefreshService(path, invalidate)
    @asynccontextmanager
    async def lifespan(_app):
        await service.start()
        try: yield
        finally: await service.close()
    router = APIRouter(prefix='/api/refresh', tags=['Catalog refresh'], dependencies=dependencies, lifespan=lifespan)

    @router.post('', status_code=202)
    async def start(body: RefreshRequest, response: Response):
        response.headers['Cache-Control'] = 'no-store'
        return service.enqueue(body.provider)

    @router.get('')
    async def status(response: Response):
        response.headers['Cache-Control'] = 'no-store'
        return service.status()

    app.include_router(router)
    app.state.refresh_service = service
