"""Additive migration; existing provider tables are never modified."""
import json
import sqlite3
from contextlib import closing
from optimum_models import Address, same_address, LookupFailure

def migrate(path):
    with closing(sqlite3.connect(path, timeout=20)) as db, db:
        db.executescript('''
            CREATE TABLE IF NOT EXISTS app_migrations (name TEXT PRIMARY KEY, applied_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS optimum_lookups (
                lookup_id TEXT PRIMARY KEY, requested_address_json TEXT NOT NULL,
                verified_address_json TEXT NOT NULL, street TEXT NOT NULL, city TEXT NOT NULL,
                state TEXT NOT NULL, zip_code TEXT NOT NULL, unit TEXT,
                service_location_id TEXT, captured_at TEXT NOT NULL, source_url TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS optimum_address_plans (
                id INTEGER PRIMARY KEY, lookup_id TEXT NOT NULL REFERENCES optimum_lookups(lookup_id),
                provider TEXT NOT NULL CHECK(provider = 'Optimum'), plan_name TEXT NOT NULL,
                speed_mbps REAL, monthly_price_cents INTEGER, currency TEXT,
                promotional_period TEXT, conditions TEXT, supporting_card_text TEXT NOT NULL,
                source_url TEXT NOT NULL, captured_at TEXT NOT NULL,
                UNIQUE(lookup_id, plan_name)
            );
            CREATE INDEX IF NOT EXISTS optimum_address_index ON optimum_lookups(street,city,state,zip_code,unit);
            INSERT OR IGNORE INTO app_migrations VALUES ('001_optimum_address_lookups', strftime('%Y-%m-%dT%H:%M:%SZ','now'));
        ''')

def save(path, lookup_id, requested, result):
    verified = Address(**result['verified_address'])
    expected = requested if requested.unit else requested.model_copy(update={'unit': verified.unit})
    if not same_address(expected, verified) or not result['plans']:
        raise LookupFailure('address_mismatch', 'Results do not belong to the verified address. Nothing was saved.')
    with closing(sqlite3.connect(path, timeout=20)) as db, db:
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('INSERT INTO optimum_lookups VALUES (?,?,?,?,?,?,?,?,?,?,?)', (
            lookup_id, requested.model_dump_json(), verified.model_dump_json(), verified.street,
            verified.city, verified.state, verified.zip_code, verified.unit,
            result['service_location_id'], result['captured_at'], result['source_url']))
        db.executemany('''INSERT INTO optimum_address_plans
            (lookup_id,provider,plan_name,speed_mbps,monthly_price_cents,currency,promotional_period,
             conditions,supporting_card_text,source_url,captured_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)''', [
            (lookup_id, p['provider'], p['plan_name'], p['speed_mbps'], p['monthly_price_cents'],
             p['currency'], p['promotional_period'], p['conditions'], p['supporting_card_text'],
             result['source_url'], result['captured_at']) for p in result['plans']])

def read(path, lookup_id):
    with closing(sqlite3.connect(path, timeout=20)) as db:
        db.row_factory = sqlite3.Row
        row = db.execute('SELECT * FROM optimum_lookups WHERE lookup_id=?', (lookup_id,)).fetchone()
        if not row:
            return None
        return dict(lookup_id=lookup_id, status='succeeded', cached=True,
                    requested_address=json.loads(row['requested_address_json']),
                    verified_address=json.loads(row['verified_address_json']),
                    service_location_id=row['service_location_id'], captured_at=row['captured_at'],
                    source_url=row['source_url'],
                    plans=[dict(p) for p in db.execute('SELECT * FROM optimum_address_plans WHERE lookup_id=? ORDER BY id', (lookup_id,))])
