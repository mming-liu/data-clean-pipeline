"""清洗规则与主流程的边界测试。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline import cleaner, profile  # noqa: E402
from pipeline import rules as R  # noqa: E402


class TestParsers(unittest.TestCase):
    def test_date_formats(self):
        cases = {
            "2026-07-05": "2026-07-05",
            "2026/7/5": "2026-07-05",
            "2026.07.05": "2026-07-05",
            "20260705": "2026-07-05",
            "2026年7月5日": "2026-07-05",
            "2026-07-05 13:22:11": "2026-07-05",
        }
        for raw, expected in cases.items():
            self.assertEqual(R.parse_date(raw)[0], expected, raw)

    def test_date_garbage(self):
        for raw in ("", "  ", "待定", "2026-13-45", "abc"):
            self.assertIsNone(R.parse_date(raw)[0], raw)

    def test_number(self):
        self.assertEqual(R.parse_number("¥1,234.50")[0], 1234.5)
        self.assertEqual(R.parse_number("1234.5元")[0], 1234.5)
        self.assertEqual(R.parse_number("（1,234.50）")[0], -1234.5)  # 中文括号按负数处理
        self.assertEqual(R.parse_number("(123.45)")[0], -123.45)
        self.assertIsNone(R.parse_number("N/A")[0])

    def test_int_with_unit(self):
        self.assertEqual(R.parse_int("3件")[0], 3)
        self.assertEqual(R.parse_int(" 12 ")[0], 12)
        self.assertEqual(R.parse_int("2.6")[0], 3)
        self.assertIsNone(R.parse_int("")[0])

    def test_phone(self):
        self.assertEqual(R.parse_phone("138-0013-8000")[0], "13800138000")
        self.assertEqual(R.parse_phone("138 0013 8000")[0], "13800138000")
        self.assertEqual(R.parse_phone("+8613800138000")[0], "13800138000")
        self.assertIsNone(R.parse_phone("1380013800")[0])   # 少一位
        self.assertIsNone(R.parse_phone("12345678901")[0])  # 号段不合法

    def test_enum(self):
        self.assertEqual(R.map_enum("PAID", {"paid": "已支付"}, )[0], "已支付")
        self.assertEqual(R.map_enum(" 华东区 ", {"华东区": "华东"})[0], "华东")
        self.assertIsNone(R.map_enum("火星区", {"华东区": "华东"})[0])

    def test_safe_eval(self):
        self.assertEqual(R.safe_eval("qty * unit_price", {"qty": "2", "unit_price": "3.5"}), 7.0)
        self.assertIsNone(R.safe_eval("qty * unit_price", {"qty": "", "unit_price": "3"}))
        self.assertIsNone(R.safe_eval("__import__('os').system('echo x')", {}))  # 必须拒绝任意代码

    def test_text_normalize(self):
        self.assertEqual(R.normalize_text("　华东 \n"), "华东")
        self.assertEqual(R.normalize_text(None), "")


HEADER = ["order_id", "order_date", "region", "channel", "category", "qty", "unit_price", "amount", "customer_phone", "status"]


def make_rows(specs: list[list[str]]) -> list[dict]:
    return [dict(zip(HEADER, spec)) for spec in specs]


class TestCleanFlow(unittest.TestCase):
    def test_full_clean(self):
        rows = make_rows(
            [
                ["SO1", "2026/7/5", " 华东 ", "天猫", "办公耗材类", "3件", "10.00", "¥30.00", "138-0013-8000", "paid"],
                ["SO2", "2026年7月6日", "华东区", "门店", "清洁用品", " 5 ", "20.00", "", "13800138001", "PENDING"],
                ["SO3", "", "华北", "京东", "包装材料", "", "5.00", "", "", ""],
            ]
        )
        result = cleaner.clean_table(HEADER, rows)
        out = {r["order_id"]: r for r in result.rows}

        self.assertEqual(out["SO1"]["order_date"], "2026-07-05")
        self.assertEqual(out["SO1"]["region"], "华东")
        self.assertEqual(out["SO1"]["category"], "办公耗材")
        self.assertEqual(out["SO1"]["qty"], "3")
        self.assertEqual(out["SO1"]["amount"], "30.00")
        self.assertEqual(out["SO1"]["customer_phone"], "13800138000")
        self.assertEqual(out["SO1"]["status"], "已支付")

        self.assertEqual(out["SO2"]["channel"], "线下门店")
        self.assertEqual(out["SO2"]["amount"], "100.00")  # 空金额由 qty*price 回算
        self.assertEqual(out["SO2"]["status"], "待支付")

        # 日期缺失 → 用众数填充；数量缺失 → 用中位数填充
        self.assertEqual(out["SO3"]["order_date"], "2026-07-05")
        self.assertEqual(out["SO3"]["qty"], "4")  # (3+5)/2 = 4，整数列取整
        self.assertEqual(result.stats["rejected_rows"], 0)

    def test_dedupe_keeps_most_complete(self):
        rows = make_rows(
            [
                ["SO1", "2026-07-05", "华东", "天猫", "办公耗材", "3", "10.00", "30.00", "", ""],
                ["SO1", "2026-07-05", "华东", "天猫", "办公耗材", "3", "10.00", "30.00", "13800138000", "已支付"],
            ]
        )
        result = cleaner.clean_table(HEADER, rows)
        self.assertEqual(len(result.rows), 1)
        self.assertEqual(result.rows[0]["customer_phone"], "13800138000")  # 保留字段更全的
        self.assertEqual(result.stats["deduped_rows"], 1)

    def test_missing_required_goes_to_rejected_not_lost(self):
        rows = make_rows(
            [
                ["", "2026-07-05", "华东", "天猫", "办公耗材", "3", "10.00", "30.00", "13800138000", "已支付"],
                ["SO2", "2026-07-05", "华东", "天猫", "办公耗材", "3", "10.00", "30.00", "13800138000", "已支付"],
            ]
        )
        result = cleaner.clean_table(HEADER, rows)
        self.assertEqual(len(result.rows), 1)
        self.assertEqual(len(result.rejected), 1)
        self.assertIn("order_id", result.rejected[0]["_剔除原因"])
        # 行数守恒：进 = 出 + 剔除 + 去重
        s = result.stats
        self.assertEqual(s["src_rows"], s["out_rows"] + s["rejected_rows"] + s["deduped_rows"])

    def test_row_count_never_leaks(self):
        """随机脏数据也要保证行数守恒，这是清洗交付的底线。"""
        rows = []
        for i in range(50):
            rows.append(
                [
                    f"SO{i}" if i % 10 else "",
                    ["2026-07-05", "", "2026/7/6", "bad"][i % 4],
                    ["华东", "", "华东区"][i % 3],
                    "天猫",
                    "办公耗材",
                    ["", "3", "3件"][i % 3],
                    "10.00",
                    "",
                    ["13800138000", "138-0013-8000", ""][i % 3],
                    ["paid", "", "已支付"][i % 3],
                ]
            )
        result = cleaner.clean_table(HEADER, make_rows(rows))
        s = result.stats
        self.assertEqual(s["src_rows"], s["out_rows"] + s["rejected_rows"] + s["deduped_rows"])
        for row in result.rows:
            for col in cleaner.DEFAULT_CONFIG["required"]:
                self.assertNotEqual(row[col], "", f"{col} 不应为空")

    def test_outlier_flagged_not_deleted(self):
        rows = make_rows([[f"SO{i}", "2026-07-05", "华东", "天猫", "办公耗材", "2", "10.00", "20.00", "", "已支付"] for i in range(12)])
        rows.append(
            dict(zip(HEADER, ["SO99", "2026-07-05", "华东", "天猫", "办公耗材", "2", "10.00", "999999.00", "", "已支付"]))
        )
        result = cleaner.clean_table(HEADER, rows)
        self.assertEqual(len(result.rows), 13)  # 异常值不删行
        self.assertGreater(result.stats["outliers_flagged"], 0)


class TestProfile(unittest.TestCase):
    def test_detects_problems(self):
        rows = make_rows(
            [
                ["SO1", "2026/7/5", " 华东 ", "天猫", "办公耗材", "3件", "10.00", "¥30.00", "138-0013-8000", "paid"],
                ["SO1", "20260706", "华东区", "天猫", "办公耗材类", "4", "10.00", "40.00", "1380013800", "PAID"],
            ]
        )
        prof = profile.profile_table(HEADER, rows)
        self.assertGreaterEqual(prof["exact_duplicate_rows"], 0)
        self.assertGreaterEqual(prof["key_duplicate_rows"], 1)
        self.assertIn("order_date", prof["problem_columns"])
        self.assertEqual(prof["fields"]["order_date"]["format_variants"], 2)

    def test_after_is_cleaner(self):
        rows = make_rows(
            [["SO1", "2026/7/5", " 华东 ", "天猫", "办公耗材", "3件", "10.00", "¥30.00", "", "paid"]] * 2
        )
        before = profile.profile_table(HEADER, rows)
        result = cleaner.clean_table(HEADER, rows)
        after = profile.profile_table(HEADER, result.rows)
        self.assertGreaterEqual(len(before["problem_columns"]), len(after["problem_columns"]))
        self.assertEqual(after["fields"]["order_date"]["format_variants"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
