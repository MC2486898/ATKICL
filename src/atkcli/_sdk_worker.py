"""仅在独立子进程内调用厂商阻塞式 SDK。stdin 接受 refresh 与逐条脚本命令。"""
import json
import os
import sys
import time
from pathlib import Path

REPLY_LIMIT = 20000
COMMAND_LIMIT = 128
ARGUMENT_LIMIT = 4000


def _scene_name(raw):
    # 无引号的含空格名称与分隔符有歧义；不返回截断名。
    paths = str(raw or '').split()
    if not paths or not all(p.startswith('/Scenario/') for p in paths):
        return None
    names = {p[len('/Scenario/'):].partition('/')[0] for p in paths}
    if len(names) == 1:
        name = names.pop()
        if (f'/Scenario/{name}' in paths and name.isprintable() and 0 < len(name) <= 160
                and not any(c in name for c in '\",;')):
            return name
    return None


def _report(con, atkConnect, reply):
    platform = str(atkConnect(con, "GetATKVersion", "/ Platform") or "").strip()
    scene = str(atkConnect(con, "CheckScenario", "/") or "").strip()
    if (not platform or not platform.isprintable() or len(platform) > 100
            or platform.upper() in {"NACK", "0", "1", "NULL"} or scene not in {"0", "1"}):
        reply("INVALID_REPLY", message="ATK 未返回有效的平台信息和场景状态。")
        return False
    name = None
    if scene == "1":
        # 仅公开场景根名称，不输出对象清单，也不把显示名当成磁盘路径。
        name = _scene_name(atkConnect(con, "AllInstanceNames", "/"))
    reply("READY", platform=platform, scene_open=scene == "1", scene_name=name)
    return True


def _step(con, atkConnect, reply, op):
    command = op.get("command")
    argument = op.get("argument", "")
    if (not isinstance(command, str) or not isinstance(argument, str)
            or not command or not command.isprintable() or len(command) > COMMAND_LIMIT
            or not argument.isprintable() or len(argument) > ARGUMENT_LIMIT
            or "\n" in command or "\n" in argument):
        reply("SCRIPT_ERROR", message="脚本命令行不合法：命令 1–128 字符，参数不超过 4000 字符。")
        return
    t0 = time.monotonic()
    try:
        out = atkConnect(con, command, argument)
        text = "" if out is None else str(out)
        if len(text) > REPLY_LIMIT:
            text = text[:REPLY_LIMIT] + f"\n…（已截断，原长度 {len(text)} 字符）"
        reply("RESULT", command=command, argument=argument, reply=text,
              nack=text.strip().upper().startswith("NACK"),
              elapsed_s=round(time.monotonic() - t0, 3))
    except Exception as exc:
        reply("RESULT", command=command, argument=argument,
              reply=f"{type(exc).__name__}: {exc}", nack=True,
              elapsed_s=round(time.monotonic() - t0, 3))


def run(home: Path, port: int, doctor: bool, reply):
    sdk = home / "IntegratingWithATK" / "connect" / "Python"
    if sys.platform != "win32" or sys.version_info[:2] != (3, 12) or sys.maxsize <= 2**32:
        reply("SDK_UNAVAILABLE", message="此版本的实时接管需要 Windows x64 与 CPython 3.12。")
        return
    for p in (home / "ATK.exe", sdk / "ATKConnectPython.py", sdk / "_ATKConnectPython.pyd",
              sdk / "ATKConnectorDll.dll"):
        if not p.is_file():
            reply("SDK_UNAVAILABLE", message=f"安装缺少文件：{p.name}")
            return
    # -I -B 启动：忽略用户 PYTHONPATH，不向厂商目录写入字节码缓存。
    with os.add_dll_directory(str(sdk)), os.add_dll_directory(str(home)):
        sys.path.insert(0, str(sdk))
        from ATKConnectPython import atkOpen, atkConnect, atkClose
        if doctor:
            reply("SDK_OK")
            return
        con = atkOpen("127.0.0.1", port)
        if not isinstance(con, int) or con < 0:
            reply("OPEN_FAILED", message="官方 SDK 未建立 Connect 连接。")
            return
        try:
            if not _report(con, atkConnect, reply):
                return
            while True:
                raw = sys.stdin.buffer.readline(64 * 1024)
                if not raw:
                    break
                line = raw.strip()
                if not line:
                    continue
                if line == b"refresh":
                    if not _report(con, atkConnect, reply):
                        return
                    continue
                try:
                    op = json.loads(line)
                except ValueError:
                    reply("SCRIPT_ERROR", message="脚本请求不是有效 JSON。")
                    continue
                if not isinstance(op, dict) or op.get("op") != "step":
                    reply("SCRIPT_ERROR", message="不支持的脚本请求。")
                    continue
                _step(con, atkConnect, reply, op)
        finally:
            atkClose(con)


def main(argv=None):
    """工作进程入口。打包后由主程序以 --sdk-worker 参数再次进入。"""
    args = list(sys.argv[1:] if argv is None else argv)
    # 厂商动态库可能直接写 stdout。协议使用独立句柄，避免将原生输出混入 JSON。
    with os.fdopen(os.dup(1), "wb", buffering=0) as channel, open(os.devnull, "wb") as sink:
        os.dup2(sink.fileno(), 1)
        os.dup2(sink.fileno(), 2)

        def reply(state, **data):
            channel.write(json.dumps({"state": state, **data}, ensure_ascii=True).encode("ascii") + b"\n")

        try:
            run(Path(args[0]), int(args[1]), args[2] == "doctor", reply)
        except Exception as exc:
            reply("SDK_ERROR", message=f"{type(exc).__name__}: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
