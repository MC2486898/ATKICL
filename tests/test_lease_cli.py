"""跨进程锁和 CLI 输入/输出契约。仅使用测试目录。"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from atkcli.cli import main
from atkcli.errors import SessionError
from atkcli.files import output_path, receipt, write_json_new
from atkcli.lease import Lease, lock_info


@pytest.mark.parametrize('terminate', [True, False])
def test_cross_process_lock_survives_normal_and_abrupt_exit(tmp_path, terminate):
    lock = tmp_path/'atkcli.lock'
    root = Path(__file__).resolve().parents[1]/'src'
    cmd = """import sys
from pathlib import Path
from atkcli.lease import Lease
l = Lease(Path(sys.argv[1]), {'label': 'other-client'}).acquire(0)
print('ready', flush=True)
sys.stdin.readline()
l.release()
"""
    p = subprocess.Popen([getattr(sys, '_base_executable', None) or sys.executable, '-u', '-c', cmd, str(lock)],
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         env={**os.environ, 'PYTHONPATH': os.pathsep.join([str(root), *sys.path]), 'PYTHONUTF8':'1'})
    try:
        assert p.stdout.readline().strip() == b'ready'
        info = lock_info(lock)
        assert info['held'] and info['owner']['client_pid'] == p.pid
        with pytest.raises(SessionError) as ex:
            Lease(lock, {}).acquire(0)
        assert ex.value.code == 40
        if terminate:
            p.terminate()  # 本测试创建的假客户端，并非 ATK。
        else:
            p.stdin.write(b'\n'); p.stdin.flush()
        p.wait(timeout=5)
        assert not lock_info(lock)['held']
        second = Lease(lock, {'label': 'next-client'}).acquire(.2)
        second.release()
        assert lock_info(lock)['last_record']['released_utc']
    finally:
        if p.poll() is None:
            p.kill()
        p.communicate(timeout=5)


def test_unknown_lock_never_erased(tmp_path):
    path = tmp_path/'atkcli.lock'
    original = b'other tool owns this file'
    path.write_bytes(original)
    with pytest.raises(SessionError) as ex:
        Lease(path, {}).acquire(0)
    assert ex.value.state == 'UNKNOWN_LOCK_FILE' and path.read_bytes() == original
    assert lock_info(path)['held'] is None


def test_output_publish_without_replace(tmp_path):
    p = tmp_path/'result.json'
    write_json_new(p, {'seq':1})
    with pytest.raises(FileExistsError):
        write_json_new(p, {'seq':2})
    assert json.loads(p.read_bytes()) == {'seq':1}
    assert list(tmp_path.iterdir()) == [p]


def test_conflicting_outputs_and_input(tmp_path):
    p = tmp_path/'original.atk'
    p.write_bytes(b'preserve')
    for dst in (p, tmp_path/'another.atk'):
        with pytest.raises(ValueError):
            output_path(dst, ('.json',), [p])
    assert p.read_bytes() == b'preserve'


def test_cli_dry_run_creates_nothing(tmp_path,monkeypatch,capsys):
    monkeypatch.setattr('atkcli.cli.Session', lambda *a, **k: pytest.fail('dry-run 不应接管实例'))
    args = ['instance', 'attach', '--out', str(tmp_path/'receipt.json'), '--log', str(tmp_path/'listen.log')]
    for cmd in ([*args, '--dry-run'], ['--dry-run', *args, '--start-if-missing']):
        assert main(cmd) == 0
        result = json.loads(capsys.readouterr().out)
        assert result['ok'] and result['state'] == 'DRY_RUN'
    assert list(tmp_path.iterdir()) == []


def test_receipt_is_data_not_launch_instruction(tmp_path):
    p = tmp_path/'receipt.json'
    doc = {'ok': True, 'state':'READY', 'schema':'atkcli.session/1','port':6655,
           'instance':{'pid':76,'created_epoch_s':2345.25, 'exe':'not-used.exe'},
           'atk_home':'not-used', 'host':'not-used', 'start_if_missing': True}
    p.write_text(json.dumps(doc))
    assert receipt(p) == (76, 2345.25, 6655)
    from atkcli.cli import parser,config
    cfg = config(parser().parse_args(['instance','attach','--resume',str(p)]))
    assert cfg.expected_born == 2345.25 and not cfg.start_if_missing
    assert str(cfg.home) != 'not-used'
    with pytest.raises(ValueError):
        config(parser().parse_args(['instance','attach','--resume',str(p),'--pid','99']))
    for invalid in ({**doc,'ok':False},{**doc,'instance':{}},{'ok':True},[doc]):
        p.write_text(json.dumps(invalid))
        with pytest.raises(ValueError):
            receipt(p)


@pytest.mark.parametrize('args', [['instance','attach','--hold','nan'], ['instance','watch','--duration','-1'],
                                 ['reports','cadence','--input','no.csv','--step','inf'],
                                 ['instance','attach','--resume','example.json','--start-if-missing'],
                                 ['instance','attach','--port','99999'], ['bogus']])
def test_invalid_cli_is_structured(args, capsys):
    assert main(args) == 2
    assert not json.loads(capsys.readouterr().out)['ok']
