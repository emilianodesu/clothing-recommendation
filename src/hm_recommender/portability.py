"""Private logical application backups: COPY data only, no roles or executable SQL."""
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from psycopg import sql
from hm_recommender.database import connect
from hm_recommender.data.io import atomic_json,sha256
from hm_recommender.releases.transfer import export_release,import_release,pack,unpack
from hm_recommender.pipeline.runner import LOCK

TABLES=('app_metadata','schema_migrations','serving_releases','serving_pointer','event_streams',
        'stream_catalog','stream_daily_seed','interaction_events','operational_daily','operational_profiles','pipeline_targets')
BUDGET=20*1024**3


def layout(c):
    rows=c.execute('SELECT table_name,column_name,data_type FROM information_schema.columns WHERE table_schema=current_schema() ORDER BY table_name,ordinal_position').fetchall()
    result={}
    for table,column,kind in rows:
        if table not in TABLES:raise ValueError('Unrecognized application table')
        result.setdefault(table,[]).append([column,kind])
    if not set(TABLES)-{'app_metadata','schema_migrations'}<=set(result):raise ValueError('Missing application schema')
    return result


def state(c):
    return dict(pointer=c.execute('SELECT active_id,previous_id FROM serving_pointer').fetchall(),
        streams=c.execute('SELECT stream_id,release_id,data_as_of,revision,processed_events,received_before FROM event_streams ORDER BY stream_id').fetchall(),
        targets=c.execute('SELECT stream_id,release_id,data_as_of,receipt_cursor,enabled FROM pipeline_targets ORDER BY stream_id').fetchall())


def json_value(value):return json.loads(json.dumps(value,default=str))


def backup(output:Path,registry:Path,*,connection=connect):
    output=Path(output);registry=Path(registry)
    if output.exists() or output.is_symlink():raise ValueError('Backup output must be new')
    output.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.backup-',dir=output.parent) as tmp,connection() as c:
        root=Path(tmp);os.chmod(root,0o700)
        c.execute("SET LOCAL lock_timeout='30s'");c.execute("SET LOCAL statement_timeout='30min'")
        # Acquire before table locks, same order as pipeline workers. Held to end of copy.
        c.execute('SELECT pg_advisory_xact_lock(%s)',(LOCK,))
        schema=layout(c)
        c.execute(sql.SQL('LOCK TABLE {} IN SHARE MODE').format(sql.SQL(',').join(sql.Identifier(t) for t in TABLES if t in schema)))
        # Under these locks writes wait (including manual refresh/activation); SELECT still serves.
        before=json_value(state(c));files={};counts={}
        for t in TABLES:
            if t not in schema:continue
            path=root/(t+'.csv')
            with path.open('xb') as dst,c.cursor().copy(sql.SQL('COPY {} TO STDOUT WITH (FORMAT CSV)').format(sql.Identifier(t))) as copy:
                size=0
                for block in copy:
                    size+=len(block)
                    if size>BUDGET:raise ValueError('Backup exceeds budget')
                    dst.write(block)
            counts[t]=c.execute(sql.SQL('SELECT count(*) FROM {}').format(sql.Identifier(t))).fetchone()[0]
        releases=c.execute('SELECT release_id,manifest_sha256 FROM serving_releases ORDER BY release_id').fetchall()
        if not releases or before['pointer'][0][0] is None:raise ValueError('Active release required')
        for rid,checksum in releases:
            if not re.fullmatch('h-[a-f0-9]{64}',rid) or sha256(registry/rid/'manifest.json')!=checksum:raise ValueError('Registered package differs')
            export_release(registry/rid,root/(rid+'.zip'))
        for path in root.iterdir():files[path.name]=dict(size=path.stat().st_size,sha256=sha256(path))
        if sum(f['size'] for f in files.values())>BUDGET:raise ValueError('Backup exceeds budget')
        if json_value(state(c))!=before:raise ValueError('State changed during backup')
        migrations=c.execute('SELECT name,sha256 FROM schema_migrations ORDER BY name').fetchall() if 'schema_migrations' in schema else []
        manifest=dict(protocol='hm-app-backup-v1',schema=schema,counts=counts,releases=releases,state=before,migrations=migrations,files=files)
        atomic_json(root/'backup.json',manifest)
        # Copy and archive while locks are held. Context rolls back/releases on any failure.
        return pack(root,output)


def restore(archive:Path,registry:Path,*,connection=connect):
    registry=Path(registry)
    if registry.exists() or registry.is_symlink():raise ValueError('Registry destination must be new')
    registry.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.restore-',dir=registry.parent) as tmp:
        root=Path(tmp);unpack(archive,root,max_bytes=BUDGET,max_files=512)
        p=root/'backup.json'
        if not p.is_file() or p.stat().st_size>1024*1024:raise ValueError('Missing or oversized backup manifest')
        m=json.loads(p.read_text())
        if set(m)!={'protocol','schema','counts','releases','state','migrations','files'} or m['protocol']!='hm-app-backup-v1':raise ValueError('Unsupported backup')
        if set(x.name for x in root.iterdir())!=set(m['files'])|{'backup.json'}:raise ValueError('Backup inventory differs')
        expected={t+'.csv' for t in m['schema']}
        if not set(m['schema'])<=set(TABLES):raise ValueError('Unexpected schema')
        for rid,checksum in m['releases']:
            if not re.fullmatch('h-[a-f0-9]{64}',rid) or not re.fullmatch('[a-f0-9]{64}',checksum):raise ValueError('Invalid release identity')
            expected.add(rid+'.zip')
        if set(m['files'])!=expected:raise ValueError('Unexpected backup files')
        for name,entry in m['files'].items():
            f=root/name
            if f.stat().st_size!=entry['size'] or sha256(f)!=entry['sha256']:raise ValueError('Backup integrity mismatch')
        staged=root/'registry';staged.mkdir()
        for rid,checksum in m['releases']:
            imported=import_release(root/(rid+'.zip'),staged/rid)
            if imported.release_id!=rid or sha256(staged/rid/'manifest.json')!=checksum:raise ValueError('Package differs from registration')
        with connection() as c:
            c.execute("SET LOCAL lock_timeout='30s'");c.execute("SET LOCAL statement_timeout='30min'")
            c.execute('SELECT pg_advisory_xact_lock(%s)',(LOCK,))
            if layout(c)!=m['schema']:raise ValueError('Destination schema differs')
            c.execute(sql.SQL('LOCK TABLE {} IN ACCESS EXCLUSIVE MODE').format(sql.SQL(',').join(sql.Identifier(t) for t in TABLES if t in m['schema'])))
            migrations=c.execute('SELECT name,sha256 FROM schema_migrations ORDER BY name').fetchall() if 'schema_migrations' in m['schema'] else []
            if json_value(migrations)!=m['migrations']:raise ValueError('Migration versions differ')
            for t in m['schema']:
                if t=='schema_migrations':continue
                if t=='serving_pointer':
                    if c.execute('SELECT active_id,previous_id FROM serving_pointer').fetchall()!=[(None,None)]:raise ValueError('Destination is not empty')
                elif c.execute(sql.SQL('SELECT EXISTS(SELECT 1 FROM {})').format(sql.Identifier(t))).fetchone()[0]:raise ValueError('Destination is not empty')
            c.execute('DELETE FROM serving_pointer')
            if 'schema_migrations' in m['schema']:c.execute('DELETE FROM schema_migrations')
            for t in TABLES:
                if t not in m['schema']:continue
                with (root/(t+'.csv')).open('rb') as src,c.cursor().copy(sql.SQL('COPY {} FROM STDIN WITH (FORMAT CSV)').format(sql.Identifier(t))) as copy:
                    while block:=src.read(1024*1024):copy.write(block)
                if c.execute(sql.SQL('SELECT count(*) FROM {}').format(sql.Identifier(t))).fetchone()[0]!=m['counts'][t]:raise ValueError('Restored count differs')
            if json_value(state(c))!=m['state']:raise ValueError('Restored cursor or version differs')
            # Files first; a failed DB commit leaves a diagnostic destination, never a ready service.
            if registry.exists():raise ValueError('Destination appeared during restore')
            staged.rename(registry)
        return dict(status='passed',protocol=m['protocol'],releases=len(m['releases']),tables=len(m['schema']),counts=m['counts'],state_sha256=__import__('hashlib').sha256(json.dumps(m['state'],sort_keys=True).encode()).hexdigest())
