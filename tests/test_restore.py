import json
from copy import deepcopy
from hashlib import sha256
from threading import Barrier, Thread

import pytest

from app.archive.manifest import canonical, evidence_core
from app.archive.restore import restore_certificate, verify_certificate
from app.archive.store import Archive
from app.errors import InputError
from app.main import create_app
from app.rules import ATTACHMENT_VERIFIED, RULE_VERIFIED


def issue(client, body=None):
    run_id = client.post('/api/runs', json=body or {'sample': 'dirty'}).json['run_id']
    return client.get('/api/export/' + run_id).json


def reseal(certificate):
    certificate['sha256'] = sha256(canonical(evidence_core(certificate)).encode()).hexdigest()
    return certificate


def wipe(database):
    import sqlite3
    with sqlite3.connect(database) as db:
        db.execute('DELETE FROM monitoring')


@pytest.fixture
def database(tmp_path):
    return tmp_path / 'archive.sqlite'


@pytest.fixture
def lossy(database):
    client = create_app(database).test_client()
    certificate = issue(client)
    wipe(database)
    return client, certificate


@pytest.fixture
def retired(monkeypatch):
    from app import rules
    monkeypatch.delitem(rules.ENGINES, 'CEMS-1.0')
    yield
    assert 'CEMS-1.0' not in rules.ENGINES


def test_restore_recovers_original_conclusions(lossy):
    client, certificate = lossy
    response = client.post('/api/restore', json=certificate)
    assert response.status_code == 201
    body = response.json
    assert (body['reused'], body['verification']['status']) == (False, RULE_VERIFIED)
    assert body['verification']['rule_verified'] is True
    run_id = body['run_id']
    hours = client.get(f'/api/runs/{run_id}/hours').json
    assert (hours['rows'][0]['mg_nm3'], hours['totals']) == ('30.50', certificate['results']['hours']['totals'])
    page = client.get('/monitor/hours?run=' + run_id).get_data(as_text=True)
    assert '30.50' in page and '按原规则重算一致' in page
    assert client.get('/api/runs').json['runs'][0]['verification'] == RULE_VERIFIED


def test_retired_rule_keeps_conclusions_without_recomputing(lossy, retired):
    client, certificate = lossy
    response = client.post('/api/restore', json=certificate)
    assert (response.status_code, response.json['verification']['status']) == (201, ATTACHMENT_VERIFIED)
    verification = response.json['verification']
    assert (verification['attachment_verified'], verification['rule_verified'],
            verification['rule_available'], verification['recomputed']) == (True, False, False, False)
    run_id = response.json['run_id']
    stored = client.get(f'/api/runs/{run_id}/hours').json
    assert stored == certificate['results']['hours']
    page = client.get('/monitor/evidence?run=' + run_id).get_data(as_text=True)
    assert '附件校验通过 · 原规则已停用未重算' in page


def test_restore_is_idempotent_and_keeps_number(lossy):
    client, certificate = lossy
    first = client.post('/api/restore', json=certificate)
    second = client.post('/api/restore', json=certificate)
    assert (first.status_code, second.status_code, second.json['reused']) == (201, 200, True)
    assert first.json['run_id'] == second.json['run_id'] == certificate['run_id']
    assert len(client.get('/api/runs').json['runs']) == 1


def test_concurrent_restore_creates_one_record(database, lossy):
    client, certificate = lossy
    store = Archive(database)
    barrier = Barrier(2)
    outcomes = []

    def worker():
        barrier.wait()
        try:
            outcomes.append(restore_certificate(store, certificate)[1])
        except InputError as error:  # pragma: no cover - guarded by assertions
            outcomes.append(error.code)

    threads = [Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(outcomes) == [False, True]
    assert len(store.history()) == 1


def test_retry_after_failure_leaves_number_free(database, lossy):
    client, certificate = lossy
    tampered = reseal(deepcopy(certificate))
    tampered['originals']['minutes'] += 'K-04,2026-11-14T00:00Z'
    attempts = [client.post('/api/restore', json=tampered) for _ in range(2)]
    assert [(r.status_code, r.json['code']) for r in attempts] == [(409, 'EVIDENCE_MISMATCH')] * 2
    assert client.get('/api/runs').json == {'runs': []}
    recovered = client.post('/api/restore', json=certificate)
    assert recovered.json['run_id'] == certificate['run_id']


def test_tampered_minutes_rejected_under_active_rule(lossy):
    client, certificate = lossy
    tampered = deepcopy(certificate)
    tampered['originals']['minutes'] = tampered['originals']['minutes'].replace(',30,mg/Nm3,', ',31,mg/Nm3,', 1)
    response = client.post('/api/restore', json=tampered)
    assert (response.status_code, response.json['code']) == (409, 'EVIDENCE_MISMATCH')
    assert client.get('/api/runs').json == {'runs': []}


def test_doctored_conclusions_rejected_under_active_rule(lossy):
    client, certificate = lossy
    doctored = deepcopy(certificate)
    doctored['results']['hours']['rows'][0]['mg_nm3'] = '99.99'
    reseal(doctored)
    response = client.post('/api/restore', json=doctored)
    assert (response.status_code, response.json['code']) == (409, 'RULE_MISMATCH')
    assert client.get('/api/runs').json == {'runs': []}


def test_doctored_conclusions_fail_balance_without_replay(lossy, retired):
    client, certificate = lossy
    doctored = deepcopy(certificate)
    doctored['results']['hours']['totals']['missing'] -= 1
    reseal(doctored)
    response = client.post('/api/restore', json=doctored)
    assert (response.status_code, response.json['code']) == (409, 'EVIDENCE_UNBALANCED')


def test_wrong_claim_digest_rejected(lossy):
    client, certificate = lossy
    certificate = deepcopy(certificate)
    certificate['sha256'] = '0' * 64
    response = client.post('/api/restore', json=certificate)
    assert (response.status_code, response.json['code']) == (409, 'EVIDENCE_MISMATCH')


def test_run_id_recomputed_from_original_rule(database, lossy, monkeypatch):
    client, certificate = lossy
    monkeypatch.setattr('app.config.VERSION', 'CEMS-2.0')
    from app.archive import manifest as manifest_module
    monkeypatch.setattr(manifest_module, 'VERSION', 'CEMS-2.0')
    response = client.post('/api/restore', json=certificate)
    # The number is derived from the certificate's own rule, not today's.
    assert response.status_code == 201 and response.json['run_id'] == certificate['manifest']['run_id']


def test_structure_errors_do_not_claim_number(client):
    assert client.post('/api/restore', json={}).json['code'] == 'BAD_CERTIFICATE'
    assert client.post('/api/restore', json={'manifest': {}}).json['code'] == 'BAD_CERTIFICATE'
    assert client.post('/api/restore', json={'manifest': {'run_id': 'x'}}).json['code'] == 'BAD_CERTIFICATE'
    assert client.get('/api/runs').json == {'runs': []}


def test_certificate_still_independently_verifiable(client):
    certificate = issue(client)
    assert verify_certificate(certificate)[0]['status'] == RULE_VERIFIED
    assert certificate['sha256'] == sha256(canonical(evidence_core(certificate)).encode()).hexdigest()


def test_restore_form_on_archive_pages(client):
    runs = client.get('/monitor/runs').get_data(as_text=True)
    evidence = client.get('/monitor/evidence').get_data(as_text=True)
    assert '证据单恢复' in runs and 'restore-form' in runs
    assert '证据单恢复' in evidence and 'restore-form' in evidence


def test_conflicting_content_keeps_existing_archive(lossy, retired):
    client, certificate = lossy
    assert client.post('/api/restore', json=certificate).status_code == 201
    doctored = deepcopy(certificate)
    doctored['results']['hours']['rows'][0]['mg_nm3'] = '99.99'
    reseal(doctored)
    response = client.post('/api/restore', json=doctored)
    assert (response.status_code, response.json['code']) == (409, 'RUN_CONFLICT')
    stored = client.get('/api/runs/' + certificate['run_id'] + '/hours').json
    assert stored == certificate['results']['hours']
    assert len(client.get('/api/runs').json['runs']) == 1


def test_malformed_body_rejected(client):
    response = client.post('/api/restore', data='not json', content_type='application/json')
    assert (response.status_code, response.json['code']) == (422, 'BAD_CERTIFICATE')
    assert client.get('/api/runs').json == {'runs': []}


def test_wrapped_certificate_accepted(lossy):
    client, certificate = lossy
    response = client.post('/api/restore', json={'certificate': certificate})
    assert (response.status_code, response.json['run_id']) == (201, certificate['run_id'])


def test_restored_archive_reissues_same_certificate(lossy):
    client, certificate = lossy
    run_id = client.post('/api/restore', json=certificate).json['run_id']
    again = client.get('/api/export/' + run_id).json
    assert again['sha256'] == certificate['sha256']
    assert again['verification']['status'] == RULE_VERIFIED
    assert again['manifest'] == certificate['manifest']


def test_retired_rule_reissue_keeps_attachment_status(lossy, retired):
    client, certificate = lossy
    run_id = client.post('/api/restore', json=certificate).json['run_id']
    again = client.get('/api/export/' + run_id).json
    assert again['sha256'] == certificate['sha256']
    assert again['verification']['status'] == ATTACHMENT_VERIFIED
    assert client.get(f'/api/runs/{run_id}/verification').json['rule_verified'] is False
