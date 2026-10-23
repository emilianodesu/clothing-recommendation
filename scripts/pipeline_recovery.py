"""One audited PostgreSQL generation transition; no generic restart bypass."""
import hashlib
import json

IDENTITY=('container_id','started_at','restart_count','image_id')
_COMMON=dict(container_id='c3ba8850be4b2302d6ed00c8a0fa92feec78dd03911a2510ef0a56286af97a64',restart_count=0,image_id='sha256:3645570cccdfa447589da9f57dd740faa29b30938e861289a5574b6ca6b03826')
STREAM='2c9ada3d-8a02-407c-b386-b5ba84c08430'
APPROVED=dict(protocol='postgres-recovery-v1',source_run_id='events-mac24-20261023T030009Z-3f97f2a0',
    source_archive_sha256='f302f73d3a35772e0fb0dd44f784607ccff95737e185f63bffac0e0baa1ee7aa',
    previous_identity=dict(_COMMON,started_at='2026-10-20T00:46:17.675247553Z'),
    current_identity=dict(_COMMON,started_at='2026-10-23T19:15:26.34851292Z'),
    aggregates=dict(stream_id=STREAM,release_id='h-d12e30cb4d8c44cc22468e86c634db566e6f126ddc704e36723fe09d9a8a7b0a',
        data_as_of='2020-09-09',revision=3,processed_events=4,daily_rows=972591,purchases=1069693,first_day='2020-08-12',last_day='2020-09-08',stored_events=4),
    cross_generation_counters_comparable=False,assurance='identity_and_aggregate_continuity_only')


def validate_proof(proof):
    # Canonical JSON comparison distinguishes booleans from integers as well as extra keys.
    if json.dumps(proof,sort_keys=True)!=json.dumps(APPROVED,sort_keys=True):
        raise ValueError('Unapproved PostgreSQL recovery evidence; preserve state and inspect')


def approve(previous,current,aggregates,source_run,archive_sha):
    proof=dict(APPROVED,previous_identity={k:previous[k] for k in IDENTITY},current_identity={k:current[k] for k in IDENTITY},
               aggregates=aggregates,source_run_id=source_run,source_archive_sha256=archive_sha)
    validate_proof(proof)
    return proof


# Executed using the installed app's driver. No private rows or credentials are printed.
QUERY_PROGRAM='''import json,sys
from hm_recommender.database import connect
stream=sys.argv[1]
with connect() as c:
    c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
    c.execute("SET LOCAL statement_timeout = '30s'")
    row=c.execute("SELECT release_id,data_as_of,revision,processed_events FROM event_streams WHERE stream_id=%s",(stream,)).fetchone()
    if row is None:raise ValueError('Missing recovery stream')
    seed=c.execute("SELECT count(*),sum(frequency),min(day),max(day) FROM stream_daily_seed WHERE stream_id=%s",(stream,)).fetchone()
    events=c.execute("SELECT count(*) FROM interaction_events WHERE stream_id=%s",(stream,)).fetchone()[0]
    print(json.dumps(dict(stream_id=stream,release_id=row[0],data_as_of=str(row[1]),revision=row[2],processed_events=row[3],daily_rows=seed[0],purchases=int(seed[1]) if seed[1] is not None else None,first_day=str(seed[2]),last_day=str(seed[3]),stored_events=events)))
'''


def check(root,values,source,archive,previous,current,*,execute=None):
    if all(previous[k]==current[k] for k in IDENTITY):return None
    if execute is None:
        from project import compose
        execute=compose
    archive_sha=hashlib.sha256(archive.read_bytes()).hexdigest()
    # Reject other generations/sources before reading any data.
    approve(previous,current,APPROVED['aggregates'],source['run_id'],archive_sha)
    if source['status']!='passed' or source['result']['protocol']!='events-operational-v1' or source['result']['stream_id']!=STREAM:
        raise ValueError('Recovery source is not the approved T5 stream')
    raw=execute(root,values,'exec','-T','api','/opt/hmrec/.venv/bin/python','-c',QUERY_PROGRAM,STREAM,capture=True,timeout=100)
    if len(raw)>4096:raise ValueError('Oversize recovery response')
    return approve(previous,current,json.loads(raw),source['run_id'],archive_sha)
