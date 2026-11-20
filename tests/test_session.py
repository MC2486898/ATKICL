"""黑盒状态与资源归属测试。不会启动、关闭或连接真实 ATK。"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

from atkcli import Config, Session, SessionError, status, watch
from atkcli.channel import Channel
from atkcli.lease import Lease, lock_info
from atkcli.machine import Instance, LocalMachine
from atkcli._sdk_worker import _scene_name


I = Instance(231, 1230000000.25, 'X:/test/ATK.exe', 'test-user')
J = Instance(312, 1230000001.25, 'X:/test/ATK.exe', 'test-user')


class Machine:
    def __init__(self, arr=(I,), owners=(I.pid,)):
        self.arr, self.owners = list(arr), set(owners)
        self.starts = 0
        self.denied = False

    def instances(self):
        return self.arr.copy()

    def owner_pids(self, port):
        return set(self.owners)

    def validate_identity(self, inst, home):
        if self.denied:
            raise SessionError(41, 'INSTANCE_CONFLICT', '账户或路径不符。')

    def launch(self, home, port):
        assert not self.arr and not self.owners
        self.starts += 1
        self.arr, self.owners = [I], {I.pid}
        return I

    def sample(self, inst):
        if inst not in self.arr:
            raise SessionError(20, 'DEAD', '原实例已消失。', cause='unknown')
        return {'alive': True, 'cpu_s': 105.25}


class Peer:
    def __init__(self):
        self.opens = self.closes = self.refreshes = 0
        self.failure = None
        self.ok = True

    def __call__(self, home, port):
        self.opens += 1
        return self

    def receive(self, deadline, tick):
        tick()
        if self.failure:
            raise self.failure
        return {'state': 'READY', 'platform': 'AMD64', 'scene_open': True, 'scene_name': '图例'}

    def refresh(self):
        self.refreshes += 1

    def close(self):
        self.closes += 1

    def running(self):
        return self.ok


def setup_session(tmp_path, m=None, p=None, doctorfn=None, events=None, **kwargs):
    m = m or Machine(); p = p or Peer()
    conf = Config(**kwargs)
    return Session(conf, _machine=m, _channel=p, _lock=tmp_path/'atkcli.lock',
                   _doctor=doctorfn or (lambda c: None), on_event=events), m, p


def test_attach_holds_os_lease_and_does_not_start_or_close_gui(tmp_path):
    events = []
    s, m, p = setup_session(tmp_path, events=events.append, start_if_missing=True, label='读界面', expected_s=30)
    with s:
        assert s.last['instance'] == I.public() and s.last['mode'] == 'attached'
        assert s.hold(0)['alive']
        owner = lock_info(tmp_path/'atkcli.lock')['owner']
        assert owner['atk_pid'] == I.pid and owner['scene_name'] == '图例' and owner['label'] == '读界面'
        assert s.refresh()['scene_open'] is True
        assert p.opens == 1 and p.refreshes == 1
    assert p.closes == 1 and m.instances() == [I] and m.starts == 0
    assert {'heartbeat', 'selected', 'ready', 'hold_finished'} <= {e['event'] for e in events}
    assert lock_info(tmp_path/'atkcli.lock')['held'] is False
    with pytest.raises(RuntimeError):
        s.refresh()


@pytest.mark.parametrize('arr,owners,code,state', [
    ([], set(), 21, 'ABSENT'), ([], {910}, 41, 'PORT_CONFLICT'),
    ([I], {910}, 41, 'PORT_CONFLICT'), ([I, J], set(), 41, 'AMBIGUOUS'),
    ([I], set(), 10, 'TIMEOUT'), ([I], {I.pid, None}, 41, 'PORT_CONFLICT'),
])
def test_never_contact_unverified_port(tmp_path, arr, owners, code, state):
    s, m, p = setup_session(tmp_path, m=Machine(arr, owners), timeout=.015)
    with pytest.raises(SessionError) as ex:
        with s:
            pytest.fail('不得接管未验证的实例')
    assert (ex.value.code, ex.value.state) == (code, state)
    assert not p.opens and not m.starts and not lock_info(tmp_path/'atkcli.lock')['held']


def test_unowned_port_blocks_opt_in_launch(tmp_path):
    s, m, p = setup_session(tmp_path, m=Machine([], {910}), start_if_missing=True)
    with pytest.raises(SessionError, match='未启动'):
        with s:
            pass
    assert m.starts == p.opens == 0


def test_no_access_to_other_account_or_install(tmp_path):
    s, m, p = setup_session(tmp_path, start_if_missing=True)
    m.denied = True
    with pytest.raises(SessionError) as ex:
        with s:
            pass
    assert ex.value.state == 'INSTANCE_CONFLICT' and not p.opens and not m.starts


def test_multiple_cleanly_selected_by_endpoint(tmp_path):
    s, m, _ = setup_session(tmp_path, m=Machine([I, J], [J.pid]))
    with s:
        assert s.instance == J and m.starts == 0


def test_launch_only_when_opted_in_and_after_preflight(tmp_path):
    calls = []
    s, m, p = setup_session(tmp_path, m=Machine([], ()), start_if_missing=True, doctorfn=calls.append)
    with s:
        assert s.last['mode'] == 'started'
    assert m.starts == 1 and len(calls) == 1
    assert m.instances() == [I] and p.closes == 1


def test_external_start_during_preflight_is_reused(tmp_path):
    m = Machine([], ())
    def appeared(c):
        m.arr, m.owners = [J], {J.pid}
    s, _, _ = setup_session(tmp_path, m=m, start_if_missing=True, doctorfn=appeared)
    with s:
        assert s.last['mode'] == 'attached' and s.instance == J
    assert m.starts == 0


def test_stale_receipt_same_pid_is_dead(tmp_path):
    s, m, p = setup_session(tmp_path, pid=I.pid, expected_born=I.born-1)
    with pytest.raises(SessionError) as ex:
        with s:
            pass
    assert ex.value.code == 20 and ex.value.details['cause'] == 'unknown'
    assert m.starts == p.opens == 0


def test_resume_original_instance(tmp_path):
    s, m, _ = setup_session(tmp_path, pid=I.pid, expected_born=I.born, expect_scene='图例')
    with s:
        assert s.last['instance']['created_epoch_s'] == I.born and not m.starts


def test_refresh_with_wrong_scene_fails_before_success(tmp_path):
    s, m, p = setup_session(tmp_path, expect_scene='另一个场景')
    with pytest.raises(SessionError) as ex:
        with s:
            pass
    assert ex.value.state == 'SCENE_MISMATCH' and p.closes == 1 and not m.starts


def test_dead_with_replacement_never_reconnects(tmp_path):
    s, m, p = setup_session(tmp_path)
    with s:
        m.arr, m.owners = [J], {J.pid}
        with pytest.raises(SessionError) as ex:
            s.hold(10)
        assert ex.value.state == 'DEAD' and ex.value.details['listener_pids_at_detection'] == [J.pid]
        assert ex.value.details['cause'] == 'unknown' and p.opens == 1 and not m.starts


def test_client_failure_not_reported_as_gui_death(tmp_path):
    s, _, p = setup_session(tmp_path)
    with s:
        p.ok = False
        with pytest.raises(SessionError) as ex:
            s.hold(10)
        assert ex.value.code == 42 and ex.value.details['alive']


def test_inflight_timeout_releases_session(tmp_path):
    s, m, p = setup_session(tmp_path)
    with s:
        p.failure = SessionError(10, 'TIMEOUT', '超时')
        with pytest.raises(SessionError) as ex:
            s.refresh()
        assert ex.value.details['alive'] and ex.value.code == 10
        with pytest.raises(RuntimeError):
            s.refresh()
    assert p.closes == 1 and m.instances() == [I]
    assert lock_info(tmp_path/'atkcli.lock')['held'] is False


def test_cooperative_wait_does_not_contact_sdk_while_locked(tmp_path):
    s, m, p = setup_session(tmp_path, wait_lock=.015, start_if_missing=True)
    lease = Lease(tmp_path/'atkcli.lock', {'label': '其他会话'}).acquire(0)
    try:
        with pytest.raises(SessionError) as ex:
            with s:
                pass
        assert ex.value.code == 40 and ex.value.details['owner']['label'] == '其他会话'
        assert m.starts == p.opens == 0
    finally:
        lease.release()


def test_passive_watch_and_status_never_label_responsive(tmp_path):
    events = []
    m = Machine([I], [])
    assert status(_machine=m, _lock=tmp_path/'never.lock')['state'] == 'PROCESS_ONLY'
    assert not (tmp_path/'never.lock').exists()
    result = watch(duration=0, on_event=events.append, _machine=m)
    assert result['responsive'] is None and events[0]['cpu_s'] == 105.25
    m.arr = []
    with pytest.raises(SessionError) as ex:
        watch(duration=0, _machine=m)
    assert ex.value.code == 21


@pytest.mark.parametrize('args', [{'port': 0}, {'pid': -1}, {'wait_lock': float('inf')},
                                   {'timeout': float('nan')}, {'timeout': 0}, {'expected_s': -1},
                                   {'expected_born': 100}, {'expected_born': float('nan'), 'pid': 2},
                                   {'start_if_missing': True, 'expected_born': 100, 'pid': 2}])
def test_invalid_config(args):
    with pytest.raises(ValueError):
        Config(**args)


def test_native_call_hang_has_bounded_wait_and_no_lingering_client(tmp_path):
    # 以一个只阻塞的独立 Python 客户端代替动态库；完全不触碰 ATK。
    class Blocked(Channel):
        def __init__(self, home, port):
            self._start([getattr(sys, '_base_executable', None) or sys.executable,
                         '-I', '-c', 'import time; time.sleep(30)'])
            created.append(self)
    created = []
    m = Machine()
    t0 = time.monotonic()
    with pytest.raises(SessionError) as ex:
        with Session(Config(timeout=.07), _machine=m, _channel=Blocked, _lock=tmp_path/'lease'):
            pass
    assert ex.value.code == 10 and ex.value.details['alive']
    assert m.instances() == [I] and created[0].proc.poll() is not None
    assert not created[0].reader.is_alive() and time.monotonic()-t0 < 5


def test_live_process_identity_from_os():
    import psutil
    proc = psutil.Process()
    m = LocalMachine()
    inst = Instance(proc.pid, proc.create_time(), proc.exe(), proc.username())
    assert m.sample(inst)['alive']
    with pytest.raises(SessionError) as ex:
        m.sample(Instance(proc.pid, inst.born-1, inst.exe, inst.user))
    assert ex.value.code == 20
    with pytest.raises(SessionError) as ex:
        m.validate_identity(inst, Path('different'))
    assert ex.value.code == 41


@pytest.mark.parametrize('raw,expected', [('/Scenario/示例 /Scenario/示例/Facility/G', '示例'),
                                           ('/Scenario/Alpha Beta /Scenario/Alpha Beta/Other', None),
                                           ('/Scenario/A /Scenario/B', None), ('NACK', None), ('', None)])
def test_scene_display_name_not_guessed(raw, expected):
    assert _scene_name(raw) == expected
