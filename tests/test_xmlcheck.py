"""仅使用虚构的界面显示设置；在文件读取边界提供字节，不创建样例文件。"""

import codecs
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from atkcli.xmlcheck import compare_xml


_BEFORE = Path("原配色.xml")
_AFTER = Path("新配色.xml")


def compare(before, after, paths, *, numeric=False):
    files = {
        name: value.encode("utf-8") if isinstance(value, str) else value
        for name, value in ((_BEFORE, before), (_AFTER, after))
        if value is not None
    }

    def open_bytes(path, mode):
        if mode != "rb" or path not in files:
            raise AssertionError("只应以二进制只读方式打开指定文件")
        return BytesIO(files[path])

    with patch.object(Path, "is_file", new=lambda path: path in files), patch.object(Path, "open", new=open_bytes):
        return compare_xml(_BEFORE, _AFTER, paths, numeric=numeric)


def appearance(value):
    return f"<界面><显示><亮度>{value}</亮度></显示></界面>"


class XmlCheckTests(unittest.TestCase):
    def test_reports_only_requested_text_in_order(self):
        before = "<界面><主题>冷 &amp; 暖</主题><字号>12</字号><边框>细</边框></界面>"
        after = "<界面><主题><![CDATA[冷 & 暖]]></主题><字号>14</字号><边框>粗</边框></界面>"
        paths = [".//字号", "./主题", "未设置"]
        result = compare(before, after, paths)
        self.assertFalse(result["ok"])
        self.assertEqual([c["path"] for c in result["checks"]], paths)
        self.assertEqual([c["ok"] for c in result["checks"]], [False, True, False])
        self.assertEqual((result["checks"][0]["before"], result["checks"][0]["after"]), ("12", "14"))
        self.assertEqual(result["checks"][1]["after"], "冷 & 暖")
        self.assertTrue(compare(before, after, ["主题"])["ok"])

    def test_text_mode_does_not_strip_or_normalize_numbers(self):
        for right in ("12.0", " 12", "12 "):
            with self.subTest(right=right):
                self.assertFalse(compare(appearance("12"), appearance(right), ["显示/亮度"])["ok"])

    def test_each_side_must_supply_one_nonblank_leaf(self):
        bad = (
            ("<界面/>", "未命中"),
            ("<界面><字号>12</字号><字号>12</字号></界面>", "多个"),
            ("<界面><字号>12<单位>点</单位></字号></界面>", "叶节点"),
            ("<界面><字号/></界面>", "文本缺失"),
            ("<界面><字号>  \n </字号></界面>", "文本缺失"),
        )
        good = "<界面><字号>12</字号></界面>"
        for doc, reason in bad:
            for before, after, side in ((doc, good, "before"), (good, doc, "after"), (doc, doc, "before")):
                with self.subTest(doc=doc, before=before, after=after):
                    result = compare(before, after, ["字号"])
                    self.assertFalse(result["ok"])
                    self.assertFalse(result["checks"][0]["ok"])
                    self.assertIsNone(result["checks"][0][side])
                    self.assertIn(reason, result["checks"][0]["error"])

    def test_relative_paths_and_namespace_identity(self):
        xml = '<界面 xmlns:d="urn:配色"><色号>青</色号><d:色号>蓝</d:色号></界面>'
        result = compare(xml, xml, [".//{urn:配色}色号", "./色号"])
        self.assertTrue(result["ok"])
        self.assertEqual([c["before"] for c in result["checks"]], ["蓝", "青"])
        for path in ("", "/界面/色号", "../色号", "色号[1]", "*", "./@色号"):
            with self.subTest(path=path):
                self.assertFalse(compare(xml, xml, [path])["ok"])
        self.assertEqual(compare(xml, xml, []), {"ok": False, "checks": []})

    def test_numeric_comparison_is_decimal_and_ordered(self):
        cases = (
            ("10", "+1.00E+1", True),
            (" -0  .15 ", "0\n15e-2", True),
            ("[.1250, +2E-1)", "[0.125,0.20)", True),
            ("0.1234567890123456789012345678901", "0.1234567890123456789012345678902", False),
            ("2", "2 2", False),
            ("2", "[2]", False),
            ("[0,1]", "(0,1]", False),
            ("0 1", "1 0", False),
        )
        for left, right, expected in cases:
            with self.subTest(left=left, right=right):
                result = compare(appearance(left), appearance(right), ["显示/亮度"], numeric=True)
                self.assertIs(result["ok"], expected)
                self.assertEqual((result["checks"][0]["before"], result["checks"][0]["after"]), (left, right))

    def test_numeric_mode_rejects_bad_values_even_if_identical(self):
        for text in ("NaN", "-Infinity", "0xff", "蓝2", "2点", "1,,2", "1,", "[1 2", "1.1.2"):
            with self.subTest(text=text):
                xml = appearance(text)
                self.assertFalse(compare(xml, xml, [".//亮度"], numeric=True)["ok"])

    def test_utf8_and_both_utf16_byte_orders(self):
        xml = "<主题>浅青色</主题>"
        for bom, codec in ((codecs.BOM_UTF16_LE, "utf-16-le"), (codecs.BOM_UTF16_BE, "utf-16-be")):
            with self.subTest(codec=codec):
                other = bom + ('<?xml version="1.0" encoding="UTF-16"?>' + xml).encode(codec)
                result = compare(codecs.BOM_UTF8 + xml.encode("utf-8"), other, ["."])
                self.assertTrue(result["ok"], result)

    def test_dtd_declarations_are_rejected_in_either_byte_order(self):
        for dtd in (
            '<!DOCTYPE 界面>',
            '<!DOCTYPE 界面 [<!ENTITY 配色 "浅色">]>',
            '<!DOCTYPE 界面 SYSTEM "urn:示例:主题">',
        ):
            for codec in ("utf-8", "utf-16-le", "utf-16-be"):
                with self.subTest(dtd=dtd, codec=codec):
                    xml = (dtd + appearance("4")).encode(codec)
                    result = compare(appearance("4"), xml, [".//亮度"])
                    self.assertFalse(result["ok"])
                    self.assertIn("DTD", result["checks"][0]["error"])
                    self.assertEqual(result["checks"][0]["before"], "4")

    def test_unreadable_or_invalid_file_is_not_a_match(self):
        for doc in (None, b"", "<界面><字号>12", b'<?xml version="1.0" encoding="iso-8859-1"?><Theme>blue</Theme>'):
            with self.subTest(doc=doc):
                result = compare(doc, doc, ["."])
                self.assertFalse(result["ok"])
                self.assertIsNone(result["checks"][0]["before"])
                self.assertIsNone(result["checks"][0]["after"])
                self.assertIn("前文件", result["checks"][0]["error"])
                self.assertIn("后文件", result["checks"][0]["error"])

    def test_size_limit_counts_bytes_not_characters(self):
        doc = "<主题>蓝</主题>".encode("utf-8")
        with patch("atkcli.xmlcheck.MAX_XML_BYTES", len(doc)):
            self.assertTrue(compare(doc, doc, ["."])["ok"])
            result = compare(doc + b" ", doc, ["."])
        self.assertFalse(result["ok"])
        self.assertIn("字节上限", result["checks"][0]["error"])
