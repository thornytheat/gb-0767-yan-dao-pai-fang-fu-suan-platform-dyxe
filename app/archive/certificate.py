from hashlib import sha256

from app.archive.manifest import canonical, evidence_core
from app.monitor.source import digests
from app.errors import InputError
from app.rules import verification_for


def issue(packet):
    if digests(packet['originals']) != packet['manifest']['sha256']:
        raise InputError('EVIDENCE_MISMATCH', '原件摘要与留档不符', 409)
    totals = packet['results']['hours']['totals']
    delta = totals['scheduled'] - totals['valid'] - totals['exempt'] - totals['missing']
    if delta:
        raise InputError('EVIDENCE_UNBALANCED', '分钟勾稽尚未平衡', 409)
    claims = {'run_id': packet['manifest']['run_id'], 'certificate': '监测证据单',
              'minute_balance': delta, 'sha256': sha256(canonical(evidence_core(packet)).encode()).hexdigest(),
              'verification': verification_for(packet)}
    return claims | packet
