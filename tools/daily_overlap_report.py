# -*- coding: utf-8 -*-
from __future__ import annotations

import html
import json
import math
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pandas as pd

try:
    import requests
except Exception:
    requests = None

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "public_reports"
REPORT_HTML = OUT_DIR / "stock_pool_report.html"
REPORT_JSON = OUT_DIR / "stock_pool_report.json"
BJ = timezone(timedelta(hours=8))

MODULES = [
    {"key": "pojie", "name": "破界", "cmd": [sys.executable, "-u", "破界.py"], "outputs": ["破界报告/*.csv", "鐮寸晫鎶ュ憡/*.csv"], "env": {"ENABLE_TELEGRAM": "0", "POJIE_SEND_TELEGRAM": "0"}},
    {"key": "lingdong", "name": "灵动", "cmd": [sys.executable, "-u", "灵动.py", "--scan", "--limit", os.getenv("LINGDONG_MAX_STOCKS", "0")], "outputs": ["artifacts/lingdong_selected.csv", "artifacts/lingdong_active_pool.csv", "artifacts/lingdong_latest.csv"], "env": {"ENABLE_TELEGRAM": "0", "LINGDONG_SEND_TELEGRAM": "0"}},
    {"key": "qingtian", "name": "擎天", "cmd": [sys.executable, "-u", "擎天.py", "--scan", "--limit", os.getenv("QINGTIAN_MAX_STOCKS", "0")], "outputs": ["artifacts/qingtian_latest.csv"], "env": {"ENABLE_TELEGRAM": "0", "QINGTIAN_SEND_TELEGRAM": "0"}},
    {"key": "employee3", "name": "三号员工", "cmd": [sys.executable, "-u", "employee3_runner.py"], "outputs": ["employee3_reports/core_line_breakout_screen.csv"], "env": {"ENABLE_TELEGRAM": "0", "EMPLOYEE3_SEND_TELEGRAM": "0"}},
]


def now_bj() -> datetime:
    return datetime.now(BJ)


def default_trade_date() -> str:
    manual = str(os.getenv("OVERLAP_TARGET_DATE") or "").strip()
    if manual:
        return manual
    try:
        import akshare as ak
        cal = ak.tool_trade_date_hist_sina()
        col = "trade_date" if "trade_date" in cal.columns else cal.columns[0]
        today = now_bj().date()
        dates = pd.to_datetime(cal[col], errors="coerce").dt.date.dropna()
        valid = [d for d in dates if d <= today]
        if valid:
            return max(valid).isoformat()
    except Exception:
        pass
    d = now_bj().date()
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.isoformat()


def env_with_target(extra: Dict[str, str]) -> Dict[str, str]:
    target = default_trade_date()
    env = os.environ.copy()
    env.update({
        "PYTHONUNBUFFERED": "1",
        "SELECTION_TRADE_DATE": target,
        "TARGET_TRADE_DATE": target,
        "DATA_GATE_TARGET_DATE": target,
        "POJIE_TARGET_DATE": target,
        "LINGDONG_TARGET_DATE": target,
        "QINGTIAN_TARGET_DATE": target,
        "EMPLOYEE3_TARGET_DATE": target,
        "NO_COLOR": "1",
    })
    env.update(extra)
    return env


def run_module(module: Dict[str, Any]) -> Dict[str, Any]:
    start = time.time()
    if os.getenv("OVERLAP_SKIP_MODULE_RUN", "0") == "1":
        return {"ok": True, "skipped": True, "seconds": 0, "tail": "module run skipped"}
    try:
        proc = subprocess.run(
            module["cmd"], cwd=ROOT, env=env_with_target(module.get("env", {})), text=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            timeout=int(os.getenv("OVERLAP_MODULE_TIMEOUT_SECONDS", "14400")), check=False,
        )
        return {"ok": proc.returncode == 0, "returncode": proc.returncode, "seconds": round(time.time() - start, 1), "tail": "\n".join((proc.stdout or "").splitlines()[-80:])}
    except Exception as exc:
        return {"ok": False, "seconds": round(time.time() - start, 1), "tail": repr(exc)}


def normalize_code(value: Any) -> str:
    m = re.search(r"(?<!\d)(\d{6})(?!\d)", str(value or ""))
    return m.group(1) if m else ""


def clean_name(value: Any) -> str:
    s = re.sub(r"\s+", " ", str(value or "").strip())
    if not s or s.lower() == "nan" or normalize_code(s) == s:
        return ""
    return s[:32]


def expand_outputs(module: Dict[str, Any]) -> List[Path]:
    files: List[Path] = []
    for pattern in module["outputs"]:
        files.extend([p for p in ROOT.glob(pattern) if p.is_file()])
    if not files and module["key"] == "pojie":
        blocked = {"artifacts", "employee3_reports", "kline_cache", "employee5_kline_cache", "data", "cache", "tools", "modules", ".github"}
        files = [p for p in ROOT.glob("*/*.csv") if p.parent.name not in blocked and not p.name.startswith("_")]
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)


def read_csv_rows(path: Path) -> List[Dict[str, Any]]:
    df = None
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            df = pd.read_csv(path, dtype=str, encoding=enc).fillna("")
            break
        except Exception:
            pass
    if df is None or df.empty:
        return []
    out = []
    for raw in df.to_dict("records"):
        code = ""
        for col, val in raw.items():
            if any(x in str(col).lower() for x in ["code", "代码", "股票"]):
                code = normalize_code(val)
                if code:
                    break
        if not code:
            for val in raw.values():
                code = normalize_code(val)
                if code:
                    break
        if not code:
            continue
        name = ""
        for col, val in raw.items():
            if any(x in str(col).lower() for x in ["name", "名称", "简称"]):
                name = clean_name(val)
                if name:
                    break
        out.append({"code": code, "name": name, "source_path": str(path.relative_to(ROOT))})
    return out


def collect_module_picks(module: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[str]]:
    used: List[str] = []
    picks: List[Dict[str, Any]] = []
    for path in expand_outputs(module):
        rows = read_csv_rows(path)
        if rows:
            picks.extend(rows)
            used.append(str(path.relative_to(ROOT)))
            break
    uniq: Dict[str, Dict[str, Any]] = {}
    for item in picks:
        uniq.setdefault(item["code"], item)
    return list(uniq.values()), used


def pick_col(df: pd.DataFrame, names: Iterable[str]) -> Optional[str]:
    for c in df.columns:
        low = str(c).lower()
        if any(n.lower() in low for n in names):
            return c
    return None


def read_kline(code: str) -> Optional[pd.DataFrame]:
    for base in ["kline_cache", "employee5_kline_cache", "data/kline_cache", "cache/kline_cache"]:
        path = ROOT / base / f"{code}.csv"
        if path.exists():
            try:
                df = pd.read_csv(path)
                if not df.empty:
                    return df
            except Exception:
                pass
    return None


def normalize_date_value(value: Any) -> str:
    s = str(value or "").strip().replace("/", "-").replace(".", "-")
    m = re.search(r"(\d{4})[-]?(\d{2})[-]?(\d{2})", s)
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else ""


def kline_metrics(code: str) -> Dict[str, Any]:
    df = read_kline(code)
    if df is None or len(df) < 6:
        return {}
    date_c = pick_col(df, ["date", "日期", "trade_date", "交易日期"])
    close_c = pick_col(df, ["close", "收盘"])
    vol_c = pick_col(df, ["volume", "vol", "成交量"])
    amount_c = pick_col(df, ["amount", "成交额"])
    if not close_c:
        return {}
    close = pd.to_numeric(df[close_c], errors="coerce").dropna()
    if close.empty:
        return {}
    latest = float(close.iloc[-1])
    latest_date = normalize_date_value(df[date_c].iloc[-1]) if date_c else ""
    target_date = default_trade_date()
    out: Dict[str, Any] = {"latest_close": round(latest, 2), "latest_date": latest_date, "target_date": target_date, "fresh": bool(latest_date and latest_date >= target_date)}
    for days in (5, 20, 60):
        if len(close) > days:
            base = float(close.iloc[-days - 1])
            out[f"pct_{days}d"] = round((latest / base - 1) * 100, 2) if base else None
    if vol_c:
        vol = pd.to_numeric(df[vol_c], errors="coerce").dropna()
        if len(vol) >= 21:
            base = float(vol.iloc[-21:-1].mean())
            out["volume_ratio"] = round(float(vol.iloc[-1]) / base, 2) if base else None
    if amount_c:
        amt = pd.to_numeric(df[amount_c], errors="coerce").dropna()
        if len(amt) >= 20:
            out["amount20"] = round(float(amt.tail(20).mean()), 2)
    return out


def ak_name_map() -> Dict[str, str]:
    try:
        import akshare as ak
        df = ak.stock_info_a_code_name()
        code_col = pick_col(df, ["code", "代码"])
        name_col = pick_col(df, ["name", "名称"])
        if code_col and name_col:
            return {normalize_code(r[code_col]): clean_name(r[name_col]) for _, r in df.iterrows() if normalize_code(r[code_col])}
    except Exception:
        pass
    return {}


def ak_individual(code: str) -> Dict[str, Any]:
    try:
        import akshare as ak
        df = ak.stock_individual_info_em(symbol=code)
        if df is None or df.empty:
            return {}
        data = {str(r[df.columns[0]]): r[df.columns[1]] for _, r in df.iterrows()}
        return {"industry": str(data.get("行业", "") or ""), "total_mv": data.get("总市值", ""), "float_mv": data.get("流通市值", "")}
    except Exception:
        return {}


def score_item(overlap: int, metrics: Dict[str, Any]) -> Tuple[int, str, List[str]]:
    score = min(60, overlap * 18)
    reasons = [f"{overlap}个模块共振"]
    pct20, pct5, vr, amount20 = metrics.get("pct_20d"), metrics.get("pct_5d"), metrics.get("volume_ratio"), metrics.get("amount20")
    if isinstance(pct20, (int, float)):
        if 8 <= pct20 <= 35:
            score += 14; reasons.append("20日涨幅处于活跃区")
        elif pct20 > 35:
            score += 6; reasons.append("20日涨幅偏热")
        elif pct20 < -8:
            score -= 6; reasons.append("20日趋势偏弱")
    if isinstance(pct5, (int, float)) and pct5 > 4:
        score += 8; reasons.append("5日动量增强")
    if isinstance(vr, (int, float)):
        if vr >= 1.5:
            score += 10; reasons.append("成交量明显放大")
        elif vr < 0.7:
            score -= 4; reasons.append("量能不足")
    if isinstance(amount20, (int, float)) and amount20 >= 100_000_000:
        score += 8; reasons.append("20日成交额具备流动性")
    score = max(0, min(100, int(round(score))))
    hot = "热点强" if score >= 78 else "偏热点" if score >= 62 else "观察" if score >= 45 else "未确认"
    return score, hot, reasons


def build_pool(picks_by_module: Dict[str, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    names = ak_name_map()
    grouped: Dict[str, Dict[str, Any]] = {}
    for module in MODULES:
        for item in picks_by_module.get(module["key"], []):
            row = grouped.setdefault(item["code"], {"code": item["code"], "name": "", "modules": []})
            row["modules"].append(module["name"])
            row["name"] = row["name"] or item.get("name", "")
    rows = []
    for code, row in grouped.items():
        if len(row["modules"]) < int(os.getenv("OVERLAP_MIN_MODULES", "2")):
            continue
        metrics = kline_metrics(code)
        info = ak_individual(code)
        score, hot, reasons = score_item(len(row["modules"]), metrics)
        if metrics and not metrics.get("fresh"):
            reasons.append(f"K线最新日期{metrics.get('latest_date') or '未知'}，未覆盖目标交易日{metrics.get('target_date')}")
        rows.append({
            "code": code, "name": row["name"] or names.get(code, code), "industry": info.get("industry") or "待补充",
            "modules": row["modules"], "module_count": len(row["modules"]), "hot_level": hot, "score": score,
            "metrics": metrics, "market_value": {"total": info.get("total_mv", ""), "float": info.get("float_mv", "")},
            "review": "；".join(reasons),
        })
    return sorted(rows, key=lambda x: (x["module_count"], x["score"]), reverse=True)


def fmt(value: Any) -> str:
    if value is None or value == "" or (isinstance(value, float) and math.isnan(value)):
        return "--"
    return str(value)


def build_html(payload: Dict[str, Any]) -> str:
    cards = []
    max_score = max([r["score"] for r in payload["stocks"]] + [1])
    for r in payload["stocks"]:
        m = r.get("metrics", {})
        tags = "".join(f"<span>{html.escape(x)}</span>" for x in r["modules"])
        width = max(8, int(r["score"] / max_score * 100))
        cards.append(f"""
        <article class="stock-card" data-code="{html.escape(r['code'])}" data-name="{html.escape(r['name'])}" data-industry="{html.escape(r['industry'])}" data-hot="{html.escape(r['hot_level'])}">
          <div class="card-top"><div><b>{html.escape(r['name'])}</b><em>{html.escape(r['code'])}</em></div><strong>{r['score']}</strong></div>
          <div class="bar"><i style="width:{width}%"></i></div><div class="tags">{tags}<span>{html.escape(r['hot_level'])}</span></div>
          <dl><div><dt>行业</dt><dd>{html.escape(r['industry'])}</dd></div><div><dt>数据日</dt><dd>{fmt(m.get('latest_date'))}</dd></div><div><dt>5日</dt><dd>{fmt(m.get('pct_5d'))}%</dd></div><div><dt>20日</dt><dd>{fmt(m.get('pct_20d'))}%</dd></div><div><dt>量比</dt><dd>{fmt(m.get('volume_ratio'))}</dd></div><div><dt>新鲜度</dt><dd>{'最新' if m.get('fresh') else '待确认'}</dd></div></dl>
          <p>{html.escape(r['review'])}</p>
        </article>""")
    stats = "".join(f"<div><b>{html.escape(x['name'])}</b><strong>{x['count']}</strong><span>{'成功' if x['ok'] else '异常'}</span></div>" for x in payload["module_stats"])
    data = json.dumps(payload["stocks"], ensure_ascii=False).replace("</", "<\\/")
    body = "".join(cards) if cards else '<div class="empty">今晚没有形成模块重合的股票。</div>'
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>股票共振池</title><style>
:root{{--bg:#090b10;--text:#f8fafc;--muted:#94a3b8;--line:#263244;--cyan:#22d3ee;--gold:#f5c451;--rose:#fb7185}}*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(circle at 20% 10%,#1f3b57 0,#090b10 35%,#07080c 100%);color:var(--text);font-family:Inter,'Microsoft YaHei',system-ui,sans-serif;letter-spacing:0}}canvas{{position:fixed;inset:0;z-index:-1}}header{{padding:40px clamp(18px,4vw,64px) 20px}}h1{{font-size:clamp(34px,7vw,82px);margin:0;font-weight:900;letter-spacing:0}}header p{{max-width:860px;color:var(--muted);line-height:1.7}}.glow{{background:linear-gradient(90deg,#fff,var(--cyan),var(--gold));-webkit-background-clip:text;color:transparent;text-shadow:0 0 32px rgba(34,211,238,.22)}}.toolbar{{display:grid;grid-template-columns:1fr auto auto;gap:12px;padding:0 clamp(18px,4vw,64px) 24px;position:sticky;top:0;background:linear-gradient(180deg,rgba(9,11,16,.96),rgba(9,11,16,.72));backdrop-filter:blur(18px);z-index:2}}input,select{{height:42px;border:1px solid var(--line);background:rgba(15,23,42,.78);color:var(--text);border-radius:8px;padding:0 12px}}.stats{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;padding:0 clamp(18px,4vw,64px) 28px}}.stats div,.stock-card{{border:1px solid rgba(148,163,184,.18);background:rgba(15,23,42,.62);border-radius:8px}}.stats div{{padding:14px}}.stats b,.stats span{{display:block;color:var(--muted);font-size:12px}}.stats strong{{font-size:30px;color:var(--gold)}}.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(286px,1fr));gap:14px;padding:0 clamp(18px,4vw,64px) 60px}}.stock-card{{padding:16px;box-shadow:0 20px 60px rgba(0,0,0,.28);transition:.22s ease;overflow:hidden;position:relative}}.stock-card:hover{{transform:translateY(-4px) rotateX(1deg);border-color:rgba(34,211,238,.6)}}.card-top{{display:flex;justify-content:space-between;gap:16px}}.card-top b{{display:block;font-size:20px}}.card-top em{{display:block;color:var(--muted);font-style:normal}}.card-top strong{{font-size:38px;color:var(--gold)}}.bar{{height:6px;background:#1e293b;border-radius:99px;overflow:hidden;margin:12px 0}}.bar i{{display:block;height:100%;background:linear-gradient(90deg,var(--cyan),var(--gold),var(--rose))}}.tags{{display:flex;gap:6px;flex-wrap:wrap;margin:12px 0}}.tags span{{border:1px solid rgba(34,211,238,.35);color:#cffafe;background:rgba(8,145,178,.12);border-radius:999px;padding:4px 8px;font-size:12px}}dl{{display:grid;grid-template-columns:1fr 1fr;gap:8px}}dt{{color:var(--muted);font-size:12px}}dd{{margin:3px 0 0;font-weight:700}}p{{color:#cbd5e1;line-height:1.65}}.empty{{padding:50px;color:var(--muted)}}footer{{padding:0 clamp(18px,4vw,64px) 42px;color:var(--muted);font-size:12px}}@media(max-width:760px){{.toolbar{{grid-template-columns:1fr}}.stats{{grid-template-columns:1fr 1fr}}}}
</style></head><body><canvas id="fx"></canvas><header><h1 class="glow">股票共振池</h1><p>目标交易日：{html.escape(payload['target_date'])}。生成时间：{html.escape(payload['generated_at_bj'])}。只保留至少被两个模块共同选中的股票，并标记命中模块、热点强度、趋势量能和基础评测；每张卡片会显示本地K线实际覆盖到哪一天。</p></header><section class="toolbar"><input id="q" placeholder="搜索代码、名称、行业"><select id="hot"><option value="">全部热点</option><option>热点强</option><option>偏热点</option><option>观察</option><option>未确认</option></select><select id="mod"><option value="0">全部共振</option><option value="2">至少2模块</option><option value="3">至少3模块</option><option value="4">至少4模块</option></select></section><section class="stats">{stats}</section><main class="grid" id="grid">{body}</main><footer>模块运行状态和原始输出路径见 stock_pool_report.json。本报告为量化筛选辅助，不构成投资建议。</footer><script>
const DATA={data};const q=document.getElementById('q'),hot=document.getElementById('hot'),mod=document.getElementById('mod');function f(){{const s=q.value.trim().toLowerCase(),h=hot.value,m=+mod.value;document.querySelectorAll('.stock-card').forEach((el,i)=>{{const r=DATA[i];const t=(r.code+r.name+r.industry+r.modules.join('')).toLowerCase();el.style.display=(!s||t.includes(s))&&(!h||r.hot_level===h)&&(!m||r.module_count>=m)?'block':'none';}})}}[q,hot,mod].forEach(x=>x.addEventListener('input',f));const c=document.getElementById('fx'),ctx=c.getContext('2d');let W,H,pts=[];function rs(){{W=c.width=innerWidth;H=c.height=innerHeight;pts=Array.from({{length:90}},()=>[Math.random()*W,Math.random()*H,(Math.random()-.5)*.6,(Math.random()-.5)*.6]);}}rs();addEventListener('resize',rs);function tick(){{ctx.clearRect(0,0,W,H);for(const p of pts){{p[0]=(p[0]+p[2]+W)%W;p[1]=(p[1]+p[3]+H)%H;ctx.fillStyle='rgba(34,211,238,.45)';ctx.fillRect(p[0],p[1],1.8,1.8)}}for(let i=0;i<pts.length;i++)for(let j=i+1;j<pts.length;j++){{const a=pts[i],b=pts[j],d=Math.hypot(a[0]-b[0],a[1]-b[1]);if(d<130){{ctx.strokeStyle=`rgba(245,196,81,${{(130-d)/900}})`;ctx.beginPath();ctx.moveTo(a[0],a[1]);ctx.lineTo(b[0],b[1]);ctx.stroke();}}}}requestAnimationFrame(tick)}}tick();
</script></body></html>"""


def send_telegram(url: str, payload: Dict[str, Any]) -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN")
    chat = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat or requests is None:
        return False
    fresh_count = sum(1 for x in payload["stocks"] if x.get("metrics", {}).get("fresh"))
    lines = ["股票共振池已生成", f"目标交易日：{payload['target_date']}", f"时间：{payload['generated_at_bj']}", f"重合股票：{len(payload['stocks'])} 只，数据覆盖最新：{fresh_count} 只", f"网页：{url}"]
    for i, x in enumerate(payload["stocks"][:8], 1):
        lines.append(f"{i}. {x['name']}({x['code']}) {x['score']}分 {'/'.join(x['modules'])}")
    resp = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", json={"chat_id": chat, "text": "\n".join(lines), "disable_web_page_preview": False}, timeout=30)
    return resp.ok


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    module_results: Dict[str, Any] = {}
    picks_by_module: Dict[str, List[Dict[str, Any]]] = {}
    target = default_trade_date()
    for module in MODULES:
        result = run_module(module)
        picks, used = collect_module_picks(module)
        result["used_outputs"] = used
        result["pick_count"] = len(picks)
        module_results[module["key"]] = result
        picks_by_module[module["key"]] = picks
    stocks = build_pool(picks_by_module)
    payload = {
        "generated_at_bj": now_bj().strftime("%Y-%m-%d %H:%M:%S"),
        "target_date": target,
        "stocks": stocks,
        "module_stats": [{"key": m["key"], "name": m["name"], "ok": bool(module_results[m["key"]].get("ok")), "count": len(picks_by_module.get(m["key"], [])), "outputs": module_results[m["key"]].get("used_outputs", [])} for m in MODULES],
        "module_results": module_results,
    }
    REPORT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    REPORT_HTML.write_text(build_html(payload), encoding="utf-8")
    report_url = os.getenv("REPORT_PUBLIC_URL", "").strip() or "https://driveaway1207.github.io/stock-alert-public-runner/stock_pool_report.html"
    if os.getenv("OVERLAP_SEND_TELEGRAM", "1") == "1":
        payload["telegram_sent"] = send_telegram(report_url, payload)
        REPORT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "target_date": target, "stocks": len(stocks), "html": str(REPORT_HTML), "json": str(REPORT_JSON)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
