# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional, Tuple

import akshare as ak
import pandas as pd
import requests

REPORT_DIR = Path(__file__).resolve().parent / "employee4_reports"
TARGET_DATE = os.getenv("EMPLOYEE4_TARGET_DATE") or datetime.now().strftime("%Y%m%d")
MAX_STOCKS = int(os.getenv("EMPLOYEE4_MAX_STOCKS", "120"))
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


def sf(x: Any, default: float = 0.0) -> float:
    try:
        if x is None or pd.isna(x):
            return default
        return float(str(x).replace("%", "").replace(",", ""))
    except Exception:
        return default


def first_col(df: pd.DataFrame, cols: Iterable[str]) -> Optional[str]:
    for c in cols:
        if c in df.columns:
            return c
    return None


def board_limit(code: str, name: str) -> Tuple[str, float]:
    code = str(code).zfill(6)
    upper_name = str(name).upper()
    if "ST" in upper_name:
        return "ST", 5.0
    if code.startswith(("688", "689", "300", "301")):
        return "20cm", 20.0
    if code.startswith(("920", "8", "4")):
        return "30cm", 30.0
    return "10cm", 10.0


def is_floor_move(pct: float, limit_pct: float) -> bool:
    if limit_pct <= 5:
        return pct <= -4.75
    if limit_pct <= 10:
        return pct <= -9.65
    if limit_pct <= 20:
        return pct <= -19.2
    return pct <= -28.8


def send_tg(text: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram missing; skip")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    r = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text[:3900], "disable_web_page_preview": True}, timeout=30)
    print("Telegram status:", r.status_code)


def main() -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    df = ak.stock_zh_a_spot_em()
    code_col = first_col(df, ["代码", "股票代码"])
    name_col = first_col(df, ["名称", "股票简称"])
    pct_col = first_col(df, ["涨跌幅", "涨幅"])
    rows = []
    if code_col and name_col and pct_col:
        for _, row in df.iterrows():
            code = str(row[code_col]).zfill(6)
            name = str(row[name_col])
            pct = sf(row[pct_col])
            board, lim = board_limit(code, name)
            if is_floor_move(pct, lim):
                rows.append({"code": code, "name": name, "pct_chg": pct, "board": board, "limit_pct": lim})
    rows = sorted(rows, key=lambda x: x["pct_chg"])[:MAX_STOCKS]
    if rows:
        body = "\n".join([f"{x['name']}({x['code']}) {x['pct_chg']}% {x['board']}" for x in rows[:30]])
        text = f"🧯【四号员工-弱势样本归因】\n日期：{TARGET_DATE}\n样本：{len(rows)}只\n" + body
    else:
        text = f"🧯【四号员工-弱势样本归因】\n日期：{TARGET_DATE}\n未识别到极弱样本。"
    (REPORT_DIR / "risk_research_report.md").write_text(text, encoding="utf-8")
    (REPORT_DIR / "risk_research_report.json").write_text(json.dumps({"target_date": TARGET_DATE, "sample_count": len(rows), "results": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(text)
    send_tg(text)


if __name__ == "__main__":
    main()
