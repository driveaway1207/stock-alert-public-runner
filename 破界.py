# -*- coding: utf-8 -*-
from __future__ import annotations

"""
破界.py
核心线突破海选器：只做“核心线 + 最近20日高质量突破”。
"""

import json
import math
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd


启动标识 = "破界_核心线突破海选器_V1"

根目录 = Path(__file__).resolve().parent
报告目录 = 根目录 / "破界报告"

缓存目录列表 = [
    根目录 / "kline_cache",
    根目录 / "employee5_kline_cache",
    根目录 / "data" / "kline_cache",
    根目录 / "cache" / "kline_cache",
]

报告文件 = 报告目录 / "核心线突破海选报告.md"
明细文件 = 报告目录 / "核心线突破海选明细.csv"
数据文件 = 报告目录 / "核心线突破海选数据.json"

突破回看天数 = 20
最少K线数 = 80
最少共振点 = 3
核心线容差 = 0.010
核心线带宽 = 0.015
正式输出数量 = 5
正式最低分 = 70.0


def 北京时间() -> str:
    return datetime.now(timezone.utc).astimezone(
        timezone(timedelta(hours=8))
    ).strftime("%Y-%m-%d %H:%M:%S")


def 安全浮点(x: Any, default: float = 0.0) -> float:
    try:
        if pd.isna(x):
            return default
        v = float(x)
        return v if math.isfinite(v) else default
    except Exception:
        return default


def 标准代码(raw: str) -> str:
    text = str(raw).strip()
    m = re.search(r"(sh|sz|bj)[._-]?(\d{6})", text, re.I)
    if m:
        return f"{m.group(1).lower()}.{m.group(2)}"

    m = re.search(r"(\d{6})", text)
    if not m:
        return text

    code = m.group(1)
    if code.startswith(("6", "9")):
        return "sh." + code
    if code.startswith(("4", "8")):
        return "bj." + code
    return "sz." + code


def 显示代码(code: str) -> str:
    return 标准代码(code).split(".")[-1]


def 读取缓存文件(path: Path) -> pd.DataFrame:
    try:
        df = pd.read_csv(path)
    except UnicodeDecodeError:
        df = pd.read_csv(path, encoding="gbk")

    df.columns = [str(c).strip().lower() for c in df.columns]

    rename = {
        "日期": "date",
        "交易日期": "date",
        "trade_date": "date",
        "开盘": "open",
        "最高": "high",
        "最低": "low",
        "收盘": "close",
        "成交量": "volume",
        "成交额": "amount",
    }

    df = df.rename(columns={c: rename.get(c, c) for c in df.columns})

    if not {"date", "open", "high", "low", "close"}.issubset(df.columns):
        return pd.DataFrame()

    for c in ["open", "high", "low", "close", "volume", "amount"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    df["date"] = pd.to_datetime(
        df["date"].astype(str).str.replace("-", ""),
        format="%Y%m%d",
        errors="coerce",
    )

    df = (
        df.dropna(subset=["date", "open", "high", "low", "close"])
        .sort_values("date")
        .drop_duplicates("date", keep="last")
        .reset_index(drop=True)
    )

    if "volume" not in df.columns:
        df["volume"] = 0.0

    if "amount" not in df.columns:
        df["amount"] = df["close"] * df["volume"]

    return df


def 找缓存文件() -> List[Path]:
    files: List[Path] = []

    for d in 缓存目录列表:
        if not d.exists():
            continue

        for p in d.rglob("*.csv"):
            name = p.name.lower()

            if any(x in name for x in ["report", "result", "summary", "明细", "报告"]):
                continue

            if re.search(r"\d{6}|sh[._-]?\d{6}|sz[._-]?\d{6}|bj[._-]?\d{6}", name):
                files.append(p)

    return sorted(set(files))


def 月线聚合(df: pd.DataFrame) -> pd.DataFrame:
    x = df.copy()
    x["年月"] = x["date"].dt.to_period("M")

    rows = []

    for _, g in x.groupby("年月"):
        rows.append(
            {
                "date": g["date"].iloc[-1],
                "open": g["open"].iloc[0],
                "high": g["high"].max(),
                "low": g["low"].min(),
                "close": g["close"].iloc[-1],
                "volume": g["volume"].sum(),
                "amount": g["amount"].sum(),
            }
        )

    return pd.DataFrame(rows)


def 价格触线(row: pd.Series, line: float) -> tuple[int, int, int]:
    body_top = max(安全浮点(row["open"]), 安全浮点(row["close"]))
    body_bot = min(安全浮点(row["open"]), 安全浮点(row["close"]))

    points = [
        row["high"],
        row["low"],
        row["close"],
        body_top,
        body_bot,
    ]

    hit = any(
        abs(安全浮点(p) / line - 1) <= 核心线容差
        for p in points
        if 安全浮点(p) > 0
    )

    cut_body = body_bot * 1.005 < line < body_top * 0.995

    vol_bonus = (
        hit
        and 安全浮点(row.get("volume"), 0)
        >= 安全浮点(row.get("均量"), 0) * 1.5
        > 0
    )

    return int(hit), int(cut_body), int(vol_bonus)


def 候选核心线(monthly: pd.DataFrame, daily: pd.DataFrame) -> List[float]:
    prices: List[float] = []

    for src in [monthly.tail(120), daily.tail(500)]:
        for _, r in src.iterrows():
            body_top = max(安全浮点(r["open"]), 安全浮点(r["close"]))
            body_bot = min(安全浮点(r["open"]), 安全浮点(r["close"]))

            for p in [r["high"], r["close"], body_top, body_bot]:
                p = 安全浮点(p)
                if p > 0:
                    prices.append(round(p, 3))

    groups: List[List[float]] = []

    for p in sorted(set(prices)):
        if groups and abs(p / np.mean(groups[-1]) - 1) <= 核心线带宽:
            groups[-1].append(p)
        else:
            groups.append([p])

    return [round(float(np.median(g)), 3) for g in groups]


def 计算核心线(df: pd.DataFrame) -> Optional[Dict[str, Any]]:
    daily = df.copy()
    monthly = 月线聚合(daily)

    if monthly.empty:
        return None

    monthly["均量"] = monthly["volume"].rolling(12, min_periods=3).mean()
    daily["均量"] = daily["volume"].rolling(60, min_periods=10).mean()

    last_close = 安全浮点(daily["close"].iloc[-1])

    scored: List[Dict[str, Any]] = []

    for line in 候选核心线(monthly, daily):
        if line > last_close * 1.25 or line < last_close * 0.55:
            continue

        月共振 = 月切体 = 月带量 = 0
        日共振 = 日切体 = 日带量 = 0

        for _, r in monthly.tail(120).iterrows():
            h, c, v = 价格触线(r, line)
            月共振 += h
            月切体 += c
            月带量 += v

        for _, r in daily.tail(500).iterrows():
            h, c, v = 价格触线(r, line)
            日共振 += h
            日切体 += c
            日带量 += v

        if 月共振 + 日共振 < 最少共振点:
            continue

        score = (
            月共振 * 1.4
            + 日共振 * 0.45
            + 月带量 * 1.8
            + 日带量 * 0.8
            - 月切体 * 0.9
            - 日切体 * 0.18
        )

        scored.append(
            {
                "核心线": round(line, 3),
                "核心线评分": round(score, 2),
                "月线共振": int(月共振),
                "日线共振": int(日共振),
                "带量共振": int(月带量 + 日带量),
                "切实体次数": int(月切体 + 日切体),
                "距离核心线%": round((last_close / line - 1) * 100, 2),
            }
        )

    if not scored:
        return None

    return max(
        scored,
        key=lambda x: (
            x["核心线评分"],
            x["月线共振"],
            x["带量共振"],
        ),
    )


def 计算突破质量(df: pd.DataFrame, line: float) -> Optional[Dict[str, Any]]:
    recent = df.tail(突破回看天数 + 1).copy().reset_index(drop=True)

    best = None

    for i in range(1, len(recent)):
        prev = recent.iloc[i - 1]
        row = recent.iloc[i]

        prev_close = 安全浮点(prev["close"])
        close = 安全浮点(row["close"])
        open_ = 安全浮点(row["open"])
        high = 安全浮点(row["high"])
        low = 安全浮点(row["low"])

        if prev_close <= 0 or close <= 0 or high <= low:
            continue

        rng = high - low
        pct = (close / prev_close - 1) * 100
        body = abs(close - open_)

        实体占比 = body / rng
        收盘位置 = (close - low) / rng
        上影比例 = (high - max(open_, close)) / rng
        实体涨幅 = body / prev_close

        left = max(0, len(df) - 突破回看天数 - 40)
        vol_ma = 安全浮点(
            df.iloc[left : len(df) - 突破回看天数 + i]["volume"]
            .tail(20)
            .mean(),
            0,
        )

        量比 = 安全浮点(row.get("volume"), 0) / vol_ma if vol_ma > 0 else 1.0

        ok = (
            prev_close <= line * 0.995
            and close >= line * 1.003
            and close > open_
            and pct >= 1.0
            and 实体占比 >= 0.25
            and 收盘位置 >= 0.65
            and 上影比例 <= 0.35
            and 实体涨幅 >= 0.005
        )

        if not ok:
            continue

        quality = (
            50
            + min(18, pct * 1.8)
            + min(12, max(0, 收盘位置 - 0.65) * 40)
            + min(10, max(0, 实体占比 - 0.25) * 18)
            + min(10, max(0, 量比 - 1) * 6)
            - max(0, 上影比例 - 0.18) * 20
        )

        item = {
            "突破日期": row["date"].strftime("%Y-%m-%d"),
            "突破收盘": round(close, 3),
            "突破涨幅%": round(pct, 2),
            "突破量比": round(量比, 2),
            "突破质量分": round(quality, 2),
        }

        if best is None or item["突破质量分"] > best["突破质量分"]:
            best = item

    return best


def 筛选单票(code: str, df: pd.DataFrame) -> Optional[Dict[str, Any]]:
    core = 计算核心线(df)

    if not core:
        return None

    br = 计算突破质量(df, core["核心线"])

    if not br:
        return None

    close = 安全浮点(df["close"].iloc[-1])

    penalty = 0.0
    risk = []

    if close / core["核心线"] - 1 > 0.18:
        penalty += 12
        risk.append("距离核心线过远")

    if len(df) >= 20 and close / 安全浮点(df["close"].iloc[-20], close) - 1 > 0.35:
        penalty += 10
        risk.append("20日涨幅过热")

    if 安全浮点(df["amount"].tail(20).mean(), 0) < 5e7:
        penalty += 8
        risk.append("成交额偏低")

    score = min(
        100,
        max(
            0,
            min(30, core["核心线评分"] * 1.6)
            + min(45, br["突破质量分"] * 0.55)
            + min(10, core["带量共振"] * 2)
            + max(0, 10 - abs(core["距离核心线%"] * 0.35))
            - penalty,
        ),
    )

    grade = (
        "S"
        if score >= 85
        else "A"
        if score >= 75
        else "B"
        if score >= 65
        else "C"
        if score >= 55
        else "D"
    )

    return {
        "代码": 显示代码(code),
        "标准代码": 标准代码(code),
        "最新收盘": round(close, 3),
        "等级": grade,
        "总分": round(score, 2),
        "是否正式": score >= 正式最低分 and grade in ("S", "A", "B"),
        **core,
        **br,
        "风险提示": "；".join(risk or ["未见明显技术风险"]),
    }


def main() -> None:
    print(启动标识, flush=True)

    报告目录.mkdir(parents=True, exist_ok=True)

    files = 找缓存文件()
    rows = []

    for idx, path in enumerate(files, 1):
        code = 标准代码(path.stem if re.search(r"\d{6}", path.stem) else path.name)

        try:
            df = 读取缓存文件(path)

            if len(df) < 最少K线数:
                continue

            row = 筛选单票(code, df)

            if row:
                rows.append(row)

        except Exception as exc:
            print(f"筛选异常 {code}: {exc}", flush=True)

        if idx % 300 == 0:
            print(f"破界海选 {idx}/{len(files)} 命中={len(rows)}", flush=True)

    rows.sort(
        key=lambda x: (
            x["是否正式"],
            x["总分"],
            x["突破质量分"],
        ),
        reverse=True,
    )

    top = rows[:正式输出数量]

    lines = [
        f"破界核心线海选｜{北京时间()}",
        f"缓存文件：{len(files)}｜海选命中：{len(rows)}",
        "",
    ]

    if not top:
        lines.append("今日无核心线突破海选命中。")
    else:
        for i, r in enumerate(top, 1):
            lines += [
                f"{i}. {r['代码']}｜{r['等级']}｜{r['总分']}",
                f"   核心线：{r['核心线']}｜距离：{r['距离核心线%']}%",
                f"   突破：{r['突破日期']}｜收盘：{r['突破收盘']}｜涨幅：{r['突破涨幅%']}%｜量比：{r['突破量比']}",
                f"   共振：月线{r['月线共振']}｜日线{r['日线共振']}｜带量{r['带量共振']}｜切实体{r['切实体次数']}",
                f"   风险：{r['风险提示']}",
                "",
            ]

    md = "\n".join(lines)

    报告文件.write_text(md, encoding="utf-8")

    pd.DataFrame(rows).to_csv(
        明细文件,
        index=False,
        encoding="utf-8-sig",
    )

    数据文件.write_text(
        json.dumps(
            {
                "启动标识": 启动标识,
                "生成时间": 北京时间(),
                "结果": rows,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(md, flush=True)
    print(f"报告文件：{报告文件}", flush=True)
    print(f"明细文件：{明细文件}", flush=True)
    print(f"数据文件：{数据文件}", flush=True)


if __name__ == "__main__":
    main()
