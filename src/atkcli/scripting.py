"""Connect 脚本：解析命令行、内置模板与命令参考，复用会话锁执行。

每行一条命令：`命令名 参数`，参数原样传给 `atkConnect`（对象路径与参数之间用空格分隔，
含空格的值用双引号括起）。`#` 开头为注释；`sleep 秒数` 在两条命令之间等待。
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass

from .session import Session, seconds

COMMAND_MAX = 128
ARGUMENT_MAX = 4000
SLEEP_MAX = 3600


@dataclass(frozen=True)
class Command:
    command: str
    argument: str = ""
    sleep_s: float = 0.0


def parse_script(text):
    if not isinstance(text, str):
        raise ValueError("脚本必须是文本。")
    commands = []
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 1)
        head = parts[0]
        argument = parts[1].strip() if len(parts) > 1 else ""
        if head.lower() == "sleep":
            try:
                duration = float(argument)
            except ValueError:
                raise ValueError(f"第 {number} 行：sleep 需要秒数。") from None
            if not math.isfinite(duration) or not 0 <= duration <= SLEEP_MAX:
                raise ValueError(f"第 {number} 行：sleep 需在 0–{SLEEP_MAX} 秒之间。")
            commands.append(Command("", "", duration))
            continue
        if not head.isprintable() or len(head) > COMMAND_MAX:
            raise ValueError(f"第 {number} 行：命令名须为 1–{COMMAND_MAX} 个可打印字符。")
        if not argument.isprintable() or len(argument) > ARGUMENT_MAX:
            raise ValueError(f"第 {number} 行：参数不能含换行，且不超过 {ARGUMENT_MAX} 字符。")
        commands.append(Command(head, argument))
    if not commands:
        raise ValueError("脚本为空。")
    return commands


def run_script(config, script, *, timeout=30, continue_on_error=False,
               should_stop=None, on_result=None, on_event=None):
    """进入会话（取得锁、核验实例）后逐条执行命令，返回脚本结果。"""
    if isinstance(script, str):
        commands = [asdict(c) for c in parse_script(script)]
    else:
        commands = [asdict(c) if isinstance(c, Command) else dict(c) for c in script]
    if not commands:
        raise ValueError("脚本为空。")
    seconds(timeout, "timeout", positive=True)
    with Session(config, on_event=on_event) as session:
        return session.execute(commands, timeout=timeout, continue_on_error=continue_on_error,
                               should_stop=should_stop, on_result=on_result)


TEMPLATES = (
    {
        "name": "只读检查",
        "description": "只查询版本、场景状态与对象清单，不修改任何内容。",
        "text": """# 只读检查：不修改场景
GetATKVersion / Platform
CheckScenario /
AllInstanceNames /
""",
    },
    {
        "name": "读取报告",
        "description": "读取已有卫星的位置报告；需先对场景执行过 Animate * Reset。",
        "text": """# 场景中须已有 Satellite1，并已生成星历
Report_RM */Satellite/Satellite1 Style "Position" TimePeriod "5 Mar 2025 00:00:00.000" "5 Mar 2025 01:00:00.000"
""",
    },
    {
        "name": "修改分析时段",
        "description": "修改场景分析时段并用 Reset 使其生效。",
        "text": """# 修改分析时段后必须 Reset 才会生效
SetAnalysisTimePeriod * "5 Mar 2025 00:00:00.000" "6 Mar 2025 00:00:00.000"
Animate * Reset
""",
    },
    {
        "name": "新建场景与卫星",
        "description": "替换当前场景，新建卫星并生成星历，最后读一段位置报告。参数为示例。",
        "text": """# 会替换当前场景；请先确认原内容已保存
New / Scenario ScriptDemo
SetAnalysisTimePeriod * "5 Mar 2025 00:00:00.000" "6 Mar 2025 00:00:00.000"
New / */Satellite Satellite1
SetState */Satellite/Satellite1 Classical TwoBody NoProp 60 J2000 "5 Mar 2025 00:00:00.000" 7000000 0.001 45 0 0 0
Animate * Reset
Report_RM */Satellite/Satellite1 Style "Position" TimePeriod "5 Mar 2025 00:00:00.000" "5 Mar 2025 01:00:00.000"
""",
    },
    {
        "name": "新建地面站",
        "description": "新建地面站并设置经纬高。",
        "text": """# 经度、纬度、高度，单位由 Connect 设置决定
New / */Facility Facility1
SetPosition */Facility/Facility1 Geodetic 39.9 116.4 0.0
""",
    },
    {
        "name": "可见性分析",
        "description": "计算卫星与地面站的可见时段；对象须已存在且已 Reset。",
        "text": """# 两个对象须已存在且已生成星历
Access */Satellite/Satellite1 */Facility/Facility1 TimePeriod "5 Mar 2025 00:00:00.000" "5 Mar 2025 06:00:00.000"
""",
    },
    {
        "name": "保存与另存",
        "description": "将当前场景保存到默认位置，并另存为一份新文件。",
        "text": """# 保存当前场景
Save / *
# 另存为新文件；路径请按本机修改，文件不能已存在
SaveAs / * "D:/ATK/Scenario-backup.xml"
""",
    },
    {
        "name": "仿真控制",
        "description": "启动、等待、暂停、重置仿真；Start 是异步的，用 sleep 等待。",
        "text": """Animate * Start
sleep 5
Animate * Pause
Animate * Reset
""",
    },
)


COMMANDS = (
    ("GetATKVersion", "/ [Details | Platform]", "/ Platform", "读取 ATK 版本信息。"),
    ("CheckScenario", "/", "CheckScenario /", "检测是否已打开场景，返回 1 或 0。"),
    ("AllInstanceNames", "/ [IncludeAccess] [Delimiter]", "AllInstanceNames /", "列出当前场景所有对象路径。"),
    ("DoesObjExist", "<ApplicationPath> <ObjectPath>", "DoesObjExist / */Satellite/Satellite1", "判断对象是否存在。"),
    ("New", "/ <ClassPath> <NewObjectName> {NewOptions}", "New / Scenario Demo", "新建场景或对象；示例：New / */Satellite Satellite1 CentralBody Earth。"),
    ("Load", "/ Scenario \"<FilePath>\"", 'Load / Scenario "D:/ATK/A.xml"', "打开已有场景；已打开其他场景时会失败。"),
    ("Save", "/ * [\"<FilePath>\"]", "Save / *", "保存当前场景。"),
    ("SaveAs", "/ * \"<FilePath>\"", 'SaveAs / * "D:/ATK/B.xml"', "另存为指定文件。"),
    ("Reload", "/ */Satellite \"<FilePath>\"", 'Reload / */Satellite "D:/ATK/A.xml"', "从文件恢复卫星数据。"),
    ("Unload", "/ * [RemAssignedObjs]", "Unload / *", "移除场景或对象；不会删除磁盘文件。"),
    ("Rename", "<ObjectPath> <NewName>", "Rename */Satellite/Satellite1 Sat1", "对象重命名。"),
    ("SetAnalysisTimePeriod", "* \"<Start>\" \"<Stop>\"", 'SetAnalysisTimePeriod * "5 Mar 2025 00:00:00.000" "6 Mar 2025 00:00:00.000"', "设置分析时段，需 Animate * Reset 生效。"),
    ("Animate", "* Start | Pause | Reset | Speed <x> | Step Forward | Step Reverse", "Animate * Reset", "仿真控制；Reset 同时生成星历。"),
    ("SetAnimation", "* CurrentTime \"<Time>\" | AnimationMode Normal | RealTime | XRealTime", "SetAnimation * AnimationMode XRealTime", "设置当前仿真时间或模式。"),
    ("GetTimePeriod", "<ObjectPath>", "GetTimePeriod */Satellite/Satellite1", "读取当前场景或对象的起止时间。"),
    ("Report_RM", "<ObjectPath> Style \"<Style>\" TimePeriod \"<Start>\" \"<Stop>\"", 'Report_RM */Satellite/Satellite1 Style "Position" TimePeriod "5 Mar 2025 00:00:00.000" "5 Mar 2025 01:00:00.000"', "读取报告；不写 TimePeriod 会读 2000-01-01 的固定时段。"),
    ("Access", "<ObjectPath> <AccessObjectPath> TimePeriod \"<Start>\" \"<Stop>\" | UseObjectTimes", 'Access */Satellite/Satellite1 */Facility/Facility1 UseObjectTimes', "计算可见性；两对象须不同且已生成星历。"),
    ("SetState Classical", "<SatPath> Classical <Propagator> {NoProp | \"<Start>\" \"<Stop>\"} <Step> <CoordSystem> \"<Epoch>\" <a> <e> <i> <w> <RAAN> <M>", 'SetState */Satellite/Satellite1 Classical HPOP "5 Mar 2025 00:00:00.000" "5 Mar 2025 04:00:00.000" 60 J2000 "5 Mar 2025 00:00:00.000" 7163000 0.5 98.5 0 139.7 120', "设置卫星经典轨道根数。"),
    ("SetPosition", "<ObjectPath> [{Type}] {CoordType} <Parameters>", "SetPosition */Facility/Facility1 Geodetic 39.9 116.4 0.0", "设置地面站位置（Cartesian 或 Geodetic）。"),
    ("SetMass", "<SatObjectPath> Value <Mass> | Matrix <6 个值>", "SetMass */Satellite/Satellite1 Value 1000", "设置卫星质量或惯量矩阵。"),
    ("Define", "<SensorObjectPath> Rectangular <V> <H> | SimpleCone <Angle>", "Define */Satellite/Satellite1/Sensor/Sensor1 Rectangular 25.1 36.8", "定义传感器类型与角度。"),
)
