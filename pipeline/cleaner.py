"""清洗主流程：按「配置驱动」的方式把脏表洗成标准数据集。

处理顺序（顺序本身很重要，顺序错了结果就不可复现）：
  1 文本归一   → 2 类型化   → 3 枚举归一
  4 缺失填充   → 5 派生补全 → 6 异常标记
  7 去重       → 8 必填校验（不合格行单独出库，不静默删除）
"""
from __future__ import annotations

from . import rules as R

DEFAULT_CONFIG: dict = {
    "date_columns": ["order_date"],
    "int_columns": ["qty"],
    "float_columns": ["unit_price", "amount"],
    "phone_columns": ["customer_phone"],
    "enum_maps": {
        "status": {
            "已支付": "已支付",
            "paid": "已支付",
            "Paid": "已支付",
            "PAID": "已支付",
            "已完成": "已支付",
            "待支付": "待支付",
            "未支付": "待支付",
            "pending": "待支付",
            "已取消": "已取消",
            "取消": "已取消",
            "cancel": "已取消",
            "cancelled": "已取消",
            "退款": "退款",
            "已退款": "退款",
            "refund": "退款",
        },
        "region": {
            "华东": "华东",
            "华东区": "华东",
            "华 东": "华东",
            "华北": "华北",
            "华北区": "华北",
            "华南": "华南",
            "华南区": "华南",
            "西南": "西南",
            "西南区": "西南",
            "华中": "华中",
            "华中区": "华中",
        },
        "category": {
            "办公耗材": "办公耗材",
            "办公耗材类": "办公耗材",
            "数码配件": "数码配件",
            "数码配件类": "数码配件",
            "清洁用品": "清洁用品",
            "清洁用品类": "清洁用品",
            "包装材料": "包装材料",
            "包装材料类": "包装材料",
        },
        "channel": {
            "官网": "官网",
            "自营商城": "官网",
            "天猫": "天猫",
            "天猫旗舰店": "天猫",
            "京东": "京东",
            "京东自营": "京东",
            "线下门店": "线下门店",
            "门店": "线下门店",
            "小程序": "小程序",
        },
    },
    "ranges": {
        "qty": [1, 5000],
        "unit_price": [0.01, 100000],
        "amount": [0, 1000000],
    },
    "missing": {
        "qty": "median",
        "unit_price": "median",
        "region": "mode",
        "category": "mode",
        "channel": "mode",
        "status": "default:待支付",
        "order_date": "mode",
        "customer_phone": "keep",
    },
    "derive": {"amount": "qty * unit_price"},
    "dedupe": {"keys": ["order_id"], "keep": "most_complete"},
    "outliers": {"columns": ["amount"], "iqr_factor": 3.0, "action": "flag"},
    "required": ["order_id", "order_date", "qty", "unit_price"],
}


def _fmt(col: str, value: float, cfg: dict) -> str:
    if col in cfg.get("int_columns", []):
        return str(int(round(value)))
    if col in cfg.get("float_columns", []):
        return f"{value:.2f}"
    return str(value)


def clean_table(header: list[str], rows: list[dict], config: dict | None = None) -> R.CleanResult:
    cfg = {**DEFAULT_CONFIG, **(config or {})}
    result = R.CleanResult(header=list(header))
    issues = result.issues
    work = [{col: (row.get(col, "") or "") for col in header} for row in rows]
    src_rows = len(work)

    counters = {
        "cells_normalized": 0,
        "cells_typed": 0,
        "cells_enum": 0,
        "cells_filled": 0,
        "cells_derived": 0,
        "outliers": 0,
        "deduped": 0,
        "rejected": 0,
    }
    column_stats = {col: {"normalized": 0, "typed": 0, "enum": 0, "filled": 0, "unknown_enum": 0, "out_of_range": 0} for col in header}

    # ---------- 1 文本归一
    for idx, row in enumerate(work, start=1):
        for col in header:
            raw = row[col]
            if raw == "":
                continue
            cleaned = R.normalize_text(raw)
            if cleaned != raw:
                issues.append(R.Issue(idx, col, raw, "格式统一", "去全角空格/换行/多余空白", cleaned))
                column_stats[col]["normalized"] += 1
                counters["cells_normalized"] += 1
            row[col] = cleaned

    # ---------- 2 类型化
    for idx, row in enumerate(work, start=1):
        for col in cfg["date_columns"]:
            if col not in row or row[col] == "":
                continue
            value, fixed = R.parse_date(row[col])
            if value is None:
                issues.append(R.Issue(idx, col, row[col], "非法值", "无法识别日期，置空待回填", ""))
                row[col] = ""
                column_stats[col]["unknown_enum"] += 1
            elif fixed:
                issues.append(R.Issue(idx, col, row[col], "格式统一", "统一为 YYYY-MM-DD", value))
                row[col] = value
                column_stats[col]["typed"] += 1
                counters["cells_typed"] += 1

        for col in cfg["int_columns"]:
            if col not in row or row[col] == "":
                continue
            value, fixed = R.parse_int(row[col])
            if value is None:
                issues.append(R.Issue(idx, col, row[col], "非法值", "无法识别数字，置空待回填", ""))
                row[col] = ""
            elif fixed:
                issues.append(R.Issue(idx, col, row[col], "格式统一", "去掉单位/符号，转整数", str(value)))
                row[col] = str(value)
                column_stats[col]["typed"] += 1
                counters["cells_typed"] += 1

        for col in cfg["float_columns"]:
            if col not in row or row[col] == "":
                continue
            value, fixed = R.parse_number(row[col])
            if value is None:
                issues.append(R.Issue(idx, col, row[col], "非法值", "无法识别金额，置空待回填", ""))
                row[col] = ""
            elif fixed:
                issues.append(R.Issue(idx, col, row[col], "格式统一", "去货币符号/千分位，保留两位", f"{value:.2f}"))
                row[col] = f"{value:.2f}"
                column_stats[col]["typed"] += 1
                counters["cells_typed"] += 1

        for col in cfg["phone_columns"]:
            if col not in row or row[col] == "":
                continue
            value, fixed = R.parse_phone(row[col])
            if value is None:
                issues.append(R.Issue(idx, col, row[col], "非法值", "非 11 位大陆手机号，置空待回填", ""))
                row[col] = ""
                column_stats[col]["unknown_enum"] += 1
            elif fixed:
                issues.append(R.Issue(idx, col, row[col], "格式统一", "去分隔符/国际码，统一 11 位", value))
                row[col] = value
                column_stats[col]["typed"] += 1
                counters["cells_typed"] += 1

    # ---------- 3 枚举归一
    for idx, row in enumerate(work, start=1):
        for col, mapping in cfg["enum_maps"].items():
            if col not in row or row[col] == "":
                continue
            mapped, changed = R.map_enum(row[col], mapping)
            if mapped is None:
                issues.append(R.Issue(idx, col, row[col], "枚举未知", "不在映射表内，置空待业务确认", ""))
                row[col] = ""
                column_stats[col]["unknown_enum"] += 1
            elif changed:
                issues.append(R.Issue(idx, col, row[col], "枚举归一", "映射为标准取值", mapped))
                row[col] = mapped
                column_stats[col]["enum"] += 1
                counters["cells_enum"] += 1

    # ---------- 4 缺失填充
    for col, policy in cfg["missing"].items():
        if col not in header or policy == "keep":
            continue
        present = [row[col] for row in work if row[col] != ""]
        fill_value: str | None = None
        if policy.startswith("default:"):
            fill_value = policy.split(":", 1)[1]
        elif policy == "median":
            numbers = []
            for v in present:
                try:
                    numbers.append(float(v))
                except ValueError:
                    continue
            med = R.median(numbers)
            fill_value = _fmt(col, med, cfg) if med is not None else None
        elif policy == "mode":
            fill_value = R.mode(present)
        elif policy == "forward":
            last = None
            for row in work:
                if row[col] == "" and last is not None:
                    row[col] = last
                    counters["cells_filled"] += 1
                    column_stats[col]["filled"] += 1
                elif row[col] != "":
                    last = row[col]
            continue

        if fill_value is None:
            continue
        for idx, row in enumerate(work, start=1):
            if row[col] == "":
                issues.append(
                    R.Issue(idx, col, "", "缺失填充", f"按 {policy} 填充", fill_value)
                )
                row[col] = fill_value
                counters["cells_filled"] += 1
                column_stats[col]["filled"] += 1

    # ---------- 5 派生补全
    for target, expr in cfg["derive"].items():
        if target not in header:
            continue
        for idx, row in enumerate(work, start=1):
            if row[target] != "":
                continue
            value = R.safe_eval(expr, row)
            if value is None:
                continue
            text = _fmt(target, value, cfg)
            issues.append(R.Issue(idx, target, "", "派生补全", f"按 {expr} 回算", text))
            row[target] = text
            counters["cells_derived"] += 1

    # ---------- 6 异常标记（只标记，不擅自删数据）
    outlier_rows: list[int] = []
    for col in cfg.get("outliers", {}).get("columns", []):
        if col not in header:
            continue
        numbers = []
        for row in work:
            try:
                numbers.append(float(row[col]))
            except (TypeError, ValueError):
                continue
        if len(numbers) < 8:
            continue
        q1 = R.quantile(numbers, 0.25)
        q3 = R.quantile(numbers, 0.75)
        iqr = q3 - q1
        factor = cfg["outliers"].get("iqr_factor", 3.0)
        upper, lower = q3 + factor * iqr, q1 - factor * iqr
        for idx, row in enumerate(work, start=1):
            try:
                v = float(row[col])
            except (TypeError, ValueError):
                continue
            if v > upper or v < lower:
                issues.append(
                    R.Issue(idx, col, row[col], "异常值", f"超出 IQR×{factor:g} 区间 [{lower:.2f}, {upper:.2f}]，标记待复核", row[col])
                )
                counters["outliers"] += 1
                column_stats[col]["out_of_range"] += 1
                if idx not in outlier_rows:
                    outlier_rows.append(idx)

    for col, (lo, hi) in cfg.get("ranges", {}).items():
        if col not in header:
            continue
        for idx, row in enumerate(work, start=1):
            try:
                v = float(row[col])
            except (TypeError, ValueError):
                continue
            if v < lo or v > hi:
                issues.append(R.Issue(idx, col, row[col], "超出业务范围", f"应在 [{lo}, {hi}]，标记待复核", row[col]))
                counters["outliers"] += 1
                column_stats[col]["out_of_range"] += 1
                if idx not in outlier_rows:
                    outlier_rows.append(idx)

    # ---------- 7 去重
    keys = cfg.get("dedupe", {}).get("keys", [])
    if keys:
        groups: dict[tuple, list[tuple[int, dict]]] = {}
        keyless: list[tuple[int, dict]] = []
        for idx, row in enumerate(work, start=1):
            key = tuple(row.get(k, "") for k in keys)
            if all(k == "" for k in key):
                # 主键整行为空，无法参与去重：保留原行，交给必填校验决定去留，绝不静默丢弃
                keyless.append((idx, row))
                continue
            groups.setdefault(key, []).append((idx, row))

        kept: list[dict] = []
        for key, members in groups.items():
            if len(members) == 1:
                kept.append(members[0][1])
                continue
            # keep=most_complete：保留字段最全的一条，其次保留靠前的
            members_sorted = sorted(
                members,
                key=lambda item: (-sum(1 for v in item[1].values() if v != ""), item[0]),
            )
            keep_idx, keep_row = members_sorted[0]
            kept.append(keep_row)
            for idx, row in members_sorted[1:]:
                issues.append(
                    R.Issue(idx, ",".join(keys), " / ".join(key), "重复行", f"保留第 {keep_idx} 行（字段更全），删除本行", "")
                )
                counters["deduped"] += 1

        for idx, row in keyless:
            issues.append(R.Issue(idx, keys[0], "", "主键缺失", "无法参与去重，保留并交必填校验", ""))
            kept.append(row)

        # 保持原始顺序
        order_index = {id(row): idx for idx, row in enumerate(work)}
        kept.sort(key=lambda row: order_index.get(id(row), 0))
        work = kept

    # ---------- 8 必填校验
    required = cfg.get("required", [])
    passed: list[dict] = []
    rejected: list[dict] = []
    for row in work:
        missing = [col for col in required if row.get(col, "") == ""]
        if missing:
            row = dict(row)
            row["_剔除原因"] = "必填项为空：" + "、".join(missing)
            rejected.append(row)
            counters["rejected"] += 1
        else:
            passed.append(row)

    # ---------- 汇总
    result.rows = passed
    result.rejected = rejected
    result.outlier_rows = outlier_rows
    column_stats_out = {}
    for col in header:
        before_blank = sum(1 for r in rows if not R.normalize_text(str(r.get(col, "") or "")))
        after_blank = sum(1 for r in passed if r.get(col, "") == "")
        cs = column_stats[col]
        column_stats_out[col] = {
            "blank_before_pct": round(before_blank / src_rows * 100, 1) if src_rows else 0.0,
            "blank_after_pct": round(after_blank / len(passed) * 100, 1) if passed else 0.0,
            **{k: v for k, v in cs.items() if v},
        }
    result.column_stats = column_stats_out
    result.stats = {
        "src_rows": src_rows,
        "out_rows": len(passed),
        "rejected_rows": counters["rejected"],
        "deduped_rows": counters["deduped"],
        "cells_normalized": counters["cells_normalized"],
        "cells_typed": counters["cells_typed"],
        "cells_enum": counters["cells_enum"],
        "cells_filled": counters["cells_filled"],
        "cells_derived": counters["cells_derived"],
        "outliers_flagged": counters["outliers"],
        "total_fixes": sum(
            counters[k]
            for k in ("cells_normalized", "cells_typed", "cells_enum", "cells_filled", "cells_derived")
        ),
    }
    return result
