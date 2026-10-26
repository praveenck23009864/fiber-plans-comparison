"""One isolated provider scrape. Output files contain no credentials."""
import json
import sys
from pathlib import Path
from cloud_browser import error_message
from refresh_plans import normalize
from refresh_store import validate

def scrape_batch(provider):
    if provider == 'jio':
        import jio_adapter as adapter
        records = normalize(provider, adapter.scrape())
    elif provider == 'act':
        import act_adapter as adapter
        raw, url = adapter.scrape()
        records = normalize(provider, raw, url)
    elif provider == 'hathway':
        import hathway_adapter as adapter
        raw, url, tax = adapter.scrape()
        records = normalize(provider, raw, url, tax)
    else:
        raise ValueError('Unknown provider')
    evidence = adapter.LAST_COVERAGE or {}
    validate(provider, records, evidence)
    return {'records': records, 'evidence': evidence}

def main():
    provider, output = sys.argv[1:]
    try:
        result = {'ok': True, **scrape_batch(provider)}
    except Exception as exc:
        result = {'ok': False, 'error': error_message(exc)}
    Path(output).write_text(json.dumps(result, ensure_ascii=False, allow_nan=False), encoding='utf-8')

if __name__ == '__main__': main()
