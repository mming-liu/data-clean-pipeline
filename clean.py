"""data-clean-pipeline 命令行入口。

用法：
    python clean.py --demo                                  生成脏数据并完整清洗一遍（推荐先跑这个）
    python clean.py --input raw/orders_dirty.csv            清洗指定文件
    python clean.py --input x.csv --rules rules.json        使用自定义规则
    python clean.py --input x.csv --out-dir 交付目录
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from pipeline import cleaner, profile, report  # noqa: E402
from pipeline.rules import CleanResult  # noqa: E402


def setup_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass


def read_csv(path: Path) -> tuple[list[str], list[dict]]:
    for enc in ("utf-8-sig", "gbk"):
        try:
            with path.open("r", encoding=enc, newline="") as fh:
                reader = csv.DictReader(fh)
                header = list(reader.fieldnames or [])
                return header, [dict(r) for r in reader]
        except UnicodeDecodeError:
            continue
    raise SystemExit(f"无法识别文件编码：{path}")


def write_csv(path: Path, header: list[str], rows: list[dict], extra: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = header + [c for c in (extra or []) if c not in header]
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fields})


def _sample_pairs(raw_rows: list[dict], result: CleanResult, limit: int = 40) -> list[tuple[int, dict, dict]]:
    """把清洗后的行和原始脏行配对，供报告做「前后对照」。

    优先选「修得最多」的行 —— 这类行最能说明问题被处理过。
    """
    index: dict[str, tuple[int, dict]] = {}
    key_col = result.header[0]
    for i, row in enumerate(raw_rows, start=1):
        key = (row.get(key_col, "") or "").strip()
        if key and key not in index:
            index[key] = (i, row)

    scored: list[tuple[int, dict, dict, int]] = []
    for clean_row in result.rows:
        key = (clean_row.get(key_col, "") or "").strip()
        if key not in index:
            continue
        row_no, raw_row = index[key]
        diff_count = sum(
            1
            for col in result.header
            if (raw_row.get(col, "") or "").strip() != clean_row.get(col, "")
        )
        if diff_count:
            scored.append((row_no, raw_row, clean_row, diff_count))

    scored.sort(key=lambda item: -item[3])
    return [(a, b, c) for a, b, c, _ in scored[:limit]]


def run(input_path: Path, out_dir: Path, rules_path: Path | None = None, rule_name: str = "") -> dict:
    header, raw_rows = read_csv(input_path)
    config = cleaner.DEFAULT_CONFIG
    label = rule_name or "默认规则 v1.0 (内置)"
    if rules_path and rules_path.exists():
        loaded = json.loads(rules_path.read_text(encoding="utf-8"))
        custom = loaded.get("config", loaded)
        config = {**cleaner.DEFAULT_CONFIG, **custom}
        label = rule_name or f"{rules_path.name}"

    print(f"[1/4] 读取 {input_path.name}：{len(raw_rows)} 行 × {len(header)} 列")
    before = profile.profile_table(header, raw_rows)
    print(f"      体检完成，发现 {len(before['problem_columns'])} 个字段存在问题")

    print("[2/4] 按规则清洗")
    result = cleaner.clean_table(header, raw_rows, config)
    print(
        f"      修复 {result.stats['total_fixes']} 个单元格，"
        f"去重 {result.stats['deduped_rows']} 行，剔除 {result.stats['rejected_rows']} 行，"
        f"标记异常 {result.stats['outliers_flagged']} 条"
    )

    after_header = header
    after = profile.profile_table(after_header, result.rows)

    print("[3/4] 写出交付文件")
    out_dir.mkdir(parents=True, exist_ok=True)
    write_csv(out_dir / "clean_dataset.csv", header, result.rows)
    write_csv(out_dir / "rejected.csv", header, result.rejected, extra=["_剔除原因"])
    write_csv(
        out_dir / "issues.csv",
        ["行号", "字段", "原值", "问题类型", "处理动作", "处理结果"],
        [
            {
                "行号": i.row,
                "字段": i.column,
                "原值": i.raw,
                "问题类型": i.kind,
                "处理动作": i.action,
                "处理结果": i.result,
            }
            for i in result.issues
        ],
    )
    (out_dir / "profile_before.json").write_text(json.dumps(before, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "profile_after.json").write_text(json.dumps(after, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "stats.json").write_text(
        json.dumps({**result.stats, "rule": label, "source": str(input_path)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("[4/4] 生成 HTML 清洗报告")
    html = report.build_html(
        result,
        before,
        after,
        meta={"source": str(input_path), "rule_name": label},
        config=config,
        sample_rows=_sample_pairs(raw_rows, result),
    )
    report_path = out_dir / "clean_report.html"
    report_path.write_text(html, encoding="utf-8")

    return {
        "source": str(input_path),
        "output_dir": str(out_dir),
        "report": str(report_path),
        **result.stats,
    }


def main(argv: list[str] | None = None) -> int:
    setup_console()
    parser = argparse.ArgumentParser(
        prog="data-clean-pipeline",
        description="本地文件级数据清洗：体检 → 规则清洗 → 逐格留痕 → HTML 报告",
    )
    parser.add_argument("--input", help="待清洗的 CSV 文件")
    parser.add_argument("--rules", help="规则 JSON（不传则用内置默认规则）")
    parser.add_argument("--out-dir", default=str(ROOT / "out"), help="交付目录")
    parser.add_argument("--demo", action="store_true", help="生成脏数据并完整跑一遍")
    args = parser.parse_args(argv)

    if args.demo and not args.input:
        from demo.make_dirty_data import make_dirty

        info = make_dirty(ROOT / "raw" / "orders_dirty.csv")
        print(f"[0/4] 已生成脏数据 {info['rows']} 行（其中重复上报 {info['duplicate_rows']} 行）\n")
        args.input = info["path"]

    if not args.input:
        parser.error("请用 --input 指定文件，或加 --demo 一键演示")

    input_path = Path(args.input)
    if not input_path.exists():
        raise SystemExit(f"文件不存在：{input_path}")

    result = run(input_path, Path(args.out_dir), Path(args.rules) if args.rules else None)
    print("\n完成。交付文件：")
    for key in ("clean_dataset.csv", "rejected.csv", "issues.csv", "clean_report.html", "stats.json"):
        path = Path(args.out_dir) / key
        if path.exists():
            print(f"  - {path}  ({path.stat().st_size / 1024:.1f} KB)")
    print(json.dumps({k: v for k, v in result.items() if k in ("src_rows", "out_rows", "deduped_rows", "total_fixes")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
