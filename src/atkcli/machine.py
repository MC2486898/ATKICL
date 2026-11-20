"""本机进程和监听端点；端口号与进程名都不能单独证明实例身份。"""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

import psutil

from .errors import SessionError
from .lease import iso


@dataclass(frozen=True)
class Instance:
    pid: int
    born: float | None
    exe: str | None
    user: str | None

    def public(self):
        return {"pid": self.pid, "created_epoch_s": self.born,
                "started_utc": iso(self.born) if self.born is not None else None, "exe": self.exe}


def _unreadable():
    return SessionError(42, "PROCESS_INFO_UNAVAILABLE", "系统未允许核对进程或端口归属，已停止操作。")


def _matching_path(a, b):
    return a is not None and os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


class LocalMachine:
    def instances(self):
        found = []
        psutil.process_iter.cache_clear()
        for proc in psutil.process_iter(["name"]):
            if (proc.info["name"] or "").casefold() != "atk.exe":
                continue
            try:
                data = proc.as_dict(attrs=["create_time", "exe", "username"], ad_value=None)
                found.append(Instance(proc.pid, data["create_time"], data["exe"], data["username"]))
            except psutil.NoSuchProcess:
                continue
        return found

    def owner_pids(self, port):
        try:
            return {c.pid for c in psutil.net_connections(kind="tcp")
                    if c.status == psutil.CONN_LISTEN and c.laddr.port == port
                    and c.laddr.ip in ("0.0.0.0", "127.0.0.1", "::", "::ffff:127.0.0.1")}
        except (OSError, psutil.AccessDenied) as exc:
            raise _unreadable() from exc

    def validate_identity(self, inst, home):
        if inst.born is None or not inst.user or not inst.exe:
            raise _unreadable()
        try:
            user = psutil.Process().username()
        except psutil.AccessDenied as exc:
            raise _unreadable() from exc
        if user.casefold() != inst.user.casefold() or not _matching_path(inst.exe, home / "ATK.exe"):
            raise SessionError(41, "INSTANCE_CONFLICT", "已有实例不属于当前账户或指定的 ATK 安装目录。",
                               instance=inst.public())

    def sample(self, inst):
        try:
            proc = psutil.Process(inst.pid)
            if proc.create_time() != inst.born or not proc.is_running():
                raise psutil.NoSuchProcess(inst.pid)
            t = proc.cpu_times()
            return {"alive": True, "cpu_s": round(t.user + t.system, 4)}
        except psutil.NoSuchProcess as exc:
            raise SessionError(20, "DEAD", "原 ATK 进程已经消失或 PID 被复用。", cause="unknown",
                               instance=inst.public()) from exc
        except psutil.AccessDenied as exc:
            raise _unreadable() from exc

    def launch(self, home: Path, port: int):
        # 只启动空 GUI；保持独立生命周期，父进程结束后也不关闭 ATK。
        p = subprocess.Popen([str(home / "ATK.exe"), "-p", str(port)], cwd=home,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            proc = psutil.Process(p.pid)
            inst = Instance(p.pid, proc.create_time(), proc.exe(), proc.username())
        except psutil.NoSuchProcess as exc:
            raise SessionError(20, "DEAD", "新启动的 ATK 进程已经退出。", pid=p.pid, cause="unknown") from exc
        except psutil.AccessDenied as exc:
            raise _unreadable() from exc
        self.validate_identity(inst, home)
        return inst


def select_instance(instances, owners, pid):
    if pid is not None:
        matches = [p for p in instances if p.pid == pid]
        if not matches:
            raise SessionError(21, "ABSENT", "指定 PID 不是当前可见的 ATK 实例。", pid=pid)
        return matches[0]
    if not instances:
        return None
    if len(instances) == 1:
        return instances[0]
    matches = [p for p in instances if p.pid in owners]
    if len(matches) == 1 and owners == {matches[0].pid}:
        return matches[0]
    raise SessionError(41, "AMBIGUOUS", "找到多个 ATK 实例，请指定 --pid 和对应 --port。",
                       instances=[p.public() for p in instances])
