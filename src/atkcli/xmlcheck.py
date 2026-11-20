"""比较两份已保存 XML 中指定叶节点的文本，仅报告文件回读差异。"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from xml.etree import ElementTree as ET
from xml.parsers import expat


MAX_XML_BYTES = 8 * 1024 * 1024
_MAX_ELEMENTS = 100_000
_MAX_NUMBERS = 65_536
_NAME = r"(?:\{[^{}\s*]+\})?[^\W\d][\w.-]*"
_PATH = re.compile(rf"(?:\.//?)?{_NAME}(?:/{_NAME})*")
_NUMBER = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")


class _Invalid(Exception):
    """文件或选定的值不能参与比较。"""


def _reject_declaration(*_args):
    raise _Invalid("不允许 DTD、自定义实体或外部实体")


def _check_encoding(encoding, prefix):
    name = (encoding or '').upper()
    if name and name not in {"UTF-8", "UTF-16", "UTF-16LE", "UTF-16BE"}:
        raise _Invalid("只接受 UTF-8 或 UTF-16 编码的 XML")
    if name == 'UTF-16' and prefix not in {b'\xfe\xff', b'\xff\xfe'}:
        raise _Invalid("声明为 UTF-16 的 XML 缺少字节序标记（BOM）")


def _tag(name: str) -> str:
    # 将解析器展开的名称转为 ElementTree 的 {命名空间}局部名。
    return "{" + name if "}" in name else name


def _load(path: Path) -> ET.Element:
    path = Path(path)
    if str(path).replace("\\", "/").startswith("//"):
        raise _Invalid("只接受本地普通文件，不读取网络共享或设备路径")
    try:
        if not path.is_file():
            raise _Invalid("文件不存在或不是普通文件")
        with path.open("rb") as source:
            data = source.read(MAX_XML_BYTES + 1)
    except (OSError, ValueError):
        raise _Invalid("无法打开或读取文件") from None
    if len(data) > MAX_XML_BYTES:
        raise _Invalid(f"文件超过 {MAX_XML_BYTES} 字节上限")

    tree = ET.TreeBuilder()
    count = 0

    def start(name, _attrs):
        nonlocal count
        count += 1
        if count > _MAX_ELEMENTS:
            raise _Invalid(f"XML 超过 {_MAX_ELEMENTS} 个元素的上限")
        # 只查询元素文本；属性不参与比较，也不解释其中的地址。
        tree.start(_tag(name), {})

    # 直接解析字节，由解析器识别字节序；在读取 DTD 内容之前拒绝声明。
    parser = expat.ParserCreate(namespace_separator="}")
    parser.StartDoctypeDeclHandler = _reject_declaration
    parser.EntityDeclHandler = _reject_declaration
    parser.ExternalEntityRefHandler = _reject_declaration
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    parser.XmlDeclHandler = lambda _v, encoding, _s: _check_encoding(encoding, data[:2])
    parser.StartElementHandler = start
    parser.EndElementHandler = lambda name: tree.end(_tag(name))
    parser.CharacterDataHandler = tree.data
    try:
        parser.Parse(data, True)
    except expat.ExpatError as exc:
        raise _Invalid(f"XML 格式或编码错误（第 {exc.lineno} 行，第 {exc.offset + 1} 列）") from None
    except (LookupError, ValueError):
        raise _Invalid("无法解析 XML 编码") from None
    return tree.close()


def _leaf_text(root: ET.Element, path: str) -> str:
    matches = root.iterfind(path)
    node = next(matches, None)
    if node is None:
        raise _Invalid("未命中节点")
    if next(matches, None) is not None:
        raise _Invalid("命中多个节点，无法唯一定位")
    if len(node):
        raise _Invalid("命中的不是叶节点")
    if node.text is None or not node.text.strip():
        raise _Invalid("叶节点文本缺失或只有空白")
    return node.text


def _numeric_form(text: str) -> tuple:
    text = text.strip()
    left = text[:1] if text[:1] in {"[", "("} else ""
    right = text[-1:] if text[-1:] in {"]", ")"} else ""
    if bool(left) != bool(right):
        raise _Invalid("数值序列的边界符不完整")
    if left:
        text = text[1:-1].strip()
    separator = "," if "," in text else None
    parts = text.split(separator, _MAX_NUMBERS)
    if not parts or len(parts) > _MAX_NUMBERS:
        raise _Invalid(f"数值个数须在 1 至 {_MAX_NUMBERS} 之间")
    numbers = []
    for part in parts:
        token = part.strip()
        if not _NUMBER.fullmatch(token):
            raise _Invalid("数值项不是有限十进制数")
        try:
            number = Decimal(token)
        except (InvalidOperation, ValueError, OverflowError):
            raise _Invalid("十进制数超出可表示范围") from None
        if not number.is_finite():
            raise _Invalid("数值项不是有限十进制数")
        numbers.append(number)
    return left, right, separator, numbers


def compare_xml(before: Path, after: Path, paths: list[str], numeric: bool = False) -> dict:
    """逐路径比较本地文件；空路径列表或任何一项未通过时，整体 ok 为假。

    路径相对根元素，如“显示/字号”“./显示/字号”或“.//字号”；“.”表示根本身。
    可用 {命名空间}局部名，不支持谓词、通配符或属性选择。每边必须唯一命中
    有非空白文本的叶元素。默认逐字符比较解析后的文本，不去除首尾空白。
    数值模式识别十进制及科学记数法；按顺序精确比较，不忽略项数、逗号与
    空白分隔方式的区别，或方括号与圆括号的区别。不作单位换算或区间推断。
    checks 保留请求顺序和两边的原文本；无法取得的值为 None，失败项附中文原因。
    """
    if not isinstance(paths, list) or not paths:
        return {"ok": False, "checks": []}

    sources = []
    for key, label, file in (("before", "前文件", before), ("after", "后文件", after)):
        try:
            sources.append((key, label, _load(file), None))
        except _Invalid as exc:
            sources.append((key, label, None, str(exc)))

    checks = []
    for path in paths:
        check = {"path": path, "ok": False, "before": None, "after": None}
        checks.append(check)
        if not isinstance(path, str) or len(path) > 512 or (path != "." and not _PATH.fullmatch(path)):
            check["error"] = "需要不超过 512 字符的简单相对元素路径，如 ./显示/字号"
            continue
        errors = []
        values = []
        for key, label, root, load_error in sources:
            if root is None:
                errors.append(f"{label}：{load_error}")
                continue
            try:
                text = _leaf_text(root, path)
                check[key] = text
                values.append(_numeric_form(text) if numeric else text)
            except _Invalid as exc:
                errors.append(f"{label}：{exc}")
        if not errors:
            check["ok"] = values[0] == values[1]
            if not check["ok"]:
                errors.append("数值或序列形状不同" if numeric else "叶节点文本不同")
        if errors:
            check["error"] = "；".join(errors)
    return {"ok": all(check["ok"] for check in checks), "checks": checks}
