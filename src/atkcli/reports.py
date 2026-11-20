"""只核验内存中的导出文本；不打开文件、不连接软件、不推算窗口。

日期按民用 UTC 日历解释，支持至纳秒，不猜测时区、不处理闰秒标签。
无分析数据不是零个窗口。只能识别可见的截断；如果文本恰在完整行末
丢失尾部且没有行数等佐证，单凭剩余文本无法判定文件是否完整。
"""

import csv
import math
import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


_NS = 10**9
_MONTH = {m: i for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split(), 1)}
_MONTH.update({m: i for i, m in enumerate("一 二 三 四 五 六 七 八 九 十 十一 十二".split(), 1)})
_CN_MONTH = r"(?:[0-9]{1,2}|[一二三四五六七八九十]{1,2})"
_STAMP = re.compile(
    rf"(?:(?P<iy>[0-9]{{4}})-(?P<im>[0-9]{{2}})-(?P<id>[0-9]{{2}})(?:T|\s+)"
    rf"|(?P<d>[0-9]{{1,2}})\s+(?P<m>[a-z]{{3}}|{_CN_MONTH}月)\s+(?P<y>[0-9]{{4}})\s+"
    rf"|(?P<cy>[0-9]{{4}})\s*年\s*(?P<cm>{_CN_MONTH})\s*月\s*(?P<cd>[0-9]{{1,2}})\s*日\s*)"
    r"(?P<h>[0-9]{2}):(?P<min>[0-9]{2}):(?P<s>[0-9]{2})(?:\.(?P<f>[0-9]{1,9}))?"
    r"(?:\s*(?P<zone>Z|UTCG?|[+-][0-9]{2}:?[0-9]{2}))?(?=\s|$)", re.I,
)
_NUMBER = re.compile(r"\+?[0-9]{1,15}(?:\.[0-9]{1,9})?")
_INDEX = re.compile(r"[0-9]{1,15}")
_DECORATION = re.compile(r"[-=]{3,}(?:[\s,]+[-=]{3,})*")
_DAMAGED = re.compile(r"\bNACK\b|\btruncat(?:ed|ion)\b|截断|截短|…|\.{3}|\x00|�", re.I | re.ASCII)
_NO_DATA = re.compile(
    r"(?:(?:ATK|STK|报告|报表)(?:\s*:\s*|\s+))?"
    r"(?:(?:there\s+is\s+)?no\s+(?:available\s+)?analysis\s+data"
    r"(?:\s+(?:is\s+|was\s+)?(?:available|found))?(?:\s+for\s+[^.!?]+)?|inability\s+to\s+analyze\s+data"
    r"|(?:无|暂无|没有|未找到|找不到|未生成)(?:可用的?|可供(?:本|此|该)?(?:报告|报表)(?:使用)?的?"
    r"|可用于(?:生成)?(?:本|此|该)?(?:报告|报表)的?)?分析数据(?:可用)?)"
    r"[.!。！]?", re.I,
)
_PUNCT = str.maketrans("（）：，", "():,")


def _lines(text):
    if not isinstance(text, str):
        raise ValueError("应传入已解码的报表文本，而非文件路径或字节流")
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip().translate(_PUNCT)
        if n == 1:
            line = line.lstrip("﻿").strip()
        if _DAMAGED.search(line):
            raise ValueError(f"第 {n} 行含否定应答、截断标记或损坏的文本")
        if line and not _DECORATION.fullmatch(line):
            yield n, line


def _csv(line, delimiter=','):
    try:
        reader = csv.reader([line], delimiter=delimiter, strict=True, skipinitialspace=True)
        return [cell.strip() for cell in next(reader)]
    except csv.Error:
        raise ValueError("CSV 引号或分隔符不完整") from None


@dataclass(frozen=True)
class _Reading:
    ns: int
    unit: int  # 打印值最后一位代表的纳秒数


@dataclass(frozen=True)
class _Time(_Reading):
    iso: str


@dataclass(frozen=True)
class _Window:
    number: int
    start: _Time
    end: _Time
    duration: _Reading


def _take_time(text, utc_header):
    """读取行首的完整时间字段，余下部分必须以空白分隔。"""
    m = _STAMP.match(text)
    if not m:
        raise ValueError("时间字段缺失或格式不受支持")
    zone = (m["zone"] or "").upper()
    if not (zone in {"Z", "UTC", "UTCG", "+00:00", "+0000"} or (not zone and utc_header)):
        raise ValueError("时间字段不是明确的 UTC")
    month = (m["im"] or m["cm"] or m["m"]).lower().removesuffix("月")
    try:
        dt = datetime(int(m["iy"] or m["cy"] or m["y"]), int(month) if month.isdigit() else _MONTH[month],
                      int(m["id"] or m["cd"] or m["d"]), int(m["h"]), int(m["min"]), int(m["s"]))
    except (ValueError, KeyError):
        raise ValueError("日期、月份或时分秒无效") from None
    frac = m["f"] or ""
    ns = ((dt.toordinal() * 24 + dt.hour) * 3600 + dt.minute * 60 + dt.second) * _NS + int(frac.ljust(9, "0"))
    iso = dt.isoformat() + ("." + frac if frac else "") + "Z"
    return _Time(ns, 10 ** (9 - len(frac)), iso), text[m.end():].strip()


def _whole_time(cell, utc_header=True):
    value, rest = _take_time(cell, utc_header)
    if rest:
        raise ValueError("时间列含多余内容或未知时区")
    return value


def _amount(token):
    if not _NUMBER.fullmatch(token):
        raise ValueError("秒数应为非负十进制数，最多保留九位小数")
    whole, _, frac = token.partition(".")
    return _Reading(int(whole) * _NS + int(frac.ljust(9, "0")), 10 ** (9 - len(frac)))


def _unit(unit):
    return rf"(?:\s*\(\s*{unit}\s*\)|\s*\[\s*{unit}\s*\]|[ _]+{unit})"


_UTC = _unit(r"(?:UTC|UTCG(?:_MM)?)")
_SEC = _unit(r"(?:s|sec(?:ond)?s?|秒)")
_HEAD = (
    r"(?:access|window|interval|pass)(?:[ _]+(?:id|number|no\.?|#))?|id|no\.?|#|序号|编号|访问(?:编号|序号)?|窗口(?:编号|序号|号)?",
    r"(?:start(?:[ _]+time)?|begin(?:[ _]+time)?|(?:开始|起始)(?:时间|时刻|历元)?)" + _UTC,
    r"(?:(?:stop|end)(?:[ _]+time)?|(?:结束|终止|停止)(?:时间|时刻|历元)?)" + _UTC,
    r"(?:dur(?:ation)?|持续时间|时长|历时)" + _SEC,
)
_TEXT_HEAD = re.compile(r"\s+".join(f"(?:{col})" for col in _HEAD), re.I)


def _detail_header(line):
    if "," not in line:
        return bool(_TEXT_HEAD.fullmatch(line))
    fields = _csv(line)
    return len(fields) == 4 and all(re.fullmatch(col, cell, re.I) for col, cell in zip(_HEAD, fields))


def _detail(line, csv_layout):
    if csv_layout:
        fields = _csv(line)
        if len(fields) != 4:
            raise ValueError("明细必须恰有编号、起止时间和秒数四列")
        number, a, b, dur = fields
        start, end = _whole_time(a), _whole_time(b)
    else:
        first = line.split(None, 1)
        if len(first) != 2:
            raise ValueError("缺少完整的编号明细")
        number, rest = first
        start, rest = _take_time(rest, True)
        end, dur = _take_time(rest, True)
    if not _INDEX.fullmatch(number):
        raise ValueError("明细编号不是整数")
    duration = _amount(dur)
    delta = end.ns - start.ns
    if delta <= 0 or duration.ns <= 0:
        raise ValueError("窗口终点须晚于起点，且时长须为正")
    # 起点、终点和时长各自最多舍入半个末位；用两倍纳秒避免半单位误差。
    if 2 * abs(delta - duration.ns) > start.unit + end.unit + duration.unit:
        raise ValueError("时长与起止时间之差不符，已超出打印舍入范围")
    return _Window(int(number), start, end, duration)


_STAT_TITLE = re.compile(r"(?:(?:(?:global|overall|summary|access)\s+)?statistics|summary|(?:全局|总体|窗口|访问|汇总)?统计(?:信息|数据|结果)?|汇总)\s*:?", re.I)
_STAT_LABELS = {
    "count": r"(?:count|number\s+of\s+(?:accesses|windows|intervals)|(?:access|window)\s+count|(?:总)?(?:窗口数|访问次数|访问数|记录数))",
    "min_duration_s": r"(?:(?:access\s+)?min(?:imum)?(?:\s+duration)?|最(?:短|小)(?:可见)?(?:持续时间|时长|历时|时间))",
    "max_duration_s": r"(?:(?:access\s+)?max(?:imum)?(?:\s+duration)?|最(?:长|大)(?:可见)?(?:持续时间|时长|历时|时间))",
    "mean_duration_s": r"(?:(?:access\s+)?(?:mean|average)(?:\s+duration)?|平均(?:可见)?(?:持续时间|时长|历时|时间))",
    "total_duration_s": r"(?:(?:access\s+)?(?:total|sum)(?:\s+duration)?|(?:总|累计)(?:可见)?(?:持续时间|时长|历时|时间))",
    "mean_unavailable_s": r"(?:unaccess\s+mean\s+duration|平均不可见(?:时长|时间))",
}
_STAT_ROWS = {key: re.compile(label + (rf"(?:{_SEC})?" if key != "count" else "") + r"(?:\s*[:=]\s*|\s+)(.*)", re.I)
              for key, label in _STAT_LABELS.items()}


def _stat_value(key, value, rows):
    if key == "count":
        if not _INDEX.fullmatch(value) or int(value) != len(rows):
            raise ValueError("统计行数与编号明细不符")
        return int(value)
    if key == "mean_unavailable_s":
        # 报告时段边界未随明细提供。保留印出的值，不用可见时长推算不可见均值。
        return _amount(value)
    if key in {"min_duration_s", "max_duration_s"} and not _NUMBER.fullmatch(value):
        repeated = _detail(value, False)
        if not 1 <= repeated.number <= len(rows):
            raise ValueError("统计行引用了不存在的明细编号")
        ref = rows[repeated.number - 1]
        for a, b in ((repeated.start, ref.start), (repeated.end, ref.end), (repeated.duration, ref.duration)):
            if 2 * abs(a.ns - b.ns) > a.unit + b.unit:
                raise ValueError("统计行与所引编号的明细不符")
        amount = repeated.duration
    else:
        amount = _amount(value)
    # 统计量本身也可能单独舍入，不能强求它等于显示值的浮点聚合。
    combine = {"min_duration_s": min, "max_duration_s": max}.get(key, sum)
    # 真实时长既要与时长列相容，也要与两个端点相容。使用两者舍入区间的交集。
    def bound(row, sign):
        d = row.end.ns - row.start.ns
        printed = 2 * row.duration.ns + sign * row.duration.unit
        endpoints = 2 * d + sign * (row.start.unit + row.end.unit)
        return max(0, printed, endpoints) if sign < 0 else min(printed, endpoints)

    lo = combine(bound(row, -1) for row in rows)
    hi = combine(bound(row, 1) for row in rows)
    factor = len(rows) if key == "mean_duration_s" else 1
    if (2 * amount.ns + amount.unit) * factor < lo or (2 * amount.ns - amount.unit) * factor > hi:
        raise ValueError("统计值与明细不符，已超出打印舍入范围")
    return amount


def _summary_values(stats, count):
    # 将总量换算到均值的量纲，再检查最小值 ≤ 均值 ≤ 最大值；保留各列的打印精度。
    low = stats.get('min_duration_s')
    high = stats.get('max_duration_s')
    mean = stats.get('mean_duration_s')
    total = stats.get('total_duration_s')
    def exceeds(a, b, scale_a=1, scale_b=1):
        return (a is not None and b is not None and
                (2*a.ns - a.unit)*scale_a > (2*b.ns + b.unit)*scale_b)
    if (exceeds(low, high) or exceeds(low, mean) or exceeds(mean, high) or
            exceeds(low, total, count) or exceeds(total, high, 1, count) or
            exceeds(mean, total, count) or exceeds(total, mean, 1, count)):
        raise ValueError('统计项之间矛盾，超出各列打印舍入范围')
    return {k: (v.ns / _NS if isinstance(v, _Reading) else v) for k, v in stats.items()}


def _preamble(line):
    """允许标题和单独的生成时间；不把表格、未编号数据或损坏的表头当标题跳过。"""
    if _STAMP.fullmatch(line):
        _whole_time(line)
        return
    if (_STAT_TITLE.fullmatch(line) or _NO_DATA.fullmatch(line)
            or any(p.fullmatch(line) for p in _STAT_ROWS.values())
            or "," in line or "\t" in line or re.match(r"[0-9]+\s|[0-9]{4}[-年]", line)
            or re.search(r"\b(?:start|stop|duration|time\s*\(|NACK)\b|(?:开始|起始|结束|终止|停止)(?:时刻|历元|时间)|持续时间|(?:编号|序号)\s", line, re.I)):
        raise ValueError("表头未知或在有效表头之前出现数据")


def parse_report(text: str) -> dict:
    """核对单张编号窗口表，返回规范 UTC 字符串及报表实际列出的统计项。"""
    rows = []
    statistics = {}
    state = "preamble"
    csv_layout = False
    for n, line in _lines(text):
        try:
            if state == "no_data":
                raise ValueError("无分析数据提示后仍有其他内容")
            if _NO_DATA.fullmatch(line):
                if rows or state == "stats":
                    raise ValueError("无分析数据提示与已有明细或统计块冲突")
                state = "no_data"
                continue
            if _detail_header(line):
                if state == "stats" and not statistics:
                    continue  # 统计块有时会重印列名，不是新的一张明细表。
                if state != "preamble":
                    raise ValueError("重复或嵌套的明细表头")
                state, csv_layout = "details", "," in line
                continue
            if state == "preamble":
                _preamble(line)
                continue
            label_line = line
            if ',' in line:
                cells = _csv(line)
                if any(not cell for cell in cells):
                    raise ValueError('明细或统计行含缺失的 CSV 字段')
                label_line = ' '.join(cells)
            if _STAT_TITLE.fullmatch(label_line):
                if not rows or state == "stats":
                    raise ValueError("统计块之前没有完整明细，或统计块重复")
                state = "stats"
                continue
            for key, pattern in _STAT_ROWS.items():
                if match := pattern.fullmatch(label_line):
                    if not rows or key in statistics:
                        raise ValueError("统计项缺少明细或重复出现")
                    statistics[key] = _stat_value(key, match[1].strip(), rows)
                    state = "stats"
                    break
            else:
                if state == "stats":
                    raise ValueError("统计块出现未知行或夹入编号明细")
                row = _detail(line, csv_layout)
                if row.number != len(rows) + 1:
                    raise ValueError("编号应从一开始连续递增")
                if rows and row.start.ns < rows[-1].end.ns:
                    raise ValueError("编号窗口逆序或重叠")
                rows.append(row)
        except ValueError as exc:
            raise ValueError(f"第 {n} 行：{exc}") from None
    if state == "no_data":
        return {"ok": False, "status": "NO_ANALYSIS_DATA"}
    if not rows or (state == "stats" and not statistics):
        raise ValueError("缺少已识别的完整明细或统计块为空，不能认定窗口数为零")
    return {"ok": True, "status": "OK", "windows": [
        {"start_utc": row.start.iso, "end_utc": row.end.iso, "dur_s": row.duration.ns / _NS} for row in rows
    ], "statistics": _summary_values(statistics, len(rows))}


_TIME_NAME = r"(?:time(?:stamp)?|date[ _]?time|epoch|采样时间|时间戳|日期时间|时间|时刻)"
_SERIES_HEAD = re.compile(rf"(?:(?P<utc>{_TIME_NAME}{_UTC}|UTCG?)|{_TIME_NAME})(?=\s|$)", re.I)
_ZONE_WORD = re.compile(
    r"(?:UTC\S*|GMT\S*|UT1|TAI|GPS|TDT|TDB|TT|LOCAL|[CEPM][DS]T|CEST|CET|"
    r"北京时间|当地时间|地方时|本地时间|[A-Za-z]+/(?:[A-Za-z_+\-/0-9]+)|[+-][0-9]{2}:?[0-9]{2})(?:\s|$)", re.I,
)


def check_cadence(text: str, expected_s: float | None = None) -> dict:
    """按原顺序核验每一个打印间隔；未指定步长时，以首个间隔作参照。

    时间不递增或间隔不符返回 ok=False；格式错误、缺少 UTC 依据或点数
    不足抛 ValueError。不会排序、补点、取整或用平均间隔代替逐对核验。
    """
    if expected_s is not None and (isinstance(expected_s, bool) or not isinstance(expected_s, (int, float))
                                   or not math.isfinite(expected_s) or expected_s <= 0):
        raise ValueError("期望步长必须是有限正数（秒）")
    reference = Decimal(str(expected_s)) * _NS if expected_s is not None else None
    previous = low = high = None
    count = bad = 0
    layout = None  # ','、制表符或 ''（空白对齐）
    utc_header = preamble = wide_columns = False
    width = 0

    def width_of_rest(rest):
        if not rest.strip():
            return 1
        return 1 + len(re.split(r'\s{2,}' if wide_columns else r'\s+', rest.strip()))

    for n, line in _lines(text):
        try:
            if layout is None:
                sep = '\t' if '\t' in line and ',' not in line else (',' if ',' in line else '')
                fields = _csv(line, sep) if sep else [line]
                match = _SERIES_HEAD.fullmatch(fields[0]) if sep else _SERIES_HEAD.match(line)
                if match:
                    layout = sep
                    utc_header = bool(match['utc'])
                    suffix = fields[0][match.end():]
                    if suffix.strip().startswith(('(', '[')) or _ZONE_WORD.match(suffix.strip()):
                        raise ValueError('时间列表头含未知单位或非 UTC 时标')
                    wide_columns = bool(re.search(r'\s{2,}', suffix))
                    width = len(fields) if sep else width_of_rest(suffix)
                    continue
                stamp = _STAMP.match(fields[0])
                if not stamp or (not sep and stamp.end() == len(line) and not stamp['zone']):
                    _preamble(line)
                    preamble = True
                    continue
                if preamble:
                    raise ValueError('标题之后缺少可识别的 UTC 首列表头')
                layout = sep
                suffix = fields[0][stamp.end():]
                wide_columns = bool(re.search(r'\s{2,}', suffix))
                width = len(fields) if sep else width_of_rest(suffix)
            if layout:
                cells = _csv(line, layout)
                if len(cells) != width or not all(cells):
                    raise ValueError('分隔表列数不一致，字段缺失或记录被截断')
                sample = _whole_time(cells[0], utc_header)
            else:
                sample, rest = _take_time(line, utc_header)
                if _ZONE_WORD.match(rest) or width_of_rest(rest) != width:
                    raise ValueError('时间列含未知时区，或采样行与表头列数不一致')
            if previous is not None:
                delta = sample.ns - previous
                if reference is None:
                    reference = delta
                bad += int(delta <= 0 or delta != reference)
                low = delta if low is None else min(low, delta)
                high = delta if high is None else max(high, delta)
            count += 1
            previous = sample.ns
        except ValueError as exc:
            raise ValueError(f"第 {n} 行：{exc}") from None
    if count < 2:
        raise ValueError("核验采样间隔至少需要两个有明确 UTC 依据的时间点")
    return {"ok": bad == 0, "n": count, "min_step_s": low / _NS, "max_step_s": high / _NS,
            "expected_s": expected_s, "reference_step_s": float(reference / _NS), "bad_intervals": bad}
