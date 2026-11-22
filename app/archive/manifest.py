import json
from datetime import UTC, datetime
from hashlib import sha256
from time import perf_counter

from app.config import VERSION
from app.monitor.source import digests

CORE = ('manifest', 'originals', 'results')


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), default=str)


def evidence_core(packet):
    """The exact object covered by the certificate content digest.

    Kept to the three evidence keys so that certificates issued before a
    verification block existed stay independently checkable.
    """
    return {key: packet[key] for key in CORE}


def batch_key(files, rule=VERSION):
    hashes = digests(files)
    return sha256(canonical([rule, hashes]).encode()).hexdigest(), hashes


def manifest(run_id, hashes, window, name, started, rule=VERSION):
    return {'run_id': run_id, 'source': name, 'rule': rule, 'sha256': hashes,
            'created_utc': datetime.now(UTC).isoformat(timespec='seconds'),
            'elapsed_ms': round((perf_counter() - started) * 1000), 'parameters': window}
