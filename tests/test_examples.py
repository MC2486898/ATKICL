"""最终 CLI 的离线样例验收，不运行 ATK。"""
import json
from pathlib import Path

import pytest

from atkcli.cli import main

EX = Path(__file__).resolve().parents[1] / 'examples'


@pytest.mark.parametrize('lang', ['en', 'zh'])
def test_numbered_rows_are_separate_from_statistics(lang,tmp_path,capsys):
    out = tmp_path/'intervals.json'
    assert main(['reports','parse','--input',str(EX/f'ground-station.{lang}.txt'),'--out',str(out)]) == 0
    doc = json.loads(out.read_bytes())
    assert doc == json.loads(capsys.readouterr().out)
    assert [w['dur_s'] for w in doc['windows']] == [5., 3.]
    assert doc['statistics']['total_duration_s'] == 8.
    assert 5. in doc['statistics'].values() and len(doc['statistics']) == 5


@pytest.mark.parametrize('diag', ['无分析数据！','Inability to analyze data!'])
def test_no_analysis_data_is_indeterminate(diag,tmp_path,capsys):
    f = tmp_path/'empty.txt'
    f.write_text('Ground Station Downlink Report\n-----------\n' + diag, encoding='utf-8')
    assert main(['reports','parse','--input',str(f)]) == 31
    assert json.loads(capsys.readouterr().out)['status'] == 'NO_ANALYSIS_DATA'


def test_cadence_judged_from_actual_rows_and_safe_outputs(tmp_path,capsys):
    out = tmp_path/'audit.json'
    assert main(['reports','cadence','--input',str(EX/'instrument.csv'),'--step','60','--out',str(out)]) == 0
    assert json.loads(capsys.readouterr().out)['n'] == 3
    args = ['reports','cadence','--input',str(EX/'instrument.csv'),'--step','1']
    assert main(args) == 31
    obj = json.loads(capsys.readouterr().out)
    assert not obj['ok'] and obj['min_step_s'] == obj['max_step_s'] == 60
    original = out.read_bytes()
    assert main([*args,'--out',str(out)]) == 2 and out.read_bytes() == original


def test_two_saved_xmls_numeric_and_literal(capsys):
    args = ['xml','compare','--before',str(EX/'display.before.xml'),'--after',str(EX/'display.after.xml'),
            '--path','Display/Scale']
    assert main(args) == 31
    assert main([*args,'--numeric']) == 0
    docs = list(map(json.loads,capsys.readouterr().out.splitlines()))
    assert [d['ok'] for d in docs] == [False, True]
