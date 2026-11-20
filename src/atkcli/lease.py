"""同一临时目录下所有 atkcli 客户端共用的操作系统锁；不按 PID 删锁。"""
from __future__ import annotations

import errno
import json
import os
import stat
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from .errors import SessionError


FORMAT = "atkcli.lease/1"
LIMIT = 8192


def iso(timestamp=None):
    t = datetime.now(timezone.utc) if timestamp is None else datetime.fromtimestamp(timestamp, timezone.utc)
    return t.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def lock_path():
    temp = os.environ.get('TEMP') if os.name == 'nt' else None
    return Path(temp or tempfile.gettempdir()) / 'atkcli.lock'


def _regular(path):
    try:
        st = path.lstat()
    except FileNotFoundError:
        return
    if not stat.S_ISREG(st.st_mode) or getattr(st, "st_file_attributes", 0) & 0x400 or st.st_nlink > 1:
        raise SessionError(41, "LOCK_PATH_CONFLICT", "锁路径不是独立的普通文件。", path=str(path))


def _lock(f):
    if os.name == "nt":
        import msvcrt
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(f):
    if os.name == "nt":
        import msvcrt
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(f, fcntl.LOCK_UN)


def _contended(exc):
    return exc.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK) or getattr(exc, "winerror", None) == 33


def _metadata(f):
    # 第 0 字节被 Windows 强制加锁；读者从第 1 字节读取描述。描述只用于显示。
    try:
        f.seek(1)
        raw = f.read(LIMIT)
    except OSError:
        return None
    try:
        obj = json.loads(raw)
        if isinstance(obj, dict) and obj.get("format") == FORMAT:
            return obj
    except (ValueError, UnicodeError):
        pass
    return None


def lock_info(path=None):
    p = Path(path) if path is not None else lock_path()
    base = {"path": str(p)}
    _regular(p)
    try:
        f = p.open("r+b", buffering=0)
    except FileNotFoundError:
        return {**base, "held": False, "owner": None}
    except OSError:
        return {**base, "held": None, "owner": None, "note": "锁状态不可读。"}
    with f:
        try:
            _lock(f)
        except OSError as exc:
            if not _contended(exc):
                return {**base, "held": None, "owner": None}
            return {**base, "held": True, "owner": _metadata(f)}
        try:
            meta = _metadata(f)
            if os.fstat(f.fileno()).st_size and meta is None:
                return {**base, 'held': None, 'owner': None, 'note': '登记格式未知；接管会保留原文件并退出。'}
            return {**base, "held": False, "owner": None, "last_record": meta}
        finally:
            _unlock(f)


class Lease:
    def __init__(self, path: Path, record: dict):
        self.path = path
        self.record = {**record, "format": FORMAT, "client_pid": os.getpid(), "released_utc": None}
        self.file = None

    def acquire(self, seconds: float):
        _regular(self.path)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) |
                     getattr(os, "O_BINARY", 0), 0o600)
        f = os.fdopen(fd, "r+b", buffering=0)
        deadline = time.monotonic() + seconds
        try:
            while True:
                try:
                    _lock(f)
                    break
                except OSError as exc:
                    if not _contended(exc):
                        raise
                    left = deadline - time.monotonic()
                    if left <= 0:
                        raise SessionError(40, "LOCKED", "实例锁已被占用。", path=str(self.path),
                                           owner=_metadata(f)) from None
                    time.sleep(min(0.2, left))
            # 已有的其他工具也可能约定用这个文件。拒绝覆盖无法识别的登记。
            size = os.fstat(f.fileno()).st_size
            if size and (size > LIMIT or _metadata(f) is None):
                raise SessionError(41, "UNKNOWN_LOCK_FILE", "已有锁文件的格式不可识别，已保留原文件。",
                                   path=str(self.path))
            self.file = f
            self.update(acquired_utc=iso())
            return self
        except BaseException:
            self.file = None
            f.close()  # 内核自动释放此句柄上的锁
            raise

    def update(self, **fields):
        self.record.update(fields)
        data = b" " + json.dumps(self.record, ensure_ascii=False, allow_nan=False).encode("utf-8")
        if len(data) > LIMIT:
            raise ValueError("锁元数据过长。")
        f = self.file
        f.seek(0)
        f.write(data)
        f.truncate()

    def release(self):
        if self.file is None:
            return
        f = self.file
        try:
            self.update(released_utc=iso())
        finally:
            self.file = None
            f.close()
