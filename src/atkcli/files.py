"""CLI 的文件边界：输出必须是新文件，dry-run 不打开文件写入。"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


LIMIT = 64 * 1024 * 1024


def read_text(path: Path, encoding='auto', limit=LIMIT):
    if not path.is_file():
        raise ValueError(f'输入不是普通文件：{path}')
    with path.open('rb') as f:
        raw = f.read(limit + 1)
    if len(raw) > limit:
        raise ValueError(f'输入超过 {limit} 字节上限：{path}')
    if encoding == 'auto':
        if raw.startswith((b'\xff\xfe', b'\xfe\xff')):
            return raw.decode('utf-16')
        try:
            return raw.decode('utf-8-sig')
        except UnicodeDecodeError:
            return raw.decode('gb18030')
    return raw.decode(encoding)


def receipt(path):
    data = json.loads(read_text(path, 'utf-8-sig', 32768))
    if (not isinstance(data, dict) or data.get('schema') != 'atkcli.session/1'
            or data.get('state') != 'READY' or data.get('ok') is not True):
        raise ValueError('需要由 instance attach 生成的成功接管回执。')
    inst = data.get('instance')
    if (not isinstance(inst, dict) or type(inst.get('pid')) is not int
            or type(inst.get('created_epoch_s')) not in (int, float) or type(data.get('port')) is not int):
        raise ValueError('回执缺少有效的实例身份。')
    return inst['pid'], inst['created_epoch_s'], data['port']


def output_path(path, suffixes, inputs=()):
    if path is None:
        return
    if path.suffix.casefold() not in suffixes:
        raise ValueError(f'输出后缀须为 {", ".join(suffixes)}：{path}')
    p = path.resolve()
    if any(p == i.resolve() for i in inputs if i is not None):
        raise ValueError(f'禁止用输入文件作为输出：{path}')
    if path.exists() or path.is_symlink():
        raise FileExistsError(f'输出已存在，请换用新文件：{path}')
    if not path.parent.is_dir():
        raise ValueError(f'请先创建输出目录：{path.parent}')


def json_text(obj):
    return json.dumps(obj, ensure_ascii=False, allow_nan=False)


def write_json_new(path, obj):
    # 写好临时文件才公布；提交时若目标已由另一进程创建，保留对方文件。
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                     prefix='.atkcli-', suffix='.tmp', delete=False) as f:
        tmp = Path(f.name)
        try:
            f.write(json_text(obj) + '\n')
            f.flush()
            os.fsync(f.fileno())
        except BaseException:
            f.close()
            tmp.unlink(missing_ok=True)
            raise
    try:
        if os.name == 'nt':
            os.rename(tmp, path)  # Windows rename 不覆盖已存在的目标。
        else:
            os.link(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
