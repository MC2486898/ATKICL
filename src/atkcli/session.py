"""ATK 本机会话：被动发现 → 独占租约 → 进程与端口核验 → SDK 握手。

会话默认只读（refresh/hold/status）；只有显式调用 execute() 才会发送会修改场景的
脚本命令，且每条命令都经过同一子进程和同一把租约锁。
"""
from __future__ import annotations

import math
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from .channel import Channel
from .errors import SessionError
from .lease import Lease, iso, lock_info, lock_path
from .machine import LocalMachine, select_instance


def seconds(value, name, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
        raise ValueError(f"{name} 必须是有限秒数。")
    if value < 0 or (positive and not value):
        raise ValueError(f"{name} 必须{'大于' if positive else '大于或等于'}零。")
    return value


def default_home():
    return Path(os.environ.get("ATK_HOME", r"D:\ATK-4.2.0-rc.2"))


@dataclass(frozen=True)
class Config:
    home: Path = field(default_factory=default_home)
    port: int = 6655
    pid: int | None = None
    timeout: float = 15
    wait_lock: float = 0
    start_if_missing: bool = False
    label: str = ""
    expected_s: float | None = None
    expected_born: float | None = None
    expect_scene: str | None = None

    def select(self, instances, owners):
        if self.expected_born is not None:
            match = next((p for p in instances if p.pid == self.pid), None)
            if match is not None and match.born is None:
                raise SessionError(42, 'PROCESS_INFO_UNAVAILABLE', '读不到进程创建时间，无法验证回执。')
            if match is None or match.born != self.expected_born:
                raise SessionError(20, 'DEAD', '回执中的原实例已消失或 PID 已被复用。',
                                   cause='unknown', original_pid=self.pid,
                                   original_started_utc=iso(self.expected_born))
        return select_instance(instances, owners, self.pid)

    def __post_init__(self):
        home = Path(self.home).expanduser()
        if str(home).startswith(("\\\\", "//")):
            raise ValueError("请指定本机受信任的 ATK 安装目录。")
        object.__setattr__(self, 'home', home.resolve())
        if type(self.port) is not int or not 1 <= self.port <= 65535:
            raise ValueError("port 必须是 1 至 65535 的整数。")
        if self.pid is not None and (type(self.pid) is not int or self.pid <= 0):
            raise ValueError("PID 必须是正整数。")
        seconds(self.timeout, 'timeout', positive=True)
        seconds(self.wait_lock, 'wait_lock')
        if self.expected_s is not None:
            seconds(self.expected_s, 'expected_s')
        if self.expected_born is not None:
            seconds(self.expected_born, 'created_epoch_s', positive=True)
            try:
                iso(self.expected_born)
            except (ValueError, OverflowError, OSError) as exc:
                raise ValueError('回执中的创建时间超出可表示范围。') from exc
            if self.pid is None or self.start_if_missing:
                raise ValueError('凭回执接回原实例需要 PID，且不允许启动新进程。')
        if self.expect_scene is not None and (not self.expect_scene or len(self.expect_scene) > 160
                                               or not self.expect_scene.isprintable()):
            raise ValueError('期望场景名须为 1–160 个可打印字符。')
        if len(self.label) > 160 or self.label and not self.label.isprintable():
            raise ValueError("label 最多 160 个可打印字符。")


def doctor(config=None):
    c = config or Config()
    ch = Channel(c.home, c.port, 'doctor')
    try:
        result = ch.receive(time.monotonic() + c.timeout, lambda: None)
    finally:
        ch.close()
    return {"ok": True, **result, "home": str(c.home), "connected": False}


def status(config=None, *, _machine=None, _lock=None):
    """被动观察。监听端口存在不等于 ATK 能响应命令。"""
    c, m = config or Config(), _machine or LocalMachine()
    arr, owners = m.instances(), m.owner_pids(c.port)
    selected = [i for i in arr if c.pid is None or c.pid == i.pid]
    if not selected:
        state = 'PORT_CONFLICT' if owners else 'ABSENT'
    elif owners and not any(owners == {i.pid} for i in selected):
        state = 'PORT_CONFLICT'
    else:
        state = 'LISTENING' if owners else 'PROCESS_ONLY'
    return {"ok": True, "state": state, "instances": [p.public() for p in selected],
            "endpoint": f"127.0.0.1:{c.port}", "listener_pids": sorted(owners, key=lambda v: v or 0),
            "responsive": None, "lock": lock_info(_lock)}


def _sample(m, inst, port):
    try:
        return m.sample(inst)
    except SessionError as exc:
        if exc.state == 'DEAD':
            # 只能观察终点有无新 PID，不能据此判断是抢占、人手关闭还是崩溃。
            try:
                exc.details['listener_pids_at_detection'] = sorted(
                    p for p in m.owner_pids(port) if p is not None)
            except SessionError:
                pass
        raise


class Session:
    """with 期间持有一个本机 SDK 连接和实例锁；退出只断开客户端。"""

    def __init__(self, config=None, *, on_event=None, _machine=None, _channel=None, _lock=None, _doctor=None):
        self.config = config or Config()
        self.machine = _machine or LocalMachine()
        self._factory = _channel or Channel
        self._doctor = _doctor or doctor
        self._path = _lock if _lock is not None else lock_path()
        self.on_event = on_event
        self.instance = self._lease = self._channel = None
        self.last = {}
        self.mode = None

    def _event(self, event, **fields):
        if self.on_event is not None:
            self.on_event({"utc": iso(), "event": event, "elapsed_s": round(time.monotonic()-self._t0, 3),
                           "pid": self.instance.pid if self.instance else None, **fields})

    def _tick(self):
        sample = _sample(self.machine, self.instance, self.config.port)
        now = time.monotonic()
        if now >= self._next_beat:
            self._event('heartbeat', **sample)
            self._next_beat = now + 30
        return sample

    def _ensure_owner(self, deadline):
        c, inst = self.config, self.instance
        while True:
            sample = self._tick()
            owners = self.machine.owner_pids(c.port)
            if owners == {inst.pid}:
                return
            if owners:
                raise SessionError(41, 'PORT_CONFLICT', '端口所有者与选定实例不一致。',
                                   expected_pid=inst.pid, listener_pids=sorted(owners, key=lambda x: x or 0))
            left = deadline - time.monotonic()
            if left <= 0:
                raise SessionError(10, 'TIMEOUT', '进程仍在，指定端口在期限内未就绪。',
                                   **sample, instance=inst.public())
            time.sleep(min(left, 0.4))

    def _receive(self, deadline):
        try:
            response = self._channel.receive(deadline, self._tick)
            self._ensure_owner(deadline)
        except SessionError as exc:
            if exc.state != 'DEAD':
                exc.details.update(self._tick(), instance=self.instance.public())
            raise
        if self.config.expect_scene is not None and response.get('scene_name') != self.config.expect_scene:
            raise SessionError(41, 'SCENE_MISMATCH', '场景名未匹配，已断开客户端，未切换场景。',
                               expected=self.config.expect_scene, actual=response.get('scene_name'))
        self.last = {"ok": True, "schema": "atkcli.session/1", **response, "mode": self.mode,
                     "instance": self.instance.public(), "port": self.config.port,
                     "endpoint": f"127.0.0.1:{self.config.port}", "queried_utc": iso()}
        self._lease.update(scene_open=response['scene_open'], scene_name=response.get('scene_name'))
        return dict(self.last)

    def __enter__(self):
        if self._lease is not None:
            raise RuntimeError('此会话已经打开。')
        c, m = self.config, self.machine
        self.instance, self.mode, self.last = None, None, {}
        self._t0 = self._next_beat = time.monotonic()
        self._lease = Lease(self._path, {"session_started_utc": iso(), "label": c.label,
                                        "scene_name": None, "expected_s": c.expected_s, "port": c.port})
        try:
            self._lease.acquire(c.wait_lock)
            arr, owners = m.instances(), m.owner_pids(c.port)
            inst = c.select(arr, owners)
            if inst is None and c.start_if_missing:
                if owners:
                    raise SessionError(41, 'PORT_CONFLICT', '指定端口已有监听者，未启动 ATK。')
                self._doctor(c)  # 缺 SDK 时不要先开 GUI 再报错。
                arr, owners = m.instances(), m.owner_pids(c.port)
                inst = c.select(arr, owners)
                if inst is None:
                    if owners:
                        raise SessionError(41, 'PORT_CONFLICT', '指定端口已经被占用，未启动 ATK。')
                    inst = m.launch(c.home, c.port)
                    self.mode = 'started'
            if inst is None:
                if owners:
                    raise SessionError(41, 'PORT_CONFLICT', '端口有监听者，但未找到可核验的 ATK 实例。')
                raise SessionError(21, 'ABSENT', '未发现 ATK 实例；attach 不会自行启动。')
            self.instance = inst
            self.mode = self.mode or 'attached'
            m.validate_identity(inst, c.home)
            self._lease.update(atk_pid=inst.pid, atk_started_utc=iso(inst.born), mode=self.mode)
            self._event('selected', mode=self.mode)
            deadline = time.monotonic() + c.timeout
            self._ensure_owner(deadline)
            self._channel = self._factory(c.home, c.port)
            self._receive(deadline)
            self._event('ready')
            return self
        except BaseException:
            self.close()
            raise

    def refresh(self):
        """重新进行只读 SDK 查询，使用最初的连接；不重连替换实例。"""
        if self._channel is None:
            raise RuntimeError('请先进入 Session 上下文。')
        # 失败后本连接可能仍有未完成的读取；必须关闭，不能将迟到的旧回复当成下一次查询。
        deadline = time.monotonic() + self.config.timeout
        try:
            self._ensure_owner(deadline)
            self._channel.refresh()
            return self._receive(deadline)
        except BaseException:
            try:
                self._tick()
            finally:
                self.close()
            raise

    def execute(self, commands, *, timeout=30, continue_on_error=False,
                should_stop=None, on_result=None):
        """逐条发送 Connect 命令。仅由脚本页或 script 子命令调用，会修改场景。"""
        if self._channel is None:
            raise RuntimeError('请先进入 Session 上下文。')
        seconds(timeout, 'timeout', positive=True)
        t0 = time.monotonic()
        results = []
        stopped = False
        try:
            for index, item in enumerate(commands):
                if should_stop is not None and should_stop():
                    stopped = True
                    break
                sleep_s = item.get('sleep_s') or 0
                if sleep_s:
                    end = time.monotonic() + sleep_s
                    while True:
                        self._tick()
                        left = end - time.monotonic()
                        if left <= 0:
                            break
                        if should_stop is not None and should_stop():
                            stopped = True
                            break
                        time.sleep(min(left, 0.4))
                    record = {'index': index, 'command': 'sleep', 'argument': str(sleep_s),
                              'ok': True, 'reply': '', 'elapsed_s': sleep_s}
                    results.append(record)
                    if on_result is not None:
                        on_result(record)
                    if stopped:
                        break
                    continue
                self._channel.send({'op': 'step', 'command': item['command'],
                                    'argument': item.get('argument', '')})
                message = self._channel.receive_message(time.monotonic() + timeout, self._tick)
                if message.get('state') != 'RESULT':
                    raise SessionError(42, message.get('state', 'SDK_ERROR'),
                                       message.get('message', 'SDK 未返回脚本执行结果。'))
                record = {'index': index, 'command': message.get('command'),
                          'argument': message.get('argument', ''),
                          'ok': not message.get('nack'), 'reply': message.get('reply', ''),
                          'elapsed_s': message.get('elapsed_s')}
                results.append(record)
                if on_result is not None:
                    on_result(record)
                if not record['ok'] and not continue_on_error:
                    break
        except BaseException:
            try:
                self._tick()
            finally:
                self.close()
            raise
        failed = sum(1 for r in results if not r['ok'])
        state = 'STOPPED' if stopped else ('SCRIPT_FAILED' if failed else 'SCRIPT_DONE')
        result = {'ok': not stopped and failed == 0, 'state': state, 'schema': 'atkcli.script/1',
                  'executed': len(results), 'failed': failed, 'results': results,
                  'elapsed_s': round(time.monotonic() - t0, 3),
                  'instance': self.instance.public(), 'port': self.config.port,
                  'endpoint': f'127.0.0.1:{self.config.port}', 'queried_utc': iso()}
        if stopped:
            result['exit_code'] = 130
        elif failed:
            result['exit_code'] = 31
        return result

    def hold(self, duration):
        """持连接和锁观察原 PID；不在漫长无响应期间循环发送 SDK 请求。"""
        if self._channel is None:
            raise RuntimeError('请先进入 Session 上下文。')
        seconds(duration, 'duration')
        end = time.monotonic() + duration
        while True:
            sample = self._tick()
            if not self._channel.running():
                raise SessionError(42, 'SDK_WORKER_EXITED', '接管客户端已退出，原 ATK 进程仍在。', **sample)
            left = end - time.monotonic()
            if left <= 0:
                self._event('hold_finished', **sample)
                return sample
            time.sleep(min(left, 0.4))

    def close(self):
        try:
            if self._channel is not None:
                self._channel.close()
        finally:
            self._channel = None
            if self._lease is not None:
                self._lease.release()
                self._lease = None

    def __exit__(self, exc_type, exc, tb):
        self.close()


def watch(config=None, *, duration=60, on_event=None, _machine=None):
    """不占锁、不连 TCP，只观察启动时选定的进程。"""
    seconds(duration, 'duration')
    c, m = config or Config(), _machine or LocalMachine()
    inst = c.select(m.instances(), m.owner_pids(c.port))
    if inst is None:
        raise SessionError(21, 'ABSENT', '未发现可监控的 ATK 进程。')
    m.validate_identity(inst, c.home)
    t0 = next_beat = time.monotonic()
    while True:
        sample = _sample(m, inst, c.port)
        now = time.monotonic()
        if now >= next_beat:
            if on_event:
                on_event({"event": "heartbeat", "utc": iso(), "pid": inst.pid, **sample,
                          "elapsed_s": round(now-t0, 3)})
            next_beat = now + 30
        left = t0 + duration - now
        if left <= 0:
            return {"ok": True, "state": "OBSERVED", **sample, "instance": inst.public(),
                    "elapsed_s": round(now-t0, 3), "responsive": None}
        time.sleep(min(left, 0.4))
