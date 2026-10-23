"""Bounded, data-only portable packages. Never extract archive paths directly."""
import os
from pathlib import Path
import shutil
import stat
import tempfile
import zipfile
from hm_recommender.releases.package import verify_history_release

FILES={'manifest.json','rules.json','validation-reference.json','catalog.parquet','customers.parquet','histories.parquet','popularity.parquet'}
MAX_BYTES=2*1024**3


def unpack(archive, destination, allowed=None, max_bytes=MAX_BYTES, max_files=1024):
    archive=Path(archive);destination=Path(destination)
    if archive.is_symlink() or not archive.is_file() or archive.stat().st_size>max_bytes:raise ValueError('Invalid archive')
    try:
        with zipfile.ZipFile(archive) as z:
            infos=z.infolist();names=[i.filename for i in infos]
            if not infos or len(infos)>max_files or len(set(names))!=len(names):raise ValueError('Invalid archive inventory')
            if allowed is not None and set(names)!=set(allowed):raise ValueError('Unexpected archive inventory')
            if sum(i.file_size for i in infos)>max_bytes:raise ValueError('Archive exceeds budget')
            for i in infos:
                # Flat filenames only; no links, directories, devices, encryption or alternate paths.
                mode=i.external_attr>>16
                if (not i.filename or '/' in i.filename or '\\' in i.filename or ':' in i.filename or i.filename in ('.','..')
                    or i.flag_bits&1 or stat.S_IFMT(mode) not in (0,stat.S_IFREG)):
                    raise ValueError('Unsafe archive entry')
            for i in infos:
                with z.open(i) as src,(destination/i.filename).open('xb') as dst:
                    shutil.copyfileobj(src,dst,1024*1024)
    except (zipfile.BadZipFile,RuntimeError,NotImplementedError) as error:
        raise ValueError('Invalid archive encoding or checksum') from error


def pack(directory, output):
    directory=Path(directory);output=Path(output)
    output.parent.mkdir(parents=True,exist_ok=True)
    if output.exists() or output.is_symlink():raise ValueError('Output already exists')
    fd,tmp=tempfile.mkstemp(prefix='.transfer-',dir=output.parent);os.close(fd)
    try:
        with zipfile.ZipFile(tmp,'w',compression=zipfile.ZIP_STORED,allowZip64=True) as z:
            for path in sorted(directory.iterdir()):
                if not path.is_file() or path.is_symlink():raise ValueError('Only regular files can be packed')
                z.write(path,path.name)
        with open(tmp,'rb') as f:os.fsync(f.fileno())
        # Atomic create without clobbering a concurrent export.
        os.link(tmp,output)
        os.chmod(output,0o600)
    finally:Path(tmp).unlink(missing_ok=True)
    return output


def export_release(directory: Path, output: Path) -> Path:
    directory=Path(directory);before=verify_history_release(directory)
    Path(output).parent.mkdir(parents=True,exist_ok=True)
    # Reverify the actual exported bytes to detect source changes during copy.
    with tempfile.TemporaryDirectory(dir=Path(output).parent) as tmp:
        for name in FILES:shutil.copyfile(directory/name,Path(tmp)/name)
        after=verify_history_release(Path(tmp))
        if after.model_dump()!=before.model_dump():raise ValueError('Release changed during export')
        return pack(tmp,output)


def import_release(archive: Path, destination: Path):
    destination=Path(destination)
    if destination.exists() or destination.is_symlink():raise ValueError('Destination must be new')
    destination.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.import-',dir=destination.parent) as tmp:
        root=Path(tmp);unpack(archive,root,FILES)
        manifest=verify_history_release(root)
        # A same-filesystem rename only after full semantic verification.
        root.rename(destination)
    manifest._root=destination
    return manifest
