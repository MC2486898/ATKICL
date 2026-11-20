"""在独立进程中以替身模块测试公开 SDK 调用顺序。没有网络连接，也不启动应用。"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

from atkcli.channel import Channel
from atkcli.errors import SessionError


pytestmark = pytest.mark.skipif(os.name != 'nt' or sys.version_info[:2] != (3, 12) or sys.maxsize <= 2**32,
                                reason='厂商 Windows CPython 3.12 x64 SDK 适配层')


@pytest.fixture
def sdk(tmp_path):
    home = tmp_path/'模拟安装目录'
    sdk = home/'IntegratingWithATK'/'connect'/'Python'
    sdk.mkdir(parents=True)
    (home/'ATK.exe').touch()
    for dep in ('_ATKConnectPython.pyd','ATKConnectorDll.dll'):
        (sdk/dep).touch()  # 仅供 SDK 安装目录存在性检查使用；不会加载这些空文件。
    (sdk/'ATKConnectPython.py').write_text('''from pathlib import Path
here=Path(__file__).parent

def mark(s):
    with (here/'calls.txt').open('a', encoding='utf-8') as f:
        f.write(s+'\\n')
def atkOpen(addr,port):
    mark(f'open {addr}:{port}')
    return 11

def atkConnect(con,cmd,arg):
    assert con==11
    mark(cmd+' '+arg)
    if (here/'refuse').exists(): return 'NACK'
    return {('GetATKVersion','/ Platform'):'AMD64', ('CheckScenario','/'):'1',
            ('AllInstanceNames','/'):'/Scenario/演示 /Scenario/演示/Facility/GS'}[(cmd,arg)]

def atkClose(con): mark('close '+str(con))
''', encoding='utf-8')
    return home, sdk


def test_real_client_ipc_holds_one_sdk_handle_and_closes(sdk):
    home, directory = sdk
    channel = Channel(home, 6655)
    try:
        for n in (0,1):
            if n:
                channel.refresh()
            resp = channel.receive(time.monotonic()+5, lambda: None)
            assert resp == {'state':'READY','platform':'AMD64','scene_open':True,'scene_name':'演示'}
        assert channel.running()
    finally:
        channel.close()
    assert (directory/'calls.txt').read_text(encoding='utf-8').splitlines() == [
        'open 127.0.0.1:6655', *(['GetATKVersion / Platform','CheckScenario /','AllInstanceNames /']*2), 'close 11']
    assert not channel.running()


def test_nack_is_not_attach_success(sdk):
    home, directory = sdk
    (directory/'refuse').touch()
    channel = Channel(home, 6655)
    try:
        with pytest.raises(SessionError) as ex:
            channel.receive(time.monotonic()+5, lambda: None)
        assert ex.value.state == 'INVALID_REPLY'
    finally:
        channel.close()
    assert 'close 11' in (directory/'calls.txt').read_text(encoding='utf-8')


def test_doctor_does_not_open_connection(sdk):
    home, directory = sdk
    channel = Channel(home, 6655, 'doctor')
    try:
        assert channel.receive(time.monotonic()+5, lambda: None) == {'state':'SDK_OK'}
    finally:
        channel.close()
    assert not (directory/'calls.txt').exists()
