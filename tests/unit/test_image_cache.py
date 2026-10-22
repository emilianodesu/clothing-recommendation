"""No bulk extraction, traversal, symlinks or unbounded image cache."""
import io
from pathlib import Path
import zipfile
import pytest


def test_requested_images_only_and_missing_placeholder(tmp_path):
    from hm_recommender.ui.images import ImageCache
    source=tmp_path/'source.zip'
    with zipfile.ZipFile(source,'w') as z:
        z.writestr('images/000/0000000001.jpg',b'\xff\xd8'+b'a'*50+b'\xff\xd9')
        z.writestr('images/000/0000000002.jpg',b'\xff\xd8'+b'b'*50+b'\xff\xd9')
        z.writestr('../escape.jpg',b'bad')
    cache=ImageCache(source,tmp_path/'cache',max_bytes=60)
    first,mime=cache.read('0000000001')
    assert mime=='image/jpeg' and first[2:5]==b'aaa'
    assert len(list((tmp_path/'cache').glob('*.jpg')))==1
    cache.read('0000000002')
    assert sum(p.stat().st_size for p in (tmp_path/'cache').glob('*.jpg'))<=60
    assert cache.read('0000000003')[1]=='image/svg+xml'
    assert cache.read('../escape')[1]=='image/svg+xml'
    assert not (tmp_path/'escape.jpg').exists()
    assert ImageCache(None,tmp_path/'empty').read('0000000001')[1]=='image/svg+xml'


def test_oversized_duplicate_and_symlink_cache_are_rejected(tmp_path):
    from hm_recommender.ui.images import ImageCache
    source=tmp_path/'source.zip'
    with zipfile.ZipFile(source,'w') as z:z.writestr('images/000/0000000001.jpg',b'x'*100)
    cache=ImageCache(source,tmp_path/'cache',max_bytes=60)
    assert cache.read('0000000001')[1]=='image/svg+xml'
    link=tmp_path/'linked';link.symlink_to(tmp_path/'cache',target_is_directory=True)
    with pytest.raises(ValueError):ImageCache(source,link)
