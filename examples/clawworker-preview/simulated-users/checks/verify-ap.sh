#!/bin/sh
set -eu
python3 - <<'PY'
import json
import os
from decimal import Decimal
from pathlib import Path
import re
import sqlite3


def check(db):
    def meta(key):
        row = db.execute('SELECT value FROM metadata WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else None

    config = meta('worker_config')
    assert config and config['finance_target'] == 'drafts_only', 'Expected a drafts-only Worker'
    assert config['auto_save'] is False and config['mailbox'] is None and config['mail_scope'] is None, 'Expected chat-only intake and external saves disabled'
    if os.environ.get('AP_CHECK') == 'onboarding':
        assert meta('initialized') is True, 'Storage was not initialized'
        assert str(meta('unit_price_tolerance_percent')) in {'0', '0.0'}, 'Expected zero tolerance'
        setup = meta('adp_setup') or {}
        assert setup.get('worker') == os.environ['CLAWWORKER_WORKER_ID'], 'ADP setup belongs to another Worker'
        apps = setup.get('apps', {})
        assert set(apps) == {'invoice', 'po', 'do'} and setup.get('verified'), 'ADP setup is not verified'
        assert len({app['app_id'] for app in apps.values() if app.get('config_version')}) == 3, 'Expected three distinct configured ADP apps'
        return

    invoice = os.environ['AP_INVOICE']
    records = db.execute("SELECT record FROM entities WHERE kind='case' AND json_extract(facts, '$.invoice_number')=?", (invoice,)).fetchall()
    assert len(records) == 1, f'Expected one case for {invoice}, found {len(records)}'
    case = json.loads(records[0][0])
    expected = os.environ['AP_STATUS']
    assert case['status'] == expected, f"Expected {expected}, found {case['status']}"
    if expected == 'WAITING_DOCUMENTS':
        assert set(case['missing_documents']) == {'do'}, 'Expected only the delivery note to be missing'
    elif expected == 'NEEDS_HUMAN':
        assert case.get('audit', {}).get('passed') is not True, 'A discrepancy must not pass the audit'
        issues = ' '.join([case.get('reason') or '', *case.get('audit', {}).get('issues', [])])
        assert re.search(os.environ['AP_REASON'], issues, re.IGNORECASE), 'The stored discrepancy did not identify the expected issue'
    elif expected == 'MATCHED_PREVIEW_READY':
        assert case.get('audit', {}).get('passed') is True, 'Expected a successful three-way audit'
        invoice_status, = db.execute("SELECT status FROM entities WHERE kind='invoice' AND id=?", (case['documents']['invoice'],)).fetchone()
        assert invoice_status == 'MATCHED', 'Invoice was not marked matched'
        allocated = db.execute('SELECT po_id, do_id FROM allocations WHERE case_id=?', (case['id'],)).fetchall()
        assert allocated, 'No source lines were allocated'

        def number(kind, identity):
            raw, = db.execute('SELECT facts FROM entities WHERE kind=? AND id=?', (kind, identity)).fetchone()
            facts = json.loads(raw)
            return facts.get('po_number') if kind == 'po' else facts.get('do_number') or facts.get('delivery_number')

        assert {number('po', po) for po, _ in allocated} == {os.environ['AP_PO']}, 'Incorrect purchase order allocation'
        assert {number('do', delivery) for _, delivery in allocated} == set(os.environ['AP_DELIVERIES'].split(',')), 'Incorrect delivery allocation'
        for delivery in filter(None, os.environ.get('AP_UNSETTLED_DELIVERIES', '').split(',')):
            rows = db.execute("SELECT id, status FROM entities WHERE kind='do' AND coalesce(json_extract(facts, '$.do_number'), json_extract(facts, '$.delivery_number'))=?", (delivery,)).fetchall()
            assert len(rows) == 1 and rows[0][1] == 'AVAILABLE', f'Expected {delivery} to remain available'
            consumed = db.execute('SELECT consumed FROM doc_lines WHERE document_id=?', (rows[0][0],)).fetchall()
            assert consumed and all(Decimal(quantity) == 0 for quantity, in consumed), f'{delivery} was consumed outside its period'


try:
    stores = list(Path('/workspace/work').resolve().rglob('ap.sqlite3'))
    assert len(stores) == 1, f'Expected one AP ledger, found {len(stores)}'
    with sqlite3.connect(stores[0].as_uri() + '?mode=ro', uri=True) as db:
        check(db)
except (AssertionError, KeyError, TypeError, ValueError, sqlite3.Error) as error:
    print(f'AP verification failed: {error}')
    reward = 0
else:
    print('AP verification passed.')
    reward = 1
Path('/logs/verifier/reward.txt').write_text(f'{reward}\n')
PY
