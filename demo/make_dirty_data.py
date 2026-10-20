"""生成一份「像真实业务系统导出的脏表」用于演示。

刻意埋的坑（每一个都在报告里能对上号）：
  日期 5 种写法混用 / 区域带「区」和空格 / 数量带单位 / 金额带 ¥ 和千分位 /
  手机号三种写法 + 位数不足 / 状态中英文混用 / 空值 / 超出区间的极大值 / 重复上报
"""
from __future__ import annotations

import csv
import random
from datetime import date, timedelta
from pathlib import Path

REGIONS = ["华东", "华北", "华南", "西南", "华中"]
REGION_DIRTY = {
    "华东": ["华东", "华东区", " 华东 ", "华\u3000东"],
    "华北": ["华北", "华北区", "华北 "],
    "华南": ["华南", "华南区", " 华南"],
    "西南": ["西南", "西南区"],
    "华中": ["华中", "华中区"],
}
CHANNELS = ["官网", "天猫", "京东", "线下门店", "小程序"]
CHANNEL_DIRTY = ["官网", "自营商城", "天猫", "天猫旗舰店", "京东", "京东自营", "线下门店", "门店", "小程序"]
CATEGORIES = ["办公耗材", "数码配件", "清洁用品", "包装材料"]
PRODUCTS = {
    "办公耗材": ["A4 复印纸", "中性笔 0.5mm", "文件夹", "订书机"],
    "数码配件": ["Type-C 数据线", "无线鼠标", "蓝牙键盘", "USB 集线器"],
    "清洁用品": ["洗手液 500ml", "抽纸 3 层", "垃圾袋", "消毒湿巾"],
    "包装材料": ["气泡膜", "封箱胶带", "瓦楞纸箱", "珍珠棉"],
}
STATUS_DIRTY = ["已支付", "已支付", "已支付", "paid", "PAID", "已完成", "待支付", "pending", "已取消", "取消", "refund", ""]

DATE_STYLES = ["%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y%m%d", "%Y年%m月%d日"]


def _dirty_date(d: date, rng: random.Random) -> str:
    if rng.random() < 0.06:
        return ""
    return d.strftime(rng.choice(DATE_STYLES))


def _dirty_amount(value: float, rng: random.Random) -> str:
    roll = rng.random()
    if roll < 0.06:
        return ""
    if roll < 0.35:
        return f"¥{value:,.2f}"
    if roll < 0.5:
        return f"{value:,.2f}"
    if roll < 0.6:
        return f"{value:.2f}元"
    return f"{value:.2f}"


def _dirty_phone(rng: random.Random) -> str:
    head = rng.choice(["138", "139", "150", "186", "177", "199"])
    body = "".join(rng.choice("0123456789") for _ in range(8))
    full = head + body
    roll = rng.random()
    if roll < 0.08:
        return ""
    if roll < 0.3:
        return f"{full[:3]}-{full[3:7]}-{full[7:]}"
    if roll < 0.45:
        return f"{full[:3]} {full[3:7]} {full[7:]}"
    if roll < 0.55:
        return f"+86{full}"
    if roll < 0.6:
        return full[:10]  # 少一位，必须被识别为非法
    return full


def _dirty_qty(rng: random.Random) -> str:
    qty = rng.randint(1, 24)
    roll = rng.random()
    if roll < 0.05:
        return ""
    if roll < 0.2:
        return f" {qty} "
    if roll < 0.28:
        return f"{qty}件"
    if roll < 0.3:
        return "-1"  # 负数量，业务上不成立
    if roll < 0.315:
        return "96"  # 疑似录入错误的大单，IQR 会标记为待复核
    return str(qty)


def make_dirty(path: str | Path, rows: int = 260, seed: int = 20261020) -> dict:
    rng = random.Random(seed)
    out_rows: list[list[str]] = []
    start = date(2026, 7, 1)

    for i in range(1, rows + 1):
        region = rng.choice(REGIONS)
        category = rng.choice(CATEGORIES)
        day = start + timedelta(days=rng.randint(0, 61))
        qty_raw = _dirty_qty(rng)
        try:
            qty_num = int(str(qty_raw).strip().replace("件", "") or 0)
        except ValueError:
            qty_num = 0
        unit_price = round(rng.uniform(12, 680), 2)
        amount = round(max(qty_num, 0) * unit_price, 2)

        out_rows.append(
            [
                f"SO2026{i:05d}",
                _dirty_date(day, rng),
                rng.choice(REGION_DIRTY[region]),
                rng.choice(CHANNEL_DIRTY),
                rng.choice([category, category + "类"]),
                rng.choice(PRODUCTS[category]),
                qty_raw,
                f"{unit_price:.2f}",
                _dirty_amount(amount, rng),
                _dirty_phone(rng),
                rng.choice(STATUS_DIRTY),
            ]
        )

    # 重复上报：同一条订单被两个门店各报一次，其中一份更残缺
    duplicates: list[list[str]] = []
    for i, row in enumerate(rng.sample(out_rows, 30)):
        clone = list(row)
        clone[6] = ""            # 数量缺失
        clone[9] = ""            # 手机号缺失
        clone[10] = ""           # 状态缺失
        if i < 8:
            clone[0] = ""        # 主键丢失：粘表时把第一列带掉了，无法合并，只能单列剔除
        duplicates.append(clone)

    # 完全重复行
    exact = [list(r) for r in rng.sample(out_rows, 12)]

    final = out_rows + duplicates + exact
    rng.shuffle(final)

    header = [
        "order_id", "order_date", "region", "channel", "category", "product",
        "qty", "unit_price", "amount", "customer_phone", "status",
    ]
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(header)
        writer.writerows(final)

    return {
        "path": str(path),
        "rows": len(final),
        "base_rows": rows,
        "duplicate_rows": len(duplicates) + len(exact),
        "exact_duplicate_rows": len(exact),
    }


if __name__ == "__main__":
    import json

    info = make_dirty(Path(__file__).resolve().parent.parent / "raw" / "orders_dirty.csv")
    print(json.dumps(info, ensure_ascii=False, indent=2))
