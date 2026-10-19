"""ZIP structure and source identity. Never extract arbitrary archive members."""
import csv
import io
from pathlib import Path, PurePosixPath
import stat
from zipfile import ZipFile

from hm_recommender.data.io import sha256

COMPETITION = 'h-and-m-personalized-fashion-recommendations'
HM_SOURCE = {
    'bytes': 30810293747,
    'sha256': 'e4695ac063fd621a29ba83523aa97be4041a91c45fba331162784f55771fbe9c',
    'files': 105104,
    'rows': {'articles': 105542, 'customers': 1371980, 'sample_submission': 1371980, 'transactions': 31788324},
}
SCHEMAS = {
    'articles.csv': 'article_id,product_code,prod_name,product_type_no,product_type_name,product_group_name,graphical_appearance_no,graphical_appearance_name,colour_group_code,colour_group_name,perceived_colour_value_id,perceived_colour_value_name,perceived_colour_master_id,perceived_colour_master_name,department_no,department_name,index_code,index_name,index_group_no,index_group_name,section_no,section_name,garment_group_no,garment_group_name,detail_desc'.split(','),
    'customers.csv': 'customer_id,FN,Active,club_member_status,fashion_news_frequency,age,postal_code'.split(','),
    'sample_submission.csv': ['customer_id', 'prediction'],
    'transactions_train.csv': ['t_dat', 'customer_id', 'article_id', 'price', 'sales_channel_id'],
}


def validate_source(source_zip: Path, mode='full', expected=HM_SOURCE):
    if mode not in ('quick', 'full'):
        raise ValueError('Modo inválido')
    size = source_zip.stat().st_size
    if expected is not None and size != expected['bytes']:
        raise ValueError('ZIP incompleto o fuente diferente: tamaño inesperado')
    with ZipFile(source_zip) as bundle:
        entries = bundle.infolist()
        names = [e.filename for e in entries]
        if len(names) != len(set(names)) or not set(SCHEMAS).issubset(names):
            raise ValueError('Inventario duplicado o faltan tablas')
        if expected is not None and len(entries) != expected['files']:
            raise ValueError('Inventario incompleto')
        for entry in entries:
            path = PurePosixPath(entry.filename)
            kind = stat.S_IFMT(entry.external_attr >> 16)
            if (path.is_absolute() or '..' in path.parts or '\\' in entry.filename or ':' in entry.filename
                    or kind not in (0, stat.S_IFREG, stat.S_IFDIR) or entry.flag_bits & 1):
                raise ValueError('Ruta o tipo de archivo inseguro en ZIP')
            if not entry.is_dir() and entry.filename not in SCHEMAS and not entry.filename.startswith('images/'):
                raise ValueError('Archivo inesperado en ZIP')
        for name, columns in SCHEMAS.items():
            with bundle.open(name) as stream:
                if next(csv.reader(io.TextIOWrapper(stream, encoding='utf-8'))) != columns:
                    raise ValueError('Esquema inesperado: ' + name)
    digest = sha256(source_zip) if mode == 'full' else None
    if expected is not None and digest is not None and digest != expected['sha256']:
        raise ValueError('SHA-256 de la fuente no coincide')
    return {'status': 'passed', 'mode': mode, 'bytes': size, 'files': len(entries),
            'source_sha256': digest, 'identity_verified': expected is not None and digest is not None,
            'image_decode': 'not_repeated', 'csv_contents': 'validated_during_ingest'}
