"""Small public request contract shared by container reference and stdlib host client."""
import hashlib
import json


def host_requests():
    return [dict(customer_id='__hmrec_unknown_probe__', k=12, filters=filters)
        for filters in ({}, {'colour_group_name':'__hmrec_empty_filter__'})]


def responses_digest(bodies):
    digest=hashlib.sha256()
    for body in bodies:
        digest.update(json.dumps(body,sort_keys=True,separators=(',',':')).encode())
    return digest.hexdigest()
