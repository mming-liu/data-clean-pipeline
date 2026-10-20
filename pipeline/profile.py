"""数据体检：不改数据，只诊断。输出问题清单供客户开工前确认规则。"""
from __future__ import annotations

import re

from . import rules as R

DIGIT_RE = re.compile(r"\d")


def _pattern_of(value: str) -> str:
    """把具体值抽象成「格式骨架」，用于统计同义格式变体，如 2024-07-05 → 9999-99-99。"""
    return DIGIT_RE.sub("9", R.normalize_text(value))


def _infer_type(values: list[str]) -> str:
    present = [v for v in values if v != ""]
    if not present:
        return "空列"
    if all(_looks_like_phone(v) for v in present):
        return "手机号"
    if all(R.parse_date(v)[0] for v in present):
        return "日期"
    if all(_is_int(v) for v in present):
        return "整数"
    if all(R.parse_number(v)[0] is not None for v in present):
        return "数值"
    return "文本"


def _phone_digits(value: str) -> str:
    digits = R.PHONE_STRIP_RE.sub("", R.normalize_text(value)).replace("+", "")
    if digits.startswith("86") and len(digits) == 13:
        digits = digits[2:]
    return digits


def _looks_like_phone(value: str) -> bool:
    digits = _phone_digits(value)
    return digits.isdigit() and len(digits) == 11 and digits.startswith("1")


def _is_int(value: str) -> bool:
    num, _ = R.parse_number(value)
    return num is not None and num == int(num)


def profile_table(header: list[str], rows: list[dict]) -> dict:
    total = len(rows)
    fields: dict[str, dict] = {}

    for col in header:
        values = [R.normalize_text(str(r.get(col, "") or "")) for r in rows]
        present = [v for v in values if v != ""]
        blank = total - len(present)
        unique = len(set(present))
        patterns: dict[str, int] = {}
        for v in present:
            p = _pattern_of(v)
            patterns[p] = patterns.get(p, 0) + 1

        flags: list[str] = []
        if blank:
            flags.append(f"{blank} 行空值")
        if any(v != str(v).strip() or "\u3000" in str(v) for v in values):
            flags.append("存在全角空格/首尾空白")
        if len(patterns) > 1 and _infer_type(values) in ("日期", "数值", "整数"):
            flags.append(f"{len(patterns)} 种格式混用")

        # 该是数字却带符号/千分位
        noisy = [v for v in present if R.parse_number(v)[1] and R.parse_number(v)[0] is not None]
        if noisy:
            flags.append(f"{len(noisy)} 个值含货币符号/千分位")

        # 枚举变体：归一后唯一值变少
        normalized_unique = len({R.normalize_text(v).lower() for v in present})
        if unique > normalized_unique:
            flags.append("大小写/空白不一致")

        # 手机号位数异常：只看「像号码」的值（10~13 位纯数字），避免误伤日期和数量
        bad_phone = []
        for v in present:
            digits = _phone_digits(v)
            if digits.isdigit() and 10 <= len(digits) <= 13 and len(digits) != 11:
                bad_phone.append(v)
        if bad_phone:
            flags.append(f"{len(bad_phone)} 个号码位数不对")

        fields[col] = {
            "type": _infer_type(values),
            "blank": blank,
            "blank_pct": round(blank / total * 100, 1) if total else 0.0,
            "unique": unique,
            "format_variants": len(patterns),
            "samples": present[:5],
            "flags": flags,
        }

    exact_dup = 0
    seen: set[tuple] = set()
    for row in rows:
        key = tuple(R.normalize_text(str(row.get(c, "") or "")) for c in header)
        if key in seen:
            exact_dup += 1
        else:
            seen.add(key)

    key_col = header[0] if header else None
    key_dup = 0
    if key_col:
        counts: dict[str, int] = {}
        for row in rows:
            k = R.normalize_text(str(row.get(key_col, "") or ""))
            if k:
                counts[k] = counts.get(k, 0) + 1
        key_dup = sum(c - 1 for c in counts.values() if c > 1)

    return {
        "rows": total,
        "columns": len(header),
        "exact_duplicate_rows": exact_dup,
        "key_duplicate_rows": key_dup,
        "key_column": key_col,
        "fields": fields,
        "problem_columns": [c for c, f in fields.items() if f["flags"]],
    }


def compare(before: dict, after: dict) -> dict:
    """清洗前后体检对比，用于报告里的「改善幅度」。"""
    rows = []
    for col, info in before["fields"].items():
        after_info = after["fields"].get(col, {})
        rows.append(
            {
                "column": col,
                "type_before": info["type"],
                "type_after": after_info.get("type", "-"),
                "blank_before_pct": info["blank_pct"],
                "blank_after_pct": after_info.get("blank_pct", 0.0),
                "variants_before": info["format_variants"],
                "variants_after": after_info.get("format_variants", 0),
                "flags_before": info["flags"],
                "flags_after": after_info.get("flags", []),
            }
        )
    return {
        "rows": rows,
        "dup_before": before["exact_duplicate_rows"],
        "dup_after": after["exact_duplicate_rows"],
        "problem_cols_before": len(before["problem_columns"]),
        "problem_cols_after": len(after["problem_columns"]),
    }
