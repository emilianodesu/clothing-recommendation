"""Display metadata uses a real FastAPI route with an immutable synthetic snapshot."""
from types import SimpleNamespace, MappingProxyType
from unittest.mock import Mock
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from hm_recommender.api.events import install_events


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('HMREC_IMAGE_CACHE', str(tmp_path/'images'))
    snapshot = SimpleNamespace(release_id='synthetic-release', catalog=frozenset({'001', '002'}),
        filter_index=MappingProxyType({
            'product_type_name': {'T-shirt': frozenset({'001', '002'})},
            'colour_group_name': {'Blue': frozenset({'001'}), 'Black': frozenset({'002'})},
        }))
    connection = Mock(side_effect=AssertionError('Must not write or query the database'))
    registry = SimpleNamespace(snapshot=lambda: snapshot, store=SimpleNamespace(connection=connection))
    app = FastAPI()
    install_events(app, registry, lambda: None)
    with TestClient(app) as c: yield c
    connection.assert_not_called()


def test_details_are_bounded_ordered_and_read_only(client):
    response = client.post('/articles/details', json={'article_ids': ['002', '001', '002', '999']})
    assert response.status_code == 200
    assert response.json() == {'release_id': 'synthetic-release', 'items': [
        {'article_id': '002', 'product_type_name': 'T-shirt', 'colour_group_name': 'Black'},
        {'article_id': '001', 'product_type_name': 'T-shirt', 'colour_group_name': 'Blue'},
    ]}


@pytest.mark.parametrize('ids', [[], ['001']*49, ['../secret'], ['1'*33], [1]])
def test_invalid_details_are_rejected(client, ids):
    assert client.post('/articles/details', json={'article_ids': ids}).status_code == 422
