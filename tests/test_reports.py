"""所有样例均为手写的合成地面站下传文本，不读取场景或报表文件。"""

import unittest

from atkcli.reports import check_cadence, parse_report


HEADER = "Access    Start Time (UTCG)    Stop Time (UTCG)    Duration (sec)"
ROW1 = "1   29 Feb 2000 23:59:59.999    1 Mar 2000 00:00:01.000    1.000"
ROW2 = "2    1 Mar 2000 00:00:02.004    1 Mar 2000 00:00:05.004    3.000"


def report(*body):
    return "\n".join((HEADER, *body))


class ReportTests(unittest.TestCase):
    def test_english_statistics_and_midnight_rounding(self):
        text = "﻿ 合成地面站下传：窗口报表\r\n" + report(
            "------    -----------------------    -----------------------    --------------",
            "   " + ROW1 + "   ", ROW2, "", "Global Statistics", "-----------------",
            "Min Duration    " + ROW1, "Max Duration    " + ROW2,
            "Mean Duration      2.000", "Total Duration     4.000",
        )
        result = parse_report(text)
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "OK")
        self.assertEqual(result["windows"], [
            {"start_utc": "2000-02-29T23:59:59.999Z", "end_utc": "2000-03-01T00:00:01.000Z", "dur_s": 1.0},
            {"start_utc": "2000-03-01T00:00:02.004Z", "end_utc": "2000-03-01T00:00:05.004Z", "dur_s": 3.0},
        ])
        self.assertEqual(result["statistics"], {
            "min_duration_s": 1.0, "max_duration_s": 3.0,
            "mean_duration_s": 2.0, "total_duration_s": 4.0,
        })

    def test_chinese_months_and_statistics(self):
        result = parse_report("""  序号   开始时间（UTC）  结束时间（UTC）  持续时间（秒）
            1    1 十二月 2000 08:00:00   1 12月 2000 08:00:02   2.000
            2    2000年12月1日 08:01:00   2000年十二月1日 08:01:04   4.000
            全局统计：
            窗口数：2
            最短持续时间（秒）：2.000
            最长持续时间（秒）：4.000
            平均持续时间（秒）：3.000
            总持续时间（秒）：6.000   """)
        self.assertEqual(result["windows"][0]["start_utc"], "2000-12-01T08:00:00Z")
        self.assertEqual(result["statistics"], {
            "count": 2, "min_duration_s": 2.0, "max_duration_s": 4.0,
            "mean_duration_s": 3.0, "total_duration_s": 6.0,
        })

    def test_localized_stop_heading_and_wide_spaces(self):
        result = parse_report("访问   开始时间(UTCG)   停止时间(UTCG)   持续时间(秒)\n"
                              "1　1　十月　2000 00:00:00　1 十月 2000 00:00:02　2")
        self.assertEqual(result["windows"][0]["end_utc"], "2000-10-01T00:00:02Z")

    def test_iso_csv_and_missing_statistics(self):
        result = parse_report('编号,start_utc,end_utc,dur_s\n 1, "2000-01-02T00:00:00Z", 2000-01-02T00:00:01+00:00, 1')
        self.assertEqual(result["windows"], [{"start_utc": "2000-01-02T00:00:00Z", "end_utc": "2000-01-02T00:00:01Z", "dur_s": 1.0}])
        self.assertEqual(result["statistics"], {})

    def test_no_analysis_is_not_an_empty_result(self):
        for text in (
            "  无可用的分析数据。  ", "合成地面站下传报表\nNo analysis data available.",
            "ATK 无分析数据", "没有可用于此报告的分析数据。", "No analysis data found for this report.",
            report("没有分析数据"),
        ):
            with self.subTest(text=text):
                self.assertEqual(parse_report(text), {"ok": False, "status": "NO_ANALYSIS_DATA"})
        for text in ("", HEADER, report("Global Statistics", "Count: 0"), report(ROW1, "无分析数据")):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_report(text)

    def test_diagnostics_truncation_and_unknown_layout(self):
        for text in (
            "NACK\n无分析数据", report(ROW1, "NACK"), report(ROW1, "…"),
            report(ROW1, "报表已截断"), report(ROW1, ROW2.rsplit(None, 1)[0]),
            report(ROW1, "Statistics"), report(ROW1, "Mean Duration:"),
            report(ROW1, "未识别的行"), report(ROW1, ROW2 + "   9"),
            "编号 未知A 未知B 秒\n" + ROW1,
            HEADER.replace("Stop Time (UTCG)", "Stop Time (GPS)") + "\n" + ROW1,
            HEADER.replace("UTCG", "UTC+8") + "\n" + ROW1,
            HEADER.replace("sec", "min") + "\n" + ROW1,
            report(ROW1.replace("23:59:59.999", "23:59:59.999+08:00")),
            report(ROW1.replace("29 Feb 2000", "29 Feb 2001")),
            report(ROW1.replace("Feb", "Xyz")), report(ROW1.replace("23:59", "24:59")),
        ):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_report(text)

    def test_detail_order_duration_and_summary_evidence(self):
        for text in (
            report(ROW1.replace("1.000", "1.004" , 1)),
            report(ROW1.rsplit(None, 1)[0] + "   -1.000"),
            report(ROW1, ROW2.replace("00:00:02.004", "00:00:00.004")),
            report(ROW2), report(ROW1, "3" + ROW2[1:]),
            report("1  2000-01-01T00:00:01Z  2000-01-01T00:00:01Z  0"),
            report("1  2000-01-01T00:00:01Z  2000-01-01T00:00:00Z  1"),
            report(ROW1, "Global Statistics", "Number of Accesses: 2"),
            report(ROW1, ROW2, "Statistics", "Total Duration: 9.000"),
            report(ROW1, "Min Duration    " + ROW2),
            report(ROW1, "Total Duration: 1.000", "Total Duration: 1.000"),
            report(ROW1, "Statistics", "Mean Duration: 1.000", ROW2),
        ):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_report(text)

    def test_statistics_separated_by_the_same_columns_as_details(self):
        result = parse_report(report(
            ROW1, "统计信息", HEADER, "最短时间    " + ROW1, "最长时间    " + ROW1,
            "平均时间: 1.000", "总时间: 1.000",
        ))
        self.assertEqual(len(result["windows"]), 1)
        self.assertEqual(len(result["statistics"]), 4)

    def test_aggregate_can_round_separately(self):
        result = parse_report(report(
            "1   2000-01-01T00:00:00.0000Z  2000-01-01T00:00:01.0004Z  1.000",
            "2   2000-01-01T00:01:00.0000Z  2000-01-01T00:01:01.0004Z  1.000",
            "Total Duration: 2.001",
        ))
        self.assertEqual(result["statistics"]["total_duration_s"], 2.001)


class CadenceTests(unittest.TestCase):
    def test_csv_first_column_only_and_decimal_steps(self):
        text = '''﻿Time (UTC),备注
           "2000-02-29T23:59:59.9Z","下传, 合成样本"
           2000-03-01T00:00:00.0Z,2001-12-31T23:59:58Z
           2000-03-01T00:00:00.1Z,2002-12-31T23:59:58Z'''
        result = check_cadence(text, 0.1)
        self.assertTrue(result["ok"])
        self.assertEqual((result["n"], result["min_step_s"], result["max_step_s"]), (3, 0.1, 0.1))

    def test_text_and_explicit_utc_without_header(self):
        text = """地面站下传采样
            时间（UTCG）    合成读数
            -----------------------    --------
            1 十二月 2000 12:00:00     10
            1 Dec 2000 12:00:02        11   """
        self.assertTrue(check_cadence(text)["ok"])
        one_column = " 2000-01-01T00:00:00.000000001Z \n 2000-01-01T00:00:00.000000002Z "
        self.assertEqual(check_cadence(one_column, 1e-9)["min_step_s"], 1e-9)

    def test_all_intervals_not_just_ends_or_average(self):
        text = "UTC,下传计数\n" + "\n".join(f"2000-01-01T00:00:{s:02},1" for s in (0, 2, 3, 6, 8))
        for expected in (None, 2.0):
            with self.subTest(expected=expected):
                result = check_cadence(text, expected)
                self.assertFalse(result["ok"])
                self.assertEqual((result["n"], result["min_step_s"], result["max_step_s"]), (5, 1.0, 3.0))
                self.assertEqual(result["bad_intervals"], 2)
        self.assertFalse(check_cadence("UTC\n2000-01-01T00:00:02Z\n2000-01-01T00:00:01Z")["ok"])
        self.assertFalse(check_cadence("UTC\n2000-01-01T00:00:02Z\n2000-01-01T00:00:02Z")["ok"])

    def test_series_rejects_bad_evidence(self):
        good = "2000-01-01T00:00:00Z,1\n2000-01-01T00:00:01Z,2"
        for text in (
            "", "Time (UTC)", good.splitlines()[0], "NACK\n" + good,
            "Time (GPS),读数\n" + good, "未知表头,读数\n" + good,
            "读数,UTC\n1,2000-01-01T00:00:00Z\n2,2000-01-01T00:00:01Z",
            good.replace("Z", ""), "Time (UTC),读数\n" + good.replace("Z", "+08:00"),
            "Time (UTC)  读数\n2000-01-01T00:00:00 CST  1\n2000-01-01T00:00:01 CST  2",
            good.replace("2000-01-01", "2000-02-30"), good + "\n...",
            good + '\n"2000-01-01T00:00:02Z,3', good + "\n2000-01-01T00:00:02Z",
            "Time (UTCG)    读数\n2000-01-01T00:00:00    1\n2000-01-01T00:00:01",
            "时间 北京时间  读数\n" + good.replace(",", "  "),
            "Time GPS    读数\n" + good.replace(",", "  "),
            "UTC  读数\n2000-01-01T00:00:00  北京时间  1\n2000-01-01T00:00:01  北京时间  2",
        ):
            with self.subTest(text=text), self.assertRaises(ValueError):
                check_cadence(text)
        for expected in (float("nan"), float("inf"), 0, -1):
            with self.subTest(expected=expected), self.assertRaises(ValueError):
                check_cadence(good, expected)


if __name__ == "__main__":
    unittest.main()
