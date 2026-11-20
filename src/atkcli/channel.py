"""堵塞在原生 SDK 时仍能超时或响应 Ctrl+C；只回收本工具的客户端子进程。"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

from .errors import SessionError


class Channel:
    def __init__(self, home, port, mode="attach"):
        if getattr(sys, 'frozen', False):
            # 打包后没有独立解释器和 .py 文件：用保留参数重新进入同一个 exe。
            args = [sys.executable, "--sdk-worker", str(home), str(port), mode]
        else:
            script = Path(__file__).with_name("_sdk_worker.py")
            # Windows venv 的 python.exe 可能是启动器。工作进程只需标准库和厂商 SDK，
            # 直接运行同版本基础解释器，回收阻塞客户端时不留下持有管道的孙进程。
            interpreter = getattr(sys, '_base_executable', None) or sys.executable
            args = [interpreter, "-I", "-B", "-u", str(script), str(home), str(port), mode]
        self._start(args)

    def _start(self, args):
        self.messages = queue.Queue()
        self.proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                     creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self):
        try:
            for line in self.proc.stdout:
                self.messages.put(json.loads(line))
        except (OSError, ValueError):
            pass
        finally:
            self.messages.put(None)

    def _next(self, deadline, tick):
        while True:
            tick()
            left = deadline - time.monotonic()
            if left <= 0:
                raise SessionError(10, "TIMEOUT", "在等待期限内未收到有效的 SDK 应答。")
            try:
                item = self.messages.get(timeout=min(left, 0.4))
            except queue.Empty:
                continue
            tick()
            if not isinstance(item, dict):
                raise SessionError(42, "SDK_WORKER_EXITED", "SDK 客户端已退出；该状态不代表 ATK 退出。")
            return item

    def receive(self, deadline, tick):
        item = self._next(deadline, tick)
        if item.get("state") not in {"READY", "SDK_OK"}:
            raise SessionError(42, item.get("state", "SDK_ERROR"), item.get("message", "SDK 未返回有效响应。"))
        return item

    def receive_message(self, deadline, tick):
        return self._next(deadline, tick)

    def send(self, payload):
        try:
            self.proc.stdin.write(json.dumps(payload, ensure_ascii=True).encode("ascii") + b"\n")
            self.proc.stdin.flush()
        except OSError as exc:
            raise SessionError(42, "SDK_WORKER_EXITED", "客户端连接已关闭。") from exc

    def running(self):
        return self.proc.poll() is None

    def refresh(self):
        try:
            self.proc.stdin.write(b"refresh\n")
            self.proc.stdin.flush()
        except OSError as exc:
            raise SessionError(42, "SDK_WORKER_EXITED", "客户端连接已关闭。") from exc

    def close(self):
        try:
            # EOF 请求正常 atkClose，若 SDK 卡住则仅终止此客户端。
            try:
                self.proc.stdin.close()
            except OSError:
                pass
            try:
                self.proc.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=3)
        finally:
            self.reader.join(timeout=1)
            self.proc.stdout.close()
