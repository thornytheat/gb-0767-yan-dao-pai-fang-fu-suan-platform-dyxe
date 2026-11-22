"""Rule-version registry for archived evidence.

A certificate records the rule version that produced it.  Restoring a
certificate must never re-run it under the current rule: once a rule is
retired its conclusions are frozen.  Only versions with a registered engine
here can be replayed, and the replay always uses that version's engine.
"""

from app.config import VERSION

NATIVE = 'NATIVE'
RULE_VERIFIED = 'RULE_VERIFIED'
ATTACHMENT_VERIFIED = 'ATTACHMENT_VERIFIED'


def _current(files):
    # Imported lazily so importing the registry never pulls in the calculator.
    from app.monitor.inspection import Inspection
    audit = Inspection(files)
    return vars(audit.window), audit.results()


# version -> engine(files) -> (window_parameters, results).  A retired version
# is removed from this mapping; its evidence is no longer recomputable.
ENGINES = {VERSION: _current}


def available(version):
    return isinstance(version, str) and version in ENGINES


def recompute(files, version):
    """Return ``(window_parameters, results)`` for a registered rule version."""
    engine = ENGINES.get(version) if isinstance(version, str) else None
    return None if engine is None else engine(files)


def native_verification(rule=VERSION):
    return {'status': NATIVE, 'rule': rule, 'rule_available': True,
            'attachment_verified': True, 'rule_verified': True,
            'checks': {'computed_at_archive': True}, 'restored': False}


def verification_for(packet):
    value = packet.get('verification')
    if isinstance(value, dict) and value.get('status'):
        return value
    return native_verification(packet.get('manifest', {}).get('rule', VERSION))
