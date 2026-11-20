"""atkcli 命令行。标准输出始终保留给最终 JSON；心跳可写入独立的 JSONL。"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import __version__
from .errors import SessionError
from .files import json_text, output_path, read_text, receipt, write_json_new
from .lease import iso
from .session import Config, Session, default_home, doctor, seconds, status, watch


ACTIONS = {
    'doctor': '在隔离子进程内导入本机官方 SDK，不打开 ATK',
    'status': '只读检查进程、端口归属和协作锁，不连接 ATK',
    'attach': '取得锁，核对实例身份，建立只读 Connect 连接；退出时释放锁和连接',
    'watch': '只监控原进程的创建时间、存活与 CPU，不连接 ATK',
    'parse': '将已有 UTC 报表的编号明细与统计区分开，输出 JSON',
    'cadence': '核验已有 UTC 数据文件的实际采样间隔',
    'compare': '比较两个已保存 XML 文件的指定叶节点，不写入场景',
    'run': '取得锁后逐条发送 Connect 命令；会修改场景，需已有实例',
}


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def parser():
    p = Parser(prog='atkcli', description='ATK 本机会话接管与离线数据校验')
    p.add_argument('--version', action='version', version=f'atkcli {__version__}')
    p.add_argument('--dry-run', action='store_true', help='只输出动作，不接触现场')
    top = p.add_subparsers(required=True, dest='group')
    dg = top.add_parser('doctor', help='SDK 环境自检')
    i = top.add_parser('instance', help='本地 ATK 实例').add_subparsers(required=True, dest='op')
    r = top.add_parser('reports', help='读取已导出的报表').add_subparsers(required=True, dest='op')
    x = top.add_parser('xml', help='核对已保存 XML').add_subparsers(required=True, dest='op')
    sc = top.add_parser('script', help='逐条发送 Connect 命令（会修改场景）'
                        ).add_subparsers(required=True, dest='op')
    st = i.add_parser('status', help='被动发现（不建立 Connect 连接）')
    at = i.add_parser('attach', help='接管同账户已有实例，默认不启动、不切场景')
    wa = i.add_parser('watch', help='观察同一进程，适合界面不响应时使用')
    rp = r.add_parser('parse', help='解析已有的中/英文 UTC 区间报表')
    rc = r.add_parser('cadence', help='核验已有采样文件的真实步长')
    xc = x.add_parser('compare', help='比较两份文件，不调用 ATK')
    sr = sc.add_parser('run', help='执行脚本文件，逐条发送 Connect 命令')
    for leaf in (dg, st, at, wa, rp, rc, xc, sr):
        leaf.add_argument('--dry-run', action='store_true', default=argparse.SUPPRESS, help='只输出动作')
        leaf.add_argument('--out', type=Path, help='新 JSON 文件；也在标准输出给出结果')
    for leaf in (dg, at, wa, sr):
        leaf.add_argument('--atk-home', type=Path, default=None, help='本机 ATK 安装目录；默认取 ATK_HOME')
    for leaf in (st, at, wa, sr):
        leaf.add_argument('--port', type=int, help='本机 Connect 端口；默认 6655')
        leaf.add_argument('--pid', type=int, help='指定本机 ATK 进程')
    for leaf in (dg, at, sr):
        leaf.add_argument('--timeout', type=float, default=15, help='SDK/端口就绪等待上限，秒')
    for leaf in (at, wa):
        leaf.add_argument('--log', type=Path, help='新的 .jsonl 或 .log 文件，每 30 秒记录心跳')
        leaf.add_argument('--resume', type=Path, help='从成功回执识别原实例，不恢复仿真任务')
    at.add_argument('--start-if-missing', action='store_true',
                    help='显式允许在无实例、无端口冲突时启动空 GUI；已有实例一律复用')
    at.add_argument('--hold', type=float, default=0, metavar='SECONDS', help='握手后保持连接和锁的秒数')
    at.add_argument('--wait-lock', type=float, default=0, metavar='SECONDS', help='等待其他 atkcli 释放锁的上限')
    at.add_argument('--expect-scene', help='限制当前场景显示名；无法核实或不符即退出')
    at.add_argument('--label', default='', help='写入锁的占用说明，可供其他会话查看')
    wa.add_argument('--duration', type=float, default=60, help='监控时长（秒）')
    sr.add_argument('--input', type=Path, required=True, help='脚本文本文件；每行一条命令')
    sr.add_argument('--encoding', choices=['auto', 'utf-8-sig', 'utf-16', 'gb18030'], default='auto')
    sr.add_argument('--command-timeout', type=float, default=30, metavar='SECONDS',
                    help='单条命令的等待上限，秒')
    sr.add_argument('--continue-on-error', action='store_true', help='某条命令失败后仍继续执行')
    sr.add_argument('--wait-lock', type=float, default=0, metavar='SECONDS', help='等待其他 atkcli 释放锁的上限')
    sr.add_argument('--label', default='', help='写入锁的占用说明')
    sr.add_argument('--expect-scene', help='限制当前场景显示名；不符即退出')
    sr.add_argument('--start-if-missing', action='store_true',
                    help='显式允许在无实例时启动空 GUI，再执行脚本')
    sr.add_argument('--log', type=Path, help='新的 .jsonl 事件日志')
    for leaf in (rp, rc):
        leaf.add_argument('--input', type=Path, required=True, help='已导出的本地文本或 CSV')
        leaf.add_argument('--encoding', choices=['auto', 'utf-8-sig', 'utf-16', 'gb18030'], default='auto')
    rc.add_argument('--step', type=float, help='要求的固定步长（秒）；会核对所有相邻间隔')
    xc.add_argument('--before', type=Path, required=True, help='期望值文件')
    xc.add_argument('--after', type=Path, required=True, help='从软件另存后的回读文件')
    xc.add_argument('--path', action='append', required=True, help='相对 XML 叶节点路径，可多次指定')
    xc.add_argument('--numeric', action='store_true', help='按有限十进制数值及分量数比较')
    return p


def config(a):
    pid = getattr(a, 'pid', None)
    port = getattr(a, 'port', None)
    born = None
    resume = getattr(a, 'resume', None)
    if resume is not None:
        if getattr(a, 'start_if_missing', False):
            raise ValueError('--resume 仅允许接回原实例，不能与 --start-if-missing 同用。')
        if not a.dry_run:
            saved_pid, born, saved_port = receipt(resume)
            if (pid is not None and pid != saved_pid) or (port is not None and port != saved_port):
                raise ValueError('回执与显式给定的 --pid 或 --port 冲突。')
            pid, port = saved_pid, saved_port
    hold = seconds(getattr(a, 'hold', 0), '--hold')
    if pid is not None and getattr(a, 'start_if_missing', False):
        raise ValueError('指定原实例 --pid 时不允许启动另一个实例。')
    if port is None:
        port = int(os.environ.get('ATK_PORT', '6655'))
    handshake = getattr(a, 'timeout', 15)
    estimated = hold + handshake * (2 if hold else 1)
    return Config(home=getattr(a, 'atk_home', None) or default_home(), port=port, pid=pid,
                  timeout=handshake, wait_lock=getattr(a, 'wait_lock', 0),
                  start_if_missing=getattr(a, 'start_if_missing', False), label=getattr(a, 'label', ''),
                  expected_s=estimated, expected_born=born, expect_scene=getattr(a, 'expect_scene', None))


def execute(a):
    op = getattr(a, 'op', a.group)
    if op == 'cadence' and a.step is not None:
        seconds(a.step, '--step', positive=True)
    if op == 'watch':
        seconds(a.duration, '--duration')
    if op == 'run':
        seconds(a.command_timeout, '--command-timeout', positive=True)
    c = config(a) if a.group in ('doctor', 'instance', 'script') else None
    if a.dry_run:
        payload = {'ok': True, 'state': 'DRY_RUN', 'action': ACTIONS[op],
                   'start_if_missing': bool(c and c.start_if_missing),
                   'out': str(a.out) if a.out is not None else None}
        if op == 'run':
            from .scripting import parse_script
            payload['commands'] = [{'command': cmd.command, 'argument': cmd.argument,
                                    'sleep_s': cmd.sleep_s}
                                   for cmd in parse_script(read_text(a.input, a.encoding))]
        return payload, 0

    inputs = [getattr(a, name, None) for name in ('input', 'before', 'after', 'resume')]
    log = getattr(a, 'log', None)
    output_path(a.out, ('.json',), inputs)
    output_path(log, ('.log', '.jsonl'), inputs + [a.out])
    logf = log.open('x', encoding='utf-8') if log is not None else None

    def event(obj):
        if logf:
            logf.write(json_text(obj) + '\n')
            logf.flush()

    try:
        event({'event': 'begin', 'action': op, 'utc': iso()})
        if op == 'doctor':
            result = doctor(c)
        elif op == 'status':
            result = status(c)
        elif op == 'attach':
            with Session(c, on_event=event) as sess:
                sample = sess.hold(a.hold)
                result = sess.refresh() if a.hold else dict(sess.last)
                result.update(hold_s=a.hold, **sample)
        elif op == 'watch':
            result = watch(c, duration=a.duration, on_event=event)
        elif op == 'run':
            from .scripting import run_script
            result = run_script(c, read_text(a.input, a.encoding), timeout=a.command_timeout,
                                continue_on_error=a.continue_on_error, on_event=event)
        elif op == 'compare':
            from .xmlcheck import compare_xml
            result = compare_xml(a.before, a.after, a.path, a.numeric)
        else:
            from .reports import check_cadence, parse_report
            text = read_text(a.input, a.encoding)
            result = parse_report(text) if op == 'parse' else check_cadence(text, a.step)
        code = result.get('exit_code', 0 if result['ok'] else 31)
        if a.out is not None:
            write_json_new(a.out, result)
        event({'event': 'end', 'utc': iso(), 'exit_code': code})
        return result, code
    except SessionError as exc:
        event({'event': 'error', 'utc': iso(), **exc.as_dict()})
        raise
    except KeyboardInterrupt:
        event({'event': 'interrupted', 'utc': iso(), 'exit_code': 130})
        raise
    finally:
        if logf:
            logf.close()


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ['--sdk-worker']:
        from ._sdk_worker import main as worker_main
        return worker_main(args[1:])
    try:
        result, code = execute(parser().parse_args(args))
    except SessionError as exc:
        result, code = exc.as_dict(), exc.code
    except KeyboardInterrupt:
        result, code = {'ok': False, 'state': 'INTERRUPTED', 'message': '客户端已中断。'}, 130
    except (ValueError, OSError) as exc:
        result, code = {'ok': False, 'state': 'INPUT_OR_IO_ERROR', 'message': str(exc)}, 2
    text = json_text(result) + '\n'
    if hasattr(sys.stdout, 'buffer'):
        sys.stdout.buffer.write(text.encode('utf-8'))
        sys.stdout.buffer.flush()
    else:
        sys.stdout.write(text)
    return code
