"""独立核对发现的反例：输入矛盾时不能产生肯定结论。"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

from atkcli.reports import check_cadence, parse_report
from atkcli.xmlcheck import compare_xml


HEADER = '编号,start_utc,end_utc,dur_s'
ROW = '1,2000-01-01T00:00:00.000Z,2000-01-01T00:00:01.000Z,1'
BASE = HEADER + '\n' + ROW


@pytest.mark.parametrize('doc', [
    BASE + '\nMin Duration: 1.4\nMax Duration: 0.6',
    BASE + '\nTotal Duration: 1.400',
    BASE + '\nStatistics\n' + HEADER + '\nMin Duration,1,,,\nMax Duration,' + ROW,
    '编号  开始时刻(GPS)  结束时刻(GPS)  时长(秒)\n' + BASE,
    '全局统计\n窗口数: 0\n' + BASE,
    'Count: 0\n' + BASE,
])
def test_contradictory_report_not_accepted(doc):
    with pytest.raises(ValueError):
        parse_report(doc)


@pytest.mark.parametrize('doc', [
    'Time (UTC)\t读数\n2000-01-01T00:00:00 Asia/Shanghai\t1\n2000-01-01T00:00:01 Asia/Shanghai\t2',
    'Time (UTC)\t温度\t计数\n2000-01-01T00:00:00\t1\t2\n2000-01-01T00:00:01\t2',
    'No analysis data available.\nUTC,读数\n2000-01-01T00:00:00,0\n2000-01-01T00:00:01,1',
    '时间 (UTC)  温度  计数\n2000-01-01T00:00:00  1  2\n2000-01-01T00:00:01  2',
])
def test_contradictory_series_not_accepted(doc):
    with pytest.raises(ValueError):
        check_cadence(doc)


@pytest.mark.parametrize('encoding', ['utf-16-le','utf-16-be'])
def test_generic_utf16_declaration_needs_bom(tmp_path, encoding):
    p = tmp_path/'sample.xml'
    p.write_bytes('<?xml version="1.0" encoding="UTF-16"?><字号>12</字号>'.encode(encoding))
    assert not compare_xml(p, p, ['.'])['ok']


def test_offline_api_imports_with_stdlib_only(tmp_path):
    src = Path(__file__).resolve().parents[1]/'src'
    run = subprocess.run([sys.executable, '-I', '-S', '-B', '-c',
        'import sys;sys.path.insert(0,sys.argv[1]);from atkcli.xmlcheck import compare_xml;'
        'from atkcli.reports import parse_report;assert "psutil" not in sys.modules', str(src)],
        cwd=tmp_path, env={**os.environ,'PYTHONUTF8':'1'}, capture_output=True, timeout=5)
    assert run.returncode == 0, run.stderr
