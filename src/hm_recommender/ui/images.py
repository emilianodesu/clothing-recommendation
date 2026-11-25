"""Read requested archive members with a bounded local JPEG cache."""

import hashlib
import os
from pathlib import Path
import re
import threading
import zipfile
import zlib

PLACEHOLDER = (
    b'<svg xmlns="http://www.w3.org/2000/svg" width="300" height="320" viewBox="0 0 300 320">'
    b'<rect width="300" height="320" fill="#eeeae3"/>'
    b'<path d="M110 90L70 120 90 155 110 142V235H190V142L210 155 230 120 190 90 175 105H125Z" fill="#b9b0a2"/>'
    b'<text x="150" y="278" text-anchor="middle" fill="#5a5145" font-family="sans-serif" font-size="14">'
    b'Imagen no disponible</text></svg>'
)


class ImageCache:
    """Thread-safe disk cache for product catalog images extracted from ZIP archive."""

    def __init__(self, archive: str | Path | None, directory: str | Path, *, max_bytes: int = 64 * 1024**2):
        self.archive = Path(archive) if archive else None
        self.directory = Path(directory)
        if (
            isinstance(max_bytes, bool)
            or not isinstance(max_bytes, int)
            or self.directory.is_symlink()
            or not 1 <= max_bytes <= 512 * 1024**2
        ):
            raise ValueError('Invalid image cache')
        self.directory.mkdir(parents=True, exist_ok=True)
        self.max_bytes = max_bytes
        self.lock = threading.Lock()

    def read(self, article_id: str) -> tuple[bytes, str]:
        """Read an article image from cache or archive, returning bytes and content-type."""
        if (
            not isinstance(article_id, str)
            or not re.fullmatch(r'[0-9]{10}', article_id)
            or self.archive is None
            or not self.archive.is_file()
        ):
            return PLACEHOLDER, 'image/svg+xml'

        with self.lock:
            try:
                stat = self.archive.stat()
                key = hashlib.sha256(
                    f'{self.archive.resolve()}:{stat.st_size}:{stat.st_mtime_ns}:{article_id}'.encode()
                ).hexdigest()
                target = self.directory / (key + '.jpg')

                if target.is_symlink():
                    raise ValueError('Cache symlink')

                if target.is_file():
                    if target.stat().st_size > self.max_bytes:
                        raise ValueError('Oversized cache entry')
                    data = target.read_bytes()
                    os.utime(target, None)
                    return data, 'image/jpeg'

                member = f'images/{article_id[:3]}/{article_id}.jpg'
                with zipfile.ZipFile(self.archive) as z:
                    entry = z.getinfo(member)
                    if entry.is_dir():
                        return PLACEHOLDER, 'image/svg+xml'
                    if (entry.external_attr >> 16) & 0o170000 == 0o120000:
                        raise ValueError('Zip symlink member')
                    max_member_bytes = min(self.max_bytes, 10 * 1024**2)
                    if entry.file_size > max_member_bytes or entry.flag_bits & 1:
                        raise ValueError('Invalid image member')
                    data = z.read(entry)
                    if len(data) > max_member_bytes:
                        raise ValueError('Decompressed member exceeds max_bytes')

                if not data.startswith(b'\xff\xd8') or not data.endswith(b'\xff\xd9'):
                    raise ValueError('Not a JPEG')

                # Evict oldest files if needed to stay within max_bytes budget
                files = sorted(
                    (p for p in self.directory.glob('*.jpg') if not p.is_symlink()),
                    key=lambda p: p.stat().st_mtime_ns,
                )
                used = sum(p.stat().st_size for p in files)
                for p in files:
                    if used + len(data) <= self.max_bytes:
                        break
                    size = p.stat().st_size
                    p.unlink()
                    used -= size

                temporary = target.with_suffix('.partial')
                if temporary.is_symlink():
                    raise ValueError('Cache symlink')
                try:
                    temporary.write_bytes(data)
                    temporary.replace(target)
                except BaseException:
                    temporary.unlink(missing_ok=True)
                    raise
                return data, 'image/jpeg'

            except (
                OSError,
                ValueError,
                KeyError,
                zipfile.BadZipFile,
                zlib.error,
                EOFError,
                NotImplementedError,
                RuntimeError,
            ):
                return PLACEHOLDER, 'image/svg+xml'
