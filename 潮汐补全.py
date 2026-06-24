# -*- coding: utf-8 -*-
"""
潮汐补全.py

职责：
    不改潮汐公式、不改 WINNER 近似、不改信号结果。
    只在 潮汐.py 产出信号 CSV 后，补全股票名称/行业，重写中文报告，并发送 Telegram。

说明：
    优先使用信号 CSV 中已有字段；缺失时用 AkShare 的东方财富基础资料兜底。
    若无申万映射文件，行业列会使用可获得的东方财富行业，避免报告显示“未知/未知”。
"""

from __future__ import annotations

import argparse
import os
import re
from pathlib import Path
from typing import Dict, Tuple

import pandas as pd
import requests

指标名 = "潮汐"


def 规范代码(x) -> str:
    s = str(x or "").strip().upper().replace("_", ".").replace("-", ".")
    m = re.search(r"(\d{6})\.(SH|SZ|BJ)", s)
    if m:
        return f"{m.group(1)}.{m.group(2)}"
    m = re.search(r"(SH|SZ|BJ)\.?([0-9]{6})", s)
    if m:
        return f"{m.group(2)}.{m.group(1)}"
    m = re.search(r"(\d{6})", s)
    if not m:
        return s
    num = m.group(1)
    if num.startswith(("6", "5", "9")):
        return f"{num}.SH"
    if num.startswith(("0", "2", "3")):
        return f"{num}.SZ"
    if num.startswith(("4", "8")):
        return f"{num}.BJ"
    return num


def 读CSV(path: Path) -> pd.DataFrame:
    for enc in ["utf-8-sig", "utf-8", "gbk", "gb18030"]:
        try:
            return pd.read_csv(path, encoding=enc)
        except UnicodeDecodeError:
            continue
    return pd.read_csv(path)


def 找最新信号文件(out_dir: Path, date: str = "") -> Path:
    if date:
        ds = date.replace("-", "")
        cand = sorted(out_dir.glob(f"潮汐_{ds}_信号.csv"))
        if cand:
            return cand[-1]
    cand = sorted(out_dir.glob("潮汐_*_信号.csv"))
    if not cand:
        raise FileNotFoundError(f"{out_dir} 下没有找到 潮汐_*_信号.csv")
    return cand[-1]


def 提取日期(path: Path) -> str:
    m = re.search(r"潮汐_(\d{8})_信号\.csv", path.name)
    if not m:
        return ""
    s = m.group(1)
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}"


def 加载AkShare基础信息(codes) -> Dict[str, Dict[str, str]]:
    info: Dict[str, Dict[str, str]] = {规范代码(c): {"名称": "", "行业": ""} for c in codes}
    try:
        import akshare as ak
    except Exception as e:
        print(f"[{指标名}补全] AkShare 不可用，跳过在线基础信息补全：{e}")
        return info

    # 先用全市场实时表补股票简称。
    try:
        spot = ak.stock_zh_a_spot_em()
        code_col = "代码" if "代码" in spot.columns else None
        name_col = "名称" if "名称" in spot.columns else None
        if code_col and name_col:
            for _, r in spot[[code_col, name_col]].dropna().iterrows():
                code = 规范代码(str(r[code_col]))
                if code in info:
                    info[code]["名称"] = str(r[name_col]).strip()
    except Exception as e:
        print(f"[{指标名}补全] 股票简称批量补全失败：{e}")

    # 再对入选股票逐只拉个股资料，补行业。入选数通常不大，速度可接受。
    for code in list(info.keys()):
        symbol = re.sub(r"\D", "", code)[:6]
        if not symbol:
            continue
        try:
            detail = ak.stock_individual_info_em(symbol=symbol)
            if detail is None or detail.empty:
                continue
            cols = list(detail.columns)
            if "item" in cols and "value" in cols:
                mp = dict(zip(detail["item"].astype(str), detail["value"].astype(str)))
            elif "项目" in cols and "值" in cols:
                mp = dict(zip(detail["项目"].astype(str), detail["值"].astype(str)))
            else:
                mp = {}
            name = mp.get("股票简称") or mp.get("简称") or ""
            industry = mp.get("行业") or mp.get("所属行业") or ""
            if name and not info[code]["名称"]:
                info[code]["名称"] = str(name).strip()
            if industry:
                info[code]["行业"] = str(industry).strip()
        except Exception as e:
            print(f"[{指标名}补全] {code} 个股行业补全失败：{e}")
    return info


def 补全信号(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    df = df.copy()
    if "code" not in df.columns:
        raise ValueError("信号文件缺少 code 字段")
    df["code"] = df["code"].apply(规范代码)
    for c in ["name", "申万一级", "申万二级"]:
        if c not in df.columns:
            df[c] = ""
        df[c] = df[c].fillna("").astype(str)

    need_codes = []
    for _, r in df.iterrows():
        if (not r.get("name", "").strip()) or r.get("申万一级", "") in ["", "未知", "nan", "None"]:
            need_codes.append(r["code"])
    if not need_codes:
        return df

    base = 加载AkShare基础信息(sorted(set(need_codes)))
    for i, r in df.iterrows():
        code = r["code"]
        b = base.get(code, {})
        name = str(r.get("name", "") or "").strip()
        l1 = str(r.get("申万一级", "") or "").strip()
        l2 = str(r.get("申万二级", "") or "").strip()
        if not name and b.get("名称"):
            df.at[i, "name"] = b["名称"]
        # 没有申万映射时，用可得行业兜底，避免板块集中度全部未知。
        if (not l1 or l1 in ["未知", "nan", "None"]) and b.get("行业"):
            df.at[i, "申万一级"] = b["行业"]
        if (not l2 or l2 in ["未知", "nan", "None"]) and b.get("行业"):
            df.at[i, "申万二级"] = b["行业"]
    df["name"] = df["name"].replace("", "未知名称")
    df["申万一级"] = df["申万一级"].replace("", "未知")
    df["申万二级"] = df["申万二级"].replace("", "未知")
    return df


def 集中度(df: pd.DataFrame, col: str) -> pd.DataFrame:
    if df.empty or col not in df.columns:
        return pd.DataFrame(columns=["rank", "board", "count", "ratio"])
    total = len(df)
    x = df[col].fillna("未知").replace("", "未知").value_counts().head(3).reset_index()
    x.columns = ["board", "count"]
    x["rank"] = range(1, len(x) + 1)
    x["ratio"] = x["count"] / total
    return x[["rank", "board", "count", "ratio"]]


def 百分比(x) -> str:
    try:
        return f"{float(x) * 100:.2f}%"
    except Exception:
        return "0.00%"


def 读审计(out_dir: Path, date: str) -> Tuple[int, int, int, int]:
    ds = date.replace("-", "")
    p = out_dir / f"潮汐_{ds}_审计.csv"
    if not p.exists():
        return 0, 0, 0, 0
    try:
        audit = 读CSV(p)
        scanned = int((audit["status"] == "已扫描").sum()) if "status" in audit else 0
        filtered = int((audit["status"] == "过滤").sum()) if "status" in audit else 0
        skipped = int((audit["status"] == "跳过").sum()) if "status" in audit else 0
        errors = int((audit["status"] == "错误").sum()) if "status" in audit else 0
        return scanned, filtered, skipped, errors
    except Exception:
        return 0, 0, 0, 0


def 生成报告(df: pd.DataFrame, date: str, out_dir: Path) -> str:
    scanned, filtered, skipped, errors = 读审计(out_dir, date)
    lines = [f"【{指标名}】", "", f"日期：{date}", f"今日触发：{len(df)}只"]
    if scanned or filtered or skipped or errors:
        lines.append(f"扫描通过：{scanned}只；外层过滤：{filtered}只；未更新跳过：{skipped}只；错误：{errors}只")
    lines.append("")

    lines.append("前三集中板块（一级行业/可用行业）：")
    t1 = 集中度(df, "申万一级")
    if t1.empty:
        lines.append("无")
    else:
        for _, r in t1.iterrows():
            lines.append(f"{int(r['rank'])}. {r['board']}：{int(r['count'])}只，占比{百分比(r['ratio'])}")
    lines.append("")

    lines.append("前三集中板块（二级行业/可用行业）：")
    t2 = 集中度(df, "申万二级")
    if t2.empty:
        lines.append("无")
    else:
        for _, r in t2.iterrows():
            lines.append(f"{int(r['rank'])}. {r['board']}：{int(r['count'])}只，占比{百分比(r['ratio'])}")
    lines.append("")

    lines.append("全部股票：")
    if df.empty:
        lines.append("无触发股票。")
    else:
        for i, r in df.reset_index(drop=True).iterrows():
            name = str(r.get("name", "") or "未知名称")
            l1 = str(r.get("申万一级", "") or "未知")
            l2 = str(r.get("申万二级", "") or "未知")
            close = float(r.get("close", 0) or 0)
            tglxs = float(r.get("TGLXS", 0) or 0)
            zzlkp = float(r.get("ZZLKP", 0) or 0)
            lines.append(f"{i+1}. {r['code']} {name}｜{l1}/{l2}｜收盘{close:.2f}｜TGLXS {tglxs:.2f}｜ZZLKP {zzlkp:.2f}")

    lines += [
        "",
        "说明：",
        "- 潮汐公式主体严格对应原通达信条件，补全脚本不改变入选名单。",
        "- WINNER 为 Python 成本分布近似，不是通达信内置 WINNER 的逐点复制。",
        "- 若缓存没有申万映射，行业会使用可获得的在线行业口径兜底，避免板块集中度显示未知。",
        "- 本报告用于观察市场低位初动扩散和板块集中度，不等同于买入建议。",
    ]
    return "\n".join(lines)


def 发送Telegram(text: str) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print(f"[{指标名}补全] Telegram token/chat_id 不完整，跳过发送。")
        return False
    chunks, cur = [], ""
    for line in text.splitlines():
        cand = cur + ("\n" if cur else "") + line
        if len(cand) > 3600:
            chunks.append(cur)
            cur = line
        else:
            cur = cand
    if cur:
        chunks.append(cur)
    ok = True
    for i, chunk in enumerate(chunks, 1):
        payload = {"chat_id": chat_id, "text": chunk if len(chunks) == 1 else f"{chunk}\n\n（{i}/{len(chunks)}）", "disable_web_page_preview": True}
        try:
            r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", json=payload, timeout=20)
            if r.status_code >= 300:
                ok = False
                print(f"[{指标名}补全] Telegram发送失败：{r.status_code} {r.text[:200]}")
        except Exception as e:
            ok = False
            print(f"[{指标名}补全] Telegram发送异常：{e}")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description="潮汐补全：补股票名/行业、重写报告、发送Telegram")
    ap.add_argument("--out-dir", default="潮汐输出")
    ap.add_argument("--date", default="")
    ap.add_argument("--print-report", action="store_true")
    ap.add_argument("--send-telegram", action="store_true")
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    sig_path = 找最新信号文件(out_dir, args.date)
    date = args.date or 提取日期(sig_path)
    if not date:
        date = "未知日期"
    df = 读CSV(sig_path)
    df = 补全信号(df)
    df.to_csv(sig_path, index=False, encoding="utf-8-sig")
    report = 生成报告(df, date, out_dir)
    ds = date.replace("-", "")
    md_path = out_dir / f"潮汐_{ds}_报告.md"
    md_path.write_text(report, encoding="utf-8")
    print(f"[{指标名}补全] 已补全并重写：{sig_path}")
    print(f"[{指标名}补全] 已重写报告：{md_path}")
    if args.print_report:
        print("\n" + report)
    send_env = str(os.environ.get("CHAOXI_SEND_TELEGRAM") or "").strip().lower()
    if args.send_telegram or send_env in {"1", "true", "yes", "on", "发送", "是"}:
        发送Telegram(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
