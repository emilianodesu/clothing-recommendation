"""Hash executable package content without host paths or secrets."""
import hashlib
from pathlib import Path


def source_digest(root=None):
    root=Path(root) if root is not None else Path(__file__).resolve().parents[1]
    digest=hashlib.sha256()
    for path in sorted(root.rglob('*.py')):
        digest.update(str(path.relative_to(root)).encode()+b'\0'+path.read_bytes()+b'\0')
    return digest.hexdigest()
