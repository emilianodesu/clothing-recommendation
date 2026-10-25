"""Read-only, bounded selection of purchase histories for an illustrative UI.

These are descriptions of catalogue purchases, never inferred demographics.
The selector neither trains nor changes H, its data, or its recommendations.
"""
from collections import Counter, OrderedDict
import csv
import io
from pathlib import Path
import threading
from uuid import uuid4
import zipfile
import zlib

from hm_recommender.events.store import Conflict

SAMPLE_LIMIT = 25_000
HISTORY_LIMIT = 12
DARK = {'Black', 'Dark Blue', 'Dark Grey', 'Grey', 'Dark Green', 'Dark Red', 'Dark Brown', 'Dark Purple'}
LIGHT = {'White', 'Off White', 'Light Beige', 'Beige', 'Light Grey', 'Light Blue', 'Light Pink', 'Light Yellow'}
CLOTHES = {'T-shirt', 'Top', 'Vest top', 'Sweater', 'Cardigan', 'Jacket', 'Blazer', 'Coat',
           'Dress', 'Skirt', 'Trousers', 'Shorts', 'Leggings/Tights', 'Shirt', 'Blouse', 'Hoodie', 'Garment Set'}
PATTERNS = {'Stripe', 'All over pattern', 'Dot', 'Check', 'Front print', 'Placement print',
            'Mixed solid/pattern', 'Other pattern', 'Jacquard', 'Argyle'}
RULES = (
    ('mens-dark', 'Moda masculina · oscuros', 'moda masculina en tonos oscuros'),
    ('ladies-light', 'Moda femenina · claros', 'moda femenina en tonos claros'),
    ('dark', 'Negro, gris y tonos oscuros', 'tonos oscuros'),
    ('light', 'Blancos y tonos claros', 'tonos claros'),
    ('patterns', 'Estampados y rayas', 'estampados o rayas'),
    ('dresses', 'Vestidos y faldas', 'vestidos o faldas'),
    ('layers', 'Capas y abrigo', 'suéteres, cárdigans, chaquetas o abrigos'),
    ('one-colour', 'Un color, varias prendas', 'un mismo color en al menos tres tipos de prenda'),
    ('varied', 'Variedad sin un favorito', 'variedad de prendas y colores, sin una mayoría dominante'),
)


def article_traits(base, archive=None):
    """Use the served catalogue; optionally enrich gender-category/print from CSV.

    Missing/invalid photo archives only disable criteria that need those fields.
    No transaction or customer CSV is read, and no archive is extracted.
    """
    traits = {item: {'article_id': item} for item in base.catalog}
    for field, groups in base.filter_index.items():
        for value, members in groups.items():
            for item in members:
                if item in traits: traits[item][field] = value
    extra = {}
    try:
        if archive is None: return traits
        with zipfile.ZipFile(archive) as z:
            entries = [info for info in z.infolist() if info.filename == 'articles.csv']
            if len(entries) != 1 or entries[0].file_size > 64*1024**2 or entries[0].flag_bits & 1:
                return traits
            with z.open(entries[0]) as raw:
                reader = csv.DictReader(io.TextIOWrapper(raw, encoding='utf-8-sig'))
                fields = {'article_id', 'index_group_name', 'graphical_appearance_name'}
                if not fields.issubset(reader.fieldnames or []): return traits
                for number, row in enumerate(reader, 1):
                    if number > 200_000: return traits
                    item = row['article_id']
                    if item in traits:
                        if item in extra: return traits
                        extra[item] = {field: row[field] for field in fields-{'article_id'}}
        for item, values in extra.items(): traits[item].update(values)
    except (OSError, ValueError, KeyError, UnicodeError, csv.Error, zipfile.BadZipFile, NotImplementedError, zlib.error):
        pass
    return traits


def select_profiles(rows, traits, allowed_customers):
    """Keep at most nine candidates per rule, independent of input ordering."""
    pools = {key: [] for key, _, _ in RULES}
    for customer, history in rows:
        if customer not in allowed_customers: continue
        ids = list(dict.fromkeys(history))[:HISTORY_LIMIT]
        if not 4 <= len(ids) <= HISTORY_LIMIT: continue
        articles = [traits.get(item, {}) for item in ids]
        n = len(articles)
        clothing = [a.get('product_type_name') in CLOTHES for a in articles]
        dark = [ok and a.get('colour_group_name') in DARK for ok, a in zip(clothing, articles)]
        light = [ok and a.get('colour_group_name') in LIGHT for ok, a in zip(clothing, articles)]
        matches = {
            'mens-dark': sum(ok and a.get('index_group_name') == 'Menswear' for ok, a in zip(dark, articles)),
            'ladies-light': sum(ok and a.get('index_group_name') == 'Ladieswear' for ok, a in zip(light, articles)),
            'dark': sum(dark), 'light': sum(light),
            'patterns': sum(ok and a.get('graphical_appearance_name') in PATTERNS for ok, a in zip(clothing, articles)),
            'dresses': sum(a.get('product_type_name') in {'Dress', 'Skirt'} for a in articles),
            'layers': sum(a.get('product_type_name') in {'Sweater', 'Cardigan', 'Jacket', 'Blazer', 'Coat', 'Hoodie'} for a in articles),
        }
        colours = Counter(a.get('colour_group_name') for a in articles if a.get('colour_group_name') not in (None, '', 'Unknown'))
        kinds = Counter(a.get('product_type_name') for a in articles if a.get('product_type_name') in CLOTHES)
        main_colour = max(colours.values(), default=0)
        main_kind = max(kinds.values(), default=0)
        dominant_colour = colours.most_common(1)[0][0] if colours else None
        dominant_types = {a.get('product_type_name') for a in articles
                          if a.get('colour_group_name') == dominant_colour and a.get('product_type_name') in CLOTHES}
        if n >= 5 and len(dominant_types) >= 3 and sum(clothing)/n >= .8 and main_colour/n >= .8:
            matches['one-colour'] = main_colour
        if (n >= 6 and len(kinds) >= 4 and len(colours) >= 3 and sum(colours.values()) == n
                and sum(clothing)/n >= .8 and max(main_colour, main_kind)/n <= .5):
            matches['varied'] = n
        for key, matched in matches.items():
            if matched/n < .75: continue
            score = matched/n if key != 'varied' else 1-max(main_colour, main_kind)/n
            candidate = (score, min(n, 8), customer, ids, matched)
            pool = pools[key]
            pool.append(candidate)
            pool.sort(key=lambda p: (-p[0], -p[1], p[2]))
            del pool[len(RULES):]
    result, used = [], set()
    for key, name, description in RULES:
        for _, _, customer, history, matched in pools[key]:
            if customer in used: continue
            result.append(dict(key=key, customer_id=customer, label=name,
                description=f'{matched} de {len(history)} prendas de referencia: {description}.',
                history_count=len(history), matched_count=matched,
                examples=[traits[item] for item in history[:3] if item in traits]))
            used.add(customer)
            break
    return result


class DemoProfiles:
    """One catalogue in memory and four small, revision-bound result caches."""
    def __init__(self, connection, archive=None):
        self.connection = connection
        self.archive = Path(archive) if archive else None
        self.lock = threading.Lock()
        self.traits_key = None
        self.traits = {}
        self.cache = OrderedDict()

    def get(self, base, stream_id):
        try:
            stat = self.archive.stat() if self.archive else None
            signature = (stat.st_size, stat.st_mtime_ns) if stat else None
        except OSError:
            signature = None
        with self.lock, self.connection() as conn:
            # The profile rows and revision must describe the same publication.
            conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
            conn.execute("SET LOCAL statement_timeout = '20s'")
            row = conn.execute('SELECT release_id,revision,data_as_of,mode FROM event_streams WHERE stream_id=%s', (stream_id,)).fetchone()
            if row is None or row[1] < 1: raise ValueError('Stream unavailable')
            if row[0] != base.release_id: raise Conflict('Stream belongs to another release')
            key = (base.release_id, stream_id, row[1], signature)
            if key in self.cache:
                self.cache.move_to_end(key)
                return self.cache[key]
            traits_key = (base.release_id, signature)
            if traits_key != self.traits_key:
                self.traits = article_traits(base, self.archive)
                self.traits_key = traits_key
            with conn.cursor(name='demo_profiles_'+uuid4().hex) as cursor:
                cursor.itersize = 512
                cursor.execute('''SELECT customer_id,history[1:12] FROM operational_profiles
                    WHERE stream_id=%s AND cardinality(history)>=4
                    ORDER BY customer_id LIMIT %s''', (stream_id, SAMPLE_LIMIT))
                items = select_profiles(cursor, self.traits, base.customers)
            result = dict(release_id=base.release_id, stream_id=stream_id, revision=row[1],
                          data_as_of=row[2].isoformat(), mode=row[3], items=items)
            self.cache[key] = result
            while len(self.cache) > 4: self.cache.popitem(last=False)
            return result
