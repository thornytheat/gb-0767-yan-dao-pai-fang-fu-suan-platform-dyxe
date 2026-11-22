import json
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path

from app.archive.manifest import canonical
from app.errors import InputError


class Archive:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.execute('CREATE TABLE IF NOT EXISTS monitoring (id TEXT PRIMARY KEY, packet TEXT NOT NULL)')

    @contextmanager
    def connection(self):
        with closing(sqlite3.connect(self.path, timeout=15)) as db, db:
            yield db

    def lookup(self, run_id):
        with self.connection() as db:
            row = db.execute('SELECT packet FROM monitoring WHERE id = ?', (run_id,)).fetchone()
        return None if row is None else json.loads(row[0])

    def require(self, run_id):
        packet = self.lookup(run_id)
        if packet is None:
            raise InputError('RUN_UNKNOWN', '找不到监测留档', 404)
        return packet

    def commit(self, run_id, packet):
        with self.connection() as db:
            cursor = db.execute('INSERT INTO monitoring VALUES (?, ?) ON CONFLICT(id) DO NOTHING',
                                (run_id, canonical(packet)))
            reused = cursor.rowcount == 0
            record = db.execute('SELECT packet FROM monitoring WHERE id = ?', (run_id,)).fetchone()[0]
        return json.loads(record), reused

    def restore(self, run_id, packet):
        """Insert a recovered packet under its original identifier.

        The insert is a single conflict-tolerant statement, so two shifts
        submitting the same certificate at once still create one record.  A
        failed verification never reaches this method, leaving the identifier
        free for the genuine certificate.
        """
        with self.connection() as db:
            cursor = db.execute('INSERT INTO monitoring VALUES (?, ?) ON CONFLICT(id) DO NOTHING',
                                (run_id, canonical(packet)))
            inserted = cursor.rowcount == 1
            record = db.execute('SELECT packet FROM monitoring WHERE id = ?', (run_id,)).fetchone()[0]
        stored = json.loads(record)
        if not inserted:
            if stored.get('originals') != packet['originals'] or stored.get('results') != packet['results']:
                raise InputError('RUN_CONFLICT', '归档编号已被不同内容占用', 409)
            if not stored.get('verification') and packet.get('verification'):
                stored['verification'] = packet['verification']
                with self.connection() as db:
                    db.execute('UPDATE monitoring SET packet = ? WHERE id = ?', (canonical(stored), run_id))
        return stored, not inserted

    def history(self):
        with self.connection() as db:
            rows = db.execute(
                "SELECT json_extract(packet, '$.manifest'), json_extract(packet, '$.verification.status') "
                "FROM monitoring ORDER BY rowid DESC LIMIT 100").fetchall()
        history = []
        for manifest, status in rows:
            if isinstance(manifest, str):
                manifest = json.loads(manifest)
            # Packets written before verification existed were computed at
            # archive time under their own rule version.
            history.append(manifest | {'verification': status or 'NATIVE'})
        return history
