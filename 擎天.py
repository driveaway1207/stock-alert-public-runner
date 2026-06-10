# -*- coding: utf-8 -*-
"""
擎天.py

擎天战法全市场月线扫描器。

核心定义：
1）月线大阳线必须是真阳线，且实体涨幅严格大于 30%。
   实体涨幅 = (收盘价 - 开盘价) / 开盘价 * 100。
2）擎天位 = 大阳线开盘价 + 大阳线实体高度 * 2/3，约等于实体上 1/3 位，也就是 66.7% 位。
3）大阳线之后必须连续超过 3 根月K收盘不破擎天位，即至少 4 根后续月K收盘价 >= 擎天位。
4）只看收盘不破，不用最低价过滤；最低价只作为回踩深度参考。

输出：
- artifacts/qingtian_latest.csv
- artifacts/qingtian_latest.json
- artifacts/qingtian_report.md

运行：
python 擎天.py --scan
python 擎天.py --self-test
"""

from __future__ import annotations

import argparse
import json
import math
import os
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Tuple

import pandas as pd

try:
    import requests
except Exception:  # pragma: no cover
    requests = None


VERSION = "擎天-v1.0.0"
DEFAULT_OUTPUT_DIR = Path("artifacts")
DEFAULT_CACHE_DIR = Path("qingtian_cache")


@dataclass
class QingtianSignal:
    code: str
    name: str
    qingtian_month: str
    qingtian_open: float
    qingtian_close: float
    qingtian_high: float
    qingtian_low: float
    body_pct: float
    qingtian_level: float
    confirm_months: int
    last_month: str
    last_close: float
    distance_to_qingtian_pct: float
    max_drawdown_close_pct: float
    lowest_low_after_pct: float
    status: str


@dataclass
class ScanSummary:
    version: str
    run_time: str
    scanned_count: int
    success_count: int
    failed_count: int
    signal_count: int
    strict_body_pct: float
    required_confirm_months: int


def normalize_code(raw: str) -> str:
    code = str(raw).strip()
    if "." in code:
        code = code.split(".")[-1]
    return code.zfill(6)


def qingtian_level(open_price: float, close_price: float, ratio: float = 2 / 3) -> float:
    return float(open_price + (close_price - open_price) * ratio)


def prepare_monthly_df(df: pd.DataFrame) -> pd.DataFrame:
    rename_map = {
        "日期": "date", "开盘": "open", "收盘": "close", "最高": "high", "最低": "low",
        "成交量": "volume", "成交额": "amount",
        "date": "date", "open": "open", "close": "close", "high": "high", "low": "low",
        "volume": "volume", "amount": "amount",
    }
    df = df.rename(columns={c: rename_map.get(c, c) for c in df.columns}).copy()
    need = ["date", "open", "high", "low", "close"]
    missing = [c for c in need if c not in df.columns]
    if missing:
        raise ValueError(f"月线数据缺少字段: {missing}")
    df = df[need + [c for c in ["volume", "amount"] if c in df.columns]].copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    for col in ["open", "high", "low", "close"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["date", "open", "high", "low", "close"])
    df = df[(df["open"] > 0) & (df["high"] > 0) & (df["low"] > 0) & (df["close"] > 0)]
    df = df.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)
    df["date"] = df["date"].dt.strftime("%Y-%m-%d")
    return df


def find_qingtian_signals(
    monthly_df: pd.DataFrame,
    code: str = "",
    name: str = "",
    min_body_pct: float = 30.0,
    required_confirm_months: int = 4,
    ratio: float = 2 / 3,
) -> List[QingtianSignal]:
    """在单只股票月线数据中寻找擎天结构。"""
    df = prepare_monthly_df(monthly_df)
    signals: List[QingtianSignal] = []
    if len(df) <= required_confirm_months:
        return signals

    for i in range(0, len(df) - required_confirm_months):
        row = df.iloc[i]
        o, c = float(row["open"]), float(row["close"])
        h, l = float(row["high"]), float(row["low"])
        if c <= o:
            continue
        body_pct = (c - o) / o * 100.0
        # 用户定义是“超过30个点”，这里严格使用 > 30，不用 >= 30。
        if body_pct <= min_body_pct:
            continue

        level = qingtian_level(o, c, ratio=ratio)
        future = df.iloc[i + 1 :].copy()
        confirm = 0
        for close_price in future["close"].astype(float).tolist():
            if close_price >= level:
                confirm += 1
            else:
                break
        if confirm < required_confirm_months:
            continue

        accepted = future.iloc[:confirm]
        last = accepted.iloc[-1]
        last_close = float(last["close"])
        min_close_after = float(accepted["close"].min())
        min_low_after = float(accepted["low"].min())
        status = "擎天长期锁筹" if confirm >= 8 else "擎天确认"
        signals.append(
            QingtianSignal(
                code=normalize_code(code) if code else "",
                name=name or "",
                qingtian_month=str(row["date"]),
                qingtian_open=round(o, 4),
                qingtian_close=round(c, 4),
                qingtian_high=round(h, 4),
                qingtian_low=round(l, 4),
                body_pct=round(body_pct, 2),
                qingtian_level=round(level, 4),
                confirm_months=int(confirm),
                last_month=str(last["date"]),
                last_close=round(last_close, 4),
                distance_to_qingtian_pct=round((last_close / level - 1.0) * 100.0, 2),
                max_drawdown_close_pct=round((min_close_after / level - 1.0) * 100.0, 2),
                lowest_low_after_pct=round((min_low_after / level - 1.0) * 100.0, 2),
                status=status,
            )
        )
    return signals


def get_stock_pool() -> pd.DataFrame:
    """获取A股股票池。优先 AkShare，字段统一为 code/name。"""
    try:
        import akshare as ak
    except Exception as exc:
        raise RuntimeError("缺少 akshare，请先 pip install akshare") from exc

    spot = ak.stock_zh_a_spot_em()
    code_col = "代码" if "代码" in spot.columns else "code"
    name_col = "名称" if "名称" in spot.columns else "name"
    pool = spot[[code_col, name_col]].rename(columns={code_col: "code", name_col: "name"}).copy()
    pool["code"] = pool["code"].map(normalize_code)
    pool["name"] = pool["name"].astype(str)
    bad_name = pool["name"].str.contains("ST|退|退市|ETF|基金|债|转债|B股", case=False, regex=True, na=False)
    bad_code = pool["code"].str.startswith(("8", "4"))
    return pool[~bad_name & ~bad_code].drop_duplicates("code").reset_index(drop=True)


def fetch_monthly_with_cache(code: str, cache_dir: Path, sleep_sec: float = 0.15) -> pd.DataFrame:
    cache_dir.mkdir(parents=True, exist_ok=True)
    code = normalize_code(code)
    cache_file = cache_dir / f"{code}_monthly.csv"
    today = datetime.now().strftime("%Y-%m-%d")
    if cache_file.exists():
        try:
            cached = pd.read_csv(cache_file)
            if "cache_date" in cached.columns and str(cached["cache_date"].iloc[-1]) == today:
                return cached.drop(columns=["cache_date"], errors="ignore")
        except Exception:
            pass
    try:
        import akshare as ak
        df = ak.stock_zh_a_hist(symbol=code, period="monthly", start_date="19900101", end_date="20991231", adjust="qfq")
        if df is None or df.empty:
            raise ValueError("AkShare 返回空月线数据")
        df = prepare_monthly_df(df)
    except Exception as exc:
        raise RuntimeError(f"获取月线失败 {code}: {exc}") from exc
    out = df.copy()
    out["cache_date"] = today
    out.to_csv(cache_file, index=False, encoding="utf-8-sig")
    if sleep_sec > 0:
        time.sleep(sleep_sec)
    return df


def rank_signals(signals: List[QingtianSignal]) -> pd.DataFrame:
    if not signals:
        return pd.DataFrame()
    df = pd.DataFrame([asdict(s) for s in signals])
    df["qingtian_month_dt"] = pd.to_datetime(df["qingtian_month"], errors="coerce")
    df["rank_score"] = (
        df["body_pct"].clip(upper=80) * 0.35
        + df["confirm_months"].clip(upper=18) * 2.5
        + df["distance_to_qingtian_pct"].clip(lower=0, upper=60) * 0.25
        - df["lowest_low_after_pct"].clip(upper=0).abs() * 0.20
    )
    return df.sort_values(["qingtian_month_dt", "rank_score"], ascending=[False, False]).drop(columns=["qingtian_month_dt"]).reset_index(drop=True)


def build_markdown_report(df: pd.DataFrame, summary: ScanSummary) -> str:
    lines = [
        "# 擎天扫描报告", "",
        f"- 版本：{summary.version}",
        f"- 运行时间：{summary.run_time}",
        f"- 扫描数量：{summary.scanned_count}",
        f"- 成功数量：{summary.success_count}",
        f"- 失败数量：{summary.failed_count}",
        f"- 命中数量：{summary.signal_count}",
        f"- 严格规则：月线阳实体涨幅 > {summary.strict_body_pct:.1f}%，后续至少 {summary.required_confirm_months} 根月K收盘不破擎天位",
        "",
    ]
    if df.empty:
        lines.append("本次没有命中擎天结构。")
        return "\n".join(lines)
    show_cols = ["code", "name", "qingtian_month", "body_pct", "qingtian_level", "confirm_months", "last_month", "last_close", "distance_to_qingtian_pct", "status"]
    lines += ["## 命中列表", "", df[show_cols].head(80).to_markdown(index=False), "", "## 字段说明", ""]
    lines += [
        "- 擎天月：出现月线大阳实体涨幅超过30%的月份。",
        "- 擎天位：大阳线实体66.7%位，即后续收盘不能跌破的强势承接线。",
        "- 确认月数：擎天月之后连续收盘不破擎天位的月K数量，必须大于3。",
        "- 距擎天位：最后确认月收盘价相对擎天位的距离。",
    ]
    return "\n".join(lines)


def send_telegram(text: str) -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip() or os.getenv("TG_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip() or os.getenv("TG_CHAT_ID", "").strip()
    if not token or not chat_id or requests is None:
        return False
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text[:3900], "parse_mode": "HTML", "disable_web_page_preview": True},
            timeout=20,
        )
        return bool(resp.ok)
    except Exception:
        return False


def run_scan(args: argparse.Namespace) -> Tuple[pd.DataFrame, ScanSummary]:
    output_dir, cache_dir = Path(args.output_dir), Path(args.cache_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)
    pool = get_stock_pool()
    if args.limit and args.limit > 0:
        pool = pool.head(args.limit).copy()

    all_signals: List[QingtianSignal] = []
    failed: List[dict] = []
    success = 0
    for idx, item in pool.iterrows():
        code, name = normalize_code(item["code"]), str(item["name"])
        try:
            monthly = fetch_monthly_with_cache(code, cache_dir=cache_dir, sleep_sec=float(args.sleep))
            all_signals.extend(find_qingtian_signals(monthly, code=code, name=name, min_body_pct=float(args.min_body_pct), required_confirm_months=int(args.confirm_months), ratio=float(args.ratio)))
            success += 1
        except Exception as exc:
            failed.append({"code": code, "name": name, "error": str(exc)})
        if (idx + 1) % 200 == 0:
            print(f"已扫描 {idx + 1}/{len(pool)}，当前命中 {len(all_signals)}，失败 {len(failed)}", flush=True)

    result_df = rank_signals(all_signals)
    summary = ScanSummary(VERSION, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), int(len(pool)), int(success), int(len(failed)), int(len(result_df)), float(args.min_body_pct), int(args.confirm_months))
    result_df.to_csv(output_dir / "qingtian_latest.csv", index=False, encoding="utf-8-sig")
    (output_dir / "qingtian_latest.json").write_text(json.dumps({"summary": asdict(summary), "signals": result_df.to_dict("records")}, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "qingtian_report.md").write_text(build_markdown_report(result_df, summary), encoding="utf-8")
    if failed:
        pd.DataFrame(failed).to_csv(output_dir / "qingtian_failed.csv", index=False, encoding="utf-8-sig")
    if args.telegram:
        top_text = f"擎天扫描完成\n命中：{summary.signal_count}\n成功：{summary.success_count}\n失败：{summary.failed_count}"
        if not result_df.empty:
            rows = [f"{r.code} {r.name} 擎天位{r.qingtian_level} 确认{r.confirm_months}月" for r in result_df.head(10).itertuples()]
            top_text += "\n" + "\n".join(rows)
        send_telegram(top_text)
    return result_df, summary


def self_test() -> None:
    df_equal_30 = pd.DataFrame([
        {"date": "2024-01-31", "open": 10, "high": 13, "low": 9.8, "close": 13},
        {"date": "2024-02-29", "open": 13, "high": 13.5, "low": 12, "close": 12.1},
        {"date": "2024-03-31", "open": 12.1, "high": 13, "low": 12, "close": 12.2},
        {"date": "2024-04-30", "open": 12.2, "high": 13, "low": 12, "close": 12.3},
        {"date": "2024-05-31", "open": 12.3, "high": 13, "low": 12, "close": 12.4},
    ])
    assert len(find_qingtian_signals(df_equal_30, min_body_pct=30, required_confirm_months=4)) == 0

    df_hit = pd.DataFrame([
        {"date": "2024-01-31", "open": 10, "high": 14, "low": 9.8, "close": 13.2},
        {"date": "2024-02-29", "open": 13.2, "high": 13.5, "low": 11.0, "close": 12.2},
        {"date": "2024-03-31", "open": 12.2, "high": 13, "low": 11.1, "close": 12.2},
        {"date": "2024-04-30", "open": 12.0, "high": 13, "low": 11.2, "close": 12.2},
        {"date": "2024-05-31", "open": 12.1, "high": 13, "low": 11.1, "close": 12.3},
    ])
    sig = find_qingtian_signals(df_hit, code="000001", name="测试", min_body_pct=30, required_confirm_months=4)
    assert len(sig) == 1
    assert sig[0].confirm_months == 4
    assert math.isclose(sig[0].qingtian_level, round(10 + (13.2 - 10) * 2 / 3, 4), rel_tol=0, abs_tol=1e-4)

    assert len(find_qingtian_signals(df_hit.iloc[:4].copy(), min_body_pct=30, required_confirm_months=4)) == 0
    df_break = df_hit.copy(); df_break.loc[3, "close"] = 11.0
    assert len(find_qingtian_signals(df_break, min_body_pct=30, required_confirm_months=4)) == 0
    df_low_break = df_hit.copy(); df_low_break.loc[2, "low"] = 8.0
    assert len(find_qingtian_signals(df_low_break, min_body_pct=30, required_confirm_months=4)) == 1
    print("擎天自检通过：5/5")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="擎天战法全市场月线扫描器")
    parser.add_argument("--scan", action="store_true", help="运行全市场扫描")
    parser.add_argument("--self-test", action="store_true", help="运行内置自检")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="输出目录")
    parser.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR), help="缓存目录")
    parser.add_argument("--min-body-pct", type=float, default=30.0, help="月线实体涨幅阈值，严格大于该值")
    parser.add_argument("--confirm-months", type=int, default=4, help="后续至少多少根月K收盘不破；4表示大于3根")
    parser.add_argument("--ratio", type=float, default=2 / 3, help="擎天位比例，默认实体66.7%%位")
    parser.add_argument("--limit", type=int, default=0, help="调试用，仅扫描前N只；0为全市场")
    parser.add_argument("--sleep", type=float, default=0.15, help="单票请求间隔秒数")
    parser.add_argument("--telegram", action="store_true", help="若环境变量存在则推送Telegram")
    return parser


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    if args.scan:
        _, summary = run_scan(args)
        print(json.dumps(asdict(summary), ensure_ascii=False, indent=2))
        return 0
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
