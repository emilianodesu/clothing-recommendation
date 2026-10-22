"""Past-only seed and transactional publication of operational H profiles."""
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID
import polars as pl
from hm_recommender.database import connect
from hm_recommender.data.ingest import load_dataset, scan
from hm_recommender.data.io import sha256
from hm_recommender.data.validate import HM_SOURCE
from hm_recommender.releases.package import verify_history_release


def stream_identity(value):
    canonical=str(UUID(value))
    if value != canonical: raise ValueError('Canonical stream ID required')
    return canonical


def initialize_stream(dataset_dir: Path, release_directory: Path, stream_id: str, *, mode: str, connection=connect):
    stream_identity(stream_id)
    dataset_dir=Path(dataset_dir); release_directory=Path(release_directory)
    manifest=verify_history_release(release_directory)
    dataset=load_dataset(dataset_dir)
    if (sha256(dataset_dir/'manifest.json')!=manifest.dataset_manifest_sha256
        or dataset.source_sha256!=manifest.source_sha256): raise ValueError('Dataset differs from frozen package')
    expected='simulation' if dataset.source_sha256==HM_SOURCE['sha256'] else 'synthetic'
    if mode!=expected: raise ValueError('Stream origin must match source')
    daily=(scan(dataset).filter((pl.col('t_dat')>=manifest.data_as_of-timedelta(days=28)) &
             (pl.col('t_dat')<manifest.data_as_of))
           .group_by('customer_id','article_id','t_dat').agg(pl.len().alias('frequency')).collect(engine='streaming'))
    catalog=pl.read_parquet(release_directory/'catalog.parquet')['article_id']
    with connection() as conn:
        conn.execute('INSERT INTO event_streams(stream_id,release_id,mode,dataset_sha256,seed_cutoff,data_as_of) VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',
            (stream_id,manifest.release_id,mode,manifest.dataset_manifest_sha256,manifest.data_as_of,manifest.data_as_of))
        saved=conn.execute('SELECT release_id,mode,dataset_sha256,revision FROM event_streams WHERE stream_id=%s FOR UPDATE',(stream_id,)).fetchone()
        if saved[:3]!=(manifest.release_id,mode,manifest.dataset_manifest_sha256): raise ValueError('Stream already belongs to another source')
        if saved[3]==0:
            if not conn.execute('SELECT 1 FROM stream_catalog WHERE stream_id=%s LIMIT 1',(stream_id,)).fetchone():
                with conn.cursor().copy('COPY stream_catalog(stream_id,article_id) FROM STDIN') as copy:
                    for item in catalog: copy.write_row((stream_id,item))
                with conn.cursor().copy('COPY stream_daily_seed(stream_id,customer_id,article_id,day,frequency) FROM STDIN') as copy:
                    for row in daily.iter_rows(): copy.write_row((stream_id,*row))
    del daily
    return aggregate_events(manifest.release_id,datetime.now(timezone.utc),stream_id=stream_id,connection=connection)


def metadata(conn, stream_id):
    row=conn.execute('SELECT release_id,mode,data_as_of,revision,received_before,published_at,processed_events FROM event_streams WHERE stream_id=%s',(stream_id,)).fetchone()
    return dict(stream_id=stream_id,release_id=row[0],mode=row[1],data_as_of=row[2].isoformat(),revision=row[3],
        received_before=row[4].isoformat(),published_at=row[5].isoformat(),processed_events=row[6])


def aggregate_events(release_id: str, received_before: datetime, *, stream_id: str, data_as_of: date | None=None, connection=connect) -> dict:
    stream_identity(stream_id)
    if not isinstance(received_before,datetime) or received_before.tzinfo is None: raise ValueError('Aware receipt watermark required')
    with connection() as conn:
        row=conn.execute('SELECT release_id,data_as_of,revision,received_before,processed_events FROM event_streams WHERE stream_id=%s FOR UPDATE',(stream_id,)).fetchone()
        if row is None or row[0]!=release_id: raise ValueError('Unknown stream/version')
        cutoff=data_as_of or row[1]
        if type(cutoff) is not date or cutoff<row[1] or received_before<row[3]: raise ValueError('Clock/watermark cannot move backwards')
        if received_before>conn.execute('SELECT clock_timestamp()').fetchone()[0]: raise ValueError('Receipt watermark cannot be in the future')
        count=conn.execute('SELECT count(*) FROM interaction_events WHERE stream_id=%s AND received_at<%s',(stream_id,received_before)).fetchone()[0]
        if row[2]>0 and cutoff==row[1] and count==row[4]: return metadata(conn,stream_id)
        conn.execute('DELETE FROM operational_daily WHERE stream_id=%s',(stream_id,))
        conn.execute('''INSERT INTO operational_daily
            SELECT %s,customer_id,article_id,day,sum(frequency) FROM (
              SELECT customer_id,article_id,day,frequency FROM stream_daily_seed WHERE stream_id=%s
              UNION ALL SELECT customer_id,article_id,(event_time AT TIME ZONE 'UTC')::date,1
                FROM interaction_events WHERE stream_id=%s AND received_at<%s AND kind='purchase'
            ) inputs WHERE day >= %s AND day < %s GROUP BY customer_id,article_id,day''',
            (stream_id,stream_id,stream_id,received_before,cutoff-timedelta(days=28),cutoff))
        conn.execute('DELETE FROM operational_profiles WHERE stream_id=%s',(stream_id,))
        conn.execute('''INSERT INTO operational_profiles
            SELECT %s,customer_id,array_agg(article_id ORDER BY last_day DESC,frequency DESC,article_id COLLATE "C")
            FROM (SELECT customer_id,article_id,max(day) last_day,sum(frequency) frequency
              FROM operational_daily WHERE stream_id=%s GROUP BY customer_id,article_id) history GROUP BY customer_id''',(stream_id,stream_id))
        popularity=conn.execute('''SELECT array_agg(c.article_id ORDER BY coalesce(p.frequency,0) DESC,c.article_id COLLATE "C")
            FROM stream_catalog c LEFT JOIN (
              SELECT article_id,sum(frequency) frequency FROM operational_daily
              WHERE stream_id=%s AND day >= %s GROUP BY article_id
            ) p USING(article_id) WHERE c.stream_id=%s''',(stream_id,cutoff-timedelta(days=7),stream_id)).fetchone()[0]
        conn.execute('''UPDATE event_streams SET data_as_of=%s,revision=revision+1,received_before=%s,
            processed_events=%s,published_at=clock_timestamp(),popularity=%s WHERE stream_id=%s''',
            (cutoff,received_before,count,popularity,stream_id))
        result=metadata(conn,stream_id)
    return result
