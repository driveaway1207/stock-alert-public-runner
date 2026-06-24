# -*- coding: utf-8 -*-
from __future__ import annotations

"""
破界.py
核心线突破海选器｜V17全量20日召回延迟精修工程版

定位：
1）不是一号员工/三号员工的完整替代品；
2）只做“核心压力线/触发线 + 最近20日高质量突破 + 交易可行性硬闸”；
3）借鉴三号员工中与破界直接相关的有效逻辑：
   - 历史核心共振线 + 近500日日线触发线双线评估；
   - 自然月K核心线共振评分，带量共振加权，切实体扣分，实体接受只改变状态；
   - 日线突破必须从线下突破，实体/收盘/上影/量能要合格；
   - 突破后接受、跌回线下、回踩缩量、小阴小阳、重新转强；
   - 真实交易防守位、上方第一压力、空间、RR；
   - 正式池必须通过硬闸，不允许只靠总分堆出来。
"""

import ast
import json
import math
import os
import re
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


启动标识 = "破界_核心线突破海选器_V17_全量20日召回_延迟精修_进度日志_Top3_Telegram"

根目录 = Path(__file__).resolve().parent
报告目录 = 根目录 / "破界报告"

缓存目录列表 = [
    根目录 / "kline_cache",
    根目录 / "employee5_kline_cache",
    根目录 / "data" / "kline_cache",
    根目录 / "cache" / "kline_cache",
    根目录.parent / "kline_cache",
]

报告文件 = 报告目录 / "核心线突破海选报告.md"
明细文件 = 报告目录 / "核心线突破海选明细.csv"
标签明细文件 = 报告目录 / "核心线突破事件回测标签.csv"
数据文件 = 报告目录 / "核心线突破海选数据.json"
自检文件 = 报告目录 / "破界自检.json"

目标日期输入 = (
    os.getenv("POJIE_TARGET_DATE")
    or os.getenv("EMPLOYEE3_TARGET_DATE")
    or os.getenv("SELECTION_TRADE_DATE")
    or os.getenv("TARGET_TRADE_DATE")
    or os.getenv("DATA_GATE_TARGET_DATE")
    or ""
).strip()
要求严格目标日 = os.getenv("POJIE_REQUIRE_EXACT_TARGET_DATE", "0").strip() not in {"0", "false", "False", "no", "NO", ""}

Telegram开关文本 = (os.getenv("POJIE_SEND_TELEGRAM") or os.getenv("ENABLE_TELEGRAM") or "0").strip()
启用Telegram推送 = Telegram开关文本 not in {"0", "false", "False", "no", "NO", "否", "不发送", "⬜ 否｜不发送Telegram", ""}
TelegramToken = (os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN") or "").strip()
TelegramChatID = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()
Telegram推送TopN = int(os.getenv("POJIE_TELEGRAM_TOP_N", os.getenv("POJIE_TOP_LIMIT", "3")))

# ---------- V15 工程稳定参数：参考三号员工链路，保证 GitHub Actions 不再无声长跑 ----------
进度日志间隔 = max(1, int(os.getenv("POJIE_PROGRESS_EVERY", "50")))
慢票告警秒 = float(os.getenv("POJIE_SLOW_STOCK_SECONDS", "8"))
缓存索引日志间隔 = max(100, int(os.getenv("POJIE_CACHE_INDEX_PROGRESS_EVERY", "1000")))
# V17：默认保留最近20日完整突破召回，避免漏掉“早突破、后承接”的好股票。
# 加速只来自候选线延迟精修/进度日志，不再默认缩短突破回看窗口。
# 如需临时极速排错，可显式设置 POJIE_FAST_DAILY_MODE=1 且 POJIE_FAST_SCAN_DAYS=3。
快速日跑模式 = os.getenv("POJIE_FAST_DAILY_MODE", "0").strip() not in {"0", "false", "False", "no", "NO"}
快速扫描回看天数 = max(1, int(os.getenv("POJIE_FAST_SCAN_DAYS", os.getenv("POJIE_BREAKOUT_LOOKBACK_DAYS", "20"))))
延迟核心线精修 = os.getenv("POJIE_DELAY_LINE_REFINEMENT", "1").strip() not in {"0", "false", "False", "no", "NO"}
启动时刻 = time.time()

def 日志(msg: str) -> None:
    elapsed = time.time() - 启动时刻
    print(f"[破界V17][{elapsed:8.1f}s] {msg}", flush=True)

# ---------- 可调参数 ----------
突破回看天数 = int(os.getenv("POJIE_BREAKOUT_LOOKBACK_DAYS", "20"))
最少K线数 = int(os.getenv("POJIE_MIN_ROWS", "80"))
最少共振点 = int(os.getenv("POJIE_MIN_CORE_RESONANCE", "3"))
核心线容差 = float(os.getenv("POJIE_CORE_LINE_TOL", "0.010"))
核心线带宽 = float(os.getenv("POJIE_CORE_LINE_BAND_TOL", "0.015"))
正式输出数量 = int(os.getenv("POJIE_TOP_LIMIT", "3"))
观察输出数量 = int(os.getenv("POJIE_OBSERVE_LIMIT", "20"))
正式最低分 = float(os.getenv("POJIE_FORMAL_MIN_SCORE", "78"))
观察最低分 = float(os.getenv("POJIE_OBSERVE_MIN_SCORE", "62"))

历史月线窗口 = int(os.getenv("POJIE_MONTHLY_LOOKBACK", "120"))
近端日线窗口 = int(os.getenv("POJIE_TRIGGER_LOOKBACK_DAYS", "500"))
上方压力最小距离 = float(os.getenv("POJIE_PRESSURE_MIN_ABOVE_PCT", "0.025"))
上方压力带宽 = float(os.getenv("POJIE_PRESSURE_BAND_TOL", "0.018"))
价格发现最大防守距离 = float(os.getenv("POJIE_PRICE_DISCOVERY_MAX_RISK_PCT", "8.5"))
价格发现最大距线 = float(os.getenv("POJIE_PRICE_DISCOVERY_MAX_DISTANCE_PCT", "12.0"))
最低成交额20日 = float(os.getenv("POJIE_MIN_AMOUNT20", "50000000"))

# 突破硬条件。注意：涨停板量能可能失真，后面单独处理。
突破前收线下 = float(os.getenv("POJIE_BREAK_PREV_BELOW_PCT", "0.005"))
突破收盘站上 = float(os.getenv("POJIE_BREAK_CLOSE_ABOVE_PCT", "0.003"))
突破最小涨幅 = float(os.getenv("POJIE_BREAK_MIN_PCT_CHG", "1.0"))
突破最小实体涨幅 = float(os.getenv("POJIE_BREAK_MIN_BODY_PCT", "0.005"))
突破最小实体占比 = float(os.getenv("POJIE_BREAK_BODY_RATIO_MIN", "0.30"))
突破最小收盘位置 = float(os.getenv("POJIE_BREAK_CLOSE_POS_MIN", "0.68"))
突破最大上影比例 = float(os.getenv("POJIE_BREAK_UPPER_SHADOW_MAX", "0.32"))
突破实体上线硬闸 = float(os.getenv("POJIE_BREAK_ENTITY_ABOVE_LINE_MIN", "0.35"))
突破贴线蓄势容忍 = float(os.getenv("POJIE_BREAK_PREV_NEAR_LINE_TOL", "0.002"))
普通突破最小量比 = float(os.getenv("POJIE_BREAK_MIN_VOLUME_RATIO", "1.20"))
健康突破最小量比 = float(os.getenv("POJIE_BREAK_HEALTHY_VOLUME_RATIO", "1.50"))
突破二次确认最大前收站上 = float(os.getenv("POJIE_BREAK_CONFIRM_PREV_ABOVE_MAX", "0.020"))
一字涨停最小量比 = float(os.getenv("POJIE_ONE_PRICE_LIMIT_MIN_VOLUME_RATIO", "0.50"))
实体顶贴线不算切实体容忍 = float(os.getenv("POJIE_BODY_TOP_EDGE_TOL", "0.005"))
历史核心线候选数量 = int(os.getenv("POJIE_HISTORICAL_LINE_CANDIDATES", "5"))
近端触发线候选数量 = int(os.getenv("POJIE_TRIGGER_LINE_CANDIDATES", "8"))

防守缓冲 = float(os.getenv("POJIE_DEFENSE_BUFFER_PCT", "0.015"))
正式最大防守距离 = float(os.getenv("POJIE_FORMAL_MAX_RISK_PCT", "10.5"))
正式最大距线 = float(os.getenv("POJIE_FORMAL_MAX_DISTANCE_LINE_PCT", "18.0"))
正式最低RR = float(os.getenv("POJIE_FORMAL_MIN_RR", "1.35"))
正式最低空间 = float(os.getenv("POJIE_FORMAL_MIN_SPACE_PCT", "8.0"))
正式承接最低分 = float(os.getenv("POJIE_FORMAL_MIN_ACCEPT_SCORE", "6.0"))
过热20日涨幅 = float(os.getenv("POJIE_HOT_20D_PCT", "25.0"))

# V6 深度硬闸参数：宁可少选，不可把假突破/假承接放进正式池。
深刺穿降级阈值 = float(os.getenv("POJIE_PULLBACK_DEEP_PIERCE_WARN", "0.025"))
深刺穿硬拒阈值 = float(os.getenv("POJIE_PULLBACK_DEEP_PIERCE_BLOCK", "0.050"))
价格发现突破后最大涨幅 = float(os.getenv("POJIE_PRICE_DISCOVERY_MAX_POST_BREAK_GAIN", "18.0"))
近压最小识别距离 = float(os.getenv("POJIE_NEAR_PRESSURE_MIN_ABOVE_PCT", "0.001"))
允许北交所 = os.getenv("POJIE_ALLOW_BJ", "1").strip() not in {"0", "false", "False", "no", "NO"}

# V7 界体系：在用户“最大共振核心线”基础上，加入专业化边界带、波动自适应、多周期证据与状态置信度。
启用自适应界容差 = os.getenv("POJIE_ADAPTIVE_BOUNDARY_TOL", "1").strip() not in {"0", "false", "False", "no", "NO"}
核心线最小容差 = float(os.getenv("POJIE_CORE_LINE_TOL_MIN", "0.006"))
核心线最大容差 = float(os.getenv("POJIE_CORE_LINE_TOL_MAX", "0.022"))
边界带最小容差 = float(os.getenv("POJIE_BOUNDARY_BAND_TOL_MIN", "0.010"))
边界带最大容差 = float(os.getenv("POJIE_BOUNDARY_BAND_TOL_MAX", "0.035"))
正式必须突破边界带上沿 = os.getenv("POJIE_FORMAL_REQUIRE_BAND_UPPER", "1").strip() not in {"0", "false", "False", "no", "NO"}
边界上沿突破容忍 = float(os.getenv("POJIE_BAND_UPPER_BREAK_TOL", "0.001"))
边界上沿接受容忍 = float(os.getenv("POJIE_BAND_UPPER_ACCEPT_TOL", "0.008"))
二次确认供应窗口 = int(os.getenv("POJIE_BREAK_CONFIRM_SUPPLY_LOOKBACK", "10"))
正式使用融合界上沿 = os.getenv("POJIE_FORMAL_USE_MULTI_TF_UPPER", "1").strip() not in {"0", "false", "False", "no", "NO"}
突破后摆动压力参与硬闸 = os.getenv("POJIE_POST_SWING_PRESSURE_HARD_GATE", "1").strip() not in {"0", "false", "False", "no", "NO"}
大周期融合候选数量 = int(os.getenv("POJIE_MULTI_TF_LINE_CANDIDATES", "10"))
硬风险名称关键词 = tuple(x for x in os.getenv("POJIE_HARD_RISK_NAME_KEYWORDS", "ST,*ST,退,退市").split(",") if x)


# V9 七项实盘落地：不是报告概念，必须进入评分/融合/硬闸/复盘标签。
启用带量共振分层 = os.getenv("POJIE_VOLUME_RESONANCE_TIERED", "1").strip() not in {"0", "false", "False", "no", "NO"}
启用VBP筹码带 = os.getenv("POJIE_ENABLE_VBP", "1").strip() not in {"0", "false", "False", "no", "NO"}
VBP分箱数量 = int(os.getenv("POJIE_VBP_BINS", "64"))
VBP最小重叠比例 = float(os.getenv("POJIE_VBP_MIN_OVERLAP", "0.18"))
VBP最大回看日 = int(os.getenv("POJIE_VBP_LOOKBACK_DAYS", "900"))
启用最低边界敏感性测试 = os.getenv("POJIE_ENABLE_BOUNDARY_SENSITIVITY", "0").strip() not in {"0", "false", "False", "no", "NO"}
敏感性偏移列表 = tuple(float(x) for x in os.getenv("POJIE_SENSITIVITY_SHIFTS", "-0.010,-0.005,0,0.005,0.010").split(",") if x.strip())
启用回测标签输出 = os.getenv("POJIE_ENABLE_EVENT_LABELS", "1").strip() not in {"0", "false", "False", "no", "NO"}
界过宽无确认降级阈值 = float(os.getenv("POJIE_WIDE_BOUNDARY_DOWNGRADE_PCT", "5.5"))
日线不得反客为主 = os.getenv("POJIE_DAILY_TRIGGER_CANNOT_DOMINATE", "1").strip() not in {"0", "false", "False", "no", "NO"}

# V10 窄界实盘硬闸：压力带不能无限扩张，否则一次性突破/跳空不现实。
VBP确认线最大抬升 = float(os.getenv("POJIE_VBP_CONFIRM_MAX_LIFT_PCT", "0.030"))
VBP下沿最大扩展 = float(os.getenv("POJIE_VBP_LOW_MAX_EXPAND_PCT", "0.018"))
压力带理想最大宽度 = float(os.getenv("POJIE_BOUNDARY_IDEAL_MAX_WIDTH_PCT", "4.2"))
压力带正式最大宽度 = float(os.getenv("POJIE_FORMAL_MAX_BOUNDARY_WIDTH_PCT", "5.5"))
压力带极宽硬拒宽度 = float(os.getenv("POJIE_BOUNDARY_TOO_WIDE_BLOCK_PCT", "7.0"))
强势悬空接受最大界宽 = float(os.getenv("POJIE_STRONG_FLOAT_ACCEPT_MAX_WIDTH_PCT", "4.2"))
强势悬空接受最少天数 = int(os.getenv("POJIE_STRONG_FLOAT_ACCEPT_MIN_DAYS", "2"))
强势悬空接受最多天数 = int(os.getenv("POJIE_STRONG_FLOAT_ACCEPT_MAX_DAYS", "6"))
真实回踩触碰上浮容忍 = float(os.getenv("POJIE_REAL_PULLBACK_TOUCH_TOL", "0.008"))
强势悬空观察带宽 = float(os.getenv("POJIE_FLOAT_ACCEPT_ZONE_PCT", "0.035"))
极低成交额20日前置跳过 = float(os.getenv("POJIE_ULTRA_LOW_AMOUNT20_SKIP", "20000000"))
突破后单点压力最小空间 = float(os.getenv("POJIE_POST_SINGLE_SWING_MIN_SPACE_PCT", "6.0"))
日线触发大周期共振容忍 = float(os.getenv("POJIE_DAILY_MAJOR_CONFLUENCE_TOL", "0.030"))

# V10.2 交易语义落地：强势悬空不是弱，但必须拆分执行权重、防守确定性与报告口径。
强势悬空B接受最多天数 = int(os.getenv("POJIE_STRONG_FLOAT_B_MAX_DAYS", "8"))
强势悬空B最大界宽 = float(os.getenv("POJIE_STRONG_FLOAT_B_MAX_WIDTH_PCT", "5.5"))
强势悬空A执行权重 = float(os.getenv("POJIE_FLOAT_A_POSITION_WEIGHT", "0.65"))
强势悬空B执行权重 = float(os.getenv("POJIE_FLOAT_B_POSITION_WEIGHT", "0.35"))
真实回踩执行权重 = float(os.getenv("POJIE_CLEAN_PULLBACK_POSITION_WEIGHT", "1.00"))
结构假设执行权重 = float(os.getenv("POJIE_STRUCTURAL_ASSUMPTION_POSITION_WEIGHT", "0.25"))
预突破报告最大距确认线 = float(os.getenv("POJIE_PREBREAK_REPORT_MAX_DISTANCE_PCT", "5.0"))
预突破远离确认线封顶分 = float(os.getenv("POJIE_PREBREAK_FAR_SCORE_CAP", "54.0"))
突破后摆动压力最小共振次数 = int(os.getenv("POJIE_POST_SWING_PRESSURE_MIN_HITS", "2"))
突破后失败高点确认收盘数 = int(os.getenv("POJIE_POST_FAILED_HIGH_CONFIRM_CLOSES", "1"))
价格发现最大20日涨幅 = float(os.getenv("POJIE_PRICE_DISCOVERY_MAX_20D_PCT", "30.0"))
价格发现移动止盈主线 = os.getenv("POJIE_PRICE_DISCOVERY_TRAIL", "MA10/BBI/前一日低点/突破K实体中位")

# V10.3 前八问题全面落地：成交额可靠性、突破后压力重定价、突破日高点、价格发现状态拆分、组合执行约束。
成交额缺失正式硬拒 = os.getenv("POJIE_BLOCK_UNRELIABLE_AMOUNT_FORMAL", "1").strip() not in {"0", "false", "False", "no", "NO"}
成交额估算允许观察 = os.getenv("POJIE_ALLOW_ESTIMATED_AMOUNT_OBSERVE", "1").strip() not in {"0", "false", "False", "no", "NO"}
突破后失败高点最少确认收盘数 = int(os.getenv("POJIE_POST_FAILED_HIGH_MIN_CONFIRM_CLOSES", "2"))
突破后失败高点最小上影比例 = float(os.getenv("POJIE_POST_FAILED_HIGH_MIN_UPPER_SHADOW", "0.22"))
突破后失败高点弱收盘阈值 = float(os.getenv("POJIE_POST_FAILED_HIGH_WEAK_CLOSE_POS", "0.68"))
突破日上影压力最小上影比例 = float(os.getenv("POJIE_BREAKOUT_DAY_PRESSURE_UPPER_SHADOW", "0.24"))
突破日压力最小空间 = float(os.getenv("POJIE_BREAKOUT_DAY_PRESSURE_MIN_SPACE_PCT", "3.0"))
强势悬空正式池最多数量 = int(os.getenv("POJIE_FLOATING_FORMAL_MAX_COUNT", "2"))

# V11 分层破界：主核心线先触发，融合上沿负责升级；VBP只做筹码参考，不再抬高硬确认线。
正式允许主核心线回踩确认 = os.getenv("POJIE_FORMAL_ALLOW_CORE_PULLBACK", "1").strip() not in {"0", "false", "False", "no", "NO"}
VBP参与硬确认 = os.getenv("POJIE_VBP_HARD_CONFIRM", "0").strip() not in {"0", "false", "False", "no", "NO"}
强势悬空进入正式池 = os.getenv("POJIE_FLOATING_CAN_BE_FORMAL", "0").strip() not in {"0", "false", "False", "no", "NO"}
实盘明细剥离未来标签 = os.getenv("POJIE_STRIP_LABELS_FROM_LIVE_OUTPUT", "1").strip() not in {"0", "false", "False", "no", "NO"}
实体接受状态加分上限 = float(os.getenv("POJIE_ENTITY_ACCEPT_STATE_BONUS_CAP", "4.0"))
正式必须外部雷区已接入 = os.getenv("POJIE_FORMAL_REQUIRE_EXTERNAL_RISK", "1").strip() not in {"0", "false", "False", "no", "NO"}

# V13：稳健核心线与多路径输出，解决确认线污染、异常上沿、跨周期原始共振不可比、外部风险误杀。
融合上沿稳健分位 = float(os.getenv("POJIE_FUSION_UPPER_QUANTILE", "0.80"))
异常上沿最大偏离 = float(os.getenv("POJIE_FUSION_UPPER_OUTLIER_MAX_DEV", "0.035"))
核心线切实体硬降级比例 = float(os.getenv("POJIE_CORE_LINE_CUT_HARD_DOWNGRADE_RATIO", "0.70"))
带量切实体硬降级阈值 = int(os.getenv("POJIE_VOLUME_ENTITY_CUT_DOWNGRADE_COUNT", "2"))
每票保留备用路径数 = int(os.getenv("POJIE_ALT_PATHS_PER_STOCK", "2"))
预突破观察最低质量分 = float(os.getenv("POJIE_PREBREAK_OBSERVE_MIN_QUALITY", "20"))


# ---------- 通用工具 ----------
def 北京时间() -> str:
    return datetime.now(timezone.utc).astimezone(
        timezone(timedelta(hours=8))
    ).strftime("%Y-%m-%d %H:%M:%S")


def 解析日期文本(x: Any) -> Optional[pd.Timestamp]:
    s = str(x or "").strip()
    if not s:
        return None
    s = s.replace("年", "-").replace("月", "-").replace("日", "")
    s = s.replace("/", "-").replace(".", "-").replace("_", "-")
    if re.fullmatch(r"\d{8}", s):
        return pd.to_datetime(s, format="%Y%m%d", errors="coerce")
    if re.fullmatch(r"\d{4}-\d{1,2}-\d{1,2}", s):
        return pd.to_datetime(s, errors="coerce")
    return pd.to_datetime(s[:10], errors="coerce")


def 目标交易日对象() -> Optional[pd.Timestamp]:
    t = 解析日期文本(目标日期输入)
    if t is None or pd.isna(t):
        return None
    return pd.Timestamp(t).normalize()


def Telegram开关有效() -> bool:
    return bool(启用Telegram推送 and TelegramToken and TelegramChatID)


def 安全浮点(x: Any, default: float = 0.0) -> float:
    try:
        if x is None or pd.isna(x):
            return default
        v = float(str(x).replace("%", "").replace(",", ""))
        return v if math.isfinite(v) else default
    except Exception:
        return default


def 四舍五入(x: Any, n: int = 3) -> float:
    v = 安全浮点(x)
    return 0.0 if math.isnan(v) or math.isinf(v) else round(v, n)


def 夹紧(x: Any, lo: float = 0.0, hi: float = 100.0) -> float:
    v = 安全浮点(x)
    if math.isnan(v) or math.isinf(v):
        v = 0.0
    return max(lo, min(hi, v))


def 涨幅百分比(a: float, b: float) -> float:
    return (a / b - 1.0) * 100.0 if b and b > 0 else 0.0


def 标准代码(raw: str) -> str:
    text = str(raw).strip()
    m = re.search(r"(sh|sz|bj)[._-]?(\d{6})", text, re.I)
    if m:
        return f"{m.group(1).lower()}.{m.group(2)}"
    m = re.search(r"(\d{6})", text)
    if not m:
        return text
    code = m.group(1)
    # 北交所920必须优先于“9字头上海”判断，否则920xxx会被误归为sh.920xxx。
    if code.startswith(("920", "4", "8")):
        return "bj." + code
    if code.startswith(("6", "9")):
        return "sh." + code
    return "sz." + code


def 显示代码(code: str) -> str:
    return 标准代码(code).split(".")[-1]


def A股有效代码(code: str) -> bool:
    c = 显示代码(code)
    return c.startswith(("000", "001", "002", "003", "300", "301", "600", "601", "603", "605", "688", "689", "920", "8", "4"))


def 命中名称硬风险(name: str) -> bool:
    """名称硬风险只识别A股真实ST/退市语义，避免英文缩写里包含st造成误杀。"""
    txt = str(name or "").strip()
    if not txt:
        return False
    upper = txt.upper()
    return bool(re.search(r"^(?:\*ST|ST|S\*ST|SST)", upper) or "退市" in txt or txt.startswith("退"))


def 涨停阈值(code: str) -> float:
    c = 显示代码(code)
    if c.startswith(("300", "301", "688", "689")):
        return 19.3
    if c.startswith(("8", "4", "920")):
        return 29.0
    return 9.3


# ---------- 数据读取 ----------
def 读取缓存文件(path: Path) -> pd.DataFrame:
    try:
        df = pd.read_csv(path)
    except UnicodeDecodeError:
        df = pd.read_csv(path, encoding="gbk")
    except Exception:
        return pd.DataFrame()

    df.columns = [str(c).strip() for c in df.columns]
    lower_map = {c: str(c).strip().lower() for c in df.columns}
    df = df.rename(columns=lower_map)

    rename = {
        "日期": "date", "交易日期": "date", "trade_date": "date", "time": "date",
        "代码": "code", "证券代码": "code", "股票代码": "code", "symbol": "code",
        "名称": "name", "股票名称": "name", "股票中文名称": "name", "证券简称": "name",
        "开盘": "open", "开盘价": "open",
        "最高": "high", "最高价": "high",
        "最低": "low", "最低价": "low",
        "收盘": "close", "收盘价": "close",
        "成交量": "volume", "vol": "volume",
        "成交额": "amount",
        "涨跌幅": "pct_chg", "涨幅": "pct_chg", "pctchg": "pct_chg",
    }
    df = df.rename(columns={c: rename.get(c, c) for c in df.columns})

    if not {"date", "open", "high", "low", "close"}.issubset(df.columns):
        return pd.DataFrame()

    amount_was_present = "amount" in df.columns
    for c in ["open", "high", "low", "close", "volume", "amount", "pct_chg"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c].astype(str).str.replace("%", ""), errors="coerce")

    raw_date = df["date"].astype(str).str.replace("-", "", regex=False).str.replace("/", "", regex=False)
    df["date"] = pd.to_datetime(raw_date.str[:8], format="%Y%m%d", errors="coerce")

    df = (
        df.dropna(subset=["date", "open", "high", "low", "close"])
        .sort_values("date")
        .drop_duplicates("date", keep="last")
        .reset_index(drop=True)
    )

    target_day = 目标交易日对象()
    if target_day is not None:
        df = df[df["date"] <= target_day].reset_index(drop=True)
        if df.empty:
            return pd.DataFrame()
        if 要求严格目标日 and pd.Timestamp(df["date"].max()).normalize() != target_day:
            return pd.DataFrame()

    if "volume" not in df.columns:
        df["volume"] = 0.0
    df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0.0)

    if (not amount_was_present) or "amount" not in df.columns or pd.to_numeric(df.get("amount"), errors="coerce").fillna(0.0).abs().sum() <= 0:
        df["amount"] = df["close"] * df["volume"]
        df["amount_quality"] = "estimated_close_x_volume"
    else:
        df["amount"] = pd.to_numeric(df["amount"], errors="coerce").fillna(0.0)
        df["amount_quality"] = "reported"

    # 兼容不同数据源：有些缓存 volume 单位可能是“手”，amount 缺失时 close*volume 会低估100倍。
    # 不直接用这个值替代真实成交额，只在成交额缺失/估算时作为流动性硬杀的反证参考。
    df["amount_alt_hand_unit"] = df["close"] * df["volume"] * 100.0

    if "pct_chg" not in df.columns or df["pct_chg"].abs().sum() == 0:
        prev = df["close"].shift(1)
        df["pct_chg"] = (df["close"] / prev - 1.0) * 100.0
        df.loc[prev <= 0, "pct_chg"] = 0.0
    if "name" not in df.columns:
        df["name"] = ""
    if "code" not in df.columns:
        df["code"] = ""

    return df

def 快速缓存最后日期(path: Path) -> pd.Timestamp:
    """快速读取缓存文件尾部日期，避免为了择新而全量读几千个CSV。"""
    try:
        size = path.stat().st_size
        with path.open("rb") as f:
            f.seek(max(0, size - 65536))
            raw = f.read()
        text = raw.decode("utf-8", errors="ignore")
        if not text.strip():
            text = raw.decode("gbk", errors="ignore")
        matches = re.findall(r"(?:20\d{2}[-/]?\d{1,2}[-/]?\d{1,2}|\d{8})", text)
        for token in reversed(matches):
            t = token.replace("/", "-")
            if len(t) == 8 and t.isdigit():
                dt = pd.to_datetime(t, format="%Y%m%d", errors="coerce")
            else:
                dt = pd.to_datetime(t, errors="coerce")
            if not pd.isna(dt):
                return pd.Timestamp(dt).normalize()
    except Exception:
        pass
    return pd.Timestamp.min


def 找缓存文件() -> List[Path]:
    """为每个代码选择最新缓存文件。V15：三号员工式可见进度 + 快速尾部日期择新。"""
    日志("开始索引K线缓存文件")
    candidates: Dict[str, List[Path]] = {}
    total_seen = 0
    for d in 缓存目录列表:
        if not d.exists():
            日志(f"缓存目录不存在，跳过：{d}")
            continue
        dir_seen = 0
        try:
            iterator = d.rglob("*.csv")
        except Exception:
            iterator = d.glob("*.csv")
        for p in iterator:
            total_seen += 1
            dir_seen += 1
            if total_seen % 缓存索引日志间隔 == 0:
                日志(f"缓存索引中：已查看{total_seen}个CSV，候选代码{len(candidates)}")
            name = p.name.lower()
            if any(x in name for x in ["report", "result", "summary", "明细", "报告"]):
                continue
            m = re.search(r"(\d{6})", name)
            if not m:
                continue
            code = 标准代码(m.group(1))
            if A股有效代码(code):
                candidates.setdefault(显示代码(code), []).append(p)
        日志(f"缓存目录扫描完成：{d}｜CSV={dir_seen}｜累计候选代码={len(candidates)}")

    def file_key(path: Path) -> Tuple[pd.Timestamp, float]:
        last_date = 快速缓存最后日期(path)
        try:
            mtime = path.stat().st_mtime
        except Exception:
            mtime = 0.0
        return last_date, mtime

    picked: List[Path] = []
    duplicate_codes = 0
    for i, (_, paths) in enumerate(candidates.items(), 1):
        if len(paths) == 1:
            picked.append(paths[0])
        else:
            duplicate_codes += 1
            picked.append(max(paths, key=file_key))
        if i % 缓存索引日志间隔 == 0:
            日志(f"缓存去重中：{i}/{len(candidates)}，重复代码={duplicate_codes}")
    日志(f"缓存索引完成：原始CSV={total_seen}｜有效代码={len(candidates)}｜重复代码={duplicate_codes}｜最终文件={len(picked)}")
    return picked


def 成交额20日状态(df: pd.DataFrame) -> Dict[str, Any]:
    """20日成交额状态。

    V10.3修正：amount缺失时，close*volume*100只能作为“单位可能是手”的参考，不能直接替代真实成交额
    让股票通过正式池。正式池只信reported成交额；估算成交额最多允许事件/观察，避免低流动性票被放大100倍误放行。
    """
    d = df.copy()
    if d.empty:
        return {
            "amount20": 0.0, "raw_amount20": 0.0, "alt_amount20": 0.0,
            "formal_amount20": 0.0, "observe_amount20": 0.0,
            "quality": "missing", "low": True, "formal_low": True, "observe_low": True,
            "reliable": False, "unit_uncertain": True, "formal_block": True,
            "detail": "成交额样本不足",
        }

    raw_series = pd.to_numeric(d.get("amount"), errors="coerce") if "amount" in d.columns else pd.Series(dtype=float)
    raw = 安全浮点(raw_series.tail(20).mean()) if not raw_series.empty else 0.0
    alt = 安全浮点(pd.to_numeric(d.get("amount_alt_hand_unit"), errors="coerce").tail(20).mean()) if "amount_alt_hand_unit" in d.columns else 0.0
    quality_series = d.get("amount_quality")
    quality = str(quality_series.dropna().iloc[-1]) if isinstance(quality_series, pd.Series) and not quality_series.dropna().empty else "unknown"

    reliable = quality == "reported" and raw > 0
    unit_uncertain = not reliable
    formal_amount = raw if reliable else 0.0
    observe_amount = raw
    amount_basis = "reported" if reliable else "estimated_raw"
    detail = f"成交额20日{raw/1e8:.2f}亿"

    if reliable:
        formal_low = raw < 最低成交额20日
        observe_low = formal_low
        low = formal_low
        formal_block = formal_low
        detail = f"成交额20日{raw/1e8:.2f}亿｜reported"
    else:
        # 估算口径分两层：raw是真实最保守口径；alt只作为观察层参考，不允许让正式池过闸。
        if raw >= 最低成交额20日:
            observe_amount = raw
            observe_low = False
            detail = f"成交额缺失估算raw {raw/1e8:.2f}亿，单位不确定；仅允许观察/事件，正式池需reported成交额"
        elif alt >= 最低成交额20日 and 成交额估算允许观察:
            observe_amount = alt
            observe_low = False
            amount_basis = "estimated_alt_hand_unit_reference"
            detail = f"成交额缺失估算raw {raw/1e8:.2f}亿，按volume为手参考{alt/1e8:.2f}亿；单位不确定，仅观察不正式"
        else:
            observe_amount = max(raw, alt)
            observe_low = True
            detail = f"成交额缺失估算不足：raw {raw/1e8:.2f}亿/手口径参考{alt/1e8:.2f}亿"
        formal_low = True
        low = True if 成交额缺失正式硬拒 else observe_low
        formal_block = bool(成交额缺失正式硬拒)

    return {
        "amount20": 四舍五入(observe_amount if not reliable else raw, 2),
        "formal_amount20": 四舍五入(formal_amount, 2),
        "observe_amount20": 四舍五入(observe_amount, 2),
        "raw_amount20": 四舍五入(raw, 2),
        "alt_amount20": 四舍五入(alt, 2),
        "quality": quality,
        "amount_basis": amount_basis,
        "low": bool(low),
        "formal_low": bool(formal_low),
        "observe_low": bool(observe_low),
        "reliable": bool(reliable),
        "unit_uncertain": bool(unit_uncertain),
        "formal_block": bool(formal_block),
        "detail": detail,
    }


def 外部雷区字段检查(df: pd.DataFrame) -> Dict[str, Any]:
    """读取缓存中可能已经带入的公告/财务/监管风险字段。

    V13修正：风险字段不能“非空即硬拦”。先识别否定语义，再分硬雷区/中风险/提示项。
    只有立案、退市、非标、资金占用、违规担保、重大诉讼、债务违约等硬雷区才block；
    “暂无诉讼/无减持计划/低风险/正常”等不会误杀。
    """
    if df is None or df.empty:
        return {"available": False, "block": False, "level": "未知", "flags": [], "soft_flags": [], "info_flags": [], "detail": "外部雷区字段未接入"}
    latest = df.iloc[-1]
    risk_cols = [
        "risk_flags", "hard_risk_flags", "external_risk_flags", "公告风险", "监管风险", "财务风险", "治理风险",
        "risk_level", "external_risk_level", "audit_opinion", "审计意见", "立案调查", "处罚", "诉讼", "质押冻结",
        "退市风险", "非标审计", "资金占用", "违规担保", "业绩亏损", "扣非亏损", "减持", "财报重述",
    ]
    existing = [c for c in risk_cols if c in df.columns]
    if not existing:
        return {"available": False, "block": False, "level": "未知", "flags": [], "soft_flags": [], "info_flags": [], "detail": "外部雷区字段未接入"}

    negative_keywords = (
        "无", "暂无", "未见", "未发现", "不存在", "否", "没有", "正常", "低风险", "低", "low", "none", "false", "0",
        "无减持", "暂无减持", "无诉讼", "暂无诉讼", "未立案", "未处罚", "不适用", "nan"
    )
    hard_keywords = (
        "立案", "调查", "重大违法", "退市", "暂停上市", "终止上市", "非标", "无法表示", "否定意见", "保留意见",
        "资金占用", "违规担保", "债务违约", "破产", "重整", "司法冻结", "司法拍卖", "信披违规",
        "持续经营重大不确定", "财报重述", "重大诉讼", "重大仲裁", "商誉减值", "ST", "*ST"
    )
    soft_keywords = (
        "减持计划", "高质押", "质押比例高", "业绩亏损", "扣非亏损", "诉讼", "仲裁", "处罚", "警示函", "监管函",
        "业绩下滑", "商誉", "问询函", "延期披露", "审计强调事项"
    )

    hard_flags: List[str] = []
    soft_flags: List[str] = []
    info_flags: List[str] = []
    hard_cols = {"立案调查", "退市风险", "非标审计", "资金占用", "违规担保"}

    for c in existing:
        v = latest.get(c)
        if v is None or (isinstance(v, float) and math.isnan(v)):
            continue
        raw = str(v).strip()
        text = raw.replace(" ", "")
        low = text.lower()
        if not text or low in {"0", "false", "none", "nan"}:
            continue
        if any(k.lower() in low for k in negative_keywords):
            continue

        snippet = raw[:80]
        if c in hard_cols:
            hard_flags.append(f"{c}:{snippet}")
            continue
        if any(k in text for k in hard_keywords):
            hard_flags.append(f"{c}:{snippet}")
        elif any(k in text for k in soft_keywords):
            soft_flags.append(f"{c}:{snippet}")
        elif c.lower() in {"risk_level", "external_risk_level"} or c in {"公告风险", "监管风险", "财务风险", "治理风险"}:
            # 风险等级类字段只在明确高/重大/严重时硬拦；普通“中/一般”作为提示。
            if any(k in text for k in ("高", "重大", "严重", "high", "major")):
                hard_flags.append(f"{c}:{snippet}")
            elif any(k in text for k in ("中", "一般", "medium")):
                soft_flags.append(f"{c}:{snippet}")
            else:
                info_flags.append(f"{c}:{snippet}")

    block = bool(hard_flags)
    if block:
        level = "高"
    elif soft_flags:
        level = "中"
    else:
        level = "无"
    details = hard_flags + soft_flags + info_flags
    return {
        "available": True,
        "block": block,
        "level": level,
        "flags": hard_flags,
        "soft_flags": soft_flags,
        "info_flags": info_flags,
        "detail": "；".join(details) if details else "外部雷区字段已接入且未命中",
    }


def 基础硬风险检查(code: str, df: pd.DataFrame) -> Dict[str, Any]:
    """破界海选器的基础硬风险过滤：技术可交易 + 已接入外部雷区字段硬拦。"""
    d = df.copy()
    flags: List[str] = []
    block = False
    if d.empty:
        return {"block": True, "level": "高", "flags": ["数据为空"], "detail": "数据为空"}

    latest = d.iloc[-1]
    name = str(latest.get("name", "") or "")
    code6 = 显示代码(code)
    if 命中名称硬风险(name):
        flags.append(f"名称硬风险:{name}")
        block = True
    if (code6.startswith(("8", "4", "920"))) and not 允许北交所:
        flags.append("北交所标的被当前配置关闭")
        block = True
    if len(d) < 最少K线数:
        flags.append("上市/样本过短")
        block = True

    tail10 = d.tail(10)
    zero_vol_days = int((pd.to_numeric(tail10.get("volume"), errors="coerce").fillna(0.0) <= 0).sum()) if not tail10.empty else 10
    if zero_vol_days >= 3:
        flags.append(f"近10日无成交/疑似停牌{zero_vol_days}天")
        block = True
    if 安全浮点(latest.get("close")) <= 0 or 安全浮点(latest.get("high")) <= 0 or 安全浮点(latest.get("low")) <= 0:
        flags.append("最新价格异常")
        block = True

    amount_state = 成交额20日状态(d)
    if amount_state.get("low"):
        flags.append(amount_state.get("detail", "成交额不足"))
        # 成交额不足不在这里直接block，仍交给交易硬闸归入事件记录；避免缺失amount时误杀。

    external_risk = 外部雷区字段检查(d)
    if bool(external_risk.get("block")):
        flags.extend([f"外部雷区:{x}" for x in external_risk.get("flags", [])])
        block = True

    level = "高" if block else "中" if flags else "无"
    detail = "；".join(flags) or "无基础硬风险"
    if not bool(external_risk.get("available")):
        detail += "；外部公告/财务/监管雷区字段未接入，正式交易需另行硬复核"
    return {"block": bool(block), "level": level, "flags": flags, "detail": detail, "amount_state": amount_state, "external_risk": external_risk}

def 加基础指标(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy().reset_index(drop=True)
    if d.empty:
        return d
    close = pd.to_numeric(d["close"], errors="coerce")
    high = pd.to_numeric(d["high"], errors="coerce")
    low = pd.to_numeric(d["low"], errors="coerce")
    open_ = pd.to_numeric(d["open"], errors="coerce")
    volume = pd.to_numeric(d["volume"], errors="coerce").fillna(0.0)
    amount = pd.to_numeric(d["amount"], errors="coerce").fillna(0.0)
    prev_close = close.shift(1)

    d["ma3"] = close.rolling(3, min_periods=2).mean()
    d["ma5"] = close.rolling(5, min_periods=3).mean()
    d["ma6"] = close.rolling(6, min_periods=3).mean()
    d["ma10"] = close.rolling(10, min_periods=5).mean()
    d["ma12"] = close.rolling(12, min_periods=6).mean()
    d["ma20"] = close.rolling(20, min_periods=10).mean()
    d["ma24"] = close.rolling(24, min_periods=12).mean()
    d["bbi"] = (d["ma3"] + d["ma6"] + d["ma12"] + d["ma24"]) / 4.0
    d["vol_ma20"] = volume.rolling(20, min_periods=8).mean()
    d["vol_med20"] = volume.rolling(20, min_periods=8).median()
    d["amount20"] = amount.rolling(20, min_periods=8).mean()
    d["body_top"] = pd.concat([open_, close], axis=1).max(axis=1)
    d["body_bottom"] = pd.concat([open_, close], axis=1).min(axis=1)
    d["body_mid"] = (d["body_top"] + d["body_bottom"]) / 2.0
    d["range"] = (high - low).replace(0, np.nan)
    d["range_pct"] = (high - low) / prev_close.replace(0, np.nan)
    d["body_abs_pct"] = (close - open_).abs() / prev_close.replace(0, np.nan)
    d["body_pct"] = (close - open_) / prev_close.replace(0, np.nan)
    d["body_ratio"] = (close - open_).abs() / d["range"]
    d["close_pos"] = (close - low) / d["range"]
    d["upper_shadow_ratio"] = (high - d["body_top"]) / d["range"]
    d["lower_shadow_ratio"] = (d["body_bottom"] - low) / d["range"]
    return d





# ---------- V7 界体系：自适应容差、多周期聚合、边界带置信度 ----------
def 周期聚合(df: pd.DataFrame, period: str, date_label: str = "period") -> pd.DataFrame:
    """把日K聚合成周/月/季/年K。用于大周期界，不改变日线触发逻辑。"""
    x = df.copy()
    if x.empty or "date" not in x.columns:
        return pd.DataFrame()
    x["周期"] = x["date"].dt.to_period(period)
    rows: List[Dict[str, Any]] = []
    for per, g in x.groupby("周期", sort=True):
        g = g.sort_values("date").reset_index(drop=True)
        rows.append({
            date_label: str(per),
            "date": g["date"].iloc[-1],
            "open": 安全浮点(g["open"].iloc[0]),
            "high": 安全浮点(g["high"].max()),
            "low": 安全浮点(g["low"].min()),
            "close": 安全浮点(g["close"].iloc[-1]),
            "volume": 安全浮点(g["volume"].sum()),
            "amount": 安全浮点(g["amount"].sum()) if "amount" in g.columns else 0.0,
        })
    k = pd.DataFrame(rows)
    if k.empty:
        return k
    k["body_top"] = k[["open", "close"]].max(axis=1)
    k["body_bottom"] = k[["open", "close"]].min(axis=1)
    k["body_mid"] = (k["body_top"] + k["body_bottom"]) / 2.0
    k["range"] = (k["high"] - k["low"]).replace(0, np.nan)
    k["body_ratio"] = ((k["close"] - k["open"]).abs() / k["range"]).fillna(0.0)
    k["vol_med12"] = k["volume"].rolling(12, min_periods=3).median()
    return k


def 自适应界容差(k: pd.DataFrame, timeframe: str = "日线") -> Tuple[float, float]:
    """根据波动率生成核心线贴合容差和边界带分组容差。

    用户思路：核心线必须来自最大共振，不可乱漂移。
    专业补充：不同股票/周期波动不同，固定1%会导致低波票太宽、高波票太窄。
    因此容差只在小范围内自适应，且设置上下限，避免模型漂移。
    """
    if not 启用自适应界容差 or k.empty:
        return 核心线容差, 核心线带宽
    close = pd.to_numeric(k.get("close"), errors="coerce").replace(0, np.nan)
    high = pd.to_numeric(k.get("high"), errors="coerce")
    low = pd.to_numeric(k.get("low"), errors="coerce")
    range_pct = ((high - low).abs() / close).replace([np.inf, -np.inf], np.nan).dropna()
    med_range = float(range_pct.tail(60).median()) if not range_pct.empty else 0.0
    tf = str(timeframe)
    if "年" in tf:
        factor, base_add = 0.030, 0.004
    elif "季" in tf:
        factor, base_add = 0.040, 0.003
    elif "月" in tf:
        factor, base_add = 0.055, 0.002
    elif "周" in tf:
        factor, base_add = 0.090, 0.001
    else:
        factor, base_add = 0.160, 0.000
    tol = 核心线容差 + base_add + med_range * factor
    tol = 夹紧(tol, 核心线最小容差, 核心线最大容差)
    band = max(核心线带宽, tol * 1.55)
    band = 夹紧(band, 边界带最小容差, 边界带最大容差)
    return 四舍五入(tol, 5), 四舍五入(band, 5)



def 量能触线质量(open_: float, high: float, low: float, close: float, volume: float, vol_med: float) -> Dict[str, Any]:
    """把“带量共振”拆成可交易质量，而不是固定+0.60。

    返回的 bonus 是普通触线 +1 之外的额外分；爆量滞涨触线给负分。
    """
    if vol_med <= 0 or volume <= 0:
        return {"type": "普通共振", "bonus": 0.0, "ratio": 0.0, "is_volume": False, "is_stall": False}
    ratio = volume / vol_med
    rng = max(high - low, 1e-9)
    body_top = max(open_, close)
    body_abs = abs(close - open_)
    close_pos = (close - low) / rng
    upper = (high - body_top) / rng
    body_ratio = body_abs / rng
    bullish = close > open_
    if ratio < 1.30:
        return {"type": "普通共振", "bonus": 0.0, "ratio": ratio, "is_volume": False, "is_stall": False}
    stall = bool((not bullish) or close_pos < 0.55 or upper >= 0.45 or (ratio >= 2.8 and body_ratio < 0.22))
    if stall:
        return {"type": "放量滞涨触线", "bonus": -0.60, "ratio": ratio, "is_volume": True, "is_stall": True}
    if bullish and 2.50 < ratio <= 4.50 and body_ratio >= 0.45 and close_pos >= 0.70 and upper <= 0.28:
        return {"type": "高质量倍量共振", "bonus": 1.10, "ratio": ratio, "is_volume": True, "is_stall": False}
    if bullish and 1.80 <= ratio <= 2.50 and body_ratio >= 0.30 and close_pos >= 0.62 and upper <= 0.35:
        return {"type": "标准倍量共振", "bonus": 0.90, "ratio": ratio, "is_volume": True, "is_stall": False}
    if bullish and ratio >= 1.50 and close_pos >= 0.62 and upper <= 0.38:
        return {"type": "健康放量共振", "bonus": 0.60, "ratio": ratio, "is_volume": True, "is_stall": False}
    if bullish:
        return {"type": "温和放量共振", "bonus": 0.40, "ratio": ratio, "is_volume": True, "is_stall": False}
    return {"type": "普通带量触线", "bonus": 0.15, "ratio": ratio, "is_volume": True, "is_stall": False}


def 计算VBP筹码带(df: pd.DataFrame, lookback_days: int = VBP最大回看日, bins: int = VBP分箱数量) -> Dict[str, Any]:
    """按 high-low 区间把成交额分布到价格箱，实体区和收盘附近加权。

    V10 不再把整根K线成交额压到典型价，避免大振幅/长影线K把筹码带画偏。
    输出的 VBP 只用于参考/置信度，不参与硬确认线和正式压力带宽；成交额非 reported 时只保留不可用说明。
    """
    if not 启用VBP筹码带 or df.empty:
        return {"enabled": False, "vbp_cluster_score": 0.0, "vbp_band_low": 0.0, "vbp_band_high": 0.0, "vbp_peak_price": 0.0, "vbp_method": "disabled"}
    d = df.copy().reset_index(drop=True)
    amount_state_for_vbp = 成交额20日状态(d)
    if not bool(amount_state_for_vbp.get("reliable")):
        return {
            "enabled": True, "vbp_cluster_score": 0.0, "vbp_band_low": 0.0, "vbp_band_high": 0.0, "vbp_peak_price": 0.0,
            "vbp_method": "amount_unreliable_reference_only",
            "reason": "成交额非reported，VBP不参与排序/带宽/硬闸",
            "vbp_amount_reliable": False,
        }
    if lookback_days > 0 and len(d) > lookback_days:
        d = d.tail(lookback_days).copy().reset_index(drop=True)
    if len(d) < 30:
        return {"enabled": True, "vbp_cluster_score": 0.0, "vbp_band_low": 0.0, "vbp_band_high": 0.0, "vbp_peak_price": 0.0, "reason": "VBP样本不足", "vbp_method": "range_distributed"}

    high = pd.to_numeric(d.get("high"), errors="coerce").fillna(0.0)
    low = pd.to_numeric(d.get("low"), errors="coerce").fillna(0.0)
    open_ = pd.to_numeric(d.get("open"), errors="coerce").fillna(0.0)
    close = pd.to_numeric(d.get("close"), errors="coerce").fillna(0.0)
    amount = pd.to_numeric(d.get("amount"), errors="coerce").fillna(0.0)
    if float(amount.sum()) <= 0:
        return {"enabled": True, "vbp_cluster_score": 0.0, "vbp_band_low": 0.0, "vbp_band_high": 0.0, "vbp_peak_price": 0.0, "reason": "reported成交额为空，VBP停用", "vbp_method": "reported_amount_missing", "vbp_amount_reliable": False}

    valid = (high > 0) & (low > 0) & (high >= low) & (close > 0) & (amount > 0)
    if int(valid.sum()) < 20:
        return {"enabled": True, "vbp_cluster_score": 0.0, "vbp_band_low": 0.0, "vbp_band_high": 0.0, "vbp_peak_price": 0.0, "reason": "VBP成交额不足", "vbp_method": "range_distributed"}

    h = high[valid].to_numpy(dtype=float)
    l = low[valid].to_numpy(dtype=float)
    o = open_[valid].to_numpy(dtype=float)
    c = close[valid].to_numpy(dtype=float)
    a = amount[valid].to_numpy(dtype=float)
    lo, hi = float(np.nanmin(l)), float(np.nanmax(h))
    if lo <= 0 or hi <= lo:
        return {"enabled": True, "vbp_cluster_score": 0.0, "vbp_band_low": 0.0, "vbp_band_high": 0.0, "vbp_peak_price": 0.0, "reason": "VBP价格范围异常", "vbp_method": "range_distributed"}

    bins = max(24, min(int(bins), 180))
    edges = np.linspace(lo, hi, bins + 1)
    hist = np.zeros(bins, dtype=float)
    centers = (edges[:-1] + edges[1:]) / 2.0

    for open_i, high_i, low_i, close_i, amount_i in zip(o, h, l, c, a):
        if amount_i <= 0 or high_i <= 0 or low_i <= 0:
            continue
        rng_low, rng_high = min(low_i, high_i), max(low_i, high_i)
        if rng_high <= rng_low:
            idx = int(np.clip(np.searchsorted(edges, close_i, side="right") - 1, 0, bins - 1))
            hist[idx] += amount_i
            continue
        overlap = np.maximum(0.0, np.minimum(edges[1:], rng_high) - np.maximum(edges[:-1], rng_low))
        weights = overlap / max(rng_high - rng_low, 1e-9)
        body_low, body_high = min(open_i, close_i), max(open_i, close_i)
        body_overlap = np.maximum(0.0, np.minimum(edges[1:], body_high) - np.maximum(edges[:-1], body_low))
        if body_high > body_low:
            weights += 0.65 * body_overlap / max(body_high - body_low, 1e-9)
        close_idx = int(np.clip(np.searchsorted(edges, close_i, side="right") - 1, 0, bins - 1))
        weights[close_idx] += 0.20
        sw = float(weights.sum())
        if sw > 0:
            hist += amount_i * weights / sw

    total = float(hist.sum())
    if total <= 0:
        return {"enabled": True, "vbp_cluster_score": 0.0, "vbp_band_low": 0.0, "vbp_band_high": 0.0, "vbp_peak_price": 0.0, "reason": "VBP权重为空", "vbp_method": "range_distributed"}

    peak_idx = int(np.argmax(hist))
    target = total * 0.20
    left = right = peak_idx
    acc = float(hist[peak_idx])
    while acc < target and (left > 0 or right < len(hist) - 1):
        lv = hist[left - 1] if left > 0 else -1
        rv = hist[right + 1] if right < len(hist) - 1 else -1
        if rv >= lv and right < len(hist) - 1:
            right += 1; acc += float(hist[right])
        elif left > 0:
            left -= 1; acc += float(hist[left])
        else:
            break
    band_low = float(edges[left])
    band_high = float(edges[right + 1])
    peak_price = float(centers[peak_idx])
    amount_ratio = acc / total if total > 0 else 0.0
    width_pct = 涨幅百分比(band_high, band_low) if band_low > 0 else 0.0
    density = amount_ratio / max(width_pct / 100.0, 1e-6)
    cluster_score = 夹紧(amount_ratio * 100.0 + min(12.0, density * 0.08) + max(0.0, 6.0 - width_pct) * 1.2, 0.0, 40.0)
    return {
        "enabled": True,
        "vbp_band_low": 四舍五入(band_low, 3),
        "vbp_band_high": 四舍五入(band_high, 3),
        "vbp_peak_price": 四舍五入(peak_price, 3),
        "vbp_amount_ratio": 四舍五入(amount_ratio, 4),
        "vbp_band_width_pct": 四舍五入(width_pct, 2),
        "vbp_cluster_score": 四舍五入(cluster_score, 2),
        "vbp_total_amount": 四舍五入(total, 2),
        "vbp_density_score": 四舍五入(density, 3),
        "vbp_method": "range_distributed_entity_close_weighted",
        "vbp_amount_reliable": True,
    }


def 区间重叠比例(a_low: float, a_high: float, b_low: float, b_high: float) -> float:
    a_low, a_high, b_low, b_high = map(安全浮点, [a_low, a_high, b_low, b_high])
    if a_low <= 0 or a_high <= 0 or b_low <= 0 or b_high <= 0:
        return 0.0
    if a_low > a_high:
        a_low, a_high = a_high, a_low
    if b_low > b_high:
        b_low, b_high = b_high, b_low
    inter = max(0.0, min(a_high, b_high) - max(a_low, b_low))
    base = max(1e-9, min(a_high - a_low, b_high - b_low))
    return 夹紧(inter / base, 0.0, 1.0)


def 核心线敏感性测试(k: pd.DataFrame, line: float, source: str, line_tol: float) -> Dict[str, Any]:
    """验证“最低有效边界线”：线再往下是否因切实体显著变差。"""
    if not 启用最低边界敏感性测试 or k.empty or 安全浮点(line) <= 0:
        return {"sensitivity_bonus": 0.0, "lowest_valid_boundary": False, "sensitivity_summary": "未启用"}
    rows: List[Dict[str, Any]] = []
    for s in 敏感性偏移列表:
        L = 安全浮点(line) * (1.0 + 安全浮点(s))
        ev = 评估单条核心线(k, L, source, line_tol=line_tol)
        rows.append({"shift": 安全浮点(s), "line": 四舍五入(L, 3), "net": 安全浮点(ev.get("net_score")), "hit": 安全浮点(ev.get("effective_resonance_count")), "cut": 安全浮点(ev.get("entity_cut_count")), "vcut": 安全浮点(ev.get("volume_entity_cut_count"))})
    cur = min(rows, key=lambda x: abs(x["shift"])) if rows else {"net": 0, "cut": 0, "vcut": 0}
    down = [x for x in rows if x["shift"] < 0]
    up = [x for x in rows if x["shift"] > 0]
    best = max(rows, key=lambda x: x["net"]) if rows else cur
    down_worse = False
    if down:
        down_best = max(down, key=lambda x: x["net"])
        down_worse = bool(down_best["net"] <= cur["net"] + 0.20 and min(x["cut"] + x["vcut"] for x in down) >= cur["cut"] + cur["vcut"])
    up_loses = bool(up and max(x["net"] for x in up) <= cur["net"] + 0.15)
    lowest_valid = bool(down_worse and cur["net"] >= 0)
    bonus = 0.0
    if lowest_valid:
        bonus += 1.2
    if up_loses:
        bonus += 0.5
    if abs(best["shift"]) >= 0.005 and best["net"] > cur["net"] + 1.0:
        bonus -= 1.0
    return {
        "sensitivity_bonus": 四舍五入(夹紧(bonus, -2.0, 2.0), 2),
        "lowest_valid_boundary": lowest_valid,
        "sensitivity_best_line": best.get("line", 0),
        "sensitivity_current_net": 四舍五入(cur.get("net", 0), 3),
        "sensitivity_best_net": 四舍五入(best.get("net", 0), 3),
        "sensitivity_down_05_net": 四舍五入(next((x["net"] for x in rows if abs(x["shift"] + 0.005) < 1e-9), 0), 3),
        "sensitivity_down_10_net": 四舍五入(next((x["net"] for x in rows if abs(x["shift"] + 0.010) < 1e-9), 0), 3),
        "sensitivity_up_05_net": 四舍五入(next((x["net"] for x in rows if abs(x["shift"] - 0.005) < 1e-9), 0), 3),
        "sensitivity_up_10_net": 四舍五入(next((x["net"] for x in rows if abs(x["shift"] - 0.010) < 1e-9), 0), 3),
        "sensitivity_summary": f"当前净分{cur.get('net',0):.2f}｜最佳{best.get('line',0)}={best.get('net',0):.2f}｜下移变差={down_worse}",
    }


def 注入敏感性字段(k: pd.DataFrame, scored: List[Dict[str, Any]], sources: Dict[float, str], line_tol: float) -> List[Dict[str, Any]]:
    if not scored or not 启用最低边界敏感性测试:
        return scored
    out: List[Dict[str, Any]] = []
    source_map = {四舍五入(p, 3): s for p, s in sources.items()}
    for item in scored:
        L = 四舍五入(item.get("line"), 3)
        src = source_map.get(L, str(item.get("source", "")))
        sens = 核心线敏感性测试(k, L, src, line_tol)
        z = dict(item)
        z.update(sens)
        z["boundary_quality_score"] = 四舍五入(安全浮点(z.get("net_score")) + 安全浮点(z.get("sensitivity_bonus")) + 安全浮点(z.get("vbp_support_score")), 3)
        out.append(z)
    return out


def 细分边界状态(item: Dict[str, Any]) -> str:
    """静态状态兜底；优先使用 时间序列边界状态() 的结果。"""
    hit = 安全浮点(item.get("effective_resonance_count"))
    accept = 安全浮点(item.get("entity_accept_count"))
    false_break = 安全浮点(item.get("false_breakout_count"))
    failed = 安全浮点(item.get("failed_retest_count"))
    if false_break >= 2:
        return "多次假突破记忆线"
    if failed >= 3:
        return "破位后反抽失败线"
    if accept >= max(3.0, hit):
        return "实体突破接受线"
    if accept > 0:
        return "存在实体接受记录"
    return "未接受压力线"


def 时间序列边界状态(k: pd.DataFrame, line: float, tol: float) -> Dict[str, Any]:
    """按时间顺序识别压力/支撑转换、假突破、反抽失败，而不是只数次数。"""
    L = 安全浮点(line)
    if k.empty or L <= 0:
        return {"acceptance_state": "状态样本不足", "boundary_role": "未知", "false_breakout_count": 0, "failed_retest_count": 0}
    d = k.copy().reset_index(drop=True)
    false_break = 0
    failed_retest = 0
    accepted_segments = 0
    below_segments = 0
    last_state = "below"
    recent_state = "below"
    for _, r in d.iterrows():
        high = 安全浮点(r.get("high")); low = 安全浮点(r.get("low")); close = 安全浮点(r.get("close"))
        body_bottom = 安全浮点(r.get("body_bottom")); body_top = 安全浮点(r.get("body_top"))
        if high <= 0 or close <= 0:
            continue
        entity_above = body_bottom > L * (1.0 + tol * 0.20)
        close_above = close > L * (1.0 + tol * 0.20)
        close_below = close < L * (1.0 - tol * 0.35)
        touch_fail = high >= L * (1.0 - tol) and close_below
        fake = high > L * (1.0 + tol) and close < L
        if fake:
            false_break += 1
        if touch_fail:
            failed_retest += 1
        state = "accepted" if entity_above and close_above else "below" if close_below else "touch"
        if state == "accepted" and last_state != "accepted":
            accepted_segments += 1
        if state == "below" and last_state == "accepted":
            below_segments += 1
        if state in {"accepted", "below"}:
            last_state = state
        recent_state = state
    tail = d.tail(min(6, len(d)))
    tail_accept = bool(not tail.empty and (tail["body_bottom"] > L).sum() >= max(2, len(tail)//2)) if "body_bottom" in tail.columns else False
    tail_below = bool(not tail.empty and (tail["close"] < L * (1.0 - tol)).sum() >= max(2, len(tail)//2))
    if false_break >= 2 and not tail_accept:
        state_text, role = "多次假突破记忆线", "压力"
    elif failed_retest >= 3 and tail_below:
        state_text, role = "破位后反抽失败线", "压力"
    elif accepted_segments > 0 and below_segments > 0 and tail_below:
        state_text, role = "支撑跌破后反压线", "压力"
    elif tail_accept:
        state_text, role = "压力转支撑接受线", "支撑"
    elif accepted_segments > 0:
        state_text, role = "实体突破接受线", "中性偏支撑"
    else:
        state_text, role = "未接受压力线", "压力"
    return {
        "acceptance_state": state_text,
        "boundary_role": role,
        "false_breakout_count": int(false_break),
        "failed_retest_count": int(failed_retest),
        "accepted_segments": int(accepted_segments),
        "support_resistance_flip_count": int(below_segments),
        "recent_boundary_state": recent_state,
    }


def 补充核心线精修(k: pd.DataFrame, items: List[Dict[str, Any]], sources: Optional[Dict[float, str]], line_tol: float) -> List[Dict[str, Any]]:
    """V16：只对已入围核心线做重计算，避免全市场逐线分钟级卡顿。"""
    if not items:
        return items
    source_map = {四舍五入(p, 3): s for p, s in (sources or {}).items()}
    out: List[Dict[str, Any]] = []
    for item in items:
        z = dict(item)
        L = 四舍五入(z.get("line"), 3)
        if L <= 0:
            out.append(z)
            continue
        src = source_map.get(L, str(z.get("source", "")))
        if 启用最低边界敏感性测试:
            try:
                z.update(核心线敏感性测试(k, L, src, line_tol))
            except Exception as exc:
                z["sensitivity_summary"] = f"敏感性测试失败：{exc}"
        try:
            seq = 时间序列边界状态(k, L, line_tol)
        except Exception as exc:
            seq = {"acceptance_state": f"时间序列状态失败：{exc}", "boundary_role": "未知", "false_breakout_count": 0, "failed_retest_count": 0, "accepted_segments": 0, "support_resistance_flip_count": 0}
        z.update({
            "false_breakout_count": seq.get("false_breakout_count", 0),
            "failed_retest_count": seq.get("failed_retest_count", 0),
            "accepted_segments": seq.get("accepted_segments", 0),
            "support_resistance_flip_count": seq.get("support_resistance_flip_count", 0),
            "current_state": seq.get("acceptance_state", 细分边界状态(z)),
            "acceptance_state": seq.get("acceptance_state", 细分边界状态(z)),
            "boundary_role": seq.get("boundary_role", z.get("boundary_role", "")),
        })
        z["acceptance_strength_score"] = 四舍五入(夹紧(
            安全浮点(z.get("entity_accept_count")) * 0.35 + 安全浮点(z.get("volume_entity_accept_count")) * 1.10
            + 安全浮点(z.get("accepted_segments")) * 1.20 - 安全浮点(z.get("support_resistance_flip_count")) * 1.50,
            0.0, 12.0), 3)
        z["boundary_quality_score"] = 四舍五入(
            安全浮点(z.get("net_score")) + 安全浮点(z.get("sensitivity_bonus")) + 安全浮点(z.get("vbp_support_score")), 3)
        try:
            conf, conf_score = 界置信度(z)
            z["boundary_confidence"] = conf
            z["boundary_confidence_score"] = conf_score
        except Exception:
            pass
        out.append(z)
    return out


def 生成回测标签(df: pd.DataFrame, bidx: int, line_info: Dict[str, Any]) -> Dict[str, Any]:
    """输出未来表现标签。没有未来样本时只标记样本不足，不伪造胜率。"""
    if not 启用回测标签输出:
        return {}
    d = 加基础指标(df)
    if d.empty or bidx < 0 or bidx >= len(d):
        return {"label_available": False, "label_reason": "样本不足"}
    base_close = 安全浮点(d.iloc[bidx].get("close"))
    line_info = 规范化融合界字段(line_info)
    confirm_line = 安全浮点(line_info.get("effective_confirm_line"), 安全浮点(line_info.get("line")))
    out: Dict[str, Any] = {"label_available": bool(len(d) - 1 - bidx >= 3), "label_days_available": int(max(0, len(d) - 1 - bidx))}
    for n in [1, 3, 5, 10, 20]:
        end = min(len(d) - 1, bidx + n)
        win = d.iloc[bidx + 1:end + 1].copy() if end > bidx else pd.DataFrame()
        if win.empty or base_close <= 0:
            out[f"label_{n}d_max_gain_pct"] = 0.0
            out[f"label_{n}d_max_drawdown_pct"] = 0.0
            out[f"label_{n}d_close_pct"] = 0.0
            continue
        out[f"label_{n}d_max_gain_pct"] = 四舍五入(涨幅百分比(安全浮点(win["high"].max()), base_close), 2)
        out[f"label_{n}d_max_drawdown_pct"] = 四舍五入(涨幅百分比(安全浮点(win["low"].min()), base_close), 2)
        out[f"label_{n}d_close_pct"] = 四舍五入(涨幅百分比(安全浮点(win["close"].iloc[-1]), base_close), 2)
    future = d.iloc[bidx + 1:].copy()
    if not future.empty and confirm_line > 0:
        out["label_fell_back_inside_boundary"] = bool((future["close"] < confirm_line * (1.0 - 边界上沿接受容忍)).any())
        out["label_acceptance_success_5d"] = bool(len(future) >= 5 and (future.head(5)["close"] >= confirm_line * (1.0 - 边界上沿接受容忍)).all())
        out["label_false_breakout_10d"] = bool(len(future) >= 3 and (future.head(min(10, len(future)))["close"] < confirm_line * 0.985).any())
    else:
        out["label_fell_back_inside_boundary"] = False
        out["label_acceptance_success_5d"] = False
        out["label_false_breakout_10d"] = False
    return out


def 猴子代码自检() -> Dict[str, Any]:
    """深扫猴子代码：重复函数、不可达旧逻辑、只输出不参与、废参数残留。"""
    try:
        src = Path(__file__).read_text(encoding="utf-8")
        tree = ast.parse(src)
    except Exception as exc:
        return {"passed": False, "detail": f"无法读取/解析源码：{exc}"}

    defs = [n.name for n in tree.body if isinstance(n, ast.FunctionDef)]
    dup_defs = sorted({x for x in defs if defs.count(x) > 1})
    suspicious = []
    scan_src = re.sub(r"def 猴子代码自检\(\).*?(?=\ndef |\n# ----------|\nif __name__|$)", "", src, flags=re.S)
    for token in ["TODO", "FIXME", "猴子补丁", "NotImplemented", "return out\n\n\n    out:", "# ---------- 主程序 ----------\n# ---------- 主程序 ----------"]:
        if token in scan_src:
            suspicious.append(token)

    unreachable_blocks = []
    for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
        body = fn.body
        for i, stmt in enumerate(body[:-1]):
            if isinstance(stmt, (ast.Return, ast.Raise)):
                nxt = body[i + 1]
                # 允许函数末尾返回，不允许同级 return 后还有大段旧逻辑。
                unreachable_blocks.append({"function": fn.name, "return_line": stmt.lineno, "next_line": nxt.lineno})
                break

    critical_symbols = [
        "启用带量共振分层", "计算VBP筹码带", "核心线敏感性测试", "生成回测标签",
        "有效突破确认线", "support_is_trade_defense", "post_swing_pressure_blocks",
        "时间序列边界状态", "压力带正式最大宽度", "VBP确认线最大抬升",
    ]
    unused = [sym for sym in critical_symbols if src.count(sym) < 2]
    report_only = []
    for sym in ["VBP支持分", "带量质量分", "界宽%", "有效突破确认线"]:
        count = src.count(sym)
        if count <= 2:
            report_only.append(sym)

    boundary_invariant_errors = []
    try:
        inv = 生成界状态({"line": 10.0, "boundary_band_high": 9.0, "multi_tf_boundary_high": 8.0, "effective_confirm_line": 7.0}, {"突破收盘": 10.2}, 10.2)
        if 安全浮点(inv.get("有效突破确认线")) < 10.0 or 安全浮点(inv.get("融合界上沿")) < 10.0:
            boundary_invariant_errors.append("确认线/融合界上沿低于核心线")
    except Exception as exc:
        boundary_invariant_errors.append(f"融合界不变量自检异常:{exc}")

    passed = not dup_defs and not suspicious and not unreachable_blocks and not unused and not report_only and not boundary_invariant_errors
    return {
        "passed": bool(passed),
        "duplicate_functions": dup_defs,
        "suspicious_tokens": suspicious,
        "unreachable_after_return": unreachable_blocks,
        "possibly_unused_critical_symbols": unused,
        "possibly_report_only_symbols": report_only,
        "boundary_invariant_errors": boundary_invariant_errors,
        "function_count": len(defs),
        "source_lines": src.count("\n") + 1,
    }


def 界置信度(item: Dict[str, Any]) -> Tuple[str, float]:
    """结构置信度，不伪装成胜率。

    V10.3：实体接受不再机械扣分，按当前状态处理：
    - 压力转支撑接受：状态改善，小幅加分；
    - 实体接受后跌回/反压/跌破：扣分；
    - 普通实体接受：只做轻提示，不显著扣分。
    """
    hit = 安全浮点(item.get("effective_resonance_count"))
    vol_score = 安全浮点(item.get("volume_quality_score"), 安全浮点(item.get("volume_resonance_count")) * 0.6)
    cut = 安全浮点(item.get("entity_cut_count"))
    vcut = 安全浮点(item.get("volume_entity_cut_count"))
    net = 安全浮点(item.get("net_score"))
    width = 安全浮点(item.get("boundary_band_width_pct"))
    accept = 安全浮点(item.get("entity_accept_count"))
    accept_strength = 安全浮点(item.get("acceptance_strength_score"))
    density = 安全浮点(item.get("price_cluster_density"))
    sens = 安全浮点(item.get("sensitivity_bonus"))
    vbp = 安全浮点(item.get("vbp_support_score"))
    state = str(item.get("acceptance_state", item.get("current_state", "")))
    tf = str(item.get("line_timeframe", ""))
    tf_bonus = 0.0
    if "年" in tf:
        tf_bonus = 9.0
    elif "季" in tf:
        tf_bonus = 7.0
    elif "月" in tf:
        tf_bonus = 5.0
    elif "周" in tf:
        tf_bonus = 2.5

    state_penalty = 0.0
    state_bonus = 0.0
    if "假突破" in state:
        state_penalty += 5.0
    if "反抽失败" in state:
        state_penalty += 4.0

    accept_penalty = 0.0
    if accept > 0:
        if ("压力转支撑" in state and "接受" in state) or "支撑接受" in state:
            state_bonus += min(实体接受状态加分上限, max(accept * 0.35, accept_strength * 0.35))
        elif "跌回" in state or "反压" in state or "跌破" in state:
            accept_penalty += min(5.0, accept * 1.0)
        else:
            accept_penalty += min(1.2, max(0.0, accept - hit) * 0.35)

    raw = (
        hit * 7.5 + vol_score * 6.0 + max(0.0, net) * 1.35 + tf_bonus
        + density * 0.8 + sens * 3.0 + vbp * 0.8 + state_bonus
        - cut * 4.0 - vcut * 8.0 - max(0.0, width - 2.5) * 2.0
        - accept_penalty - state_penalty
    )
    if width >= 界过宽无确认降级阈值 and vbp <= 0 and 安全浮点(item.get("multi_tf_confluence_count", 1)) <= 1:
        raw -= 10.0
    score = 夹紧(raw, 0.0, 100.0)
    level = "S" if score >= 82 else "A" if score >= 68 else "B" if score >= 52 else "C" if score >= 36 else "D"
    return level, 四舍五入(score, 2)


def 规范化融合界字段(line_info: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """统一核心线、单周期上沿、融合上沿、硬确认线和交易确认线。

    V13原则：
    - core_confirm_line：主核心线，第一层触发；
    - single_confirm_line：单周期边界上沿；
    - fusion_confirm_line：多周期融合上沿，强确认；
    - hard_confirm_line：配置层硬确认线，受“正式使用融合界上沿”控制；
    - trade_confirm_line/effective_confirm_line：本次交易实际确认线，可用于主核心线早期回踩确认，但必须显式标明层级。
    """
    info = dict(line_info or {})
    L = 安全浮点(info.get("line"))
    if L <= 0:
        return info

    anomalies: List[str] = []
    raw_single_low = 安全浮点(info.get("boundary_band_low"), L) or L
    raw_single_high = 安全浮点(info.get("boundary_band_high"), L) or L
    has_multi_high = "multi_tf_boundary_high" in info and 安全浮点(info.get("multi_tf_boundary_high"), 0.0) > 0
    raw_multi_low = 安全浮点(info.get("multi_tf_boundary_low"), raw_single_low) or raw_single_low
    raw_multi_high = 安全浮点(info.get("multi_tf_boundary_high"), 0.0) if has_multi_high else 0.0
    raw_trade_confirm = 安全浮点(info.get("trade_confirm_line"), 0.0)
    raw_effective = 安全浮点(info.get("effective_confirm_line"), 0.0)
    raw_vbp_confirm = 安全浮点(info.get("vbp_confirm_line"), 0.0)

    single_low = min(x for x in [raw_single_low, L] if x > 0)
    single_high = max(L, raw_single_high)
    if raw_single_high > 0 and raw_single_high < L:
        anomalies.append("单周期界上沿低于核心线，已修正")

    multi_low_candidates = [x for x in [raw_multi_low, single_low, L] if x > 0]
    multi_low = min(multi_low_candidates) if multi_low_candidates else single_low
    # V14修正：多周期稳健融合上沿一旦存在，就不能被单周期异常上沿重新抬高；
    # 单周期上沿只进入 single_confirm_line / hard_confirm_line，不污染 fusion_confirm_line。
    fusion_high = max(L, raw_multi_high) if has_multi_high else max(L, single_high)
    if raw_multi_high > 0 and raw_multi_high < L:
        anomalies.append("融合界上沿低于核心线，已修正")

    # 硬确认线来源必须可解释：主核心线、单周期上沿、融合上沿、VBP不能混名。
    hard_candidates: List[Tuple[float, str]] = [(L, "主核心线确认"), (single_high, "单周期上沿确认")]
    if 正式使用融合界上沿:
        hard_candidates.append((fusion_high, "融合上沿确认"))
    if VBP参与硬确认 and raw_vbp_confirm > 0:
        hard_candidates.append((raw_vbp_confirm, "VBP硬确认"))
    hard_confirm_line, hard_layer = max([(x, name) for x, name in hard_candidates if x > 0], key=lambda t: t[0])

    # 交易确认线是本次事件实际执行口径。它可以等于主核心线，但不能低于主核心线，也不能高于硬确认线。
    if raw_trade_confirm > 0:
        effective = max(L, min(raw_trade_confirm, hard_confirm_line))
    elif raw_effective > 0 and raw_effective <= hard_confirm_line:
        effective = max(L, raw_effective)
    else:
        effective = hard_confirm_line

    if effective <= L * 1.001 and hard_confirm_line > L * 1.001:
        layer = "主核心线早期交易确认"
    elif abs(effective - fusion_high) / max(fusion_high, 1e-9) <= 0.001 and fusion_high > L * 1.001:
        layer = "融合上沿确认"
    elif abs(effective - single_high) / max(single_high, 1e-9) <= 0.001 and single_high > L * 1.001:
        layer = "单周期上沿确认"
    elif effective > L * 1.001:
        layer = "分层交易确认"
    else:
        layer = "主核心线确认"

    if raw_effective > 0 and raw_effective < L:
        anomalies.append("有效突破确认线低于核心线，已修正")
    if raw_vbp_confirm > 0 and not VBP参与硬确认:
        anomalies.append("VBP仅作参考，未抬高硬确认线")
    if not 正式使用融合界上沿 and fusion_high > hard_confirm_line * 1.001:
        anomalies.append("融合界上沿保留为升级线，但未参与硬确认")

    if multi_low > max(hard_confirm_line, fusion_high):
        anomalies.append("融合界下沿高于确认线，已回退到核心线")
        multi_low = min(L, single_low)

    info["boundary_band_low"] = 四舍五入(single_low, 3)
    info["boundary_band_high"] = 四舍五入(single_high, 3)
    info["multi_tf_boundary_low"] = 四舍五入(multi_low, 3)
    info["multi_tf_boundary_high"] = 四舍五入(fusion_high, 3)
    info["core_confirm_line"] = 四舍五入(L, 3)
    info["single_confirm_line"] = 四舍五入(single_high, 3)
    info["fusion_confirm_line"] = 四舍五入(fusion_high, 3)
    info["hard_confirm_line"] = 四舍五入(hard_confirm_line, 3)
    info["trade_confirm_line"] = 四舍五入(effective, 3)
    info["effective_confirm_line"] = 四舍五入(effective, 3)
    info["actual_break_line"] = 四舍五入(安全浮点(info.get("actual_break_line"), effective), 3)
    info["effective_confirm_layer"] = layer
    info["hard_confirm_layer"] = hard_layer
    info["fusion_upgrade_line"] = 四舍五入(fusion_high, 3)
    if raw_vbp_confirm > 0:
        info["vbp_reference_line"] = 四舍五入(raw_vbp_confirm, 3)
    if anomalies:
        old = str(info.get("boundary_normalization", "") or "")
        joined = "；".join(anomalies)
        info["boundary_normalization"] = joined if not old else old + "；" + joined
    return info


def 生成界状态(line_info: Dict[str, Any], br: Dict[str, Any], latest_close: float) -> Dict[str, Any]:
    """输出分层界状态：主核心线、交易确认线、硬确认线、融合上沿分别判断。"""
    line_info = 规范化融合界字段(line_info)
    L = 安全浮点(line_info.get("core_confirm_line"), 安全浮点(line_info.get("line")))
    single_low = 安全浮点(line_info.get("boundary_band_low"), L)
    single_high = 安全浮点(line_info.get("single_confirm_line"), 安全浮点(line_info.get("boundary_band_high"), L))
    multi_low = 安全浮点(line_info.get("multi_tf_boundary_low"), single_low or L)
    fusion_upper = 安全浮点(line_info.get("fusion_confirm_line"), 安全浮点(line_info.get("multi_tf_boundary_high"), single_high or L))
    hard_confirm = 安全浮点(line_info.get("hard_confirm_line"), single_high or L)
    trade_confirm = 安全浮点(line_info.get("trade_confirm_line"), 安全浮点(line_info.get("effective_confirm_line"), hard_confirm or L))
    vals_low = [x for x in [L, single_low, multi_low] if 安全浮点(x) > 0]
    lower = min(vals_low) if vals_low else L
    single_upper = max([x for x in [L, single_high] if 安全浮点(x) > 0], default=L)
    fusion_upper = max([x for x in [single_upper, fusion_upper] if 安全浮点(x) > 0], default=single_upper)
    trade_upper = max(L, trade_confirm) if trade_confirm > 0 else L
    hard_upper = max(L, hard_confirm)
    fusion_width_pct = (fusion_upper / lower - 1.0) * 100.0 if lower > 0 else 0.0
    trade_width_pct = (trade_upper / lower - 1.0) * 100.0 if lower > 0 else 0.0
    width_pct = (hard_upper / lower - 1.0) * 100.0 if lower > 0 else 0.0
    break_close = 安全浮点(br.get("突破收盘"), latest_close)

    core_confirm = bool(L > 0 and break_close >= L * (1.0 + 边界上沿突破容忍))
    single_confirm = bool(single_upper > 0 and break_close >= single_upper * (1.0 + 边界上沿突破容忍))
    fusion_confirm = bool(fusion_upper > 0 and break_close >= fusion_upper * (1.0 + 边界上沿突破容忍))
    hard_line_confirm = bool(hard_upper > 0 and break_close >= hard_upper * (1.0 + 边界上沿突破容忍))
    trade_confirmed = bool(trade_upper > 0 and break_close >= trade_upper * (1.0 + 边界上沿突破容忍))

    core_accept = bool(L > 0 and latest_close >= L * (1.0 - 边界上沿接受容忍))
    single_accept = bool(single_upper > 0 and latest_close >= single_upper * (1.0 - 边界上沿接受容忍))
    fusion_accept = bool(fusion_upper > 0 and latest_close >= fusion_upper * (1.0 - 边界上沿接受容忍))
    hard_accept = bool(hard_upper > 0 and latest_close >= hard_upper * (1.0 - 边界上沿接受容忍))
    trade_accept = bool(trade_upper > 0 and latest_close >= trade_upper * (1.0 - 边界上沿接受容忍))
    fell_back_inside_trade = bool(trade_confirmed and not trade_accept)
    fell_back_inside_fusion = bool(fusion_confirm and not fusion_accept)

    if fusion_confirm and fusion_accept:
        state = "融合界上沿已突破并接受"
    elif fusion_confirm and not fusion_accept:
        state = "突破融合界上沿后回落观察"
    elif hard_line_confirm and hard_accept:
        state = "硬确认线已突破并接受"
    elif single_confirm and single_accept:
        state = "单周期上沿突破并接受/等待融合上沿升级"
    elif core_confirm and core_accept:
        state = "主核心线第一层突破并接受/融合上沿待升级"
    elif core_confirm and not core_accept:
        state = "主核心线突破后回落观察"
    elif latest_close < lower * 0.992:
        state = "跌回主界下方"
    else:
        state = "贴近主界观察"

    return {
        "界下沿": 四舍五入(lower, 3), "界上沿": 四舍五入(hard_upper, 3), "界宽%": 四舍五入(width_pct, 2),
        "交易界宽%": 四舍五入(trade_width_pct, 2), "融合界宽%": 四舍五入(fusion_width_pct, 2),
        "单周期界上沿": 四舍五入(single_upper, 3), "融合界下沿": 四舍五入(multi_low, 3), "融合界上沿": 四舍五入(fusion_upper, 3),
        "核心确认线": 四舍五入(L, 3), "交易确认线": 四舍五入(trade_upper, 3), "硬确认线": 四舍五入(hard_upper, 3),
        "有效突破确认线": 四舍五入(trade_upper, 3), "交易确认层级": line_info.get("effective_confirm_layer", ""),
        "是否突破主核心线": core_confirm, "是否最新接受主核心线": core_accept,
        "是否突破单周期上沿": single_confirm, "是否最新接受单周期上沿": single_accept,
        "是否突破交易确认线": trade_confirmed, "是否最新接受交易确认线": trade_accept,
        "是否突破硬确认线": hard_line_confirm, "是否最新接受硬确认线": hard_accept,
        "是否突破融合界上沿": fusion_confirm, "是否最新接受融合界上沿": fusion_accept,
        # 兼容旧字段：边界上沿=交易确认线，不再伪装成融合上沿。
        "是否突破边界上沿": trade_confirmed,
        "是否最新接受有效突破确认线": trade_accept,
        "是否跌回有效确认线下": fell_back_inside_trade,
        "是否跌回融合界内部": fell_back_inside_fusion,
        "当前界状态": state, "界过宽": bool(width_pct >= 压力带正式最大宽度), "界极宽硬拒": bool(width_pct >= 压力带极宽硬拒宽度),
        "界宽质量": "过宽硬拒" if width_pct >= 压力带极宽硬拒宽度 else "偏宽需吸收" if width_pct >= 压力带正式最大宽度 else "略宽" if width_pct >= 压力带理想最大宽度 else "紧凑",
    }


def 核心线候选来源(k: pd.DataFrame) -> Dict[float, str]:
    sources: Dict[float, set] = {}

    def add(price: Any, source: str) -> None:
        p = 安全浮点(price)
        if p > 0:
            sources.setdefault(四舍五入(p, 3), set()).add(source)

    for _, r in k.iterrows():
        add(r.get("high"), "最高价")
        add(r.get("body_top"), "实体顶")
        add(r.get("close"), "收盘价")

    vol_med = 安全浮点(k["volume"].median()) if "volume" in k.columns else 0.0
    for _, r in k.iterrows():
        open_ = 安全浮点(r.get("open"))
        close = 安全浮点(r.get("close"))
        high = 安全浮点(r.get("high"))
        volume = 安全浮点(r.get("volume"))
        body_ratio = 安全浮点(r.get("body_ratio"))
        if close > open_ and volume >= vol_med * 1.30 > 0:
            add(high, "带量阳K高点")
            if body_ratio >= 0.30:
                add(max(open_, close), "带量阳K实体顶")
    return {p: "+".join(sorted(v)) for p, v in sources.items()}






def 动态价格带分组(items: List[Any], price_getter, band_tol: float) -> List[List[Any]]:
    """按动态中位数分组，避免固定第一根base造成链式断裂。"""
    xs = sorted([x for x in items if 安全浮点(price_getter(x)) > 0], key=lambda z: 安全浮点(price_getter(z)))
    groups: List[List[Any]] = []
    cur: List[Any] = []
    for item in xs:
        price = 安全浮点(price_getter(item))
        if not cur:
            cur = [item]
            continue
        center = float(np.median([安全浮点(price_getter(z)) for z in cur]))
        last_price = 安全浮点(price_getter(cur[-1]))
        # 同时参考动态中位数与相邻价格，连续压力带不会被首个base硬切断。
        if center > 0 and (abs(price - center) / center <= band_tol or (last_price > 0 and abs(price - last_price) / last_price <= band_tol * 0.72)):
            cur.append(item)
        else:
            groups.append(cur)
            cur = [item]
    if cur:
        groups.append(cur)
    return groups


def 周期级别权重(tf: str) -> float:
    s = str(tf)
    if "年" in s:
        return 4.0
    if "季" in s:
        return 3.3
    if "月" in s:
        return 2.6
    if "周" in s:
        return 2.0
    if "日" in s:
        return 1.2
    return 1.0


def 周期预估样本数(tf: str) -> float:
    s = str(tf)
    if "年" in s:
        return 35.0
    if "季" in s:
        return 80.0
    if "月" in s:
        return float(max(36, 历史月线窗口))
    if "周" in s:
        return 180.0
    if "日" in s:
        return float(max(120, 近端日线窗口))
    return 120.0


def 共振归一强度(item: Dict[str, Any]) -> float:
    """不同周期原始hit不可直接比较；用样本规模平方根归一，再叠加大周期方向权重。"""
    hit = 安全浮点(item.get("effective_resonance_count"))
    tf = str(item.get("line_timeframe", ""))
    base = math.sqrt(max(1.0, 周期预估样本数(tf)))
    return 四舍五入((hit / base) * 周期级别权重(tf), 4)


def 切实体污染等级(item: Dict[str, Any]) -> Tuple[bool, str, float]:
    hit = max(1.0, 安全浮点(item.get("effective_resonance_count")))
    cut = 安全浮点(item.get("entity_cut_count"))
    vcut = 安全浮点(item.get("volume_entity_cut_count"))
    ratio = cut / hit
    hard = bool(ratio >= 核心线切实体硬降级比例 or vcut >= 带量切实体硬降级阈值)
    if hard:
        reason = f"切实体污染偏重：切实体{cut:.0f}/共振{hit:.0f}，带量切实体{vcut:.0f}"
    elif ratio >= 0.45 or vcut >= 1:
        reason = f"切实体需降权：切实体{cut:.0f}/共振{hit:.0f}，带量切实体{vcut:.0f}"
    else:
        reason = "切实体可接受"
    return hard, reason, 四舍五入(ratio, 3)


def 稳健融合边界(g: List[Dict[str, Any]]) -> Tuple[float, float, str]:
    """融合上沿不再直接max，避免一个异常宽上沿污染整个硬闸/宽度。"""
    lows = [安全浮点(x.get("boundary_band_low", x.get("line"))) for x in g if 安全浮点(x.get("boundary_band_low", x.get("line"))) > 0]
    highs = [安全浮点(x.get("boundary_band_high", x.get("line"))) for x in g if 安全浮点(x.get("boundary_band_high", x.get("line"))) > 0]
    lines = [安全浮点(x.get("line")) for x in g if 安全浮点(x.get("line")) > 0]
    if not lows or not highs:
        L = 安全浮点(g[0].get("line")) if g else 0.0
        return L, L, "样本不足，回退核心线"
    low = min(lows)
    if len(highs) <= 2:
        return low, max(highs), "候选少，保留最高上沿"
    q = float(np.quantile(highs, min(max(融合上沿稳健分位, 0.60), 0.90)))
    median_line = float(np.median(lines)) if lines else q
    # 带量/高共振候选的上沿优先，但剔除相对中位数过度偏离的异常上沿。
    ranked = sorted(g, key=核心线字典序排序键, reverse=True) if '核心线字典序排序键' in globals() else g
    preferred_highs = []
    for x in ranked[:max(2, min(5, len(ranked)) )]:
        h = 安全浮点(x.get("boundary_band_high", x.get("line")))
        if h > 0 and (median_line <= 0 or h <= median_line * (1.0 + 异常上沿最大偏离)):
            preferred_highs.append(h)
    robust = max(preferred_highs + [q]) if preferred_highs else q
    robust = max(robust, max(lines) if lines else robust)
    raw_max = max(highs)
    note = "稳健P80/优质上沿"
    if raw_max > robust * (1.0 + 0.006):
        note += f"；剔除异常上沿{raw_max:.2f}"
    return low, robust, note

def 压缩候选来源(sources: Dict[float, str], band_tol: float = 核心线带宽, max_count: int = 260, keep_per_group: int = 4) -> Dict[float, str]:
    """候选线压缩只做提速，不允许提前替代核心线评分。

    历史问题每个价格带只保留1条代表线，容易在真实共振评估前把最有效的线扔掉。
    新版每个价格带至少保留多个候选：带量/实体来源较强者、价格带上下沿、靠近中位者。
    后续仍由批量评估核心线按真实共振/带量共振/切实体扣分决定最终排名。
    """
    items = sorted((安全浮点(p), str(src)) for p, src in sources.items() if 安全浮点(p) > 0)
    if not items:
        return {}

    groups: List[List[Tuple[float, str]]] = 动态价格带分组(items, lambda x: x[0], band_tol)

    def seed_weight(src: str) -> float:
        parts = [x for x in src.split("+") if x]
        w = float(len(parts))
        if "带量" in src:
            w += 1.2
        if "实体顶" in src:
            w += 0.4
        if "最高价" in src:
            w += 0.2
        return w

    picked: Dict[float, str] = {}
    ranked_groups: List[Tuple[float, List[Tuple[float, str]]]] = []
    for g in groups:
        prices = [x[0] for x in g]
        mid = float(np.median(prices)) if prices else 0.0
        ranked_inside = sorted(g, key=lambda x: (seed_weight(x[1]), -abs(x[0] - mid), -x[0]), reverse=True)
        # 保留：来源质量靠前、价格带下沿/上沿、最靠近中位的线。
        keep: List[Tuple[float, str]] = ranked_inside[:max(1, keep_per_group)]
        keep.append(min(g, key=lambda x: x[0]))
        keep.append(max(g, key=lambda x: x[0]))
        keep.append(min(g, key=lambda x: abs(x[0] - mid)))
        merged: Dict[float, set] = {}
        for price, src in keep:
            merged.setdefault(四舍五入(price, 3), set()).update([s for s in src.split("+") if s])
        group_weight = len(g) + max(seed_weight(src) for _, src in g)
        ranked_groups.append((group_weight, [(price, "+".join(sorted(srcs))) for price, srcs in merged.items()]))

    # 优先保留候选密度高的价格带，但不是每带只留一条。
    for _, keep in sorted(ranked_groups, key=lambda x: x[0], reverse=True):
        for price, src in keep:
            picked[四舍五入(price, 3)] = src
            if len(picked) >= max_count:
                return picked
    return picked



def 评估单条核心线(k: pd.DataFrame, line: float, source: str, line_tol: Optional[float] = None) -> Dict[str, Any]:
    L = 安全浮点(line)
    tol = 安全浮点(line_tol, 核心线容差)
    if k.empty or L <= 0:
        return {}
    hit = high_touch = upper_hit = body_top_hit = close_hit = 0
    cut = accept = volume_accept = volume_cut = 0
    volume_hit = stall_touch = mild_vol = healthy_vol = standard_vol = high_quality_vol = 0
    volume_quality_score = 0.0
    vol_med = 安全浮点(k["volume"].median()) if "volume" in k.columns else 0.0

    for _, r in k.iterrows():
        open_ = 安全浮点(r.get("open")); high = 安全浮点(r.get("high")); low = 安全浮点(r.get("low")); close = 安全浮点(r.get("close"))
        body_top = 安全浮点(r.get("body_top")); body_bottom = 安全浮点(r.get("body_bottom")); volume = 安全浮点(r.get("volume"))
        if high <= 0 or body_top <= 0 or body_bottom <= 0:
            continue
        entity_accept = body_bottom > L
        near_body_top_edge = body_bottom < L <= body_top and abs(body_top / L - 1.0) <= 实体顶贴线不算切实体容忍
        entity_cut = body_bottom < L < body_top and not near_body_top_edge
        normal_zone = not entity_accept and not entity_cut
        is_high = normal_zone and abs(high / L - 1.0) <= tol
        is_upper = normal_zone and body_top <= L <= high
        is_body_top = normal_zone and abs(body_top / L - 1.0) <= max(tol, 实体顶贴线不算切实体容忍)
        is_close = normal_zone and abs(close / L - 1.0) <= tol
        touch = is_high or is_upper or is_body_top or is_close
        volq = 量能触线质量(open_, high, low, close, volume, vol_med) if 启用带量共振分层 else {"type": "带量共振" if vol_med > 0 and volume >= vol_med * 1.30 else "普通共振", "bonus": 0.60 if vol_med > 0 and volume >= vol_med * 1.30 else 0.0, "is_volume": vol_med > 0 and volume >= vol_med * 1.30, "is_stall": False}
        if touch:
            hit += 1
            high_touch += int(is_high); upper_hit += int(is_upper and not is_body_top); body_top_hit += int(is_body_top); close_hit += int(is_close)
            if bool(volq.get("is_volume")):
                volume_hit += 1
                volume_quality_score += 安全浮点(volq.get("bonus"))
                t = str(volq.get("type"))
                stall_touch += int("滞涨" in t); mild_vol += int("温和" in t or "普通带量" in t)
                healthy_vol += int("健康" in t); standard_vol += int("标准" in t); high_quality_vol += int("高质量" in t)
        if entity_cut:
            cut += 1
            volume_cut += int(bool(volq.get("is_volume")))
        if entity_accept:
            accept += 1
            volume_accept += int(bool(volq.get("is_volume")))
    net = hit + volume_quality_score - cut * 0.55 - volume_cut * 1.25
    cut_ratio = cut / max(1, hit)
    heavy_cut = bool(cut_ratio >= 核心线切实体硬降级比例 or volume_cut >= 带量切实体硬降级阈值)
    level = "核心线候选" if hit >= 最少共振点 and net > 0 and not heavy_cut else "切实体污染降级" if heavy_cut and hit >= 最少共振点 else "未成线"
    seq = 时间序列边界状态(k, L, tol)
    return {
        "line": 四舍五入(L, 3), "source": source, "score": 四舍五入(hit, 3), "net_score": 四舍五入(net, 3),
        "effective_resonance_count": int(hit), "volume_resonance_count": int(volume_hit), "volume_quality_score": 四舍五入(volume_quality_score, 3), "cut_entity_ratio": 四舍五入(cut_ratio, 3), "heavy_entity_cut_downrank": bool(heavy_cut),
        "mild_volume_touch_count": int(mild_vol), "healthy_volume_touch_count": int(healthy_vol), "standard_double_volume_touch_count": int(standard_vol), "high_quality_double_volume_touch_count": int(high_quality_vol), "stall_volume_touch_count": int(stall_touch),
        "high_touch_count": int(high_touch), "upper_shadow_hit_count": int(upper_hit), "body_top_touch_count": int(body_top_hit), "close_touch_count": int(close_hit),
        "entity_cut_count": int(cut), "volume_entity_cut_count": int(volume_cut), "entity_accept_count": int(accept), "volume_entity_accept_count": int(volume_accept),
        "false_breakout_count": seq.get("false_breakout_count", 0), "failed_retest_count": seq.get("failed_retest_count", 0),
        "level": level, "current_state": seq.get("acceptance_state", ""), "acceptance_state": seq.get("acceptance_state", ""), "boundary_role": seq.get("boundary_role", ""),
        "accepted_segments": seq.get("accepted_segments", 0), "support_resistance_flip_count": seq.get("support_resistance_flip_count", 0),
        "boundary_quality_score": 四舍五入(net, 3),
    }



def 批量评估核心线(k: pd.DataFrame, sources: Dict[float, str], line_tol: Optional[float] = None) -> List[Dict[str, Any]]:
    """批量评估候选核心线。保留向量化主体，不保留return后的旧逻辑。"""
    tol = 安全浮点(line_tol, 核心线容差)
    if k.empty or not sources:
        return []
    open_arr = pd.to_numeric(k.get("open"), errors="coerce").fillna(0.0).to_numpy(dtype=float)
    high_arr = pd.to_numeric(k.get("high"), errors="coerce").fillna(0.0).to_numpy(dtype=float)
    low_arr = pd.to_numeric(k.get("low"), errors="coerce").fillna(0.0).to_numpy(dtype=float)
    close_arr = pd.to_numeric(k.get("close"), errors="coerce").fillna(0.0).to_numpy(dtype=float)
    body_top_arr = pd.to_numeric(k.get("body_top"), errors="coerce").fillna(0.0).to_numpy(dtype=float)
    body_bottom_arr = pd.to_numeric(k.get("body_bottom"), errors="coerce").fillna(0.0).to_numpy(dtype=float)
    volume_arr = pd.to_numeric(k.get("volume"), errors="coerce").fillna(0.0).to_numpy(dtype=float)
    vol_med = float(np.nanmedian(volume_arr)) if len(volume_arr) else 0.0
    valid = (high_arr > 0) & (body_top_arr > 0) & (body_bottom_arr > 0)
    rng = np.maximum(high_arr - low_arr, 1e-9)
    close_pos = (close_arr - low_arr) / rng
    upper = (high_arr - body_top_arr) / rng
    body_ratio = np.abs(close_arr - open_arr) / rng
    bullish = close_arr > open_arr
    vol_ratio = volume_arr / max(vol_med, 1e-9)
    vol_bonus = np.zeros_like(volume_arr, dtype=float)
    vol_type_code = np.zeros_like(volume_arr, dtype=int)
    if vol_med > 0:
        volume_base = vol_ratio >= 1.30
        stall = volume_base & ((~bullish) | (close_pos < 0.55) | (upper >= 0.45) | ((vol_ratio >= 2.8) & (body_ratio < 0.22)))
        high_quality = volume_base & bullish & (vol_ratio > 2.50) & (vol_ratio <= 4.50) & (body_ratio >= 0.45) & (close_pos >= 0.70) & (upper <= 0.28) & (~stall)
        standard = volume_base & bullish & (vol_ratio >= 1.80) & (vol_ratio <= 2.50) & (body_ratio >= 0.30) & (close_pos >= 0.62) & (upper <= 0.35) & (~stall) & (~high_quality)
        healthy = volume_base & bullish & (vol_ratio >= 1.50) & (close_pos >= 0.62) & (upper <= 0.38) & (~stall) & (~standard) & (~high_quality)
        mild = volume_base & bullish & (~stall) & (~healthy) & (~standard) & (~high_quality)
        other_vol = volume_base & (~stall) & (~mild) & (~healthy) & (~standard) & (~high_quality)
        vol_bonus[stall] = -0.60; vol_type_code[stall] = -1
        vol_bonus[high_quality] = 1.10; vol_type_code[high_quality] = 4
        vol_bonus[standard] = 0.90; vol_type_code[standard] = 3
        vol_bonus[healthy] = 0.60; vol_type_code[healthy] = 2
        vol_bonus[mild] = 0.40; vol_type_code[mild] = 1
        vol_bonus[other_vol] = 0.15; vol_type_code[other_vol] = 1
    vol_event = vol_type_code != 0
    out: List[Dict[str, Any]] = []
    for line, source in sources.items():
        L = 安全浮点(line)
        if L <= 0:
            continue
        entity_accept = valid & (body_bottom_arr > L)
        near_body_top_edge = valid & (body_bottom_arr < L) & (L <= body_top_arr) & (np.abs(body_top_arr / L - 1.0) <= 实体顶贴线不算切实体容忍)
        entity_cut = valid & (body_bottom_arr < L) & (L < body_top_arr) & (~near_body_top_edge)
        normal_zone = valid & (~entity_accept) & (~entity_cut)
        is_high = normal_zone & (np.abs(high_arr / L - 1.0) <= tol)
        is_upper = normal_zone & (body_top_arr <= L) & (L <= high_arr)
        is_body_top = normal_zone & (np.abs(body_top_arr / L - 1.0) <= max(tol, 实体顶贴线不算切实体容忍))
        is_close = normal_zone & (np.abs(close_arr / L - 1.0) <= tol)
        touch = is_high | is_upper | is_body_top | is_close
        hit = int(touch.sum())
        volume_hit = int((touch & vol_event).sum())
        volume_quality_score = float(vol_bonus[touch].sum())
        cut = int(entity_cut.sum()); volume_cut = int((entity_cut & vol_event).sum())
        accept = int(entity_accept.sum()); volume_accept = int((entity_accept & vol_event).sum())
        net = hit + volume_quality_score - cut * 0.55 - volume_cut * 1.25
        cut_ratio = cut / max(1, hit)
        heavy_cut = bool(cut_ratio >= 核心线切实体硬降级比例 or volume_cut >= 带量切实体硬降级阈值)
        level = "核心线候选" if hit >= 最少共振点 and net > 0 and not heavy_cut else "切实体污染降级" if heavy_cut and hit >= 最少共振点 else "未成线"
        # V16：批量候选阶段禁止逐线跑时间序列状态。该函数是 O(候选线×K线) 的 Python 循环，
        # 会让单票从秒级膨胀到分钟级。先用静态计数成线，入围后再由 补充核心线精修() 精算。
        seq = {"acceptance_state": 细分边界状态({"effective_resonance_count": hit, "entity_accept_count": accept, "false_breakout_count": 0, "failed_retest_count": 0}), "boundary_role": "待精修", "false_breakout_count": 0, "failed_retest_count": 0, "accepted_segments": 0, "support_resistance_flip_count": 0}
        out.append({
            "line": 四舍五入(L, 3), "source": source, "score": 四舍五入(hit, 3), "net_score": 四舍五入(net, 3),
            "effective_resonance_count": hit, "volume_resonance_count": volume_hit, "volume_quality_score": 四舍五入(volume_quality_score, 3), "cut_entity_ratio": 四舍五入(cut_ratio, 3), "heavy_entity_cut_downrank": bool(heavy_cut),
            "mild_volume_touch_count": int((touch & (vol_type_code == 1)).sum()), "healthy_volume_touch_count": int((touch & (vol_type_code == 2)).sum()), "standard_double_volume_touch_count": int((touch & (vol_type_code == 3)).sum()), "high_quality_double_volume_touch_count": int((touch & (vol_type_code == 4)).sum()), "stall_volume_touch_count": int((touch & (vol_type_code == -1)).sum()),
            "high_touch_count": int(is_high.sum()), "upper_shadow_hit_count": int((is_upper & ~is_body_top).sum()), "body_top_touch_count": int(is_body_top.sum()), "close_touch_count": int(is_close.sum()),
            "entity_cut_count": cut, "volume_entity_cut_count": volume_cut, "entity_accept_count": accept, "volume_entity_accept_count": volume_accept,
            "false_breakout_count": seq.get("false_breakout_count", 0), "failed_retest_count": seq.get("failed_retest_count", 0),
            "level": level, "current_state": seq.get("acceptance_state", ""), "acceptance_state": seq.get("acceptance_state", ""), "boundary_role": seq.get("boundary_role", ""),
            "accepted_segments": seq.get("accepted_segments", 0), "support_resistance_flip_count": seq.get("support_resistance_flip_count", 0),
            "acceptance_strength_score": 四舍五入(夹紧(accept * 0.35 + volume_accept * 1.10 + 安全浮点(seq.get("accepted_segments")) * 1.20 - 安全浮点(seq.get("support_resistance_flip_count")) * 1.50, 0.0, 12.0), 3),
            "boundary_quality_score": 四舍五入(net, 3),
        })
    return out


def 分组取核心线(scored: List[Dict[str, Any]], rank_mode: str, band_tol: Optional[float] = None, max_output: Optional[int] = None) -> List[Dict[str, Any]]:
    """按价格带分组，保留主线/最低有效边界/上沿/带量线。V9加入密度、智能宽度和敏感性入排序。"""
    btol = 安全浮点(band_tol, 核心线带宽)
    xs = sorted([x for x in scored if 安全浮点(x.get("line")) > 0 and x.get("level") == "核心线候选"], key=lambda z: 安全浮点(z.get("line")))
    groups: List[List[Dict[str, Any]]] = 动态价格带分组(xs, lambda z: z.get("line"), btol)

    def quality(item: Dict[str, Any]) -> float:
        return 安全浮点(item.get("boundary_quality_score"), 安全浮点(item.get("net_score")) + 安全浮点(item.get("sensitivity_bonus")) + 安全浮点(item.get("vbp_support_score")))

    def rank_key(item: Dict[str, Any]) -> Tuple[float, float, float, float, float]:
        # V11：最大共振第一。周期、VBP、密度只能校准，不能把共振最多的核心线挤掉。
        hit = 安全浮点(item.get("effective_resonance_count"))
        volq = 安全浮点(item.get("volume_quality_score"))
        net = 安全浮点(item.get("net_score"))
        cut = 安全浮点(item.get("entity_cut_count")) + 安全浮点(item.get("volume_entity_cut_count")) * 1.6
        q = quality(item)
        if rank_mode == "trigger":
            return (hit, volq, net, -cut, -安全浮点(item.get("line")))
        return (hit, volq, net, q * 0.25 - cut, -安全浮点(item.get("line")))

    keep_per_band = 5 if rank_mode == "trigger" else 4
    selected_by_line: Dict[float, Dict[str, Any]] = {}

    def add_item(item: Dict[str, Any], role: str, band_low: float, band_high: float, band_size: int) -> None:
        L = 四舍五入(item.get("line"), 3)
        if L <= 0:
            return
        out = dict(item)
        width_pct = 四舍五入(涨幅百分比(band_high, band_low), 2) if band_low > 0 else 0.0
        density = 四舍五入(band_size / max(width_pct, 0.25), 3)
        out["boundary_band_low"] = 四舍五入(band_low, 3)
        out["boundary_band_high"] = 四舍五入(band_high, 3)
        out["boundary_band_mid"] = 四舍五入((band_low + band_high) / 2.0, 3)
        out["boundary_band_width_pct"] = width_pct
        out["boundary_band_member_count"] = int(band_size)
        out["price_cluster_density"] = density
        out["boundary_width_quality"] = "窄密集" if width_pct <= 2.0 and density >= 1.2 else "宽带需确认" if width_pct >= 界过宽无确认降级阈值 else "正常边界带"
        out["boundary_quality_score"] = 四舍五入(quality(out) + min(2.0, density * 0.25) - (1.5 if out["boundary_width_quality"] == "宽带需确认" else 0.0), 3)
        old = selected_by_line.get(L)
        if old:
            roles = set(str(old.get("boundary_role", "")).split("+")) | {role}
            old["boundary_role"] = "+".join(sorted(x for x in roles if x))
            return
        out["boundary_role"] = role
        out["acceptance_state"] = 细分边界状态(out)
        conf, conf_score = 界置信度(out)
        out["boundary_confidence"] = conf
        out["boundary_confidence_score"] = conf_score
        selected_by_line[L] = out

    for g in groups:
        ranked_group = sorted(g, key=rank_key, reverse=True)
        prices = [安全浮点(z.get("line")) for z in g if 安全浮点(z.get("line")) > 0]
        if not prices:
            continue
        band_low, band_high = min(prices), max(prices)
        for n, item in enumerate(ranked_group[:keep_per_band], 1):
            add_item(item, "主核心线" if n == 1 else f"次优线{n}", band_low, band_high, len(g))
        add_item(min(g, key=lambda z: 安全浮点(z.get("line"))), "最低有效边界线", band_low, band_high, len(g))
        add_item(max(g, key=lambda z: 安全浮点(z.get("line"))), "边界带上沿线", band_low, band_high, len(g))
        add_item(max(g, key=lambda z: (安全浮点(z.get("volume_quality_score")), 安全浮点(z.get("net_score")))), "带量共振线", band_low, band_high, len(g))
        sens_candidates = [z for z in g if bool(z.get("lowest_valid_boundary"))]
        if sens_candidates:
            add_item(max(sens_candidates, key=rank_key), "敏感性最低有效线", band_low, band_high, len(g))

    selected = list(selected_by_line.values())
    selected = sorted(selected, key=rank_key, reverse=True)
    if max_output and max_output > 0:
        selected = selected[:max_output]
    return selected



def 选择周期核心线(df: pd.DataFrame, label: str, period: str, timeframe: str, lookback_bars: int, next_date: Any = None) -> Dict[str, Any]:
    """多周期核心界：周/月/季/年共用同一套最大共振逻辑。"""
    d = df.copy().reset_index(drop=True)
    k = 周期聚合(d, period, date_label="period")
    if k.empty:
        return {"line": 0.0, "level": "数据不足", "line_label": label, "reason": f"{label}样本不足"}
    k_eval = k.copy().reset_index(drop=True)
    if len(k_eval) >= 2 and not d.empty:
        last_date = pd.to_datetime(d["date"].iloc[-1], errors="coerce")
        nd = pd.to_datetime(next_date, errors="coerce") if next_date is not None else pd.NaT
        if pd.notna(last_date) and pd.notna(nd):
            try:
                if last_date.to_period(period) == nd.to_period(period):
                    k_eval = k_eval.iloc[:-1].copy().reset_index(drop=True)
            except Exception as exc:
                # period 字符串异常时保守保留样本，不中断全市场扫描。
                _period_compare_error = str(exc)
    if lookback_bars > 0 and len(k_eval) > lookback_bars:
        k_eval = k_eval.tail(lookback_bars).copy().reset_index(drop=True)
    if len(k_eval) < 3:
        return {"line": 0.0, "level": "数据不足", "line_label": label, "reason": f"{label}有效样本不足"}
    line_tol, band_tol = 自适应界容差(k_eval, timeframe)
    sources = 核心线候选来源(k_eval)
    scored = 批量评估核心线(k_eval, sources, line_tol=line_tol)
    # V16：敏感性/时间序列状态延迟到入围线后再精修。
    if not 延迟核心线精修:
        scored = 注入敏感性字段(k_eval, scored, sources, line_tol)
    ranked = 分组取核心线(scored, rank_mode="historical", band_tol=band_tol, max_output=max(大周期融合候选数量, 8))
    ranked = 补充核心线精修(k_eval, ranked, sources, line_tol)
    if not ranked:
        return {"line": 0.0, "level": "未识别", "line_label": label, "reason": f"{label}未识别到有效共振线"}
    best = dict(ranked[0])
    best["line_label"] = label
    best["rank_mode"] = "historical"
    best["line_timeframe"] = timeframe
    best["top_candidates"] = ranked[:max(大周期融合候选数量, 8)]
    best["all_candidates_count"] = len(sources)
    best["effective_candidates_count"] = len(ranked)
    best["line_tolerance"] = line_tol
    best["band_tolerance"] = band_tol
    return best


def 核心线字典序排序键(item: Dict[str, Any]) -> Tuple[float, ...]:
    """V13：跨周期排序先用归一共振强度，再看带量共振与净质量。

    原始hit在周/月/季/年之间不可直接硬比；这里用样本规模归一 + 周期级别权重解决低周期天然hit更多的问题。
    VBP、密度、周期bonus只做末级校准。
    """
    hit = 安全浮点(item.get("effective_resonance_count"))
    norm_hit = 共振归一强度(item)
    volq = 安全浮点(item.get("volume_quality_score"))
    vol_hit = 安全浮点(item.get("volume_resonance_count"))
    net = 安全浮点(item.get("net_score"))
    cut = 安全浮点(item.get("entity_cut_count")) + 安全浮点(item.get("volume_entity_cut_count")) * 2.0
    cut_ratio = 安全浮点(item.get("cut_entity_ratio"), 安全浮点(item.get("entity_cut_count")) / max(1.0, hit))
    frames = 安全浮点(item.get("multi_tf_confluence_count", 1))
    tf_grade = 周期级别权重(str(item.get("line_timeframe", "")))
    vbp_score = 安全浮点(item.get("vbp_support_score")) if bool(item.get("vbp_amount_reliable", True)) else 0.0
    density = 安全浮点(item.get("price_cluster_density"))
    width = 安全浮点(item.get("boundary_band_width_pct"))
    heavy_penalty = 1.0 if bool(item.get("heavy_entity_cut_downrank")) else 0.0
    return (
        norm_hit,
        volq,
        vol_hit,
        max(0.0, net),
        -cut_ratio,
        -cut,
        tf_grade,
        frames,
        hit,
        vbp_score,
        density,
        -width,
        -heavy_penalty,
        安全浮点(item.get("multi_tf_rank_score")),
    )


def 选择大周期融合核心线(df: pd.DataFrame, next_date: Any = None) -> Dict[str, Any]:
    """周/月/季/年融合界。

    V13：
    1）跨周期共振按周期样本规模归一，避免周线/日线原始hit天然压过年/季/月线；
    2）VBP只做可靠成交额前提下的参考置信，不抬高确认线、不扩宽界带、不参与硬拒；
    3）融合上沿使用稳健上沿，不再被单个异常高上沿污染；
    4）融合上沿保留为升级确认线，是否作为硬确认由配置控制。
    """
    specs = [
        ("自然周核心共振线", "W-FRI", "自然周", 180, 1.5),
        ("自然月核心共振线", "M", "自然月", 历史月线窗口, 4.0),
        ("自然季核心共振线", "Q", "自然季", 80, 6.0),
        ("自然年核心共振线", "Y", "自然年", 35, 8.0),
    ]
    candidates: List[Dict[str, Any]] = []
    for label, period, tf, lookback, tf_bonus in specs:
        info = 选择周期核心线(df, label, period, tf, lookback, next_date=next_date)
        for cand in 展开核心线候选(info, max(3, 大周期融合候选数量)):
            if 安全浮点(cand.get("line")) <= 0:
                continue
            item = dict(cand)
            item["line_label"] = label
            item["line_timeframe"] = tf
            item["multi_tf_bonus"] = tf_bonus
            hit = 安全浮点(item.get("effective_resonance_count"))
            volq = 安全浮点(item.get("volume_quality_score"))
            net = 安全浮点(item.get("net_score"))
            cut = 安全浮点(item.get("entity_cut_count")) + 安全浮点(item.get("volume_entity_cut_count")) * 1.6
            item["primary_resonance_rank_score"] = 四舍五入(hit * 12.0 + volq * 4.0 + max(0.0, net) * 1.1 - cut * 3.0, 3)
            item["multi_tf_rank_score"] = 四舍五入(安全浮点(item.get("primary_resonance_rank_score")), 3)
            candidates.append(item)
    if not candidates:
        return {"line": 0.0, "level": "未识别", "line_label": "大周期融合核心界", "reason": "多周期均未识别到有效共振线"}

    vbp = 计算VBP筹码带(df)
    vbp_reliable = bool(vbp.get("vbp_amount_reliable", False))
    for item in candidates:
        ilow = 安全浮点(item.get("boundary_band_low", item.get("line")))
        ihigh = 安全浮点(item.get("boundary_band_high", item.get("line")))
        overlap = 区间重叠比例(ilow, ihigh, 安全浮点(vbp.get("vbp_band_low")), 安全浮点(vbp.get("vbp_band_high"))) if vbp_reliable else 0.0
        vbp_score = 0.0
        if vbp_reliable and overlap >= VBP最小重叠比例:
            vbp_score = min(5.0, 安全浮点(vbp.get("vbp_cluster_score")) * overlap * 0.20)
        item.update({
            "vbp_overlap_ratio": 四舍五入(overlap, 3), "vbp_support_score": 四舍五入(vbp_score, 2),
            "vbp_band_low": vbp.get("vbp_band_low", 0), "vbp_band_high": vbp.get("vbp_band_high", 0), "vbp_peak_price": vbp.get("vbp_peak_price", 0),
            "vbp_cluster_score": vbp.get("vbp_cluster_score", 0), "vbp_band_width_pct": vbp.get("vbp_band_width_pct", 0), "vbp_method": vbp.get("vbp_method", ""),
            "vbp_amount_reliable": bool(vbp_reliable),
        })

    candidates = sorted(candidates, key=lambda z: 安全浮点(z.get("line")))
    mtf_band_tol = max(核心线带宽, 0.022)
    groups: List[List[Dict[str, Any]]] = 动态价格带分组(candidates, lambda z: z.get("line"), mtf_band_tol)

    for g in groups:
        frames = sorted(set(str(x.get("line_timeframe", "")) for x in g if str(x.get("line_timeframe", ""))))
        lows = [安全浮点(x.get("boundary_band_low", x.get("line"))) for x in g if 安全浮点(x.get("line")) > 0]
        highs = [安全浮点(x.get("boundary_band_high", x.get("line"))) for x in g if 安全浮点(x.get("line")) > 0]
        raw_core_low = min(lows) if lows else 0.0
        raw_core_high = max(highs) if highs else 0.0
        robust_low, robust_high, robust_note = 稳健融合边界(g)
        confluence_bonus = max(0, len(frames) - 1) * 5.0 + max(0, len(g) - 1) * 0.8
        for item in g:
            fusion_low = robust_low
            fusion_high = robust_high
            width_pct = 涨幅百分比(fusion_high, fusion_low) if fusion_low > 0 else 0.0
            width_quality = "过宽硬拒" if width_pct >= 压力带极宽硬拒宽度 else "偏宽需吸收" if width_pct >= 压力带正式最大宽度 else "略宽" if width_pct >= 压力带理想最大宽度 else "紧凑"
            item["multi_tf_confluence_count"] = len(frames)
            item["multi_tf_confluence_frames"] = "/".join(frames)
            item["multi_tf_confluence_bonus"] = 四舍五入(confluence_bonus, 2)
            item["multi_tf_boundary_low"] = 四舍五入(fusion_low, 3)
            item["multi_tf_boundary_high"] = 四舍五入(fusion_high, 3)
            item["core_boundary_low"] = 四舍五入(raw_core_low, 3)
            item["core_boundary_high"] = 四舍五入(raw_core_high, 3)
            item["robust_fusion_high_note"] = robust_note
            item["fusion_confirm_line"] = 四舍五入(fusion_high, 3)
            item["vbp_confirm_line"] = 四舍五入(fusion_high, 3)  # 仅兼容旧字段，VBP不抬高。
            item["vbp_reference_line"] = 四舍五入(安全浮点(item.get("vbp_peak_price")) if 安全浮点(item.get("vbp_peak_price")) > 0 else fusion_high, 3)
            item["boundary_band_width_pct"] = 四舍五入(width_pct, 2)
            item["boundary_width_quality"] = width_quality
            penalty = 0.0
            if width_pct >= 压力带正式最大宽度:
                penalty += (width_pct - 压力带正式最大宽度) * 1.8
            if width_pct >= 压力带极宽硬拒宽度:
                penalty += 8.0
            # rank_score只作为末级校准，不参与最大共振主排序抢第一。
            item["multi_tf_rank_score"] = 四舍五入(
                安全浮点(item.get("primary_resonance_rank_score")) + confluence_bonus * 0.45 + 安全浮点(item.get("vbp_support_score")) * 0.20 - penalty,
                3,
            )
            conf, conf_score = 界置信度(规范化融合界字段(item))
            item["boundary_confidence"] = conf
            item["boundary_confidence_score"] = conf_score

    # 上面为了保持对象引用，需重新规范化所有候选。
    normalized: List[Dict[str, Any]] = []
    for item in candidates:
        z = 规范化融合界字段(item)
        if "boundary_confidence" not in z:
            conf, conf_score = 界置信度(z)
            z["boundary_confidence"] = conf
            z["boundary_confidence_score"] = conf_score
        normalized.append(z)

    normalized = sorted(normalized, key=核心线字典序排序键, reverse=True)
    best = dict(normalized[0])
    best["line_label"] = "大周期融合核心界"
    best["rank_mode"] = "multi_timeframe"
    best["top_candidates"] = normalized[:max(大周期融合候选数量, 历史核心线候选数量, 8)]
    best["multi_timeframe_candidates_count"] = len(normalized)
    return best


def 选择日线触发线(df: pd.DataFrame, label: str = "近500日日线共振触发线", lookback_days: int = 近端日线窗口) -> Dict[str, Any]:
    """用突破日前已出现的日K寻找近端触发线。

    这条线用于捕捉日线小平台、低量精准触发线、近期粘合区上沿等短线触发位置；
    候选压缩只做提速，每个价格带保留多条线，最终仍按真实共振净分排名。
    """
    d = 加基础指标(df).copy().reset_index(drop=True)
    if lookback_days > 0 and len(d) > lookback_days:
        d = d.tail(lookback_days).copy().reset_index(drop=True)
    if d.empty or len(d) < 30:
        return {"line": 0.0, "level": "数据不足", "line_label": label, "reason": f"{label}日线样本不足"}

    raw_sources = 核心线候选来源(d)
    # 日线候选量大，但不能每带只留一条；否则会漏掉真正共振最多的触发线。
    max_sources = max(260, 近端触发线候选数量 * 45)
    line_tol, band_tol = 自适应界容差(d, "日线")
    sources = 压缩候选来源(raw_sources, band_tol=band_tol, max_count=max_sources, keep_per_group=4)
    scored = 批量评估核心线(d, sources, line_tol=line_tol)
    # V16：日线候选更多，默认只对入围触发线做精修。
    if not 延迟核心线精修:
        scored = 注入敏感性字段(d, scored, sources, line_tol)
    ranked = 分组取核心线(scored, rank_mode="trigger", band_tol=band_tol)
    ranked = 补充核心线精修(d, ranked, sources, line_tol)
    if not ranked:
        return {"line": 0.0, "level": "未识别", "line_label": label, "reason": f"{label}未识别到有效日线共振触发线"}
    best = dict(ranked[0])
    best["line_label"] = label
    best["lookback_days"] = int(lookback_days)
    best["rank_mode"] = "trigger"
    best["line_timeframe"] = "日线"
    best["top_candidates"] = ranked[:max(近端触发线候选数量, 8)]
    best["all_candidates_count"] = len(raw_sources)
    best["compressed_candidates_count"] = len(sources)
    best["effective_candidates_count"] = len(ranked)
    best["line_tolerance"] = line_tol
    best["band_tolerance"] = band_tol
    return best

def 选择历史核心线(df: pd.DataFrame, next_date: Any = None) -> Dict[str, Any]:
    return 选择大周期融合核心线(df, next_date=next_date)

def 选择近端触发线(df: pd.DataFrame) -> Dict[str, Any]:
    return 选择日线触发线(df, "近500日日线共振触发线", lookback_days=近端日线窗口)



def 选择突破日前双线(df: pd.DataFrame, breakout_idx: int) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """冻结核心线：只允许使用突破日前已经存在的数据生成线。"""
    d = df.iloc[:max(0, breakout_idx)].copy().reset_index(drop=True)
    next_date = df.iloc[breakout_idx]["date"] if 0 <= breakout_idx < len(df) and "date" in df.columns else None
    if len(d) < 最少K线数:
        empty = {"line": 0.0, "level": "数据不足", "reason": "突破日前样本不足"}
        return dict(empty, line_label="历史核心共振线"), dict(empty, line_label="近500日日线共振触发线")
    return 选择历史核心线(d, next_date=next_date), 选择近端触发线(d)


def 展开核心线候选(line_info: Dict[str, Any], limit: int) -> List[Dict[str, Any]]:
    """把主线和top_candidates展开，避免只测排名第一的核心线导致漏筛。"""
    out: List[Dict[str, Any]] = []
    seen: set = set()

    def add(src: Dict[str, Any]) -> None:
        if not isinstance(src, dict):
            return
        L = 四舍五入(src.get("line"), 3)
        if L <= 0 or L in seen:
            return
        item = dict(line_info)
        item.update(src)
        # 保留主线的标签/周期/冻结信息。
        for key in ["line_label", "lookback_days", "rank_mode", "line_timeframe", "line_frozen_before_date", "line_frozen_data_end"]:
            if key in line_info and key not in item:
                item[key] = line_info[key]
        seen.add(L)
        out.append(item)

    add(line_info)
    for cand in line_info.get("top_candidates", []) or []:
        add(cand)
        if len(out) >= limit:
            break
    return out[:limit]

# ---------- 日线突破 ----------

def K线特征(row: pd.Series, prev_close: float, line: float = 0.0) -> Dict[str, float]:
    open_ = 安全浮点(row.get("open"))
    high = 安全浮点(row.get("high"))
    low = 安全浮点(row.get("low"))
    close = 安全浮点(row.get("close"))
    rng_raw = high - low
    rng = max(rng_raw, 1e-9)
    body_top = max(open_, close)
    body_bottom = min(open_, close)
    body_abs = abs(close - open_)
    entity_above_line_ratio = 0.0
    if line > 0:
        if body_abs > 0:
            entity_above_line_ratio = max(0.0, body_top - max(body_bottom, line)) / body_abs
        elif close > line and abs(high - low) <= max(prev_close, 1e-9) * 0.001:
            # 一字涨停没有实体，但事件上已经整体站在线上；只用于事件识别，正式池另行硬拦。
            entity_above_line_ratio = 1.0
    return {
        "body_ratio": body_abs / rng,
        "close_pos": (close - low) / rng if rng_raw > 0 else (1.0 if close >= high else 0.0),
        "upper_shadow_ratio": (high - body_top) / rng if rng_raw > 0 else 0.0,
        "body_pct_abs": body_abs / max(prev_close, 1e-9),
        "entity_above_line_ratio": entity_above_line_ratio,
        "one_price_range": 1.0 if abs(high - low) <= max(prev_close, 1e-9) * 0.001 else 0.0,
    }


def 突破日基础预过滤(df: pd.DataFrame, global_idx: int, code: str) -> bool:
    """不依赖核心线的突破K基础预过滤；只跳过必然不可能成为破界的弱K。"""
    d = 加基础指标(df)
    if d.empty or global_idx <= 0 or global_idx >= len(d):
        return False
    prev = d.iloc[global_idx - 1]
    r = d.iloc[global_idx]
    prev_close = 安全浮点(prev.get("close"))
    open_ = 安全浮点(r.get("open"))
    close = 安全浮点(r.get("close"))
    high = 安全浮点(r.get("high"))
    low = 安全浮点(r.get("low"))
    pct = 安全浮点(r.get("pct_chg"))
    if prev_close <= 0 or close <= 0 or pct < 突破最小涨幅:
        return False

    pre20 = d.iloc[max(0, global_idx - 20):global_idx]
    vol_ref = 安全浮点(pre20["volume"].median()) if not pre20.empty else 0.0
    volume_ratio = 安全浮点(r.get("volume")) / vol_ref if vol_ref > 0 else 0.0
    feat = K线特征(r, prev_close, 0.0)
    limit_up = pct >= 涨停阈值(code) and feat["close_pos"] >= 0.88
    one_price_limit = bool(limit_up and feat.get("one_price_range", 0.0) >= 1.0 and abs(close - open_) <= max(prev_close, 1e-9) * 0.001)

    # 一字涨停作为突破事件允许进入后续画线验证，但后面正式池硬拦，避免不可交易票混入买入池。
    if one_price_limit:
        return bool(volume_ratio >= 一字涨停最小量比 or vol_ref <= 0)

    if close <= open_ or high <= low:
        return False
    if feat["body_ratio"] < 突破最小实体占比 or feat["close_pos"] < 突破最小收盘位置 or feat["upper_shadow_ratio"] > 突破最大上影比例 or feat["body_pct_abs"] < 突破最小实体涨幅:
        return False
    # 涨停板日线量能容易被早盘封板压低：预过滤只负责放行，正式交易仍看后续承接/可追性。
    return bool(volume_ratio >= 普通突破最小量比 or limit_up)


def 日线单日突破质量(df: pd.DataFrame, global_idx: int, line: float, code: str, line_info: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """评估指定交易日是否形成破界。

    V11分层：
    - 主核心线突破 = 第一层触发；
    - 单周期/融合上沿突破 = 升级确认；
    - 只有VBP参考线被越过，不算硬确认。
    """
    d = 加基础指标(df)
    L = 安全浮点(line)
    empty = {"hit": False, "date": "", "quality": 0.0, "reason": "指定日未形成有效突破"}
    if d.empty or L <= 0 or global_idx <= 0 or global_idx >= len(d):
        return empty

    prev = d.iloc[global_idx - 1]
    r = d.iloc[global_idx]
    prev_close = 安全浮点(prev.get("close"))
    open_ = 安全浮点(r.get("open"))
    high = 安全浮点(r.get("high"))
    low = 安全浮点(r.get("low"))
    close = 安全浮点(r.get("close"))
    pct = 安全浮点(r.get("pct_chg"))
    if prev_close <= 0 or close <= 0:
        return empty

    info = 规范化融合界字段(line_info or {"line": L})
    hard_line = 安全浮点(info.get("hard_confirm_line"), L) or L
    fusion_line = 安全浮点(info.get("fusion_confirm_line"), hard_line) or hard_line
    single_line = 安全浮点(info.get("single_confirm_line"), L) or L
    effective_line = 安全浮点(info.get("effective_confirm_line"), hard_line) or hard_line
    boundary_required = bool(line_info is not None and hard_line > L * 1.001 and 正式必须突破边界带上沿)

    volume = 安全浮点(r.get("volume"))
    pre20 = d.iloc[max(0, global_idx - 20):global_idx]
    vol_ref = 安全浮点(pre20["volume"].median()) if not pre20.empty else 0.0
    volume_ratio = volume / vol_ref if vol_ref > 0 else 0.0

    def supply_eval(eval_line: float) -> Tuple[float, bool, bool, bool, bool, int]:
        crossed = prev_close <= eval_line * (1.0 - 突破前收线下)
        near_line = eval_line * (1.0 - 突破前收线下) < prev_close <= eval_line * (1.0 + 突破贴线蓄势容忍)
        supply_window = max(3, 二次确认供应窗口)
        pre3 = d.iloc[max(0, global_idx - 3):global_idx]
        preN = d.iloc[max(0, global_idx - supply_window):global_idx]
        recent_above_count = int((pre3["close"] > eval_line * (1.0 + 突破贴线蓄势容忍)).sum()) if not pre3.empty else 0
        supply_cap_pct = max(0.080, 突破二次确认最大前收站上 + 0.045)
        pre3_supply = pre3[(pre3["high"] >= eval_line * 0.985) & (pre3["high"] <= eval_line * (1.0 + supply_cap_pct))] if not pre3.empty else pre3
        pre3_high = 安全浮点(pre3_supply["high"].max()) if not pre3_supply.empty else 0.0
        near_supply = preN[(preN["high"] >= eval_line * 0.985) & (preN["low"] <= eval_line * (1.0 + 突破二次确认最大前收站上 + 0.035)) & (preN["high"] <= eval_line * (1.0 + supply_cap_pct))] if not preN.empty else preN
        platform_supply_high = 安全浮点(near_supply["high"].max()) if not near_supply.empty else pre3_high
        supply_high = max(pre3_high, platform_supply_high)
        body_top_today = max(open_, close)
        supply_cleared = bool(supply_high <= 0 or (close >= supply_high and body_top_today >= supply_high))
        volume_confirm_ok_local = volume_ratio >= 健康突破最小量比
        recent_soft = bool(
            eval_line * (1.0 + 突破贴线蓄势容忍) < prev_close <= eval_line * (1.0 + 突破二次确认最大前收站上)
            and recent_above_count <= 2
            and volume_confirm_ok_local
            and supply_cleared
        )
        already = bool(prev_close > eval_line * (1.0 + 突破二次确认最大前收站上) or (recent_above_count >= 3 and prev_close > eval_line * (1.0 + 突破贴线蓄势容忍)))
        return supply_high, crossed, near_line, recent_soft, already, recent_above_count

    def evaluate_line(eval_line: float) -> Dict[str, Any]:
        feat = K线特征(r, prev_close, eval_line)
        limit_up = pct >= 涨停阈值(code) and feat["close_pos"] >= 0.88
        one_price_limit = bool(limit_up and feat.get("one_price_range", 0.0) >= 1.0 and abs(close - open_) <= max(prev_close, 1e-9) * 0.001)
        # 涨停板量能不按普通量比预杀；无分时情况下先放行，后续由承接/可追/报告复核处理。
        volume_ok = volume_ratio >= 普通突破最小量比 or limit_up or (one_price_limit and (volume_ratio >= 一字涨停最小量比 or vol_ref <= 0))
        volume_confirm_ok = volume_ratio >= 健康突破最小量比 or limit_up or one_price_limit
        supply_high, crossed, near_line, recent_soft, already, recent_above_count = supply_eval(eval_line)
        close_confirm = close >= eval_line * (1.0 + 突破收盘站上)
        entity_ratio = feat["entity_above_line_ratio"]
        normal_k_quality = (
            pct >= 突破最小涨幅
            and close > open_
            and high > low
            and feat["body_ratio"] >= 突破最小实体占比
            and feat["close_pos"] >= 突破最小收盘位置
            and feat["upper_shadow_ratio"] <= 突破最大上影比例
            and feat["body_pct_abs"] >= 突破最小实体涨幅
            and entity_ratio >= 突破实体上线硬闸
            and volume_ok
        )
        one_price_quality = bool(one_price_limit and close_confirm and close > eval_line and volume_ok)
        k_quality = normal_k_quality or one_price_quality
        path_ok = crossed or near_line or recent_soft
        quality = 0.0
        if one_price_limit:
            quality += 18.0
        else:
            quality += 10.0 if entity_ratio >= 0.80 else 8.0 if entity_ratio >= 0.60 else 5.0 if entity_ratio >= 0.35 else 0.0
            quality += 7.0 if feat["close_pos"] >= 0.90 else 5.0 if feat["close_pos"] >= 0.80 else 3.0
            quality += 5.0 if feat["body_ratio"] >= 0.65 else 3.5 if feat["body_ratio"] >= 0.45 else 2.0
        quality += 8.0 if 1.8 <= volume_ratio <= 2.5 else 6.0 if 健康突破最小量比 <= volume_ratio <= 3.2 else 4.0 if volume_ok else 0.0
        quality += 4.0 if limit_up and not one_price_limit else 0.0
        quality += 2.0 if near_line else 0.0
        quality += 2.0 if recent_soft else 0.0
        quality += min(5.0, max(0.0, pct - 1.0) * 0.7)
        quality -= max(0.0, feat["upper_shadow_ratio"] - 0.18) * 10.0
        mode = "一字涨停突破" if one_price_limit else "弱站上后二次确认突破" if recent_soft else "贴线蓄势突破" if near_line else "线下突破"
        reason = ""
        if already:
            reason = "前收已明显在线上，转入突破后接受逻辑，不算首次破界"
        elif eval_line * (1.0 + 突破贴线蓄势容忍) < prev_close <= eval_line * (1.0 + 突破二次确认最大前收站上) and not bool(supply_high <= 0 or close >= supply_high):
            reason = f"二次确认未清掉近{max(3, 二次确认供应窗口)}日贴线供应高点{supply_high:.2f}"
        elif not volume_ok:
            reason = f"突破量能不足{volume_ratio:.2f}"
        elif entity_ratio < 突破实体上线硬闸 and not one_price_limit:
            reason = f"实体站上线比例不足{entity_ratio:.2f}"
        else:
            reason = "未从线下/贴线/弱站上确认区突破"
        return {
            "ok": bool(path_ok and not already and close_confirm and high >= eval_line and k_quality),
            "close_confirm": close_confirm, "high_touch": bool(high >= eval_line), "quality": 夹紧(quality, 0.0, 40.0),
            "feat": feat, "mode": mode, "limit_up": bool(limit_up), "one_price_limit": bool(one_price_limit),
            "volume_ok": bool(volume_ok), "volume_confirm_ok": bool(volume_confirm_ok), "supply_high": supply_high,
            "entity_ratio": entity_ratio, "reason": reason,
        }

    hard_eval = evaluate_line(hard_line)
    core_eval = evaluate_line(L)

    def ret(stage: str, eval_line: float, ev: Dict[str, Any], hit: bool, tradable: bool, reason_prefix: str, fusion_hit: bool) -> Dict[str, Any]:
        feat = ev["feat"]
        return {
            "hit": bool(hit),
            "date": r["date"].strftime("%Y-%m-%d"),
            "quality": 四舍五入(ev.get("quality"), 2),
            "reason": reason_prefix,
            "breakout_idx": int(global_idx),
            "突破日期": r["date"].strftime("%Y-%m-%d"),
            "突破收盘": 四舍五入(close, 3),
            "突破涨幅%": 四舍五入(pct, 2),
            "突破量比": 四舍五入(volume_ratio, 2),
            "突破质量分": 四舍五入(ev.get("quality"), 2),
            "突破实体上线占比": 四舍五入(ev.get("entity_ratio"), 3),
            "突破实体占比": 四舍五入(feat["body_ratio"], 3),
            "突破收盘位置": 四舍五入(feat["close_pos"], 3),
            "突破上影比例": 四舍五入(feat["upper_shadow_ratio"], 3),
            "突破模式": ev.get("mode", ""),
            "涨停特殊处理": bool(ev.get("limit_up")),
            "一字涨停事件": bool(ev.get("one_price_limit")),
            "交易可追": bool(tradable and not ev.get("one_price_limit")),
            "二次确认供应高点": 四舍五入(ev.get("supply_high"), 3),
            "核心线": 四舍五入(L, 3),
            "交易确认线": 四舍五入(eval_line, 3),
            "实际突破线": 四舍五入(eval_line, 3),
            "有效确认线": 四舍五入(eval_line, 3),
            "硬确认线": 四舍五入(hard_line, 3),
            "融合确认线": 四舍五入(fusion_line, 3),
            "单周期确认线": 四舍五入(single_line, 3),
            "是否打穿融合界": bool(fusion_hit),
            "确认层级": stage,
            "突破口径": stage,
            "突破量能层级": "标准倍量" if 1.8 <= volume_ratio <= 2.5 else "健康放量" if volume_ratio >= 健康突破最小量比 else "最低过闸" if ev.get("volume_ok") else "不足",
        }

    if hard_eval["ok"]:
        if abs(hard_line - fusion_line) / max(fusion_line, 1e-9) <= 0.001 and fusion_line > L * 1.001:
            stage_name = "融合界上沿确认"
        elif abs(hard_line - single_line) / max(single_line, 1e-9) <= 0.001 and single_line > L * 1.001:
            stage_name = "单周期上沿确认"
        else:
            stage_name = "主核心线确认"
        fusion_hit = bool(close >= fusion_line * (1.0 + 突破收盘站上))
        return ret(stage_name, hard_line, hard_eval, True, True, f"{stage_name}｜{hard_eval['mode']}", fusion_hit)

    # 只破主核心线但未打穿融合上沿：不再按废票处理。它是第一层破界，后续必须靠回踩接受升级。
    if boundary_required and core_eval["ok"] and close < hard_line * (1.0 + 突破收盘站上):
        quality = min(34.0, 安全浮点(core_eval.get("quality")) + 1.5)
        core_eval = dict(core_eval); core_eval["quality"] = quality
        return ret("主核心线第一层突破", L, core_eval, True, True, f"主核心线{L:.2f}第一层突破，融合上沿{hard_line:.2f}待升级｜{core_eval['mode']}", False)

    if boundary_required and core_eval["close_confirm"] and close < hard_line * (1.0 + 突破收盘站上):
        pre_quality = 夹紧(12.0 + min(10.0, max(0.0, volume_ratio - 1.0) * 4.0) + 安全浮点(core_eval["feat"].get("close_pos")) * 6.0, 0.0, 28.0)
        return {
            **empty,
            "pre_hit": True,
            "event_stage": "带内突破/预突破",
            "reason": f"只突破核心线{L:.2f}，尚未打穿硬确认线{hard_line:.2f}/融合升级线{fusion_line:.2f}；{core_eval.get('reason','')}",
            "breakout_idx": int(global_idx),
            "突破日期": r["date"].strftime("%Y-%m-%d"),
            "突破收盘": 四舍五入(close, 3),
            "突破涨幅%": 四舍五入(pct, 2),
            "突破量比": 四舍五入(volume_ratio, 2),
            "突破质量分": 四舍五入(pre_quality, 2),
            "突破实体上线占比": 四舍五入(core_eval.get("entity_ratio"), 3),
            "突破实体占比": 四舍五入(core_eval["feat"].get("body_ratio"), 3),
            "突破收盘位置": 四舍五入(core_eval["feat"].get("close_pos"), 3),
            "突破上影比例": 四舍五入(core_eval["feat"].get("upper_shadow_ratio"), 3),
            "突破模式": "带内预突破",
            "涨停特殊处理": bool(core_eval.get("limit_up")),
            "一字涨停事件": bool(core_eval.get("one_price_limit")),
            "交易可追": False,
            "二次确认供应高点": 四舍五入(core_eval.get("supply_high"), 3),
            "核心线": 四舍五入(L, 3),
            "交易确认线": 四舍五入(L, 3),
            "实际突破线": 四舍五入(L, 3),
            "有效确认线": 四舍五入(L, 3),
            "硬确认线": 四舍五入(hard_line, 3),
            "融合确认线": 四舍五入(fusion_line, 3),
            "单周期确认线": 四舍五入(single_line, 3),
            "是否打穿融合界": False,
            "确认层级": "带内预突破",
            "突破口径": "核心线预警",
            "突破量能层级": "标准倍量" if 1.8 <= volume_ratio <= 2.5 else "健康放量" if volume_ratio >= 健康突破最小量比 else "最低过闸" if core_eval.get("volume_ok") else "不足",
        }

    if boundary_required and hard_eval["close_confirm"]:
        pre_quality = 夹紧(14.0 + min(8.0, max(0.0, volume_ratio - 1.0) * 3.0) + 安全浮点(hard_eval["feat"].get("close_pos")) * 5.0, 0.0, 30.0)
        return {
            **empty,
            "pre_hit": True,
            "event_stage": "已越过确认线但缺正式突破K",
            "reason": f"收盘已越过硬确认线{hard_line:.2f}/融合升级线{fusion_line:.2f}，但路径/实体/量能未达正式破界标准；{hard_eval.get('reason','')}",
            "breakout_idx": int(global_idx),
            "突破日期": r["date"].strftime("%Y-%m-%d"),
            "突破收盘": 四舍五入(close, 3),
            "突破涨幅%": 四舍五入(pct, 2),
            "突破量比": 四舍五入(volume_ratio, 2),
            "突破质量分": 四舍五入(pre_quality, 2),
            "突破实体上线占比": 四舍五入(hard_eval.get("entity_ratio"), 3),
            "突破实体占比": 四舍五入(hard_eval["feat"].get("body_ratio"), 3),
            "突破收盘位置": 四舍五入(hard_eval["feat"].get("close_pos"), 3),
            "突破上影比例": 四舍五入(hard_eval["feat"].get("upper_shadow_ratio"), 3),
            "突破模式": "越线未达正式K",
            "涨停特殊处理": bool(hard_eval.get("limit_up")),
            "一字涨停事件": bool(hard_eval.get("one_price_limit")),
            "交易可追": False,
            "二次确认供应高点": 四舍五入(hard_eval.get("supply_high"), 3),
            "核心线": 四舍五入(L, 3),
            "交易确认线": 四舍五入(hard_line, 3),
            "实际突破线": 四舍五入(hard_line, 3),
            "有效确认线": 四舍五入(hard_line, 3),
            "硬确认线": 四舍五入(hard_line, 3),
            "融合确认线": 四舍五入(fusion_line, 3),
            "单周期确认线": 四舍五入(single_line, 3),
            "是否打穿融合界": bool(close >= fusion_line * (1.0 + 突破收盘站上)),
            "确认层级": "越线待确认",
            "突破口径": "越线待确认",
            "突破量能层级": "标准倍量" if 1.8 <= volume_ratio <= 2.5 else "健康放量" if volume_ratio >= 健康突破最小量比 else "最低过闸" if hard_eval.get("volume_ok") else "不足",
        }

    reason = hard_eval.get("reason") or core_eval.get("reason") or "未形成有效分层破界"
    return {**empty, "reason": reason, "二次确认供应高点": 四舍五入(hard_eval.get("supply_high", 0), 3)}


# ---------- 突破后接受、资金、风险 ----------

def 评估突破后接受(df: pd.DataFrame, bidx: int, line: float, line_info: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """评估突破后的界上接受。

    核心口径：
    1）真实回踩触碰只认窄阈值，避免把悬空横住误判为回踩承接；
    2）强势悬空是攻击强信号，可以确认接受，但防守位类型必须标为假设防守；
    3）真实回踩防守与悬空假设防守拆开输出，供交易定价和报告使用。
    """
    d = 加基础指标(df)
    L = 安全浮点(line)
    info = line_info or {}
    latest_close0 = 安全浮点(d.iloc[-1].get("close")) if not d.empty else 0
    boundary_state = 生成界状态(info if info else {"line": L}, {"突破收盘": 安全浮点(d.iloc[bidx].get("close")) if not d.empty and 0 <= bidx < len(d) else 0}, latest_close0)
    effective_line = 安全浮点(boundary_state.get("有效突破确认线"), L) or L
    base_empty = {
        "score": 0.0, "type": "无", "detail": "承接样本不足", "support_price": 0.0,
        "acceptance_confirmed": False, "acceptance_stage": "样本不足", "support_is_trade_defense": False,
        "trade_pullback_touched": False, "trade_pullback_confirmed": False, "clean_pullback_acceptance": False,
        "strong_no_pullback_acceptance": False, "deep_pierce_repair": False, "close_break_repair": False,
        "max_pierce_pct": 0.0, "effective_break_line": 四舍五入(effective_line, 3),
        "accepted_effective_line": False, "fell_back_inside_boundary": False,
        "floating_acceptance_only": False, "floating_distance_from_line_pct": 0.0,
        "defense_mode": "未确认", "position_mode": "等待确认", "defense_validated": False,
        "execution_grade": "未确认", "position_weight": 0.0, "defense_certainty": "未验证",
        "floating_grade": "无",
    }
    if d.empty or L <= 0 or bidx <= 0 or bidx >= len(d):
        return base_empty
    b = d.iloc[bidx]
    post_all = d.iloc[bidx:].copy().reset_index(drop=True)
    after = d.iloc[bidx + 1:].copy().reset_index(drop=True)
    body_bottom = min(安全浮点(b.get("open")), 安全浮点(b.get("close")))
    body_top = max(安全浮点(b.get("open")), 安全浮点(b.get("close")))
    body_mid = (body_top + body_bottom) / 2.0
    latest_close = 安全浮点(d.iloc[-1].get("close"))
    if after.empty:
        return {
            **base_empty, "score": 3.0, "type": "突破当天未确认", "detail": "突破日为最新交易日，无后续K线验证",
            "support_price": 四舍五入(max(effective_line, body_bottom), 3), "support_type": "突破当天临时防守",
            "break_body_mid": 四舍五入(body_mid, 3), "break_body_bottom": 四舍五入(body_bottom, 3),
            "acceptance_stage": "breakout_day_only", "accepted_effective_line": bool(latest_close >= effective_line * (1.0 - 边界上沿接受容忍)),
            "defense_mode": "突破当天临时防守", "position_mode": "只记录突破日，等待后续承接", "defense_validated": False,
        }

    after_below_effective = int((after["close"] < effective_line * (1.0 - 边界上沿接受容忍)).sum())
    last3_below_effective = int((after.tail(3)["close"] < effective_line * (1.0 - 边界上沿接受容忍)).sum())
    below_main_count = int((post_all["close"] < L * 0.992).sum())
    accepted_effective = bool(latest_close >= effective_line * (1.0 - 边界上沿接受容忍))
    if last3_below_effective >= 2 or latest_close < effective_line * (1.0 - 边界上沿接受容忍 * 1.35):
        return {
            **base_empty, "score": 1.5, "type": "突破失败", "detail": "突破后跌回有效确认线/融合界内部",
            "support_price": 四舍五入(effective_line, 3), "support_type": "有效突破确认线",
            "below_line_count": below_main_count, "effective_below_count": after_below_effective,
            "accepted_effective_line": False, "fell_back_inside_boundary": True,
            "acceptance_stage": "failed_back_inside_boundary", "defense_mode": "破界失败不定防守", "position_mode": "放弃/重新等待放量站回",
        }

    static_levels: List[Tuple[str, float, float, bool]] = [
        ("有效突破确认线", effective_line, 0.992, True),
        ("突破K实底", body_bottom, 0.985, body_bottom >= effective_line * 0.985),
        ("突破K实体中位", body_mid, 0.990, False),
        ("主核心线", L, 0.985, False),
    ]
    best_name, best_level, best_score = "", 0.0, 0.0
    trade_pullback_confirmed = False
    trade_pullback_touched = False
    deep_pierce_repair = False
    max_pierce_pct = 0.0
    reasons: List[str] = []
    after_tail = after.tail(min(8, len(after))).copy()

    for name, level, hold_buffer, tradable in static_levels:
        if level <= 0:
            continue
        real_pull = after[after["low"] <= level * (1.0 + 真实回踩触碰上浮容忍)]
        touched_real = not real_pull.empty
        close_hold_all = bool((after["close"] >= level * hold_buffer).all())
        close_hold_tail = bool((after_tail["close"] >= level * max(hold_buffer, 0.992)).all()) if not after_tail.empty else False
        min_low_all = 安全浮点(after["low"].min())
        min_low_touch = 安全浮点(real_pull["low"].min()) if touched_real else min_low_all
        pierce = max(0.0, (level - min_low_touch) / level) if level > 0 and min_low_touch > 0 else 0.0
        if touched_real:
            max_pierce_pct = max(max_pierce_pct, pierce * 100.0)
        clean = bool(touched_real and pierce <= 深刺穿降级阈值 and (close_hold_all or close_hold_tail))
        if touched_real and pierce > 深刺穿降级阈值:
            deep_pierce_repair = True
        score = 0.0
        if clean:
            score = 8.0 if tradable else 5.0
        elif touched_real and pierce <= 深刺穿硬拒阈值 and close_hold_tail:
            score = 4.0 if tradable else 2.5
        elif 安全浮点(after["close"].min()) >= level * 0.995:
            # 这里只是线上维持，不能当真实回踩；给少量状态分，不给防守确认。
            score = 2.0
        if score > best_score:
            best_name, best_level, best_score = name, level, score
        if tradable and touched_real:
            trade_pullback_touched = True
        if tradable and clean:
            trade_pullback_confirmed = True
    if best_score > 0:
        if trade_pullback_touched:
            reasons.append(f"真实回踩触碰{best_name}{best_level:.2f}后承接")
        else:
            reasons.append(f"线上维持/悬空横住，尚未真实回踩{effective_line:.2f}")

    bvol = 安全浮点(b.get("volume"))
    floating_sample = pd.DataFrame()
    if bvol > 0:
        if trade_pullback_touched:
            ref_level = max(effective_line, best_level or effective_line)
            pull_sample = after[after["low"] <= ref_level * (1.0 + 真实回踩触碰上浮容忍)]
            sample_label = "回踩"
        else:
            ref_level = effective_line
            pull_sample = after[(after["low"] > ref_level * (1.0 + 真实回踩触碰上浮容忍)) & (after["low"] <= ref_level * (1.0 + 强势悬空观察带宽))]
            floating_sample = pull_sample
            sample_label = "悬空横盘"
        if not pull_sample.empty:
            pull_vol_ratio = 安全浮点(pull_sample["volume"].median()) / bvol
            small_body_ratio = float(((pull_sample["body_abs_pct"].abs() <= 0.035).sum()) / max(1, len(pull_sample))) if "body_abs_pct" in pull_sample.columns else 0.0
            if pull_vol_ratio <= 0.75:
                best_score += 3.0; reasons.append(f"{sample_label}缩量{pull_vol_ratio:.2f}")
            if small_body_ratio >= 0.55:
                best_score += 2.0; reasons.append(f"{sample_label}小阴小阳")

    # 强势悬空路径：强信号，但不是已验证防守。必须明确区分攻击强度与防守确认。
    post_days = len(after)
    boundary_width = 安全浮点(boundary_state.get("界宽%"))
    strong_no_pullback = False
    floating_grade = "无"
    min_tail_low = 安全浮点(after["low"].min()) if not after.empty else 0.0
    floating_distance_pct = 涨幅百分比(min_tail_low, effective_line) if effective_line > 0 and min_tail_low > 0 else 0.0
    if 强势悬空接受最少天数 <= post_days <= 强势悬空B接受最多天数 and boundary_width <= 强势悬空B最大界宽 and not trade_pullback_touched:
        tail = after.tail(post_days)
        no_close_break = bool((tail["close"] >= effective_line * (1.0 - 边界上沿接受容忍)).all())
        true_float_low = bool((tail["low"] > effective_line * (1.0 + 真实回踩触碰上浮容忍)).all())
        not_too_far = bool(min_tail_low <= effective_line * (1.0 + 强势悬空观察带宽))
        shrink = bool(bvol > 0 and 安全浮点(tail["volume"].median()) <= bvol * 0.85)
        not_expand = bool(bvol > 0 and 安全浮点(tail["volume"].median()) <= bvol * 1.05)
        small_body_ratio_float = float((tail["body_abs_pct"].abs() <= 0.035).sum()) / max(1, len(tail)) if "body_abs_pct" in tail.columns else 0.0
        small_body = bool(small_body_ratio_float >= 0.50)
        latest_strong = bool(latest_close >= effective_line * 1.012)
        if no_close_break and true_float_low and not_too_far and shrink and small_body and latest_strong and post_days <= 强势悬空接受最多天数 and boundary_width <= 强势悬空接受最大界宽:
            strong_no_pullback = True
            floating_grade = "A"
            best_score += 7.0
            reasons.append(f"强势悬空A：缩量小K横住，未真实回踩，最低离确认线{floating_distance_pct:.1f}%")
            best_name, best_level = "强势悬空A假设防守/有效确认线", max(effective_line, body_mid)
        elif no_close_break and true_float_low and not_too_far and not_expand and latest_strong:
            # B档仍是强，但不如A干净：可观察/轻仓，不给满承接权重。
            strong_no_pullback = True
            floating_grade = "B"
            best_score += 4.5
            reasons.append(f"强势悬空B：站稳确认线但缩量/小K不完美，最低离确认线{floating_distance_pct:.1f}%")
            best_name, best_level = "强势悬空B假设防守/有效确认线", max(effective_line, body_mid)
        elif no_close_break and true_float_low and min_tail_low > effective_line * (1.0 + 强势悬空观察带宽):
            reasons.append(f"强势悬空但离确认线{floating_distance_pct:.1f}%偏远，防追高")

    if after_below_effective > 0:
        best_score -= min(5.0, after_below_effective * 2.0); reasons.append(f"突破后曾跌回有效确认线{after_below_effective}次")
    if deep_pierce_repair:
        best_score -= 4.0; reasons.append(f"盘中深刺穿{max_pierce_pct:.1f}%")
    if len(post_all) >= 2:
        last_post = post_all.iloc[-1]; prev_post = post_all.iloc[-2]
        if 安全浮点(last_post.get("close")) > 安全浮点(prev_post.get("close")) and 安全浮点(last_post.get("close_pos")) >= 0.65 and 安全浮点(last_post.get("close")) >= effective_line * 1.003:
            best_score += 2.5; reasons.append("回踩/横盘后重新转强")

    score = 夹紧(best_score, 0.0, 15.0)
    close_break_repair = bool(after_below_effective == 1 and accepted_effective)
    clean_pullback = bool(trade_pullback_confirmed and not deep_pierce_repair and after_below_effective == 0 and accepted_effective)
    confirmed = bool(score >= 正式承接最低分 and accepted_effective and (clean_pullback or strong_no_pullback))
    typ = "回踩承接二买" if clean_pullback and score >= 12 else "强势悬空接受" if strong_no_pullback else "深刺穿修复观察" if deep_pierce_repair else "破线修复观察" if close_break_repair else "突破后接受" if confirmed else "承接一般" if score >= 5 else "未确认承接"
    support_is_trade = bool(clean_pullback)
    if clean_pullback:
        defense_mode = "真实回踩验证防守"
        position_mode = "正常正式候选/按结构防守执行"
        execution_grade = "回踩确认S/A"
        position_weight = 真实回踩执行权重
        defense_certainty = "已验证"
    elif strong_no_pullback:
        defense_mode = f"强势悬空{floating_grade}假设防守"
        position_mode = "强势轻仓确认/等待首次回踩升级" if floating_grade == "A" else "强势观察/极轻仓或等首次回踩"
        execution_grade = f"强势悬空{floating_grade}"
        position_weight = 强势悬空A执行权重 if floating_grade == "A" else 强势悬空B执行权重
        defense_certainty = "未回踩验证"
    else:
        defense_mode = "承接未确认"
        position_mode = "等待回踩确认或重新放量转强"
        execution_grade = "未确认"
        position_weight = 0.0
        defense_certainty = "未验证"

    return {
        "score": 四舍五入(score, 2), "type": typ, "detail": "；".join(reasons) or "有后续K线但未形成清晰回踩/接受证据",
        "support_price": 四舍五入(best_level or effective_line, 3), "support_type": best_name or "有效突破确认线",
        "below_line_count": int(below_main_count), "effective_below_count": int(after_below_effective),
        "break_body_mid": 四舍五入(body_mid, 3), "break_body_bottom": 四舍五入(body_bottom, 3),
        "acceptance_confirmed": confirmed, "acceptance_stage": "confirmed" if confirmed else "post_break_unconfirmed", "post_days": int(post_days),
        "support_is_trade_defense": support_is_trade, "trade_pullback_touched": bool(trade_pullback_touched), "trade_pullback_confirmed": bool(trade_pullback_confirmed),
        "clean_pullback_acceptance": clean_pullback, "strong_no_pullback_acceptance": bool(strong_no_pullback), "floating_acceptance_only": bool(strong_no_pullback and not clean_pullback),
        "deep_pierce_repair": bool(deep_pierce_repair), "close_break_repair": close_break_repair,
        "max_pierce_pct": 四舍五入(max_pierce_pct, 2), "effective_break_line": 四舍五入(effective_line, 3), "accepted_effective_line": accepted_effective,
        "fell_back_inside_boundary": bool(not accepted_effective), "floating_distance_from_line_pct": 四舍五入(floating_distance_pct, 2),
        "real_pullback_touch_tolerance_pct": 四舍五入(真实回踩触碰上浮容忍 * 100.0, 2), "floating_acceptance_zone_pct": 四舍五入(强势悬空观察带宽 * 100.0, 2),
        "defense_mode": defense_mode, "position_mode": position_mode, "defense_validated": bool(clean_pullback),
        "execution_grade": execution_grade, "position_weight": 四舍五入(position_weight, 2), "defense_certainty": defense_certainty,
        "floating_grade": floating_grade,
    }

def 评估资金行为(df: pd.DataFrame, bidx: int) -> Dict[str, Any]:
    d = 加基础指标(df)
    if d.empty or bidx <= 0 or bidx >= len(d):
        return {"score": 0.0, "type": "无", "detail": "资金样本不足", "volume_ratio": 0.0, "stall": False}
    b = d.iloc[bidx]
    pre = d.iloc[max(0, bidx - 20):bidx]
    med20 = 安全浮点(pre["volume"].median()) if not pre.empty else 0.0
    vol_ratio = 安全浮点(b.get("volume")) / med20 if med20 > 0 else 0.0
    bullish = 安全浮点(b.get("close")) > 安全浮点(b.get("open"))
    close_pos = 安全浮点(b.get("close_pos"))
    upper = 安全浮点(b.get("upper_shadow_ratio"))
    pct = 安全浮点(b.get("pct_chg"))
    body_abs_pct = 安全浮点(b.get("body_abs_pct"))
    stall = bool(vol_ratio >= 1.8 and (pct < 1.2 or close_pos < 0.55 or upper >= 0.42 or body_abs_pct < 0.008))
    reasons: List[str] = []
    if stall:
        score, typ = -8.0, "放量滞涨"
        reasons.append(f"高量低效{vol_ratio:.2f}倍")
    elif bullish and 1.8 <= vol_ratio <= 2.5 and close_pos >= 0.68:
        score, typ = 12.0, "标准倍量阳K"
        reasons.append(f"标准倍量{vol_ratio:.2f}倍")
    elif bullish and 1.2 <= vol_ratio <= 3.2 and close_pos >= 0.62:
        score, typ = 9.0, "健康放量"
        reasons.append(f"健康放量{vol_ratio:.2f}倍")
    elif bullish and 0.85 <= vol_ratio < 1.2 and close_pos >= 0.72:
        score, typ = 5.0, "平量强收"
        reasons.append(f"平量强收{vol_ratio:.2f}倍")
    elif vol_ratio > 4.5:
        score, typ = -4.0, "爆量分歧"
        reasons.append(f"爆量{vol_ratio:.2f}倍")
    else:
        score, typ = 2.0, "量能普通"
        reasons.append(f"量比{vol_ratio:.2f}")

    if bidx + 1 < len(d) and vol_ratio >= 1.5:
        n1 = d.iloc[bidx + 1]
        diff = abs(安全浮点(n1.get("volume")) / max(安全浮点(b.get("volume")), 1e-9) - 1.0)
        n1_bad = 安全浮点(n1.get("close")) < min(安全浮点(b.get("open")), 安全浮点(b.get("close"))) * 0.985 and 安全浮点(n1.get("close")) < 安全浮点(n1.get("open"))
        if diff <= 0.08 and not n1_bad:
            score += 3.0
            reasons.append(f"次日平量承接差{diff:.1%}")
    return {"score": 四舍五入(夹紧(score, -10, 15), 2), "type": typ, "detail": "；".join(reasons), "volume_ratio": 四舍五入(vol_ratio, 2), "stall": bool(stall)}


# ---------- 上方压力、交易定价 ----------
def _价格事件(window: pd.DataFrame, last_close: float, min_above_pct: float = 近压最小识别距离) -> List[Dict[str, Any]]:
    if window.empty or last_close <= 0:
        return []
    threshold = last_close * (1.0 + min_above_pct)
    events: List[Dict[str, Any]] = []
    for pos, (_, r) in enumerate(window.iterrows()):
        high = 安全浮点(r.get("high"))
        close = 安全浮点(r.get("close"))
        body_top = max(安全浮点(r.get("open")), close)
        volume = 安全浮点(r.get("volume"))
        date = r.get("date")
        if high <= 0 or close <= 0:
            continue
        for price, source in [(high, "最高价"), (body_top, "实体顶"), (close, "收盘价")]:
            if price >= threshold:
                events.append({"price": price, "row_pos": pos, "date": date, "volume": volume, "source": source})
    return sorted(events, key=lambda x: 安全浮点(x.get("price")))


def 扫描上方压力(df: pd.DataFrame, last_close: float, end_idx: int) -> Dict[str, Any]:
    """扫描当前价上方压力，并严格区分：真正价格发现、近压太近、正常目标压力。"""
    d = df.iloc[:max(1, min(end_idx, len(df)))].copy().reset_index(drop=True)
    if d.empty or last_close <= 0:
        return {"pressure_found": False, "target_reliable": False, "target_price": 0.0, "space_pct": 0.0, "pricing_mode": "压力样本不足", "target_type": "压力样本不足"}

    full_high = 安全浮点(d["high"].max()) if "high" in d.columns else 0.0
    near_threshold = last_close * (1.0 + 近压最小识别距离)
    min_target_threshold = last_close * (1.0 + 上方压力最小距离)

    # 只有历史最高价已经不在当前价上方时，才是真正价格发现。
    if full_high <= near_threshold:
        return {"pressure_found": False, "target_reliable": False, "target_price": 0.0, "space_pct": 0.0, "pricing_mode": "全历史价格发现", "target_type": "全历史上方无有效压力", "full_history_high": 四舍五入(full_high, 3)}

    all_above_events = _价格事件(d, last_close, min_above_pct=近压最小识别距离)
    target_events = [ev for ev in all_above_events if 安全浮点(ev.get("price")) >= min_target_threshold]

    # 当前价上方确实有压力，但空间不足最小赔率阈值：不能误判为价格发现。
    if not target_events:
        nearest = min((安全浮点(ev.get("price")) for ev in all_above_events), default=full_high)
        if nearest <= 0:
            nearest = full_high
        return {
            "pressure_found": True,
            "target_reliable": True,
            "target_price": 四舍五入(nearest, 3),
            "target_quality": "near",
            "space_pct": 四舍五入(涨幅百分比(nearest, last_close), 2),
            "pricing_mode": "上方压力太近",
            "target_type": "近端压力太近/赔率不足",
            "full_history_high": 四舍五入(full_high, 3),
            "hit_count": 1,
            "volume_hit_count": 0,
            "band_low": 四舍五入(nearest, 3),
            "band_high": 四舍五入(nearest, 3),
            "pressure_score": 1.0,
        }

    groups: List[List[Dict[str, Any]]] = []
    cur: List[Dict[str, Any]] = []
    base = 0.0
    for ev in target_events:
        price = 安全浮点(ev.get("price"))
        if not cur:
            cur = [ev]
            base = price
        elif base > 0 and abs(price - base) / base <= 上方压力带宽:
            cur.append(ev)
        else:
            groups.append(cur)
            cur = [ev]
            base = price
    if cur:
        groups.append(cur)

    vol_med = 安全浮点(d["volume"].median()) if "volume" in d.columns else 0.0
    scored: List[Dict[str, Any]] = []
    for group in groups:
        prices = [安全浮点(x.get("price")) for x in group if 安全浮点(x.get("price")) > 0]
        if not prices:
            continue
        rows = set(int(安全浮点(x.get("row_pos"))) for x in group)
        unique_hits = len(rows)
        volume_rows = set(int(安全浮点(x.get("row_pos"))) for x in group if vol_med > 0 and 安全浮点(x.get("volume")) >= vol_med * 1.30)
        volume_hits = len(volume_rows)
        body_close = sum(1 for x in group if str(x.get("source")) in {"实体顶", "收盘价"})
        pressure_score = unique_hits + volume_hits * 0.70 + body_close * 0.15
        quality = "strong" if unique_hits >= 4 or volume_hits >= 2 or pressure_score >= 5 else "valid" if unique_hits >= 2 or volume_hits >= 1 else "weak"
        target = min(prices)
        scored.append({
            "pressure_found": True,
            "target_reliable": quality in {"strong", "valid"},
            "target_price": 四舍五入(target, 3),
            "target_quality": quality,
            "space_pct": 四舍五入(涨幅百分比(target, last_close), 2),
            "hit_count": unique_hits,
            "volume_hit_count": volume_hits,
            "band_low": 四舍五入(min(prices), 3),
            "band_high": 四舍五入(max(prices), 3),
            "pressure_score": 四舍五入(pressure_score, 2),
            "full_history_high": 四舍五入(full_high, 3),
        })
    if not scored:
        return {"pressure_found": False, "target_reliable": False, "target_price": 0.0, "space_pct": 0.0, "pricing_mode": "压力未成线", "target_type": "压力分组无效", "full_history_high": 四舍五入(full_high, 3)}
    reliable = [x for x in scored if bool(x.get("target_reliable"))]
    picked = min(reliable, key=lambda x: 安全浮点(x.get("target_price"))) if reliable else min(scored, key=lambda x: 安全浮点(x.get("target_price")))
    picked["pricing_mode"] = "历史压力定价" if picked.get("target_reliable") else "弱压力参考"
    picked["target_type"] = "第一历史压力" if picked.get("target_reliable") else "第一弱压力参考"
    return picked



def 评估突破后摆动压力(after_window: pd.DataFrame, last_close: float) -> Dict[str, Any]:
    """突破后压力：只认“已确认”的新压力，并可参与重新定价。

    单点失败高点不再用“一天没收复”粗暴确认；至少满足：
    - 后续收盘确认失败次数达到阈值；
    - 最新收盘仍未收复；
    - 且高点K本身弱收盘/明显上影，或后续有放量回落/多日未收复。
    """
    base = {
        "pressure_found": False, "target_reliable": False, "target_price": 0.0, "space_pct": 0.0,
        "pricing_mode": "无已确认突破后摆动压力", "target_type": "无已确认突破后摆动压力",
        "hit_count": 0, "volume_hit_count": 0, "hard_block_eligible": False,
        "reprice_eligible": False, "pressure_source": "post_none", "confirm_detail": "未发现已确认突破后压力",
    }
    if after_window is None or after_window.empty or last_close <= 0 or len(after_window) < 2:
        return base
    aw = after_window.copy().reset_index(drop=True)
    confirmed = aw.iloc[:-1].copy().reset_index(drop=True)  # 排除最新K自己的上影，避免强势横住被误杀
    if confirmed.empty:
        return base

    candidates: List[Dict[str, Any]] = []

    # A. 多次共振压力：至少两根K线在同一上方价格带反复打到。
    events = _价格事件(confirmed, last_close, min_above_pct=近压最小识别距离)
    groups: List[List[Dict[str, Any]]] = []
    cur: List[Dict[str, Any]] = []
    base_price = 0.0
    for ev in events:
        price = 安全浮点(ev.get("price"))
        if price <= 0:
            continue
        if not cur:
            cur = [ev]; base_price = price
        elif base_price > 0 and abs(price - base_price) / base_price <= 上方压力带宽:
            cur.append(ev)
        else:
            groups.append(cur); cur = [ev]; base_price = price
    if cur:
        groups.append(cur)
    vol_med = 安全浮点(confirmed["volume"].median()) if "volume" in confirmed.columns else 0.0
    for group in groups:
        prices = [安全浮点(x.get("price")) for x in group if 安全浮点(x.get("price")) > 0]
        rows = set(int(安全浮点(x.get("row_pos"))) for x in group)
        if not prices or len(rows) < 突破后摆动压力最小共振次数:
            continue
        volume_rows = set(int(安全浮点(x.get("row_pos"))) for x in group if vol_med > 0 and 安全浮点(x.get("volume")) >= vol_med * 1.30)
        target = min(prices)
        candidates.append({
            "pressure_found": True, "target_reliable": True, "target_price": 四舍五入(target, 3),
            "space_pct": 四舍五入(涨幅百分比(target, last_close), 2),
            "pricing_mode": "突破后多次共振压力定价", "target_type": "突破后多次共振压力",
            "hit_count": len(rows), "volume_hit_count": len(volume_rows), "hard_block_eligible": True,
            "reprice_eligible": True, "pressure_source": "post_multi_resonance",
            "confirm_detail": f"突破后{len(rows)}根K线共振，带量{len(volume_rows)}次",
        })

    # B. 单点失败高点：需要高点K弱化证据 + 后续多次未收复/放量回落确认。
    min_fail_closes = max(突破后失败高点确认收盘数, 突破后失败高点最少确认收盘数)
    for pos, r in confirmed.iterrows():
        swing_high = 安全浮点(r.get("high"))
        if swing_high <= last_close * (1.0 + 近压最小识别距离):
            continue
        following = aw.iloc[int(pos) + 1:].copy().reset_index(drop=True)
        if following.empty:
            continue
        fail_closes = int((following["close"] < swing_high * (1.0 - 近压最小识别距离)).sum())
        latest_still_below = bool(last_close < swing_high * (1.0 - 近压最小识别距离))
        close_pos = 安全浮点(r.get("close_pos"))
        upper_shadow = 安全浮点(r.get("upper_shadow_ratio"))
        vol_ma = 安全浮点(r.get("vol_ma20"))
        weak_high_bar = bool(upper_shadow >= 突破后失败高点最小上影比例 or (0 < close_pos <= 突破后失败高点弱收盘阈值))
        following_bear = following[following["close"] < following["open"]].copy() if {"close", "open"}.issubset(following.columns) else pd.DataFrame()
        volume_reject = False
        if not following_bear.empty and "volume" in following_bear.columns:
            ref = vol_ma if vol_ma > 0 else 安全浮点(confirmed["volume"].median()) if "volume" in confirmed.columns else 0.0
            volume_reject = bool(ref > 0 and (following_bear["volume"] >= ref * 1.20).any())
        multi_fail = fail_closes >= min_fail_closes
        if latest_still_below and multi_fail and (weak_high_bar or volume_reject or fail_closes >= min_fail_closes + 1):
            # 若最新K强收盘且距离高点极近，视为重新冲击，不判失败。
            latest_close_pos = 安全浮点(aw.iloc[-1].get("close_pos")) if "close_pos" in aw.columns else 0.0
            if latest_close_pos >= 0.82 and 涨幅百分比(swing_high, last_close) <= 2.0:
                continue
            candidates.append({
                "pressure_found": True, "target_reliable": True, "target_price": 四舍五入(swing_high, 3),
                "space_pct": 四舍五入(涨幅百分比(swing_high, last_close), 2),
                "pricing_mode": "突破后失败高点定价", "target_type": "突破后单点失败高点",
                "hit_count": 1, "volume_hit_count": 1 if volume_reject else 0, "hard_block_eligible": True,
                "reprice_eligible": True, "pressure_source": "post_failed_high",
                "confirm_detail": f"高点后{fail_closes}次收盘未收复，上影{upper_shadow:.2f}/收盘位{close_pos:.2f}",
            })

    if not candidates:
        return base
    return min(candidates, key=lambda x: 安全浮点(x.get("target_price")))


def 评估突破日压力(df: pd.DataFrame, bidx: int, last_close: float) -> Dict[str, Any]:
    """突破日高点压力重定价。

    V11修正：距离突破日高点有3%以上空间，不能单独证明失败。
    只有突破K本身弱化（长上影/弱收盘/放量低效）并且后续确实未收复，才把突破日高点当压力；
    强突破后的正常回踩，高点只是目标，不参与硬拦。
    """
    base = {
        "pressure_found": False, "target_reliable": False, "target_price": 0.0, "space_pct": 0.0,
        "pricing_mode": "无突破日确认压力", "target_type": "无突破日确认压力",
        "hit_count": 0, "volume_hit_count": 0, "hard_block_eligible": False,
        "reprice_eligible": False, "pressure_source": "breakout_day_none", "confirm_detail": "突破日高点未构成确认压力",
    }
    if df is None or df.empty or bidx < 0 or bidx >= len(df) or last_close <= 0 or bidx >= len(df) - 1:
        return base
    d = df.copy().reset_index(drop=True)
    b = d.iloc[bidx]
    high = 安全浮点(b.get("high"))
    close = 安全浮点(b.get("close"))
    open_ = 安全浮点(b.get("open"))
    body_top = max(open_, close)
    close_pos = 安全浮点(b.get("close_pos"))
    upper_shadow = 安全浮点(b.get("upper_shadow_ratio"))
    vol = 安全浮点(b.get("volume"))
    vol_ma = 安全浮点(b.get("vol_ma20"))
    if high <= last_close * (1.0 + 近压最小识别距离):
        return base
    after = d.iloc[bidx + 1:].copy().reset_index(drop=True)
    recovered_high = bool((after["close"] >= high * (1.0 + 边界上沿突破容忍)).any()) if not after.empty else False
    if recovered_high:
        return base
    space_high = 涨幅百分比(high, last_close)
    weak_bar = bool(upper_shadow >= 突破日上影压力最小上影比例 or (0 < close_pos <= 突破后失败高点弱收盘阈值))
    volume_inefficient = bool(vol_ma > 0 and vol >= vol_ma * 1.35 and (close_pos < 0.70 or upper_shadow >= 0.18 or close <= open_))
    fail_closes = int((after["close"] < high * (1.0 - 近压最小识别距离)).sum()) if not after.empty else 0
    latest_still_below = bool(last_close < high * (1.0 - 近压最小识别距离))
    latest_charging = bool(not after.empty and 安全浮点(after.iloc[-1].get("close_pos")) >= 0.82 and space_high <= 2.0)

    # 只有“弱突破高点”才重定价；正常强突破回踩不因为没有立刻收复高点就被误杀。
    confirmed_weak_pressure = bool(latest_still_below and fail_closes >= 2 and (weak_bar or volume_inefficient or fail_closes >= 3) and not latest_charging)
    if not confirmed_weak_pressure:
        return {
            **base,
            "target_price": 四舍五入(high, 3),
            "space_pct": 四舍五入(space_high, 2),
            "pricing_mode": "突破日高点仅作目标参考",
            "target_type": "正常突破高点目标/非失败压力",
            "pressure_source": "breakout_day_target_reference",
            "confirm_detail": f"突破日高点{high:.2f}未收复，但K线未确认弱化；空间{space_high:.1f}%只作目标参考",
        }

    target = high
    source = "突破日弱高点未收复"
    # 实体顶只有在同样未收复且离当前更近时，才作为第一压力；否则不压低目标。
    if body_top > last_close * (1.0 + 近压最小识别距离) and body_top < high and not bool((after["close"] >= body_top * (1.0 + 边界上沿突破容忍)).any()):
        target = body_top
        source = "突破日弱实体顶/高点未收复"
    return {
        "pressure_found": True, "target_reliable": True, "target_price": 四舍五入(target, 3),
        "space_pct": 四舍五入(涨幅百分比(target, last_close), 2),
        "pricing_mode": "突破日弱高点压力定价", "target_type": source,
        "hit_count": 1, "volume_hit_count": 1 if volume_inefficient else 0,
        "hard_block_eligible": True, "reprice_eligible": True, "pressure_source": "breakout_day_pressure",
        "confirm_detail": f"突破日高点{high:.2f}后{fail_closes}次未收复，上影{upper_shadow:.2f}/收盘位{close_pos:.2f}",
    }


def 合并定价压力(pre_pressure: Dict[str, Any], breakout_pressure: Dict[str, Any], post_pressure: Dict[str, Any], last_close: float) -> Dict[str, Any]:
    """统一目标压力：取突破前、突破日、突破后已确认压力中的最近可靠压力。"""
    candidates: List[Dict[str, Any]] = []
    for label, src in [("pre", pre_pressure), ("breakout_day", breakout_pressure), ("post", post_pressure)]:
        if not isinstance(src, dict):
            continue
        target = 安全浮点(src.get("target_price"))
        if target <= last_close * (1.0 + 近压最小识别距离):
            continue
        if bool(src.get("target_reliable")) or bool(src.get("reprice_eligible")):
            item = dict(src)
            item["pressure_source"] = src.get("pressure_source", label)
            candidates.append(item)
    if candidates:
        picked = min(candidates, key=lambda x: 安全浮点(x.get("target_price")))
        source = str(picked.get("pressure_source", ""))
        # 价格发现后出现新压力时，必须切换成新压力定价，不能继续走移动止盈幻想目标。
        if str(pre_pressure.get("pricing_mode")) == "全历史价格发现" and source in {"breakout_day_pressure", "post_failed_high", "post_multi_resonance", "post"}:
            picked["pricing_mode"] = "价格发现后新压力定价"
            picked["target_type"] = str(picked.get("target_type", "突破后新压力"))
        elif source == "breakout_day_pressure":
            picked["pricing_mode"] = "突破日压力重定价"
        elif source.startswith("post"):
            picked["pricing_mode"] = str(picked.get("pricing_mode", "突破后新压力定价"))
        return picked
    return dict(pre_pressure)

def 评估交易定价(df: pd.DataFrame, bidx: int, line: float, support_price: float, line_info: Optional[Dict[str, Any]] = None, acceptance: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """用融合界结构防守位计算真实RR。

    V10.3：目标压力统一重定价：突破日前历史压力、突破日未收复高点、突破后已确认失败/共振压力，
    三者取最近可靠压力。价格发现遇到突破后新压力时自动切换为“新压力定价”，不再继续按无压力处理。
    """
    d = 加基础指标(df)
    if d.empty or bidx <= 0 or bidx >= len(d) or line <= 0:
        return {"score": 0.0, "ok": False, "detail": "交易定价样本不足", "defense_price": 0.0, "target_price": 0.0, "rr": 0.0}
    last = d.iloc[-1]
    b = d.iloc[bidx]
    last_close = 安全浮点(last.get("close"))
    if last_close <= 0:
        return {"score": 0.0, "ok": False, "detail": "最新价无效", "defense_price": 0.0, "target_price": 0.0, "rr": 0.0}

    info = line_info or {"line": line}
    acc = acceptance or {}
    floating_only = bool(acc.get("floating_acceptance_only") or (acc.get("strong_no_pullback_acceptance") and not acc.get("clean_pullback_acceptance")))
    clean_defense = bool(acc.get("clean_pullback_acceptance") and acc.get("support_is_trade_defense"))
    floating_grade = str(acc.get("floating_grade", "A" if floating_only else "无"))
    defense_mode = str(acc.get("defense_mode")) if acc.get("defense_mode") else ("真实回踩验证防守" if clean_defense else f"强势悬空{floating_grade}假设防守" if floating_only else "结构假设防守")
    position_mode = str(acc.get("position_mode")) if acc.get("position_mode") else ("正常正式候选/按结构防守执行" if clean_defense else "强势轻仓确认/等待首次回踩升级" if floating_only else "等待承接确认")
    execution_grade = str(acc.get("execution_grade")) if acc.get("execution_grade") else ("回踩确认S/A" if clean_defense else f"强势悬空{floating_grade}" if floating_only else "结构假设")
    position_weight = 安全浮点(acc.get("position_weight"), 真实回踩执行权重 if clean_defense else 强势悬空A执行权重 if floating_only and floating_grade == "A" else 强势悬空B执行权重 if floating_only else 结构假设执行权重)
    defense_certainty = str(acc.get("defense_certainty")) if acc.get("defense_certainty") else ("已验证" if clean_defense else "未回踩验证" if floating_only else "假设")
    br_stub = {"突破收盘": 安全浮点(b.get("close"))}
    boundary_state = 生成界状态(info, br_stub, last_close)
    effective_line = 安全浮点(boundary_state.get("有效突破确认线"), line) or line
    distance_line_pct = 涨幅百分比(last_close, line)
    distance_effective_pct = 涨幅百分比(last_close, effective_line) if effective_line > 0 else distance_line_pct

    if last_close < effective_line * (1.0 - 边界上沿接受容忍):
        defense_price = effective_line * (1.0 - 防守缓冲)
        return {
            "score": 0.0, "ok": False,
            "detail": f"最新收盘未接受有效突破确认线{effective_line:.2f}，仍在融合界内/下方，交易定价不过闸；距确认线{distance_effective_pct:.1f}%",
            "defense_price": 四舍五入(defense_price, 3), "defense_type": "有效突破确认线缓冲", "structure_key_price": 四舍五入(effective_line, 3),
            "defense_distance_pct": 四舍五入(涨幅百分比(last_close, defense_price), 2),
            "target_price": 0.0, "target_type": "未接受有效确认线不定价", "target_reliable": False, "pricing_mode": "跌回有效突破确认线下方",
            "space_pct": 0.0, "rr": 0.0, "distance_line_pct": 四舍五入(distance_line_pct, 2), "distance_effective_pct": 四舍五入(distance_effective_pct, 2),
            "confirm_condition": f"重新放量收盘站稳有效突破确认线{effective_line:.2f}并完成承接",
            "giveup_condition": f"继续收盘低于有效突破确认线{effective_line:.2f}，或跌破主界线{line:.2f}",
            "defense_candidates": [], "auxiliary_defense_refs": [], "pressure_detail": "未接受融合界，不做赔率测算",
            "pre_break_pressure_detail": "未测算", "breakout_day_pressure_detail": "未测算", "post_swing_pressure_detail": "未测算", "post_swing_pressure_blocks": False,
            "defense_mode": defense_mode, "position_mode": position_mode, "execution_grade": execution_grade,
            "position_weight": 四舍五入(position_weight, 2), "defense_certainty": defense_certainty, "defense_validated": False,
            "target_mode": "不定价", "trailing_stop_rule": "等待重新站稳确认线",
            "external_risk_note": "技术筛选未接入公告/财务/监管雷区数据",
            "pressure_source": "none",
        }

    body_bottom = min(安全浮点(b.get("open")), 安全浮点(b.get("close")))
    body_top = max(安全浮点(b.get("open")), 安全浮点(b.get("close")))
    body_mid = (body_top + body_bottom) / 2.0
    structural_candidates: List[Dict[str, Any]] = []

    def add_struct(name: str, raw_price: float, buffer: float, priority: int) -> None:
        raw = 安全浮点(raw_price)
        if raw > 0 and raw < last_close:
            structural_candidates.append({"name": name, "raw": raw, "defense": raw * (1.0 - buffer), "priority": priority})

    if support_price > 0 and support_price >= effective_line * 0.970:
        add_struct("后续融合界承接位", support_price, 0.012, 5)
    effective_defense_name = "强势悬空假设防守｜有效突破确认线" if floating_only else "有效突破确认线"
    add_struct(effective_defense_name, effective_line, 0.012, 4)
    if body_bottom > 0 and body_bottom >= effective_line * 0.970:
        add_struct("突破K实底", body_bottom, 0.012, 3)
    add_struct("核心线缓冲", line, 防守缓冲, 1)

    if structural_candidates:
        max_priority = max(int(x["priority"]) for x in structural_candidates)
        priority_group = [x for x in structural_candidates if int(x["priority"]) == max_priority]
        picked = max(priority_group, key=lambda x: 安全浮点(x.get("defense")))
        defense_price = 安全浮点(picked.get("defense"))
        defense_type = str(picked.get("name"))
        structure_key = 安全浮点(picked.get("raw"))
    else:
        defense_price, defense_type, structure_key = effective_line * (1.0 - 防守缓冲), "有效突破确认线缓冲", effective_line

    auxiliary_refs: List[str] = []
    for name, raw in [("突破K实体中位", body_mid), ("主核心线", line), ("MA10", 安全浮点(last.get("ma10"))), ("BBI", 安全浮点(last.get("bbi")) )]:
        if raw > 0 and raw < last_close:
            auxiliary_refs.append(f"{name}{raw:.2f}")

    risk_pct = 涨幅百分比(last_close, defense_price) if defense_price > 0 else 0.0
    pre_pressure = 扫描上方压力(d, last_close, end_idx=max(1, bidx))
    breakout_day_pressure = 评估突破日压力(d, bidx, last_close)
    after_window = d.iloc[bidx + 1:].copy().reset_index(drop=True)
    post_pressure = 评估突破后摆动压力(after_window, last_close)
    pressure = 合并定价压力(pre_pressure, breakout_day_pressure, post_pressure, last_close)

    target_price = 安全浮点(pressure.get("target_price"))
    target_reliable = bool(pressure.get("target_reliable"))
    pricing_mode = str(pressure.get("pricing_mode", "压力未识别"))
    pressure_source = str(pressure.get("pressure_source", "pre"))
    space_pct = 涨幅百分比(target_price, last_close) if target_price > 0 else 0.0
    rr = space_pct / risk_pct if risk_pct > 0 and target_price > 0 and target_reliable else 0.0
    post_break_gain_pct = 涨幅百分比(last_close, 安全浮点(b.get("close"))) if 安全浮点(b.get("close")) > 0 else 0.0
    recent20_for_pricing = 涨幅百分比(last_close, 安全浮点(d.tail(20).iloc[0].get("close"))) if len(d.tail(20)) >= 2 and 安全浮点(d.tail(20).iloc[0].get("close")) > 0 else 0.0

    post_target = 安全浮点(post_pressure.get("target_price"))
    post_space_pct = 涨幅百分比(post_target, last_close) if post_target > 0 else 0.0
    breakout_day_target = 安全浮点(breakout_day_pressure.get("target_price"))
    breakout_day_space_pct = 涨幅百分比(breakout_day_target, last_close) if breakout_day_target > 0 else 0.0
    new_pressure_space_pct = space_pct if pressure_source in {"breakout_day_pressure", "post_failed_high", "post_multi_resonance", "post"} else 999.0
    post_swing_blocks = bool(
        突破后摆动压力参与硬闸
        and target_price > 0 and target_reliable
        and pressure_source in {"breakout_day_pressure", "post_failed_high", "post_multi_resonance", "post"}
        and new_pressure_space_pct < max(正式最低空间, 突破后单点压力最小空间)
    )

    reasons: List[str] = [f"结构防守={defense_type}{structure_key:.2f}", f"有效确认线={effective_line:.2f}", f"防守模式={defense_mode}"]
    if floating_only:
        reasons.append("强势悬空：攻击强，但首次回踩确认线尚未实测")
    if auxiliary_refs:
        reasons.append("辅助参考：" + "/".join(auxiliary_refs[:4]))
    reasons.append(f"赔率目标采用{pricing_mode}")
    if breakout_day_target > 0:
        reasons.append(f"突破日压力：{breakout_day_target:.2f}/空间{breakout_day_space_pct:.1f}%/{breakout_day_pressure.get('confirm_detail', '')}")
    if post_target > 0:
        reasons.append(f"突破后摆动压力：{post_target:.2f}/空间{post_space_pct:.1f}%/{post_pressure.get('confirm_detail', '')}")
    if post_swing_blocks:
        reasons.append("突破日/突破后新压力太近，赔率硬闸不通过")

    score = 0.0
    if risk_pct <= 6.0:
        score += 7.0; reasons.append(f"防守距离{risk_pct:.1f}%")
    elif risk_pct <= 正式最大防守距离:
        score += 4.0; reasons.append(f"防守距离{risk_pct:.1f}%")
    else:
        reasons.append(f"防守距离{risk_pct:.1f}%偏远")

    if target_price > 0 and target_reliable:
        space_label = "新压力空间" if pressure_source in {"breakout_day_pressure", "post_failed_high", "post_multi_resonance", "post"} else "上方空间"
        if space_pct >= 18.0:
            score += 5.0; reasons.append(f"{space_label}{space_pct:.1f}%")
        elif space_pct >= 正式最低空间:
            score += 3.0; reasons.append(f"{space_label}{space_pct:.1f}%")
        else:
            reasons.append(f"第一有效压力近{space_pct:.1f}%")
        if rr >= 2.0:
            score += 6.0; reasons.append(f"RR={rr:.2f}")
        elif rr >= 正式最低RR:
            score += 3.5; reasons.append(f"RR={rr:.2f}")
        else:
            reasons.append(f"RR={rr:.2f}不足")
    elif pricing_mode == "全历史价格发现":
        if risk_pct <= 价格发现最大防守距离 and distance_effective_pct <= 价格发现最大距线 and post_break_gain_pct <= 价格发现突破后最大涨幅 and recent20_for_pricing <= 价格发现最大20日涨幅:
            score += 5.0; reasons.append("突破日前和突破后均无确认压力，按融合界防守和移动止盈管理")
        elif post_break_gain_pct > 价格发现突破后最大涨幅 or recent20_for_pricing > 价格发现最大20日涨幅:
            reasons.append(f"价格发现但突破后已涨{post_break_gain_pct:.1f}%/20日{recent20_for_pricing:.1f}%，防止末端追高")
        else:
            reasons.append("价格发现但距确认线/防守偏远")
    elif pricing_mode == "上方压力太近":
        reasons.append("上方压力太近，赔率不足，不按价格发现处理")
    else:
        reasons.append("上方压力不可靠，赔率不虚构")

    if 0 <= distance_effective_pct <= 8.0:
        score += 2.0; reasons.append(f"距确认线{distance_effective_pct:.1f}%")
    elif 8.0 < distance_effective_pct <= 12.0:
        score += 0.5; reasons.append(f"距确认线{distance_effective_pct:.1f}%略远")
    elif distance_effective_pct > 正式最大距线:
        score -= 4.0; reasons.append(f"距确认线{distance_effective_pct:.1f}%过远")

    historical_ok = bool(target_price > 0 and target_reliable and risk_pct <= 正式最大防守距离 and rr >= 正式最低RR and space_pct >= 正式最低空间 and distance_effective_pct <= 正式最大距线)
    price_discovery_ok = bool(target_price <= 0 and pricing_mode == "全历史价格发现" and risk_pct <= 价格发现最大防守距离 and distance_effective_pct <= 价格发现最大距线 and post_break_gain_pct <= 价格发现突破后最大涨幅 and recent20_for_pricing <= 价格发现最大20日涨幅)
    ok = bool((historical_ok or price_discovery_ok) and not post_swing_blocks)

    if floating_only:
        confirm_prefix = f"强势悬空接受，攻击强但防守未实测；首次回踩有效确认线{effective_line:.2f}不破可升级"
        giveup = f"收盘跌回有效突破确认线{effective_line:.2f}下方，或首次回踩无法收复确认线"
    else:
        confirm_prefix = f"已接受有效突破确认线{effective_line:.2f}"
        giveup = f"收盘跌破结构交易防守位{defense_price:.2f}，或跌回有效突破确认线{effective_line:.2f}下方"
    if target_price > 0 and target_reliable:
        confirm = f"{confirm_prefix}；后续守住结构防守位{defense_price:.2f}，并向第一有效压力{target_price:.2f}推进"
        target_mode = "突破后新压力目标" if pressure_source in {"breakout_day_pressure", "post_failed_high", "post_multi_resonance", "post"} else "历史压力目标"
        trailing_stop_rule = "到达第一压力前按结构防守；接近压力后看量价是否加速或减仓"
    elif pricing_mode == "全历史价格发现":
        confirm = f"{confirm_prefix}；全历史价格发现且突破后未形成新压力，沿MA10/BBI或移动止盈管理"
        target_mode = "价格发现移动止盈"
        trailing_stop_rule = 价格发现移动止盈主线
    else:
        confirm = f"{confirm_prefix}；上方压力不可靠，只看能否守住结构防守{defense_price:.2f}并继续放量拓展"
        target_mode = "弱压力/不虚构目标"
        trailing_stop_rule = "守结构防守位；不能放量拓展则放弃"
    external_risk_note = "技术筛选已过，不代表公告/财务/监管雷区通过；正式交易需外部雷区复核"

    return {
        "score": 四舍五入(夹紧(score, -8, 20), 2), "ok": bool(ok), "detail": "；".join(reasons),
        "defense_price": 四舍五入(defense_price, 3), "defense_type": defense_type, "structure_key_price": 四舍五入(structure_key, 3),
        "defense_distance_pct": 四舍五入(risk_pct, 2), "target_price": 四舍五入(target_price, 3), "target_type": pressure.get("target_type", ""),
        "target_reliable": bool(target_reliable), "pricing_mode": pricing_mode, "space_pct": 四舍五入(space_pct, 2), "rr": 四舍五入(rr, 2),
        "distance_line_pct": 四舍五入(distance_line_pct, 2), "distance_effective_pct": 四舍五入(distance_effective_pct, 2),
        "post_break_gain_pct": 四舍五入(post_break_gain_pct, 2), "post_swing_pressure_blocks": bool(post_swing_blocks),
        "confirm_condition": confirm, "giveup_condition": giveup, "defense_candidates": structural_candidates, "auxiliary_defense_refs": auxiliary_refs,
        "defense_mode": defense_mode, "position_mode": position_mode, "defense_validated": bool(clean_defense),
        "execution_grade": execution_grade, "position_weight": 四舍五入(position_weight, 2), "defense_certainty": defense_certainty,
        "target_mode": target_mode, "trailing_stop_rule": trailing_stop_rule, "recent20_for_pricing_pct": 四舍五入(recent20_for_pricing, 2),
        "external_risk_note": external_risk_note, "pressure_source": pressure_source,
        "pressure_detail": f"最终{pressure.get('target_type', '')}｜{pressure.get('target_price', 0)}｜模式{pricing_mode}｜共振{pressure.get('hit_count', 0)}｜带量{pressure.get('volume_hit_count', 0)}",
        "pre_break_pressure_detail": f"{pre_pressure.get('target_type', '')}｜{pre_pressure.get('target_price', 0)}｜{pre_pressure.get('pricing_mode', '')}",
        "breakout_day_pressure_detail": f"{breakout_day_pressure.get('target_type', '')}｜{breakout_day_pressure.get('target_price', 0)}｜{breakout_day_pressure.get('pricing_mode', '')}",
        "post_swing_pressure_detail": f"{post_pressure.get('target_type', '')}｜{post_pressure.get('target_price', 0)}｜{post_pressure.get('pricing_mode', '')}｜空间{post_space_pct:.1f}%",
    }

def 评估风险反证(df: pd.DataFrame, bidx: int, line: float, fund: Dict[str, Any], trade: Dict[str, Any], line_info: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    d = 加基础指标(df)
    if d.empty or bidx <= 0 or line <= 0:
        return {"penalty": 40.0, "block": True, "level": "高", "detail": "风险样本不足"}
    info = line_info or {"line": line}
    boundary_state = 生成界状态(info, {"突破收盘": 安全浮点(d.iloc[bidx].get("close"))}, 安全浮点(d.iloc[-1].get("close")))
    effective_line = 安全浮点(boundary_state.get("有效突破确认线"), line) or line
    post = d.iloc[bidx:].copy().reset_index(drop=True)
    last = d.iloc[-1]
    penalty = 0.0; block = False; reasons: List[str] = []
    below_effective = int((post["close"] < effective_line * (1.0 - 边界上沿接受容忍)).sum())
    last3_below_effective = int((post.tail(3)["close"] < effective_line * (1.0 - 边界上沿接受容忍)).sum())
    below_main = int((post["close"] < line * 0.992).sum())
    if 安全浮点(last.get("close")) < effective_line * (1.0 - 边界上沿接受容忍 * 1.4) or last3_below_effective >= 2:
        penalty += 35.0; block = True; reasons.append("突破失败跌回有效确认线/融合界内")
    elif below_effective > 0:
        penalty += min(16.0, below_effective * 4.0); reasons.append(f"突破后跌回有效确认线{below_effective}次")
    if below_main > 0:
        penalty += min(8.0, below_main * 2.0); reasons.append(f"跌回主核心线{below_main}次")
    last20 = d.tail(20).copy()
    long_bear = (last20["close"] < last20["open"]) & (((last20["open"] - last20["close"]) / last20["close"].shift(1).replace(0, np.nan)) >= 0.035) & (last20["volume"] >= last20["vol_ma20"].fillna(last20["volume"].median()) * 1.35)
    lb_cnt = int(long_bear.sum())
    if lb_cnt >= 2:
        penalty += 12.0; reasons.append(f"近20日放量长阴{lb_cnt}次")
    elif lb_cnt == 1:
        penalty += 5.0; reasons.append("近20日有放量长阴")
    if bool(fund.get("stall")):
        penalty += 18.0; reasons.append("放量滞涨")
    recent20_pct = 涨幅百分比(安全浮点(d.iloc[-1].get("close")), 安全浮点(last20.iloc[0].get("close"))) if len(last20) >= 2 else 0.0
    if recent20_pct > 过热20日涨幅:
        penalty += 8.0; reasons.append(f"近20日涨幅{recent20_pct:.1f}%过热")
    max_post_high = 安全浮点(post["high"].max()) if not post.empty else 0.0
    drawdown = 涨幅百分比(安全浮点(last.get("close")), max_post_high) if max_post_high > 0 else 0.0
    if drawdown < -12.0:
        penalty += 7.0; reasons.append(f"突破后回撤{drawdown:.1f}%")
    if 安全浮点(trade.get("defense_distance_pct")) > 12.0:
        penalty += 8.0; reasons.append("防守距离过远")
    amount_state = 成交额20日状态(d)
    amount20_used = 安全浮点(amount_state.get("amount20"))
    if bool(amount_state.get("low")):
        penalty += 6.0; reasons.append(f"成交额不足{amount20_used/1e8:.2f}亿")
    level = "高" if block or penalty >= 25 else "中" if penalty >= 10 else "低" if penalty > 0 else "无"
    return {"penalty": 四舍五入(夹紧(penalty, 0, 45), 2), "block": bool(block), "level": level, "detail": "；".join(reasons) or "无明显风险反证", "recent20_pct": 四舍五入(recent20_pct, 2), "post_drawdown_pct": 四舍五入(drawdown, 2), "below_line_count": below_main, "below_effective_count": below_effective}

def 核心线等级分(line_type: str, info: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    historical_hit = bool(context.get("historical_hit")); trigger_hit = bool(context.get("trigger_hit"))
    historical_price = 安全浮点(context.get("historical_price")); trigger_price = 安全浮点(context.get("trigger_price"))
    pair_dist = abs(historical_price - trigger_price) / max(historical_price, trigger_price, 1e-9) * 100.0 if historical_price > 0 and trigger_price > 0 else 999.0
    hit = int(安全浮点(info.get("effective_resonance_count")))
    vol_hit = int(安全浮点(info.get("volume_resonance_count")))
    vol_quality = 安全浮点(info.get("volume_quality_score"))
    standard = 安全浮点(info.get("standard_double_volume_touch_count")); highq = 安全浮点(info.get("high_quality_double_volume_touch_count")); stall = 安全浮点(info.get("stall_volume_touch_count"))
    net = 安全浮点(info.get("net_score")); width = 安全浮点(info.get("boundary_band_width_pct"))
    bonus_raw = (hit - 3) * 0.30 + vol_quality * 0.55 + standard * 0.55 + highq * 0.80 + max(0.0, net) * 0.035 - stall * 0.85 - max(0.0, width - 压力带理想最大宽度) * 0.25
    bonus = min(4.0, max(-2.0, bonus_raw))
    if historical_hit and trigger_hit and pair_dist <= 5.0:
        score = 22.0 + min(3.0, bonus + 1.0); typ = "历史线+近500日日线触发线双线共振突破"; detail = f"双线共振，距离{pair_dist:.1f}%"
    elif "历史" in line_type:
        score = 18.0 + bonus; typ = "历史核心线突破"; detail = f"大周期核心界，共振{hit}次/带量质量{vol_quality:.1f}/标准倍量{standard:.0f}/高质量{highq:.0f}"
    elif "500" in line_type or "近" in line_type:
        score = 14.5 + min(3.5, bonus); typ = "近500日日线触发线突破"; detail = f"近端触发线，共振{hit}次/带量质量{vol_quality:.1f}"
    else:
        score = 12.0 + min(3.0, bonus); typ = "普通核心线突破"; detail = f"普通线型，共振{hit}次/带量质量{vol_quality:.1f}"
    state = str(info.get("acceptance_state", info.get("current_state", "")))
    if "假突破" in state:
        score -= 1.5; detail += "；有假突破记忆，要求更强确认"
    if "反抽失败" in state:
        score -= 1.2; detail += "；反抽失败线，突破要求更严格"
    entity_accept_count = 安全浮点(info.get("entity_accept_count"))
    if ("压力转支撑" in state and "接受" in state) or "支撑接受" in state:
        score += 0.6; detail += "；压力转支撑接受，历史核心线状态改善"
    elif (("实体" in state and "接受" in state) or entity_accept_count > 0) and ("跌回" in state or "反压" in state or "跌破" in state):
        score -= 1.4; detail += "；实体接受后又跌回/反压，要求重新突破确认"
    elif ("实体" in state and "接受" in state) or entity_accept_count > 0:
        score -= 0.3; detail += "；该线有实体接受记录，仅作为状态提示不否定核心线"
    return {"score": 四舍五入(夹紧(score, 0, 25), 2), "type": typ, "detail": detail, "dual_line_distance_pct": 四舍五入(pair_dist, 2)}


def 加权组件(value: Any, raw_max: float, target: float) -> float:
    return 四舍五入(夹紧(value, 0, raw_max) / raw_max * target, 2) if raw_max > 0 else 0.0


def 剥离未来标签(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """实盘明细去掉 label_* 未来字段，避免后续自学习/排序误用未来函数。"""
    out: List[Dict[str, Any]] = []
    for r in rows:
        out.append({k: v for k, v in r.items() if not str(k).startswith("label_")})
    return out


def 仅未来标签(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """回测标签单独输出，明确与实盘特征/决策字段隔离。"""
    out: List[Dict[str, Any]] = []
    for r in rows:
        z = {k: r.get(k) for k in ["代码", "标准代码", "名称", "突破日期", "核心线", "有效突破确认线", "确认层级", "输出分层", "是否正式", "总分"] if k in r}
        z.update({k: v for k, v in r.items() if str(k).startswith("label_")})
        if any(str(k).startswith("label_") for k in z):
            out.append(z)
    return out


def 深度评估(code: str, df: pd.DataFrame, line_info: Dict[str, Any], br: Dict[str, Any], line_type: str, context: Dict[str, Any]) -> Dict[str, Any]:
    d = 加基础指标(df)
    line_info = 规范化融合界字段(line_info)
    L = 安全浮点(line_info.get("line"))
    if d.empty or L <= 0:
        return {"总分": 0.0, "等级": "D", "是否正式": False, "状态": "剔除｜数据不足或未突破"}
    bidx = int(安全浮点(br.get("breakout_idx"), -1))
    if bidx <= 0 or bidx >= len(d):
        return {"总分": 0.0, "等级": "D", "是否正式": False, "状态": "剔除｜突破日期异常"}

    if not br.get("hit") and br.get("pre_hit"):
        last_close = 安全浮点(d.iloc[-1].get("close"))
        boundary_state = 生成界状态(line_info, br, last_close)
        amount_state = 成交额20日状态(d)
        amount20 = 安全浮点(amount_state.get("amount20"))
        base_risk = 基础硬风险检查(code, d)
        line_score = 核心线等级分(line_type, line_info, context)
        line_component = 夹紧(line_score.get("score"), 0, 25)
        breakout_component = 加权组件(br.get("突破质量分"), 40, 18)
        risk_component = 0.0 if bool(base_risk.get("block")) else 4.0 if amount_state.get("observe_low") else 6.0
        effective_for_prebreak = 安全浮点(boundary_state.get("有效突破确认线"), L)
        distance_to_confirm = 涨幅百分比(effective_for_prebreak, last_close) if effective_for_prebreak > 0 and last_close > 0 else 999.0
        # 预突破单独降噪：离有效确认线太远的，只进后台事件，不抢观察位。
        prebreak_proximity_bonus = 8.0 if 0 <= distance_to_confirm <= 2.0 else 5.0 if distance_to_confirm <= 预突破报告最大距确认线 else 0.0
        score = 四舍五入(夹紧(line_component + breakout_component + risk_component + prebreak_proximity_bonus, 0, 66), 2)
        if distance_to_confirm > 预突破报告最大距确认线:
            score = min(score, 预突破远离确认线封顶分)
        event_labels = 生成回测标签(d, bidx, line_info)
        event_stage = str(br.get("event_stage", "带内突破/预突破"))
        if last_close < L * (1.0 - 边界上沿接受容忍):
            event_stage = "预突破失败/跌回核心线下方"
        elif bool(boundary_state.get("是否最新接受有效突破确认线")) and event_stage == "带内突破/预突破":
            event_stage = "最新已越过确认线但缺正式突破K"
        hard_reason = f"{event_stage}｜" + str(br.get("reason", "带内预突破，尚未打穿融合界"))
        if distance_to_confirm > 预突破报告最大距确认线:
            hard_reason += f"；距有效确认线{distance_to_confirm:.1f}%偏远，降噪不进观察"
        if bool(base_risk.get("block")):
            hard_reason += "；" + str(base_risk.get("detail"))
        if amount_state.get("formal_low"):
            hard_reason += "；正式成交额不足/口径不可靠"
        prebreak_observe = (
            not bool(base_risk.get("block"))
            and not bool(amount_state.get("observe_low"))
            and score >= 观察最低分
            and distance_to_confirm <= 预突破报告最大距确认线
            and 安全浮点(br.get("突破质量分"), 安全浮点(br.get("quality"))) >= 预突破观察最低质量分
        )
        output_bucket = "观察候选" if prebreak_observe else "事件记录"
        state_prefix = "临界预突破观察｜" if prebreak_observe else "事件记录｜"
        grade = "B" if score >= 62 else "C" if score >= 55 else "D"
        return {
            "最新收盘": 四舍五入(last_close, 3), "等级": grade, "总分": score, "是否正式": False,
            "状态": state_prefix + hard_reason, "输出分层": output_bucket,
            "界下沿": boundary_state.get("界下沿", 0), "界上沿": boundary_state.get("界上沿", 0), "界宽%": boundary_state.get("界宽%", 0),
            "核心确认线": boundary_state.get("核心确认线", 0), "交易确认线": boundary_state.get("交易确认线", 0), "硬确认线": boundary_state.get("硬确认线", 0),
            "有效突破确认线": boundary_state.get("有效突破确认线", 0), "是否突破边界上沿": False, "是否突破交易确认线": bool(boundary_state.get("是否突破交易确认线")), "是否突破硬确认线": bool(boundary_state.get("是否突破硬确认线")), "是否突破融合界上沿": False,
            "是否最新接受交易确认线": bool(boundary_state.get("是否最新接受交易确认线")), "是否最新接受有效突破确认线": bool(boundary_state.get("是否最新接受有效突破确认线")), "是否跌回有效确认线下": bool(boundary_state.get("是否跌回有效确认线下")), "是否跌回融合界内部": bool(boundary_state.get("是否跌回融合界内部")),
            "确认路径": event_stage, "正式候选类别": "事件记录",
            "界宽质量": boundary_state.get("界宽质量", ""), "界极宽硬拒": bool(boundary_state.get("界极宽硬拒")),
            "融合界下沿": boundary_state.get("融合界下沿", 0), "融合界上沿": boundary_state.get("融合界上沿", 0), "当前界状态": event_stage,
            "界角色": line_info.get("boundary_role", ""), "界置信度": line_info.get("boundary_confidence", ""), "界置信度分": line_info.get("boundary_confidence_score", 0),
            "自适应贴线容差": line_info.get("line_tolerance", 0), "自适应边界带容差": line_info.get("band_tolerance", 0),
            "VBP筹码带": f"{line_info.get('vbp_band_low', 0)}-{line_info.get('vbp_band_high', 0)}", "VBP峰值价": line_info.get("vbp_peak_price", 0),
            "VBP重合比例": line_info.get("vbp_overlap_ratio", 0), "VBP支持分": line_info.get("vbp_support_score", 0),
            "带量质量分": line_info.get("volume_quality_score", 0), "温和放量共振": line_info.get("mild_volume_touch_count", 0),
            "健康放量共振": line_info.get("healthy_volume_touch_count", 0), "标准倍量共振": line_info.get("standard_double_volume_touch_count", 0),
            "高质量倍量共振": line_info.get("high_quality_double_volume_touch_count", 0), "滞涨放量触线": line_info.get("stall_volume_touch_count", 0),
            "最低有效边界敏感性": bool(line_info.get("lowest_valid_boundary")), "敏感性摘要": line_info.get("sensitivity_summary", ""),
            "边界密度": line_info.get("price_cluster_density", 0), "边界宽度质量": line_info.get("boundary_width_quality", ""),
            "边界状态细分": line_info.get("acceptance_state", line_info.get("current_state", "")), "假突破记忆次数": line_info.get("false_breakout_count", 0),
            "反抽失败次数": line_info.get("failed_retest_count", 0), "硬拒原因": hard_reason, "基础风险": base_risk.get("detail", ""), "基础风险等级": base_risk.get("level", ""),
            "预突破向上需突破%": 四舍五入(max(0.0, distance_to_confirm), 2), "已高于确认线%": 四舍五入(max(0.0, 涨幅百分比(last_close, effective_for_prebreak)), 2) if effective_for_prebreak > 0 else 0.0,
            "预突破距确认线%": 四舍五入(distance_to_confirm, 2), "预突破降噪封顶": bool(distance_to_confirm > 预突破报告最大距确认线),
            "成交额20日": 四舍五入(amount20, 2), "成交额质量": amount_state.get("quality", ""), "成交额说明": amount_state.get("detail", ""),
            "成交额可靠": bool(amount_state.get("reliable")), "成交额正式可用": not bool(amount_state.get("formal_low")), "成交额口径": amount_state.get("amount_basis", ""),
            "主评测线类型": line_type, "核心线级别分": 四舍五入(line_component, 2), "突破K分": 四舍五入(breakout_component, 2),
            "承接分": 0.0, "空间RR分": 0.0, "风险分": 四舍五入(risk_component, 2), "上下文分": 0.0,
            "核心线级别说明": line_score.get("detail", ""), "突破后接受": event_stage + "，只记录冲关/越线状态，不视为正式破界承接",
            "资金行为": "预突破事件，资金只作为突破K质量参考", "交易定价": "未打穿有效确认线，不做正式RR定价",
            "风险反证": "预突破事件未进入正式风险定价", "上下文说明": "核心线突破预警保留，等待打穿融合界上沿",
            "交易防守位": 0.0, "防守位类型": "未完成交易确认不设交易防守", "防守距离%": 0.0, "距有效确认线%": 四舍五入(涨幅百分比(last_close, effective_for_prebreak), 2) if effective_for_prebreak > 0 else 0.0,
            "突破后摆动压力硬拦": False, "上方目标/压力": 0.0, "上方空间%": 0.0, "RR": 0.0, "交易定价过闸": False,
            "防守模式": "未破界不定防守", "执行模式": "预警跟踪/等待打穿有效确认线", "执行等级": "预突破事件", "建议仓位权重": 0.0, "防守确定性": "未破界", "真实防守验证": False,
            "目标模式": "未破界不设目标", "移动止盈规则": "等待打穿确认线后再定", "外部雷区提示": "技术预警未接入公告/财务/监管雷区数据",
            "确认条件": f"放量收盘打穿有效突破确认线{boundary_state.get('有效突破确认线', 0)}，再观察是否接受",
            "放弃条件": f"重新跌回核心线{L:.2f}下方，或放量冲高回落不再收复",
            "风险等级": "事件", "风险扣分": 0, "20日涨幅%": 0, "突破后回撤%": 0, "突破后跌回线下次数": 0,
            "突破后天数": int(len(d) - 1 - bidx), "承接是否确认": False, "承接阶段": "pre_break_inside_boundary",
            "承接支撑可作防守": False, "是否真实回踩结构位": False, "强势悬空未回踩防守": False, "干净回踩承接": False,
            "强势悬空接受": False, "深刺穿修复": False, "破线修复": False, "最大刺穿幅度%": 0, "是否触碰过交易结构位": False,
            "突破前压力明细": "未测算", "突破后摆动压力明细": "未测算",
            **event_labels,
        }

    if not br.get("hit"):
        return {"总分": 0.0, "等级": "D", "是否正式": False, "状态": "剔除｜数据不足或未突破"}

    line_score = 核心线等级分(line_type, line_info, context)
    fund = 评估资金行为(d, bidx)
    acceptance = 评估突破后接受(d, bidx, L, line_info)
    trade_support_price = 安全浮点(acceptance.get("support_price")) if (bool(acceptance.get("acceptance_confirmed")) and bool(acceptance.get("support_is_trade_defense"))) else 0.0
    trade = 评估交易定价(d, bidx, L, trade_support_price, line_info=line_info, acceptance=acceptance)
    risk = 评估风险反证(d, bidx, L, fund, trade, line_info=line_info)
    base_risk = 基础硬风险检查(code, d)
    event_labels = 生成回测标签(d, bidx, line_info)

    # 破界正式100分：核心线25 + 突破K27 + 接受15 + 空间/RR12 + 风险8 + 位置/活跃13。
    line_component = 夹紧(line_score.get("score"), 0, 25)
    breakout_component = 加权组件(br.get("quality"), 40, 27)
    accept_component = 夹紧(acceptance.get("score"), 0, 15)

    # 空间/RR直接按交易定价压缩到12分。
    trade_score = 安全浮点(trade.get("score"))
    space_component = 加权组件(max(0.0, trade_score), 20, 12)

    risk_penalty = 安全浮点(risk.get("penalty"))
    if bool(risk.get("block")):
        risk_component = 0.0
    elif risk_penalty >= 25:
        risk_component = 1.0
    elif risk_penalty >= 10:
        risk_component = 4.0
    elif risk_penalty > 0:
        risk_component = 6.0
    else:
        risk_component = 8.0

    last_close = 安全浮点(d.iloc[-1].get("close"))
    boundary_state = 生成界状态(line_info, br, last_close)
    distance_line_pct = 安全浮点(trade.get("distance_effective_pct"), 安全浮点(trade.get("distance_line_pct")))
    amount_state = 成交额20日状态(d)
    amount20 = 安全浮点(amount_state.get("amount20"))
    external_risk_state = base_risk.get("external_risk", {}) if isinstance(base_risk.get("external_risk", {}), dict) else {}
    external_available = bool(external_risk_state.get("available"))
    fusion_confirm_path = bool(boundary_state.get("是否突破融合界上沿")) and bool(boundary_state.get("是否最新接受融合界上沿"))
    core_early_path = (
        正式允许主核心线回踩确认
        and str(br.get("确认层级", "")) == "主核心线第一层突破"
        and bool(boundary_state.get("是否突破交易确认线"))
        and bool(boundary_state.get("是否最新接受交易确认线"))
        and not bool(boundary_state.get("是否突破融合界上沿"))
    )
    hard_confirm_path = bool(boundary_state.get("是否突破硬确认线")) and bool(boundary_state.get("是否最新接受硬确认线"))
    if fusion_confirm_path:
        confirm_path_name = "融合确认正式"
    elif core_early_path:
        confirm_path_name = "主核心线早期回踩确认"
    elif hard_confirm_path:
        confirm_path_name = "硬确认线确认"
    else:
        confirm_path_name = "确认不足"
    recent20 = 安全浮点(risk.get("recent20_pct"))
    days_since = len(d) - 1 - bidx
    context_score = 0.0
    context_reasons: List[str] = []
    if 0 <= distance_line_pct <= 6:
        context_score += 4.0; context_reasons.append(f"距有效确认线{distance_line_pct:.1f}%舒服")
    elif 6 < distance_line_pct <= 12:
        context_score += 2.0; context_reasons.append(f"距有效确认线{distance_line_pct:.1f}%可接受")
    if 2 <= days_since <= 8:
        context_score += 3.0; context_reasons.append(f"突破后{days_since}日处于确认窗口")
    elif days_since <= 1:
        context_score += 1.5; context_reasons.append("突破初期")
    elif 9 <= days_since <= 20:
        context_score += 1.5; context_reasons.append(f"突破后{days_since}日仍在观察窗口")
    if bool(amount_state.get("reliable")) and amount20 >= 1.5e8:
        context_score += 2.5; context_reasons.append(f"成交额{amount20/1e8:.1f}亿")
    elif bool(amount_state.get("reliable")) and amount20 >= 最低成交额20日:
        context_score += 1.5; context_reasons.append(f"成交额{amount20/1e8:.1f}亿")
    elif not bool(amount_state.get("reliable")) and not bool(amount_state.get("observe_low")):
        context_reasons.append("成交额估算可观察但不加正式流动性分")
    if recent20 <= 15:
        context_score += 2.0; context_reasons.append(f"20日涨幅{recent20:.1f}%未过热")
    elif recent20 <= 过热20日涨幅:
        context_score += 1.0; context_reasons.append(f"20日涨幅{recent20:.1f}%可接受")
    if 安全浮点(fund.get("score")) >= 9:
        context_score += 1.5; context_reasons.append(str(fund.get("type")))
    if 安全浮点(line_info.get("vbp_support_score")) > 0:
        context_score += min(2.0, 安全浮点(line_info.get("vbp_support_score")) * 0.25); context_reasons.append(f"VBP筹码带重合{安全浮点(line_info.get('vbp_overlap_ratio')):.2f}")
    if bool(line_info.get("lowest_valid_boundary")):
        context_score += 0.8; context_reasons.append("最低有效边界敏感性确认")
    context_component = 夹紧(context_score, 0, 13)

    raw_total = line_component + breakout_component + accept_component + space_component + risk_component + context_component
    score = 四舍五入(夹紧(raw_total, 0, 100), 2)

    hard_reject_reasons: List[str] = []
    if bool(base_risk.get("block")):
        hard_reject_reasons.append(str(base_risk.get("detail")))
    if bool(risk.get("block")):
        hard_reject_reasons.append(str(risk.get("detail")))
    if 正式必须外部雷区已接入 and not external_available:
        hard_reject_reasons.append("外部公告/财务/监管雷区字段未接入：不能进入最终正式买入池")
    if not bool(trade.get("ok")):
        hard_reject_reasons.append("交易定价未过闸")
    if not bool(acceptance.get("acceptance_confirmed")) or 安全浮点(acceptance.get("score")) < 正式承接最低分:
        hard_reject_reasons.append("突破后尚未确认承接")
    if not (bool(acceptance.get("clean_pullback_acceptance")) or bool(acceptance.get("strong_no_pullback_acceptance"))):
        hard_reject_reasons.append("未形成干净回踩承接或强势悬空接受")
    if bool(acceptance.get("strong_no_pullback_acceptance")) and not bool(acceptance.get("clean_pullback_acceptance")) and not 强势悬空进入正式池:
        hard_reject_reasons.append("强势悬空未真实回踩验证防守，不进入正式买入池；仅作轻仓确认/观察")
    elif bool(acceptance.get("strong_no_pullback_acceptance")) and str(acceptance.get("floating_grade")) == "B":
        hard_reject_reasons.append("强势悬空B档未完成缩量小K标准，先观察等待首次回踩或再放量升级")
    if not bool(br.get("交易可追", True)) and not bool(br.get("pre_hit")):
        hard_reject_reasons.append("一字涨停/不可追突破只记录事件")
    if 安全浮点(br.get("突破量比")) < 普通突破最小量比 and not bool(br.get("涨停特殊处理")):
        hard_reject_reasons.append("突破量能不足")
    if bool(amount_state.get("formal_low")):
        hard_reject_reasons.append("正式成交额不足/不可靠")
    if bool(amount_state.get("formal_block")) and not bool(amount_state.get("reliable")):
        hard_reject_reasons.append("成交额口径缺失或单位不确定，最多观察不进正式")

    if 正式必须突破边界带上沿:
        if not (fusion_confirm_path or core_early_path or hard_confirm_path):
            hard_reject_reasons.append("未完成可执行确认路径：需融合上沿确认，或允许的主核心线早期回踩确认")
        if str(br.get("确认层级", "")) == "主核心线第一层突破" and not 正式允许主核心线回踩确认 and not fusion_confirm_path:
            hard_reject_reasons.append("配置禁止主核心线早期确认，必须等待融合上沿升级")
    if bool(boundary_state.get("是否跌回有效确认线下")):
        hard_reject_reasons.append("突破后跌回交易确认线下")
    if fusion_confirm_path and bool(boundary_state.get("是否跌回融合界内部")):
        hard_reject_reasons.append("突破后跌回融合界内部")
    width_for_path = 安全浮点(boundary_state.get("交易界宽%" if core_early_path else ("融合界宽%" if fusion_confirm_path else "界宽%")))
    if width_for_path >= 压力带极宽硬拒宽度:
        hard_reject_reasons.append("当前确认路径对应压力带极宽，无法按一次性破界处理")
    elif width_for_path >= 压力带正式最大宽度 and not (bool(acceptance.get("clean_pullback_acceptance")) or bool(acceptance.get("strong_no_pullback_acceptance"))):
        hard_reject_reasons.append("当前确认路径对应压力带偏宽，必须先完成带内吸收或有效承接")
    if bool(trade.get("post_swing_pressure_blocks")):
        hard_reject_reasons.append("突破后摆动压力太近，赔率不足")
    daily_major_confirmed = bool(context.get("historical_hit")) and 安全浮点(line_score.get("dual_line_distance_pct"), 999) <= 日线触发大周期共振容忍 * 100
    if 日线不得反客为主 and "日线" in str(line_info.get("line_timeframe", "")) and 安全浮点(line_info.get("multi_tf_confluence_count", 1)) <= 1 and not daily_major_confirmed:
        hard_reject_reasons.append("日线触发线未获得大周期融合界确认")

    if hard_reject_reasons:
        formal = False
        hard_text = "；".join(hard_reject_reasons[:4])
        prebreak_observe = bool(br.get("pre_hit")) and 安全浮点(br.get("突破质量分"), 安全浮点(br.get("quality"))) >= 预突破观察最低质量分
        severe_event = bool(base_risk.get("block")) or bool(risk.get("block")) or (not bool(br.get("交易可追", True)) and not prebreak_observe) or bool(amount_state.get("observe_low"))
        external_only_block = all("外部公告/财务/监管雷区字段未接入" in x for x in hard_reject_reasons)
        output_bucket = "事件记录" if severe_event else "观察候选"
        if prebreak_observe and not severe_event:
            state_prefix = "临界预突破观察｜"
        else:
            state_prefix = "事件记录｜" if severe_event else ("技术正式待外部雷区复核｜" if external_only_block else "观察/待确认｜")
        state = state_prefix + hard_text
        if score >= 78:
            score = min(score, 76.0 if external_only_block else 69.0)
    else:
        formal = score >= 正式最低分
        output_bucket = "正式候选" if formal else "观察候选"
        state = f"正式候选｜{confirm_path_name}" if formal else "观察候选｜等待承接/RR进一步确认"

    grade = "S" if score >= 88 else "A" if score >= 78 else "B" if score >= 68 else "C" if score >= 58 else "D"

    return {
        "最新收盘": 四舍五入(last_close, 3),
        "等级": grade,
        "总分": score,
        "是否正式": bool(formal),
        "状态": state,
        "输出分层": output_bucket,
        "界下沿": boundary_state.get("界下沿", 0),
        "界上沿": boundary_state.get("界上沿", 0),
        "界宽%": boundary_state.get("界宽%", 0),
        "核心确认线": boundary_state.get("核心确认线", 0),
        "交易确认线": boundary_state.get("交易确认线", 0),
        "硬确认线": boundary_state.get("硬确认线", 0),
        "有效突破确认线": boundary_state.get("有效突破确认线", 0),
        "是否突破边界上沿": bool(boundary_state.get("是否突破边界上沿")),
        "是否突破交易确认线": bool(boundary_state.get("是否突破交易确认线")),
        "是否突破硬确认线": bool(boundary_state.get("是否突破硬确认线")),
        "是否突破融合界上沿": bool(boundary_state.get("是否突破融合界上沿")),
        "是否最新接受交易确认线": bool(boundary_state.get("是否最新接受交易确认线")),
        "是否最新接受有效突破确认线": bool(boundary_state.get("是否最新接受有效突破确认线")),
        "是否跌回有效确认线下": bool(boundary_state.get("是否跌回有效确认线下")),
        "是否跌回融合界内部": bool(boundary_state.get("是否跌回融合界内部")),
        "确认路径": confirm_path_name,
        "正式候选类别": confirm_path_name if bool(formal) else output_bucket,
        "界宽质量": boundary_state.get("界宽质量", ""),
        "界极宽硬拒": bool(boundary_state.get("界极宽硬拒")),
        "融合界下沿": boundary_state.get("融合界下沿", 0),
        "融合界上沿": boundary_state.get("融合界上沿", 0),
        "当前界状态": boundary_state.get("当前界状态", ""),
        "界角色": line_info.get("boundary_role", ""),
        "界置信度": line_info.get("boundary_confidence", ""),
        "界置信度分": line_info.get("boundary_confidence_score", 0),
        "自适应贴线容差": line_info.get("line_tolerance", 0),
        "自适应边界带容差": line_info.get("band_tolerance", 0),
        "VBP筹码带": f"{line_info.get('vbp_band_low', 0)}-{line_info.get('vbp_band_high', 0)}",
        "VBP峰值价": line_info.get("vbp_peak_price", 0),
        "VBP重合比例": line_info.get("vbp_overlap_ratio", 0),
        "VBP支持分": line_info.get("vbp_support_score", 0),
        "带量质量分": line_info.get("volume_quality_score", 0),
        "温和放量共振": line_info.get("mild_volume_touch_count", 0),
        "健康放量共振": line_info.get("healthy_volume_touch_count", 0),
        "标准倍量共振": line_info.get("standard_double_volume_touch_count", 0),
        "高质量倍量共振": line_info.get("high_quality_double_volume_touch_count", 0),
        "滞涨放量触线": line_info.get("stall_volume_touch_count", 0),
        "最低有效边界敏感性": bool(line_info.get("lowest_valid_boundary")),
        "敏感性摘要": line_info.get("sensitivity_summary", ""),
        "边界密度": line_info.get("price_cluster_density", 0),
        "边界宽度质量": line_info.get("boundary_width_quality", ""),
        "边界状态细分": line_info.get("acceptance_state", line_info.get("current_state", "")),
        "假突破记忆次数": line_info.get("false_breakout_count", 0),
        "反抽失败次数": line_info.get("failed_retest_count", 0),
        "硬拒原因": "；".join(hard_reject_reasons),
        "基础风险": base_risk.get("detail", ""),
        "基础风险等级": base_risk.get("level", ""),
        "成交额20日": 四舍五入(amount20, 2),
        "成交额质量": amount_state.get("quality", ""),
        "成交额说明": amount_state.get("detail", ""),
        "成交额可靠": bool(amount_state.get("reliable")),
        "成交额正式可用": not bool(amount_state.get("formal_low")),
        "成交额口径": amount_state.get("amount_basis", ""),
        "主评测线类型": line_type,
        "核心线级别分": 四舍五入(line_component, 2),
        "突破K分": 四舍五入(breakout_component, 2),
        "承接分": 四舍五入(accept_component, 2),
        "空间RR分": 四舍五入(space_component, 2),
        "风险分": 四舍五入(risk_component, 2),
        "上下文分": 四舍五入(context_component, 2),
        "核心线级别说明": line_score.get("detail", ""),
        "突破后接受": acceptance.get("detail", ""),
        "资金行为": fund.get("detail", ""),
        "交易定价": trade.get("detail", ""),
        "风险反证": risk.get("detail", ""),
        "上下文说明": "；".join(context_reasons) or "无额外上下文加分",
        "交易防守位": trade.get("defense_price", 0),
        "防守位类型": trade.get("defense_type", ""),
        "防守距离%": trade.get("defense_distance_pct", 0),
        "距有效确认线%": trade.get("distance_effective_pct", 0),
        "突破后摆动压力硬拦": bool(trade.get("post_swing_pressure_blocks")),
        "上方目标/压力": trade.get("target_price", 0),
        "上方空间%": trade.get("space_pct", 0),
        "RR": trade.get("rr", 0),
        "交易定价过闸": bool(trade.get("ok")),
        "防守模式": trade.get("defense_mode", acceptance.get("defense_mode", "")),
        "执行模式": trade.get("position_mode", acceptance.get("position_mode", "")),
        "执行等级": trade.get("execution_grade", acceptance.get("execution_grade", "")),
        "建议仓位权重": trade.get("position_weight", acceptance.get("position_weight", 0)),
        "防守确定性": trade.get("defense_certainty", acceptance.get("defense_certainty", "")),
        "真实防守验证": bool(trade.get("defense_validated", acceptance.get("defense_validated", False))),
        "目标模式": trade.get("target_mode", ""),
        "移动止盈规则": trade.get("trailing_stop_rule", ""),
        "外部雷区提示": trade.get("external_risk_note", "技术筛选未接入公告/财务/监管雷区数据"),
        "确认条件": trade.get("confirm_condition", ""),
        "放弃条件": trade.get("giveup_condition", ""),
        "风险等级": risk.get("level", ""),
        "风险扣分": risk.get("penalty", 0),
        "20日涨幅%": risk.get("recent20_pct", 0),
        "突破后回撤%": risk.get("post_drawdown_pct", 0),
        "突破后跌回线下次数": risk.get("below_line_count", 0),
        "突破后天数": int(days_since),
        "承接是否确认": bool(acceptance.get("acceptance_confirmed")),
        "承接阶段": acceptance.get("acceptance_stage", ""),
        "承接支撑可作防守": bool(acceptance.get("support_is_trade_defense")),
        "是否真实回踩结构位": bool(acceptance.get("trade_pullback_confirmed")),
        "强势悬空未回踩防守": bool(acceptance.get("floating_acceptance_only")),
        "干净回踩承接": bool(acceptance.get("clean_pullback_acceptance")),
        "强势悬空接受": bool(acceptance.get("strong_no_pullback_acceptance")),
        "深刺穿修复": bool(acceptance.get("deep_pierce_repair")),
        "破线修复": bool(acceptance.get("close_break_repair")),
        "最大刺穿幅度%": acceptance.get("max_pierce_pct", 0),
        "是否触碰过交易结构位": bool(acceptance.get("trade_pullback_touched")),
        "突破前压力明细": trade.get("pre_break_pressure_detail", ""),
        "突破日压力明细": trade.get("breakout_day_pressure_detail", ""),
        "突破后摆动压力明细": trade.get("post_swing_pressure_detail", ""),
        "最终压力来源": trade.get("pressure_source", ""),
        **event_labels,
    }



def 筛选单票(code: str, df: pd.DataFrame) -> Optional[Dict[str, Any]]:
    d = 加基础指标(df)
    if len(d) < 最少K线数:
        return None
    amount_state0 = 成交额20日状态(d)
    # 极低成交额前置跳过只看观察口径；成交额缺失但手口径可能达标的保留事件/观察，不允许正式。
    if bool(amount_state0.get("observe_low")) and 安全浮点(amount_state0.get("observe_amount20")) < 极低成交额20日前置跳过:
        return None

    # V17：实盘默认保持最近20日完整召回。
    # 只有显式打开 POJIE_FAST_DAILY_MODE=1 时，才临时缩短窗口用于排错/测速。
    scan_days = min(突破回看天数, 快速扫描回看天数) if 快速日跑模式 else 突破回看天数
    start_idx = max(1, len(d) - scan_days)
    options: List[Dict[str, Any]] = []
    frozen_line_cache: Dict[int, Tuple[Dict[str, Any], Dict[str, Any]]] = {}

    for bidx in range(start_idx, len(d)):
        if not 突破日基础预过滤(d, bidx, code):
            continue
        # 核心线必须冻结在突破日前，不能用突破日及之后的数据反推。
        if bidx not in frozen_line_cache:
            frozen_line_cache[bidx] = 选择突破日前双线(d, bidx)
        historical_line, trigger_line = frozen_line_cache[bidx]
        breakout_date = d.iloc[bidx]["date"].strftime("%Y-%m-%d")

        line_groups = [
            ("历史核心共振线", 展开核心线候选(historical_line, 历史核心线候选数量)),
            ("近500日日线共振触发线", 展开核心线候选(trigger_line, 近端触发线候选数量)),
        ]
        for line_type, line_candidates in line_groups:
            for line_info_raw in line_candidates:
                L = 安全浮点(line_info_raw.get("line"))
                if L <= 0:
                    continue
                latest_close = 安全浮点(d.iloc[-1].get("close"))
                # 评测线不能离当前太离谱；破界是突破/接受，不是远距离历史画线。
                if L > latest_close * 1.25 or L < latest_close * 0.55:
                    continue
                line_info_probe = 规范化融合界字段(line_info_raw)
                br = 日线单日突破质量(d, bidx, L, code, line_info=line_info_probe)
                if not br.get("hit") and not br.get("pre_hit"):
                    continue
                line_info = dict(line_info_probe)
                if str(br.get("确认层级", "")) == "主核心线第一层突破" and 正式允许主核心线回踩确认:
                    line_info["trade_confirm_line"] = L
                    line_info["effective_confirm_line"] = L
                    line_info["actual_break_line"] = L
                    line_info["effective_confirm_layer"] = "主核心线早期交易确认"
                    line_info = 规范化融合界字段(line_info)
                elif str(br.get("确认层级", "")) == "主核心线第一层突破":
                    # 配置不允许主核心线早期正式时，保留事件，但不降低交易确认线。
                    line_info["actual_break_line"] = L
                    line_info = 规范化融合界字段(line_info)
                line_info["line_label"] = line_type
                line_info["line_frozen_before_date"] = breakout_date
                line_info["line_frozen_data_end"] = d.iloc[bidx - 1]["date"].strftime("%Y-%m-%d") if bidx > 0 else ""
                options.append({"line_type": line_type, "line_info": line_info, "breakout": br, "breakout_idx": bidx})

    if not options:
        return None

    evaluated: List[Dict[str, Any]] = []
    context_cache: Dict[int, Tuple[Dict[str, Any], Dict[str, Any], List[Dict[str, Any]], List[Dict[str, Any]]]] = {}
    for opt in options:
        # 双线共振上下文也按同一个突破日前冻结快照计算，避免当前全量数据污染。
        bidx = int(opt["breakout_idx"])
        if bidx not in context_cache:
            historical_line, trigger_line = frozen_line_cache[bidx]
            hist_candidates = 展开核心线候选(historical_line, 历史核心线候选数量)
            trig_candidates = 展开核心线候选(trigger_line, 近端触发线候选数量)
            context_cache[bidx] = (historical_line, trigger_line, hist_candidates, trig_candidates)
        historical_line, trigger_line, hist_candidates, trig_candidates = context_cache[bidx]

        current_line = 安全浮点(opt["line_info"].get("line"))
        historical_hit_infos = []
        for cand in hist_candidates:
            cand_info = 规范化融合界字段(cand)
            p = 安全浮点(cand_info.get("line"))
            if p > 0 and 日线单日突破质量(d, bidx, p, code, line_info=cand_info).get("hit"):
                historical_hit_infos.append(cand_info)
        trigger_hit_infos = []
        for cand in trig_candidates:
            cand_info = 规范化融合界字段(cand)
            p = 安全浮点(cand_info.get("line"))
            if p > 0 and 日线单日突破质量(d, bidx, p, code, line_info=cand_info).get("hit"):
                trigger_hit_infos.append(cand_info)
        def 实际突破价(cand_info: Dict[str, Any]) -> float:
            ev = 日线单日突破质量(d, bidx, 安全浮点(cand_info.get("line")), code, line_info=cand_info)
            return 安全浮点(ev.get("实际突破线"), 安全浮点(cand_info.get("trade_confirm_line"), 安全浮点(cand_info.get("line"))))
        historical_hit_prices = [实际突破价(x) for x in historical_hit_infos]
        trigger_hit_prices = [实际突破价(x) for x in trigger_hit_infos]
        hist_default_info = 规范化融合界字段(historical_line)
        trig_default_info = 规范化融合界字段(trigger_line)
        historical_price = min(historical_hit_prices, key=lambda p: abs(p - current_line), default=安全浮点(hist_default_info.get("trade_confirm_line"), 安全浮点(hist_default_info.get("line"))))
        trigger_price = min(trigger_hit_prices, key=lambda p: abs(p - current_line), default=安全浮点(trig_default_info.get("trade_confirm_line"), 安全浮点(trig_default_info.get("line"))))
        context = {
            "historical_hit": bool(historical_hit_prices),
            "trigger_hit": bool(trigger_hit_prices),
            "historical_price": historical_price,
            "trigger_price": trigger_price,
        }
        deep = 深度评估(code, d, opt["line_info"], opt["breakout"], opt["line_type"], context)
        item = {
            "代码": 显示代码(code),
            "标准代码": 标准代码(code),
            "名称": str(d.iloc[-1].get("name", "") or ""),
            "核心线": 四舍五入(opt["line_info"].get("line"), 3),
            "核心线状态": opt["line_info"].get("current_state", ""),
            "核心线来源": opt["line_info"].get("source", ""),
            "核心线周期": opt["line_info"].get("line_timeframe", ""),
            "核心线冻结日": opt["line_info"].get("line_frozen_data_end", ""),
            "界下沿_原始": opt["line_info"].get("boundary_band_low", 0),
            "界上沿_原始": opt["line_info"].get("boundary_band_high", 0),
            "界宽%_原始": opt["line_info"].get("boundary_band_width_pct", 0),
            "界角色_原始": opt["line_info"].get("boundary_role", ""),
            "界置信度_原始": opt["line_info"].get("boundary_confidence", ""),
            "跨周期共振数": opt["line_info"].get("multi_tf_confluence_count", 1),
            "跨周期共振周期": opt["line_info"].get("multi_tf_confluence_frames", opt["line_info"].get("line_timeframe", "")),
            "跨周期融合带下沿": opt["line_info"].get("multi_tf_boundary_low", opt["line_info"].get("boundary_band_low", 0)),
            "跨周期融合带上沿": opt["line_info"].get("multi_tf_boundary_high", opt["line_info"].get("boundary_band_high", 0)),
            "核心线净分": opt["line_info"].get("net_score", 0),
            "月/日线共振": opt["line_info"].get("effective_resonance_count", 0),
            "带量共振": opt["line_info"].get("volume_resonance_count", 0),
            "带量质量分": opt["line_info"].get("volume_quality_score", 0),
            "标准倍量共振": opt["line_info"].get("standard_double_volume_touch_count", 0),
            "滞涨放量触线": opt["line_info"].get("stall_volume_touch_count", 0),
            "VBP筹码带": f"{opt['line_info'].get('vbp_band_low', 0)}-{opt['line_info'].get('vbp_band_high', 0)}",
            "VBP重合比例": opt["line_info"].get("vbp_overlap_ratio", 0),
            "VBP支持分": opt["line_info"].get("vbp_support_score", 0),
            "最低有效边界敏感性": bool(opt["line_info"].get("lowest_valid_boundary")),
            "敏感性摘要": opt["line_info"].get("sensitivity_summary", ""),
            "边界状态细分": opt["line_info"].get("acceptance_state", opt["line_info"].get("current_state", "")),
            "切实体次数": opt["line_info"].get("entity_cut_count", 0),
            "实体接受次数": opt["line_info"].get("entity_accept_count", 0),
            **opt["breakout"],
            **deep,
        }
        item["距离核心线%"] = 四舍五入(涨幅百分比(安全浮点(item.get("最新收盘")), 安全浮点(item.get("核心线"))), 2)
        evaluated.append(item)

    def bucket_priority(x: Dict[str, Any]) -> int:
        bucket = str(x.get("输出分层", ""))
        if bool(x.get("是否正式")) or bucket == "正式候选":
            return 3
        if bucket == "观察候选":
            return 2
        if bucket == "事件记录":
            return 1
        return 0

    ranked_evaluated = sorted(
        evaluated,
        key=lambda x: (
            bucket_priority(x),
            安全浮点(x.get("总分")) * max(0.25, 安全浮点(x.get("建议仓位权重"), 1.0)),
            1 if bool(x.get("交易定价过闸")) else 0,
            安全浮点(x.get("建议仓位权重")),
            安全浮点(x.get("突破质量分")),
        ),
        reverse=True,
    )
    best = dict(ranked_evaluated[0])
    alt_rows = []
    seen_lines = {四舍五入(best.get("核心线"), 3)}
    for alt in ranked_evaluated[1:]:
        line_key = 四舍五入(alt.get("核心线"), 3)
        if line_key in seen_lines:
            continue
        seen_lines.add(line_key)
        alt_rows.append({
            "线": line_key,
            "类型": alt.get("主评测线类型", ""),
            "周期": alt.get("核心线周期", ""),
            "状态": alt.get("状态", ""),
            "分数": alt.get("总分", 0),
            "确认线": alt.get("交易确认线", 0),
            "融合上沿": alt.get("融合界上沿", 0),
            "突破日": alt.get("突破日期", ""),
        })
        if len(alt_rows) >= max(0, 每票保留备用路径数):
            break
    best["备用关键路径数"] = len(alt_rows)
    best["备用关键路径"] = json.dumps(alt_rows, ensure_ascii=False) if alt_rows else ""
    return best


# ---------- Telegram 推送 ----------
def Telegram安全文本(x: Any, limit: int = 900) -> str:
    s = str(x or "").replace("\r", " ").strip()
    s = re.sub(r"\s+", " ", s)
    return s[:limit]


def 构建Telegram摘要(formal_top: List[Dict[str, Any]], observe_top: List[Dict[str, Any]], event_top: List[Dict[str, Any]], stat: Dict[str, Any], rows_count: int) -> str:
    target = 目标日期输入 or "最新缓存日"
    source = formal_top if formal_top else observe_top[:Telegram推送TopN]
    title = "正式候选Top" if formal_top else "观察候选Top"
    lines = [
        f"破界 Top{min(Telegram推送TopN, max(1, len(source)))}｜{target}",
        f"缓存{stat.get('缓存文件', 0)}｜命中{rows_count}｜正式{len(formal_top)}｜观察{len(observe_top)}｜事件{len(event_top)}",
    ]
    if not source:
        lines.append("今日无核心线突破海选命中。")
        return "\n".join(lines)
    lines.append(f"【{title}】")
    for i, r in enumerate(source[:Telegram推送TopN], 1):
        lines.append(
            f"{i}. {r.get('代码','')} {r.get('名称','')}｜{r.get('等级','')}｜{r.get('总分','')}｜{Telegram安全文本(r.get('状态'), 120)}"
        )
        lines.append(
            f"线:{r.get('核心线',0)}｜交易:{r.get('交易确认线', r.get('有效突破确认线',0))}｜融合:{r.get('融合界上沿',0)}｜突破:{r.get('突破日期','')}｜RR:{r.get('RR',0)}｜防守:{r.get('交易防守位',0)}"
        )
        lines.append(f"确认:{Telegram安全文本(r.get('确认条件'), 180)}")
        lines.append(f"放弃:{Telegram安全文本(r.get('放弃条件'), 180)}")
    lines.append("完整报告见 GitHub Actions artifact：pojie-reports。")
    return "\n".join(lines)


def 发送Telegram文本(text: str) -> bool:
    if not 启用Telegram推送:
        print("Telegram推送关闭：POJIE_SEND_TELEGRAM/ENABLE_TELEGRAM 未开启", flush=True)
        return False
    if not TelegramToken or not TelegramChatID:
        print("Telegram推送跳过：缺少 TELEGRAM_BOT_TOKEN/TELEGRAM_TOKEN 或 TELEGRAM_CHAT_ID", flush=True)
        return False
    try:
        import requests
        url = f"https://api.telegram.org/bot{TelegramToken}/sendMessage"
        chunks = [text[i:i+3800] for i in range(0, len(text), 3800)] or [text]
        ok_all = True
        for chunk in chunks:
            resp = requests.post(url, json={"chat_id": TelegramChatID, "text": chunk, "disable_web_page_preview": True}, timeout=20)
            if not resp.ok:
                ok_all = False
                print(f"Telegram推送失败：HTTP {resp.status_code} {resp.text[:300]}", flush=True)
        if ok_all:
            print("Telegram推送完成", flush=True)
        return ok_all
    except Exception as exc:
        print(f"Telegram推送异常：{exc}", flush=True)
        return False


# ---------- 主程序 ----------
def main() -> None:
    日志(启动标识)
    日志(f"入口文件：{Path(__file__).resolve()}")
    日志(f"目标日期：{目标日期输入 or '未指定/使用缓存最新'}｜严格目标日={要求严格目标日}")
    日志(f"TopN：正式{正式输出数量}｜观察{观察输出数量}｜TelegramTop{Telegram推送TopN}")
    日志(f"突破召回窗口：最近{突破回看天数}日｜快速模式={快速日跑模式}｜实际快速日数={快速扫描回看天数 if 快速日跑模式 else 突破回看天数}｜延迟精修={延迟核心线精修}")
    日志(f"Telegram：enabled={启用Telegram推送}｜token={'有' if bool(TelegramToken) else '无'}｜chat_id={'有' if bool(TelegramChatID) else '无'}")
    日志(f"BaoStock fallback 环境：POJIE_ALLOW_BAOSTOCK_FALLBACK={os.getenv('POJIE_ALLOW_BAOSTOCK_FALLBACK','0')}｜本脚本默认只读缓存")
    报告目录.mkdir(parents=True, exist_ok=True)

    files = 找缓存文件()
    日志(f"进入海选扫描：缓存文件数={len(files)}")
    rows: List[Dict[str, Any]] = []
    stat = {"缓存文件": len(files), "读取失败": 0, "样本不足": 0, "命中": 0}

    scan_t0 = time.time()
    slow_count = 0
    for idx, path in enumerate(files, 1):
        item_t0 = time.time()
        code = 标准代码(path.stem if re.search(r"\d{6}", path.stem) else path.name)
        try:
            df = 读取缓存文件(path)
            if df.empty:
                stat["读取失败"] += 1
                continue
            if len(df) < 最少K线数:
                stat["样本不足"] += 1
                continue
            row = 筛选单票(code, df)
            if row:
                rows.append(row)
                stat["命中"] += 1
        except Exception as exc:
            stat["读取失败"] += 1
            日志(f"筛选异常 {code}: {exc}")
        finally:
            cost = time.time() - item_t0
            if cost >= 慢票告警秒:
                slow_count += 1
                日志(f"慢票告警 code={code} cost={cost:.1f}s path={path}")
            if idx % 进度日志间隔 == 0 or idx == len(files):
                elapsed = time.time() - scan_t0
                speed = idx / elapsed if elapsed > 0 else 0.0
                eta = (len(files) - idx) / speed if speed > 0 else 0.0
                日志(f"海选进度 {idx}/{len(files)}｜命中={len(rows)}｜读取失败={stat['读取失败']}｜样本不足={stat['样本不足']}｜慢票={slow_count}｜速度={speed:.2f}只/秒｜预计剩余={eta/60:.1f}分钟｜当前={code}")
    日志(f"海选扫描完成：总耗时={(time.time()-scan_t0)/60:.1f}分钟｜命中={len(rows)}｜读取失败={stat['读取失败']}｜样本不足={stat['样本不足']}｜慢票={slow_count}")

    def 行分层优先级(x: Dict[str, Any]) -> int:
        bucket = str(x.get("输出分层", ""))
        if bool(x.get("是否正式")) or bucket == "正式候选":
            return 3
        if bucket == "观察候选":
            return 2
        if bucket == "事件记录":
            return 1
        return 0

    rows.sort(
        key=lambda x: (
            行分层优先级(x),
            安全浮点(x.get("总分")) * max(0.25, 安全浮点(x.get("建议仓位权重"), 1.0)),
            1 if bool(x.get("交易定价过闸")) else 0,
            安全浮点(x.get("建议仓位权重")),
            安全浮点(x.get("突破质量分")),
        ),
        reverse=True,
    )

    formal = [x for x in rows if bool(x.get("是否正式"))]
    observe = [x for x in rows if not bool(x.get("是否正式")) and str(x.get("输出分层")) == "观察候选" and 安全浮点(x.get("总分")) >= 观察最低分]
    event_records = [x for x in rows if not bool(x.get("是否正式")) and str(x.get("输出分层")) == "事件记录" and 安全浮点(x.get("总分")) >= 观察最低分]
    def 选择正式Top(data: List[Dict[str, Any]], limit: int) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        picked: List[Dict[str, Any]] = []
        floating_count = 0
        deferred: List[Dict[str, Any]] = []
        for item in data:
            is_float = str(item.get("执行等级", "")).startswith("强势悬空") or bool(item.get("强势悬空接受"))
            if is_float and floating_count >= 强势悬空正式池最多数量:
                item = dict(item)
                item["组合约束说明"] = f"强势悬空正式池已达{强势悬空正式池最多数量}只，顺延观察"
                deferred.append(item)
                continue
            picked.append(item)
            if is_float:
                floating_count += 1
            if len(picked) >= limit:
                break
        return picked, deferred

    formal_top, deferred_formal = 选择正式Top(formal, 正式输出数量)
    observe_top = (deferred_formal + observe)[:观察输出数量]
    event_top = event_records[:观察输出数量]

    lines = [
        f"破界核心线突破海选｜{北京时间()}",
        f"启动标识：{启动标识}",
        f"目标日期：{目标日期输入 or '未指定/使用缓存最新'}｜严格目标日：{要求严格目标日}",
        f"缓存文件：{len(files)}｜海选命中：{len(rows)}｜正式候选：{len(formal)}｜观察候选：{len(observe)}｜事件记录：{len(event_records)}",
        "",
        "规则摘要：核心线25 + 突破K27 + 承接15 + 空间/RR12 + 风险8 + 上下文13；正式池分两类：①融合上沿确认正式，②允许配置下的主核心线早期回踩确认；二者都必须通过交易定价、承接、防守、成交额、外部雷区硬闸。VBP只做可靠成交额下的筹码参考，不抬高确认线、不扩宽压力带；只突破核心线但未完成承接/复核的票进入观察或事件，不伪装成融合突破。",
        "",
    ]

    def 写入结果段(title: str, data: List[Dict[str, Any]]) -> None:
        if not data:
            return
        lines.append(f"## {title} Top{len(data)}")
        lines.append("")
        for i, r in enumerate(data, 1):
            lines.extend([
                f"{i}. {r['代码']} {r.get('名称', '')}｜{r['等级']}｜{r['总分']}｜{r['状态']}",
                f"   主线：{r['主评测线类型']} {r['核心线']}｜冻结日：{r.get('核心线冻结日', '')}｜距线：{r['距离核心线%']}%｜状态：{r['核心线状态']}",
                f"   界体系：边界带{r.get('界下沿', 0)}-{r.get('界上沿', 0)}｜核心/交易/硬/融合确认线={r.get('核心确认线', 0)}/{r.get('交易确认线', r.get('有效突破确认线', 0))}/{r.get('硬确认线', 0)}/{r.get('融合界上沿', 0)}｜宽度{r.get('界宽%', 0)}%｜路径{r.get('确认路径', r.get('交易确认层级', ''))}｜类别{r.get('正式候选类别', '')}｜角色{r.get('界角色', '')}｜置信{r.get('界置信度', '')}({r.get('界置信度分', 0)})｜跨周期{r.get('跨周期共振数', 1)}({r.get('跨周期共振周期', '')})｜当前{r.get('当前界状态', '')}｜交易确认={r.get('是否突破交易确认线', False)}/{r.get('是否最新接受交易确认线', False)}｜融合确认={r.get('是否突破融合界上沿', False)}/{r.get('是否最新接受融合界上沿', False)}",
                f"   突破：{r['突破日期']}｜{r.get('突破模式', '')}｜收盘：{r['突破收盘']}｜涨幅：{r['突破涨幅%']}%｜量比：{r['突破量比']}({r.get('突破量能层级', '')})｜质量：{r['突破质量分']}",
                f"   共振：{r.get('核心线周期', '')}{r.get('月/日线共振', r.get('月线共振', 0))}｜带量{r['带量共振']}｜带量质量{r.get('带量质量分', 0)}｜标准倍量{r.get('标准倍量共振', 0)}｜滞涨触线{r.get('滞涨放量触线', 0)}｜切实体{r['切实体次数']}｜实体接受{r['实体接受次数']}",
                f"   界增强：VBP{r.get('VBP筹码带', '')}｜VBP重合{r.get('VBP重合比例', 0)}｜VBP分{r.get('VBP支持分', 0)}｜最低边界敏感性={r.get('最低有效边界敏感性', False)}｜密度{r.get('边界密度', 0)}｜宽度质量{r.get('边界宽度质量', '')}｜状态细分{r.get('边界状态细分', '')}｜假突破{r.get('假突破记忆次数', 0)}｜反抽失败{r.get('反抽失败次数', 0)}",
                f"   备用路径：{r.get('备用关键路径', '') if r.get('备用关键路径') else '无'}",
                f"   交易：防守{r['交易防守位']}({r['防守位类型']})｜模式{r.get('防守模式', '')}/{r.get('执行模式', '')}｜执行{r.get('执行等级', '')}/权重{r.get('建议仓位权重', 0)}｜防守确定性{r.get('防守确定性', '')}｜真实防守={r.get('真实防守验证', False)}｜防守距{r['防守距离%']}%｜目标模式{r.get('目标模式', '')}｜压力{r['上方目标/压力']}｜空间{r['上方空间%']}%｜RR {r['RR']}｜过闸：{r['交易定价过闸']}",
                f"   分项：核心线{r['核心线级别分']}｜突破{r['突破K分']}｜承接{r['承接分']}｜空间RR{r['空间RR分']}｜风险{r['风险分']}｜上下文{r['上下文分']}",
                f"   好处：{r['核心线级别说明']}；承接确认={r.get('承接是否确认', False)}｜真实回踩守住={r.get('是否真实回踩结构位', False)}｜干净回踩={r.get('干净回踩承接', False)}｜强势悬空未回踩防守={r.get('强势悬空未回踩防守', False)}｜深刺穿={r.get('深刺穿修复', False)}｜破线修复={r.get('破线修复', False)}｜最大刺穿{r.get('最大刺穿幅度%', 0)}%｜防守可用={r.get('承接支撑可作防守', False)}｜{r['突破后接受']}；{r['资金行为']}",
                f"   压力：突破前={r.get('突破前压力明细', '')}｜突破日={r.get('突破日压力明细', '')}｜突破后摆动={r.get('突破后摆动压力明细', '')}｜最终来源={r.get('最终压力来源', '')}｜移动止盈={r.get('移动止盈规则', '')}",
                f"   风险：{r['风险等级']}｜基础：{r.get('基础风险', '')}｜成交额：{r.get('成交额说明', '')}｜可靠={r.get('成交额可靠', False)}/正式可用={r.get('成交额正式可用', False)}｜{r['风险反证']}｜外部雷区：{r.get('外部雷区提示', '')}",
                f"   确认：{r['确认条件']}",
                f"   放弃：{r['放弃条件']}",
                "",
            ])

    if not formal_top and not observe_top and not event_top:
        lines.append("今日无核心线突破海选命中。")
    else:
        写入结果段("正式候选", formal_top)
        写入结果段("观察候选", observe_top)
        写入结果段("事件记录", event_top)

    md = "\n".join(lines)
    日志("开始写入报告与明细")
    报告文件.write_text(md, encoding="utf-8")
    live_rows = 剥离未来标签(rows) if 实盘明细剥离未来标签 else rows
    label_rows = 仅未来标签(rows)
    pd.DataFrame(live_rows).to_csv(明细文件, index=False, encoding="utf-8-sig")
    if label_rows:
        pd.DataFrame(label_rows).to_csv(标签明细文件, index=False, encoding="utf-8-sig")
    数据文件.write_text(json.dumps({"启动标识": 启动标识, "生成时间": 北京时间(), "统计": stat, "结果": live_rows, "标签文件": str(标签明细文件) if label_rows else ""}, ensure_ascii=False, indent=2), encoding="utf-8")
    自检文件.write_text(json.dumps({"启动标识": 启动标识, "生成时间": 北京时间(), "统计": stat, "输出字段": list(live_rows[0].keys()) if live_rows else [], "标签字段已剥离实盘明细": bool(实盘明细剥离未来标签), "猴子代码自检": 猴子代码自检()}, ensure_ascii=False, indent=2), encoding="utf-8")
    日志("报告与明细写入完成")

    print(md, flush=True)
    print(f"报告文件：{报告文件}", flush=True)
    print(f"明细文件：{明细文件}", flush=True)
    if label_rows:
        print(f"标签明细文件：{标签明细文件}", flush=True)
    print(f"数据文件：{数据文件}", flush=True)
    print(f"自检文件：{自检文件}", flush=True)

    tg_text = 构建Telegram摘要(formal_top, observe_top, event_top, stat, len(rows))
    if 启用Telegram推送:
        日志(f"准备Telegram推送：正式Top={len(formal_top)}｜观察Top={len(observe_top)}｜事件Top={len(event_top)}｜总命中={len(rows)}")
        发送Telegram文本(tg_text)
    else:
        日志("Telegram未开启，本次只生成报告")
    日志("破界任务结束")


if __name__ == "__main__":
    main()
