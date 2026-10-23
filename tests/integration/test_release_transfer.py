from datetime import date
import importlib.util
import zipfile
import pytest
from hm_recommender.model.demo import prepare_demo_dataset
from hm_recommender.releases.package import build_history_release
from hm_recommender.api.recommend import load_state


def transfer():
    assert importlib.util.find_spec('hm_recommender.releases.transfer'), 'release transfer missing'
    from hm_recommender.releases import transfer
    return transfer

@pytest.fixture
def package(tmp_path,monkeypatch):
    monkeypatch.setenv('HMREC_SOURCE_COMMIT','a'*40)
    return build_history_release(prepare_demo_dataset(tmp_path/'data'),date(2020,9,9),tmp_path/'package')

def test_import_without_original_mlflow(package,tmp_path):
    t=transfer();archive=t.export_release(package._root,tmp_path/'portable.zip')
    saved=t.import_release(archive,tmp_path/'another user'/'package')
    assert saved.release_id==package.release_id
    a,b=load_state(package._root),load_state(saved._root)
    for user in ['unknown',*a.customers][:10]:
        assert a.rank(user,{})==b.rank(user,{})
    with pytest.raises(ValueError):t.import_release(archive,saved._root)

@pytest.mark.parametrize('name',['../escape','/absolute','nested/file','manifest.json'])
def test_corrupt_or_unsafe_release_is_rejected(package,tmp_path,name):
    t=transfer();archive=t.export_release(package._root,tmp_path/'portable.zip')
    with zipfile.ZipFile(archive,'a') as z:z.writestr(name,b'invalid')
    with pytest.raises(ValueError):t.import_release(archive,tmp_path/'destination')
    assert not (tmp_path/'destination').exists()

def test_symlink_member_and_modified_hash_rejected(package,tmp_path):
    t=transfer();archive=t.export_release(package._root,tmp_path/'portable.zip')
    bad=tmp_path/'bad.zip'
    with zipfile.ZipFile(archive) as src,zipfile.ZipFile(bad,'w') as dst:
        for i in src.infolist():
            if i.filename=='rules.json':i.external_attr=(0o120777<<16);dst.writestr(i,b'/etc/passwd')
            else:dst.writestr(i,src.read(i))
    with pytest.raises(ValueError):t.import_release(bad,tmp_path/'out')
    with pytest.raises(ValueError):t.export_release(package._root,archive)
