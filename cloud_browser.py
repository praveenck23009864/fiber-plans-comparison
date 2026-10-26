"""Bounded Browserbase requests, hard session TTL, and parent cleanup registry."""
import json
import os
from pathlib import Path
from browserbase import Browserbase

class ManagedBrowserbase(Browserbase):
    def __init__(self, **kwargs):
        super().__init__(timeout=20, max_retries=1, **kwargs)
        create, update = self.sessions.create, self.sessions.update
        self._allocated = []
        registry = os.getenv('REFRESH_SESSION_REGISTRY')
        if registry and Path(registry).exists():
            self._allocated = json.loads(Path(registry).read_text(encoding='utf-8'))

        def write_registry():
            name = os.getenv('REFRESH_SESSION_REGISTRY')
            if name:
                path = Path(name)
                temp = path.with_suffix('.tmp')
                temp.write_text(json.dumps(self._allocated), encoding='utf-8')
                temp.replace(path)

        def tracked_create(**options):
            options.setdefault('api_timeout', 600)
            session = create(**options)
            self._allocated.append(session.id)
            try:
                write_registry()
            except Exception:
                update(session.id, project_id=options.get('project_id'), status='REQUEST_RELEASE')
                raise
            return session

        def tracked_update(session_id, **options):
            result = update(session_id, **options)
            if options.get('status') == 'REQUEST_RELEASE' and session_id in self._allocated:
                self._allocated.remove(session_id)
                write_registry()
            return result

        self.sessions.create = tracked_create
        self.sessions.update = tracked_update

def error_message(exc):
    status = getattr(exc, 'status_code', None)
    if status in (402,429): return 'Browserbase quota or rate limit exceeded; saved plans retained.'
    if status in (401,403): return 'Browserbase access denied; check backend credentials.'
    if type(exc).__name__ in ('APIConnectionError','APITimeoutError'):
        return f'Browserbase {type(exc).__name__}; check backend network connectivity. Saved plans retained.'
    if isinstance(exc, (ValueError, RuntimeError)):
        import re
        message = str(exc)
        for name in ('API_KEY','BROWSERBASE_API_KEY','BROWSERBASE_PROJECT_ID'):
            value = os.getenv(name)
            if value: message = message.replace(value, '[REDACTED]')
        return re.sub(r'(?:https?|wss?)://[^\s]+', '[URL]', message)[:500]
    return f'{type(exc).__name__}: scrape did not complete; saved plans retained.'
