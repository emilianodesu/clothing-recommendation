"""Durable receipts with a single serialization boundary per stream."""
from datetime import timezone
from psycopg.types.json import Jsonb
from hm_recommender.database import connect


class Conflict(ValueError):
    pass


class EventStore:
    def __init__(self, connection=connect):
        self.connection = connection

    def insert(self, event):
        body = event.model_dump(mode='json')
        body['event_time'] = event.event_time.astimezone(timezone.utc).isoformat()
        with self.connection() as conn:
            # Assign received_at AFTER acquiring the refresh lock. A late commit
            # cannot hide behind the aggregation watermark.
            row = conn.execute('SELECT release_id,mode FROM event_streams WHERE stream_id=%s FOR UPDATE',
                               (event.stream_id,)).fetchone()
            if row != (event.release_id,event.origin): raise Conflict('Stream identity differs')
            if not conn.execute('SELECT 1 FROM stream_catalog WHERE stream_id=%s AND article_id=%s',
                                (event.stream_id,event.article_id)).fetchone():
                raise ValueError('Unknown article')
            inserted = conn.execute('''INSERT INTO interaction_events
                (event_id,stream_id,customer_id,article_id,kind,event_time,payload)
                VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING event_id''',
                (event.event_id,event.stream_id,event.customer_id,event.article_id,event.kind,event.event_time,Jsonb(body))).fetchone()
            if inserted is None:
                saved = conn.execute('SELECT payload FROM interaction_events WHERE event_id=%s',(event.event_id,)).fetchone()
                if saved is None or saved[0] != body: raise Conflict('Event ID already has another payload')
            response = {'event_id':event.event_id,'status':'created' if inserted else 'duplicate'}
        return response  # Commit completed; otherwise the context raises.

    def profile(self, stream_id, customer_id):
        with self.connection() as conn:
            row = conn.execute('''SELECT s.release_id,s.data_as_of,s.revision,s.received_before,
                s.published_at,s.popularity,coalesce(p.history,'{}'::text[]),s.mode
                FROM event_streams s LEFT JOIN operational_profiles p
                ON p.stream_id=s.stream_id AND p.customer_id=%s WHERE s.stream_id=%s''',
                (customer_id,stream_id)).fetchone()
            if row is None or row[2] < 1: raise ValueError('Stream is not ready')
            return row
