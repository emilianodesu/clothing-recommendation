"""Reproducible nontrivial fixture for training and checkpoint recovery."""
import csv
from datetime import date, timedelta
import io
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED

from hm_recommender.data.ingest import ingest
from hm_recommender.data.validate import SCHEMAS
from hm_recommender.features.build import build_examples, build_features, VALIDATION
from hm_recommender.model.inputs import load_features


def prepare_demo_dataset(root):
    root.mkdir(parents=True, exist_ok=True)
    users = ['demo-%03d' % i for i in range(33)]
    articles = ['%010d' % i for i in range(25)]
    rows = {'customers.csv': [dict(customer_id=u) for u in users],
        'sample_submission.csv': [dict(customer_id=u, prediction=' '.join(articles[:12])) for u in users],
        'articles.csv': [dict(article_id=a, product_type_name='type-' + str(i % 4),
            product_group_name='group-' + str(i % 2), colour_group_name='color-' + str(i % 3)) for i, a in enumerate(articles)],
        'transactions_train.csv': []}
    # Week zero exposes every ordinary item. Later weeks give each user a stable taste.
    for week in range(8):
        for i, user in enumerate(users[:32]):
            for j in range(2):
                item = (i + j * 4) % 24
                rows['transactions_train.csv'].append(dict(t_dat=str(date(2020, 7, 22) + timedelta(days=7 * week)),
                    customer_id=user, article_id=articles[item], price=str(.01 * (1 + item % 4)), sales_channel_id=str(1 + i % 2)))
    rows['transactions_train.csv'].extend([
        dict(t_dat='2020-09-09', customer_id=users[-1], article_id=articles[0], price='.02', sales_channel_id='1'),
        dict(t_dat='2020-09-09', customer_id=users[0], article_id=articles[-1], price='.02', sales_channel_id='1')])
    source = root / 'synthetic.zip'
    with ZipFile(source, 'w') as bundle:
        for name, records in rows.items():
            stream = io.StringIO(); writer = csv.DictWriter(stream, fieldnames=SCHEMAS[name])
            writer.writeheader(); writer.writerows(records)
            info = ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0)); info.compress_type = ZIP_DEFLATED
            bundle.writestr(info, stream.getvalue())
    dataset = ingest(source, root / 'dataset', expected=None)
    return dataset


def prepare_demo(root):
    dataset = prepare_demo_dataset(root)
    build_features(dataset, VALIDATION, root / 'features/train')
    build_examples(dataset, root / 'features/examples')
    return load_features(root / 'features/train')
