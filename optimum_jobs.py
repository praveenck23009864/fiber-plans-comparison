"""Single-process bounded background jobs. Restart retains only saved successes."""
import asyncio
import secrets
import time
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone
from uuid import uuid4
from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, ConfigDict
from optimum_models import Address, FullAddressInput, LookupFailure
from optimum_adapter import scrape
from optimum_store import migrate, save, read

TERMINAL = {'succeeded', 'failed', 'cancelled', 'expired'}

class Selection(BaseModel):
    model_config = ConfigDict(extra='forbid')
    choice_id: str

class Jobs:
    def __init__(self, path, runner=scrape, selection_timeout=180, job_timeout=540):
        self.path, self.runner = path, runner
        self.selection_timeout, self.job_timeout = selection_timeout, job_timeout
        self.jobs = {}

    def public(self, job):
        return {key: value for key, value in job.items() if not key.startswith('_')}

    def start(self, address):
        now = time.monotonic()
        for key in list(self.jobs):
            job = self.jobs[key]
            if job['status'] in TERMINAL and now - job['_created'] > 3600:
                del self.jobs[key]
        active = [j for j in self.jobs.values() if j['status'] not in TERMINAL]
        if any(j['requested_address'] == address.model_dump() for j in active):
            raise HTTPException(409, 'A lookup for this address is already running.')
        if len(active) >= 2 or len(self.jobs) >= 100:
            raise HTTPException(429, 'Lookup capacity reached. Try later.')
        job = dict(lookup_id=str(uuid4()), requested_address=address.model_dump(), status='queued',
                   choices=[], plans=[], cached=False, _created=now)
        self.jobs[job['lookup_id']] = job
        job['_task'] = asyncio.create_task(self.run(job, address))
        return self.public(job)

    async def run(self, job, address):
        async def ask(kind, choices):
            ids = [secrets.token_urlsafe(18) for _ in choices]
            future = asyncio.get_running_loop().create_future()
            job.update(status='awaiting_selection', selection_kind=kind,
                       selection_expires_at=datetime.fromtimestamp(time.time() + self.selection_timeout, timezone.utc).isoformat(),
                       choices=[dict(choice_id=key, **choice) for key, choice in zip(ids, choices)], _selection=future)
            try:
                chosen = await asyncio.wait_for(future, self.selection_timeout)
            except asyncio.TimeoutError:
                raise LookupFailure('selection_expired', 'Address selection expired. Start a new search.') from None
            finally:
                job['choices'] = []
                job.pop('_selection', None)
            job['status'] = 'running'
            return ids.index(chosen)
        try:
            job['status'] = 'running'
            result = await asyncio.wait_for(self.runner(address, ask), self.job_timeout)
            result['captured_at'] = datetime.now(timezone.utc).isoformat()
            # Persist before publishing success; no older-address fallback on failure.
            save(self.path, job['lookup_id'], address, result)
            job.update(result, status='succeeded')
        except asyncio.CancelledError:
            job.update(status='cancelled', code='cancelled', message='Lookup cancelled.', plans=[])
        except asyncio.TimeoutError:
            job.update(status='failed', code='timeout', message='Lookup timed out. Availability is unknown.', plans=[])
        except LookupFailure as exc:
            job.update(status='expired' if exc.code == 'selection_expired' else 'failed', code=exc.code, message=exc.message, plans=[])
        except Exception:
            job.update(status='failed', code='lookup_failed', message='Lookup or storage failed. No fresh results are available.', plans=[])
        finally:
            job['choices'] = []

    def get(self, lookup_id):
        if lookup_id in self.jobs:
            return self.public(self.jobs[lookup_id])
        saved = read(self.path, lookup_id)
        if saved:
            return saved
        raise HTTPException(404, 'Lookup not found or expired. Start a new search.')

    def select(self, lookup_id, choice_id):
        job = self.jobs.get(lookup_id)
        if not job or job['status'] != 'awaiting_selection' or '_selection' not in job:
            raise HTTPException(409, 'This lookup is not waiting for a selection.')
        if job['_selection'].done() or not any(c['choice_id'] == choice_id for c in job['choices']):
            raise HTTPException(409, 'Invalid or expired choice. Refresh lookup status.')
        job['_selection'].set_result(choice_id)
        job['status'] = 'running'
        job['choices'] = []
        return self.public(job)

    async def cancel(self, lookup_id):
        job = self.jobs.get(lookup_id)
        if not job:
            raise HTTPException(404, 'Lookup not found.')
        if job['status'] not in TERMINAL:
            job['_task'].cancel()
            with suppress(asyncio.CancelledError):
                await job['_task']
            job.update(status='cancelled', code='cancelled', message='Lookup cancelled.', plans=[], choices=[])
        return self.public(job)

    async def close(self):
        for job in list(self.jobs.values()):
            if job['status'] not in TERMINAL:
                await self.cancel(job['lookup_id'])

def install(app, path, dependencies):
    jobs = Jobs(path)
    @asynccontextmanager
    async def lifespan(_app):
        migrate(path)
        try:
            yield
        finally:
            await jobs.close()
    router = APIRouter(prefix='/api/optimum', tags=['Optimum'], dependencies=dependencies, lifespan=lifespan)

    @router.post('/lookups', status_code=202)
    async def lookup(address: FullAddressInput | Address, response: Response):
        response.headers['Cache-Control'] = 'no-store'
        return jobs.start(address.to_address() if isinstance(address, FullAddressInput) else address)

    @router.get('/lookups/{lookup_id}')
    async def status(lookup_id: str, response: Response):
        response.headers['Cache-Control'] = 'no-store'
        return jobs.get(lookup_id)

    @router.post('/lookups/{lookup_id}/selection')
    async def selection(lookup_id: str, body: Selection, response: Response):
        response.headers['Cache-Control'] = 'no-store'
        return jobs.select(lookup_id, body.choice_id)

    @router.delete('/lookups/{lookup_id}')
    async def cancel(lookup_id: str, response: Response):
        response.headers['Cache-Control'] = 'no-store'
        return await jobs.cancel(lookup_id)

    app.include_router(router)
    app.state.optimum_jobs = jobs
