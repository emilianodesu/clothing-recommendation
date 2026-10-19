"""Small public synthetic data, including temporal boundary cases."""
import csv
import io
from pathlib import Path
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED

from hm_recommender.data.validate import SCHEMAS


def make_zip(path: Path, future=False):
    rows = {
        'articles.csv': [dict(article_id='00' + a, product_type_name=t, product_group_name='Garment', colour_group_name='Blue') for a, t in [('a','Top'),('b','Trousers'),('c','Novel')]],
        'customers.csv': [dict(customer_id='user-' + u) for u in ('a', 'b')],
        'sample_submission.csv': [dict(customer_id='user-' + u, prediction='00a 00b 00c') for u in ('a', 'b')],
        'transactions_train.csv': [dict(zip(SCHEMAS['transactions_train.csv'], row)) for row in [
            ('2020-08-31','user-a','00a','0.01','1'),
            ('2020-09-01','user-b','00b','0.02','2'),
            ('2020-09-08','user-a','00a','0.025','2'),
            ('2020-09-08','user-a','00a','0.025','2'),
            ('2020-09-09','user-a','00c','0.04','1'),
            ('2020-09-15','user-b','00a','0.03','2'),
            ('2020-09-16','user-a','00b','0.01','2'),
        ]],
    }
    if future:
        rows['transactions_train.csv'].append(dict(t_dat='2020-09-22', customer_id='user-a', article_id='00c', price='0.99', sales_channel_id='1'))
    path.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(path, 'w') as bundle:
        for name, records in rows.items():
            stream = io.StringIO()
            writer = csv.DictWriter(stream, fieldnames=SCHEMAS[name])
            writer.writeheader()
            writer.writerows(records)
            info = ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            bundle.writestr(info, stream.getvalue())
    return path
