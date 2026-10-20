"""解析与清洗原语：所有「把脏值变成标准值」的判断都集中在这里。

每个解析函数统一返回 (标准值, 是否发生了修改)，便于逐格留痕。
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field

# 全角空格、零宽字符、不换行空格、各类横线
INVISIBLES = dict.fromkeys(map(ord, "\u3000\u00a0\u200b\u200c\u200d\ufeff\t\r\n"), " ")
DASHES = dict.fromkeys(map(ord, "－—–−"), "-")

DATE_FORMATS = [
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%Y.%m.%d",
    "%Y%m%d",
    "%Y年%m月%d日",
    "%Y-%m-%d %H:%M:%S",
    "%Y/%m/%d %H:%M:%S",
    "%d/%m/%Y",
]

NUM_STRIP_RE = re.compile(r"[¥￥$,，\s元人民币]")
PHONE_STRIP_RE = re.compile(r"[-\s()（）]")
CN_MOBILE_RE = re.compile(r"^1[3-9]\d{9}$")


@dataclass
class Issue:
    """一条清洗留痕：哪一行、哪一列、原值、问题类型、做了什么、变成什么。"""

    row: int
    column: str
    raw: str
    kind: str
    action: str
    result: str


@dataclass
class CleanResult:
    header: list[str] = field(default_factory=list)
    rows: list[dict] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)
    outlier_rows: list[int] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    column_stats: dict = field(default_factory=dict)

    def issues_of(self, kind: str) -> list[Issue]:
        return [i for i in self.issues if i.kind == kind]


# ------------------------------------------------------------------ 文本
def normalize_text(value: str) -> str:
    """去全角空格 / 零宽字符 / 首尾空白，压缩连续空白。"""
    if value is None:
        return ""
    text = str(value).translate(INVISIBLES)
    text = re.sub(r"\s+", " ", text).strip()
    return text


# ------------------------------------------------------------------ 日期
def parse_date(value: str) -> tuple[str | None, bool]:
    """尝试多种格式解析日期，统一输出 YYYY-MM-DD。"""
    raw = normalize_text(value)
    if not raw:
        return None, False
    cleaned = raw.translate(DASHES).replace("T", " ")
    cleaned = cleaned.split(" ")[0] if " " in cleaned and ":" not in cleaned else cleaned

    for fmt in DATE_FORMATS:
        try:
            from datetime import datetime

            dt = datetime.strptime(cleaned, fmt)
            out = dt.strftime("%Y-%m-%d")
            return out, out != raw
        except ValueError:
            continue

    # Excel 序列号（纯数字 5 位）
    if cleaned.isdigit() and len(cleaned) == 5:
        from datetime import datetime, timedelta

        base = datetime(1899, 12, 30)
        return (base + timedelta(days=int(cleaned))).strftime("%Y-%m-%d"), True
    return None, False


# ------------------------------------------------------------------ 数值
def parse_number(value: str) -> tuple[float | None, bool]:
    """去掉货币符号、千分位、单位后转数字。"""
    raw = normalize_text(value)
    if not raw:
        return None, False
    cleaned = NUM_STRIP_RE.sub("", raw)
    cleaned = cleaned.replace("（", "-").replace("）", ")")
    # 括号表示负数： (123.45)
    negative = cleaned.startswith("(") and cleaned.endswith(")")
    cleaned = cleaned.strip("()")
    try:
        num = float(cleaned)
    except ValueError:
        return None, False
    if negative:
        num = -num
    return num, cleaned != raw or negative


def parse_int(value: str) -> tuple[int | None, bool]:
    """处理「3件」「3 个」这类带单位的整数。"""
    raw = normalize_text(value)
    if not raw:
        return None, False
    match = re.match(r"^([+-]?\d+(?:\.\d+)?)", NUM_STRIP_RE.sub("", raw))
    if not match:
        num, fixed = parse_number(raw)
        if num is None:
            return None, False
        return int(round(num)), fixed
    text = match.group(1)
    num = float(text)
    if num != int(num):
        return int(round(num)), True
    return int(num), text != raw


# ------------------------------------------------------------------ 手机号
def parse_phone(value: str) -> tuple[str | None, bool]:
    """统一手机号：去分隔符、去 +86/86 前缀，校验 11 位。"""
    raw = normalize_text(value)
    if not raw:
        return None, False
    digits = PHONE_STRIP_RE.sub("", raw)
    digits = digits.replace("+", "")
    if digits.startswith("86") and len(digits) == 13:
        digits = digits[2:]
    if digits.endswith(".0"):
        digits = digits[:-2]
    if not CN_MOBILE_RE.match(digits):
        return None, False
    # 脱敏不是本工具职责，原样保留完整号码，是否脱敏由业务方决定
    return digits, digits != raw


# ------------------------------------------------------------------ 枚举
def map_enum(value: str, mapping: dict[str, str], case_insensitive: bool = True) -> tuple[str | None, bool]:
    """按映射表归一枚举值；映射表里没命中时，尝试大小写/空白归一后再匹配。"""
    raw = normalize_text(value)
    if not raw:
        return None, False

    candidates = [raw]
    if case_insensitive:
        candidates += [raw.lower(), raw.upper()]
    for cand in candidates:
        if cand in mapping:
            mapped = mapping[cand]
            return mapped, mapped != raw

    lowered = {k.lower(): v for k, v in mapping.items()}
    if case_insensitive and raw.lower() in lowered:
        mapped = lowered[raw.lower()]
        return mapped, mapped != raw
    return None, False


# ------------------------------------------------------------------ 表达式
_ALLOWED_BINOPS = (ast.Add, ast.Sub, ast.Mult, ast.Div)


def safe_eval(expr: str, row: dict) -> float | None:
    """只允许 列名/数字 的四则运算，用于派生列，例如 qty * unit_price。"""
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        return None

    def _eval(node):
        if isinstance(node, ast.Expression):
            return _eval(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        if isinstance(node, ast.Name):
            value = row.get(node.id)
            if value in (None, ""):
                return None
            try:
                return float(value)
            except (TypeError, ValueError):
                return None
        if isinstance(node, ast.BinOp) and isinstance(node.op, _ALLOWED_BINOPS):
            left, right = _eval(node.left), _eval(node.right)
            if left is None or right is None:
                return None
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div):
                return None if right == 0 else left / right
        raise ValueError("不支持的表达式")

    try:
        return _eval(tree)
    except (ValueError, TypeError, ZeroDivisionError):
        return None


# ------------------------------------------------------------------ 统计
def median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def quantile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * q
    low = int(pos)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (pos - low)


def mode(values: list[str]) -> str | None:
    """众数。并列时取最先出现的那个，结果可复现、符合业务直觉。"""
    if not values:
        return None
    counts: dict[str, int] = {}
    first_at: dict[str, int] = {}
    for index, value in enumerate(values):
        counts[value] = counts.get(value, 0) + 1
        first_at.setdefault(value, index)
    return max(counts, key=lambda v: (counts[v], -first_at[v]))
