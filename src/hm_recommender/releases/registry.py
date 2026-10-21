"""Durable PostgreSQL pointer and a single serving owner, with atomic snapshots."""
from contextlib import contextmanager
import os
from pathlib import Path
import re
import shutil
import tempfile
import threading

from hm_recommender.database import connect
from hm_recommender.data.io import sha256
from hm_recommender.releases.package import verify_history_release
from hm_recommender.api.recommend import load_state
from hm_recommender.releases.contracts import HistoryPublicationResult


class Unavailable(RuntimeError):
    pass


class PostgresStore:
    def __init__(self, connection):
        self.connection = connection
        self.lease = None

    def open(self):
        self.lease = self.connection()
        self.lease.autocommit = True
        try:
            locked = self.lease.execute("SELECT pg_try_advisory_lock(hashtext(current_database()), hashtext(current_schema() || ':hmrec-serving'))").fetchone()[0]
            if not locked: raise RuntimeError('Another API owns this registry')
        except BaseException:
            self.close()
            raise

    def check(self):
        if self.lease is None or self.lease.closed: raise Unavailable('Serving owner unavailable')
        self.lease.execute('SELECT 1').fetchone()

    def close(self):
        if self.lease is not None: self.lease.close()
        self.lease = None

    def register(self, manifest, checksum):
        with self.connection() as conn:
            conn.execute('INSERT INTO serving_releases(release_id,manifest_sha256,data_as_of) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING',
                (manifest.release_id,checksum,manifest.data_as_of))
            if conn.execute('SELECT manifest_sha256 FROM serving_releases WHERE release_id=%s',(manifest.release_id,)).fetchone() != (checksum,):
                raise ValueError('Registered version differs')

    def checksum(self, release_id):
        with self.connection() as conn:
            row = conn.execute('SELECT manifest_sha256 FROM serving_releases WHERE release_id=%s',(release_id,)).fetchone()
            if row is None: raise ValueError('Release not prepared')
            return row[0]

    def pointer(self):
        with self.connection() as conn:
            return conn.execute('SELECT active_id,previous_id FROM serving_pointer WHERE singleton').fetchone()

    def activate(self, release_id):
        with self.connection() as conn:
            conn.execute('UPDATE serving_pointer SET previous_id=active_id,active_id=%s,updated_at=now() WHERE singleton AND active_id IS DISTINCT FROM %s',
                         (release_id,release_id))


class ReleaseRegistry:
    def __init__(self, root: Path, connection=connect):
        self.root = Path(root)
        self.store = PostgresStore(connection)
        self._state = None
        self._lock = threading.RLock()
        self._publication = threading.Lock()

    def open(self):
        self.root.mkdir(parents=True,exist_ok=True)
        self.store.open()
        try: self.recover()
        except BaseException:
            self.close()
            raise

    def close(self):
        with self._lock:
            self._state = None
            self.store.close()

    def _directory(self, release_id):
        if not re.fullmatch(r'h-[a-f0-9]{64}',release_id): raise ValueError('Invalid release ID')
        return self.root/release_id

    def _load(self, release_id):
        directory = self._directory(release_id)
        checksum = self.store.checksum(release_id)
        if directory.is_symlink() or sha256(directory/'manifest.json') != checksum:
            raise ValueError('Registered manifest changed')
        return load_state(directory)

    def recover(self):
        with self._publication, self._lock:
            self._state = None
            self.store.check()
            active,_ = self.store.pointer()
            if active is not None: self._state = self._load(active)

    def snapshot(self):
        with self._lock:
            try: self.store.check()
            except Exception:
                self._state = None
                raise Unavailable('Serving owner unavailable') from None
            if self._state is None: raise Unavailable('No valid active release')
            return self._state

    def prepare_release(self, directory: Path):
        with self._publication:
            with self._lock: self.store.check()
            manifest = verify_history_release(directory)
            checksum = sha256(directory/'manifest.json')
            target = self._directory(manifest.release_id)
            if not target.exists():
                temporary = Path(tempfile.mkdtemp(prefix='.prepare-',dir=self.root))
                # Copy only the verified inventory; changes during copy must still verify.
                for path in directory.iterdir():
                    if path.is_symlink() or not path.is_file(): raise ValueError('Invalid source file')
                    shutil.copyfile(path,temporary/path.name)
                state = load_state(temporary)
                if state.release_id != manifest.release_id or sha256(temporary/'manifest.json') != checksum:
                    raise ValueError('Source changed during preparation')
                del state
                for path in temporary.iterdir():
                    with path.open('rb') as stream: os.fsync(stream.fileno())
                with closing_directory(temporary): pass
                temporary.rename(target)
                with closing_directory(self.root): pass
            if sha256(target/'manifest.json') != checksum: raise ValueError('Existing release differs')
            verify_history_release(target)
            self.store.register(manifest,checksum)
            return HistoryPublicationResult(release_id=manifest.release_id,catalog_size=manifest.files['catalog'].rows,
                past_buyers=manifest.files['customers'].rows,history_rows=manifest.files['histories'].rows).model_dump()

    def activate_release(self, release_id: str):
        with self._publication:
            candidate = self._load(release_id)  # Keep previous active while validating/loading.
            with self._lock:
                try:
                    self.store.check()
                    self.store.activate(release_id)
                except Exception:
                    # A lost commit reply is ambiguous; never keep serving a stale pointer.
                    self._state = None
                    raise
                self._state = candidate

    def rollback(self):
        # Select previous and activate under the same publication lock.
        with self._publication:
            _,previous = self.store.pointer()
            if previous is None: raise ValueError('No previous release')
            candidate = self._load(previous)
            with self._lock:
                try:
                    self.store.check(); self.store.activate(previous)
                except Exception:
                    self._state = None
                    raise
                self._state = candidate
            return previous


@contextmanager
def closing_directory(path):
    descriptor=os.open(path,os.O_RDONLY)
    try:
        os.fsync(descriptor)
        yield
    finally: os.close(descriptor)
