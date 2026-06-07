from __future__ import annotations

import json
import math
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pandas as pd

# 重要：本脚本不复制三号员工核心线算法，直接复用 employee3_runner.py。
# 这样“历史核心共振线”和“五百日共振触发线”的口径天然与三号员工保持一致。
import employee3_runner as e3

BOOT = "LIMIT_UP_HISTORICAL_CORE_LINE_SCAN_20260607"
ROOT = Path(__file__).resolve().parent
REPORT_DIR = ROOT / "涨停板历史核心线报告"
OUTPUT_MD = REPORT_DIR / "涨停板历史核心线.md"
OUTPUT_CSV = REPORT_DIR / "涨停板历史核心线.csv"
OUTPUT_JSON = REPORT_DIR / "涨停板历史核心线.json"

TARGET = e3.TARGET
TARGET_DASH = e3.TARGET_DASH
MAX_STOCKS = int(os.getenv("LIMITUP_CORE_MAX_STOCKS", "0"))
REQUIRE_TARGET_DATE = os.getenv("LIMITUP_CORE_REQUIRE_TARGET_DATE", "1") != "0"
SHOW_TOP_CANDIDATES = int(os.getenv("LIMITUP_CORE_SHOW_TOP_CANDIDATES", "3"))


def ss(x: Any) -> str:
    return e3.ss(x)


def sf(x: Any, default: float = 0.0) -> float:
    return e3.sf(x, default)


def rd(x: Any, n: int = 3) -> float:
    return e3.rd(x, n)


def pct_change(a: float, b: float) -> float:
    return e3.pct_change(a, b)


def get_last_target_bar(df: pd.DataFrame) -> Tuple[pd.DataFrame, Dict[str, Any]]:
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
    # 三号员工原口径：主板约9.3，创业/科创约19.3，北交所约29.3（由 employee3_runner.get_limit_threshold 控制）。
    threshold = e3.get_limit_threshold(code)
    # ST 如果名称里明确出现，按5%涨停单独兼容；非ST完全跟三号员工阈值走。
    if "ST" in name.upper() or "＊ST" in name.upper() or "*ST" in name.upper():
        threshold = min(threshold, 4.7)
    close_near_high = close >= high * 0.992
    return pct >= threshold and close_near_high


def compact_line_info(info: Dict[str, Any], prefix: str) -> Dict[str, Any]:
    line = sf(info.get("line"))
    top = info.get("top_candidates") or []
    top_lines = []
    for item in top[:max(0, SHOW_TOP_CANDIDATES)]:
        top_lines.append(
            f"{rd(item.get('line'), 3)}(净{rd(item.get('net_score'), 2)}/共振{int(sf(item.get('effective_resonance_count')))}"
            f"/带量{int(sf(item.get('volume_resonance_count')))})"
        )
    return {
        f"{prefix}价位": rd(line, 3) if line > 0 else 0.0,
        f"{prefix}级别": ss(info.get("level")),
        f"{prefix}净分": rd(info.get("net_score"), 3),
        f"{prefix}共振次数": int(sf(info.get("effective_resonance_count"))),
        f"{prefix}带量共振次数": int(sf(info.get("volume_resonance_count"))),
        f"{prefix}最高价触碰": int(sf(info.get("high_touch_count"))),
        f"{prefix}上影线穿越": int(sf(info.get("upper_shadow_hit_count"))),
        f"{prefix}实体顶贴线": int(sf(info.get("body_top_touch_count"))),
        f"{prefix}收盘贴线": int(sf(info.get("close_touch_count"))),
        f"{prefix}切实体次数": int(sf(info.get("entity_cut_count"))),
        f"{prefix}带量切实体次数": int(sf(info.get("volume_entity_cut_count"))),
        f"{prefix}实体接受次数": int(sf(info.get("entity_accept_count"))),
        f"{prefix}带量实体接受次数": int(sf(info.get("volume_entity_accept_count"))),
        f"{prefix}当前状态": ss(info.get("current_state")),
        f"{prefix}来源": ss(info.get("source")),
        f"{prefix}候选数量": int(sf(info.get("effective_candidates_count"))),
        f"{prefix}分组数量": int(sf(info.get("band_candidates_count"))),
        f"{prefix}Top候选": "；".join(top_lines),
    }


def line_state(close: float, line: float) -> Tuple[str, float]:
    if close <= 0 or line <= 0:
        return "无有效线", 0.0
    distance = pct_change(close, line)
    if close >= line * 1.003:
        return "已站上", rd(distance, 2)
    if close >= line * 0.99:
        return "贴线附近", rd(distance, 2)
    return "仍在线下", rd(distance, 2)


def build_row(code: str, name: str, df: pd.DataFrame) -> Dict[str, Any]:
    d, bar = get_last_target_bar(df)
    if not bar:
        return {}
    if not is_limit_up(code, name, bar):
        return {}

    hist_line = e3.choose_historical_core_resonance_line(d)
    line500 = e3.choose_five_hundred_day_resonance_trigger_line(d)

    close = sf(bar.get("close"))
    high = sf(bar.get("high"))
    pct = sf(bar.get("pct_chg"))
    date = ss(bar.get("date"))

    hist_price = sf(hist_line.get("line"))
    line500_price = sf(line500.get("line"))
    hist_state, hist_dist = line_state(close, hist_price)
    line500_state, line500_dist = line_state(close, line500_price)

    row: Dict[str, Any] = {
        "股票代码": code,
        "股票中文名称": name,
        "日期": date,
        "涨跌幅": rd(pct, 2),
        "收盘价": rd(close, 3),
        "最高价": rd(high, 3),
        "涨停阈值": rd(e3.get_limit_threshold(code), 2),
        "历史核心线状态": hist_state,
        "历史核心线距离%": hist_dist,
        "五百日线状态": line500_state,
        "五百日线距离%": line500_dist,
    }
    row.update(compact_line_info(hist_line, "历史核心线"))
    row.update(compact_line_info(line500, "五百日共振线"))
    return row


def sort_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    def key(r: Dict[str, Any]) -> Tuple[float, int, int, float, float]:
        hist_net = sf(r.get("历史核心线净分"))
        hist_hit = int(sf(r.get("历史核心线共振次数")))
        hist_vol = int(sf(r.get("历史核心线带量共振次数")))
        line500_hit = int(sf(r.get("五百日共振线共振次数")))
        pct = sf(r.get("涨跌幅"))
        return (hist_net, hist_hit + line500_hit, hist_vol, pct, -abs(sf(r.get("历史核心线距离%"))))

    return sorted(rows, key=key, reverse=True)


def scan_limit_up_core_lines(hist: Dict[str, pd.DataFrame], names: Dict[str, str]) -> List[Dict[str, Any]]:
    items = list(hist.items())
    if MAX_STOCKS > 0:
        items = items[:MAX_STOCKS]
    rows: List[Dict[str, Any]] = []
    start = time.time()
    total = len(items)
    for i, (code, df) in enumerate(items, 1):
        name = ss(names.get(code))
        if not name and df is not None and not df.empty and "name" in df.columns:
            vals = [ss(x) for x in df["name"].tolist() if ss(x)]
            name = vals[-1] if vals else ""
        try:
            row = build_row(code, name, df)
            if row:
                rows.append(row)
        except Exception as exc:
            print(f"scan failed {code}: {exc}", flush=True)
        if i == 1 or i % 300 == 0 or i == total:
            e3.progress("screen", i, total, start, f"hit={len(rows)} current={code}")
    return sort_rows(rows)


def short(x: Any, n: int = 42) -> str:
    s = ss(x)
    return s if len(s) <= n else s[:n] + "…"


def build_report(rows: List[Dict[str, Any]], stat: Dict[str, Any]) -> str:
    lines: List[str] = []
    lines.append("📌 涨停板历史核心线")
    lines.append("")
    lines.append(f"日期：{TARGET_DASH or TARGET}")
    lines.append("口径：完全复用三号员工 employee3_runner.py 的历史核心共振线 + 五百日共振触发线")
    lines.append(f"聚合窗口：{e3.AGG_WINDOW}日聚合K｜核心线容差：{e3.CORE_LINE_TOL:.2%}｜最少共振：{e3.MIN_CORE_RESONANCE}")
    lines.append(f"缓存文件：{stat.get('cache_files', 0)}｜有效缓存：{stat.get('cache_hit', 0)}｜涨停数量：{len(rows)}")
    lines.append("")

    if not rows:
        lines.append("今日未识别到符合涨停条件的股票，或缓存未覆盖目标日期。")
        return "\n".join(lines)

    lines.extend([
        "| 排名 | 股票代码 | 股票简称 | 涨幅 | 收盘 | 历史核心线 | 状态/距离 | 历史共振 | 历史净分 | 500日线 | 状态/距离 | 500日共振 | 500日净分 |",
        "|---:|---|---|---:|---:|---:|---|---:|---:|---:|---|---:|---:|",
    ])
    for idx, r in enumerate(rows, 1):
        lines.append(
            f"| {idx} | {r.get('股票代码','')} | {r.get('股票中文名称','')} | {r.get('涨跌幅',0)}% | {r.get('收盘价',0)} | "
            f"{r.get('历史核心线价位',0)} | {r.get('历史核心线状态','')}/{r.get('历史核心线距离%',0)}% | "
            f"{r.get('历史核心线共振次数',0)}({r.get('历史核心线带量共振次数',0)}) | {r.get('历史核心线净分',0)} | "
            f"{r.get('五百日共振线价位',0)} | {r.get('五百日线状态','')}/{r.get('五百日线距离%',0)}% | "
            f"{r.get('五百日共振线共振次数',0)}({r.get('五百日共振线带量共振次数',0)}) | {r.get('五百日共振线净分',0)} |"
        )
    lines.append("")

    lines.append("## 明细")
    lines.append("")
    for idx, r in enumerate(rows, 1):
        lines.append(f"{idx}）{r.get('股票代码','')} {r.get('股票中文名称','')}")
        lines.append(f"- 涨停日：{r.get('日期','')}｜涨幅：{r.get('涨跌幅',0)}%｜收盘：{r.get('收盘价',0)}｜最高：{r.get('最高价',0)}")
        lines.append(
            f"- 历史核心线：{r.get('历史核心线价位',0)}｜{r.get('历史核心线状态','')}｜距线{r.get('历史核心线距离%',0)}%｜"
            f"共振{r.get('历史核心线共振次数',0)}次｜带量{r.get('历史核心线带量共振次数',0)}次｜净分{r.get('历史核心线净分',0)}"
        )
        lines.append(
            f"  触碰构成：最高价{r.get('历史核心线最高价触碰',0)}｜上影线{r.get('历史核心线上影线穿越',0)}｜"
            f"实体顶{r.get('历史核心线实体顶贴线',0)}｜收盘{r.get('历史核心线收盘贴线',0)}｜切实体{r.get('历史核心线切实体次数',0)}"
        )
        if ss(r.get("历史核心线Top候选")):
            lines.append(f"  Top候选：{short(r.get('历史核心线Top候选'), 180)}")
        lines.append(
            f"- 五百日共振线：{r.get('五百日共振线价位',0)}｜{r.get('五百日线状态','')}｜距线{r.get('五百日线距离%',0)}%｜"
            f"共振{r.get('五百日共振线共振次数',0)}次｜带量{r.get('五百日共振线带量共振次数',0)}次｜净分{r.get('五百日共振线净分',0)}"
        )
        lines.append(
            f"  触碰构成：最高价{r.get('五百日共振线最高价触碰',0)}｜上影线{r.get('五百日共振线上影线穿越',0)}｜"
            f"实体顶{r.get('五百日共振线实体顶贴线',0)}｜收盘{r.get('五百日共振线收盘贴线',0)}｜切实体{r.get('五百日共振线切实体次数',0)}"
        )
        if ss(r.get("五百日共振线Top候选")):
            lines.append(f"  Top候选：{short(r.get('五百日共振线Top候选'), 180)}")
        lines.append("")
    return "\n".join(lines)


def write_outputs(rows: List[Dict[str, Any]], md: str, stat: Dict[str, Any]) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_MD.write_text(md, encoding="utf-8")
    pd.DataFrame(rows).to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")
    payload = {
        "generated_at_bj": e3.now_bj().strftime("%Y-%m-%d %H:%M:%S"),
        "target": TARGET,
        "target_dash": TARGET_DASH,
        "boot": BOOT,
        "source_coreline_engine": "employee3_runner.py",
        "config": {
            "agg_window": e3.AGG_WINDOW,
            "historical_line_function": "choose_historical_core_resonance_line",
            "five_hundred_day_line_function": "choose_five_hundred_day_resonance_trigger_line",
            "five_hundred_day_lookback": e3.FIVE_HUNDRED_DAY_LOOKBACK,
            "core_line_tol": e3.CORE_LINE_TOL,
            "core_line_band_tol": e3.CORE_LINE_BAND_TOL,
            "min_core_resonance": e3.MIN_CORE_RESONANCE,
            "require_target_date": REQUIRE_TARGET_DATE,
        },
        "stat": stat,
        "rows": rows,
    }
    OUTPUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    print(BOOT, flush=True)
    print(f"file={Path(__file__).resolve()}", flush=True)
    print(f"target={TARGET} target_dash={TARGET_DASH}", flush=True)
    print(f"source_coreline_engine={Path(e3.__file__).resolve()}", flush=True)
    print("cache_dirs=" + " | ".join(str(x) for x in e3.CACHE_DIRS), flush=True)

    hist, names, stat = e3.load_cache()
    if hist and e3.ALLOW_BAOSTOCK_FALLBACK:
        stat["recent_refresh"] = e3.refresh_recent_cache(hist)
    elif not hist:
        print("公共缓存为空：输出空报告。", flush=True)

    rows = scan_limit_up_core_lines(hist, names) if hist else []
    md = build_report(rows, stat)
    write_outputs(rows, md, stat)
    e3.send_report(md[:9000])

    print(f"涨停板历史核心线完成。Report: {OUTPUT_MD}", flush=True)
    print(f"CSV: {OUTPUT_CSV}", flush=True)
    print(f"JSON: {OUTPUT_JSON}", flush=True)


if __name__ == "__main__":
    main()
