"""Recover an archive from an already issued evidence certificate.

Recovery answers two different questions separately:

* ``attachment_verified`` — do the certificate's own contents hang together?
  The original file digests, the manifest, the certificate content digest, the
  minute balance and the stored conclusions must agree with each other.
* ``rule_verified`` — can the recorded rule still be replayed, and does the
  replay reproduce the stored conclusions exactly?

A retired rule is never replaced by the current one.  Such a certificate is
recovered as attachment-verified only, so the conclusions issued at the time
are preserved instead of being silently rewritten under today's rule.
"""

from hashlib import sha256

from app.archive.manifest import batch_key, canonical, evidence_core
from app.config import FILES, SOURCE_LIMIT
from app.errors import InputError
from app.monitor.inspection import STAGES
from app.monitor.source import digests
from app.rules import (ATTACHMENT_VERIFIED, RULE_VERIFIED,
                       available as rule_available, recompute, verification_for)

HEX = frozenset('0123456789abcdef')
TOTAL_KEYS = ('scheduled', 'valid', 'exempt', 'missing')


def _bad(detail, code='BAD_CERTIFICATE', status=422):
    raise InputError(code, detail, status)


def _hex(value):
    return isinstance(value, str) and len(value) == 64 and all(char in HEX for char in value)


def _structure(certificate):
    if not isinstance(certificate, dict):
        _bad('证据单必须是 JSON 对象')
    manifest = certificate.get('manifest')
    originals = certificate.get('originals')
    results = certificate.get('results')
    if not isinstance(manifest, dict) or not isinstance(originals, dict) or not isinstance(results, dict):
        _bad('证据单缺少 manifest、originals 或 results')
    run_id = manifest.get('run_id')
    if not _hex(run_id):
        _bad('证据单归档编号无效')
    if certificate.get('run_id') not in (None, run_id):
        _bad('证据单归档编号与清单不一致', 'EVIDENCE_MISMATCH', 409)
    rule = manifest.get('rule')
    if not isinstance(rule, str) or not rule.strip() or len(rule) > 40:
        _bad('证据单规则版本无效')
    hashes = manifest.get('sha256')
    if not isinstance(hashes, dict) or any(not _hex(hashes.get(key)) for key in FILES):
        _bad('证据单缺少原件摘要')
    if set(originals) != set(FILES):
        _bad('证据单原件不完整')
    for value in originals.values():
        if not isinstance(value, str) or len(value) > SOURCE_LIMIT:
            _bad('证据单原件格式或长度无效')
        try:
            value.encode('utf-8')
        except UnicodeEncodeError:
            _bad('证据单原件含无效 Unicode')
    if not isinstance(manifest.get('parameters'), dict):
        _bad('证据单缺少监测参数')
    if any(stage not in results or not isinstance(results[stage], dict) for stage in STAGES):
        _bad('证据单缺少复算工序')
    return manifest, originals, results, run_id, rule, hashes


def _balanced(results):
    """Internal consistency of the frozen conclusions, independent of rules."""
    hours = results['hours']
    rows, totals = hours.get('rows'), hours.get('totals')
    if not isinstance(rows, list) or not isinstance(totals, dict):
        _bad('证据单小时结论不完整', 'EVIDENCE_INCONSISTENT', 409)
    if any(type(totals.get(key)) is not int for key in TOTAL_KEYS):
        _bad('证据单小时勾稽不完整', 'EVIDENCE_INCONSISTENT', 409)
    if totals['scheduled'] - totals['valid'] - totals['exempt'] - totals['missing']:
        _bad('分钟勾稽尚未平衡', 'EVIDENCE_UNBALANCED', 409)
    try:
        for key in TOTAL_KEYS:
            if sum(row[key] for row in rows) != totals[key]:
                _bad('证据单小时结论与合计不符', 'EVIDENCE_INCONSISTENT', 409)
        if any(row['scheduled'] != row['valid'] + row['exempt'] + row['missing'] for row in rows):
            _bad('证据单逐小时分钟不守恒', 'EVIDENCE_INCONSISTENT', 409)
        if len(results['validity'].get('rows', [])) != totals['scheduled']:
            _bad('证据单分钟裁决条数与应测不符', 'EVIDENCE_INCONSISTENT', 409)
        reports = results['reports']
        if len(reports.get('rows', [])) != len(rows):
            _bad('证据单报送对账条数与小时不符', 'EVIDENCE_INCONSISTENT', 409)
        if sum(row['code'] == 'DEVIATION' for row in reports['rows']) != reports.get('deviations'):
            _bad('证据单报送超差数与明细不符', 'EVIDENCE_INCONSISTENT', 409)
    except (KeyError, TypeError):
        _bad('证据单结论结构无法核对', 'EVIDENCE_INCONSISTENT', 409)
    return totals


def decode_certificate(payload):
    """Decode a certificate posted directly, wrapped, or as a JSON string."""
    if isinstance(payload, (str, bytes)):
        import json
        try:
            payload = json.loads(payload)
        except (json.JSONDecodeError, RecursionError, UnicodeDecodeError):
            _bad('证据单不是有效 JSON')
    if isinstance(payload, dict) and 'manifest' not in payload:
        for key in ('certificate', 'evidence', 'cert', 'data'):
            if key in payload:
                return decode_certificate(payload[key])
    return payload


def verify_certificate(certificate):
    """Verify a certificate without writing anything.

    Anything that fails here is rejected before it can claim an archive number.
    """
    certificate = decode_certificate(certificate)
    manifest, originals, results, run_id, rule, hashes = _structure(certificate)

    actual = digests(originals)
    if any(hashes.get(key) != actual[key] for key in FILES):
        _bad('原件摘要与清单不符', 'EVIDENCE_MISMATCH', 409)

    outer = certificate.get('sha256')
    if not _hex(outer):
        _bad('证据单缺少内容摘要')
    if outer != sha256(canonical(evidence_core(certificate)).encode()).hexdigest():
        _bad('证据单内容摘要不符', 'EVIDENCE_MISMATCH', 409)

    expected, _ = batch_key(originals, rule)
    if expected != run_id:
        _bad('证据单归档编号与原件内容不符', 'RUN_ID_MISMATCH', 409)

    totals = _balanced(results)
    claimed = certificate.get('minute_balance')
    if claimed is not None and claimed != 0:
        _bad('证据单勾稽差额与复算不符', 'EVIDENCE_UNBALANCED', 409)

    checks = {'originals_sha256': True, 'certificate_sha256': True, 'minute_balance': True,
              'run_id': True, 'conclusions_balanced': True,
              'rule_available': rule_available(rule), 'rule_recompute': None, 'parameters': None}
    status = ATTACHMENT_VERIFIED
    if checks['rule_available']:
        replay = recompute(originals, rule)
        if replay is None:
            # The registry changed between the availability probe and replay.
            checks['rule_available'] = False
        else:
            parameters, replayed = replay
            if canonical(parameters) != canonical(manifest['parameters']):
                _bad('按原规则复算的监测参数与证据单不符', 'RULE_MISMATCH', 409)
            checks['parameters'] = True
            if canonical(replayed) != canonical(results):
                _bad('按原规则复算结果与证据单不符', 'RULE_MISMATCH', 409)
            checks['rule_recompute'] = True
            status = RULE_VERIFIED
    return {'status': status, 'rule': rule, 'rule_available': checks['rule_available'],
            'attachment_verified': True, 'rule_verified': status == RULE_VERIFIED,
            'recomputed': status == RULE_VERIFIED, 'checks': checks, 'restored': True}, totals


def restore_certificate(store, payload):
    certificate = decode_certificate(payload)
    verification, totals = verify_certificate(certificate)
    manifest = certificate['manifest']
    packet = {'manifest': manifest, 'originals': certificate['originals'],
              'results': certificate['results'], 'verification': verification}
    stored, reused = store.restore(manifest['run_id'], packet)
    return stored, reused, verification_for(stored), totals


def restore(store, payload):
    return restore_certificate(store, payload)


def verify(payload):
    return verify_certificate(payload)[0]
