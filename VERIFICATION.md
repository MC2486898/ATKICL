# atkcli 0.1.0 验证记录

验证日期：2026-11-20。环境：Windows、项目虚拟环境 CPython 3.12.15、psutil 7.2.2。本记录区分了真实安装、使用测试替身的接口验收和未做的现场操作。

| 层次 | 执行和结果 | 证据 |
|---|---|---|
| 本机安装 | 从本地 wheel 离线安装到项目 `.venv`。从源码树以外的临时目录运行实际的 `atkcli.exe`。包内 12 个 Python 模块和安装后文件逐字节一致。 | `verification/smoke.json` 含 wheel 摘要、版本和命令输出。 |
| 官方 SDK | 已通过真实 `ATKConnectPython` 的导入检查；`doctor` 返回 `SDK_OK`、`connected: false`。 | `verification/smoke.json` → `doctor`。 |
| 本机进程发现 | 终验时 `instance status` 返回 `ABSENT`。默认 `instance attach` 返回 `ABSENT`、退出码 21，没有启动 GUI。 | `verification/smoke.json` → `passive_status`、`attach_if_absent`。 |
| 会话接口与进程边界 | 测试替身验证复用同一 SDK 句柄、读平台/场景、关闭连接；真实子进程用于验证锁争用、持锁者异常退出和客户端阻塞回收。测试覆盖原 PID 消失或复用与连接超时的区分、所有者不符、回执过期、已有实例时禁止再启动。 | `tests/test_session.py`、`test_sdk_bridge.py`、`test_lease_cli.py`。 |
| 离线校验 | 中英文合成报表各两条明细，统计中的日期对没有被重复计数。样例实际步长 60 秒，要求 60 秒时通过，要求 1 秒时退出 31。显示参数的十进制数值回读一致。 | `examples/`；`verification/smoke.json` 中对应的 5 项验收。 |
| 回归测试 | **94 项测试通过，另有 119 个子用例通过**。包含非 UTC 表头、截断列、互相矛盾的明细与统计、缺少 BOM 的 UTF-16 声明等失败用例。 | `verification/pytest.txt`。

最终 wheel：`atkcli-0.1.0-py3-none-any.whl`；SHA-256：`2e64ddbbe6ea5a241d6c626125025d7e8da68f64d4d6bcab4b28f9612895abe4`。

**未做：** 没有对正在运行的真实 GUI 做端到端接管，没有关闭或替换用户实例，没有启动任务计算。这些不能从 `doctor`、离线测试或示例输出推断为已经通过。

## 有空闲窗口时的现场接管验收

1. 在可用时段手工打开 ATK；在项目根设置 PowerShell 变量 `$cli = '.\.venv\Scripts\atkcli.exe'`。
2. 运行 `& $cli instance status`，记下 PID，并核对 `state` 为 `LISTENING`。
3. 运行 `& $cli instance attach --pid <上步的PID> --hold 10 --timeout 20 --out gui-session.json`。使用尚不存在的输出文件。成功应返回同一 PID、`state: READY`、`mode: attached`，原窗口仍保留。
4. 如需验证排队，在一个终端用 `--hold 60` 持锁，看到另一终端的 `instance status` 显示 `lock.held: true` 后，再运行默认 `instance attach`。预期第二个终端退出码 40，不启动其他 GUI。持锁终端正常退出后可重新接管。

项目根再次运行 `.\.venv\Scripts\python.exe -m pytest tools/atkcli/tests -q` 可复跑自动测试，不需要启动 ATK。

ClawsGO Science Agent
