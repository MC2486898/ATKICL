<div align="center">

# ATK 会话工具

**接管本机已运行的 ATK：查询状态、保持会话、监控进程、执行 Connect 脚本，并离线校验导出文件。**

<<<<<<< HEAD
![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![Platform](https://img.shields.io/badge/Platform-Windows%20x64-0078D4?logo=windows&logoColor=white)
=======
![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python\&logoColor=white)
![Platform](https://img.shields.io/badge/Platform-Windows%20x64-0078D4?logo=windows\&logoColor=white)
>>>>>>> 2e0d3c6d81e80c6e0b5d95c58449808cb8dc96da
![Version](https://img.shields.io/badge/Version-0.1.0-2ea44f)
![Status](https://img.shields.io/badge/Status-beta-orange)
![Tests](https://img.shields.io/badge/Tests-94%20passed-brightgreen)
![License](https://img.shields.io/badge/License-MIT-yellow)

[简介](#简介) · [特性](#特性) · [快速开始](#快速开始) · [使用指南](#使用指南) · [常见问题](#常见问题) · [许可证](#许可证)

</div>

![主界面](docs/images/gui.png)

## 简介

ATK 的一次仿真往往要跑很久。中途想知道“软件还活着吗”“当前是不是我刚才那个场景”，
直接再开一个 Connect 客户端就会和已有连接、已有脚本争抢同一个实例；
任务卡住时，又很难区分是进程仍在计算，还是进程已经消失。

`atkcli` 把这类操作收敛成一套有边界的流程：**先核验进程身份，再获取实例锁，最后才握手**。
只读查询随时可用；会修改场景的命令只能通过脚本显式执行，并且需要二次确认。
已经导出的报表、采样序列和 XML 可以完全离线校验，不依赖 ATK 在线。

项目同时提供图形界面和命令行，两者共用同一套会话、锁与校验逻辑。

## 特性

- **双入口**：Tkinter 图形界面 + 完整命令行，行为一致
- **身份核验**：进程名、可执行文件路径、当前账户、PID、创建时间与端口归属逐项核对
- **独占租约**：`%TEMP%\atkcli.lock` 文件锁，支持有界排队，异常退出由系统释放
- **只读优先**：状态、平台版本、场景名查询不会修改场景
- **可停止的监控**：约 0.4 秒探活，进程消失立即报告 `DEAD`，不受 SDK 阻塞影响
- **Connect 脚本**：逐条执行并返回 `RESULT` / `NACK` / 耗时，支持 `sleep` 与单条超时
- **离线校验**：报表解析、采样间隔核验、XML 数值比较
- **自动化友好**：统一 JSON 输出与语义化退出码，`--dry-run` 只预览不执行

## 界面预览

实例页负责 SDK 自检、状态刷新、接管/保持与进程监控。

![实例页](docs/images/gui.png)

脚本页用于编辑并执行 Connect 脚本，逐条查看返回结果与耗时。

![脚本页](docs/images/script.png)

> 脚本页和 `script run` 是仅有的会修改场景的入口。
> `New` / `Load` / `Unload` / `Save` 可能覆盖文件，执行前请先备份。

## 快速开始

### 环境要求

<<<<<<< HEAD
| 场景 | 要求 |
|---|---|
| 接管/监控真实实例 | Windows x64、CPython 3.12、ATK 4.2.0-rc.2 |
| 仅离线校验 | Python 3.11+ |
| Python 依赖 | `psutil>=6,<8` |
=======
| 场景        | 要求                                      |
| --------- | --------------------------------------- |
| 接管/监控真实实例 | Windows x64、CPython 3.12、ATK 4.2.0-rc.2 |
| 仅离线校验     | Python 3.11+                            |
| Python 依赖 | `psutil>=6,<8`                          |
>>>>>>> 2e0d3c6d81e80c6e0b5d95c58449808cb8dc96da

实时接管必须使用 CPython 3.12，这是厂商 SDK 的限制；仅做离线校验时 3.11 也可运行。

### 从源码安装

```powershell
git clone https://github.com/MC2486898/atkcli.git
cd atkcli
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
atkcli doctor
```

`doctor` 只导入 SDK，不启动 GUI、不建立 TCP 连接。
ATK 默认目录为 `D:\ATK-4.2.0-rc.2`，可用 `--atk-home` 或环境变量 `ATK_HOME` 覆盖；
端口默认 `127.0.0.1:6655`，可用 `--port` 或 `ATK_PORT` 修改。

### 第一次接管

```powershell
atkcli instance status
atkcli instance attach --timeout 20 --out session.json
```

`attach` 成功后返回包含 PID、创建时间、场景名与握手时间的 JSON。
`--resume session.json` 只接回回执里的那一个进程；原进程已退出时返回 `DEAD`，不会跟随新实例。

### 打开图形界面

```powershell
python -m atkcli.gui
```

也可以直接运行打包目录中的 `ATK会话工具.exe`。

## 使用指南

### 图形界面

<<<<<<< HEAD
| 页签 | 用途 |
|---|---|
| 实例 | SDK 自检、状态刷新、接管/保持、进程监控、启动空 GUI |
| 报表与采样 | 解析地面站报表、核验采样间隔 |
| XML 比较 | 按路径比较两份 XML 的叶节点数值 |
| 脚本 | 编辑并执行 Connect 脚本，查看逐条结果 |
| 说明 | 版本、边界与退出码 |

### 命令行

| 命令 | 说明 |
|---|---|
| `atkcli doctor` | 导入 SDK 自检 |
| `atkcli instance status` | 被动快照：进程、端口监听与锁 |
| `atkcli instance attach` | 核验身份后接管，可保持指定时长 |
| `atkcli instance watch` | 仅用操作系统探活，不建立 Connect |
| `atkcli reports parse` | 解析已导出的地面站报表 |
| `atkcli reports cadence` | 核验相邻时间戳的实际步长 |
| `atkcli xml compare` | 比较两份 XML 的指定叶节点 |
| `atkcli script run` | 逐条执行 Connect 脚本 |
=======
| 页签     | 用途                             |
| ------ | ------------------------------ |
| 实例     | SDK 自检、状态刷新、接管/保持、进程监控、启动空 GUI |
| 报表与采样  | 解析地面站报表、核验采样间隔                 |
| XML 比较 | 按路径比较两份 XML 的叶节点数值             |
| 脚本     | 编辑并执行 Connect 脚本，查看逐条结果        |
| 说明     | 版本、边界与退出码                      |

### 命令行

| 命令                       | 说明                   |
| ------------------------ | -------------------- |
| `atkcli doctor`          | 导入 SDK 自检            |
| `atkcli instance status` | 被动快照：进程、端口监听与锁       |
| `atkcli instance attach` | 核验身份后接管，可保持指定时长      |
| `atkcli instance watch`  | 仅用操作系统探活，不建立 Connect |
| `atkcli reports parse`   | 解析已导出的地面站报表          |
| `atkcli reports cadence` | 核验相邻时间戳的实际步长         |
| `atkcli xml compare`     | 比较两份 XML 的指定叶节点      |
| `atkcli script run`      | 逐条执行 Connect 脚本      |
>>>>>>> 2e0d3c6d81e80c6e0b5d95c58449808cb8dc96da

完整参数见 `atkcli <命令> --help`。

```powershell
# 保持连接与锁 120 秒，另一个客户端占用时最多等 15 秒
atkcli instance attach --resume session.json --hold 120 --wait-lock 15 --label "课堂演示" --log session-1.jsonl

# 无响应时只做操作系统探活，不建立 Connect
atkcli instance watch --resume session.json --duration 300 --log watch-1.jsonl
```

### 离线校验

```powershell
atkcli reports parse --input examples/ground-station.zh.txt --out downlink.json
atkcli reports cadence --input examples/instrument.csv --step 60
atkcli xml compare --before examples/display.before.xml --after examples/display.after.xml --path Display/Scale --numeric
```

- 报表支持 UTF-8（含 BOM）、UTF-16（含 BOM）与 GB18030，可用 `--encoding` 指定
- `cadence` 逐个比较相邻时间戳，不补点、不重采样
- `xml compare` 只比较你已另存的两份文件，不执行 XML 中的任何内容

### 脚本执行

脚本每行一条 `命令名 参数`，`#` 开头为注释，`sleep 秒数` 在两条命令之间等待：

```text
# 只读检查
GetATKVersion / Platform
CheckScenario /
sleep 0.5
AllInstanceNames /
```

```powershell
atkcli script run --input demo.txt --command-timeout 60
```

- `--command-timeout` 是单条命令的等待上限，超时会断开客户端（ATK 可能仍在执行）
- 默认遇错即停；`--continue-on-error` 才会继续后续命令
- 脚本仍使用同一把实例锁，结束后断开客户端

命令语法见 ATK 本机帮助“二次开发教程 → 二次开发CONNECT模式 → 命令参考”，
图形界面的“命令参考”按钮也内置了常用命令。

### Python 接口

```python
from atkcli import Config, Session, SessionError

try:
    with Session(Config(timeout=20, wait_lock=5, label="课堂演示")) as session:
        print(session.last)       # 本次只读握手结果
        session.hold(2)           # 只监控原进程 2 秒
        print(session.refresh())  # 用同一个连接再查一次
except SessionError as exc:
    print(exc.code, exc.as_dict())
```

另有 `atkcli.status()`、`atkcli.watch()`、`atkcli.doctor()`，
以及 `reports.parse_report()`、`reports.check_cadence()`、`xmlcheck.compare_xml()`。

## 退出码

<<<<<<< HEAD
| 码 | 含义 |
|---:|---|
| 0 | 成功或完成 dry-run |
| 2 | 参数、输入格式、路径或文件读写错误 |
| 10 | 期限内未就绪或未获有效应答 |
| 20 | 原实例消失或 PID 已复用 |
| 21 | 未找到请求接管的实例 |
| 31 | 数据不足或核验未通过 |
| 40 | 其他客户端持锁，等待超时 |
| 41 | 实例/端口/场景归属冲突 |
| 42 | SDK、进程信息权限或握手失败 |
| 130 | 用户中断 |
=======
|   码 | 含义                |
| --: | ----------------- |
|   0 | 成功或完成 dry-run     |
|   2 | 参数、输入格式、路径或文件读写错误 |
|  10 | 期限内未就绪或未获有效应答     |
|  20 | 原实例消失或 PID 已复用    |
|  21 | 未找到请求接管的实例        |
|  31 | 数据不足或核验未通过        |
|  40 | 其他客户端持锁，等待超时      |
|  41 | 实例/端口/场景归属冲突      |
|  42 | SDK、进程信息权限或握手失败   |
| 130 | 用户中断              |
>>>>>>> 2e0d3c6d81e80c6e0b5d95c58449808cb8dc96da

## 安全边界

- 本工具不包含 ATK、许可证、场景、厂商 DLL 或任何账户凭据
- 默认不启动 ATK；只有显式传入 `--start-if-missing` 才会启动空 GUI
- 锁只协调使用同一临时目录与同一锁约定的客户端，不能阻止手工操作或其他脚本
- `DEAD` 只表示观测到原进程消失，不能证明是手动关闭、崩溃还是抢占
- 接管与脚本只使用官方 SDK 的 `atkOpen` / `atkConnect` / `atkClose`
- 不持久化内存星历，不缓存计算结果，不提供中途快照或断点恢复

## 项目结构

```text
atkcli/
├─ src/atkcli/
│  ├─ cli.py          # 命令行入口
│  ├─ gui.py          # Tkinter 界面
│  ├─ session.py      # 会话、租约与握手
│  ├─ channel.py      # SDK 子进程通道
│  ├─ machine.py      # 进程与端口核验
│  ├─ scripting.py    # Connect 脚本解析与模板
│  ├─ reports.py      # 报表解析与采样核验
│  └─ xmlcheck.py     # XML 数值比较
├─ tests/             # 单元测试与合成样例测试
├─ examples/          # 报表、采样与 XML 样例
└─ docs/images/       # README 截图
```

## 开发

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
pytest -q
```

测试不会启动真实的 ATK GUI。提交前请确保测试通过。

## 常见问题

**会自己启动 ATK 吗？**
不会。默认只接管已有实例；`--start-if-missing` 才会在找不到实例时启动空 GUI，且已有实例时不会重复启动。

**会修改场景吗？**
只有图形界面的“脚本”页和 `script run` 会按你编写的命令修改场景，执行前必须确认。其余命令保持只读。

**`status` 显示 `LISTENING` 就代表能连吗？**
不代表。它只说明看到了端口监听，`responsive: null` 表示尚未做协议握手。

**为什么必须用 Python 3.12？**
厂商 SDK 的二进制模块只兼容 CPython 3.12。仅做离线校验时可以用 3.11。

**支持 Linux 或 macOS 吗？**
不支持实时接管；离线校验部分只依赖 Python 标准库与 `psutil`。

## 贡献

欢迎提交 Issue 和 Pull Request。提交前请：

1. 运行 `pytest -q` 并确保通过
2. 在 Issue 中说明 ATK 版本、复现步骤与期望结果
3. 不要提交场景文件、许可证文件或任何账户凭据

## 许可证

[MIT](LICENSE)

本项目与 ATK 厂商无隶属关系。ATK 需自行安装并遵守其许可协议。

## 致谢

- ATK 官方 Connect SDK 与命令参考文档
- `psutil` 项目提供的进程与端口查询能力
