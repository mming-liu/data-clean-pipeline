"""生成自包含的 HTML 清洗报告：单文件、无外链、可直接发客户或打印成 PDF。"""
from __future__ import annotations

import html
import json
import time
from pathlib import Path

from . import profile as P
from .rules import CleanResult

E = html.escape

CSS = """
*{box-sizing:border-box}
body{margin:0;background:#eef2f7;color:#1c2530;font:14px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei","PingFang SC",sans-serif}
.wrap{max-width:1080px;margin:0 auto;padding:28px 22px 60px}
.hero{background:linear-gradient(135deg,#123a63,#1d5f9c);color:#fff;border-radius:14px;padding:30px 32px;box-shadow:0 10px 28px rgba(18,58,99,.22)}
.hero .tag{display:inline-block;font-size:12px;letter-spacing:.18em;background:rgba(255,255,255,.16);padding:4px 10px;border-radius:4px}
.hero h1{margin:14px 0 6px;font-size:26px;font-weight:700;letter-spacing:.5px}
.hero p{margin:0;color:#cddff2;font-size:13px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:14px;margin:20px 0 6px}
.kpi{background:#fff;border-radius:12px;padding:16px 18px;border:1px solid #e2e8f0;box-shadow:0 2px 6px rgba(24,42,66,.05)}
.kpi .lbl{font-size:12px;color:#64748b;letter-spacing:.04em}
.kpi .val{font-size:26px;font-weight:700;color:#123a63;line-height:1.25;margin-top:2px}
.kpi .val small{font-size:13px;font-weight:500;color:#64748b;margin-left:3px}
.kpi.good .val{color:#0f8a4d}
.kpi.warn .val{color:#c2410c}
section{background:#fff;border:1px solid #e2e8f0;border-radius:12px;padding:22px 24px;margin-top:18px;box-shadow:0 2px 6px rgba(24,42,66,.04)}
h2{font-size:16px;margin:0 0 4px;color:#123a63;letter-spacing:.02em}
h2 .num{display:inline-block;width:22px;height:22px;line-height:22px;text-align:center;background:#123a63;color:#fff;border-radius:5px;font-size:12px;margin-right:8px;vertical-align:2px}
.sub{color:#64748b;font-size:12.5px;margin:0 0 16px}
table{width:100%;border-collapse:collapse;font-size:13px}
th{background:#f1f5f9;color:#41536b;font-weight:600;text-align:left;padding:9px 10px;border-bottom:2px solid #dbe3ee;white-space:nowrap}
td{padding:8px 10px;border-bottom:1px solid #eef2f7;vertical-align:top}
tr:last-child td{border-bottom:none}
tbody tr:hover{background:#fafcff}
.mono{font-family:"Cascadia Mono",Consolas,"SF Mono",monospace;font-size:12.5px}
.raw{color:#9b2c2c;background:#fdf2f2;padding:1px 5px;border-radius:4px;text-decoration:line-through;text-decoration-color:#e0a0a0}
.arrow{color:#94a3b8;margin:0 5px}
.new{color:#0f6b3d;background:#effaf3;padding:1px 5px;border-radius:4px}
.bar{height:9px;border-radius:5px;background:#1d5f9c;display:inline-block;vertical-align:middle}
.bar.wrap-bar{display:flex;align-items:center;gap:10px}
.bar-track{flex:1;height:9px;background:#eef2f7;border-radius:5px;overflow:hidden}
.bar-fill{height:100%;background:linear-gradient(90deg,#1d5f9c,#3b82c4);border-radius:5px}
.bar-fill.warn{background:linear-gradient(90deg,#c2410c,#ea8c4a)}
.pill{display:inline-block;font-size:11.5px;padding:1px 8px;border-radius:20px;background:#eef2f7;color:#41536b;margin:1px 2px 1px 0}
.pill.red{background:#fdf2f2;color:#9b2c2c}
.pill.amber{background:#fffaf0;color:#9a6700}
.pill.green{background:#effaf3;color:#0f6b3d}
.empty{color:#94a3b8;font-size:13px;padding:10px 0}
.rule-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px}
.rule-card{background:#f8fafc;border:1px solid #e6edf5;border-radius:9px;padding:12px 14px}
.rule-card h3{margin:0 0 6px;font-size:13px;color:#123a63}
.rule-card .k{color:#64748b;font-size:12px}
.rule-card code{background:#eef2f7;padding:1px 5px;border-radius:4px;font-size:12px}
footer{margin-top:24px;color:#64748b;font-size:12px;text-align:center;line-height:1.9}
.legend{font-size:12px;color:#64748b;margin-top:10px}
"""


def _cell_change(raw: str, new: str) -> str:
    if raw == "" and new == "":
        return '<span class="mono" style="color:#cbd5e1">—</span>'
    if raw == new:
        return f'<span class="mono">{E(new)}</span>'
    if raw == "":
        return f'<span class="new mono">{E(new)}</span> <span class="pill green">补全</span>'
    return f'<span class="raw mono">{E(raw)}</span><span class="arrow">→</span><span class="new mono">{E(new)}</span>'


def _kind_pills(flags: list[str]) -> str:
    if not flags:
        return '<span class="pill green">无</span>'
    return "".join(f'<span class="pill amber">{E(f)}</span>' for f in flags)


def build_html(
    result: CleanResult,
    before: dict,
    after: dict,
    meta: dict,
    config: dict,
    sample_rows: list[tuple[int, dict, dict]] | None = None,
) -> str:
    stats = result.stats
    cmp = P.compare(before, after)
    src_rows = stats["src_rows"] or 1
    fix_rate = stats["total_fixes"] / (src_rows * max(len(result.header), 1)) * 100

    # ---------- KPI
    kpis = [
        ("源数据行数", f"{stats['src_rows']:,}", "", ""),
        ("交付行数", f"{stats['out_rows']:,}", f"剔除 {stats['rejected_rows']}", "good"),
        ("修复单元格", f"{stats['total_fixes']:,}", f"占比 {fix_rate:.1f}%", ""),
        ("缺失填充", f"{stats['cells_filled']:,}", "格", ""),
        ("去重删除", f"{stats['deduped_rows']:,}", "行", ""),
        ("异常待复核", f"{stats['outliers_flagged']:,}", "条", "warn"),
    ]
    kpi_html = "".join(
        f'<div class="kpi {cls}"><div class="lbl">{lbl}</div><div class="val">{val}'
        + (f"<small>{unit}</small>" if unit else "")
        + "</div></div>"
        for lbl, val, unit, cls in kpis
    )

    # ---------- 体检对比
    rows_html = []
    for row in cmp["rows"]:
        blank_txt = f"{row['blank_before_pct']}% → {row['blank_after_pct']}%"
        variant_txt = f"{row['variants_before']} → <b>{row['variants_after']}</b>"
        if row["variants_before"] != row["variants_after"]:
            variant_txt = f'<span style="color:#9b2c2c">{row["variants_before"]}</span> → <span style="color:#0f6b3d">{row["variants_after"]}</span>'
        rows_html.append(
            "<tr>"
            f'<td class="mono">{E(row["column"])}</td>'
            f"<td>{E(row['type_before'])}</td>"
            f"<td>{E(row['type_after'])}</td>"
            f"<td>{blank_txt}</td>"
            f"<td>{variant_txt}</td>"
            f"<td>{_kind_pills(row['flags_before'])}</td>"
            f"<td>{_kind_pills(row['flags_after'])}</td>"
            "</tr>"
        )

    # ---------- 问题分布
    kinds: dict[str, int] = {}
    for issue in result.issues:
        kinds[issue.kind] = kinds.get(issue.kind, 0) + 1
    max_count = max(kinds.values()) if kinds else 1
    dist_html = []
    for kind, count in sorted(kinds.items(), key=lambda kv: -kv[1]):
        pct = count / max_count * 100
        cls = "warn" if kind in ("异常值", "超出业务范围", "非法值", "枚举未知") else ""
        dist_html.append(
            f'<div style="display:grid;grid-template-columns:110px 1fr 60px;gap:12px;align-items:center;padding:5px 0">'
            f'<div style="font-size:13px">{E(kind)}</div>'
            f'<div class="bar-track"><div class="bar-fill {cls}" style="width:{pct:.1f}%"></div></div>'
            f'<div class="mono" style="text-align:right;color:#41536b">{count:,}</div>'
            "</div>"
        )
    dist_block = "".join(dist_html) if dist_html else '<div class="empty">未发现需要处理的问题。</div>'

    # ---------- 前后对照
    sample_html = []
    for row_no, raw_row, clean_row in (sample_rows or [])[:12]:
        diffs = []
        for col in result.header:
            raw_val = (raw_row.get(col, "") or "").strip()
            new_val = clean_row.get(col, "")
            if raw_val != new_val:
                diffs.append((col, raw_val, new_val))
        if not diffs:
            continue
        cells = "".join(
            f'<tr><td class="mono" style="color:#64748b">{E(col)}</td><td>{_cell_change(r, n)}</td></tr>' for col, r, n in diffs
        )
        sample_html.append(
            f'<div class="rule-card" style="margin-bottom:12px">'
            f'<h3>原始第 {row_no} 行 <span class="k mono">· {E(clean_row.get(result.header[0], ""))}</span></h3>'
            f'<table><tbody>{cells}</tbody></table></div>'
        )
    sample_block = "".join(sample_html) or '<div class="empty">样例行无需修改。</div>'

    # ---------- 待复核
    review = [i for i in result.issues if i.kind in ("异常值", "超出业务范围", "枚举未知", "非法值")]
    review_html = []
    for issue in review[:30]:
        review_html.append(
            "<tr>"
            f'<td class="mono">{issue.row}</td>'
            f'<td class="mono">{E(issue.column)}</td>'
            f'<td>{_cell_change(issue.raw, issue.result)}</td>'
            f'<td><span class="pill {"red" if issue.kind in ("非法值", "异常值", "超出业务范围") else "amber"}">{E(issue.kind)}</span></td>'
            f"<td>{E(issue.action)}</td>"
            "</tr>"
        )
    review_block = (
        '<table><thead><tr><th>行号</th><th>字段</th><th>原值 → 处理结果</th><th>类型</th><th>处理说明</th></tr></thead><tbody>'
        + "".join(review_html)
        + "</tbody></table>"
        if review_html
        else '<div class="empty">没有需要人工复核的记录，全部问题已按规则自动处理。</div>'
    )
    more = f'<div class="legend">仅展示前 30 条，完整清单见 <code>issues.csv</code>（共 {len(review)} 条）。</div>' if len(review) > 30 else ""

    # ---------- 剔除行
    rejected_html = []
    for row in result.rejected[:15]:
        rejected_html.append(
            "<tr>"
            f'<td class="mono">{E(row.get(result.header[0], ""))}</td>'
            f'<td>{E(row.get("_剔除原因", ""))}</td>'
            f'<td class="mono" style="color:#94a3b8">{E(json.dumps({k: v for k, v in row.items() if k != "_剔除原因"}, ensure_ascii=False)[:160])}</td>'
            "</tr>"
        )
    rejected_block = (
        '<table><thead><tr><th>主键</th><th>剔除原因</th><th>原始内容（截断）</th></tr></thead><tbody>'
        + "".join(rejected_html)
        + "</tbody></table>"
        if rejected_html
        else '<div class="empty">所有行均满足必填约束，无剔除。</div>'
    )

    # ---------- 规则
    rule_cards = [
        ("字段类型化", "、".join(config.get("date_columns", [])) + " → 日期；"
         + "、".join(config.get("int_columns", [])) + " → 整数；"
         + "、".join(config.get("float_columns", [])) + " → 金额两位小数"),
        ("枚举归一", "；".join(f"{k}（{len(v)} 种写法）" for k, v in config.get("enum_maps", {}).items()) or "无"),
        ("缺失填充", "；".join(f"{k} ← {v}" for k, v in config.get("missing", {}).items()) or "无"),
        ("派生列", "；".join(f"{k} = {v}" for k, v in config.get("derive", {}).items()) or "无"),
        ("去重", "键：" + "、".join(config.get("dedupe", {}).get("keys", [])) + f"；保留策略：{config.get('dedupe', {}).get('keep', '-')}"),
        ("异常检测", f"IQR×{config.get('outliers', {}).get('iqr_factor', 3)} 标记；业务区间："
         + "；".join(f"{k} ∈ [{v[0]}, {v[1]}]" for k, v in config.get("ranges", {}).items())),
        ("必填校验", "、".join(config.get("required", [])) or "无"),
        ("手机号", "去分隔符/国际码，校验 11 位大陆号码"),
    ]
    rule_block = "".join(
        f'<div class="rule-card"><h3>{E(title)}</h3><div class="k">{E(body)}</div></div>' for title, body in rule_cards
    )

    generated = time.strftime("%Y-%m-%d %H:%M:%S")
    source = meta.get("source", "-")
    rule_name = meta.get("rule_name", "默认规则 v1.0")

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>数据清洗报告 · {E(Path(source).name)}</title>
<style>{CSS}</style>
</head>
<body>
<div class="wrap">
  <div class="hero">
    <div class="tag">数据清洗交付报告</div>
    <h1>原始脏表 → 可分析标准数据集</h1>
    <p>源文件：{E(Path(source).name)} ｜ 生成时间：{generated} ｜ 规则：{E(rule_name)} ｜ 全程本地处理，数据未出本机</p>
  </div>

  <div class="kpis">{kpi_html}</div>

  <section>
    <h2><span class="num">1</span>数据体检对比</h2>
    <p class="sub">同一批字段在清洗前后的状态对照。格式变体数从多变少，说明同义写法已被归一。</p>
    <table>
      <thead><tr><th>字段</th><th>原推断类型</th><th>清洗后类型</th><th>缺失率</th><th>格式变体数</th><th>清洗前问题</th><th>清洗后</th></tr></thead>
      <tbody>{''.join(rows_html)}</tbody>
    </table>
    <div class="legend">共 {len(cmp['rows'])} 个字段，问题字段由 <b>{cmp['problem_cols_before']}</b> 个降为 <b>{cmp['problem_cols_after']}</b> 个；
    完全重复行由 <b>{cmp['dup_before']}</b> 行降为 <b>{cmp['dup_after']}</b> 行。</div>
  </section>

  <section>
    <h2><span class="num">2</span>问题分布</h2>
    <p class="sub">按处理类型统计的命中次数，橙色为需要业务方确认的类别。</p>
    {dist_block}
  </section>

  <section>
    <h2><span class="num">3</span>清洗前后对照（抽样）</h2>
    <p class="sub">左侧红色为原始值，右侧绿色为清洗后结果。可直接用于向业务方说明「改了什么、为什么改」。</p>
    {sample_block}
  </section>

  <section>
    <h2><span class="num">4</span>待复核清单</h2>
    <p class="sub">系统按规则给出判断，但下列记录涉及业务口径，建议由业务方逐条确认后再入库。</p>
    {review_block}
    {more}
  </section>

  <section>
    <h2><span class="num">5</span>剔除行</h2>
    <p class="sub">不满足必填约束的行单独出库到 <code>rejected.csv</code>，不混入交付数据集，也不静默删除。</p>
    {rejected_block}
  </section>

  <section>
    <h2><span class="num">6</span>本次执行的规则</h2>
    <p class="sub">规则全部写在 <code>rules.json</code> 中，改规则不需要改代码；本报告即规则生效的证据。</p>
    <div class="rule-grid">{rule_block}</div>
  </section>

  <footer>
    本报告由 data-clean-pipeline 自动生成，单文件可离线查看、可打印为 PDF。<br>
    交付物：clean_dataset.csv（标准数据集）· rejected.csv（剔除行）· issues.csv（逐格留痕）· 本报告
  </footer>
</div>
</body>
</html>
"""
