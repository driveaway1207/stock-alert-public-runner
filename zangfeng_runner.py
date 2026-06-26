# -*- coding: utf-8 -*-
"""藏锋 Runner：独立可运行入口。

运行：python zangfeng_runner.py
输出：zangfeng_reports/zangfeng_report.md 与 zangfeng_reports/zangfeng_candidates.json
"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pandas as pd

try:
    import requests
except Exception:
    requests = None

from modules.zangfeng_indicator import calculate_zangfeng, self_check as indicator_self_check

ROOT = Path(__file__).resolve().parent
REPORT_DIR = ROOT / "zangfeng_reports"
CACHE_DIRS = [ROOT / "kline_cache", ROOT / "employee5_kline_cache", ROOT / "data" / "kline_cache", ROOT / "cache" / "kline_cache"]
TOP_N = int(os.getenv("ZANGFENG_TOP_N", "20"))
MIN_SCORE = float(os.getenv("ZANGFENG_MIN_SCORE", "60"))
PROGRESS_EVERY = int(os.getenv("ZANGFENG_PROGRESS_EVERY", "500"))
ENABLE_TELEGRAM = os.getenv("ENABLE_TELEGRAM", "0") == "1"
BOT = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN")
CHAT = os.getenv("TELEGRAM_CHAT_ID")


def now_bj() -> datetime:
    return datetime.now(timezone(timedelta(hours=8)))


def ss(x: Any) -> str:
    return "" if x is None else str(x).strip()


def sf(x: Any, default: float = 0.0) -> float:
    try:
        if x is None or pd.isna(x):
            return default
        return float(str(x).replace("%", "").replace(",", ""))
    except Exception:
        return default


def norm_date(x: Any) -> str:
    s = re.sub(r"\D", "", ss(x)[:10])
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if len(s) >= 8 else ""


def code_of(x: Any) -> str:
    s = x.stem if isinstance(x, Path) else ss(x)
    m = re.search(r"(\d{6})", s)
    return m.group(1) if m else ""


def valid_code(code: str) -> bool:
    return code.startswith(("000", "001", "002", "003", "300", "301", "600", "601", "603", "605", "688", "689", "920", "8", "4"))


def normalize_hist(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    mp = {"日期": "date", "交易日期": "date", "date": "date", "time": "date", "代码": "code", "code": "code", "名称": "name", "股票名称": "name", "name": "name", "开盘": "open", "open": "open", "开盘价": "open", "最高": "high", "high": "high", "最高价": "high", "最低": "low", "low": "low", "最低价": "low", "收盘": "close", "close": "close", "收盘价": "close", "成交量": "volume", "volume": "volume", "vol": "volume", "成交额": "amount", "amount": "amount", "涨跌幅": "pct_chg", "pct_chg": "pct_chg", "pctChg": "pct_chg", "涨幅": "pct_chg"}
    d = df.rename(columns={c: mp.get(str(c), mp.get(str(c).lower(), c)) for c in df.columns}).copy()
    if not {"date", "open", "high", "low", "close"}.issubset(d.columns):
        return pd.DataFrame()
    for col in ["open", "high", "low", "close", "volume", "amount", "pct_chg"]:
        if col in d.columns:
            d[col] = d[col].map(sf)
    if "volume" not in d.columns:
        d["volume"] = 0.0
    d["date"] = d["date"].map(norm_date)
    d = d[(d.date != "") & (d.open > 0) & (d.high > 0) & (d.low > 0) & (d.close > 0)]
    d = d.sort_values("date").drop_duplicates("date").reset_index(drop=True)
    if "pct_chg" not in d.columns or d["pct_chg"].abs().sum() == 0:
        prev = d.close.shift(1)
        d["pct_chg"] = (d.close / prev - 1.0) * 100.0
        d.loc[prev <= 0, "pct_chg"] = 0.0
        d["pct_chg"] = d["pct_chg"].fillna(0.0)
    return d


def read_cache_file(path: Path) -> pd.DataFrame:
    try:
        return normalize_hist(pd.read_csv(path))
    except Exception:
        pass
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        rows = obj.get("rows") or obj.get("data") or obj.get("klines") or []
        return normalize_hist(pd.DataFrame(rows))
    except Exception:
        return pd.DataFrame()


def iter_cache_files() -> List[Path]:
    seen: Dict[str, Path] = {}
    for d in CACHE_DIRS:
        if not d.exists():
            continue
        for p in sorted(d.glob("*")):
            if p.suffix.lower() not in {".csv", ".json"}:
                continue
            code = code_of(p)
            if valid_code(code) and code not in seen:
                seen[code] = p
    return list(seen.values())


def estimate_lines(df: pd.DataFrame) -> Tuple[float, float, float, float]:
    close, high, low = df["close"].astype(float), df["high"].astype(float), df["low"].astype(float)
    last = float(close.iloc[-1])
    high20 = float(high.tail(20).max())
    low20 = float(low.tail(20).min())
    high120 = float(high.tail(min(len(high), 120)).max())
    high250 = float(high.tail(min(len(high), 250)).max())
    pressure = high20 if high20 >= last else last * 1.02
    support = low20 if 0 < low20 < last else last * 0.94
    next_pressure = max(high120, high250, pressure * 1.12)
    if next_pressure <= last:
        next_pressure = last * 1.18
    return pressure, support, pressure, next_pressure


def scan() -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    files = iter_cache_files()
    start = time.time()
    rows: List[Dict[str, Any]] = []
    stat = {"cache_files": len(files), "scanned": 0, "bad": 0, "short": 0, "passed": 0}
    print(f"藏锋启动：cache_files={len(files)}", flush=True)
    for i, path in enumerate(files, 1):
        code = code_of(path)
        df = read_cache_file(path)
        if df.empty:
            stat["bad"] += 1
            continue
        if len(df) < 60:
            stat["short"] += 1
            continue
        stat["scanned"] += 1
        pressure, support, trigger, next_pressure = estimate_lines(df)
        result = calculate_zangfeng(df, pressure_price=pressure, support_price=support, trigger_price=trigger, next_pressure_price=next_pressure)
        if result.get("score", 0) >= MIN_SCORE:
            stat["passed"] += 1
            latest = df.iloc[-1]
            rows.append({"code": code, "name": ss(latest.get("name", "")), "date": ss(latest.get("date", "")), "close": round(sf(latest.get("close")), 3), "score": result["score"], "grade": result["grade"], "action_bias": result["action_bias"], "dimensions": result["dimensions"], "flags": result["flags"], "metrics": result["metrics"], "pressure_price": round(pressure, 3), "support_price": round(support, 3), "trigger_price": round(trigger, 3), "next_pressure_price": round(next_pressure, 3)})
        if PROGRESS_EVERY > 0 and i % PROGRESS_EVERY == 0:
            print(f"藏锋进度 {i}/{len(files)} scanned={stat['scanned']} passed={stat['passed']} elapsed={time.time() - start:.1f}s", flush=True)
    rows.sort(key=lambda x: (x["score"], x["metrics"].get("flat_volume_ratio_10d", 0), -x["metrics"].get("return_20d", 0)), reverse=True)
    return rows[:TOP_N], stat


def render_report(candidates: List[Dict[str, Any]], stat: Dict[str, Any]) -> str:
    lines: List[str] = ["# 藏锋｜爆发前夜压缩指标", "", f"- 运行时间：{now_bj().strftime('%Y-%m-%d %H:%M:%S')} 北京时间", f"- 缓存文件：{stat.get('cache_files', 0)}", f"- 有效扫描：{stat.get('scanned', 0)}", f"- 入选数量：{len(candidates)}", f"- 最低分：{MIN_SCORE}", "", "说明：藏锋不是买点，只判断是否进入爆发前夜；真正交易仍要等放量突破、站稳、回踩确认和RR合格。", ""]
    if not candidates:
        lines += ["## 今日无藏锋候选", "", "可能原因：缓存为空、样本不足、压缩质量不足，或最低分阈值过高。"]
        return "\n".join(lines) + "\n"
    lines += ["## Top 候选", "", "| 排名 | 代码 | 名称 | 日期 | 收盘 | 分数 | 等级 | 压力/触发 | 防守 | 标签 |", "|---:|---|---|---|---:|---:|---|---:|---:|---|"]
    for idx, item in enumerate(candidates, 1):
        flags = "、".join(item.get("flags", [])[:4])
        lines.append(f"| {idx} | {item['code']} | {item.get('name','')} | {item.get('date','')} | {item.get('close',0)} | {item['score']} | {item['grade']} | {item.get('trigger_price',0)} | {item.get('support_price',0)} | {flags} |")
    lines += ["", "## 明细"]
    for idx, item in enumerate(candidates, 1):
        d, m = item["dimensions"], item["metrics"]
        lines += ["", f"### {idx}. {item['code']} {item.get('name','')}", f"- 结论：{item['action_bias']}", f"- 分项：锋势{d['锋势']} / 锋气{d['锋气']} / 锋骨{d['锋骨']} / 锋意{d['锋意']} / 出鞘准备{d['出鞘准备']}", f"- 结构线：压力/触发 {item.get('trigger_price')}，防守 {item.get('support_price')}，下一压力 {item.get('next_pressure_price')}", f"- 压缩：ATR收缩 {m.get('atr_contract_ratio')}，振幅收缩 {m.get('range_contract_ratio')}，20日平台宽度 {m.get('platform_width_20d')}", f"- 量能：平量比例 {m.get('flat_volume_ratio_10d')}，量能CV收缩 {m.get('volume_cv_contract_ratio')}，阴/阳量比 {m.get('down_up_vol_ratio')}", f"- 标签：{'、'.join(item.get('flags', []))}"]
    return "\n".join(lines) + "\n"


def send_telegram(text: str) -> None:
    if not ENABLE_TELEGRAM or not BOT or not CHAT or requests is None:
        return
    try:
        requests.post(f"https://api.telegram.org/bot{BOT}/sendMessage", json={"chat_id": CHAT, "text": text[:3900], "disable_web_page_preview": True}, timeout=20)
    except Exception as exc:
        print(f"Telegram发送失败：{exc}", flush=True)


def main() -> int:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    check = indicator_self_check()
    if not check.get("ok"):
        print(json.dumps(check, ensure_ascii=False, indent=2), flush=True)
        raise SystemExit("藏锋指标自检失败")
    candidates, stat = scan()
    report = render_report(candidates, stat)
    (REPORT_DIR / "zangfeng_report.md").write_text(report, encoding="utf-8")
    (REPORT_DIR / "zangfeng_candidates.json").write_text(json.dumps({"stat": stat, "candidates": candidates}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(report, flush=True)
    print("藏锋报告已生成：zangfeng_reports/zangfeng_report.md", flush=True)
    if candidates:
        msg = "藏锋候选 Top{}：\n".format(min(len(candidates), 5))
        for i, item in enumerate(candidates[:5], 1):
            msg += f"{i}. {item['code']} {item.get('name','')} 分数{item['score']} {item['grade']} 标签：{'、'.join(item.get('flags', [])[:3])}\n"
        send_telegram(msg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
