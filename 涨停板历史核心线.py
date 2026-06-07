from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pandas as pd

# 只复用三号员工核心线口径，不复制、不改写算法。
import employee3_runner as e3

BOOT = "LIMIT_UP_TWO_CORE_LINES_ONLY_20260607"
ROOT = Path(__file__).resolve().parent
REPORT_DIR = ROOT / "涨停板历史核心线报告"
OUTPUT_MD = REPORT_DIR / "涨停板历史核心线.md"
OUTPUT_CSV = REPORT_DIR / "涨停板历史核心线.csv"
OUTPUT_JSON = REPORT_DIR / "涨停板历史核心线.json"

TARGET = e3.TARGET
TARGET_DASH = e3.TARGET_DASH
MAX_STOCKS = int(os.getenv("LIMITUP_CORE_MAX_STOCKS", "0"))
REQUIRE_TARGET_DATE = os.getenv("LIMITUP_CORE_REQUIRE_TARGET_DATE", "1") != "0"


def ss(x: Any) -> str:
    return e3.ss(x)


def sf(x: Any, default: float = 0.0) -> float:
    return e3.sf(x, default)


def rd(x: Any, n: int = 3) -> float:
    return e3.rd(x, n)


def target_bar(df: pd.DataFrame) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    d = e3.normalize_hist(df)
    if d.empty:
        return d, {}
    if TARGET_DASH:
        d = d[d["date"] <= TARGET_DASH].reset_index(drop=True)
    if d.empty:
        return d, {}
    if REQUIRE_TARGET_DATE and TARGET_DASH:
        hit = d[d["date"].astype(str) == TARGET_DASH]
        if hit.empty:
            return d, {}
        return d, hit.iloc[-1].to_dict()
    return d, d.iloc[-1].to_dict()


def is_limit_up(code: str, name: str, bar: Dict[str, Any]) -> bool:
    close = sf(bar.get("close"))
    high = sf(bar.get("high"))
    pct = sf(bar.get("pct_chg"))
    if close <= 0 or high <= 0:
        return False
    threshold = e3.get_limit_threshold(code)
    if "ST" in name.upper() or "＊ST" in name.upper() or "*ST" in name.upper():
        threshold = min(threshold, 4.7)
    return pct >= threshold and close >= high * 0.992


def line_price(info: Dict[str, Any]) -> float:
    price = sf(info.get("line"))
    return rd(price, 3) if price > 0 else 0.0


def build_row(code: str, name: str, df: pd.DataFrame) -> Dict[str, Any]:
    d, bar = target_bar(df)
    if not bar or not is_limit_up(code, name, bar):
        return {}

    historical = e3.choose_historical_core_resonance_line(d)
    line500 = e3.choose_five_hundred_day_resonance_trigger_line(d)

    return {
        "股票代码": code,
        "股票名称": name,
        "日期": ss(bar.get("date")),
        "收盘价": rd(bar.get("close"), 3),
        "历史核心线": line_price(historical),
        "500日共振线": line_price(line500),
    }


def stock_name(code: str, df: pd.DataFrame, names: Dict[str, str]) -> str:
    name = ss(names.get(code))
    if name:
        return name
    if df is not None and not df.empty and "name" in df.columns:
        vals = [ss(x) for x in df["name"].tolist() if ss(x)]
        if vals:
            return vals[-1]
    return ""


def scan(hist: Dict[str, pd.DataFrame], names: Dict[str, str]) -> List[Dict[str, Any]]:
    items = list(hist.items())
    if MAX_STOCKS > 0:
        items = items[:MAX_STOCKS]

    rows: List[Dict[str, Any]] = []
    start = time.time()
    total = len(items)
    for i, (code, df) in enumerate(items, 1):
        name = stock_name(code, df, names)
        try:
            row = build_row(code, name, df)
            if row:
                rows.append(row)
        except Exception as exc:
            print(f"scan failed {code}: {exc}", flush=True)
        if i == 1 or i % 300 == 0 or i == total:
            e3.progress("screen", i, total, start, f"hit={len(rows)} current={code}")

    return sorted(rows, key=lambda r: (ss(r.get("股票代码"))))


def build_report(rows: List[Dict[str, Any]], stat: Dict[str, Any]) -> str:
    lines: List[str] = []
    lines.append("📌 涨停板历史核心线")
    lines.append("")
    lines.append(f"日期：{TARGET_DASH or TARGET}")
    lines.append(f"涨停数量：{len(rows)}")
    lines.append("口径：三号员工历史核心共振线 + 三号员工500日共振线")
    lines.append("")

    if not rows:
        lines.append("今日未识别到涨停板，或缓存未覆盖目标日期。")
        return "\n".join(lines)

    for r in rows:
        lines.append(f"{r.get('股票代码', '')}  {r.get('股票名称', '')}")
        lines.append(f"收盘价：{r.get('收盘价', 0)}")
        lines.append(f"历史核心线：{r.get('历史核心线', 0)}")
        lines.append(f"500日共振线：{r.get('500日共振线', 0)}")
        lines.append("")
        lines.append("------------------------------------------------")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_outputs(rows: List[Dict[str, Any]], md: str, stat: Dict[str, Any]) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_MD.write_text(md, encoding="utf-8")
    pd.DataFrame(rows, columns=["股票代码", "股票名称", "日期", "收盘价", "历史核心线", "500日共振线"]).to_csv(
        OUTPUT_CSV, index=False, encoding="utf-8-sig"
    )
    payload = {
        "generated_at_bj": e3.now_bj().strftime("%Y-%m-%d %H:%M:%S"),
        "target": TARGET,
        "target_dash": TARGET_DASH,
        "boot": BOOT,
        "source_coreline_engine": "employee3_runner.py",
        "columns": ["股票代码", "股票名称", "日期", "收盘价", "历史核心线", "500日共振线"],
        "stat": stat,
        "rows": rows,
    }
    OUTPUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    print(BOOT, flush=True)
    print(f"file={Path(__file__).resolve()}", flush=True)
    print(f"target={TARGET} target_dash={TARGET_DASH}", flush=True)
    print(f"source_coreline_engine={Path(e3.__file__).resolve()}", flush=True)

    hist, names, stat = e3.load_cache()
    if hist and e3.ALLOW_BAOSTOCK_FALLBACK:
        stat["recent_refresh"] = e3.refresh_recent_cache(hist)

    rows = scan(hist, names) if hist else []
    md = build_report(rows, stat)
    write_outputs(rows, md, stat)
    e3.send_report(md[:9000])

    print(f"完成。Report: {OUTPUT_MD}", flush=True)
    print(f"CSV: {OUTPUT_CSV}", flush=True)
    print(f"JSON: {OUTPUT_JSON}", flush=True)


if __name__ == "__main__":
    main()
