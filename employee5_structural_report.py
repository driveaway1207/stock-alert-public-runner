# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pandas as pd
import requests

import employee5_runner as base

REPORT_DIR = Path(__file__).resolve().parent / "employee5_reports"
_KEY = os.getenv("TELEGRAM_BOT_TOKEN", "") or os.getenv("TELEGRAM_TOKEN", "")
_DEST = os.getenv("TELEGRAM_CHAT_ID", "")


def sf(x: Any, default: float = 0.0) -> float:
    return base.sf(x, default)


def rd(x: Any, n: int = 2) -> float:
    return base.rd(x, n)


def pct(a: Any, b: Any) -> float:
    return base.pct(a, b)


def div(a: Any, b: Any) -> float:
    return base.div(a, b)


def send_msg(text: str) -> None:
    if not _KEY or not _DEST:
        print("structural report telegram missing; skip", flush=True)
        return
    lines, buf = [], ""
    for line in text.splitlines():
        if len(buf) + len(line) + 1 > 3500:
            if buf:
                lines.append(buf)
            buf = line
        else:
            buf = line if not buf else buf + "\n" + line
    if buf:
        lines.append(buf)
    url = "https://api." + "tele" + "gram.org/bot" + _KEY + "/sendMessage"
    for i, chunk in enumerate(lines or [text[:3500]], 1):
        r = requests.post(url, json={"chat_id": _DEST, "text": chunk, "disable_web_page_preview": True}, timeout=30)
        print(f"structural report chunk {i} status={r.status_code}", flush=True)
        time.sleep(0.35)


def aggregate_period(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    d = df.copy()
    d["date_dt"] = pd.to_datetime(d["date"], errors="coerce")
    d = d.dropna(subset=["date_dt"])
    if d.empty:
        return pd.DataFrame()
    d = d.set_index("date_dt")
    agg = d.resample(rule).agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum", "amount": "sum"}).dropna().reset_index()
    agg["date"] = agg["date_dt"].dt.strftime("%Y-%m-%d")
    return agg[["date", "open", "high", "low", "close", "volume", "amount"]]


def local_highs(df: pd.DataFrame, window: int = 2) -> List[Tuple[int, float]]:
    vals = [sf(x) for x in df["high"].tolist()]
    out: List[Tuple[int, float]] = []
    for i in range(window, max(window, len(vals) - window)):
        seg = vals[i-window:i+window+1]
        if seg and vals[i] == max(seg) and vals[i] > 0:
            out.append((i, vals[i]))
    return out


def volume_cv(s: pd.Series) -> float:
    return div(s.std(), s.mean()) if len(s) else 0.0


def lin_slope(vals: List[float]) -> float:
    vals = [sf(x) for x in vals]
    if len(vals) < 3:
        return 0.0
    xs = list(range(len(vals)))
    mx, my = sum(xs) / len(xs), sum(vals) / len(vals)
    den = sum((x - mx) ** 2 for x in xs)
    return sum((x - mx) * (y - my) for x, y in zip(xs, vals)) / den if den else 0.0


def ar(no: int, name: str, hit: bool, evidence: str, score: float = 0.0) -> Dict[str, Any]:
    return {"no": no, "name": name, "hit": bool(hit), "score": rd(score), "evidence": evidence}


def dm(no: int, cat: str, name: str, hit: bool, evidence: str, score: float = 0.0) -> Dict[str, Any]:
    return {"no": no, "category": cat, "name": name, "hit": bool(hit), "score": rd(score), "evidence": evidence}


def detect_archetypes(hist: pd.DataFrame) -> List[Dict[str, Any]]:
    daily = base.add_indicators(hist.copy())
    monthly = aggregate_period(daily, "ME")
    work = monthly if len(monthly) >= 24 else daily
    close = sf(daily.iloc[-1].close)
    h250 = base.prev_high(daily, 250)
    major_high = sf(base.prev_window(daily, 750).high.max()) if len(daily) >= 300 else sf(daily.high.max())
    recent_low = sf(daily.iloc[-500:].low.min()) if len(daily) >= 500 else sf(daily.low.min())
    deep_dd = rd((1 - recent_low / major_high) * 100) if major_high else 0
    highs = local_highs(work, 2)
    hs_hit, hs_ev = False, "三峰不足或形态不清晰"
    if len(highs) >= 3 and len(work) >= 30:
        h1, h2, h3 = highs[-3][1], highs[-2][1], highs[-1][1]
        clear = h2 >= h1 * 1.03 and h2 >= h3 * 1.03 and abs(h1 - h3) / max(h1, h3) <= 0.22
        old_top = highs[-1][0] < len(work) - 6
        top_vol = sf(work.iloc[max(0, highs[-3][0]-2):highs[-1][0]+3].volume.mean())
        post_vol = sf(work.iloc[highs[-1][0]+1:].volume.mean())
        vol_sat = top_vol > 0 and (post_vol == 0 or top_vol >= post_vol * 1.15)
        hs_hit = bool(clear and old_top and deep_dd >= 35)
        hs_ev = f"三峰清晰={clear}，顶部后深跌={deep_dd}% ，顶部量能饱和={vol_sat}，峰值={rd(h1)}/{rd(h2)}/{rd(h3)}"
    out = [ar(1, "大级别头肩/三峰供给释放后再启动", hs_hit, hs_ev, 3 if hs_hit else 0)]
    if len(daily) >= 260:
        base_zone = daily.iloc[-180:]
        width = rd((sf(base_zone.high.max()) / sf(base_zone.low.min()) - 1) * 100) if sf(base_zone.low.min()) else 0
        cv = rd(volume_cv(base_zone.volume))
        out.append(ar(2, "长周期下跌后底部磨底吸收", deep_dd >= 35 and width <= 80 and cv <= 1.25, f"前高后深跌={deep_dd}% ，近180日宽度={width}% ，量能CV={cv}", 2.5))
    out.append(ar(3, "左侧年线级前高突破", bool(h250 and close >= h250), f"250日高点={rd(h250)}，当前={rd(close)}，250日位置={base.range_position(daily,250)}", 2.8 if h250 and close >= h250 else 0))
    if len(daily) >= 180:
        v_old, v_mid, v_now = sf(daily.iloc[-180:-90].volume.mean()), sf(daily.iloc[-90:-30].volume.mean()), sf(daily.iloc[-20:].volume.mean())
        out.append(ar(4, "量能枯竭后恢复", v_old > 0 and v_mid < v_old * 0.75 and v_now > v_mid * 1.2, f"量能均值 old/mid/now={rd(v_old,0)}/{rd(v_mid,0)}/{rd(v_now,0)}", 2.3))
    h100 = base.prev_high(daily, 100)
    recent_big = sum(1 for x in daily.iloc[-40:].pct_chg.tolist() if sf(x) >= 6)
    out.append(ar(5, "凹口附近巨震/大阳攻击后蓄势", bool(h100 and abs(pct(close, h100)) <= 8 and recent_big >= 1), f"100日凹口线={rd(h100)}，距当前={rd(pct(close,h100)) if h100 else 0}% ，近40日大阳次数={recent_big}", 2.2))
    h60, l60 = base.prev_high(daily, 60), base.prev_low(daily, 60)
    width60 = rd((h60 / l60 - 1) * 100) if l60 else 0
    out.append(ar(6, "中期箱体吸收后突破", bool(width60 <= 35 and close >= h60 > 0), f"60日箱体宽度={width60}% ，是否突破上沿={bool(close >= h60 > 0)}", 2.4))
    if len(daily) >= 120:
        a, b = daily.iloc[-120:-70], daily.iloc[-65:-15]
        out.append(ar(7, "台阶平台价格/量能中枢抬升", sf(b.close.mean()) > sf(a.close.mean()) * 1.06 and sf(b.volume.mean()) > sf(a.volume.mean()) * 1.05, f"价格中枢抬升={sf(b.close.mean()) > sf(a.close.mean()) * 1.06}，量能中枢抬升={sf(b.volume.mean()) > sf(a.volume.mean()) * 1.05}", 2.0))
    bw = sf(daily.iloc[-1].get("boll_width"))
    bwr = base.pct_rank(daily.iloc[max(0, len(daily)-141):len(daily)-1].boll_width.dropna().tolist(), bw)
    out.append(ar(8, "BOLL/BBI缩口后中轨修复", bw > 0 and bwr <= .3 and close >= sf(daily.iloc[-1].get("boll_mid")), f"BOLL带宽分位={rd(bwr*100,1)}%，中轨={rd(daily.iloc[-1].get('boll_mid'))}", 2.0))
    return out


def build_dimensions(code: str, name: str, board: str, hist: pd.DataFrame) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    df = base.add_indicators(hist.copy())
    close, pc = sf(df.iloc[-1].close), sf(df.iloc[-2].close)
    cm = base.candle_metrics(df)
    returns = {f"{n}d": base.ret_pct(df, n) for n in base.RET_WINDOWS}
    vr20 = base.vol_ratio(df, 20)
    h20, h60, h100, h250 = base.prev_high(df, 20), base.prev_high(df, 60), base.prev_high(df, 100), base.prev_high(df, 250)
    near_core = min([x for x in [h20, h60, h100, h250] if x > 0 and x >= pc * .96] or [max(h20, h60, h100, h250, 0)], key=lambda x: abs(x - pc)) if max(h20, h60, h100, h250, 0) > 0 else 0
    react, vbp, anchor = base.reaction_count(df, near_core), base.vbp_pressure(df), base.find_vol_anchor(df)
    sweep, mvk, archetypes = base.sweep_memory(df, near_core), base.max_volume_k(df), detect_archetypes(hist)
    dims: List[Dict[str, Any]] = []
    no = 1
    def add(cat: str, name_: str, hit: bool, ev: str, score: float = 0.0) -> None:
        nonlocal no
        dims.append(dm(no, cat, name_, hit, ev, score))
        no += 1
    primary = [x for x in archetypes if x.get("hit")]
    add("结构原型", "大级别结构原型识别", bool(primary), "；".join([x["name"] for x in primary[:4]]) if primary else "未识别清晰大级别原型，不能硬套结构", sum(sf(x.get("score")) for x in primary[:3]))
    for x in archetypes:
        add("结构原型", x["name"], x["hit"], x["evidence"], x["score"])
    add("时间路径", "20日/月线窗口涨幅", returns["20d"] >= 30, f"20日/月线窗口涨幅={returns['20d']}%", 1.6)
    add("时间路径", "60日/季线窗口涨幅", returns["60d"] >= 50, f"60日/季线窗口涨幅={returns['60d']}%", 1.5)
    add("核心线", "60日平台上沿突破", close >= h60 > 0, f"60日高点={rd(h60)}，当前={rd(close)}", 1.4)
    add("核心线", "100日凹口/左峰突破", close >= h100 > 0, f"100日高点={rd(h100)}，当前={rd(close)}", 1.5)
    add("核心线", "250日年线级前高突破", close >= h250 > 0, f"250日高点={rd(h250)}，当前={rd(close)}", 2.2)
    add("核心线", "核心线实体/影线共振", sum(react.values()) >= 4, f"核心线={rd(near_core)}，反应={react}", 1.5)
    add("核心线", "VBP筹码压力带突破", vbp.get("available") and vbp.get("break_core"), f"VBP={vbp}", 2.0)
    add("核心线", "历史最大量阳K高点/实底", mvk.get("available") and mvk.get("valid_yang"), f"最大量K={mvk}", 1.5)
    add("核心线", "假突破记忆/Liquidity Sweep", sweep.get("available"), f"扫单记忆={sweep}", 1.5)
    add("量能资金", "健康放量区间", 1.4 <= vr20 <= 4.5, f"20日量比={vr20}", 1.4)
    add("量能资金", "爆量分歧反证", vr20 > 6, f"20日量比={vr20}", -1.0 if vr20 > 6 else 0)
    if len(df) >= 90:
        cv1, cv2 = volume_cv(df.iloc[-90:-45].volume), volume_cv(df.iloc[-45:-5].volume)
        add("量能资金", "平台量能平稳压缩", cv2 < cv1 * .8, f"量能CV {rd(cv1)}→{rd(cv2)}", 1.3)
    add("触发K线", "涨停K实体质量", cm["body_ratio"] >= .55 and cm["close_pos"] >= .8, f"K线={cm}", 1.8)
    add("触发K线", "长上影滞涨反证", cm["upper_ratio"] > .35, f"上影占比={cm['upper_ratio']}", -1.0 if cm["upper_ratio"] > .35 else 0)
    ma5, ma10 = sf(df.iloc[-1].get("ma5")), sf(df.iloc[-1].get("ma10"))
    pma5, pma10 = sf(df.iloc[-2].get("ma5")), sf(df.iloc[-2].get("ma10"))
    add("零件战法", "MA5金叉MA10倍量启动", ma5 > ma10 and pma5 <= pma10 and vr20 >= 1.6 and cm["entity_pct"] >= 3, f"MA5/10={rd(ma5)}/{rd(ma10)}，20日量比={vr20}，实体涨幅={cm['entity_pct']}%", 2.2)
    if anchor.get("available"):
        idx = int(anchor["idx"]); low_anchor = sf(df.iloc[max(0, idx-80):idx+1].low.min()); f100 = sf(anchor["high"])
        add("零件战法", "黄金二倍凹口", close >= f100, f"首次倍量锚点={anchor['date']}，100/150/200={rd(f100)}/{rd(low_anchor+1.5*(f100-low_anchor))}/{rd(low_anchor+2*(f100-low_anchor))}，当前={rd(close)}", 2.2)
    else:
        add("零件战法", "黄金二倍凹口", False, "未识别合格首次倍量锚点", 0)
    slope = lin_slope(df.iloc[-35:-1].low.tolist()) if len(df) >= 80 else 0.0
    add("零件战法", "二阶低点斜率抬升", slope > 0, f"近段低点斜率={rd(slope,4)}", 1.0)
    context_ok = bool(close >= near_core > 0 or close >= h60 > 0 or vbp.get("break_core") or primary)
    confirm_ok = bool(cm["close_pos"] >= .8 and vr20 >= .8 and cm["upper_ratio"] <= .30)
    add("华尔街框架", "Event/Context/Confirmation", context_ok and confirm_ok, f"Event=涨停；Context={context_ok}；Confirmation={confirm_ok}", 2.2)
    add("华尔街框架", "过热失真过滤", not (vr20 > 6 or (ma5 and pct(close, ma5) > 14) or cm["upper_ratio"] > .35), f"量比={vr20}，距MA5={rd(pct(close, ma5)) if ma5 else 0}%，上影={cm['upper_ratio']}", 1.0)
    return dims, archetypes


def summarize_hits(deep: List[Dict[str, Any]], key: str) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for item in deep:
        for x in item.get(key, []):
            if x.get("hit"):
                k = f"D{int(x['no']):02d} {x['category']}｜{x['name']}" if key == "dimensions" else f"AR{int(x['no']):02d} {x['name']}"
                counts[k] = counts.get(k, 0) + 1
    return counts


def main() -> None:
    start = time.time()
    report_path = REPORT_DIR / "limit_up_research_report.json"
    if not report_path.exists():
        raise FileNotFoundError("employee5 base report missing: employee5_reports/limit_up_research_report.json")
    data = json.loads(report_path.read_text(encoding="utf-8"))
    target_date = str(data.get("target_date") or base.latest_trade_date()).replace("-", "")
    deep: List[Dict[str, Any]] = []
    hist_source_count: Dict[str, int] = {}
    for item in data.get("deep_samples", [])[:3]:
        code, name, board = base.ss(item.get("code")), base.ss(item.get("name")), base.ss(item.get("board"))
        hist = base.fetch_hist(code, target_date)
        if hist.empty or len(hist) < 30:
            continue
        source = base.ss(hist.get("hist_source", pd.Series(["unknown"])).iloc[-1]) if "hist_source" in hist.columns else "unknown"
        hist_source_count[source] = hist_source_count.get(source, 0) + 1
        dims, archetypes = build_dimensions(code, name, board, hist)
        one = dict(item); one.update({"hist_source": source, "dimensions": dims, "archetypes": archetypes}); deep.append(one)
    arch_count, dim_count = summarize_hits(deep, "archetypes"), summarize_hits(deep, "dimensions")
    lines = ["🧬【五号员工-结构原型归因增强报告】", f"日期：{target_date}", f"结构增强耗时：{base.fmt_seconds(time.time()-start)}", "口径：先识别大级别结构原型，再统计可转化维度；不把数据源、周期换算、空话当维度。", f"历史K线来源：{json.dumps(hist_source_count, ensure_ascii=False)}", "", "一、结构原型共振统计："]
    lines += [f"- {k}：{v}只" for k, v in sorted(arch_count.items(), key=lambda x: x[1], reverse=True)] if arch_count else ["- 本次3只深度样本未形成明显共同结构原型，需要保留未识别结论，不能硬套。"]
    lines += ["", "二、可转化维度共振统计："]
    lines += [f"- {k}：{v}只" for k, v in sorted(dim_count.items(), key=lambda x: x[1], reverse=True)[:24]] if dim_count else ["- 无明显共振维度。"]
    lines += ["", "三、深度样本结构归因："]
    for i, item in enumerate(deep, 1):
        lines.append(f"\n【样本{i}】{item.get('name')}({item.get('code')})｜20日/月线涨幅{item.get('returns',{}).get('20d')}%｜K线源={item.get('hist_source')}")
        hits = [x for x in item.get("archetypes", []) if x.get("hit")]
        lines.append("主结构：" + ("；".join([f"AR{int(x['no']):02d}{x['name']}" for x in hits[:4]]) if hits else "未识别清晰主结构"))
        for x in item.get("archetypes", []):
            lines.append(f"AR{int(x['no']):02d}. 【{x['name']}】命中={x['hit']}｜{x['evidence']}")
        lines.append("命中维度：")
        for x in item.get("dimensions", []):
            if x.get("hit"):
                lines.append(f"✅ D{int(x['no']):02d}. 【{x['category']}｜{x['name']}】分={x.get('score')}｜{x['evidence']}")
    text = "\n".join(lines)
    out_json = {"target_date": target_date, "hist_source_count": hist_source_count, "archetype_type_count": arch_count, "dimension_type_count": dim_count, "deep_samples": deep}
    (REPORT_DIR / "limit_up_structural_report.md").write_text(text, encoding="utf-8")
    (REPORT_DIR / "limit_up_structural_report.json").write_text(json.dumps(out_json, ensure_ascii=False, indent=2), encoding="utf-8")
    print(text, flush=True)
    send_msg(text)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        err = "❌ 五号员工结构增强报告失败\n" + str(e) + "\n" + traceback.format_exc()
        print(err, flush=True)
        send_msg(err)
        raise
