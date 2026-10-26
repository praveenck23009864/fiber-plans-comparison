"""Additive catalog migration, stable identities and atomic provider upserts."""
import hashlib
import json
import math
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from urllib.parse import urlsplit
from refresh_plans import FIELDS, PROVIDERS, clean

SCOPES = {'jio': 'general', 'act': 'Chennai', 'hathway': 'Chennai'}
HOSTS = {'jio': 'www.jio.com', 'act': 'www.actcorp.in', 'hathway': 'www.hathway.com'}

def now(): return datetime.now(timezone.utc).isoformat()
def encoded(value): return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(',', ':'))

def identity(record):
    raw, meta = record['_raw'], record['_metadata']
    source_id = clean(raw.get('plan_id'))
    location = [record.get(k) for k in ('state', 'city', 'pincode')]
    if source_id:
        plan = ['source-id', source_id, meta.get('category')]
    else:
        # Price and speed deliberately excluded: both can change for the same plan.
        duration = clean(raw.get('duration_options_text') or meta.get('billing_period') or meta.get('validity_text'))
        if not duration:
            raise ValueError('No provider ID or explicit billing duration for fallback identity')
        plan = ['name-duration', clean(record['plan_name']).casefold(), duration.casefold()]
    return hashlib.sha256(encoded([record['provider'], location, plan]).encode()).hexdigest()

def tracking(record):
    raw, meta = record['_raw'], record['_metadata']
    currency = 'INR' if '\u20b9' in str(raw.get('price_text') or raw.get('heading') or '') else None
    billing = meta.get('validity_text') or raw.get('duration_options_text') or meta.get('billing_period')
    conditions = {k: raw[k] for k in ('price_basis','installation_terms','notes','row_text','raw_text','raw_details') if raw.get(k)}
    if meta.get('tax_status'): conditions['tax_status'] = meta['tax_status']
    return currency, billing, encoded(conditions) if conditions else None

def from_row(row):
    return {**{key: row[key] for key in FIELDS}, '_raw': json.loads(row['raw_json']), '_metadata': json.loads(row['metadata_json'])}

def migrate(path):
    with closing(sqlite3.connect(path, timeout=30)) as db, db:
        db.execute('BEGIN IMMEDIATE')
        columns = {r[1] for r in db.execute('PRAGMA table_info(broadband_plans)')}
        if not columns:
            db.execute('''CREATE TABLE broadband_plans (provider TEXT NOT NULL,plan_name TEXT NOT NULL,price REAL NOT NULL CHECK(price>0),speed_mbps REAL,state TEXT,city TEXT,pincode TEXT,offer TEXT,offer_price REAL,offer_expiry TEXT,source_url TEXT NOT NULL,availability_verified INTEGER NOT NULL,record_key TEXT PRIMARY KEY,metadata_json TEXT NOT NULL,raw_json TEXT NOT NULL,last_checked TEXT NOT NULL)''')
        for name, definition in {'stable_key':'TEXT','is_active':'INTEGER NOT NULL DEFAULT 1','last_seen_at':'TEXT','updated_at':'TEXT','currency':'TEXT','billing_period':'TEXT','pricing_conditions':'TEXT'}.items():
            if name not in columns: db.execute(f'ALTER TABLE broadband_plans ADD COLUMN {name} {definition}')
        db.row_factory = sqlite3.Row
        for row in db.execute('SELECT * FROM broadband_plans WHERE stable_key IS NULL').fetchall():
            record = from_row(row)
            currency, billing, conditions = tracking(record)
            db.execute('UPDATE broadband_plans SET stable_key=?,last_seen_at=last_checked,updated_at=last_checked,currency=?,billing_period=?,pricing_conditions=? WHERE record_key=?', (identity(record), currency, billing, conditions, row['record_key']))
        db.execute('CREATE UNIQUE INDEX IF NOT EXISTS broadband_stable_key ON broadband_plans(stable_key)')
        for sql in [
            '''CREATE TABLE IF NOT EXISTS catalog_refresh_jobs (job_id TEXT PRIMARY KEY,batch_id TEXT NOT NULL,provider TEXT NOT NULL,scope TEXT NOT NULL,status TEXT NOT NULL,created_at TEXT NOT NULL,started_at TEXT,finished_at TEXT,error TEXT,added INTEGER DEFAULT 0,updated INTEGER DEFAULT 0,unchanged INTEGER DEFAULT 0,inactive INTEGER DEFAULT 0,complete INTEGER DEFAULT 0,evidence_json TEXT)''',
            "CREATE UNIQUE INDEX IF NOT EXISTS refresh_no_overlap ON catalog_refresh_jobs(provider,scope) WHERE status IN ('queued','running')",
            '''CREATE TABLE IF NOT EXISTS provider_refresh_state (provider TEXT NOT NULL,scope TEXT NOT NULL,last_attempt_at TEXT,last_success_at TEXT,last_job_id TEXT,last_error TEXT,PRIMARY KEY(provider,scope))''',
            '''CREATE TABLE IF NOT EXISTS catalog_price_history (id INTEGER PRIMARY KEY,record_key TEXT NOT NULL,provider TEXT NOT NULL,scope TEXT NOT NULL,old_price REAL,new_price REAL,currency TEXT,billing_period TEXT,pricing_conditions TEXT,old_currency TEXT,old_billing_period TEXT,old_pricing_conditions TEXT,changed_at TEXT NOT NULL,job_id TEXT NOT NULL)''',
            'CREATE TABLE IF NOT EXISTS refresh_leases (name TEXT PRIMARY KEY,owner TEXT NOT NULL,expires_at REAL NOT NULL)',
            'CREATE TABLE IF NOT EXISTS refresh_schedule (name TEXT PRIMARY KEY,next_run REAL NOT NULL)',
            'CREATE TABLE IF NOT EXISTS app_migrations (name TEXT PRIMARY KEY,applied_at TEXT NOT NULL)',
        ]: db.execute(sql)
        db.execute("INSERT OR IGNORE INTO app_migrations VALUES ('002_catalog_refresh',?)", (now(),))

def validate(provider, records, evidence):
    if provider not in PROVIDERS or not records or not evidence.get('complete'):
        raise ValueError('Empty or incomplete scrape; previous plans retained')
    if evidence.get('scope') != SCOPES[provider]: raise ValueError('Scrape scope was not verified')
    if evidence.get('record_count') != len(records): raise ValueError('Coverage count does not match normalized records')
    unique = {}
    for r in records:
        if r['provider'] != PROVIDERS[provider]: raise ValueError('Wrong provider in batch')
        if provider == 'jio':
            if any(r.get(k) is not None for k in ('state','city','pincode')): raise ValueError('Unexpected Jio location')
        elif r.get('city') != 'Chennai' or r.get('state') != 'Tamil Nadu' or r.get('pincode') is not None:
            raise ValueError('Unexpected city/location in batch')
        source = urlsplit(r['source_url'])
        if source.scheme != 'https' or source.hostname != HOSTS[provider]: raise ValueError('Unexpected source URL')
        for field in ('price','speed_mbps'):
            value = r[field]
            if field == 'speed_mbps' and value is None: continue
            if isinstance(value, bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value <= 0:
                raise ValueError('Invalid numeric plan field')
        if not clean(r['plan_name']): raise ValueError('Missing plan identity')
        encoded(r)
        key = identity(r)
        if key in unique: raise ValueError('Duplicate stable plan identity')
        unique[key] = r
    return unique

def apply_batch(path, provider, records, evidence, job_id):
    keyed = validate(provider, records, evidence)
    stamp = now(); counts = dict(added=0, updated=0, unchanged=0, inactive=0)
    with closing(sqlite3.connect(path, timeout=30)) as db, db:
        db.row_factory = sqlite3.Row
        db.execute('BEGIN IMMEDIATE')
        job = db.execute('SELECT status FROM catalog_refresh_jobs WHERE job_id=?', (job_id,)).fetchone()
        if not job or job['status'] != 'running': raise ValueError('Refresh no longer owns a running job')
        # Protect against a drastic partial grid even when DOM stability was observed.
        old = db.execute('SELECT * FROM broadband_plans WHERE provider=? AND city IS ?', (PROVIDERS[provider], None if provider == 'jio' else 'Chennai')).fetchall()
        active_count = sum(r['is_active'] for r in old)
        if active_count and len(records) < math.ceil(active_count * .75):
            raise ValueError('Scrape dropped more than 25% of active plans; completeness requires review. Previous data retained')
        for key, r in keyed.items():
            previous = db.execute('SELECT * FROM broadband_plans WHERE stable_key=?', (key,)).fetchone()
            currency, billing, conditions = tracking(r)
            content = [r[f] for f in FIELDS] + [encoded(r['_metadata']), encoded(r['_raw']), currency, billing, conditions]
            columns = list(FIELDS) + ['metadata_json','raw_json','currency','billing_period','pricing_conditions']
            if previous:
                previous_content = [previous[f] for f in FIELDS] + [encoded(json.loads(previous['metadata_json'])), encoded(json.loads(previous['raw_json'])), previous['currency'], previous['billing_period'], previous['pricing_conditions']]
                changed = content != previous_content or not previous['is_active']
                counts['updated' if changed else 'unchanged'] += 1
                if previous['price'] != r['price']:
                    db.execute('''INSERT INTO catalog_price_history (record_key,provider,scope,old_price,new_price,currency,billing_period,pricing_conditions,old_currency,old_billing_period,old_pricing_conditions,changed_at,job_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''', (previous['record_key'], PROVIDERS[provider], SCOPES[provider], previous['price'], r['price'], currency, billing, conditions, previous['currency'],previous['billing_period'],previous['pricing_conditions'],stamp, job_id))
                db.execute(f"UPDATE broadband_plans SET {','.join(c+'=?' for c in columns)},last_checked=?,last_seen_at=?,updated_at=?,is_active=1 WHERE stable_key=?", content + [stamp, stamp, stamp if changed else previous['updated_at'], key])
            else:
                counts['added'] += 1
                all_columns = columns + ['record_key','stable_key','last_checked','last_seen_at','updated_at','is_active']
                db.execute(f"INSERT INTO broadband_plans ({','.join(all_columns)}) VALUES ({','.join('?' for _ in all_columns)})", content + [key,key,stamp,stamp,stamp,1])
        for r in old:
            if r['is_active'] and r['stable_key'] not in keyed:
                db.execute('UPDATE broadband_plans SET is_active=0,updated_at=? WHERE stable_key=?', (stamp,r['stable_key']))
                counts['inactive'] += 1
        db.execute('''UPDATE catalog_refresh_jobs SET status='completed',finished_at=?,added=?,updated=?,unchanged=?,inactive=?,complete=1,evidence_json=? WHERE job_id=?''', (stamp,counts['added'],counts['updated'],counts['unchanged'],counts['inactive'],encoded(evidence),job_id))
        db.execute('''UPDATE provider_refresh_state SET last_success_at=?,last_error=NULL,last_job_id=? WHERE provider=? AND scope=?''', (stamp,job_id,provider,SCOPES[provider]))
    return counts
