import os
import json
import time
import html
import warnings
import signal
import io
from contextlib import contextmanager, redirect_stdout, redirect_stderr
from datetime import datetime, timedelta

import baostock as bs
import pandas as pd
import requests

warnings.filterwarnings("ignore")

# ========================= V11 交易质量主逻辑重构说明 =========================
# 本文件基于V10.1完整版本继续做“手术式增量优化”，原则是：
# 1）原有推送、缓存、Telegram、BaoStock、候选JSON、artifact上传等基础功能不删除；
# 2）原有倍量、承接、台阶、凹口、圆弧底、破底翻、月线BBI/BOLL、雷区等模块保留；
# 3）新增V11主逻辑：先看大周期位置与空间，再看结构边界，再看买点质量/风险收益比，最后用量能确认；
# 4）倍量从“主导分”降为“结构确认分”：结构位倍量高价值，远离结构/高位压力倍量降权；
# 5）新增基础层轻量交易质量闸门和深度层精算交易优先级，避免选出“强但不敢买”的票。
# ===========================================================================


# ========================= V11.2 双阳夹阴/分歧反包增量优化说明 =========================
# 1）新增“双阳夹阴”量价承接模型：第一阳启动，第二阴有威慑但不有效破第一阳实底，第三阳拼量反包；
# 2）中间阴线允许跌破第一阳实体中位，收盘最多允许略破第一阳实底约0.3%，以体现威慑力；
# 3）中间阴线不能极端爆量，第三阳必须收复阴线实体顶部/高点且量能不弱；
# 4）该模型定位为“结构位附近的短线分歧反包承接确认”，轻中度加分，不替代凹口/破底翻/黄金倍量等主结构。
# ===========================================================================

# ========================= V11.3 次日执行分类/前10精简推送增量优化说明 =========================
# 1）最终推送默认强制精简为前10只，避免报告过长；即使 workflow 仍传 RESULT_LIMIT=20，也由 TOP_PUSH_LIMIT=10 封顶；
# 2）新增“次日执行策略分类”：可低吸候选、回踩确认候选、强势接力候选、禁止追高候选、雷区剔除候选；
# 3）每只票输出禁止追高线、回踩观察区、确认条件、放弃条件，明确候选≠开盘追；
# 4）该模块不预测必涨停，只提供条件触发式执行规则，给三号员工/人工盘中确认使用。
# ===========================================================================

# ========================= V11.4 数据源稳定性/失败保护增量优化说明 =========================
# 1）针对 GitHub Actions 上 BaoStock / 公开行情源频繁 Broken pipe 的问题，新增数据源健康监控；
# 2）单只K线失败自动多次重试，连续 Broken pipe 达阈值后自动暂停、重登 BaoStock，再继续扫描；
# 3）基础扫描后如成功样本不足，先对失败股票做一轮补拉，避免偶发断流导致全市场样本过低；
# 4）若数据源持续异常，提前进入诊断模式，不推正式选股结果，避免小样本误判；
# 5）日志从“逐条刷屏”改为汇总式诊断，明确 source=baostock、stage=fetch_kline、retry、success/fail/coverage。
# ===========================================================================

# ========================= V9.1 追高闸门增量优化说明 =========================
# 本文件基于用户提供的V8/V9源码继续做“手术式增量优化”，原则是：
# 1）BaoStock数据源、主流程、Telegram变量、缓存、基础评分底座不动；
# 2）原有结构、月线、台阶、频次、雷区等模块不删除，只对追高风险做硬约束；
# 3）新增：追高风险闸门、强攻票综合分封顶、优先候选池/强势观察池分流、
#    涨停/大阳阶段标签修正、近端压力硬约束、极端放量/高乖离/过热组合封顶。
# ===========================================================================

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
ENABLE_TELEGRAM = os.environ.get("ENABLE_TELEGRAM", "0")

SIGNAL_FILE = "signals_history.json"
CANDIDATE_FILE = "stock_candidates.json"
CACHE_DIR = "kline_cache"

N = 20
CHECK_DAYS = int(os.environ.get("CHECK_DAYS", "1"))  # V11.1：默认只扫描最新有行情交易日；如需回看可在workflow设置为3

MAX_STOCKS = int(os.environ.get("MAX_STOCKS", "0"))
RESULT_LIMIT_RAW = int(os.environ.get("RESULT_LIMIT", "20"))
# V11.3：一号员工报告默认只推前10只，避免报告太长；workflow 仍传20也会被这里封顶。
TOP_PUSH_LIMIT = int(os.environ.get("TOP_PUSH_LIMIT", "10"))
RESULT_LIMIT = min(RESULT_LIMIT_RAW, TOP_PUSH_LIMIT) if TOP_PUSH_LIMIT > 0 else RESULT_LIMIT_RAW
DEEP_SCORE_LIMIT_RAW = int(os.environ.get("DEEP_SCORE_LIMIT", "500"))
# V10：深度评分硬上限。即使 workflow 仍传 500，也默认只取基础分桶后的前150条深评，
# 避免 GitHub Actions 深度评分跑不完；如确需恢复500，可设置 DEEP_SCORE_HARD_CAP=0 或 500。
DEEP_SCORE_HARD_CAP = int(os.environ.get("DEEP_SCORE_HARD_CAP", "150"))
DEEP_SCORE_LIMIT = min(DEEP_SCORE_LIMIT_RAW, DEEP_SCORE_HARD_CAP) if DEEP_SCORE_HARD_CAP > 0 else DEEP_SCORE_LIMIT_RAW

REQUEST_SLEEP = float(os.environ.get("REQUEST_SLEEP", "0.03"))
# V9：为月线BBI/BOLL缩口与多次中轨修复提供足够样本；默认约6年，仍可通过环境变量调小以节省时间。
KLINE_LOOKBACK_DAYS = int(os.environ.get("KLINE_LOOKBACK_DAYS", "2200"))
MAX_RUNTIME_SECONDS = int(os.environ.get("MAX_RUNTIME_SECONDS", "5400"))
# 分阶段限时：避免基础评分耗尽全部时间后，深度评分被误杀。
BASIC_RUNTIME_SECONDS = int(os.environ.get("BASIC_RUNTIME_SECONDS", str(max(1800, MAX_RUNTIME_SECONDS - 3600))))
DEEP_RUNTIME_SECONDS = int(os.environ.get("DEEP_RUNTIME_SECONDS", "3600"))
MIN_VALID_KLINE = int(os.environ.get("MIN_VALID_KLINE", "1000"))
PROGRESS_INTERVAL = int(os.environ.get("PROGRESS_INTERVAL", "100"))
SINGLE_STOCK_TIMEOUT_SECONDS = int(os.environ.get("SINGLE_STOCK_TIMEOUT_SECONDS", "35"))

# V11.4：数据源稳定性保护。公开行情源偶发 Broken pipe / 接收异常时，先暂停重登，再补拉失败样本。
KLINE_MAX_RETRIES = int(os.environ.get("KLINE_MAX_RETRIES", "3"))
BROKEN_PIPE_PAUSE_THRESHOLD = int(os.environ.get("BROKEN_PIPE_PAUSE_THRESHOLD", "20"))
BROKEN_PIPE_PAUSE_SECONDS = int(os.environ.get("BROKEN_PIPE_PAUSE_SECONDS", "90"))
BAOSTOCK_RELOGIN_ON_BROKEN_PIPE = os.environ.get("BAOSTOCK_RELOGIN_ON_BROKEN_PIPE", "1")
SUPPRESS_BAOSTOCK_VERBOSE = os.environ.get("SUPPRESS_BAOSTOCK_VERBOSE", "1")
DATA_SOURCE_FAIL_FAST_AFTER = int(os.environ.get("DATA_SOURCE_FAIL_FAST_AFTER", "900"))
DATA_SOURCE_MIN_SUCCESS_RATE = float(os.environ.get("DATA_SOURCE_MIN_SUCCESS_RATE", "0.20"))
DATA_SOURCE_CONSECUTIVE_FAIL_LIMIT = int(os.environ.get("DATA_SOURCE_CONSECUTIVE_FAIL_LIMIT", "300"))
RETRY_FAILED_KLINE_AFTER_SCAN = os.environ.get("RETRY_FAILED_KLINE_AFTER_SCAN", "1")
RETRY_FAILED_KLINE_LIMIT = int(os.environ.get("RETRY_FAILED_KLINE_LIMIT", "1200"))
MIN_FORMAL_COVERAGE_RATE = float(os.environ.get("MIN_FORMAL_COVERAGE_RATE", "0.80"))

SCORE_LIMIT = 75
# 最终推送阈值：新评分体系下，80分以下不再推送；基础初筛仍沿用原SCORE_LIMIT，不改原模型。
FINAL_SCORE_THRESHOLD = float(os.environ.get("FINAL_SCORE_THRESHOLD", "80"))
# V9.1：是否只推送“优先候选池”。默认1，避免一号员工把短线强攻/涨停追高票混入正式候选。
ONLY_PUSH_PRIORITY_POOL = os.environ.get("ONLY_PUSH_PRIORITY_POOL", "1")
# V9.1：强势观察池不作为正式推送，可在候选JSON中保留，供三号员工或人工复盘。
SAVE_STRONG_WATCH_POOL = os.environ.get("SAVE_STRONG_WATCH_POOL", "1")
VR1_MIN = 1.8
VR1_MAX = 2.5

FAILED_KLINE_FILE = "failed_kline_symbols.json"
RISK_FLAGS_FILE = os.environ.get("RISK_FLAGS_FILE", "risk_flags.json")

VALID_STOCK_PREFIXES = (
    "sh.600", "sh.601", "sh.603", "sh.605", "sh.688",
    "sz.000", "sz.001", "sz.002", "sz.003", "sz.300", "sz.301",
)

LAST_TRADE_DAY = ""

KLINE_SOURCE_STATS = {
    "broken_pipe": 0,
    "timeout": 0,
    "other_error": 0,
    "consecutive_broken_pipe": 0,
    "consecutive_fail": 0,
    "pause_count": 0,
    "relogin_count": 0,
}

class StockDataTimeout(Exception):
    pass


@contextmanager
def stock_query_timeout(seconds, label=""):
    """
    单只股票取数超时保护。
    GitHub Actions 是 Linux 环境，signal.alarm 可用于防止 BaoStock 单次请求长期卡死。
    """
    if not seconds or seconds <= 0:
        yield
        return

    def _handler(signum, frame):
        raise StockDataTimeout(f"单只股票取数超时 {seconds}s {label}")

    old_handler = signal.signal(signal.SIGALRM, _handler)
    signal.alarm(int(seconds))
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)


def bj_time_str():
    return (datetime.utcnow() + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")


def format_seconds(seconds):
    try:
        seconds = int(max(0, seconds))
    except Exception:
        return "未知"

    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60

    if h > 0:
        return f"{h}小时{m}分{s}秒"
    if m > 0:
        return f"{m}分{s}秒"
    return f"{s}秒"


def progress_line(stage, done, total, start_ts, success=0, fail=0):
    elapsed = time.time() - start_ts
    speed = done / elapsed if elapsed > 0 else 0

    if speed > 0 and total and done > 0:
        eta = (total - done) / speed
        eta_text = format_seconds(eta)
    else:
        eta_text = "未知"

    pct = done / total * 100 if total else 0
    print(
        f"{stage}进度：{done}/{total} ({pct:.1f}%) | "
        f"成功：{success} | 失败：{fail} | "
        f"已耗时：{format_seconds(elapsed)} | 预计剩余：{eta_text}"
    )


def split_message_by_lines(message, max_len=3500):
    parts = []
    current = ""

    for line in message.split("\n"):
        add = line if current == "" else "\n" + line

        if len(current) + len(add) > max_len:
            if current:
                parts.append(current)
            current = line
        else:
            current += add

    if current:
        parts.append(current)

    return parts


def send_telegram(message):
    if ENABLE_TELEGRAM != "1":
        print(f"[Telegram未发送: ENABLE_TELEGRAM={ENABLE_TELEGRAM}]")
        print(message)
        return False

    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("[Telegram发送失败: TELEGRAM_TOKEN 或 TELEGRAM_CHAT_ID 为空]")
        print(message)
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    parts = split_message_by_lines(message, max_len=3500)

    ok = True

    for idx, part in enumerate(parts, 1):
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": part,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }

        try:
            resp = requests.post(url, json=payload, timeout=20)
            if resp.status_code == 200:
                try:
                    message_id = resp.json().get("result", {}).get("message_id", "未知")
                except Exception:
                    message_id = "未知"
                print(f"Telegram第{idx}/{len(parts)}条发送成功: status=200, message_id={message_id}")
            else:
                print(f"Telegram第{idx}/{len(parts)}条发送失败: status={resp.status_code}, body={resp.text[:200]}")
                ok = False

        except Exception as e:
            ok = False
            print(f"Telegram发送失败 第{idx}/{len(parts)}条: {e}")

        time.sleep(0.5)

    return ok


def load_signal_history():
    if os.path.exists(SIGNAL_FILE):
        try:
            with open(SIGNAL_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_signal_history(data):
    with open(SIGNAL_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def baostock_login():
    lg = bs.login()
    print(f"BaoStock登录状态：error_code={lg.error_code}, error_msg={lg.error_msg}")
    return lg.error_code == "0"


def baostock_logout():
    try:
        bs.logout()
    except Exception:
        pass


def baostock_relogin(reason=""):
    """V11.4：数据源连续异常时，主动重登 BaoStock，尽量恢复连接。"""
    try:
        baostock_logout()
        time.sleep(2)
        ok = baostock_login()
        KLINE_SOURCE_STATS["relogin_count"] += 1
        print(f"数据源重登：source=baostock reason={reason} ok={ok} relogin_count={KLINE_SOURCE_STATS['relogin_count']}")
        return ok
    except Exception as e:
        print(f"数据源重登失败：source=baostock reason={reason} error={e}")
        return False


def is_broken_pipe_error(err):
    text = str(err).lower()
    return ("broken pipe" in text) or ("errno 32" in text) or ("接收数据异常" in text)


def handle_kline_source_error(bs_code, retry_index, err):
    """
    V11.4：统一处理K线数据源异常。
    Broken pipe 连续出现时，暂停并重登，避免后续几千只股票全部白白失败。
    """
    if is_broken_pipe_error(err):
        KLINE_SOURCE_STATS["broken_pipe"] += 1
        KLINE_SOURCE_STATS["consecutive_broken_pipe"] += 1
        KLINE_SOURCE_STATS["consecutive_fail"] += 1
        bp = KLINE_SOURCE_STATS["broken_pipe"]
        cbp = KLINE_SOURCE_STATS["consecutive_broken_pipe"]
        if bp <= 5 or bp % 20 == 0:
            print(f"K线数据源异常：source=baostock stage=fetch_kline symbol={bs_code} retry={retry_index} type=broken_pipe total={bp} consecutive={cbp}")
        if BROKEN_PIPE_PAUSE_THRESHOLD > 0 and cbp >= BROKEN_PIPE_PAUSE_THRESHOLD:
            KLINE_SOURCE_STATS["pause_count"] += 1
            print(
                f"数据源连续Broken pipe达到阈值：source=baostock consecutive={cbp} "
                f"pause={BROKEN_PIPE_PAUSE_SECONDS}s pause_count={KLINE_SOURCE_STATS['pause_count']}"
            )
            time.sleep(max(1, BROKEN_PIPE_PAUSE_SECONDS))
            if BAOSTOCK_RELOGIN_ON_BROKEN_PIPE == "1":
                baostock_relogin("consecutive_broken_pipe")
            KLINE_SOURCE_STATS["consecutive_broken_pipe"] = 0
    else:
        KLINE_SOURCE_STATS["other_error"] += 1
        KLINE_SOURCE_STATS["consecutive_fail"] += 1
        total = KLINE_SOURCE_STATS["other_error"]
        if total <= 5 or total % 50 == 0:
            print(f"K线获取失败：source=baostock stage=fetch_kline symbol={bs_code} retry={retry_index} error={str(err)[:120]}")


def reset_kline_success_streak():
    KLINE_SOURCE_STATS["consecutive_broken_pipe"] = 0
    KLINE_SOURCE_STATS["consecutive_fail"] = 0


def should_abort_for_data_source(processed, success, fail):
    """
    V11.4：如果数据源已经大面积失效，提前停止基础扫描，避免跑完整个市场仍然只有极少样本。
    """
    if processed < DATA_SOURCE_FAIL_FAST_AFTER:
        return False
    total = max(1, success + fail)
    success_rate = success / total
    if success_rate < DATA_SOURCE_MIN_SUCCESS_RATE and KLINE_SOURCE_STATS.get("consecutive_fail", 0) >= DATA_SOURCE_CONSECUTIVE_FAIL_LIMIT:
        print(
            f"数据源疑似大面积异常，提前进入诊断保护：processed={processed}, success={success}, fail={fail}, "
            f"success_rate={success_rate:.1%}, consecutive_fail={KLINE_SOURCE_STATS.get('consecutive_fail', 0)}"
        )
        return True
    return False


def summarize_kline_source_stats():
    return (
        f"数据源诊断：BrokenPipe={KLINE_SOURCE_STATS.get('broken_pipe', 0)}，"
        f"timeout={KLINE_SOURCE_STATS.get('timeout', 0)}，other={KLINE_SOURCE_STATS.get('other_error', 0)}，"
        f"pause={KLINE_SOURCE_STATS.get('pause_count', 0)}，relogin={KLINE_SOURCE_STATS.get('relogin_count', 0)}"
    )


def get_last_trade_day():
    today = datetime.now()

    for i in range(10):
        day = (today - timedelta(days=i)).strftime("%Y-%m-%d")
        rs = bs.query_all_stock(day)
        df = rs.get_data()

        if df is not None and not df.empty:
            return day

    return today.strftime("%Y-%m-%d")


def get_a_stock_list():
    global LAST_TRADE_DAY

    trade_day = get_last_trade_day()
    LAST_TRADE_DAY = trade_day

    print(f"使用股票池日期：{trade_day}")

    rs = bs.query_all_stock(trade_day)
    df = rs.get_data()

    if df is None or df.empty:
        print("BaoStock股票列表为空")
        return pd.DataFrame(columns=["代码", "名称", "bs_code"])

    if "code" not in df.columns:
        print("BaoStock股票列表字段异常")
        print(df.head())
        return pd.DataFrame(columns=["代码", "名称", "bs_code"])

    name_col = "code_name" if "code_name" in df.columns else None

    if name_col is None:
        df["code_name"] = ""
        name_col = "code_name"

    df = df[["code", name_col]].copy()
    df = df.rename(columns={"code": "bs_code", name_col: "名称"})

    df["bs_code"] = df["bs_code"].astype(str)
    df["代码"] = df["bs_code"].str.split(".").str[-1]
    df["名称"] = df["名称"].astype(str)

    df = df[df["bs_code"].str.startswith(VALID_STOCK_PREFIXES)]
    df = df[~df["名称"].astype(str).str.contains("ST|\\*ST|退", regex=True, na=False)]
    df = df.drop_duplicates(subset=["代码"])

    if MAX_STOCKS > 0:
        df = df.head(MAX_STOCKS)

    print(f"A股个股列表获取成功：{len(df)} 只")
    print("股票池前20只：")
    print(df[["代码", "名称", "bs_code"]].head(20).to_string(index=False))

    return df[["代码", "名称", "bs_code"]]


def cache_path(bs_code):
    os.makedirs(CACHE_DIR, exist_ok=True)
    return os.path.join(CACHE_DIR, f"{bs_code.replace('.', '_')}.csv")


def normalize_kline_df(df):
    if df is None or df.empty:
        return None

    df = df.copy()

    for col in ["open", "high", "low", "close", "volume", "amount", "pct_chg", "turnover"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=["open", "high", "low", "close", "volume"])
    df = df.sort_values("date").drop_duplicates(subset=["date"])

    if df.empty:
        return None

    return df


def read_cached_kline(bs_code):
    path = cache_path(bs_code)

    if not os.path.exists(path):
        return None

    try:
        df = pd.read_csv(path, dtype={"date": str})
        df = normalize_kline_df(df)

        if df is None or df.empty or "date" not in df.columns:
            return None

        last_date = str(df["date"].max())

        if LAST_TRADE_DAY and last_date >= LAST_TRADE_DAY:
            return df

        return None

    except Exception as e:
        print(f"读取缓存失败 {bs_code}: {e}")
        return None


def write_cached_kline(bs_code, df):
    try:
        path = cache_path(bs_code)
        df.to_csv(path, index=False, encoding="utf-8")
    except Exception as e:
        print(f"写入缓存失败 {bs_code}: {e}")


def safe_float(value, default=0.0):
    try:
        if pd.isna(value):
            return default
        return float(value)
    except Exception:
        return default



def save_json_file(path, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"JSON保存失败 {path}: {e}")


def load_risk_flags(path=RISK_FLAGS_FILE):
    """
    重大基本面/监管雷区由外部JSON提供，避免联网抓公告导致不稳定。
    支持格式：
    {
      "000567": ["立案调查", "控股股东高质押", "审计报告带强调事项段"],
      "300001": {"flags": ["行政监管措施"], "note": "..."}
    }
    命中重大雷区至少扣40分，并从普通80分候选池中降级。
    """
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception as e:
        print(f"读取雷区文件失败 {path}: {e}")
        return {}


_RISK_FLAGS_CACHE = None


def evaluate_regulatory_risk(code, name=""):
    global _RISK_FLAGS_CACHE
    if _RISK_FLAGS_CACHE is None:
        _RISK_FLAGS_CACHE = load_risk_flags()

    raw = _RISK_FLAGS_CACHE.get(str(code), _RISK_FLAGS_CACHE.get(str(code).zfill(6), None))
    if not raw:
        return {"penalty": 0.0, "hard_exclude": False, "flags": [], "note": ""}

    if isinstance(raw, dict):
        flags = raw.get("flags", [])
        note = str(raw.get("note", ""))
    elif isinstance(raw, list):
        flags = raw
        note = ""
    else:
        flags = [str(raw)]
        note = ""

    flags = [str(x) for x in flags if str(x).strip()]
    joined = "；".join(flags) + ("；" + note if note else "")

    # 重大雷区至少-40；多项命中时可更重，但不无限相加。
    major_keywords = [
        "立案", "证监会", "调查", "信披违规", "资金占用", "违规担保", "重大处罚",
        "行政处罚", "非标", "无法表示", "保留意见", "否定意见", "退市", "ST", "债务违约",
    ]
    medium_keywords = [
        "监管措施", "警示函", "高质押", "质押", "冻结", "司法拍卖", "诉讼", "高管处罚",
        "股权分散", "高管长期空缺", "高管人员偏少", "股东大会议案被否", "会计师事务所变动", "频繁更名",
    ]
    financial_keywords = [
        "亏损", "连续亏损", "亏损扩大", "扣非亏损", "财报亏损", "经营现金流", "现金流为负",
        "应收", "坏账", "减值", "合同资产", "财务风险", "回款", "高商誉", "商誉",
        "负债率", "负债率逐年递增", "客户占营收比过高", "供应商采购占比高", "客户集中", "供应商集中",
    ]

    major_hits = sum(1 for k in major_keywords if k in joined)
    medium_hits = sum(1 for k in medium_keywords if k in joined)
    financial_hits = sum(1 for k in financial_keywords if k in joined)

    if major_hits > 0:
        penalty = -40.0 - min(20.0, 5.0 * max(0, major_hits - 1) + 3.0 * medium_hits)
        return {"penalty": penalty, "hard_exclude": True, "flags": flags, "note": note}
    if medium_hits > 0:
        # 用户强调重大雷区至少-40；单纯中等风险先扣20~35，若多项中等风险合并也硬降级。
        penalty = -20.0 - min(20.0, 5.0 * max(0, medium_hits - 1))
        hard = medium_hits >= 3
        if hard:
            penalty = min(penalty, -40.0)
        return {"penalty": penalty, "hard_exclude": hard, "flags": flags, "note": note}

    if financial_hits > 0:
        # V11.1：财报/治理/集中度/商誉等雷区必须在一号员工阶段严格处理。
        # 单项财务风险明显压分；多项财务风险或财务+治理风险叠加，直接降级/剔除优先池。
        penalty = -15.0 - min(35.0, 7.0 * max(0, financial_hits - 1) + 4.0 * medium_hits)
        hard = (financial_hits >= 3) or (financial_hits >= 2 and medium_hits >= 1)
        if hard:
            penalty = min(penalty, -40.0)
        return {"penalty": penalty, "hard_exclude": hard, "flags": flags, "note": note}

    return {"penalty": -10.0, "hard_exclude": False, "flags": flags, "note": note}


def build_monthly_df(df):
    """从日线构造月线，供月线BBI/BOLL缩口、中轨修复、量能、大阴破局判断。"""
    if df is None or len(df) < 160 or "date" not in df.columns:
        return pd.DataFrame()

    m = df.copy()
    m["date"] = pd.to_datetime(m["date"], errors="coerce")
    m = m.dropna(subset=["date"])
    if m.empty:
        return pd.DataFrame()

    m = m.set_index("date").sort_index()
    monthly = pd.DataFrame()
    monthly["open"] = m["open"].resample("M").first()
    monthly["high"] = m["high"].resample("M").max()
    monthly["low"] = m["low"].resample("M").min()
    monthly["close"] = m["close"].resample("M").last()
    monthly["volume"] = m["volume"].resample("M").sum()
    monthly["amount"] = m["amount"].resample("M").sum() if "amount" in m.columns else 0
    monthly = monthly.dropna(subset=["open", "high", "low", "close", "volume"])

    if monthly.empty:
        return monthly

    monthly["ma3"] = monthly["close"].rolling(3).mean()
    monthly["ma6"] = monthly["close"].rolling(6).mean()
    monthly["ma12"] = monthly["close"].rolling(12).mean()
    monthly["ma20"] = monthly["close"].rolling(20).mean()
    monthly["ma24"] = monthly["close"].rolling(24).mean()
    monthly["bbi_mid"] = (monthly["ma3"] + monthly["ma6"] + monthly["ma12"] + monthly["ma24"]) / 4
    monthly["boll_mid"] = monthly["ma20"]
    monthly["boll_std"] = monthly["close"].rolling(20).std()
    monthly["boll_upper"] = monthly["boll_mid"] + 2 * monthly["boll_std"]
    monthly["boll_lower"] = monthly["boll_mid"] - 2 * monthly["boll_std"]
    monthly["boll_width"] = (monthly["boll_upper"] - monthly["boll_lower"]) / monthly["boll_mid"].replace(0, pd.NA)
    ma_max = monthly[["ma3", "ma6", "ma12", "ma24"]].max(axis=1)
    ma_min = monthly[["ma3", "ma6", "ma12", "ma24"]].min(axis=1)
    monthly["bbi_dispersion"] = (ma_max - ma_min) / monthly["bbi_mid"].replace(0, pd.NA)
    monthly["mid"] = monthly["bbi_mid"].where(monthly["bbi_mid"].notna(), monthly["boll_mid"])
    monthly["vol_ma5"] = monthly["volume"].rolling(5).mean()
    monthly["body_pct"] = (monthly["close"] - monthly["open"]) / monthly["open"].replace(0, pd.NA)
    monthly["is_up"] = monthly["close"] > monthly["open"]
    monthly["is_down"] = monthly["close"] < monthly["open"]
    monthly["above_or_near_mid"] = monthly["close"] >= monthly["mid"] * 0.98
    monthly["below_mid"] = monthly["close"] < monthly["mid"] * 0.995
    monthly["big_down"] = monthly["is_down"] & (monthly["body_pct"] <= -0.08)
    monthly["big_down_vol"] = monthly["big_down"] & (monthly["volume"] >= monthly["vol_ma5"] * 1.15)
    return monthly

def _count_consecutive_true(values):
    count = 0
    for v in reversed(list(values)):
        if bool(v):
            count += 1
        else:
            break
    return count


def detect_monthly_midline_reclaim(df):
    """
    月线BBI/BOLL大周期模块（0~14分）：
    1）缩口必须与过去36/48/60个月相比，处于最窄或低分位；
    2）BBI均线离散度也要同步收敛；
    3）统计缩口后多次跌破中轨再收复的有效修复；
    4）检查当前是否站回/站稳中轨、是否修复关键大阴线实体/高点；
    5）月线量能只做辅助，不喧宾夺主。
    """
    m = build_monthly_df(df)
    if m is None or len(m) < 24 or "mid" not in m.columns:
        return {"score": 0.0, "flag": "", "support_months": 0, "volume_score": 0.0, "detail": "月线样本不足"}

    m = m.dropna(subset=["mid"])
    if len(m) < 12:
        return {"score": 0.0, "flag": "", "support_months": 0, "volume_score": 0.0, "detail": "月线中轨样本不足"}

    current = m.iloc[-1]
    current_close = safe_float(current["close"])
    current_high = safe_float(current["high"])
    current_mid = safe_float(current["mid"])
    if current_mid <= 0:
        return {"score": 0.0, "flag": "", "support_months": 0, "volume_score": 0.0, "detail": "月线中轨无效"}

    hist60 = m.tail(60).copy()
    valid_width = hist60["boll_width"].dropna()
    valid_disp = hist60["bbi_dispersion"].dropna()

    width_pct = None
    disp_pct = None
    shrink_score = 0.0
    dispersion_score = 0.0
    shrink_label = "无有效缩口"

    # 缩口核心：必须是与前期相比最窄/低分位，而不是绝对值看起来窄。
    if len(valid_width) >= 24 and pd.notna(current.get("boll_width", pd.NA)):
        cur_w = safe_float(current["boll_width"], None)
        if cur_w is not None:
            width_pct = float((valid_width <= cur_w).sum() / len(valid_width))
            if width_pct <= 0.10:
                shrink_score = 2.0
                shrink_label = "极致缩口"
            elif width_pct <= 0.20:
                shrink_score = 1.5
                shrink_label = "明显缩口"
            elif width_pct <= 0.35:
                shrink_score = 0.8
                shrink_label = "轻度缩口"

    if len(valid_disp) >= 24 and pd.notna(current.get("bbi_dispersion", pd.NA)):
        cur_d = safe_float(current["bbi_dispersion"], None)
        if cur_d is not None:
            disp_pct = float((valid_disp <= cur_d).sum() / len(valid_disp))
            if disp_pct <= 0.10:
                dispersion_score = 2.0
            elif disp_pct <= 0.20:
                dispersion_score = 1.5
            elif disp_pct <= 0.30:
                dispersion_score = 1.0

    # 统计最近36个月内“跌破中轨 -> 1~4个月内收复”的次数。
    recent = m.tail(36).copy()
    repairs = []
    i = 0
    rows = list(recent.iterrows())
    while i < len(rows) - 1:
        idx, row = rows[i]
        if bool(row.get("below_mid", False)):
            for j in range(i + 1, min(i + 5, len(rows))):
                ridx, rrow = rows[j]
                if safe_float(rrow["close"]) >= safe_float(rrow["mid"]):
                    # 收复后站稳月数
                    stable = 0
                    for k in range(j, min(j + 4, len(rows))):
                        _, srow = rows[k]
                        if safe_float(srow["close"]) >= safe_float(srow["mid"]) * 0.98:
                            stable += 1
                        else:
                            break
                    repairs.append({"break_idx": idx, "reclaim_idx": ridx, "stable": stable})
                    i = j
                    break
        i += 1

    repair_count = len(repairs)
    avg_stable = sum(x["stable"] for x in repairs) / repair_count if repair_count else 0
    repair_score = 0.0
    if repair_count == 1:
        repair_score = 1.0
    elif repair_count == 2:
        repair_score = 2.0
    elif repair_count >= 3:
        repair_score = 3.0
    if repair_count > 0 and avg_stable >= 2:
        repair_score += 1.0
    repair_score = min(repair_score, 4.0)

    # 当前中轨状态。
    above_seq = _count_consecutive_true(m["above_or_near_mid"].tail(12))
    current_state_score = 0.0
    if current_close >= current_mid:
        if above_seq >= 3:
            current_state_score = 2.0
        elif above_seq >= 2:
            current_state_score = 1.5
        else:
            current_state_score = 0.8

    # 关键大阴线修复。
    key_down_score = 0.0
    key_down_label = ""
    candidates = recent.iloc[:-1].tail(12)
    if not candidates.empty:
        big_downs = candidates[candidates["big_down"] | candidates["big_down_vol"]]
        if not big_downs.empty:
            br = big_downs.iloc[-1]
            br_high = safe_float(br["high"])
            br_body_top = max(safe_float(br["open"]), safe_float(br["close"]))
            if current_close >= br_body_top:
                key_down_score = 1.5
                key_down_label = "收复关键阴线实体"
            if current_close >= br_high or current_high >= br_high:
                key_down_score = 2.5
                key_down_label = "突破关键阴线高点"
            if key_down_score >= 2.5 and above_seq >= 2:
                key_down_score = 3.0
                key_down_label += "+站稳"

    # 月线量能质量，封顶1分。
    volume_score = 0.0
    vol_window = recent.tail(12)
    if not vol_window.empty:
        up = vol_window[vol_window["is_up"]]
        down = vol_window[vol_window["is_down"]]
        up_vol_avg = up["volume"].mean() if not up.empty else 0
        down_vol_avg = down["volume"].mean() if not down.empty else 0
        if down_vol_avg > 0 and up_vol_avg > down_vol_avg * 1.05:
            volume_score += 0.5
        if bool((vol_window["is_up"] & (vol_window["volume"] > vol_window["volume"].shift(1) * 1.15) & (vol_window["volume"] < vol_window["volume"].shift(1) * 2.5)).any()):
            volume_score += 0.5
    volume_score = min(volume_score, 1.0)

    support_months = int(above_seq)
    total = min(14.0, shrink_score + dispersion_score + repair_score + current_state_score + key_down_score + volume_score)

    parts = []
    if shrink_score > 0:
        pct_txt = f"{width_pct:.0%}" if width_pct is not None else "未知"
        parts.append(f"{shrink_label}(带宽分位{pct_txt})")
    if dispersion_score > 0:
        pct_txt = f"{disp_pct:.0%}" if disp_pct is not None else "未知"
        parts.append(f"BBI收敛(离散度分位{pct_txt})")
    if repair_count > 0:
        parts.append(f"中轨修复{repair_count}次")
    if current_state_score > 0:
        parts.append(f"当前站回/贴近中轨{above_seq}月")
    if key_down_label:
        parts.append(key_down_label)

    detail = (
        f"缩口{shrink_score:.1f}/2，BBI收敛{dispersion_score:.1f}/2，"
        f"中轨修复{repair_score:.1f}/4，当前中轨{current_state_score:.1f}/2，"
        f"关键阴线{key_down_score:.1f}/3，月线量能{volume_score:.1f}/1"
    )

    if total <= 0:
        return {"score": 0.0, "flag": "", "support_months": support_months, "volume_score": float(volume_score), "detail": detail}

    return {
        "score": float(total),
        "flag": "+".join(parts),
        "support_months": support_months,
        "volume_score": float(volume_score),
        "detail": detail,
        "repair_count": repair_count,
        "boll_width_percentile": width_pct if width_pct is not None else None,
        "bbi_dispersion_percentile": disp_pct if disp_pct is not None else None,
    }

def save_candidates_payload(base_rows, deep_rows, final_signals, strong_watch_pool=None, path=CANDIDATE_FILE):
    """保存候选池，给三号员工/复盘复用，避免从Telegram文本里解析。"""
    try:
        strong_watch_pool = strong_watch_pool or []
        payload = {
            "generated_at_bj": bj_time_str(),
            "base_top": base_rows[:500],
            "deep_top": deep_rows[:100],
            "final_top": final_signals,
            "strong_watch_pool": strong_watch_pool[:100],
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f"候选池已保存：{path}")
    except Exception as e:
        print(f"候选池保存失败：{e}")


def get_daily_kline(bs_code):
    cached = read_cached_kline(bs_code)

    if cached is not None:
        return cached

    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=KLINE_LOOKBACK_DAYS)).strftime("%Y-%m-%d")

    fields = "date,open,high,low,close,volume,amount,pctChg,turn,tradestatus"

    max_retries = max(1, KLINE_MAX_RETRIES)
    for i in range(max_retries):
        try:
            with stock_query_timeout(SINGLE_STOCK_TIMEOUT_SECONDS, bs_code):
                if SUPPRESS_BAOSTOCK_VERBOSE == "1":
                    # BaoStock 在 Broken pipe 时会大量向stdout打印“接收数据异常”，这里抑制刷屏，由我们统一汇总诊断。
                    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                        rs = bs.query_history_k_data_plus(
                            bs_code,
                            fields,
                            start_date=start_date,
                            end_date=end_date,
                            frequency="d",
                            adjustflag="2"
                        )
                else:
                    rs = bs.query_history_k_data_plus(
                        bs_code,
                        fields,
                        start_date=start_date,
                        end_date=end_date,
                        frequency="d",
                        adjustflag="2"
                    )

                df = rs.get_data()

            if df is None or df.empty:
                time.sleep(0.2 + 0.2 * i)
                continue

            df = df[df["tradestatus"] == "1"].copy()

            if df.empty:
                return None

            for col in ["open", "high", "low", "close", "volume", "amount", "pctChg", "turn"]:
                df[col] = pd.to_numeric(df[col], errors="coerce")

            df = df.dropna(subset=["open", "high", "low", "close", "volume"])

            if df.empty:
                return None

            df = df.rename(columns={
                "pctChg": "pct_chg",
                "turn": "turnover",
            })

            df = df[[
                "date",
                "open",
                "close",
                "high",
                "low",
                "volume",
                "amount",
                "pct_chg",
                "turnover",
            ]]

            df = normalize_kline_df(df)

            if df is None or df.empty:
                return None

            write_cached_kline(bs_code, df)
            time.sleep(REQUEST_SLEEP)
            reset_kline_success_streak()

            return df

        except StockDataTimeout as e:
            KLINE_SOURCE_STATS["timeout"] += 1
            KLINE_SOURCE_STATS["consecutive_fail"] += 1
            if KLINE_SOURCE_STATS["timeout"] <= 5 or KLINE_SOURCE_STATS["timeout"] % 20 == 0:
                print(f"K线获取超时：source=baostock stage=fetch_kline symbol={bs_code} retry={i + 1}/{max_retries} error={e}")
            time.sleep(0.8 + 0.4 * i)
            continue

        except Exception as e:
            handle_kline_source_error(bs_code, f"{i + 1}/{max_retries}", e)
            time.sleep(0.8 + 0.4 * i)

    return None


def get_limit_threshold(code):
    code = str(code)

    if code.startswith(("300", "301", "688")):
        return 19.3

    return 9.3


def _count_near_high(segment, level, tolerance=0.035):
    if segment is None or segment.empty or level <= 0:
        return 0
    high = pd.to_numeric(segment["high"], errors="coerce")
    close = pd.to_numeric(segment["close"], errors="coerce")
    near_high = (high >= level * (1 - tolerance)) & (high <= level * (1 + tolerance))
    near_close = (close >= level * (1 - tolerance)) & (close <= level * (1 + tolerance))
    return int((near_high | near_close).sum())


def _platform_high(segment, tolerance=0.035, min_touch=3):
    if segment is None or len(segment) < min_touch:
        return None

    highs = pd.to_numeric(segment["high"], errors="coerce").dropna()
    if highs.empty:
        return None

    level = float(highs.quantile(0.90))
    touch_count = _count_near_high(segment, level, tolerance=tolerance)

    if touch_count < min_touch:
        return None

    return level


def _count_near_low(segment, level, tolerance=0.035):
    if segment is None or segment.empty or level <= 0:
        return 0
    low = pd.to_numeric(segment["low"], errors="coerce")
    close = pd.to_numeric(segment["close"], errors="coerce")
    near_low = (low >= level * (1 - tolerance)) & (low <= level * (1 + tolerance))
    near_close = (close >= level * (1 - tolerance)) & (close <= level * (1 + tolerance))
    return int((near_low | near_close).sum())


def evaluate_platform_quality(segment):
    """
    平台质量量化：只作为新增评分，不改变原始基础筛选。
    平台 = 时间足够 + 价格收敛 + 上下沿多次确认 + 量价健康。
    返回0~8分，供凹口/平台突破/高级凹口二次倍量模型使用。
    """
    result = {
        "score": 0.0,
        "level": "无有效平台",
        "top": 0.0,
        "bottom": 0.0,
        "duration": 0,
        "amp": 0.0,
        "close_amp": 0.0,
        "top_touches": 0,
        "bottom_touches": 0,
        "up_down_vol_ratio": 0.0,
        "vol_contraction": 0.0,
        "big_down_count": 0,
        "slope": 0.0,
        "reason": "平台不足",
    }

    if segment is None or len(segment) < 8:
        return result

    seg = segment.copy()
    for col in ["open", "high", "low", "close", "volume"]:
        if col not in seg.columns:
            return result
        seg[col] = pd.to_numeric(seg[col], errors="coerce")
    seg = seg.dropna(subset=["open", "high", "low", "close", "volume"])
    if len(seg) < 8:
        return result

    duration = len(seg)
    high_q = safe_float(seg["high"].quantile(0.88))
    low_q = safe_float(seg["low"].quantile(0.15))
    close_mid = safe_float(seg["close"].median())
    if close_mid <= 0 or high_q <= 0 or low_q <= 0:
        return result

    total_high = safe_float(seg["high"].max())
    total_low = safe_float(seg["low"].min())
    amp = (total_high - total_low) / close_mid if close_mid > 0 else 0.0
    close_amp = (safe_float(seg["close"].quantile(0.90)) - safe_float(seg["close"].quantile(0.10))) / close_mid
    top_touches = _count_near_high(seg, high_q, tolerance=0.035)
    bottom_touches = _count_near_low(seg, low_q, tolerance=0.035)

    first = seg.iloc[:max(1, duration // 2)]
    second = seg.iloc[max(1, duration // 2):]
    first_vol = safe_float(first["volume"].mean())
    second_vol = safe_float(second["volume"].mean())
    vol_contraction = second_vol / first_vol if first_vol > 0 else 0.0

    up = seg[seg["close"] > seg["open"]]
    down = seg[seg["close"] < seg["open"]]
    up_vol = safe_float(up["volume"].mean()) if not up.empty else 0.0
    down_vol = safe_float(down["volume"].mean()) if not down.empty else 0.0
    up_down_vol_ratio = up_vol / down_vol if down_vol > 0 else (1.5 if up_vol > 0 else 0.0)

    preclose = seg["close"].shift(1)
    entity_down_pct = ((seg["open"] - seg["close"]) / preclose.replace(0, pd.NA)).where(seg["close"] < seg["open"], 0)
    range_pos = ((seg["close"] - seg["low"]) / (seg["high"] - seg["low"]).replace(0, pd.NA)).fillna(0.5)
    vol_avg = safe_float(seg["volume"].mean())
    big_down = (seg["close"] < seg["open"]) & (entity_down_pct >= 0.03) & (seg["volume"] >= vol_avg * 1.5) & (range_pos <= 0.40)
    big_down_count = int(big_down.sum())

    start_close = safe_float(seg["close"].head(min(5, duration)).mean())
    end_close = safe_float(seg["close"].tail(min(5, duration)).mean())
    slope = end_close / start_close - 1 if start_close > 0 else 0.0

    # 1）时间分：平台不能太短，过长死平台不无限加。
    time_score = 0.0
    if 8 <= duration <= 11:
        time_score = 0.5
    elif 12 <= duration <= 20:
        time_score = 1.2
    elif 21 <= duration <= 40:
        time_score = 2.0
    elif duration > 40:
        time_score = 1.5

    # 2）价格收敛分。
    price_score = 0.0
    if amp <= 0.08:
        price_score = 2.0
    elif amp <= 0.15:
        price_score = 1.5
    elif amp <= 0.22:
        price_score = 0.8

    # 收盘波动很稳时，小幅提高；但价格分封顶2。
    if close_amp <= 0.05 and price_score > 0:
        price_score = min(2.0, price_score + 0.3)

    # 3）边界确认分。
    boundary_score = 0.0
    if top_touches >= 3 and bottom_touches >= 2:
        boundary_score = 2.0
    elif top_touches >= 2 and bottom_touches >= 1:
        boundary_score = 1.2
    elif top_touches >= 2:
        boundary_score = 0.6

    # 4）量价健康分：阳量不弱、少放量长阴、平台横而不弱。
    volume_score = 0.0
    if up_down_vol_ratio >= 1.1 and big_down_count <= 1:
        volume_score = 2.0
    elif up_down_vol_ratio >= 0.9 and big_down_count <= 1:
        volume_score = 1.2
    elif up_down_vol_ratio >= 0.75 and big_down_count <= 2:
        volume_score = 0.5

    if slope < -0.08:
        volume_score = max(0.0, volume_score - 0.6)
    if slope > 0.12:
        # 已经变成上升小趋势，平台属性下降。
        price_score = max(0.0, price_score - 0.4)
    if big_down_count >= 3:
        volume_score = 0.0

    score = max(0.0, min(8.0, time_score + price_score + boundary_score + volume_score))
    if score >= 7:
        level = "高级平台"
    elif score >= 5.5:
        level = "中高级平台"
    elif score >= 4.0:
        level = "中级平台"
    elif score >= 2.5:
        level = "低级平台"
    else:
        level = "无有效平台"

    result.update({
        "score": float(score),
        "level": level,
        "top": float(high_q),
        "bottom": float(low_q),
        "duration": int(duration),
        "amp": float(amp),
        "close_amp": float(close_amp),
        "top_touches": int(top_touches),
        "bottom_touches": int(bottom_touches),
        "up_down_vol_ratio": float(up_down_vol_ratio),
        "vol_contraction": float(vol_contraction),
        "big_down_count": int(big_down_count),
        "slope": float(slope),
        "reason": (
            f"{level}：{duration}日，振幅{amp:.1%}，上沿触碰{top_touches}次，"
            f"下沿触碰{bottom_touches}次，阳量/阴量{up_down_vol_ratio:.2f}，放量长阴{big_down_count}次"
        ),
    })
    return result


def detect_advanced_ao_kou_second_volume(hist):
    """
    高级凹口二次倍量模型：
    左侧高级平台 -> 凹口下探 -> 凹口后量能更活跃 -> 第一次标准倍量 -> N日收盘不破实体中位 -> 第二次倍量确认。
    这是新增加分模型，不替代原凹口/基础倍量逻辑。
    """
    empty = {
        "hit": False,
        "score": 0.0,
        "reason": "",
        "left_platform_score": 0.0,
        "left_platform_level": "",
        "left_platform_top": 0.0,
        "first_volume_date": "",
        "first_volume_high": 0.0,
        "first_volume_mid": 0.0,
        "hold_days": 0,
        "target_150": 0.0,
        "target_dist": 0.0,
    }
    if hist is None or len(hist) < 90:
        return empty

    w = hist.tail(140).copy().reset_index(drop=True)
    for col in ["open", "high", "low", "close", "volume", "vr1", "pct_chg", "pos"]:
        if col not in w.columns:
            return empty
        w[col] = pd.to_numeric(w[col], errors="coerce")
    w = w.dropna(subset=["open", "high", "low", "close", "volume"]).reset_index(drop=True)
    if len(w) < 90:
        return empty

    # 找凹口低点：不能太靠前，也不能就是当前附近。
    search_low = w.iloc[30:max(31, len(w) - 12)]
    if search_low.empty:
        return empty
    low_idx = int(search_low["low"].idxmin())
    if low_idx < 25 or low_idx > len(w) - 12:
        return empty
    low_price = safe_float(w.loc[low_idx, "low"])

    left_start = max(0, low_idx - 55)
    left_end = max(left_start + 8, low_idx - 5)
    left = w.iloc[left_start:left_end]
    platform = evaluate_platform_quality(left)
    left_platform_score = safe_float(platform.get("score", 0.0))
    left_top = safe_float(platform.get("top", 0.0))
    left_bottom = safe_float(platform.get("bottom", 0.0))
    if left_platform_score < 4.0 or left_top <= 0 or left_bottom <= 0 or low_price <= 0:
        return empty

    # 凹口下探充分度：至少要跌破左平台下沿一定幅度。
    dip_depth = 1 - low_price / left_bottom if left_bottom > 0 else 0.0
    dip_score = 0.0
    if 0.06 <= dip_depth <= 0.15:
        dip_score = 1.4
    elif 0.15 < dip_depth <= 0.30:
        dip_score = 2.0
    elif 0.03 <= dip_depth < 0.06:
        dip_score = 0.6
    elif dip_depth > 0.30:
        dip_score = 0.6

    # 凹口后到第一次倍量前：寻找第一次标准倍量。
    standard = (w["vr1"] > 1.8) & (w["vr1"] < 2.5) & (w["close"] > w["open"])
    # 右侧第一次标准倍量：低点后至少3日，当前日前至少4日，且价格已修复到平台上沿附近。
    first_candidates = []
    for idx in range(low_idx + 3, len(w) - 4):
        if bool(standard.iloc[idx]) and safe_float(w.loc[idx, "close"]) >= left_top * 0.88:
            first_candidates.append(idx)
    if not first_candidates:
        return empty
    first_idx = first_candidates[0]

    pre_vol = safe_float(left["volume"].mean())
    after_seg = w.iloc[low_idx + 1:first_idx]
    after_vol = safe_float(after_seg["volume"].mean()) if not after_seg.empty else 0.0
    active_ratio = after_vol / pre_vol if pre_vol > 0 else 0.0
    active_score = 0.0
    if 1.1 <= active_ratio < 1.3:
        active_score = 0.5
    elif 1.3 <= active_ratio < 1.6:
        active_score = 1.0
    elif active_ratio >= 1.6:
        active_score = 1.5

    # 凹口后放量长阴过多，则活跃度不当作优点。
    if not after_seg.empty:
        avgv = safe_float(after_seg["volume"].mean())
        preclose = after_seg["close"].shift(1)
        down_body = ((after_seg["open"] - after_seg["close"]) / preclose.replace(0, pd.NA)).where(after_seg["close"] < after_seg["open"], 0)
        big_down_cnt = int(((after_seg["close"] < after_seg["open"]) & (down_body >= 0.03) & (after_seg["volume"] >= avgv * 1.5)).sum())
        if big_down_cnt >= 2:
            active_score = 0.0
    else:
        big_down_cnt = 0

    first_top = max(safe_float(w.loc[first_idx, "open"]), safe_float(w.loc[first_idx, "close"]))
    first_bottom = min(safe_float(w.loc[first_idx, "open"]), safe_float(w.loc[first_idx, "close"]))
    first_mid = (first_top + first_bottom) / 2
    first_high = safe_float(w.loc[first_idx, "high"])
    first_pos = safe_float(w.loc[first_idx, "pos"])
    first_score = 1.0
    if first_pos >= 0.65:
        first_score += 0.5
    if safe_float(w.loc[first_idx, "close"]) >= left_top * 0.96:
        first_score += 0.5
    first_score = min(2.0, first_score)

    # 第二次倍量确认：当前K线优先；若当前是强阳且量比接近，也可轻度识别。
    cur_idx = len(w) - 1
    gap_days = cur_idx - first_idx
    if gap_days < 4 or gap_days > 18:
        return empty

    hold_seg = w.iloc[first_idx + 1:cur_idx]
    if hold_seg.empty:
        return empty
    min_hold_close = safe_float(hold_seg["close"].min())
    hold_ok = min_hold_close >= first_mid
    if not hold_ok:
        return empty
    hold_days = len(hold_seg)
    hold_score = 1.5
    if 5 <= hold_days <= 10:
        hold_score = 3.0
    elif 3 <= hold_days < 5 or 10 < hold_days <= 15:
        hold_score = 2.0

    current = w.iloc[cur_idx]
    cur_vr1 = safe_float(current["vr1"])
    cur_pct = safe_float(current["pct_chg"])
    cur_pos = safe_float(current["pos"])
    cur_close = safe_float(current["close"])
    second_score = 0.0
    second_reason = ""
    if bool(standard.iloc[cur_idx]):
        second_score = 2.2
        second_reason = "第二次标准倍量"
    elif 1.5 <= cur_vr1 <= 3.2 and cur_pct >= 5 and cur_close > safe_float(current["open"]):
        # 超过2.5不按健康倍量高分，但作为大阳资金再次进入信号保留一定确认。
        second_score = 1.2
        second_reason = "第二次大阳放量确认"
    else:
        return empty
    if cur_close >= first_high * 0.995:
        second_score += 0.5
    if cur_pos >= 0.70:
        second_score += 0.3
    second_score = min(3.0, second_score)

    target_150 = low_price + (first_high - low_price) * 1.5 if first_high > low_price else 0.0
    target_dist = target_150 / cur_close - 1 if cur_close > 0 and target_150 > 0 else 0.0
    target_score = 0.0
    if target_dist >= 0.12:
        target_score = 1.0
    elif target_dist >= 0.06:
        target_score = 0.5
    elif target_dist < 0.03 and target_150 > 0:
        target_score = -0.5

    platform_contribution = min(3.0, left_platform_score * 0.4)
    score = platform_contribution + dip_score + active_score + first_score + hold_score + second_score + target_score

    # 追高保护：当前远离第一次倍量中位太多时，保留结构识别，但压低买点分。
    if first_mid > 0:
        dist_mid = cur_close / first_mid - 1
        if dist_mid > 0.22:
            score -= 1.5
        elif dist_mid > 0.15:
            score -= 0.8

    score = max(0.0, min(15.0, score))
    if score < 7.0:
        return empty

    date_text = str(w.loc[first_idx, "date"]) if "date" in w.columns else ""
    reason = (
        f"高级凹口二次倍量：左侧{platform.get('reason', '')}；"
        f"凹口下探{dip_depth:.1%}，凹口后量能/平台量{active_ratio:.2f}；"
        f"第一次标准倍量{date_text}后{hold_days}日收盘不破实体中位{first_mid:.2f}；"
        f"当前{second_reason}，150%目标{target_150:.2f}"
    )
    return {
        "hit": True,
        "score": float(score),
        "reason": reason,
        "left_platform_score": float(left_platform_score),
        "left_platform_level": str(platform.get("level", "")),
        "left_platform_top": float(left_top),
        "first_volume_date": date_text,
        "first_volume_high": float(first_high),
        "first_volume_mid": float(first_mid),
        "hold_days": int(hold_days),
        "target_150": float(target_150),
        "target_dist": float(target_dist),
    }



def detect_first_volume_fibo_reclaim_model(hist):
    """
    V11.1：严格黄金倍量模型。

    用户最新口径：
    1）第一个倍量必须发生在明显凹口/平台结构位；
    2）第一次标准倍量必须干净突破凹口/平台上沿，不能只是靠近普通前高；
    3）第一次突破后必须有调整承接，不破坏首倍K实体中位/实底或平台上沿；
    4）第二个倍量也必须是合理标准倍量，并且干净突破第一次倍量高点/确认位；
    5）必须区分低位二次确认与高位回抽100%压力位。
    """
    empty = {
        "hit": False,
        "score": 0.0,
        "type": "",
        "reason": "",
        "first_high": 0.0,
        "wave_low": 0.0,
        "level_75": 0.0,
        "level_100": 0.0,
        "level_150": 0.0,
        "level_200": 0.0,
        "dist_to_100": 0.0,
        "dist_to_150": 0.0,
    }
    if hist is None or len(hist) < 100:
        return empty

    w = hist.tail(220).copy().reset_index(drop=True)
    required = ["open", "high", "low", "close", "volume", "vr1", "pct_chg", "pos", "long_pos_250"]
    for col in required:
        if col not in w.columns:
            return empty
        w[col] = pd.to_numeric(w[col], errors="coerce")
    w = w.dropna(subset=["open", "high", "low", "close", "volume"]).reset_index(drop=True)
    if len(w) < 100:
        return empty

    cur_idx = len(w) - 1
    current = w.iloc[cur_idx]
    cur_close = safe_float(current["close"])
    cur_vr1 = safe_float(current["vr1"])
    cur_pct = safe_float(current["pct_chg"])
    cur_pos = safe_float(current["pos"])
    cur_long_pos = safe_float(current.get("long_pos_250", 0.0))
    if cur_close <= 0:
        return empty

    # 第二次确认必须是合理标准倍量、阳线、强收盘，且不能只是长上影试探。
    second_standard = 1.8 < cur_vr1 < 2.5
    second_clean_k = (safe_float(current["close"]) > safe_float(current["open"])) and cur_pos >= 0.70 and cur_pct >= 2.0
    if not (second_standard and second_clean_k):
        return empty

    # 本波段低点：取当前前120日较近波段低点，避免极远古低点导致扩展位失真。
    low_search = w.iloc[max(0, cur_idx - 120):max(1, cur_idx - 8)]
    if low_search.empty:
        return empty
    low_idx = int(low_search["low"].idxmin())
    wave_low = safe_float(w.loc[low_idx, "low"])
    if wave_low <= 0:
        return empty

    best = None
    # 首次倍量不能太近也不能太远：需要有调整承接窗口。
    for first_idx in range(low_idx + 5, cur_idx - 3):
        first = w.iloc[first_idx]
        first_vr = safe_float(first["vr1"])
        first_close = safe_float(first["close"])
        first_open = safe_float(first["open"])
        first_high = safe_float(first["high"])
        first_pos = safe_float(first["pos"])
        first_pct = safe_float(first["pct_chg"])
        if not (1.8 < first_vr < 2.5):
            continue
        if not (first_close > first_open and first_pos >= 0.70 and first_pct >= 2.0):
            continue

        # 明显凹口/平台：第一次倍量前至少40日形成左侧/平台，上沿有多次触碰，且曾有有效下探/回落。
        left = w.iloc[max(0, first_idx - 120):first_idx]
        if len(left) < 45:
            continue
        platform = evaluate_platform_quality(left.tail(80))
        platform_score = safe_float(platform.get("score", 0.0))
        platform_top = safe_float(platform.get("top", 0.0))
        top_touches = int(platform.get("top_touches", 0) or 0)
        if platform_score < 3.5 or platform_top <= 0 or top_touches < 2:
            continue

        # 凹口/平台必须有“回落-再突破”的结构，不接受普通前高附近放量。
        pullback_low = safe_float(left.tail(80)["low"].min())
        had_clear_dip = pullback_low <= platform_top * 0.90
        if not had_clear_dip:
            continue

        # 第一次倍量必须干净突破平台/凹口上沿：收盘确认、不过度拉离、非长上影。
        first_break_rate = first_close / platform_top - 1
        if first_break_rate < 0.008 or first_break_rate > 0.08:
            continue
        if first_high > platform_top * 1.12:
            continue

        first_top_body = max(first_open, first_close)
        first_bottom_body = min(first_open, first_close)
        first_mid = (first_top_body + first_bottom_body) / 2
        hold = w.iloc[first_idx + 1:cur_idx]
        if hold.empty or len(hold) < 3 or len(hold) > 30:
            continue
        min_hold_close = safe_float(hold["close"].min())
        min_hold_low = safe_float(hold["low"].min())
        # 承接优先收盘不破首倍实体中位；至少不能有效跌破实体实底/平台上沿。
        hold_score = 0.0
        hold_text = ""
        if first_mid > 0 and min_hold_close >= first_mid:
            hold_score = 3.0
            hold_text = "调整收盘不破首倍实体中位"
        elif first_bottom_body > 0 and min_hold_low >= first_bottom_body * 0.99 and min_hold_close >= platform_top * 0.985:
            hold_score = 2.0
            hold_text = "调整守住首倍实体实底/凹口上沿"
        else:
            continue

        # 第二次倍量必须干净突破第一次倍量高点，不能只是站回100%附近。
        if cur_close < first_high * 1.008:
            continue
        if cur_close / first_high - 1 > 0.10:
            # 突破太远，属于强攻后再看承接，不是舒服二次确认买点。
            continue

        amp = first_high - wave_low
        if amp <= 0:
            continue
        level_75 = wave_low + amp * 0.75
        level_100 = first_high
        level_150 = wave_low + amp * 1.5
        level_200 = wave_low + amp * 2.0
        max_after = safe_float(hold["high"].max())
        min_close_after = safe_float(hold["close"].min())
        reached_150 = max_after >= level_150 * 0.985
        reached_200 = max_after >= level_200 * 0.985
        broke_100 = min_close_after < level_100 * 0.985
        dist_to_100 = cur_close / level_100 - 1
        dist_to_150 = level_150 / cur_close - 1 if cur_close > 0 else 0.0

        # 已经打到150/200后再回抽100，不能算A类；这种通常是高位回抽压力。
        if reached_150 or reached_200:
            score = -5.0 - (2.0 if reached_200 else 0.0) - (1.0 if broke_100 else 0.0)
            candidate = {
                "hit": True,
                "score": float(max(-9.0, min(1.0, score))),
                "type": "B类：高扩展位回落后回抽100%压力位",
                "reason": (
                    f"首次倍量虽突破平台/凹口上沿{platform_top:.2f}，但之后已触达{'200%' if reached_200 else '150%'}扩展压力；"
                    f"当前再回抽100%位{level_100:.2f}，性质偏中高位修复/压力验证，不按黄金倍量高分"
                ),
                "first_high": float(first_high),
                "wave_low": float(wave_low),
                "level_75": float(level_75),
                "level_100": float(level_100),
                "level_150": float(level_150),
                "level_200": float(level_200),
                "dist_to_100": float(dist_to_100),
                "dist_to_150": float(dist_to_150),
            }
        else:
            score = 7.0 + hold_score
            if first_break_rate <= 0.05:
                score += 1.0
            if cur_pos >= 0.85:
                score += 0.8
            if dist_to_150 >= 0.12:
                score += 1.2
            elif dist_to_150 >= 0.06:
                score += 0.6
            else:
                score -= 1.5
            if cur_long_pos > 0.70:
                score -= 2.0
            if cur_long_pos > 0.80:
                score -= 2.5
            score = max(0.0, min(11.0, score))
            candidate = {
                "hit": score >= 7.0,
                "score": float(score if score >= 7.0 else 0.0),
                "type": "A类：严格黄金倍量二次确认",
                "reason": (
                    f"明显平台/凹口上沿{platform_top:.2f}，第一次标准倍量干净突破；{hold_text}；"
                    f"当前第二次标准倍量干净突破首倍高点{level_100:.2f}，150%目标{level_150:.2f}、200%目标{level_200:.2f}"
                ),
                "first_high": float(first_high),
                "wave_low": float(wave_low),
                "level_75": float(level_75),
                "level_100": float(level_100),
                "level_150": float(level_150),
                "level_200": float(level_200),
                "dist_to_100": float(dist_to_100),
                "dist_to_150": float(dist_to_150),
            }
        if best is None or safe_float(candidate.get("score", 0.0)) > safe_float(best.get("score", -99.0)):
            best = candidate

    return best if best is not None else empty

def detect_ao_kou_structure(hist):
    """
    凹口结构：左平台 + 中间下探 + 右平台 + 当日放量强阳突破左右平台上沿。
    只做新增加分，不改变原有倍量/深度评分底座。
    """
    if hist is None or len(hist) < 80:
        return {
            "hit": False,
            "neckline": 0.0,
            "score": 0.0,
            "reason": "",
        }

    window = hist.tail(80).copy()
    left = window.iloc[:35]
    middle = window.iloc[25:60]
    right = window.iloc[45:75]
    today = window.iloc[-1]

    left_high = _platform_high(left, tolerance=0.04, min_touch=3)
    right_high = _platform_high(right, tolerance=0.04, min_touch=3)
    left_platform = evaluate_platform_quality(left)
    right_platform = evaluate_platform_quality(right)

    if left_high is None or right_high is None:
        return {"hit": False, "neckline": 0.0, "score": 0.0, "reason": ""}

    neckline = max(left_high, right_high)
    lower_platform = min(left_high, right_high)

    dip_low = float(pd.to_numeric(middle["low"], errors="coerce").min())
    if dip_low <= 0 or dip_low > lower_platform * 0.90:
        return {"hit": False, "neckline": neckline, "score": 0.0, "reason": ""}

    close = float(today["close"])
    high = float(today["high"])
    low = float(today["low"])
    open_price = float(today["open"])
    volr = float(today["volr"]) if pd.notna(today.get("volr", 0)) else 0.0
    vr1 = float(today["vr1"]) if pd.notna(today.get("vr1", 0)) else 0.0
    pos = float(today["pos"]) if pd.notna(today.get("pos", 0)) else 0.0
    pct_chg = float(today["pct_chg"]) if pd.notna(today.get("pct_chg", 0)) else 0.0
    long_pos = float(today["long_pos_250"]) if pd.notna(today.get("long_pos_250", 0)) else 0.0

    break_ok = close >= neckline * 1.008
    volume_ok = (volr >= 1.5) or (vr1 >= 1.8)
    body_ok = (close > open_price) and (pos >= 0.75)
    strong_ok = pct_chg >= 3.0

    if not break_ok:
        return {"hit": False, "neckline": neckline, "score": 0.0, "reason": ""}

    # 平台级别越高，突破级别越高；这里只做额外加分，不推翻原有凹口识别。
    platform_bonus = 0.0
    platform_level_text = ""
    left_q = safe_float(left_platform.get("score", 0.0))
    right_q = safe_float(right_platform.get("score", 0.0))
    platform_q = max(left_q, right_q)
    if platform_q >= 7.0:
        platform_bonus = 3.0
        platform_level_text = "高级平台突破"
    elif platform_q >= 5.5:
        platform_bonus = 2.0
        platform_level_text = "中高级平台突破"
    elif platform_q >= 4.0:
        platform_bonus = 1.0
        platform_level_text = "中级平台突破"

    score = 7.0 + platform_bonus
    reason = "凹口结构突破"
    if platform_level_text:
        reason += f"+{platform_level_text}"

    if volume_ok:
        score += 4.0
        reason += "+量能确认"
    if body_ok:
        score += 3.0
        reason += "+强收盘"
    if strong_ok:
        score += 2.0
        reason += "+强阳"

    if close >= neckline * 1.02:
        score += 2.0
        reason += "+有效突破幅度"

    if long_pos <= 0.45:
        score += 2.0
        reason += "+低位空间"
    elif long_pos >= 0.75:
        score *= 0.55
        reason += "+高位降权"

    return {
        "hit": bool(volume_ok and body_ok),
        "neckline": float(neckline),
        "score": float(min(score, 18.0)),
        "reason": reason,
    }


def detect_arc_bottom_structure(hist):
    """
    圆弧底颈线：过去一段时间形成碗底，左沿/右沿构成颈线，当日突破颈线。
    采用保守量化：低点在中段，右侧低点抬高，MA20修复，突破左右沿较低者。
    """
    if hist is None or len(hist) < 90:
        return {
            "hit": False,
            "neckline": 0.0,
            "score": 0.0,
            "reason": "",
        }

    window = hist.tail(90).copy()
    lows = pd.to_numeric(window["low"], errors="coerce")
    if lows.isna().all():
        return {"hit": False, "neckline": 0.0, "score": 0.0, "reason": ""}

    low_pos_idx = int(lows.values.argmin())

    if low_pos_idx < 20 or low_pos_idx > 65:
        return {"hit": False, "neckline": 0.0, "score": 0.0, "reason": ""}

    left = window.iloc[max(0, low_pos_idx - 25):low_pos_idx]
    right = window.iloc[low_pos_idx + 1:min(len(window), low_pos_idx + 35)]
    recent = window.tail(20)
    today = window.iloc[-1]

    if len(left) < 10 or len(right) < 10:
        return {"hit": False, "neckline": 0.0, "score": 0.0, "reason": ""}

    left_high = float(pd.to_numeric(left["high"], errors="coerce").quantile(0.85))
    right_high = float(pd.to_numeric(right["high"], errors="coerce").quantile(0.85))
    neckline = min(left_high, right_high)

    recent_low = float(pd.to_numeric(recent["low"], errors="coerce").min())
    bottom_low = float(lows.min())

    low_repair_ok = recent_low >= bottom_low * 1.06

    ma20 = pd.to_numeric(window["ma20"], errors="coerce") if "ma20" in window.columns else pd.Series(dtype=float)
    ma20_repair_ok = False
    if len(ma20.dropna()) >= 10:
        ma20_repair_ok = bool(ma20.iloc[-1] > ma20.iloc[-6])

    close = float(today["close"])
    open_price = float(today["open"])
    volr = float(today["volr"]) if pd.notna(today.get("volr", 0)) else 0.0
    vr1 = float(today["vr1"]) if pd.notna(today.get("vr1", 0)) else 0.0
    pos = float(today["pos"]) if pd.notna(today.get("pos", 0)) else 0.0
    long_pos = float(today["long_pos_250"]) if pd.notna(today.get("long_pos_250", 0)) else 0.0

    break_ok = close >= neckline * 1.005
    volume_ok = (volr >= 1.4) or (vr1 >= 1.6)
    body_ok = close > open_price and pos >= 0.70

    if not (break_ok and low_repair_ok and ma20_repair_ok):
        return {"hit": False, "neckline": neckline, "score": 0.0, "reason": ""}

    score = 5.0
    reason = "圆弧底颈线突破"

    if volume_ok:
        score += 3.0
        reason += "+量能确认"
    if body_ok:
        score += 2.0
        reason += "+强收盘"
    if long_pos <= 0.45:
        score += 2.0
        reason += "+低位空间"
    elif long_pos >= 0.75:
        score *= 0.55
        reason += "+高位降权"

    return {
        "hit": bool(volume_ok and body_ok),
        "neckline": float(neckline),
        "score": float(min(score, 12.0)),
        "reason": reason,
    }


def detect_break_bottom_reclaim_structure(hist):
    """
    破底翻：长期平台下沿被跌破后，1~3日内快速收回平台下沿。
    核心不是“破”，而是“破后迅速翻回平台”。
    """
    if hist is None or len(hist) < 60:
        return {
            "hit": False,
            "platform_low": 0.0,
            "score": 0.0,
            "reason": "",
        }

    window = hist.tail(60).copy()
    platform = window.iloc[:-3]
    recent = window.tail(3)
    today = window.iloc[-1]

    if len(platform) < 30:
        return {"hit": False, "platform_low": 0.0, "score": 0.0, "reason": ""}

    platform_high = float(pd.to_numeric(platform["high"], errors="coerce").quantile(0.85))
    platform_low = float(pd.to_numeric(platform["low"], errors="coerce").quantile(0.15))

    if platform_low <= 0:
        return {"hit": False, "platform_low": 0.0, "score": 0.0, "reason": ""}

    platform_range = (platform_high - platform_low) / platform_low
    if platform_range > 0.28:
        return {"hit": False, "platform_low": platform_low, "score": 0.0, "reason": ""}

    recent_low = float(pd.to_numeric(recent["low"], errors="coerce").min())
    close = float(today["close"])
    open_price = float(today["open"])
    pos = float(today["pos"]) if pd.notna(today.get("pos", 0)) else 0.0
    volr = float(today["volr"]) if pd.notna(today.get("volr", 0)) else 0.0
    vr1 = float(today["vr1"]) if pd.notna(today.get("vr1", 0)) else 0.0
    long_pos = float(today["long_pos_250"]) if pd.notna(today.get("long_pos_250", 0)) else 0.0

    broke_down = recent_low <= platform_low * 0.985
    reclaimed = close >= platform_low * 1.003
    body_ok = close > open_price and pos >= 0.70
    volume_ok = (volr >= 1.3) or (vr1 >= 1.5)

    if not (broke_down and reclaimed):
        return {"hit": False, "platform_low": platform_low, "score": 0.0, "reason": ""}

    score = 5.0
    reason = "破底翻收回平台"

    if body_ok:
        score += 3.0
        reason += "+强收盘"
    if volume_ok:
        score += 2.0
        reason += "+量能确认"
    if long_pos <= 0.45:
        score += 2.0
        reason += "+低位空间"
    elif long_pos >= 0.75:
        score *= 0.55
        reason += "+高位降权"

    return {
        "hit": bool(body_ok),
        "platform_low": float(platform_low),
        "score": float(min(score, 12.0)),
        "reason": reason,
    }


def calc_base_rows(df):
    """
    V11基础候选评分：在V10整合原模型、标准倍量、突破、大阳强收盘等同源信号的基础上，
    新增“大周期位置/买点质量/粗略风险收益比”轻量闸门。

    V10.1细化：
    1）MA5金叉MA10倍量启动必须是金叉形成当日、标准倍量、实体涨幅≥3%同日共振才高分；
    2）突破有效性强调0.5%~3%最舒服，过远突破不再高奖；
    3）基础层加入简版“首次倍量高点二次确认/黄金扩展”入口模型，放入结构潜力初筛。

    目标：不是直接替代深度评分，而是让进入深度评分前100/150的候选有理论基础：
    1）健康攻击；2）低位/年线修复；3）量价承接；4）结构潜力；5）强势观察。
    """
    if df is None or len(df) < 260:
        return pd.DataFrame()

    df = df.copy()

    # ===== 原模型底座字段保留：用于诊断，也用于V10基础攻击质量的子项 =====
    df["vol_ma"] = df["volume"].rolling(N).mean()
    df["volr"] = df["volume"] / df["vol_ma"].replace(0, pd.NA)

    df["upbody"] = (df["close"] - df["open"]).where(df["close"] > df["open"], 0)
    df["upcount"] = (df["close"] > df["open"]).rolling(N).sum()
    df["upbody_sum"] = df["upbody"].rolling(N).sum()
    df["upbody_ma"] = df["upbody_sum"] / df["upcount"].replace(0, pd.NA)

    df["body"] = df["close"] - df["open"]
    df["body_ratio"] = df["body"] / df["upbody_ma"].replace(0, pd.NA)

    rng = df["high"] - df["low"]
    df["pos"] = ((df["close"] - df["low"]) / rng).where(rng != 0, 0)

    df["prehigh"] = df["high"].rolling(N).max().shift(1)

    df["ma5"] = df["close"].rolling(5).mean()
    df["ma10"] = df["close"].rolling(10).mean()
    df["ma20"] = df["close"].rolling(20).mean()
    df["ma60"] = df["close"].rolling(60).mean()
    df["ma120"] = df["close"].rolling(120).mean()
    df["ma250"] = df["close"].rolling(250).mean()

    df["uptrend"] = (df["ma5"] > df["ma10"]) & (df["ma10"] > df["ma20"])

    df["volscore"] = 0
    df.loc[df["volr"] >= 1.2, "volscore"] = 10
    df.loc[df["volr"] >= 1.5, "volscore"] = 20
    df.loc[df["volr"] >= 2.0, "volscore"] = 25
    df.loc[df["volr"] >= 2.5, "volscore"] = 30

    up = df["close"] > df["open"]
    df["bodyscore"] = 0
    df.loc[up & (df["body"] >= df["upbody_ma"]), "bodyscore"] = 10
    df.loc[up & (df["body"] >= df["upbody_ma"] * 1.2), "bodyscore"] = 15
    df.loc[up & (df["body"] >= df["upbody_ma"] * 1.5), "bodyscore"] = 20

    df["posscore"] = 0
    df.loc[df["pos"] >= 0.6, "posscore"] = 10
    df.loc[df["pos"] >= 0.7, "posscore"] = 15
    df.loc[df["pos"] >= 0.8, "posscore"] = 20

    df["brscore"] = 0
    df.loc[df["high"] >= df["prehigh"], "brscore"] = 5
    df.loc[df["close"] > df["prehigh"], "brscore"] = 15
    df.loc[df["close"] > df["prehigh"] * 1.01, "brscore"] = 20

    df["structscore"] = 0
    df.loc[(df["volr"] >= 2.5) & (df["pos"] >= 0.8), "structscore"] = 2
    df.loc[df["uptrend"], "structscore"] = 5
    df.loc[df["close"] > df["prehigh"], "structscore"] = 8
    df.loc[(df["close"] > df["prehigh"]) & (df["volr"] >= 2), "structscore"] = 10

    df["score"] = df["volscore"] + df["bodyscore"] + df["posscore"] + df["brscore"] + df["structscore"]
    df["score_base_model_legacy"] = (df["score"] / 100 * 22).clip(0, 22)

    df["vr1"] = df["volume"] / df["volume"].shift(1).replace(0, pd.NA)
    df["xg0"] = (df["score"] >= SCORE_LIMIT) & (df["vr1"] >= VR1_MIN) & (df["vr1"] <= VR1_MAX)
    df["xg"] = df["xg0"]

    df["preclose"] = df["close"].shift(1)
    df["entity_pct"] = ((df["close"] - df["open"]) / df["preclose"].replace(0, pd.NA) * 100).fillna(0)
    df["break_rate"] = (df["close"] / df["prehigh"].replace(0, pd.NA) - 1).fillna(0)

    df["bias20"] = (df["close"] / df["ma20"].replace(0, pd.NA) - 1).fillna(0)
    df["bias60"] = (df["close"] / df["ma60"].replace(0, pd.NA) - 1).fillna(0)

    df["high_250"] = df["high"].rolling(250).max()
    df["low_250"] = df["low"].rolling(250).min()
    df["long_pos_250"] = ((df["close"] - df["low_250"]) / (df["high_250"] - df["low_250"]).replace(0, pd.NA)).fillna(0)

    # 压力分层前移：基础入口就要知道近端压力是不是贴脸。
    df["overhead_high_60"] = df["high"].shift(1).rolling(60).max()
    df["overhead_high_120"] = df["high"].shift(1).rolling(120).max()
    df["overhead_high_250"] = df["high"].shift(1).rolling(250).max()
    df["near_pressure_dist"] = (df["overhead_high_60"] / df["close"].replace(0, pd.NA) - 1).fillna(0)
    df["mid_pressure_dist"] = (df["overhead_high_120"] / df["close"].replace(0, pd.NA) - 1).fillna(0)
    df["overhead_pressure_dist"] = (df["overhead_high_250"] / df["close"].replace(0, pd.NA) - 1).fillna(0)
    for _c in ["near_pressure_dist", "mid_pressure_dist", "overhead_pressure_dist"]:
        df.loc[df[_c] < 0, _c] = 0

    df["just_cross_ma120"] = (df["close"] > df["ma120"]) & (df["close"].shift(1) <= df["ma120"].shift(1))
    df["just_cross_ma250"] = (df["close"] > df["ma250"]) & (df["close"].shift(1) <= df["ma250"].shift(1))
    df["ma20_slope_5"] = (df["ma20"] / df["ma20"].shift(5).replace(0, pd.NA) - 1).fillna(0)

    # ===== 轻量指标：供基础层判断追高、启动、承接 =====
    df["is_up"] = df["close"] > df["open"]
    df["is_down"] = df["close"] < df["open"]
    df["is_beiliang"] = df["vr1"].between(1.8, 2.5)
    df["beiliang_up"] = df["is_beiliang"] & (df["close"] > df["close"].shift(1)) & df["is_up"]
    df["is_flat_volume"] = (df["volume"] >= df["volume"].shift(1) * 0.95) & (df["volume"] <= df["volume"].shift(1) * 1.05)
    df["prev_entity_mid"] = (df[["open", "close"]].max(axis=1).shift(1) + df[["open", "close"]].min(axis=1).shift(1)) / 2
    df["beiliang_flat"] = (
        df["is_beiliang"].shift(1).fillna(False)
        & df["is_flat_volume"]
        & (df["close"] >= df["prev_entity_mid"])
        & (df["close"] >= df["close"].shift(1) * 0.985)
    )
    df["beiliang_count_60_base"] = df["beiliang_up"].rolling(60).sum().fillna(0)
    df["flat_volume_count_60_base"] = df["beiliang_flat"].rolling(60).sum().fillna(0)

    for _w in [20, 40, 60]:
        up_days = df["is_up"].rolling(_w).sum()
        down_days = df["is_down"].rolling(_w).sum()
        up_vol = df["volume"].where(df["is_up"], 0).rolling(_w).sum() / up_days.replace(0, pd.NA)
        down_vol = df["volume"].where(df["is_down"], 0).rolling(_w).sum() / down_days.replace(0, pd.NA)
        df[f"base_up_days_{_w}"] = up_days.fillna(0)
        df[f"base_up_down_vol_ratio_{_w}"] = (up_vol / down_vol.replace(0, pd.NA)).fillna(0)

    # 涨停板量能特殊处理：没有分时数据时，不能把早盘锁量涨停误判为量能差。
    df["limit_up_base"] = df["pct_chg"] >= 9.3
    df["limit_volume_mode"] = "普通K线"
    df.loc[df["limit_up_base"] & (df["vr1"] < 1.8) & (df["volr"] < 3.5), "limit_volume_mode"] = "涨停锁量/疑似早盘封板"
    df.loc[df["limit_up_base"] & df["vr1"].between(1.8, 2.5), "limit_volume_mode"] = "涨停标准放量"
    df.loc[df["limit_up_base"] & (df["vr1"] >= 2.5) & (df["vr1"] < 3.5), "limit_volume_mode"] = "涨停偏强放量"
    df.loc[df["limit_up_base"] & ((df["vr1"] >= 3.5) | (df["volr"] >= 5.0)), "limit_volume_mode"] = "涨停分歧爆量"

    # 简版涨停后三日承接：基础层只做入口评分，深度层再细化。
    limit_hold_base_scores = []
    for _i in range(len(df)):
        best = 0.0
        for _j in range(max(0, _i - 3), _i):
            if not bool(df["limit_up_base"].iloc[_j]):
                continue
            hold_seg = df.iloc[_j + 1:_i + 1]
            if hold_seg.empty or len(hold_seg) > 3:
                continue
            top = max(safe_float(df["open"].iloc[_j]), safe_float(df["close"].iloc[_j]))
            bottom = min(safe_float(df["open"].iloc[_j]), safe_float(df["close"].iloc[_j]))
            mid = (top + bottom) / 2 if top > 0 else 0
            min_low = safe_float(hold_seg["low"].min())
            min_close = safe_float(hold_seg["close"].min())
            score_hold = 0.0
            if top > 0 and min_low >= top * 0.995:
                score_hold = 3.0
            elif mid > 0 and min_close >= mid:
                score_hold = 2.0
            elif bottom > 0 and min_low >= bottom * 0.995:
                score_hold = 1.0
            best = max(best, score_hold)
        limit_hold_base_scores.append(best)
    df["base_limitup_hold_score"] = limit_hold_base_scores

    # 轻量结构潜力：不跑完整凹口/圆弧/黄金扩展，只识别值得深挖的边界。
    df["high_20_prev"] = df["high"].shift(1).rolling(20).max()
    df["low_20_prev"] = df["low"].shift(1).rolling(20).min()
    df["high_40_prev"] = df["high"].shift(1).rolling(40).max()
    df["low_40_prev"] = df["low"].shift(1).rolling(40).min()
    df["amp20"] = ((df["high_20_prev"] - df["low_20_prev"]) / df["close"].replace(0, pd.NA)).fillna(0)
    df["amp40"] = ((df["high_40_prev"] - df["low_40_prev"]) / df["close"].replace(0, pd.NA)).fillna(0)
    df["platform20_break_base"] = (df["amp20"] <= 0.14) & (df["close"] > df["high_20_prev"] * 1.005)
    df["platform40_break_base"] = (df["amp40"] <= 0.22) & (df["close"] > df["high_40_prev"] * 1.005)
    df["break_bottom_reclaim_base"] = (
        (df["low"] <= df["low"].shift(1).rolling(40).min() * 0.985)
        & (df["close"] >= df["low_40_prev"] * 1.003)
        & df["is_up"]
        & (df["pos"] >= 0.60)
    )
    df["key_level_base"] = df["high_40_prev"].where(df["platform40_break_base"], df["prehigh"])
    df["distance_to_key_base"] = (df["close"] / df["key_level_base"].replace(0, pd.NA) - 1).fillna(0)

    # V11.1 基础版黄金倍量入口模型：
    # 第一倍量必须在明显平台/凹口上沿干净突破，不能只用“左侧高点±5%”宽松识别；
    # 调整后第二倍量还要干净突破首倍高点。基础层只给入口，深度层再精确分类。
    fibo_scores = []
    fibo_descs = []
    fibo_first_highs = []
    fibo_level_150s = []
    fibo_target_dists = []
    for _i in range(len(df)):
        cur_score = 0.0
        cur_desc = ""
        cur_first_high = 0.0
        cur_level_150 = 0.0
        cur_target_dist = 0.0

        # 当前必须是第二次标准倍量阳线，并且要突破第一次倍量高点，基础层才给高分入口。
        cur_close = safe_float(df["close"].iloc[_i])
        cur_high = safe_float(df["high"].iloc[_i])
        cur_pos = safe_float(df["pos"].iloc[_i])
        cur_is_second = bool(df["is_beiliang"].iloc[_i] and df["is_up"].iloc[_i] and cur_pos >= 0.60)
        if not cur_is_second or _i < 80 or cur_close <= 0:
            fibo_scores.append(0.0)
            fibo_descs.append("")
            fibo_first_highs.append(0.0)
            fibo_level_150s.append(0.0)
            fibo_target_dists.append(0.0)
            continue

        best = None
        # 第一次倍量与第二次倍量之间，间隔太短没有洗盘意义，太长则结构连续性下降。
        for _j in range(max(20, _i - 24), max(21, _i - 3)):
            if not bool(df["is_beiliang"].iloc[_j] and df["is_up"].iloc[_j]):
                continue
            first_close = safe_float(df["close"].iloc[_j])
            first_high = safe_float(df["high"].iloc[_j])
            first_open = safe_float(df["open"].iloc[_j])
            first_pos = safe_float(df["pos"].iloc[_j])
            if first_close <= 0 or first_high <= 0 or first_pos < 0.55:
                continue

            # V11.1：第一倍量必须是明显平台/凹口上沿的干净突破，不再接受普通前高附近试盘。
            left_start = max(0, _j - 120)
            left = df.iloc[left_start:_j]
            if len(left) < 45:
                continue
            platform = evaluate_platform_quality(left.tail(80))
            platform_score = safe_float(platform.get("score", 0.0))
            platform_top = safe_float(platform.get("top", 0.0))
            top_touches = int(platform.get("top_touches", 0) or 0)
            if platform_score < 3.5 or platform_top <= 0 or top_touches < 2:
                continue
            left_high = platform_top
            first_break_rate = first_close / platform_top - 1 if platform_top > 0 else 0.0
            if first_break_rate < 0.008 or first_break_rate > 0.08 or first_pos < 0.70:
                continue
            left_high_pos = left_start + int(left.tail(80)["high"].values.argmax())
            post_high_seg = df.iloc[left_high_pos:_j + 1]
            post_high_low = safe_float(post_high_seg["low"].min()) if not post_high_seg.empty else 0.0
            had_clear_pullback = post_high_low <= platform_top * 0.90
            if not had_clear_pullback:
                continue

            # 第一次倍量后的回调承接：优先收盘不破实体中位，次优低点不破实体实底。
            hold = df.iloc[_j + 1:_i]
            if hold.empty or len(hold) < 3 or len(hold) > 24:
                continue
            first_top = max(first_open, first_close)
            first_bottom = min(first_open, first_close)
            first_mid = (first_top + first_bottom) / 2
            min_hold_close = safe_float(hold["close"].min())
            min_hold_low = safe_float(hold["low"].min())
            hold_score = 0.0
            hold_text = ""
            if first_mid > 0 and min_hold_close >= first_mid:
                hold_score = 3.0
                hold_text = "回调收盘不破首倍实体中位"
            elif first_bottom > 0 and min_hold_low >= first_bottom * 0.995:
                hold_score = 2.0
                hold_text = "回调低点不破首倍实体实底"
            else:
                continue

            # 第二次倍量必须收盘突破第一次倍量K线最高点；盘中突破但收盘未确认不在基础层高分。
            if cur_close < first_high * 1.005:
                continue

            # 黄金扩展空间：本轮低点到第一次倍量高点为0~100%，当前刚过100%且离150%越远，赔率越好。
            wave_seg = df.iloc[max(0, left_high_pos):_j + 1]
            wave_low = safe_float(wave_seg["low"].min()) if not wave_seg.empty else 0.0
            if wave_low <= 0 or first_high <= wave_low:
                continue
            level_150 = wave_low + (first_high - wave_low) * 1.5
            target_dist = level_150 / cur_close - 1 if cur_close > 0 else 0.0
            target_score = 0.0
            if target_dist >= 0.12:
                target_score = 2.0
            elif target_dist >= 0.06:
                target_score = 1.0
            elif target_dist < 0.03:
                target_score = -1.0

            # 如果第一次之后、当前之前已经打到150%附近，再回抽100%，基础层不能当低位二次确认高分。
            prior_after_first = df.iloc[_j + 1:_i]
            reached_150_before = (not prior_after_first.empty) and (safe_float(prior_after_first["high"].max()) >= level_150 * 0.985)
            if reached_150_before:
                local_score = 0.0
                local_desc = f"高扩展位回落后回抽首倍高点，基础层不按二次确认高分；首倍高点{first_high:.2f}，150%位{level_150:.2f}"
            else:
                local_score = 2.0 + 3.0 + hold_score + 4.0 + target_score
                # 位置/乖离/压力修正：基础层给入口，但不纵容高位追涨。
                if safe_float(df["long_pos_250"].iloc[_i]) > 0.75:
                    local_score -= 2.5
                if safe_float(df["bias20"].iloc[_i]) > 0.18:
                    local_score -= 1.5
                npd = safe_float(df["near_pressure_dist"].iloc[_i])
                if 0 < npd < 0.05:
                    local_score -= 2.0
                local_score = max(0.0, min(12.0, local_score))
                local_desc = (
                    f"基础版首次倍量高点二次确认：左侧高点{left_high:.2f}附近首倍，"
                    f"{hold_text}，当前二次标准倍量收盘突破首倍高点{first_high:.2f}，"
                    f"150%扩展位{level_150:.2f}，距当前{target_dist:.1%}"
                )
            if best is None or local_score > best[0]:
                best = (local_score, local_desc, first_high, level_150, target_dist)

        if best is not None and best[0] >= 6.0:
            cur_score, cur_desc, cur_first_high, cur_level_150, cur_target_dist = best
        fibo_scores.append(float(cur_score))
        fibo_descs.append(str(cur_desc))
        fibo_first_highs.append(float(cur_first_high))
        fibo_level_150s.append(float(cur_level_150))
        fibo_target_dists.append(float(cur_target_dist))

    df["base_fibo_second_confirm_score"] = fibo_scores
    df["base_fibo_second_confirm_desc"] = fibo_descs
    df["base_fibo_first_high"] = fibo_first_highs
    df["base_fibo_level_150"] = fibo_level_150s
    df["base_fibo_target_dist"] = fibo_target_dists

    # 短均线启动共振：必须是“金叉形成当日 + 当日标准倍量 + 当日实体涨幅≥3%”同日共振才高分。
    # 前几天已金叉、今天才倍量突破的情况，只能算趋势延续/攻击确认，不能按金叉当日倍量启动重奖。
    df["ma5_cross_ma10"] = (df["ma5"] > df["ma10"]) & (df["ma5"].shift(1) <= df["ma10"].shift(1))
    df["short_ma_volume_entity_start"] = df["ma5_cross_ma10"] & df["is_beiliang"] & (df["entity_pct"] >= 3.0) & df["is_up"]
    df["ma5_ma10_volume_continuation"] = (
        (df["ma5"] > df["ma10"])
        & (~df["ma5_cross_ma10"])
        & df["is_beiliang"]
        & (df["entity_pct"] >= 3.0)
        & df["is_up"]
    )

    # RSI/CCI轻量过热前移，挡住前100/150被过热强攻票霸榜。
    delta = df["close"].diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    avg_gain = gain.rolling(14).mean()
    avg_loss = loss.rolling(14).mean()
    rs = avg_gain / avg_loss.replace(0, pd.NA)
    df["base_rsi"] = (100 - (100 / (1 + rs))).fillna(50)
    tp = (df["high"] + df["low"] + df["close"]) / 3
    tp_ma = tp.rolling(14).mean()
    tp_md = (tp - tp_ma).abs().rolling(14).mean()
    df["base_cci"] = ((tp - tp_ma) / (0.015 * tp_md.replace(0, pd.NA))).fillna(0)

    # ===== V10基础评分：维度不少，但不重复 =====
    df["base_attack_quality_score"] = 0.0

    # 1）量能健康度 0~10：标准倍量最高，涨停锁量特殊中性偏好，极端放量低分。
    df.loc[df["is_beiliang"] & df["volr"].between(1.2, 3.5), "base_attack_quality_score"] += 9
    df.loc[(df["vr1"] >= 1.5) & (df["vr1"] <= 1.8) & (df["volr"] >= 1.2), "base_attack_quality_score"] += 6
    df.loc[df["limit_volume_mode"].eq("涨停锁量/疑似早盘封板"), "base_attack_quality_score"] += 6
    df.loc[df["limit_volume_mode"].eq("涨停标准放量"), "base_attack_quality_score"] += 9
    df.loc[df["limit_volume_mode"].eq("涨停偏强放量"), "base_attack_quality_score"] += 4
    df.loc[(df["vr1"] >= 2.5) & (df["vr1"] < 3.5) & (~df["limit_up_base"]), "base_attack_quality_score"] += 4
    df.loc[(df["vr1"] >= 3.5) | (df["volr"] >= 5.0), "base_attack_quality_score"] -= 3

    # 2）K线攻击质量 0~8。
    df.loc[df["is_up"] & (df["entity_pct"] >= 2.0) & (df["pos"] >= 0.60), "base_attack_quality_score"] += 3
    df.loc[df["is_up"] & (df["entity_pct"] >= 3.0) & (df["pos"] >= 0.70), "base_attack_quality_score"] += 3
    df.loc[df["is_up"] & (df["entity_pct"] >= 5.0) & (df["pos"] >= 0.85), "base_attack_quality_score"] += 2
    df.loc[(df["pos"] < 0.45) & (df["entity_pct"] > 0), "base_attack_quality_score"] -= 2

    # 3）突破有效性 0~8：突破健康幅度最好，过远不再高奖。
    df.loc[(df["break_rate"] >= 0.005) & (df["break_rate"] <= 0.03), "base_attack_quality_score"] += 8
    df.loc[(df["break_rate"] > 0.03) & (df["break_rate"] <= 0.06), "base_attack_quality_score"] += 5
    df.loc[(df["break_rate"] > 0.06) & (df["break_rate"] <= 0.08), "base_attack_quality_score"] += 2
    df.loc[df["break_rate"] > 0.08, "base_attack_quality_score"] += 1
    df.loc[(df["high"] >= df["prehigh"]) & (df["close"] <= df["prehigh"]), "base_attack_quality_score"] += 1

    # 4）短趋势配合 + 短均线启动共振。
    df.loc[df["uptrend"] & (df["ma20_slope_5"] >= 0) & (df["ma20_slope_5"] <= 0.04), "base_attack_quality_score"] += 3
    df.loc[df["ma5_cross_ma10"], "base_attack_quality_score"] += 1.5
    df.loc[df["ma5_cross_ma10"] & df["is_beiliang"], "base_attack_quality_score"] += 2.0
    df.loc[df["short_ma_volume_entity_start"], "base_attack_quality_score"] += 6.0
    # 非金叉形成当日的倍量实体攻击，只给少量趋势延续确认，不能按“金叉当日启动”高分。
    df.loc[df["ma5_ma10_volume_continuation"], "base_attack_quality_score"] += 1.5
    # 高位/高乖离/压力近时，短均线金叉更像加速，不给满额入口优势。
    bad_start_context = (df["long_pos_250"] > 0.75) | (df["bias20"] > 0.15) | ((df["near_pressure_dist"] > 0) & (df["near_pressure_dist"] < 0.05))
    df.loc[bad_start_context & df["short_ma_volume_entity_start"], "base_attack_quality_score"] -= 5
    df["base_attack_quality_score"] = df["base_attack_quality_score"].clip(0, 35)

    # 位置与赔率初筛 0~20。
    df["base_position_reward_score"] = 0.0
    df.loc[df["long_pos_250"] <= 0.35, "base_position_reward_score"] += 6
    df.loc[(df["long_pos_250"] > 0.35) & (df["long_pos_250"] <= 0.55), "base_position_reward_score"] += 4
    df.loc[(df["long_pos_250"] > 0.55) & (df["long_pos_250"] <= 0.70), "base_position_reward_score"] += 2
    df.loc[df["long_pos_250"] > 0.85, "base_position_reward_score"] -= 3

    df.loc[(df["bias20"] <= 0.08) & (df["bias60"] <= 0.12), "base_position_reward_score"] += 6
    df.loc[(df["bias20"] > 0.08) & (df["bias20"] <= 0.12), "base_position_reward_score"] += 4
    df.loc[(df["bias20"] > 0.12) & (df["bias20"] <= 0.18), "base_position_reward_score"] += 2
    df.loc[df["bias20"] > 0.20, "base_position_reward_score"] -= 4
    df.loc[(df["bias20"] > 0.20) & (df["bias60"] > 0.20), "base_position_reward_score"] -= 4

    df.loc[df["near_pressure_dist"] > 0.20, "base_position_reward_score"] += 5
    df.loc[(df["near_pressure_dist"] > 0.12) & (df["near_pressure_dist"] <= 0.20), "base_position_reward_score"] += 4
    df.loc[(df["near_pressure_dist"] > 0.08) & (df["near_pressure_dist"] <= 0.12), "base_position_reward_score"] += 2
    df.loc[(df["near_pressure_dist"] > 0) & (df["near_pressure_dist"] < 0.05), "base_position_reward_score"] -= 5

    df.loc[(df["distance_to_key_base"] >= 0.005) & (df["distance_to_key_base"] <= 0.03), "base_position_reward_score"] += 3
    df.loc[(df["distance_to_key_base"] > 0.03) & (df["distance_to_key_base"] <= 0.06), "base_position_reward_score"] += 2
    df.loc[df["distance_to_key_base"] > 0.10, "base_position_reward_score"] -= 3
    df["base_position_reward_score"] = df["base_position_reward_score"].clip(-10, 20)

    # 量价承接雏形 0~15。
    df["base_volume_carry_score"] = 0.0
    df.loc[df["beiliang_count_60_base"] >= 1, "base_volume_carry_score"] += 1
    df.loc[df["beiliang_count_60_base"] >= 2, "base_volume_carry_score"] += 2
    df.loc[df["beiliang_count_60_base"] >= 4, "base_volume_carry_score"] += 1
    df.loc[df["flat_volume_count_60_base"] >= 1, "base_volume_carry_score"] += 2
    df.loc[df["flat_volume_count_60_base"] >= 2, "base_volume_carry_score"] += 2
    df.loc[(df["base_up_down_vol_ratio_20"] >= 1.10) & (df["base_up_down_vol_ratio_40"] >= 1.05), "base_volume_carry_score"] += 2
    df.loc[(df["base_up_down_vol_ratio_20"] >= 1.15) & (df["base_up_down_vol_ratio_40"] >= 1.10) & (df["base_up_down_vol_ratio_60"] >= 1.05), "base_volume_carry_score"] += 2
    df.loc[(df["base_up_down_vol_ratio_20"] < 0.85) & (df["base_up_down_vol_ratio_40"] < 0.90), "base_volume_carry_score"] -= 2
    df["base_volume_carry_score"] += df["base_limitup_hold_score"].clip(0, 3)
    df["base_volume_carry_score"] = df["base_volume_carry_score"].clip(-4, 15)

    # 结构潜力初筛 0~22：加入基础版首次倍量高点二次确认/黄金扩展入口模型。
    df["base_structure_potential_score"] = 0.0
    df.loc[df["platform40_break_base"], "base_structure_potential_score"] += 5
    df.loc[df["platform20_break_base"] & (~df["platform40_break_base"]), "base_structure_potential_score"] += 3
    df.loc[(df["amp40"] <= 0.22) & (df["close"] >= df["high_40_prev"] * 0.97), "base_structure_potential_score"] += 2
    df.loc[df["break_bottom_reclaim_base"], "base_structure_potential_score"] += 4
    df.loc[df["just_cross_ma250"], "base_structure_potential_score"] += 3
    df.loc[df["just_cross_ma120"], "base_structure_potential_score"] += 2
    key_structure_zone = df["platform40_break_base"] | df["platform20_break_base"] | df["just_cross_ma120"] | df["just_cross_ma250"] | (df["close"] > df["prehigh"])
    df.loc[key_structure_zone & (df["entity_pct"] >= 5) & (df["pos"] >= 0.75), "base_structure_potential_score"] += 3
    # 黄金扩展/二次倍量是结构潜力桶的重点入口，分数可以相对高，但仍受位置、乖离、压力修正。
    df["base_structure_potential_score"] += df["base_fibo_second_confirm_score"].clip(0, 12)
    df["base_structure_potential_score"] = df["base_structure_potential_score"].clip(0, 22)

    # 长周期潜力初筛 0~10。
    df["base_long_cycle_potential_score"] = 0.0
    df.loc[df["long_pos_250"] <= 0.35, "base_long_cycle_potential_score"] += 3
    df.loc[(df["long_pos_250"] > 0.35) & (df["long_pos_250"] <= 0.55), "base_long_cycle_potential_score"] += 2
    df.loc[df["just_cross_ma250"], "base_long_cycle_potential_score"] += 3
    df.loc[df["just_cross_ma120"], "base_long_cycle_potential_score"] += 2
    df.loc[(df["ma120"] >= df["ma120"].shift(20) * 0.98) & (df["close"] >= df["ma120"] * 0.98), "base_long_cycle_potential_score"] += 1
    amp120 = ((df["high"].rolling(120).max() - df["low"].rolling(120).min()) / df["close"].replace(0, pd.NA)).fillna(0)
    amp60 = ((df["high"].rolling(60).max() - df["low"].rolling(60).min()) / df["close"].replace(0, pd.NA)).fillna(0)
    df.loc[(amp60 < amp120 * 0.75) & (amp60 > 0), "base_long_cycle_potential_score"] += 1
    df.loc[(df["long_pos_250"] > 0.85) & ((df["vr1"] > 3.0) | (df["entity_pct"] > 6)), "base_long_cycle_potential_score"] -= 4
    df["base_long_cycle_potential_score"] = df["base_long_cycle_potential_score"].clip(-5, 10)

    # V11.1：大周期高度与买点质量轻量闸门。
    # 这里只做全市场低成本粗筛：不跑完整月线闭环，只识别“月线/年内偏高、远离防守位、空间不足”的不敢买问题。
    df["base_defense_level"] = df[["key_level_base", "ma20", "ma60", "ma120"]].max(axis=1).fillna(0)
    df["base_defense_dist"] = (df["close"] / df["base_defense_level"].replace(0, pd.NA) - 1).fillna(0)
    df.loc[df["base_defense_dist"] < 0, "base_defense_dist"] = 0
    df["base_target_dist"] = df["near_pressure_dist"].where(df["near_pressure_dist"] > 0, df["mid_pressure_dist"]).fillna(0)
    df.loc[df["base_target_dist"] <= 0, "base_target_dist"] = df["overhead_pressure_dist"]
    df["base_risk_reward_ratio"] = (df["base_target_dist"] / df["base_defense_dist"].replace(0, pd.NA)).fillna(0)

    df["base_monthly_height_proxy_score"] = 0.0
    df.loc[df["long_pos_250"] <= 0.35, "base_monthly_height_proxy_score"] += 8
    df.loc[(df["long_pos_250"] > 0.35) & (df["long_pos_250"] <= 0.55), "base_monthly_height_proxy_score"] += 5
    df.loc[(df["long_pos_250"] > 0.55) & (df["long_pos_250"] <= 0.70), "base_monthly_height_proxy_score"] += 2
    df.loc[df["long_pos_250"] > 0.75, "base_monthly_height_proxy_score"] -= 4
    df.loc[df["long_pos_250"] > 0.85, "base_monthly_height_proxy_score"] -= 6
    df.loc[(df["bias20"] <= 0.10) & (df["bias60"] <= 0.12), "base_monthly_height_proxy_score"] += 3
    df.loc[(df["bias20"] > 0.18) | (df["bias60"] > 0.20), "base_monthly_height_proxy_score"] -= 4
    df.loc[(df["near_pressure_dist"] > 0) & (df["near_pressure_dist"] < 0.08), "base_monthly_height_proxy_score"] -= 4
    df["base_monthly_height_proxy_score"] = df["base_monthly_height_proxy_score"].clip(-10, 12)

    df["base_trade_quality_score"] = 0.0
    df.loc[(df["base_defense_dist"] >= 0.005) & (df["base_defense_dist"] <= 0.05), "base_trade_quality_score"] += 7
    df.loc[(df["base_defense_dist"] > 0.05) & (df["base_defense_dist"] <= 0.08), "base_trade_quality_score"] += 4
    df.loc[df["base_defense_dist"] > 0.10, "base_trade_quality_score"] -= 5
    df.loc[df["base_target_dist"] >= 0.15, "base_trade_quality_score"] += 5
    df.loc[(df["base_target_dist"] >= 0.08) & (df["base_target_dist"] < 0.15), "base_trade_quality_score"] += 2
    df.loc[(df["base_target_dist"] > 0) & (df["base_target_dist"] < 0.06), "base_trade_quality_score"] -= 5
    df.loc[df["base_risk_reward_ratio"] >= 2.0, "base_trade_quality_score"] += 6
    df.loc[(df["base_risk_reward_ratio"] >= 1.5) & (df["base_risk_reward_ratio"] < 2.0), "base_trade_quality_score"] += 3
    df.loc[(df["base_risk_reward_ratio"] > 0) & (df["base_risk_reward_ratio"] < 1.2), "base_trade_quality_score"] -= 8
    df["base_trade_quality_score"] = df["base_trade_quality_score"].clip(-12, 18)

    # 初级风险与追高闸门 -20~0。
    df["base_risk_penalty"] = 0.0
    df.loc[(df["vr1"] > 5.0) & (df["volr"] > 5.0), "base_risk_penalty"] -= 8
    df.loc[(df["entity_pct"] > 8.0) & (df["bias20"] > 0.15), "base_risk_penalty"] -= 6
    df.loc[(df["break_rate"] > 0.08) & (df["distance_to_key_base"] > 0.08), "base_risk_penalty"] -= 5
    df.loc[(df["near_pressure_dist"] > 0) & (df["near_pressure_dist"] < 0.05) & ((df["pct_chg"] >= 5) | (df["entity_pct"] >= 5)), "base_risk_penalty"] -= 8
    df.loc[(df["base_rsi"] > 80) | (df["base_cci"] > 250), "base_risk_penalty"] -= 4
    df.loc[(df["long_pos_250"] > 0.85) & (df["entity_pct"] >= 5) & (df["vr1"] > 2.0), "base_risk_penalty"] -= 8
    df.loc[(df["bias20"] > 0.20) & (df["bias60"] > 0.20), "base_risk_penalty"] -= 8
    df.loc[df["limit_volume_mode"].eq("涨停分歧爆量"), "base_risk_penalty"] -= 5
    df["base_risk_penalty"] = df["base_risk_penalty"].clip(-25, 0)

    # V11基础总分：不再让攻击/倍量主导。
    # 大周期位置 + 结构潜力 + 买点质量为主，量能和攻击质量只做确认。
    df["base_total_score"] = (
        df["base_attack_quality_score"] * 0.55
        + df["base_position_reward_score"] * 0.85
        + df["base_volume_carry_score"] * 0.80
        + df["base_structure_potential_score"] * 1.20
        + df["base_long_cycle_potential_score"] * 1.00
        + df["base_monthly_height_proxy_score"] * 1.20
        + df["base_trade_quality_score"] * 1.10
        + df["base_risk_penalty"] * 1.20
    ).clip(0, 100)

    # 强势但交易质量差的票只能进入观察，不让其仅凭倍量/强阳挤占前排。
    poor_trade_quality_base = (df["base_trade_quality_score"] < 0) | ((df["base_risk_reward_ratio"] > 0) & (df["base_risk_reward_ratio"] < 1.2))
    high_attack_without_structure_base = (df["base_attack_quality_score"] >= 26) & (df["base_structure_potential_score"] < 6) & (df["base_monthly_height_proxy_score"] < 2)
    df.loc[poor_trade_quality_base & high_attack_without_structure_base, "base_total_score"] = df.loc[poor_trade_quality_base & high_attack_without_structure_base, "base_total_score"].clip(upper=62)

    # 兼容旧字段：base_score 改为V10基础总分；原模型折算另存 score_base_model_legacy。
    df["base_score"] = df["base_total_score"]

    # 分桶：用于进入深度评分的配额选择。
    df["base_bucket"] = "健康攻击"
    df.loc[(df["long_pos_250"] <= 0.55) & ((df["just_cross_ma120"] | df["just_cross_ma250"]) | (df["base_long_cycle_potential_score"] >= 5)), "base_bucket"] = "低位修复"
    df.loc[(df["base_volume_carry_score"] >= 8) & (df["base_attack_quality_score"] < 28), "base_bucket"] = "量价承接"
    df.loc[((df["base_structure_potential_score"] >= 8) | (df["base_fibo_second_confirm_score"] >= 6)) & (df["base_risk_penalty"] > -12), "base_bucket"] = "结构潜力"
    df.loc[(df["base_trade_quality_score"] >= 10) & (df["base_monthly_height_proxy_score"] >= 3) & (df["base_structure_potential_score"] >= 4), "base_bucket"] = "交易质量"
    strong_watch_cond = ((df["pct_chg"] >= 7) | df["limit_up_base"] | (df["entity_pct"] >= 7)) & ((df["base_risk_penalty"] <= -4) | (df["base_trade_quality_score"] < 0))
    df.loc[strong_watch_cond, "base_bucket"] = "强势观察"

    df["base_bucket_rank_score"] = df["base_total_score"].copy()
    df.loc[df["base_bucket"].eq("低位修复"), "base_bucket_rank_score"] += 3
    df.loc[df["base_bucket"].eq("量价承接"), "base_bucket_rank_score"] += 2
    df.loc[df["base_bucket"].eq("结构潜力"), "base_bucket_rank_score"] += 2
    df.loc[df["base_bucket"].eq("交易质量"), "base_bucket_rank_score"] += 4
    df.loc[df["base_bucket"].eq("强势观察"), "base_bucket_rank_score"] -= 7
    df.loc[df["short_ma_volume_entity_start"] & (df["long_pos_250"] <= 0.60) & (df["bias20"] <= 0.15), "base_bucket_rank_score"] += 4
    df.loc[df["base_fibo_second_confirm_score"] >= 8, "base_bucket_rank_score"] += 5
    df.loc[(df["base_risk_penalty"] <= -12), "base_bucket_rank_score"] -= 8

    return df.tail(CHECK_DAYS)

def _join_nonempty_flags(flags):
    return "；".join([str(x) for x in flags if str(x).strip()])


def build_chase_risk_flags(row):
    """
    V9.1：追高风险闸门解释。这里不改变基础评分，只把多项短线追高风险合并成
    可解释的flag，后续用于封顶、分池和报告展示。
    """
    flags = []
    vr1 = safe_float(row.get("vr1", 0))
    volr = safe_float(row.get("volr", 0))
    pct = safe_float(row.get("pct_chg", 0))
    entity_pct = safe_float(row.get("entity_pct", 0))
    break_rate = safe_float(row.get("break_rate", 0))
    bias20 = safe_float(row.get("bias20", 0))
    bias60 = safe_float(row.get("bias60", 0))
    rsi = safe_float(row.get("rsi", 0))
    cci = safe_float(row.get("cci", 0))
    near_p = safe_float(row.get("near_pressure_dist", 0))
    mid_p = safe_float(row.get("mid_pressure_dist", 0))
    dist_key = safe_float(row.get("distance_to_key", 0))
    long_pos = safe_float(row.get("long_pos_250", 0))
    score_structure = safe_float(row.get("score_structure_core", 0))
    score_monthly = safe_float(row.get("score_monthly_cycle", 0))
    score_advanced = safe_float(row.get("score_advanced_ao_kou", 0))
    score_fibo = safe_float(row.get("score_fibo_reclaim", 0))
    is_limit_or_attack = bool(row.get("limit_up", False)) or pct >= 7.0 or entity_pct >= 7.0

    if vr1 > 5.0 and volr > 5.0:
        flags.append("昨比/20日比双极端放量")
    elif vr1 > 3.5 or volr > 4.5:
        flags.append("极端放量")
    if entity_pct > 8.0 and bias20 > 0.18:
        flags.append("大实体叠加20日高乖离")
    if break_rate > 0.08 and dist_key > 0.06:
        flags.append("突破幅度过大且远离关键位")
    if bias20 > 0.20 and bias60 > 0.18:
        flags.append("20/60日乖离同步过高")
    elif bias20 > 0.15:
        flags.append("20日乖离偏高")
    if rsi > 75 and cci > 200:
        flags.append("RSI/CCI组合过热")
    if near_p > 0 and near_p < 0.02 and is_limit_or_attack:
        flags.append("强攻贴近近端压力")
    elif near_p > 0 and near_p < 0.05:
        flags.append("近端压力过近")
    if mid_p > 0 and mid_p < 0.03 and is_limit_or_attack:
        flags.append("中层压力过近")
    if long_pos > 0.70 and is_limit_or_attack:
        flags.append("年内位置偏高仍强攻")
    if is_limit_or_attack and score_structure <= 0 and score_monthly < 8 and score_advanced < 7 and score_fibo < 6:
        flags.append("单日强攻缺少核心结构共振")

    return flags


def apply_chase_risk_gate(merged):
    """
    V9.1：追高风险闸门。
    设计思想：不是否定涨停/强阳，而是把“强势观察票”和“可优先下单候选”分开。
    多项短线追高风险叠加时，不再只线性扣分，而是对综合分做上限封顶。
    """
    if merged is None or merged.empty:
        return merged

    merged = merged.copy()
    merged["chase_risk_flags"] = ""
    merged["chase_risk_count"] = 0
    merged["score_chase_penalty"] = 0.0
    merged["chase_score_cap"] = 999.0
    merged["candidate_pool"] = "优先候选池"
    merged["candidate_pool_reason"] = "买点与风险收益比较匹配"

    for idx, row in merged.iterrows():
        flags = build_chase_risk_flags(row)
        cnt = len(flags)
        vr1 = safe_float(row.get("vr1", 0))
        volr = safe_float(row.get("volr", 0))
        pct = safe_float(row.get("pct_chg", 0))
        entity_pct = safe_float(row.get("entity_pct", 0))
        break_rate = safe_float(row.get("break_rate", 0))
        bias20 = safe_float(row.get("bias20", 0))
        bias60 = safe_float(row.get("bias60", 0))
        rsi = safe_float(row.get("rsi", 0))
        cci = safe_float(row.get("cci", 0))
        near_p = safe_float(row.get("near_pressure_dist", 0))
        mid_p = safe_float(row.get("mid_pressure_dist", 0))
        dist_key = safe_float(row.get("distance_to_key", 0))
        long_pos = safe_float(row.get("long_pos_250", 0))
        score_carry = safe_float(row.get("score_carry_structure", 0))
        score_key_distance = safe_float(row.get("score_key_distance", 0))
        score_structure = safe_float(row.get("score_structure_core", 0))
        score_monthly = safe_float(row.get("score_monthly_cycle", 0))
        score_advanced = safe_float(row.get("score_advanced_ao_kou", 0))
        score_fibo = safe_float(row.get("score_fibo_reclaim", 0))
        is_limit_or_attack = bool(row.get("limit_up", False)) or pct >= 7.0 or entity_pct >= 7.0

        caps = []
        if vr1 > 5.0 and volr > 5.0:
            caps.append(82.0)
        if entity_pct > 8.0 and bias20 > 0.18:
            caps.append(82.0)
        if break_rate > 0.08 and dist_key > 0.06:
            caps.append(83.0)
        if rsi > 75 and cci > 200:
            caps.append(85.0)
        if bias20 > 0.20 and bias60 > 0.18:
            caps.append(83.0)
        if near_p > 0 and near_p < 0.02 and is_limit_or_attack:
            caps.append(80.0)
        if mid_p > 0 and mid_p < 0.02 and is_limit_or_attack:
            caps.append(82.0)
        if long_pos > 0.75 and is_limit_or_attack:
            caps.append(82.0)
        if score_key_distance <= 0 and is_limit_or_attack:
            caps.append(84.0)
        if cnt >= 3:
            caps.append(82.0)
        if cnt >= 4:
            caps.append(79.0)
        if cnt >= 5:
            caps.append(76.0)

        # 结构确实非常硬、月线共振很强时，允许封顶略微放宽，但仍不解除观察属性。
        strong_structure_buffer = (score_structure >= 14 and score_monthly >= 8) or score_advanced >= 10 or score_fibo >= 7
        if strong_structure_buffer and caps:
            caps = [min(86.0, c + 3.0) for c in caps]

        cap = min(caps) if caps else 999.0

        penalty = 0.0
        if cnt >= 2:
            penalty -= min(8.0, 2.0 * (cnt - 1))
        if is_limit_or_attack and score_carry < 2.0:
            penalty -= 3.0
        if is_limit_or_attack and near_p >= 0 and near_p < 0.05:
            penalty -= 3.0
        if is_limit_or_attack and dist_key > 0.08:
            penalty -= 3.0
        if vr1 > 5.0 and volr > 5.0:
            penalty -= 3.0
        penalty = max(-16.0, penalty)

        raw_total = safe_float(row.get("total_score", 0.0))
        adjusted = raw_total + penalty
        if cap < 999.0:
            adjusted = min(adjusted, cap)

        pool = "优先候选池"
        pool_reason = "买点与风险收益比较匹配"
        if cnt >= 3 or (is_limit_or_attack and (near_p >= 0 and near_p < 0.05)) or (is_limit_or_attack and bias20 > 0.18) or cap <= 82:
            pool = "强势观察池"
            pool_reason = "短线强攻/追高风险较高，次日只看承接，不作为一号员工优先下单候选"
        if score_carry >= 5 and dist_key <= 0.05 and bias20 < 0.16 and (near_p == 0 or near_p >= 0.08) and cnt <= 2:
            pool = "优先候选池"
            pool_reason = "承接较好且离关键位不远，允许进入优先候选"
        if score_structure <= 0 and score_monthly < 4 and score_advanced < 7 and score_fibo < 6:
            pool = "结构观察池"
            pool_reason = "核心结构确认不足，不进入优先候选"
            adjusted = min(adjusted, 78.0)

        merged.at[idx, "chase_risk_flags"] = _join_nonempty_flags(flags)
        merged.at[idx, "chase_risk_count"] = int(cnt)
        merged.at[idx, "score_chase_penalty"] = float(penalty)
        merged.at[idx, "chase_score_cap"] = float(cap if cap < 999.0 else 0.0)
        merged.at[idx, "candidate_pool"] = pool
        merged.at[idx, "candidate_pool_reason"] = pool_reason
        merged.at[idx, "total_score"] = float(adjusted)

    return merged

def calc_deep_rows(df, code):
    if df is None or len(df) < 260:
        return pd.DataFrame()

    df = calc_base_full(df)

    monthly_ctx = detect_monthly_midline_reclaim(df)

    limit_threshold = get_limit_threshold(code)

    extra = pd.DataFrame(index=df.index)

    extra["score_base_model"] = (df["score"] / 100 * 22).clip(0, 22)

    extra["is_up"] = df["close"] > df["open"]
    extra["is_down"] = df["close"] < df["open"]

    extra["up_days_20"] = extra["is_up"].rolling(20).sum()
    extra["down_days_20"] = extra["is_down"].rolling(20).sum()

    extra["up_vol"] = df["volume"].where(extra["is_up"], 0)
    extra["down_vol"] = df["volume"].where(extra["is_down"], 0)

    extra["up_vol_avg_20"] = extra["up_vol"].rolling(20).sum() / extra["up_days_20"].replace(0, pd.NA)
    extra["down_vol_avg_20"] = extra["down_vol"].rolling(20).sum() / extra["down_days_20"].replace(0, pd.NA)
    extra["up_down_vol_ratio_20"] = extra["up_vol_avg_20"] / extra["down_vol_avg_20"].replace(0, pd.NA)

    # 20/40/60日阳阴量价结构：真实参与后台评分，而不是只在报告中展示。
    for _w in [40, 60]:
        extra[f"up_days_{_w}"] = extra["is_up"].rolling(_w).sum()
        extra[f"down_days_{_w}"] = extra["is_down"].rolling(_w).sum()
        extra[f"up_vol_avg_{_w}"] = extra["up_vol"].rolling(_w).sum() / extra[f"up_days_{_w}"].replace(0, pd.NA)
        extra[f"down_vol_avg_{_w}"] = extra["down_vol"].rolling(_w).sum() / extra[f"down_days_{_w}"].replace(0, pd.NA)
        extra[f"up_down_vol_ratio_{_w}"] = extra[f"up_vol_avg_{_w}"] / extra[f"down_vol_avg_{_w}"].replace(0, pd.NA)

    extra["up_ratio_20"] = extra["up_days_20"] / 20
    extra["up_ratio_40"] = extra["up_days_40"] / 40
    extra["up_ratio_60"] = extra["up_days_60"] / 60

    up_body_pct = ((df["close"] - df["open"]) / df["open"].replace(0, pd.NA)).where(extra["is_up"], 0)
    down_body_pct = ((df["open"] - df["close"]) / df["open"].replace(0, pd.NA)).where(extra["is_down"], 0)
    extra["up_body_avg_60"] = up_body_pct.rolling(60).sum() / extra["up_days_60"].replace(0, pd.NA)
    extra["down_body_avg_60"] = down_body_pct.rolling(60).sum() / extra["down_days_60"].replace(0, pd.NA)

    extra["score_yang_yin_volume"] = 0.0
    extra.loc[extra["up_ratio_20"] >= 0.55, "score_yang_yin_volume"] += 0.6
    extra.loc[extra["up_ratio_40"] >= 0.52, "score_yang_yin_volume"] += 0.6
    extra.loc[extra["up_ratio_60"] >= 0.50, "score_yang_yin_volume"] += 0.6
    extra.loc[(extra["up_ratio_20"] > extra["up_ratio_40"]) & (extra["up_ratio_40"] > extra["up_ratio_60"]), "score_yang_yin_volume"] += 0.2
    extra.loc[extra["up_ratio_20"] < 0.35, "score_yang_yin_volume"] -= 0.5
    extra.loc[extra["up_ratio_40"] < 0.40, "score_yang_yin_volume"] -= 0.5
    extra.loc[extra["up_ratio_60"] < 0.42, "score_yang_yin_volume"] -= 0.5
    extra.loc[extra["up_down_vol_ratio_20"] >= 1.15, "score_yang_yin_volume"] += 0.8
    extra.loc[extra["up_down_vol_ratio_40"] >= 1.20, "score_yang_yin_volume"] += 1.0
    extra.loc[extra["up_down_vol_ratio_60"] >= 1.25, "score_yang_yin_volume"] += 1.0
    extra.loc[(extra["up_down_vol_ratio_20"] >= 1.10) & (extra["up_down_vol_ratio_40"] >= 1.10) & (extra["up_down_vol_ratio_60"] >= 1.10), "score_yang_yin_volume"] += 0.2
    extra.loc[extra["up_down_vol_ratio_20"] < 0.85, "score_yang_yin_volume"] -= 0.8
    extra.loc[extra["up_down_vol_ratio_40"] < 0.90, "score_yang_yin_volume"] -= 0.8
    extra.loc[extra["up_down_vol_ratio_60"] < 0.90, "score_yang_yin_volume"] -= 1.0
    extra.loc[extra["up_body_avg_60"] > extra["down_body_avg_60"], "score_yang_yin_volume"] += 0.7
    extra["score_yang_yin_volume"] = extra["score_yang_yin_volume"].clip(-4, 12)

    extra["is_beiliang"] = df["vr1"].between(1.8, 2.5)
    extra["beiliang_up"] = extra["is_beiliang"] & (df["close"] > df["close"].shift(1)) & (df["close"] > df["open"])
    extra["beiliang_count_20"] = extra["beiliang_up"].rolling(20).sum()
    extra["beiliang_count_30"] = extra["beiliang_up"].rolling(30).sum()

    extra["is_flat_volume"] = (
        (df["volume"] >= df["volume"].shift(1) * 0.95)
        & (df["volume"] <= df["volume"].shift(1) * 1.05)
    )

    # 倍量后平量：不仅量要平，价格也不能走坏。
    # T-1日为标准阳倍量；T日成交量与T-1日偏差≤5%；T日不破前一日实体中位，若前一日是突破阳线则不能跌回关键位。
    extra["prev_entity_mid"] = (df[["open", "close"]].max(axis=1).shift(1) + df[["open", "close"]].min(axis=1).shift(1)) / 2
    extra["beiliang_flat"] = (
        extra["is_beiliang"].shift(1).fillna(False)
        & extra["is_flat_volume"]
        & (df["close"] >= extra["prev_entity_mid"])
        & (df["close"] >= df["close"].shift(1) * 0.985)
    )

    # 连续倍倍量：今天相对昨天倍量，昨天相对前天也倍量。
    # 按用户要求：连续倍倍量本身不加分，也不直接扣分；关键看后续第4天/后续一日承接量能是否明显退潮。
    extra["beibeiliang"] = extra["is_beiliang"] & extra["is_beiliang"].shift(1).fillna(False)
    extra["beibeiliang_peak_volume"] = pd.concat([df["volume"], df["volume"].shift(1)], axis=1).max(axis=1)
    extra["beibeiliang_shrink_rate_after"] = 1 - (df["volume"] / extra["beibeiliang_peak_volume"].shift(2).replace(0, pd.NA))
    extra["beibeiliang_short_pullback_risk"] = (
        extra["beibeiliang"].shift(2).fillna(False)
        & (extra["beibeiliang_shrink_rate_after"] > 0.30)
    )

    # 分散健康倍量：近60日内标准阳倍量多次出现，但相邻有效倍量至少间隔3日，避免奖励连续情绪爆量。
    healthy_arr = list(extra["beiliang_up"].fillna(False).astype(bool).values)
    scattered_counts = []
    for _i in range(len(healthy_arr)):
        start_i = max(0, _i - 59)
        idxs = [j for j in range(start_i, _i + 1) if healthy_arr[j]]
        selected = []
        last = -999
        for j in idxs:
            if j - last >= 3:
                selected.append(j)
                last = j
        scattered_counts.append(len(selected))
    extra["scattered_beiliang_count_60"] = scattered_counts
    extra["flat_volume_count_60"] = extra["beiliang_flat"].rolling(60).sum()

    extra["yang_ladder"] = (
        extra["is_up"]
        & extra["is_up"].shift(1).fillna(False)
        & extra["is_up"].shift(2).fillna(False)
        & (df["volume"] > df["volume"].shift(1))
        & (df["volume"].shift(1) > df["volume"].shift(2))
    )

    extra["low_yang_ladder"] = extra["yang_ladder"] & (df["long_pos_250"] <= 0.45)
    extra["vol_price_sync"] = (df["volr"] >= 1.5) & (df["close"] > df["close"].shift(1))
    extra["extreme_vol"] = (df["volr"] > 4.5) | (df["vr1"] > 3.5)

    extra["score_volume_structure"] = 0
    # 倍量必须严格为：今日成交量 / 昨日成交量 > 1.8 且 < 2.5。超过2.5不是健康倍量，不能继续按倍量高分奖励。
    extra.loc[extra["is_beiliang"], "score_volume_structure"] += 4
    extra.loc[extra["beiliang_up"], "score_volume_structure"] += 4
    extra.loc[extra["beiliang_flat"], "score_volume_structure"] += 7
    extra.loc[extra["yang_ladder"], "score_volume_structure"] += 3
    extra.loc[extra["low_yang_ladder"], "score_volume_structure"] += 2
    extra.loc[extra["vol_price_sync"] & (df["volr"] <= 3.5), "score_volume_structure"] += 2
    # 分散健康倍量与倍量后平量是真正的承接优点，允许明显加分。
    extra.loc[extra["scattered_beiliang_count_60"] >= 3, "score_volume_structure"] += 2
    extra.loc[extra["scattered_beiliang_count_60"] >= 5, "score_volume_structure"] += 2
    extra.loc[extra["flat_volume_count_60"] >= 2, "score_volume_structure"] += 2
    # 极端放量更多代表短线高潮/分歧，后续放入风险扣分。
    extra.loc[extra["extreme_vol"], "score_volume_structure"] -= 4
    # 连续倍倍量本身中性：不加分、不扣分；若后续承接缩量超过30%，放入短线风险项。
    extra["score_volume_structure"] = extra["score_volume_structure"].clip(0, 18)

    extra["limit_up"] = df["pct_chg"] >= limit_threshold
    extra["limit_real_top"] = df[["open", "close"]].max(axis=1)
    extra["limit_real_bottom"] = df[["open", "close"]].min(axis=1)
    extra["limit_real_mid"] = (extra["limit_real_top"] + extra["limit_real_bottom"]) / 2

    # 涨停后三日实体承接模型：
    # 1）后三日不破涨停实顶：最强；2）后三日收盘严格不破实体中位：次强；
    # 3）后三日不破实底：第三档；4）跌破实底：不加分。
    limit_hold_scores = []
    limit_hold_levels = []
    limit_hold_ref_dates = []
    for _i in range(len(df)):
        best_score = 0.0
        best_level = "无涨停后三日承接"
        best_date = ""
        for _j in range(max(0, _i - 3), _i):
            if not bool(extra["limit_up"].iloc[_j]):
                continue
            hold_seg = df.iloc[_j + 1:_i + 1]
            if hold_seg.empty or len(hold_seg) > 3:
                continue
            top = safe_float(extra["limit_real_top"].iloc[_j])
            mid = safe_float(extra["limit_real_mid"].iloc[_j])
            bottom = safe_float(extra["limit_real_bottom"].iloc[_j])
            min_low = safe_float(hold_seg["low"].min())
            min_close = safe_float(hold_seg["close"].min())
            score = 0.0
            level = "跌破涨停实底，不给涨停承接分"
            if top > 0 and min_low >= top * 0.995:
                score = 8.0
                level = "最强承接：后三日不破涨停实顶"
            elif mid > 0 and min_close >= mid:
                score = 5.0
                level = "次强承接：后三日收盘不破涨停实体中位"
            elif bottom > 0 and min_low >= bottom * 0.995:
                score = 2.0
                level = "第三档承接：后三日不破涨停实底"
            if score > best_score:
                best_score = score
                best_level = level
                best_date = str(df["date"].iloc[_j]) if "date" in df.columns else ""
        limit_hold_scores.append(best_score)
        limit_hold_levels.append(best_level)
        limit_hold_ref_dates.append(best_date)
    extra["score_limitup_hold_3d"] = limit_hold_scores
    extra["limitup_hold_level"] = limit_hold_levels
    extra["limitup_hold_ref_date"] = limit_hold_ref_dates

    extra["hold_limit_top"] = extra["limit_up"].shift(1).fillna(False) & (df["low"] >= extra["limit_real_top"].shift(1) * 0.995)
    extra["hold_limit_mid"] = extra["limit_up"].shift(1).fillna(False) & (df["low"] >= extra["limit_real_mid"].shift(1))
    extra["hold_limit_bottom"] = extra["limit_up"].shift(1).fillna(False) & (df["low"] >= extra["limit_real_bottom"].shift(1))

    extra["bull_engulf"] = (
        (df["close"] > df["open"])
        & (df["close"].shift(1) < df["open"].shift(1))
        & (df["close"] >= df["open"].shift(1))
        & (df["open"] <= df["close"].shift(1))
    )

    extra["engulf_vol_ratio"] = df["volume"] / df["volume"].shift(1)

    extra["score_engulf_quality"] = 0
    extra.loc[extra["bull_engulf"] & (extra["engulf_vol_ratio"] < 0.8), "score_engulf_quality"] = 1
    extra.loc[extra["bull_engulf"] & (extra["engulf_vol_ratio"] >= 0.8) & (extra["engulf_vol_ratio"] < 1.0), "score_engulf_quality"] = 2
    extra.loc[extra["bull_engulf"] & (extra["engulf_vol_ratio"] >= 1.0) & (extra["engulf_vol_ratio"] <= 1.5), "score_engulf_quality"] = 5
    extra.loc[extra["bull_engulf"] & (extra["engulf_vol_ratio"] > 1.5) & (extra["engulf_vol_ratio"] <= 2.0), "score_engulf_quality"] = 3
    extra.loc[extra["bull_engulf"] & (extra["engulf_vol_ratio"] > 2.0), "score_engulf_quality"] = 0

    extra["bull_engulf_score_20"] = extra["score_engulf_quality"].rolling(20).sum().clip(0, 6)
    extra["bull_engulf_count_20"] = extra["bull_engulf"].rolling(20).sum()
    # 50日高质量阳包阴：数量、量能比和后续不破实体中位都要考虑，不能只数次数。
    extra["bull_engulf_quality"] = (
        extra["bull_engulf"]
        & (extra["engulf_vol_ratio"] >= 1.0)
        & (extra["engulf_vol_ratio"] <= 1.8)
        & (df["pos"] >= 0.60)
    )
    extra["bull_engulf_count_50"] = extra["bull_engulf"].rolling(50).sum()
    extra["bull_engulf_quality_count_50"] = extra["bull_engulf_quality"].rolling(50).sum()
    extra["score_bull_engulf_50"] = 0.0
    extra.loc[extra["bull_engulf_quality_count_50"] >= 2, "score_bull_engulf_50"] += 1.0
    extra.loc[extra["bull_engulf_quality_count_50"] >= 3, "score_bull_engulf_50"] += 1.5
    extra.loc[extra["bull_engulf_quality_count_50"] >= 5, "score_bull_engulf_50"] += 2.0
    extra["score_bull_engulf_50"] = extra["score_bull_engulf_50"].clip(0, 4)
    extra["bull_engulf_count_50"] = extra["bull_engulf"].rolling(50).sum()
    extra["bull_engulf_score_50"] = extra["score_engulf_quality"].rolling(50).sum().clip(0, 4)

    big_down_prev = (
        (df["close"].shift(1) < df["open"].shift(1))
        & (((df["open"].shift(1) - df["close"].shift(1)) / df["close"].shift(2)) >= 0.04)
        & (df["volume"].shift(1) >= df["vol_ma"].shift(1) * 1.3)
    )

    extra["breakup_line"] = (
        big_down_prev
        & (df["open"] > df["open"].shift(1))
        & (df["close"] > df["open"])
        & (df["volume"] >= df["volume"].shift(1) * 0.8)
    )

    # 三阴战法：回撤中连续三根阴线，每根阴线开盘均高于前一日收盘。
    # 只作为轻度结构分，需最好出现在此前已有资金推进/结构突破背景下。
    three_yin_core = (
        (df["close"].shift(3) < df["open"].shift(3))
        & (df["close"].shift(2) < df["open"].shift(2))
        & (df["close"].shift(1) < df["open"].shift(1))
        & (df["open"].shift(2) > df["close"].shift(3))
        & (df["open"].shift(1) > df["close"].shift(2))
    )
    three_yin_not_broken = (
        (df["volume"].shift(1) <= df["vol_ma"].shift(1) * 1.8)
        & (df["volume"].shift(2) <= df["vol_ma"].shift(2) * 1.8)
        & (df["volume"].shift(3) <= df["vol_ma"].shift(3) * 1.8)
    )
    extra["three_yin_tactic"] = three_yin_core & three_yin_not_broken
    extra["three_down_gap_reversal"] = (
        extra["three_yin_tactic"]
        & (df["close"] > df["open"])
        & (df["close"] > df["high"].shift(1))
    )

    # V11.2 双阳夹阴 / 分歧反包承接模型：
    # 第一阳先启动，第二阴制造威慑但不有效破坏第一阳实底，第三阳拼量反包重新夺回主动权。
    # 该模型不是独立主结构，只作为关键位附近的量价承接/短线行为确认。
    first_yang = df["close"].shift(2) > df["open"].shift(2)
    middle_yin = df["close"].shift(1) < df["open"].shift(1)
    third_yang = df["close"] > df["open"]
    first_real_bottom = pd.concat([df["open"].shift(2), df["close"].shift(2)], axis=1).min(axis=1)
    first_real_top = pd.concat([df["open"].shift(2), df["close"].shift(2)], axis=1).max(axis=1)
    first_real_mid = (first_real_bottom + first_real_top) / 2
    middle_entity_top = pd.concat([df["open"].shift(1), df["close"].shift(1)], axis=1).max(axis=1)
    middle_entity_bottom = pd.concat([df["open"].shift(1), df["close"].shift(1)], axis=1).min(axis=1)
    middle_body_pct = ((df["open"].shift(1) - df["close"].shift(1)) / df["close"].shift(2).replace(0, pd.NA) * 100).fillna(0)

    # 威慑：允许跌破第一阳实体中位；若第二阴实体较大，也视为有威慑。
    middle_has_threat = (df["close"].shift(1) < first_real_mid) | (df["low"].shift(1) < first_real_mid) | (middle_body_pct >= 1.2)
    # 破坏控制：第二阴收盘不应有效跌破第一阳实底，最多允许约0.3%的假破。
    middle_not_broken = df["close"].shift(1) >= first_real_bottom * 0.997
    # 中间阴线量能不能极端爆量。超过第一阳1.6倍或20日均量2.2倍，视为分歧过大，不给高质量分。
    middle_vol_not_extreme = (df["volume"].shift(1) <= df["volume"].shift(2) * 1.60) & (df["volume"].shift(1) <= df["vol_ma"].shift(1) * 2.20)
    # 第三阳必须拼量反包：至少收复阴线实体顶部；更强是突破阴线高点。
    third_recover_body = df["close"] >= middle_entity_top
    third_recover_high = df["close"] >= df["high"].shift(1)
    third_volume_match = df["volume"] >= df["volume"].shift(1) * 0.90
    third_volume_strong = df["volume"] >= df["volume"].shift(1) * 1.05
    third_close_strong = df["pos"] >= 0.70
    # 背景：低/中位、靠近关键位、或前面已有资金/结构迹象；避免高位普通震荡被重奖。
    double_yang_context = (
        (df["long_pos_250"] <= 0.70)
        | (extra.get("score_key_pullback_hold", pd.Series(0, index=df.index)) > 0)
        | (extra.get("scattered_beiliang_count_60", pd.Series(0, index=df.index)) >= 2)
        | (extra.get("flat_volume_count_60", pd.Series(0, index=df.index)) >= 1)
    )
    extra["double_yang_sandwich_yin"] = (
        first_yang
        & middle_yin
        & third_yang
        & middle_has_threat
        & middle_not_broken
        & middle_vol_not_extreme
        & third_recover_body
        & third_volume_match
        & third_close_strong
        & double_yang_context
    )
    extra["score_double_yang_sandwich"] = 0.0
    extra.loc[extra["double_yang_sandwich_yin"], "score_double_yang_sandwich"] += 2.0
    extra.loc[extra["double_yang_sandwich_yin"] & third_recover_high, "score_double_yang_sandwich"] += 1.2
    extra.loc[extra["double_yang_sandwich_yin"] & third_volume_strong, "score_double_yang_sandwich"] += 0.8
    extra.loc[extra["double_yang_sandwich_yin"] & (df["pos"] >= 0.85), "score_double_yang_sandwich"] += 0.5
    # 若第二阴虽未有效破坏，但收盘略破第一阳实底，第三阳必须更强，未突破阴线高点则降档。
    slight_break_first_bottom = (df["close"].shift(1) < first_real_bottom) & (df["close"].shift(1) >= first_real_bottom * 0.997)
    extra.loc[extra["double_yang_sandwich_yin"] & slight_break_first_bottom & (~third_recover_high), "score_double_yang_sandwich"] -= 0.8
    # 高位、乖离大、压力位附近的双阳夹阴不能重奖。
    extra.loc[extra["double_yang_sandwich_yin"] & ((df["long_pos_250"] > 0.75) | (df["bias20"] > 0.15)), "score_double_yang_sandwich"] -= 1.5
    extra["score_double_yang_sandwich"] = extra["score_double_yang_sandwich"].clip(0, 5.5)
    extra["double_yang_sandwich_desc"] = ""
    extra.loc[extra["double_yang_sandwich_yin"], "double_yang_sandwich_desc"] = "双阳夹阴：第二阴有威慑但未有效破第一阳实底，第三阳拼量收复阴线实体顶部"
    extra.loc[extra["double_yang_sandwich_yin"] & third_recover_high, "double_yang_sandwich_desc"] = "双阳夹阴：第二阴有威慑但未有效破第一阳实底，第三阳拼量反包阴线高点"
    extra.loc[extra["double_yang_sandwich_yin"] & slight_break_first_bottom, "double_yang_sandwich_desc"] = extra.loc[extra["double_yang_sandwich_yin"] & slight_break_first_bottom, "double_yang_sandwich_desc"] + "；第二阴略破第一阳实底约0.3%以内，需看第三阳强度"

    extra["score_behavior"] = 0
    extra.loc[extra["hold_limit_bottom"], "score_behavior"] += 3
    extra.loc[extra["hold_limit_mid"], "score_behavior"] += 5
    extra.loc[extra["hold_limit_top"], "score_behavior"] += 8
    extra["score_behavior"] += extra["bull_engulf_score_20"]
    extra.loc[extra["breakup_line"], "score_behavior"] += 6
    extra.loc[extra["three_yin_tactic"], "score_behavior"] += 1.5
    extra.loc[extra["three_down_gap_reversal"], "score_behavior"] += 3
    extra["score_behavior"] += extra["score_double_yang_sandwich"].fillna(0)
    # 涨停后三日实体承接是独立承接模型，加入行为承接分，但后续有总承接封顶。
    extra["score_behavior"] += extra["score_limitup_hold_3d"].clip(0, 8)
    extra["score_behavior"] = extra["score_behavior"].clip(0, 18)

    extra["platform_high_20"] = df["high"].rolling(20).max().shift(1)
    extra["platform_break_vol"] = (df["close"] > extra["platform_high_20"]) & (df["volr"] >= 1.8)

    extra["new_low_20"] = df["low"] <= df["low"].rolling(20).min().shift(1)
    extra["break_bottom_reversal"] = (
        extra["new_low_20"]
        & (df["close"] > df["open"])
        & (df["close"] > df["close"].shift(1))
        & (df["pos"] >= 0.6)
    )

    real_body_abs = (df["close"] - df["open"]).abs()
    lower_shadow = df[["open", "close"]].min(axis=1) - df["low"]
    upper_shadow = df["high"] - df[["open", "close"]].max(axis=1)

    extra["hammer"] = (
        (lower_shadow >= real_body_abs * 2)
        & (upper_shadow <= real_body_abs * 1.2)
        & (df["pos"] >= 0.85)
    )

    extra["bottom_hammer"] = extra["hammer"] & (df["long_pos_250"] <= 0.45)
    extra["gap_up"] = df["open"] > df["high"].shift(1)
    extra["hammer_gap_confirm"] = extra["bottom_hammer"].shift(1).fillna(False) & extra["gap_up"] & (df["close"] > df["open"])

    extra["strong_yang"] = df["pct_chg"] >= 5
    extra["key_break_strong_yang"] = extra["strong_yang"] & (
        extra["platform_break_vol"]
        | (df["close"] > df["prehigh"])
        | df["just_cross_ma120"]
        | df["just_cross_ma250"]
    )

    extra["score_pattern"] = 0
    extra.loc[extra["platform_break_vol"], "score_pattern"] += 6
    extra.loc[extra["break_bottom_reversal"], "score_pattern"] += 6
    extra.loc[extra["hammer_gap_confirm"], "score_pattern"] += 7
    extra.loc[extra["gap_up"] & ~extra["hammer_gap_confirm"], "score_pattern"] += 2
    extra.loc[extra["key_break_strong_yang"], "score_pattern"] += 6
    extra["score_pattern"] = extra["score_pattern"].clip(0, 14)

    extra["score_structure_core"] = 0.0
    extra["structure_flags"] = ""
    extra["structure_neckline"] = 0.0
    extra["score_advanced_ao_kou"] = 0.0
    extra["advanced_ao_kou_desc"] = ""
    extra["advanced_left_platform_score"] = 0.0
    extra["advanced_left_platform_level"] = ""
    extra["advanced_first_volume_mid"] = 0.0
    extra["advanced_target_150"] = 0.0
    extra["advanced_target_dist"] = 0.0
    # V9：首次倍量凹口黄金扩展模型。区分A类二次突破买点与B类高位回落后回抽100%压力位。
    extra["score_fibo_reclaim"] = 0.0
    extra["fibo_reclaim_type"] = ""
    extra["fibo_reclaim_desc"] = ""
    extra["fibo_level_75"] = 0.0
    extra["fibo_level_100"] = 0.0
    extra["fibo_level_150"] = 0.0
    extra["fibo_level_200"] = 0.0

    for idx in range(len(df)):
        hist = df.iloc[:idx + 1]

        ao = detect_ao_kou_structure(hist)
        arc = detect_arc_bottom_structure(hist)
        reclaim = detect_break_bottom_reclaim_structure(hist)
        advanced = detect_advanced_ao_kou_second_volume(hist)
        fibo = detect_first_volume_fibo_reclaim_model(hist)

        structure_score = 0.0
        flags = []
        neckline_value = 0.0

        if ao["score"] > 0:
            structure_score += ao["score"]
            flags.append(ao["reason"])
            neckline_value = max(neckline_value, ao["neckline"])

        if arc["score"] > 0:
            structure_score += arc["score"]
            flags.append(arc["reason"])
            neckline_value = max(neckline_value, arc["neckline"])

        if reclaim["score"] > 0:
            structure_score += reclaim["score"]
            flags.append(reclaim["reason"])
            neckline_value = max(neckline_value, reclaim["platform_low"])

        if advanced.get("hit"):
            # 高级凹口二次倍量是独立高质量模型，单独计分，避免直接撑爆原日线结构分。
            flags.append("高级凹口二次倍量确认")
            neckline_value = max(neckline_value, safe_float(advanced.get("left_platform_top", 0.0)))
            extra.iloc[idx, extra.columns.get_loc("score_advanced_ao_kou")] = safe_float(advanced.get("score", 0.0))
            extra.iloc[idx, extra.columns.get_loc("advanced_ao_kou_desc")] = str(advanced.get("reason", ""))
            extra.iloc[idx, extra.columns.get_loc("advanced_left_platform_score")] = safe_float(advanced.get("left_platform_score", 0.0))
            extra.iloc[idx, extra.columns.get_loc("advanced_left_platform_level")] = str(advanced.get("left_platform_level", ""))
            extra.iloc[idx, extra.columns.get_loc("advanced_first_volume_mid")] = safe_float(advanced.get("first_volume_mid", 0.0))
            extra.iloc[idx, extra.columns.get_loc("advanced_target_150")] = safe_float(advanced.get("target_150", 0.0))
            extra.iloc[idx, extra.columns.get_loc("advanced_target_dist")] = safe_float(advanced.get("target_dist", 0.0))

        if fibo.get("hit"):
            extra.iloc[idx, extra.columns.get_loc("score_fibo_reclaim")] = safe_float(fibo.get("score", 0.0))
            extra.iloc[idx, extra.columns.get_loc("fibo_reclaim_type")] = str(fibo.get("type", ""))
            extra.iloc[idx, extra.columns.get_loc("fibo_reclaim_desc")] = str(fibo.get("reason", ""))
            extra.iloc[idx, extra.columns.get_loc("fibo_level_75")] = safe_float(fibo.get("level_75", 0.0))
            extra.iloc[idx, extra.columns.get_loc("fibo_level_100")] = safe_float(fibo.get("level_100", 0.0))
            extra.iloc[idx, extra.columns.get_loc("fibo_level_150")] = safe_float(fibo.get("level_150", 0.0))
            extra.iloc[idx, extra.columns.get_loc("fibo_level_200")] = safe_float(fibo.get("level_200", 0.0))
            if safe_float(fibo.get("score", 0.0)) > 0:
                flags.append("首次倍量凹口100%二次突破")
            elif safe_float(fibo.get("score", 0.0)) < 0:
                flags.append("高扩展位回落后回抽100%压力")

        extra.iloc[idx, extra.columns.get_loc("score_structure_core")] = min(structure_score, 30.0)
        extra.iloc[idx, extra.columns.get_loc("structure_flags")] = "；".join(flags)
        extra.iloc[idx, extra.columns.get_loc("structure_neckline")] = neckline_value

    # V9：关键结构突破K线质量。不是单独大模型，只在平台/凹口/颈线/箱体等关键位突破时作为子项加分。
    body_range = (df["high"] - df["low"]).replace(0, pd.NA)
    entity_ratio = ((df["close"] - df["open"]).abs() / body_range).fillna(0)
    upper_shadow_ratio = ((df["high"] - df[["open", "close"]].max(axis=1)) / body_range).fillna(1)
    lower_shadow_ratio = ((df[["open", "close"]].min(axis=1) - df["low"]) / body_range).fillna(1)
    critical_break = (extra["structure_neckline"] > 0) & (df["close"] >= extra["structure_neckline"] * 1.005)
    extra["score_break_k_quality"] = 0.0
    extra.loc[critical_break & (entity_ratio >= 0.70), "score_break_k_quality"] += 1.0
    extra.loc[critical_break & (df["pos"] >= 0.85), "score_break_k_quality"] += 1.0
    extra.loc[critical_break & (lower_shadow_ratio <= 0.10), "score_break_k_quality"] += 0.5
    extra.loc[critical_break & extra["gap_up"] & (df["low"] > df["high"].shift(1)), "score_break_k_quality"] += 1.0
    extra.loc[critical_break & extra["yang_ladder"], "score_break_k_quality"] += 1.2
    extra.loc[critical_break & extra["is_beiliang"], "score_break_k_quality"] += 2.0
    extra.loc[critical_break & extra["beiliang_flat"], "score_break_k_quality"] += 2.5
    extra.loc[(~critical_break) & (df["long_pos_250"] > 0.75), "score_break_k_quality"] = 0.0
    extra["score_break_k_quality"] = extra["score_break_k_quality"].clip(0, 4.0)

    # 普通关键位回踩承接模型：用于非涨停或结构突破后的关键位回踩；
    # 与涨停后三日承接分开定义，最后通过封顶避免重复堆分。
    key_pullback_scores = []
    key_pullback_descs = []
    for _i in range(len(df)):
        key = safe_float(extra["structure_neckline"].iloc[_i])
        if key <= 0:
            key = safe_float(df["prehigh"].iloc[_i]) if "prehigh" in df.columns else 0.0
        score = 0.0
        desc = "无普通关键位回踩承接"
        if key > 0:
            low = safe_float(df["low"].iloc[_i])
            close = safe_float(df["close"].iloc[_i])
            vol = safe_float(df["volume"].iloc[_i])
            recent_vol_peak = safe_float(df["volume"].iloc[max(0, _i-8):_i+1].max())
            near_key = (low <= key * 1.05) and (close >= key * 0.99)
            light_break_reclaim = (low >= key * 0.98) and (close >= key)
            vol_ok = recent_vol_peak > 0 and vol <= recent_vol_peak * 0.80
            turn_ok = (safe_float(df["close"].iloc[_i]) > safe_float(df["open"].iloc[_i])) and (safe_float(df["pos"].iloc[_i]) >= 0.60)
            if near_key:
                score += 1.0
            if light_break_reclaim:
                score += 1.0
            if vol_ok:
                score += 1.5
            if turn_ok:
                score += 1.5
            if score > 0:
                desc = f"关键位{key:.2f}附近回踩承接，得分{score:.1f}"
        key_pullback_scores.append(min(score, 6.0))
        key_pullback_descs.append(desc)
    extra["score_key_pullback_hold"] = key_pullback_scores
    extra["key_pullback_desc"] = key_pullback_descs

    # 承接结构总分：涨停后三日实体承接 + 普通关键位回踩承接。
    # 如果两者来自同一阶段，普通关键位回踩按60%权重，最终封顶10分，避免一根涨停重复堆分。
    extra["score_carry_structure"] = (
        extra["score_limitup_hold_3d"]
        + extra["score_key_pullback_hold"] * 0.6
    ).clip(0, 10)

    # 台阶式资金推进结构：只有近60日至少两组间隔式倍量/倍平量/强阳推进后，才评价回撤质量；
    # 普通缩量回调不加分，避免把弱势缩量误判为好事。
    step_scores = []
    step_descs = []
    for _i in range(len(df)):
        start = max(0, _i - 59)
        event_idxs = []
        last_event = -999
        for _j in range(start, _i + 1):
            event = bool(
                extra["beiliang_flat"].iloc[_j]
                or extra["beiliang_up"].iloc[_j]
                or (safe_float(df["pct_chg"].iloc[_j]) >= 5 and safe_float(df["close"].iloc[_j]) > safe_float(df["open"].iloc[_j]))
                or extra["limit_up"].iloc[_j]
            )
            if event and _j - last_event >= 3:
                event_idxs.append(_j)
                last_event = _j
        if len(event_idxs) < 2:
            step_scores.append(0.0)
            step_descs.append("台阶结构未启动：不足两组间隔式资金推进")
            continue
        group_score = min(3.0, 1.0 + (len(event_idxs) - 2) * 1.0)
        pull_quality = 0.0
        valid_pull_count = 0
        prev_pull_low = None
        low_lift_count = 0
        for _k, ev in enumerate(event_idxs[:-1]):
            next_ev = event_idxs[_k + 1]
            seg = df.iloc[ev + 1:next_ev]
            if seg.empty:
                continue
            top = max(safe_float(df["open"].iloc[ev]), safe_float(df["close"].iloc[ev]))
            bottom = min(safe_float(df["open"].iloc[ev]), safe_float(df["close"].iloc[ev]))
            mid = (top + bottom) / 2
            min_close = safe_float(seg["close"].min())
            min_low = safe_float(seg["low"].min())
            ev_vol = safe_float(df["volume"].iloc[ev])
            avg_vol = safe_float(seg["volume"].mean())
            down_body = ((seg["open"] - seg["close"]) / seg["open"].replace(0, pd.NA)).where(seg["close"] < seg["open"], 0).max()
            q = 0.0
            if min_close >= top * 0.995:
                q += 1.5
            elif min_close >= mid:
                q += 1.0
            elif min_low >= bottom * 0.995:
                q += 0.5
            if ev_vol > 0:
                retrace_vol_ratio = avg_vol / ev_vol
                if 0.40 <= retrace_vol_ratio <= 0.80:
                    q += 0.5
                elif retrace_vol_ratio > 1.00:
                    q -= 0.5
            if safe_float(down_body) < 0.04:
                q += 0.3
            if q > 0:
                valid_pull_count += 1
                pull_quality += q
            if prev_pull_low is not None and min_low > prev_pull_low:
                low_lift_count += 1
            prev_pull_low = min_low
        pull_quality = min(4.0, pull_quality)
        low_lift_score = 1.0 if low_lift_count >= 1 else 0.0
        score = min(8.0, group_score + pull_quality + low_lift_score)
        possible_pulls = max(1, len(event_idxs) - 1)
        # V9：组数多但回撤守位少，不能因为“组数多”把分数堆高。
        if valid_pull_count == 0:
            score = min(score, 3.0)
        elif valid_pull_count / possible_pulls < 0.5:
            score = min(score, 4.5)
        step_scores.append(score)
        step_descs.append(f"近60日{len(event_idxs)}组间隔式资金推进，{valid_pull_count}组回撤守位，台阶分{score:.1f}")
    extra["score_stepwise_push"] = step_scores
    extra["stepwise_desc"] = step_descs

    ema12 = df["close"].ewm(span=12, adjust=False).mean()
    ema26 = df["close"].ewm(span=26, adjust=False).mean()

    extra["macd_diff"] = ema12 - ema26
    extra["macd_dea"] = extra["macd_diff"].ewm(span=9, adjust=False).mean()
    extra["macd_cross"] = (extra["macd_diff"] > extra["macd_dea"]) & (extra["macd_diff"].shift(1) <= extra["macd_dea"].shift(1))

    extra["ma20_slope_5"] = (df["ma20"] / df["ma20"].shift(5) - 1).where(df["ma20"].shift(5) != 0, 0)

    extra["early_ma_bull"] = (
        (df["ma5"] > df["ma10"])
        & (df["ma10"] > df["ma20"])
        & (df["ma10"].shift(3) <= df["ma20"].shift(3))
    )

    extra["score_trend_stage"] = 0
    extra.loc[extra["early_ma_bull"], "score_trend_stage"] += 4
    extra.loc[(extra["ma20_slope_5"] > 0) & (extra["ma20_slope_5"] < 0.03), "score_trend_stage"] += 3
    extra.loc[extra["strong_yang"], "score_trend_stage"] += 1
    extra.loc[extra["key_break_strong_yang"], "score_trend_stage"] += 3
    extra.loc[extra["macd_cross"] & (extra["macd_diff"] < 0), "score_trend_stage"] += 5
    extra.loc[extra["macd_cross"] & (extra["macd_diff"] >= 0), "score_trend_stage"] += 4
    extra.loc[(extra["macd_diff"] > extra["macd_dea"]) & (~extra["macd_cross"]), "score_trend_stage"] += 1
    extra["score_trend_stage"] = extra["score_trend_stage"].clip(0, 12)

    delta = df["close"].diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)

    avg_gain = gain.rolling(14).mean()
    avg_loss = loss.rolling(14).mean()

    rs = avg_gain / avg_loss.replace(0, pd.NA)
    extra["rsi"] = 100 - (100 / (1 + rs))

    tp = (df["high"] + df["low"] + df["close"]) / 3
    tp_ma = tp.rolling(14).mean()
    tp_md = (tp - tp_ma).abs().rolling(14).mean()
    extra["cci"] = (tp - tp_ma) / (0.015 * tp_md.replace(0, pd.NA))

    # 指标状态细分：数值必须量化为好/坏/过热，而不是只在报告中罗列。
    extra["score_indicator"] = 0.0
    # RSI
    extra.loc[extra["rsi"] < 45, "score_indicator"] -= 2
    extra.loc[(extra["rsi"] >= 45) & (extra["rsi"] < 60), "score_indicator"] += 1
    extra.loc[(extra["rsi"] >= 60) & (extra["rsi"] < 70), "score_indicator"] += 2
    extra.loc[(extra["rsi"] >= 70) & (extra["rsi"] < 80), "score_indicator"] += 1
    extra.loc[(extra["rsi"] >= 80) & (extra["rsi"] < 85), "score_indicator"] -= 1
    extra.loc[extra["rsi"] >= 85, "score_indicator"] -= 3
    # CCI
    extra.loc[extra["cci"] < 0, "score_indicator"] -= 1
    extra.loc[(extra["cci"] >= 0) & (extra["cci"] < 100), "score_indicator"] += 1
    extra.loc[(extra["cci"] >= 100) & (extra["cci"] < 180), "score_indicator"] += 2
    extra.loc[(extra["cci"] >= 180) & (extra["cci"] < 220), "score_indicator"] += 1
    extra.loc[(extra["cci"] >= 220) & (extra["cci"] < 300), "score_indicator"] -= 1
    extra.loc[extra["cci"] >= 300, "score_indicator"] -= 3
    # MA20斜率
    extra.loc[extra["ma20_slope_5"] < -0.01, "score_indicator"] -= 2
    extra.loc[(extra["ma20_slope_5"] >= 0) & (extra["ma20_slope_5"] < 0.03), "score_indicator"] += 2
    extra.loc[(extra["ma20_slope_5"] >= 0.03) & (extra["ma20_slope_5"] < 0.05), "score_indicator"] += 1
    extra.loc[extra["ma20_slope_5"] >= 0.05, "score_indicator"] -= 1
    # 组合过热惩罚
    extra.loc[(extra["rsi"] > 80) & (extra["cci"] > 250), "score_indicator"] -= 2
    extra.loc[(extra["rsi"] > 85) & (extra["cci"] > 300), "score_indicator"] -= 2
    extra.loc[(df["bias20"] > 0.15) & (extra["rsi"] > 80), "score_indicator"] -= 2
    extra.loc[(df["bias20"] > 0.20) & (df["bias60"] > 0.20), "score_indicator"] -= 3
    extra["score_indicator"] = extra["score_indicator"].clip(-6, 6)

    extra["gap_up_count_20"] = extra["gap_up"].rolling(20).sum()
    # 有效向上跳空：过去发生的跳空，之后3日内没有完全回补，才算有效承接。
    gap_effective = []
    gap_vals = list(extra["gap_up"].fillna(False).astype(bool).values)
    highs_prev = list(df["high"].shift(1).fillna(0).values)
    lows = list(df["low"].fillna(0).values)
    for _i in range(len(df)):
        ok = False
        if gap_vals[_i] and _i + 3 < len(df):
            gap_floor = highs_prev[_i]
            ok = min(lows[_i + 1:_i + 4]) >= gap_floor * 0.995
        gap_effective.append(ok)
    extra["effective_gap_up"] = gap_effective
    extra["effective_gap_up_count_50"] = extra["effective_gap_up"].rolling(50).sum()

    extra["score_count"] = 0
    extra.loc[extra["beiliang_count_20"] >= 2, "score_count"] += 2
    extra.loc[extra["beiliang_count_20"] >= 3, "score_count"] += 4
    extra.loc[extra["beiliang_count_20"] >= 5, "score_count"] += 6
    extra.loc[extra["bull_engulf_count_20"] >= 2, "score_count"] += 2
    extra.loc[extra["bull_engulf_count_20"] >= 4, "score_count"] += 4
    extra.loc[extra["bull_engulf_count_50"] >= 3, "score_count"] += 2
    extra.loc[extra["bull_engulf_count_50"] >= 5, "score_count"] += 3
    extra.loc[extra["gap_up_count_20"] >= 2, "score_count"] += 2
    extra.loc[extra["gap_up_count_20"] >= 4, "score_count"] += 3
    extra.loc[extra["effective_gap_up_count_50"] >= 2, "score_count"] += 2
    extra.loc[extra["effective_gap_up_count_50"] >= 4, "score_count"] += 2
    extra.loc[extra["up_days_20"] >= 12, "score_count"] += 2
    extra.loc[extra["up_days_20"] >= 14, "score_count"] += 4
    extra.loc[extra["up_down_vol_ratio_20"] >= 1.2, "score_count"] += 3
    extra["score_count"] += extra["score_bull_engulf_50"]
    extra["score_count"] = extra["score_count"].clip(0, 10)
    # V9：频次分拆权重。倍量后平量为0时，不能仅靠阳包阴/跳空/普通倍量把频次拉满。
    extra.loc[extra["flat_volume_count_60"] <= 0, "score_count"] = extra.loc[extra["flat_volume_count_60"] <= 0, "score_count"].clip(upper=7.0)
    extra.loc[(extra["flat_volume_count_60"] <= 0) & (extra["bull_engulf_quality_count_50"] <= 0) & (extra["scattered_beiliang_count_60"] <= 1), "score_count"] = extra.loc[(extra["flat_volume_count_60"] <= 0) & (extra["bull_engulf_quality_count_50"] <= 0) & (extra["scattered_beiliang_count_60"] <= 1), "score_count"].clip(upper=5.5)

    # V9：50个交易日内无涨停，作为A股攻击性/辨识度不足的轻度扣分，不作为硬淘汰。
    extra["limit_up_count_50"] = extra["limit_up"].rolling(50).sum()
    extra["score_limitup_activity"] = 0.0
    extra.loc[extra["limit_up_count_50"] <= 0, "score_limitup_activity"] -= 1.5
    extra.loc[extra["limit_up_count_50"] >= 2, "score_limitup_activity"] += 0.8
    extra["score_limitup_activity"] = extra["score_limitup_activity"].clip(-2, 1)

    # 位置/压力空间正向评分：近端压力远是大优点，近端压力太近是风险收益比问题。
    extra["score_pressure_space"] = 0.0
    if "near_pressure_dist" in df.columns:
        extra.loc[df["near_pressure_dist"] > 0.20, "score_pressure_space"] += 4
        extra.loc[(df["near_pressure_dist"] > 0.12) & (df["near_pressure_dist"] <= 0.20), "score_pressure_space"] += 3
        extra.loc[(df["near_pressure_dist"] > 0.08) & (df["near_pressure_dist"] <= 0.12), "score_pressure_space"] += 1.5
        extra.loc[(df["near_pressure_dist"] > 0) & (df["near_pressure_dist"] < 0.05), "score_pressure_space"] -= 2
    if "mid_pressure_dist" in df.columns:
        extra.loc[df["mid_pressure_dist"] > 0.25, "score_pressure_space"] += 2
    extra.loc[df["overhead_pressure_dist"] > 0.40, "score_pressure_space"] += 1
    extra["score_pressure_space"] = extra["score_pressure_space"].clip(-4, 8)

    # 关键位距离/买点舒适度：不直接替三号员工下单，但用于区分刚突破和已经远离关键位。
    key_level = extra["structure_neckline"].where(extra["structure_neckline"] > 0, df["prehigh"])
    extra["distance_to_key"] = (df["close"] / key_level.replace(0, pd.NA) - 1).fillna(0)
    extra["score_key_distance"] = 0.0
    extra.loc[(extra["distance_to_key"] >= 0.005) & (extra["distance_to_key"] <= 0.03), "score_key_distance"] += 4
    extra.loc[(extra["distance_to_key"] > 0.03) & (extra["distance_to_key"] <= 0.05), "score_key_distance"] += 2
    extra.loc[(extra["distance_to_key"] > 0.05) & (extra["distance_to_key"] <= 0.08), "score_key_distance"] += 0.5
    extra.loc[extra["distance_to_key"] > 0.10, "score_key_distance"] -= 3
    extra.loc[df["bias20"] < 0.10, "score_key_distance"] += 1
    extra.loc[df["bias20"] > 0.15, "score_key_distance"] -= 2
    extra["score_key_distance"] = extra["score_key_distance"].clip(-5, 6)

    extra["high_overheat"] = df["long_high_zone"] & extra["extreme_vol"]

    extra["climax_k"] = (
        (df["pct_chg"] >= 10)
        & (df["volr"] >= 3.5)
        & (df["pos"] >= 0.9)
        & (df["long_pos_250"] >= 0.7)
    )

    extra["score_penalty"] = 0
    extra.loc[extra["high_overheat"], "score_penalty"] -= 5
    extra.loc[df["bias20"] > 0.25, "score_penalty"] -= 3
    extra.loc[df["bias60"] > 0.50, "score_penalty"] -= 3
    extra.loc[df["long_pos_250"] > 0.75, "score_penalty"] -= 3
    extra.loc[df["long_pos_250"] > 0.85, "score_penalty"] -= 5
    extra.loc[extra["extreme_vol"], "score_penalty"] -= 4
    # 连续倍倍量本身不扣；只有后续第4天/后续一日相对倍倍量高峰缩量超过30%，才扣短线承接风险。
    extra.loc[extra["beibeiliang_short_pullback_risk"], "score_penalty"] -= 3
    extra.loc[(extra["limit_up"]) & (extra["extreme_vol"]) & (df["long_pos_250"] > 0.55), "score_penalty"] -= 4
    if "near_pressure_dist" in df.columns:
        extra.loc[(df["near_pressure_dist"] > 0) & (df["near_pressure_dist"] < 0.08), "score_penalty"] -= 3
        extra.loc[(df["near_pressure_dist"] > 0) & (df["near_pressure_dist"] < 0.05), "score_penalty"] -= 3
    extra.loc[(df["overhead_pressure_dist"] > 0) & (df["overhead_pressure_dist"] < 0.08), "score_penalty"] -= 3
    extra.loc[(df["overhead_pressure_dist"] > 0) & (df["overhead_pressure_dist"] < 0.05), "score_penalty"] -= 3
    extra.loc[(extra["rsi"] > 80), "score_penalty"] -= 3
    extra.loc[(extra["rsi"] > 85), "score_penalty"] -= 3
    extra.loc[(extra["cci"] > 250), "score_penalty"] -= 3
    extra.loc[(extra["cci"] > 400), "score_penalty"] -= 3
    extra.loc[extra["climax_k"] & (~extra["key_break_strong_yang"]), "score_penalty"] -= 5
    # 长期弱势反抽：月/周级别无法完全取数时，用日线MA120/MA250空头和低位突然爆量近似过滤。
    extra["weak_rebound_risk"] = (df["close"] < df["ma120"]) & (df["ma120"] < df["ma250"]) & (df["ma120"] < df["ma120"].shift(20)) & extra["extreme_vol"]
    extra.loc[extra["weak_rebound_risk"], "score_penalty"] -= 8
    extra["score_penalty"] = extra["score_penalty"].clip(-22, 0)

    extra["score_long_cycle"] = 0
    extra.loc[df["long_bottom_zone"], "score_long_cycle"] += 4
    extra.loc[(df["long_pos_250"] > 0.35) & (df["long_pos_250"] <= 0.55), "score_long_cycle"] += 2
    extra.loc[df["just_cross_ma120"], "score_long_cycle"] += 3
    extra.loc[df["just_cross_ma250"], "score_long_cycle"] += 4
    extra.loc[df["long_trend_repair"], "score_long_cycle"] += 2
    # 长期高位不再简单给高分，先交给阶段/风险模块判断。
    extra["score_long_cycle"] = extra["score_long_cycle"].clip(0, 12)

    extra["score_monthly_cycle"] = float(monthly_ctx.get("score", 0.0))
    extra["monthly_flags"] = str(monthly_ctx.get("flag", ""))
    extra["monthly_support_months"] = int(monthly_ctx.get("support_months", 0))
    extra["monthly_volume_score"] = float(monthly_ctx.get("volume_score", 0.0))
    extra["monthly_detail"] = str(monthly_ctx.get("detail", ""))

    # V11.1：月线高度/大周期空间风险。月线修复本身加分，但如果已经远离中轨、年内偏高、压力贴脸，要降级。
    extra["score_monthly_height_space"] = 0.0
    extra.loc[df["long_pos_250"] <= 0.35, "score_monthly_height_space"] += 8
    extra.loc[(df["long_pos_250"] > 0.35) & (df["long_pos_250"] <= 0.55), "score_monthly_height_space"] += 5
    extra.loc[(df["long_pos_250"] > 0.55) & (df["long_pos_250"] <= 0.70), "score_monthly_height_space"] += 2
    extra.loc[df["long_pos_250"] > 0.75, "score_monthly_height_space"] -= 5
    extra.loc[df["long_pos_250"] > 0.85, "score_monthly_height_space"] -= 7
    extra.loc[(df["bias20"] <= 0.10) & (df["bias60"] <= 0.12), "score_monthly_height_space"] += 3
    extra.loc[(df["bias20"] > 0.18) | (df["bias60"] > 0.20), "score_monthly_height_space"] -= 5
    extra.loc[(df["near_pressure_dist"] > 0) & (df["near_pressure_dist"] < 0.08), "score_monthly_height_space"] -= 4
    extra.loc[(extra["score_monthly_cycle"] >= 8) & (df["long_pos_250"] <= 0.60) & (df["bias20"] <= 0.12), "score_monthly_height_space"] += 3
    extra["score_monthly_height_space"] = extra["score_monthly_height_space"].clip(-12, 15)

    # V11.1：买点质量/风险收益比。必须区分“结构关键位”和“真实交易防守位”。
    defense_candidates = pd.concat([
        df["prehigh"].fillna(0),
        df.get("ma20", pd.Series(0, index=df.index)).fillna(0),
        df.get("ma60", pd.Series(0, index=df.index)).fillna(0),
        df.get("ma120", pd.Series(0, index=df.index)).fillna(0),
    ], axis=1)
    extra["structure_key_level"] = defense_candidates.max(axis=1)
    extra["defense_source"] = "均线/前高粗防守"
    # 结构颈线/首次倍量100%位是结构关键位；真实交易防守位必须给缓冲。
    mask_struct = extra["structure_neckline"] > 0
    extra.loc[mask_struct, "structure_key_level"] = extra.loc[mask_struct, "structure_neckline"]
    extra.loc[mask_struct, "defense_source"] = "结构颈线/平台边界"
    mask_fibo = extra["fibo_level_100"] > 0
    extra.loc[mask_fibo, "structure_key_level"] = extra.loc[mask_fibo, "fibo_level_100"]
    extra.loc[mask_fibo, "defense_source"] = "首次倍量100%位"

    extra["defense_buffer_pct"] = 0.015
    extra.loc[mask_fibo, "defense_buffer_pct"] = 0.020
    extra.loc[mask_struct, "defense_buffer_pct"] = 0.018
    extra.loc[extra["score_advanced_ao_kou"] >= 8, "defense_buffer_pct"] = 0.020
    extra.loc[extra["limit_up"], "defense_buffer_pct"] = 0.018

    extra["real_defense_level"] = extra["structure_key_level"] * (1 - extra["defense_buffer_pct"])
    # 兼容原字段：defense_level现在代表真实交易防守位；structure_key_level单独输出。
    extra["defense_level"] = extra["real_defense_level"]
    extra["defense_dist"] = (df["close"] / extra["real_defense_level"].replace(0, pd.NA) - 1).fillna(0)
    extra.loc[extra["defense_dist"] < 0, "defense_dist"] = 0
    extra["target_dist"] = df["near_pressure_dist"].where(df["near_pressure_dist"] > 0, df["mid_pressure_dist"]).fillna(0)
    extra.loc[extra["target_dist"] <= 0, "target_dist"] = df["overhead_pressure_dist"]
    # 黄金扩展150%如果还在上方，也可作为第一目标；若比近端压力更近，则取更保守目标。
    fibo_target_dist = (extra["fibo_level_150"] / df["close"].replace(0, pd.NA) - 1).fillna(0)
    extra.loc[(fibo_target_dist > 0) & ((extra["target_dist"] <= 0) | (fibo_target_dist < extra["target_dist"])), "target_dist"] = fibo_target_dist
    extra["risk_reward_ratio"] = (extra["target_dist"] / extra["defense_dist"].replace(0, pd.NA)).fillna(0)

    extra["score_trade_quality"] = 0.0
    extra.loc[(extra["defense_dist"] >= 0.005) & (extra["defense_dist"] <= 0.05), "score_trade_quality"] += 7
    extra.loc[(extra["defense_dist"] > 0.05) & (extra["defense_dist"] <= 0.08), "score_trade_quality"] += 4
    extra.loc[extra["defense_dist"] > 0.10, "score_trade_quality"] -= 6
    extra.loc[extra["target_dist"] >= 0.15, "score_trade_quality"] += 5
    extra.loc[(extra["target_dist"] >= 0.08) & (extra["target_dist"] < 0.15), "score_trade_quality"] += 2
    extra.loc[(extra["target_dist"] > 0) & (extra["target_dist"] < 0.06), "score_trade_quality"] -= 6
    extra.loc[extra["risk_reward_ratio"] >= 2.0, "score_trade_quality"] += 6
    extra.loc[(extra["risk_reward_ratio"] >= 1.5) & (extra["risk_reward_ratio"] < 2.0), "score_trade_quality"] += 3
    extra.loc[(extra["risk_reward_ratio"] > 0) & (extra["risk_reward_ratio"] < 1.2), "score_trade_quality"] -= 9
    extra.loc[(extra["score_structure_core"] >= 8) | (extra["score_fibo_reclaim"] >= 7) | (extra["score_advanced_ao_kou"] >= 8), "score_trade_quality"] += 2
    extra["score_trade_quality"] = extra["score_trade_quality"].clip(-15, 20)

    attack_signal_count = (
        extra["strong_yang"].astype(int)
        + extra["key_break_strong_yang"].astype(int)
        + extra["platform_break_vol"].astype(int)
        + (df["pos"] >= 0.85).astype(int)
        + (extra["score_structure_core"] > 0).astype(int)
    )

    extra["score_overlap_adjustment"] = 0
    extra.loc[attack_signal_count >= 3, "score_overlap_adjustment"] -= 2
    extra.loc[attack_signal_count >= 4, "score_overlap_adjustment"] -= 4
    extra.loc[attack_signal_count >= 5, "score_overlap_adjustment"] -= 7
    # 如果强势发生在明确结构突破/月线修复共振上，重复降权减半；无结构的大阳线要重降权。
    has_real_structure = (extra["score_structure_core"] >= 8) | (extra["score_monthly_cycle"] >= 8) | (extra["score_advanced_ao_kou"] >= 8) | (extra["score_fibo_reclaim"] >= 6)
    extra.loc[has_real_structure, "score_overlap_adjustment"] = extra.loc[has_real_structure, "score_overlap_adjustment"] * 0.5
    extra.loc[(~has_real_structure) & (extra["limit_up"] | extra["strong_yang"]), "score_overlap_adjustment"] -= 3
    extra["score_overlap_adjustment"] = extra["score_overlap_adjustment"].clip(-12, 0)

    # 交易阶段分类：同样高分，不同阶段的含义完全不同。
    extra["trade_stage"] = "中位突破"
    extra.loc[extra["weak_rebound_risk"], "trade_stage"] = "弱势反抽"
    extra.loc[(df["long_pos_250"] <= 0.45) & (extra["score_structure_core"] >= 12), "trade_stage"] = "底部一买"
    extra.loc[(df["long_pos_250"] <= 0.60) & (extra["beiliang_flat"] | extra["hold_limit_mid"] | extra["hold_limit_top"]), "trade_stage"] = "回踩二买"
    extra.loc[(df["long_pos_250"] > 0.60) & (df["long_pos_250"] <= 0.80) & (extra["score_structure_core"] >= 8), "trade_stage"] = "主升延续"
    extra.loc[(df["long_pos_250"] > 0.80) & (extra["strong_yang"] | extra["limit_up"]), "trade_stage"] = "高位加速"
    extra.loc[(df["long_pos_250"] > 0.85) & extra["extreme_vol"], "trade_stage"] = "高位分歧风险"

    # V9.1：涨停/大阳强攻票单独打标签，避免把“当天很强”误标成舒服买点。
    strong_attack_today = extra["limit_up"] | (df["pct_chg"] >= 7) | (df["entity_pct"] >= 7)
    attack_overheat_today = strong_attack_today & (
        extra["extreme_vol"]
        | (df["bias20"] > 0.12)
        | (extra["rsi"] > 75)
        | (extra["cci"] > 220)
        | ((df["near_pressure_dist"] > 0) & (df["near_pressure_dist"] < 0.05))
    )
    extra.loc[attack_overheat_today & (df["long_pos_250"] <= 0.55), "trade_stage"] = "低位强启动/次日看承接"
    extra.loc[attack_overheat_today & (df["long_pos_250"] > 0.55), "trade_stage"] = "短线强攻/过热冲关/次日看承接"

    extra["score_stage_adjustment"] = 0.0
    bottom_warm = (extra["trade_stage"] == "底部一买") & (df["pct_chg"] <= 6) & (df["bias20"] < 0.10) & (df["break_rate"] <= 0.05)
    bottom_strong = (extra["trade_stage"] == "底部一买") & (~bottom_warm)
    extra.loc[bottom_warm, "score_stage_adjustment"] += 6
    # 贝达药业/津投城开这类低位强启动可以保留，但不再统一满额+6。
    extra.loc[bottom_strong, "score_stage_adjustment"] += 3.5
    extra.loc[(bottom_strong) & (df["pct_chg"] > 7) & (df["bias20"] > 0.12), "score_stage_adjustment"] -= 1.0

    # 回踩二买必须是真正“突破后回踩关键位不破再转强”，否则不满额。
    true_second_buy = (extra["trade_stage"] == "回踩二买") & (extra["beiliang_flat"] | extra["hold_limit_mid"] | extra["hold_limit_top"]) & (df["bias20"] < 0.16)
    extra.loc[true_second_buy, "score_stage_adjustment"] += 4
    extra.loc[(extra["trade_stage"] == "回踩二买") & (~true_second_buy), "score_stage_adjustment"] += 1

    extra.loc[extra["trade_stage"] == "主升延续", "score_stage_adjustment"] += 1
    extra.loc[extra["trade_stage"] == "高位加速", "score_stage_adjustment"] -= 6
    extra.loc[extra["trade_stage"] == "高位分歧风险", "score_stage_adjustment"] -= 12
    extra.loc[extra["trade_stage"] == "弱势反抽", "score_stage_adjustment"] -= 15
    extra.loc[extra["trade_stage"] == "低位强启动/次日看承接", "score_stage_adjustment"] += 1.5
    extra.loc[extra["trade_stage"] == "短线强攻/过热冲关/次日看承接", "score_stage_adjustment"] -= 8
    # 月线大周期修复共振可以缓和高位加速的降权，但不能完全抵消风险。
    extra.loc[(extra["score_monthly_cycle"] >= 10) & (extra["trade_stage"] == "高位加速"), "score_stage_adjustment"] += 3
    # V9：突破率<=0或当日非标准倍量时，不能把低位修复票直接满额当“底部一买”。
    weak_bottom_confirm = (extra["trade_stage"] == "底部一买") & ((df["break_rate"] <= 0) | (~extra["is_beiliang"]))
    extra.loc[weak_bottom_confirm, "score_stage_adjustment"] = extra.loc[weak_bottom_confirm, "score_stage_adjustment"].clip(upper=3.0)

    extra["total_score"] = (
        extra["score_base_model"]
        + extra["score_volume_structure"]
        + extra["score_behavior"]
        + extra["score_pattern"]
        + extra["score_structure_core"]
        + extra["score_advanced_ao_kou"]
        + extra["score_fibo_reclaim"]
        + extra["score_break_k_quality"]
        + extra["score_trend_stage"]
        + extra["score_count"]
        + extra["score_limitup_activity"]
        + extra["score_carry_structure"]
        + extra["score_stepwise_push"]
        + extra["score_yang_yin_volume"]
        + extra["score_pressure_space"]
        + extra["score_key_distance"]
        + extra["score_indicator"]
        + extra["score_long_cycle"]
        + extra["score_monthly_cycle"]
        + extra["score_stage_adjustment"]
        + extra["score_penalty"]
        + extra["score_overlap_adjustment"]
    )

    # V11：交易质量加权调整。保留原有所有因子，但用大周期空间、买点质量、风险收益比重新排序。
    # 量能/强阳若没有结构和买点质量支撑，不再允许单独把总分推到前排。
    structure_strength_v11 = (
        extra["score_structure_core"]
        + extra["score_advanced_ao_kou"]
        + extra["score_fibo_reclaim"]
        + extra["score_monthly_cycle"] * 0.8
        + extra["score_carry_structure"] * 0.6
    )
    extra["trade_priority_score"] = (
        structure_strength_v11 * 0.35
        + extra["score_trade_quality"] * 1.20
        + extra["score_monthly_height_space"] * 1.10
        + extra["score_volume_structure"] * 0.45
        + extra["score_yang_yin_volume"] * 0.50
        + extra["score_indicator"] * 0.40
        + extra["score_penalty"] * 0.80
    ).clip(-30, 40)
    extra["total_score"] = extra["total_score"] + extra["score_trade_quality"] + extra["score_monthly_height_space"] + extra["trade_priority_score"] * 0.35

    # V11.1：交易优先级不能轻易打满。高位回抽100%、月线高位、真实防守过远必须封顶。
    fibo_pullback_pressure_v111 = extra["fibo_reclaim_type"].astype(str).str.contains("高扩展位回落", na=False)
    high_monthly_retrace_v111 = (df["long_pos_250"] > 0.70) & ((df["bias20"] > 0.10) | ((df["near_pressure_dist"] > 0) & (df["near_pressure_dist"] < 0.12)))
    far_real_defense_v111 = extra["defense_dist"] > 0.08
    extra.loc[fibo_pullback_pressure_v111, "trade_priority_score"] = extra.loc[fibo_pullback_pressure_v111, "trade_priority_score"].clip(upper=18.0)
    extra.loc[high_monthly_retrace_v111, "trade_priority_score"] = extra.loc[high_monthly_retrace_v111, "trade_priority_score"].clip(upper=24.0)
    extra.loc[far_real_defense_v111, "trade_priority_score"] = extra.loc[far_real_defense_v111, "trade_priority_score"].clip(upper=26.0)
    extra.loc[fibo_pullback_pressure_v111 | high_monthly_retrace_v111, "total_score"] = extra.loc[fibo_pullback_pressure_v111 | high_monthly_retrace_v111, "total_score"].clip(upper=82.0)

    poor_rr = (extra["risk_reward_ratio"] > 0) & (extra["risk_reward_ratio"] < 1.2)
    poor_trade_quality = (extra["score_trade_quality"] < 0) | poor_rr
    monthly_high_risk = (df["long_pos_250"] > 0.75) & ((df["bias20"] > 0.15) | ((df["near_pressure_dist"] > 0) & (df["near_pressure_dist"] < 0.08)))
    volume_without_structure = (extra["score_volume_structure"] >= 10) & (extra["score_structure_core"] < 6) & (extra["score_fibo_reclaim"] < 6) & (extra["score_advanced_ao_kou"] < 7) & (extra["score_monthly_cycle"] < 8)
    extra.loc[poor_trade_quality & volume_without_structure, "total_score"] = extra.loc[poor_trade_quality & volume_without_structure, "total_score"].clip(upper=76.0)
    extra.loc[monthly_high_risk & (extra["score_trade_quality"] < 6), "total_score"] = extra.loc[monthly_high_risk & (extra["score_trade_quality"] < 6), "total_score"].clip(upper=79.0)
    extra.loc[poor_rr, "total_score"] = extra.loc[poor_rr, "total_score"].clip(upper=78.0)
    extra.loc[(extra["score_trade_quality"] >= 12) & (extra["score_monthly_height_space"] >= 5) & (structure_strength_v11 >= 12), "total_score"] += 4.0

    # V9：高扩展位回落后回抽100%压力位，不能当作二次突破买点高分。
    fibo_pullback_pressure = extra["fibo_reclaim_type"].astype(str).str.contains("高扩展位回落", na=False)
    extra.loc[fibo_pullback_pressure, "total_score"] = extra.loc[fibo_pullback_pressure, "total_score"].clip(upper=78.0)
    # V9：低位破底翻修复但突破率<=0、非标准倍量、月线无确认、倍量后平量为0时，降为观察，不进80强候选。
    low_repair_edge = (df["break_rate"] <= 0) & (~extra["is_beiliang"]) & (extra["score_monthly_cycle"] <= 0) & (extra["flat_volume_count_60"] <= 0) & (extra["score_structure_core"] > 0)
    extra.loc[low_repair_edge, "total_score"] = extra.loc[low_repair_edge, "total_score"].clip(upper=78.0)

    # 无日线结构、无月线大周期修复共振的涨停/大阳线，不允许仅凭单日强度冲到前排。
    no_structure_attack = (extra["score_structure_core"] <= 0) & (extra["score_advanced_ao_kou"] < 7) & (extra["score_fibo_reclaim"] < 6) & (extra["score_monthly_cycle"] < 6) & (extra["limit_up"] | extra["strong_yang"])
    extra.loc[no_structure_attack, "total_score"] = extra.loc[no_structure_attack, "total_score"].clip(upper=79.0)
    no_core_structure = (extra["score_structure_core"] <= 0) & (extra["score_advanced_ao_kou"] < 7) & (extra["score_fibo_reclaim"] < 6) & (extra["score_monthly_cycle"] < 4)
    extra.loc[no_core_structure, "total_score"] = extra.loc[no_core_structure, "total_score"].clip(upper=78.0)
    # 20cm强攻但无核心结构，不进入80分优先池。
    if str(code).startswith(("300", "301", "688")):
        extra.loc[no_structure_attack, "total_score"] = extra.loc[no_structure_attack, "total_score"].clip(upper=76.0)

    merged = pd.concat([df, extra], axis=1)
    merged = apply_chase_risk_gate(merged)
    # V11：优先候选池必须同时满足交易质量与风险收益比，不再只看综合分。
    if "candidate_pool" in merged.columns:
        poor_v11 = (merged.get("score_trade_quality", 0) < 0) | ((merged.get("risk_reward_ratio", 0) > 0) & (merged.get("risk_reward_ratio", 0) < 1.2)) | (merged.get("score_monthly_height_space", 0) < -5)
        merged.loc[poor_v11 & (merged["candidate_pool"].astype(str).eq("优先候选池")), "candidate_pool"] = "交易质量观察池"
        merged.loc[poor_v11, "candidate_pool_reason"] = (merged.loc[poor_v11, "candidate_pool_reason"].astype(str) + "；V11交易质量/风险收益比不足").str.strip("；")
    return merged.tail(CHECK_DAYS)


def calc_base_full(df):
    df = df.copy()

    base = pd.DataFrame(index=df.index)

    base["vol_ma"] = df["volume"].rolling(N).mean()
    base["volr"] = df["volume"] / base["vol_ma"]

    base["upbody"] = (df["close"] - df["open"]).where(df["close"] > df["open"], 0)
    base["upcount"] = (df["close"] > df["open"]).rolling(N).sum()
    base["upbody_sum"] = base["upbody"].rolling(N).sum()
    base["upbody_ma"] = base["upbody_sum"] / base["upcount"].replace(0, pd.NA)

    base["body"] = df["close"] - df["open"]
    base["body_ratio"] = base["body"] / base["upbody_ma"]

    rng = df["high"] - df["low"]
    base["pos"] = ((df["close"] - df["low"]) / rng).where(rng != 0, 0)

    base["prehigh"] = df["high"].rolling(N).max().shift(1)

    base["ma5"] = df["close"].rolling(5).mean()
    base["ma10"] = df["close"].rolling(10).mean()
    base["ma20"] = df["close"].rolling(20).mean()
    base["ma60"] = df["close"].rolling(60).mean()
    base["ma120"] = df["close"].rolling(120).mean()
    base["ma250"] = df["close"].rolling(250).mean()

    base["uptrend"] = (base["ma5"] > base["ma10"]) & (base["ma10"] > base["ma20"])

    base["volscore"] = 0
    base.loc[base["volr"] >= 1.2, "volscore"] = 10
    base.loc[base["volr"] >= 1.5, "volscore"] = 20
    base.loc[base["volr"] >= 2.0, "volscore"] = 25
    base.loc[base["volr"] >= 2.5, "volscore"] = 30

    up = df["close"] > df["open"]

    base["bodyscore"] = 0
    base.loc[up & (base["body"] >= base["upbody_ma"]), "bodyscore"] = 10
    base.loc[up & (base["body"] >= base["upbody_ma"] * 1.2), "bodyscore"] = 15
    base.loc[up & (base["body"] >= base["upbody_ma"] * 1.5), "bodyscore"] = 20

    base["posscore"] = 0
    base.loc[base["pos"] >= 0.6, "posscore"] = 10
    base.loc[base["pos"] >= 0.7, "posscore"] = 15
    base.loc[base["pos"] >= 0.8, "posscore"] = 20

    base["brscore"] = 0
    base.loc[df["high"] >= base["prehigh"], "brscore"] = 5
    base.loc[df["close"] > base["prehigh"], "brscore"] = 15
    base.loc[df["close"] > base["prehigh"] * 1.01, "brscore"] = 20

    base["structscore"] = 0
    base.loc[(base["volr"] >= 2.5) & (base["pos"] >= 0.8), "structscore"] = 2
    base.loc[base["uptrend"], "structscore"] = 5
    base.loc[df["close"] > base["prehigh"], "structscore"] = 8
    base.loc[(df["close"] > base["prehigh"]) & (base["volr"] >= 2), "structscore"] = 10

    base["score"] = (
        base["volscore"]
        + base["bodyscore"]
        + base["posscore"]
        + base["brscore"]
        + base["structscore"]
    )

    base["vr1"] = df["volume"] / df["volume"].shift(1)
    base["xg0"] = (base["score"] >= SCORE_LIMIT) & (base["vr1"] >= VR1_MIN) & (base["vr1"] <= VR1_MAX)
    base["xg"] = base["xg0"]

    base["preclose"] = df["close"].shift(1)
    base["entity_pct"] = ((df["close"] - df["open"]) / base["preclose"] * 100).where(base["preclose"] != 0, 0)
    base["break_rate"] = (df["close"] / base["prehigh"] - 1).where(base["prehigh"] != 0, 0)

    base["bias20"] = (df["close"] / base["ma20"] - 1).where(base["ma20"] != 0, 0)
    base["bias60"] = (df["close"] / base["ma60"] - 1).where(base["ma60"] != 0, 0)

    base["high_250"] = df["high"].rolling(250).max()
    base["low_250"] = df["low"].rolling(250).min()
    base["long_pos_250"] = ((df["close"] - base["low_250"]) / (base["high_250"] - base["low_250"])).where((base["high_250"] - base["low_250"]) != 0, 0)

    # 压力距离分层：近端/中层/远端。原250日压力保留，新增加60/120日压力用于风险收益比判断。
    base["overhead_high_60"] = df["high"].shift(1).rolling(60).max()
    base["overhead_high_120"] = df["high"].shift(1).rolling(120).max()
    base["overhead_high_250"] = df["high"].shift(1).rolling(250).max()
    base["near_pressure_dist"] = (base["overhead_high_60"] / df["close"] - 1).where(df["close"] != 0, 0)
    base["mid_pressure_dist"] = (base["overhead_high_120"] / df["close"] - 1).where(df["close"] != 0, 0)
    base["overhead_pressure_dist"] = (base["overhead_high_250"] / df["close"] - 1).where(df["close"] != 0, 0)
    for _c in ["near_pressure_dist", "mid_pressure_dist", "overhead_pressure_dist"]:
        base.loc[base[_c] < 0, _c] = 0

    base["just_cross_ma120"] = (df["close"] > base["ma120"]) & (df["close"].shift(1) <= base["ma120"].shift(1))
    base["just_cross_ma250"] = (df["close"] > base["ma250"]) & (df["close"].shift(1) <= base["ma250"].shift(1))
    base["long_bottom_zone"] = base["long_pos_250"] <= 0.35
    base["long_high_zone"] = base["long_pos_250"] >= 0.85
    base["long_trend_repair"] = (base["ma120"] > base["ma120"].shift(10)) & (df["close"] > base["ma120"])

    merged = pd.concat([df, base], axis=1)
    return merged


def process_stock_base(row):
    code = row["代码"]
    name = row["名称"]
    bs_code = row["bs_code"]

    df = get_daily_kline(bs_code)
    recent = calc_base_rows(df)

    rows = []

    if recent.empty:
        return rows

    for _, r in recent.iterrows():
        rows.append({
            "code": code,
            "name": name,
            "bs_code": bs_code,
            "date": r["date"],
            "close": float(r["close"]),
            "pct_chg": float(r["pct_chg"]),
            "amount": float(r["amount"]) if pd.notna(r["amount"]) else 0,
            "score": float(r["score"]) if pd.notna(r["score"]) else 0,
            "base_score": float(r["base_score"]) if pd.notna(r["base_score"]) else 0,
            "base_total_score": float(r.get("base_total_score", 0)) if pd.notna(r.get("base_total_score", 0)) else 0,
            "base_attack_quality_score": float(r.get("base_attack_quality_score", 0)) if pd.notna(r.get("base_attack_quality_score", 0)) else 0,
            "base_position_reward_score": float(r.get("base_position_reward_score", 0)) if pd.notna(r.get("base_position_reward_score", 0)) else 0,
            "base_volume_carry_score": float(r.get("base_volume_carry_score", 0)) if pd.notna(r.get("base_volume_carry_score", 0)) else 0,
            "base_structure_potential_score": float(r.get("base_structure_potential_score", 0)) if pd.notna(r.get("base_structure_potential_score", 0)) else 0,
            "base_long_cycle_potential_score": float(r.get("base_long_cycle_potential_score", 0)) if pd.notna(r.get("base_long_cycle_potential_score", 0)) else 0,
            "base_risk_penalty": float(r.get("base_risk_penalty", 0)) if pd.notna(r.get("base_risk_penalty", 0)) else 0,
            "base_monthly_height_proxy_score": float(r.get("base_monthly_height_proxy_score", 0)) if pd.notna(r.get("base_monthly_height_proxy_score", 0)) else 0,
            "base_trade_quality_score": float(r.get("base_trade_quality_score", 0)) if pd.notna(r.get("base_trade_quality_score", 0)) else 0,
            "base_defense_dist": float(r.get("base_defense_dist", 0)) if pd.notna(r.get("base_defense_dist", 0)) else 0,
            "base_target_dist": float(r.get("base_target_dist", 0)) if pd.notna(r.get("base_target_dist", 0)) else 0,
            "base_risk_reward_ratio": float(r.get("base_risk_reward_ratio", 0)) if pd.notna(r.get("base_risk_reward_ratio", 0)) else 0,
            "base_bucket": str(r.get("base_bucket", "")) if pd.notna(r.get("base_bucket", "")) else "",
            "base_bucket_rank_score": float(r.get("base_bucket_rank_score", 0)) if pd.notna(r.get("base_bucket_rank_score", 0)) else 0,
            "score_base_model_legacy": float(r.get("score_base_model_legacy", 0)) if pd.notna(r.get("score_base_model_legacy", 0)) else 0,
            "limit_volume_mode": str(r.get("limit_volume_mode", "")) if pd.notna(r.get("limit_volume_mode", "")) else "",
            "short_ma_volume_entity_start": bool(r.get("short_ma_volume_entity_start", False)),
            "ma5_ma10_volume_continuation": bool(r.get("ma5_ma10_volume_continuation", False)),
            "ma5_cross_ma10": bool(r.get("ma5_cross_ma10", False)),
            "base_fibo_second_confirm_score": float(r.get("base_fibo_second_confirm_score", 0)) if pd.notna(r.get("base_fibo_second_confirm_score", 0)) else 0,
            "base_fibo_second_confirm_desc": str(r.get("base_fibo_second_confirm_desc", "")) if pd.notna(r.get("base_fibo_second_confirm_desc", "")) else "",
            "base_fibo_first_high": float(r.get("base_fibo_first_high", 0)) if pd.notna(r.get("base_fibo_first_high", 0)) else 0,
            "base_fibo_level_150": float(r.get("base_fibo_level_150", 0)) if pd.notna(r.get("base_fibo_level_150", 0)) else 0,
            "base_fibo_target_dist": float(r.get("base_fibo_target_dist", 0)) if pd.notna(r.get("base_fibo_target_dist", 0)) else 0,
            "beiliang_count_60_base": float(r.get("beiliang_count_60_base", 0)) if pd.notna(r.get("beiliang_count_60_base", 0)) else 0,
            "flat_volume_count_60_base": float(r.get("flat_volume_count_60_base", 0)) if pd.notna(r.get("flat_volume_count_60_base", 0)) else 0,
            "base_limitup_hold_score": float(r.get("base_limitup_hold_score", 0)) if pd.notna(r.get("base_limitup_hold_score", 0)) else 0,
            "near_pressure_dist": float(r.get("near_pressure_dist", 0)) if pd.notna(r.get("near_pressure_dist", 0)) else 0,
            "mid_pressure_dist": float(r.get("mid_pressure_dist", 0)) if pd.notna(r.get("mid_pressure_dist", 0)) else 0,
            "distance_to_key_base": float(r.get("distance_to_key_base", 0)) if pd.notna(r.get("distance_to_key_base", 0)) else 0,
            "base_rsi": float(r.get("base_rsi", 0)) if pd.notna(r.get("base_rsi", 0)) else 0,
            "base_cci": float(r.get("base_cci", 0)) if pd.notna(r.get("base_cci", 0)) else 0,
            "vr1": float(r["vr1"]) if pd.notna(r["vr1"]) else 0,
            "volr": float(r["volr"]) if pd.notna(r["volr"]) else 0,
            "body_ratio": float(r["body_ratio"]) if pd.notna(r["body_ratio"]) else 0,
            "pos": float(r["pos"]) if pd.notna(r["pos"]) else 0,
            "prehigh": float(r["prehigh"]) if pd.notna(r["prehigh"]) else 0,
            "break_rate": float(r["break_rate"]) if pd.notna(r["break_rate"]) else 0,
            "entity_pct": float(r["entity_pct"]) if pd.notna(r["entity_pct"]) else 0,
            "bias20": float(r["bias20"]) if pd.notna(r["bias20"]) else 0,
            "bias60": float(r["bias60"]) if pd.notna(r["bias60"]) else 0,
            "long_pos_250": float(r["long_pos_250"]) if pd.notna(r["long_pos_250"]) else 0,
            "xg": bool(r["xg"]),
        })

    return rows


def process_stock_deep(row):
    code = row["code"]
    name = row["name"]
    bs_code = row["bs_code"]

    df = get_daily_kline(bs_code)
    recent = calc_deep_rows(df, code)

    rows = []

    if recent.empty:
        return rows

    target_date = row["date"]
    recent = recent[recent["date"] == target_date]

    if recent.empty:
        return rows

    for _, r in recent.iterrows():
        rows.append({
            "code": code,
            "name": name,
            "bs_code": bs_code,
            "date": r["date"],
            "close": float(r["close"]),
            "pct_chg": float(r["pct_chg"]),
            "amount": float(r["amount"]) if pd.notna(r["amount"]) else 0,
            "score": float(r["score"]) if pd.notna(r["score"]) else 0,
            "base_score": float(row["base_score"]),
            "score_base_model": float(r["score_base_model"]) if pd.notna(r["score_base_model"]) else 0,
            "score_volume_structure": float(r["score_volume_structure"]) if pd.notna(r["score_volume_structure"]) else 0,
            "score_behavior": float(r["score_behavior"]) if pd.notna(r["score_behavior"]) else 0,
            "score_limitup_hold_3d": float(r.get("score_limitup_hold_3d", 0)) if pd.notna(r.get("score_limitup_hold_3d", 0)) else 0,
            "limitup_hold_level": str(r.get("limitup_hold_level", "")) if pd.notna(r.get("limitup_hold_level", "")) else "",
            "limitup_hold_ref_date": str(r.get("limitup_hold_ref_date", "")) if pd.notna(r.get("limitup_hold_ref_date", "")) else "",
            "score_key_pullback_hold": float(r.get("score_key_pullback_hold", 0)) if pd.notna(r.get("score_key_pullback_hold", 0)) else 0,
            "key_pullback_desc": str(r.get("key_pullback_desc", "")) if pd.notna(r.get("key_pullback_desc", "")) else "",
            "score_carry_structure": float(r.get("score_carry_structure", 0)) if pd.notna(r.get("score_carry_structure", 0)) else 0,
            "score_stepwise_push": float(r.get("score_stepwise_push", 0)) if pd.notna(r.get("score_stepwise_push", 0)) else 0,
            "stepwise_desc": str(r.get("stepwise_desc", "")) if pd.notna(r.get("stepwise_desc", "")) else "",
            "three_yin_tactic": bool(r.get("three_yin_tactic", False)),
            "double_yang_sandwich_yin": bool(r.get("double_yang_sandwich_yin", False)),
            "score_double_yang_sandwich": float(r.get("score_double_yang_sandwich", 0)) if pd.notna(r.get("score_double_yang_sandwich", 0)) else 0,
            "double_yang_sandwich_desc": str(r.get("double_yang_sandwich_desc", "")) if pd.notna(r.get("double_yang_sandwich_desc", "")) else "",
            "beibeiliang_short_pullback_risk": bool(r.get("beibeiliang_short_pullback_risk", False)),
            "beibeiliang_shrink_rate_after": float(r.get("beibeiliang_shrink_rate_after", 0)) if pd.notna(r.get("beibeiliang_shrink_rate_after", 0)) else 0,
            "score_pattern": float(r["score_pattern"]) if pd.notna(r["score_pattern"]) else 0,
            "score_structure_core": float(r["score_structure_core"]) if pd.notna(r["score_structure_core"]) else 0,
            "score_advanced_ao_kou": float(r.get("score_advanced_ao_kou", 0)) if pd.notna(r.get("score_advanced_ao_kou", 0)) else 0,
            "advanced_ao_kou_desc": str(r.get("advanced_ao_kou_desc", "")) if pd.notna(r.get("advanced_ao_kou_desc", "")) else "",
            "advanced_left_platform_score": float(r.get("advanced_left_platform_score", 0)) if pd.notna(r.get("advanced_left_platform_score", 0)) else 0,
            "advanced_left_platform_level": str(r.get("advanced_left_platform_level", "")) if pd.notna(r.get("advanced_left_platform_level", "")) else "",
            "advanced_first_volume_mid": float(r.get("advanced_first_volume_mid", 0)) if pd.notna(r.get("advanced_first_volume_mid", 0)) else 0,
            "advanced_target_150": float(r.get("advanced_target_150", 0)) if pd.notna(r.get("advanced_target_150", 0)) else 0,
            "advanced_target_dist": float(r.get("advanced_target_dist", 0)) if pd.notna(r.get("advanced_target_dist", 0)) else 0,
            "score_fibo_reclaim": float(r.get("score_fibo_reclaim", 0)) if pd.notna(r.get("score_fibo_reclaim", 0)) else 0,
            "fibo_reclaim_type": str(r.get("fibo_reclaim_type", "")) if pd.notna(r.get("fibo_reclaim_type", "")) else "",
            "fibo_reclaim_desc": str(r.get("fibo_reclaim_desc", "")) if pd.notna(r.get("fibo_reclaim_desc", "")) else "",
            "fibo_level_75": float(r.get("fibo_level_75", 0)) if pd.notna(r.get("fibo_level_75", 0)) else 0,
            "fibo_level_100": float(r.get("fibo_level_100", 0)) if pd.notna(r.get("fibo_level_100", 0)) else 0,
            "fibo_level_150": float(r.get("fibo_level_150", 0)) if pd.notna(r.get("fibo_level_150", 0)) else 0,
            "fibo_level_200": float(r.get("fibo_level_200", 0)) if pd.notna(r.get("fibo_level_200", 0)) else 0,
            "score_break_k_quality": float(r.get("score_break_k_quality", 0)) if pd.notna(r.get("score_break_k_quality", 0)) else 0,
            "score_limitup_activity": float(r.get("score_limitup_activity", 0)) if pd.notna(r.get("score_limitup_activity", 0)) else 0,
            "limit_up_count_50": float(r.get("limit_up_count_50", 0)) if pd.notna(r.get("limit_up_count_50", 0)) else 0,
            "structure_flags": str(r["structure_flags"]) if pd.notna(r["structure_flags"]) else "",
            "structure_neckline": float(r["structure_neckline"]) if pd.notna(r["structure_neckline"]) else 0,
            "score_overlap_adjustment": float(r["score_overlap_adjustment"]) if pd.notna(r["score_overlap_adjustment"]) else 0,
            "score_trend_stage": float(r["score_trend_stage"]) if pd.notna(r["score_trend_stage"]) else 0,
            "score_count": float(r["score_count"]) if pd.notna(r["score_count"]) else 0,
            "score_yang_yin_volume": float(r.get("score_yang_yin_volume", 0)) if pd.notna(r.get("score_yang_yin_volume", 0)) else 0,
            "score_pressure_space": float(r.get("score_pressure_space", 0)) if pd.notna(r.get("score_pressure_space", 0)) else 0,
            "score_key_distance": float(r.get("score_key_distance", 0)) if pd.notna(r.get("score_key_distance", 0)) else 0,
            "distance_to_key": float(r.get("distance_to_key", 0)) if pd.notna(r.get("distance_to_key", 0)) else 0,
            "score_indicator": float(r["score_indicator"]) if pd.notna(r["score_indicator"]) else 0,
            "score_long_cycle": float(r["score_long_cycle"]) if pd.notna(r["score_long_cycle"]) else 0,
            "score_monthly_cycle": float(r["score_monthly_cycle"]) if pd.notna(r["score_monthly_cycle"]) else 0,
            "monthly_flags": str(r["monthly_flags"]) if pd.notna(r["monthly_flags"]) else "",
            "monthly_support_months": float(r["monthly_support_months"]) if pd.notna(r["monthly_support_months"]) else 0,
            "monthly_volume_score": float(r["monthly_volume_score"]) if pd.notna(r["monthly_volume_score"]) else 0,
            "monthly_detail": str(r.get("monthly_detail", "")) if pd.notna(r.get("monthly_detail", "")) else "",
            "score_stage_adjustment": float(r["score_stage_adjustment"]) if pd.notna(r["score_stage_adjustment"]) else 0,
            "trade_stage": str(r["trade_stage"]) if pd.notna(r["trade_stage"]) else "",
            "score_penalty": float(r["score_penalty"]) if pd.notna(r["score_penalty"]) else 0,
            "score_monthly_height_space": float(r.get("score_monthly_height_space", 0)) if pd.notna(r.get("score_monthly_height_space", 0)) else 0,
            "score_trade_quality": float(r.get("score_trade_quality", 0)) if pd.notna(r.get("score_trade_quality", 0)) else 0,
            "trade_priority_score": float(r.get("trade_priority_score", 0)) if pd.notna(r.get("trade_priority_score", 0)) else 0,
            "structure_key_level": float(r.get("structure_key_level", 0)) if pd.notna(r.get("structure_key_level", 0)) else 0,
            "defense_level": float(r.get("defense_level", 0)) if pd.notna(r.get("defense_level", 0)) else 0,
            "defense_source": str(r.get("defense_source", "")) if pd.notna(r.get("defense_source", "")) else "",
            "defense_buffer_pct": float(r.get("defense_buffer_pct", 0)) if pd.notna(r.get("defense_buffer_pct", 0)) else 0,
            "defense_dist": float(r.get("defense_dist", 0)) if pd.notna(r.get("defense_dist", 0)) else 0,
            "target_dist": float(r.get("target_dist", 0)) if pd.notna(r.get("target_dist", 0)) else 0,
            "risk_reward_ratio": float(r.get("risk_reward_ratio", 0)) if pd.notna(r.get("risk_reward_ratio", 0)) else 0,
            "total_score": float(r["total_score"]) if pd.notna(r["total_score"]) else 0,
            "candidate_pool": str(r.get("candidate_pool", "优先候选池")) if pd.notna(r.get("candidate_pool", "")) else "优先候选池",
            "candidate_pool_reason": str(r.get("candidate_pool_reason", "")) if pd.notna(r.get("candidate_pool_reason", "")) else "",
            "chase_risk_flags": str(r.get("chase_risk_flags", "")) if pd.notna(r.get("chase_risk_flags", "")) else "",
            "chase_risk_count": float(r.get("chase_risk_count", 0)) if pd.notna(r.get("chase_risk_count", 0)) else 0,
            "score_chase_penalty": float(r.get("score_chase_penalty", 0)) if pd.notna(r.get("score_chase_penalty", 0)) else 0,
            "chase_score_cap": float(r.get("chase_score_cap", 0)) if pd.notna(r.get("chase_score_cap", 0)) else 0,
            "vr1": float(r["vr1"]) if pd.notna(r["vr1"]) else 0,
            "volr": float(r["volr"]) if pd.notna(r["volr"]) else 0,
            "body_ratio": float(r["body_ratio"]) if pd.notna(r["body_ratio"]) else 0,
            "pos": float(r["pos"]) if pd.notna(r["pos"]) else 0,
            "break_rate": float(r["break_rate"]) if pd.notna(r["break_rate"]) else 0,
            "entity_pct": float(r["entity_pct"]) if pd.notna(r["entity_pct"]) else 0,
            "bias20": float(r["bias20"]) if pd.notna(r["bias20"]) else 0,
            "bias60": float(r["bias60"]) if pd.notna(r["bias60"]) else 0,
            "long_pos_250": float(r["long_pos_250"]) if pd.notna(r["long_pos_250"]) else 0,
            "near_pressure_dist": float(r.get("near_pressure_dist", 0)) if pd.notna(r.get("near_pressure_dist", 0)) else 0,
            "mid_pressure_dist": float(r.get("mid_pressure_dist", 0)) if pd.notna(r.get("mid_pressure_dist", 0)) else 0,
            "overhead_pressure_dist": float(r["overhead_pressure_dist"]) if pd.notna(r.get("overhead_pressure_dist", 0)) else 0,
            "beiliang_count_20": float(r["beiliang_count_20"]) if pd.notna(r["beiliang_count_20"]) else 0,
            "up_down_vol_ratio_20": float(r["up_down_vol_ratio_20"]) if pd.notna(r["up_down_vol_ratio_20"]) else 0,
            "bull_engulf_count_20": float(r["bull_engulf_count_20"]) if pd.notna(r["bull_engulf_count_20"]) else 0,
            "gap_up_count_20": float(r["gap_up_count_20"]) if pd.notna(r["gap_up_count_20"]) else 0,
            "up_days_20": float(r["up_days_20"]) if pd.notna(r["up_days_20"]) else 0,
            "up_days_40": float(r.get("up_days_40", 0)) if pd.notna(r.get("up_days_40", 0)) else 0,
            "up_days_60": float(r.get("up_days_60", 0)) if pd.notna(r.get("up_days_60", 0)) else 0,
            "up_down_vol_ratio_40": float(r.get("up_down_vol_ratio_40", 0)) if pd.notna(r.get("up_down_vol_ratio_40", 0)) else 0,
            "up_down_vol_ratio_60": float(r.get("up_down_vol_ratio_60", 0)) if pd.notna(r.get("up_down_vol_ratio_60", 0)) else 0,
            "bull_engulf_count_50": float(r.get("bull_engulf_count_50", 0)) if pd.notna(r.get("bull_engulf_count_50", 0)) else 0,
            "bull_engulf_quality_count_50": float(r.get("bull_engulf_quality_count_50", 0)) if pd.notna(r.get("bull_engulf_quality_count_50", 0)) else 0,
            "scattered_beiliang_count_60": float(r.get("scattered_beiliang_count_60", 0)) if pd.notna(r.get("scattered_beiliang_count_60", 0)) else 0,
            "flat_volume_count_60": float(r.get("flat_volume_count_60", 0)) if pd.notna(r.get("flat_volume_count_60", 0)) else 0,
            "effective_gap_up_count_50": float(r.get("effective_gap_up_count_50", 0)) if pd.notna(r.get("effective_gap_up_count_50", 0)) else 0,
            "ma20_slope_5": float(r["ma20_slope_5"]) if pd.notna(r["ma20_slope_5"]) else 0,
            "rsi": float(r["rsi"]) if pd.notna(r["rsi"]) else 0,
            "cci": float(r["cci"]) if pd.notna(r["cci"]) else 0,
            "xg": bool(r["xg"]),
        })

    for rr in rows:
        risk = evaluate_regulatory_risk(rr.get("code", ""), rr.get("name", ""))
        rr["score_regulatory_risk"] = float(risk.get("penalty", 0.0))
        rr["risk_hard_exclude"] = bool(risk.get("hard_exclude", False))
        rr["risk_flags"] = "；".join(risk.get("flags", []))
        rr["risk_note"] = str(risk.get("note", ""))
        rr["total_score"] = float(rr.get("total_score", 0.0)) + rr["score_regulatory_risk"]
        if rr["risk_hard_exclude"]:
            rr["total_score"] = min(rr["total_score"], 59.0)
        rr.update(classify_next_day_strategy(rr))

    return rows


def select_deep_targets_v10(base_rows, limit):
    """
    V10基础候选分桶选择。
    不再简单按单一base_score取前N，避免强攻/极端放量票霸榜。
    每个股票只保留一条最优候选，减少深度评分重复计算。
    """
    if not base_rows:
        return [], {}

    limit = int(max(1, limit))

    # 先按日期、分桶排序分、基础总分去重，同一股票只取最优一条。
    sorted_rows = sorted(
        base_rows,
        key=lambda x: (
            str(x.get("date", "")),
            safe_float(x.get("base_bucket_rank_score", x.get("base_score", 0))),
            safe_float(x.get("base_total_score", x.get("base_score", 0))),
            safe_float(x.get("score", 0)),
        ),
        reverse=True,
    )
    dedup = []
    seen_codes = set()
    for r in sorted_rows:
        code = str(r.get("code", ""))
        if not code or code in seen_codes:
            continue
        seen_codes.add(code)
        dedup.append(r)

    # 配额：健康攻击保留原模型优势，低位修复/量价承接/结构潜力保证入口，强势观察限量。
    quota_plan = [
        ("健康攻击", 0.27),
        ("低位修复", 0.20),
        ("量价承接", 0.18),
        ("结构潜力", 0.22),
        ("交易质量", 0.12),
        ("强势观察", 0.08),
    ]
    quotas = {name: max(3, int(round(limit * ratio))) for name, ratio in quota_plan}
    # 修正四舍五入误差。
    while sum(quotas.values()) > limit:
        # 优先从强势观察、健康攻击里减，避免挤压低位/承接/结构。
        for name in ["强势观察", "健康攻击", "结构潜力", "量价承接", "低位修复"]:
            if quotas.get(name, 0) > 3 and sum(quotas.values()) > limit:
                quotas[name] -= 1
    while sum(quotas.values()) < limit:
        for name in ["健康攻击", "低位修复", "量价承接", "结构潜力", "强势观察"]:
            if sum(quotas.values()) < limit:
                quotas[name] += 1

    selected = []
    selected_codes = set()
    bucket_stats = {}

    for bucket, _ratio in quota_plan:
        rows = [r for r in dedup if str(r.get("base_bucket", "健康攻击")) == bucket and str(r.get("code", "")) not in selected_codes]
        rows = sorted(
            rows,
            key=lambda x: (
                safe_float(x.get("base_bucket_rank_score", x.get("base_score", 0))),
                safe_float(x.get("base_total_score", x.get("base_score", 0))),
                safe_float(x.get("score", 0)),
            ),
            reverse=True,
        )
        take = rows[:quotas.get(bucket, 0)]
        for r in take:
            selected.append(r)
            selected_codes.add(str(r.get("code", "")))
        bucket_stats[bucket] = {"available": len(rows), "quota": quotas.get(bucket, 0), "selected": len(take)}

    # 若某些桶不足，用全局优质候选补齐；仍然一股一条。
    if len(selected) < limit:
        for r in dedup:
            code = str(r.get("code", ""))
            if code in selected_codes:
                continue
            selected.append(r)
            selected_codes.add(code)
            if len(selected) >= limit:
                break

    selected = sorted(
        selected,
        key=lambda x: (
            str(x.get("date", "")),
            safe_float(x.get("base_bucket_rank_score", x.get("base_score", 0))),
            safe_float(x.get("base_total_score", x.get("base_score", 0))),
            safe_float(x.get("score", 0)),
        ),
        reverse=True,
    )[:limit]

    bucket_stats["合计"] = {"available": len(dedup), "quota": limit, "selected": len(selected)}
    return selected, bucket_stats


def calc_no_chase_line(close, strategy_type):
    """V11.3：根据策略类型给出次日禁止追高线。"""
    close = safe_float(close)
    if close <= 0:
        return 0.0
    if "可低吸" in strategy_type:
        pct = 0.015
    elif "回踩" in strategy_type:
        pct = 0.025
    elif "强势接力" in strategy_type:
        pct = 0.045
    else:
        pct = 0.020
    return round(close * (1 + pct), 2)


def classify_next_day_strategy(s):
    """
    V11.3 次日执行策略分类。
    目的不是预测一定涨停，而是把一号员工候选转化为条件式执行建议：
    哪些可低吸、哪些等回踩、哪些只做强势接力确认、哪些禁止追高。
    """
    close = safe_float(s.get("close", 0))
    pct_chg = safe_float(s.get("pct_chg", 0))
    defense = safe_float(s.get("defense_level", 0))
    structure_key = safe_float(s.get("structure_key_level", 0))
    defense_dist = safe_float(s.get("defense_dist", 0))
    target_dist = safe_float(s.get("target_dist", 0))
    rr = safe_float(s.get("risk_reward_ratio", 0))
    near_pressure = safe_float(s.get("near_pressure_dist", 0))
    bias20 = safe_float(s.get("bias20", 0))
    bias60 = safe_float(s.get("bias60", 0))
    long_pos = safe_float(s.get("long_pos_250", 0))
    score_trade_quality = safe_float(s.get("score_trade_quality", 0))
    trade_priority = safe_float(s.get("trade_priority_score", 0))
    score_structure = safe_float(s.get("score_structure_core", 0)) + safe_float(s.get("score_advanced_ao_kou", 0)) + safe_float(s.get("score_fibo_reclaim", 0))
    score_limitup_hold = safe_float(s.get("score_limitup_hold_3d", 0))
    score_double_yang = safe_float(s.get("score_double_yang_sandwich", 0))
    candidate_pool = str(s.get("candidate_pool", "优先候选池"))
    chase_flags = str(s.get("chase_risk_flags", ""))
    risk_flags = str(s.get("risk_flags", ""))
    fibo_type = str(s.get("fibo_reclaim_type", ""))

    if bool(s.get("risk_hard_exclude", False)) or risk_flags:
        return {
            "next_day_strategy": "E类：雷区剔除候选",
            "no_chase_line": 0.0,
            "pullback_zone": "不参与交易",
            "confirm_rule": "命中基本面/监管/治理雷区，一号员工阶段直接剔除优先池；技术形态不抵消重大雷区。",
            "abandon_rule": "不进入三号员工可交易候选。",
            "strategy_note": f"雷区标签：{risk_flags}" if risk_flags else "命中硬排雷规则",
        }

    high_chase_risk = (
        (candidate_pool != "优先候选池")
        or (pct_chg >= 6.5 and defense_dist > 0.045)
        or (pct_chg >= 4.0 and target_dist < 0.10)
        or (near_pressure > 0 and near_pressure < 0.055)
        or (defense_dist > 0.075)
        or (bias20 > 0.13)
        or (bias60 > 0.16)
        or ("高位" in chase_flags)
        or ("追高" in chase_flags)
        or ("回抽100" in fibo_type)
        or ("高位回抽" in fibo_type)
    )

    strong_relay = (
        (pct_chg >= 8.5 or safe_float(s.get("score_limitup_activity", 0)) > 0 or score_limitup_hold > 0)
        and not high_chase_risk
        and target_dist >= 0.08
        and defense_dist <= 0.065
        and long_pos < 0.75
    )

    low_absorb = (
        defense > 0
        and defense_dist <= 0.035
        and target_dist >= 0.12
        and rr >= 2.0
        and pct_chg <= 4.5
        and long_pos <= 0.65
        and score_trade_quality >= 12
    )

    pullback_confirm = (
        not high_chase_risk
        and not low_absorb
        and (score_structure >= 6 or score_double_yang >= 2 or score_trade_quality >= 10 or trade_priority >= 18)
    )

    if strong_relay:
        strategy_type = "C类：强势接力候选"
        key = structure_key if structure_key > 0 else defense
        observe_zone = f"强势确认为主；若开板/回落，优先看{key:.2f}附近承接" if key > 0 else "强势确认为主"
        confirm_rule = "只在开盘后快速转强、封板稳定、不开板或开板后快速回封、分时不破均线时确认；不能只因高开就追。"
        abandon_rule = "高开冲高回落、跌破昨收或跌破分时均线后不能快速收回，放弃接力。"
        note = "强势接力难度最高，只给三号员工盘中确认，不等于开盘追涨。"
    elif low_absorb:
        strategy_type = "A类：可低吸候选"
        key = structure_key if structure_key > 0 else defense
        low = defense
        high = key if key > 0 else close
        observe_zone = f"{min(low, high):.2f}-{max(low, high):.2f}附近" if low > 0 and high > 0 else "真实防守位附近"
        confirm_rule = "平开/小高开更优；回踩结构关键位或真实防守位附近不破，缩量承接后重新放量站上分时均线，再交给三号员工确认。"
        abandon_rule = f"有效跌破真实交易防守位{defense:.2f}，或高开超过禁止追高线后冲高回落，放弃。"
        note = "买点核心是靠近防守位和风险收益比，不追高。"
    elif pullback_confirm:
        strategy_type = "B类：回踩确认候选"
        key = structure_key if structure_key > 0 else defense
        observe_zone = f"{key:.2f}附近回踩承接" if key > 0 else "突破位/昨收附近回踩承接"
        confirm_rule = "不直接追；只等回踩昨日突破位、结构关键位、涨停实体位或首次倍量高点不破，并重新放量上穿分时均线。"
        abandon_rule = "不回踩不买；高开冲高回落跌破昨收，或跌破真实交易防守位，放弃。"
        note = "有结构或资金迹象，但需要次日回踩确认。"
    else:
        strategy_type = "D类：禁止追高候选"
        key = structure_key if structure_key > 0 else defense
        observe_zone = f"只看{key:.2f}附近是否重新承接" if key > 0 else "只观察，不主动买"
        confirm_rule = "仅在明显回踩关键位不破、量能恢复、分时重新转强后，才允许三号员工重新评估。"
        abandon_rule = "高开超过2%-3%、开到压力/扩展位附近、冲高回落跌破昨收，直接放弃。"
        note = "强势不等于可追；当前买点不舒服或赔率不足。"

    return {
        "next_day_strategy": strategy_type,
        "no_chase_line": calc_no_chase_line(close, strategy_type),
        "pullback_zone": observe_zone,
        "confirm_rule": confirm_rule,
        "abandon_rule": abandon_rule,
        "strategy_note": note,
    }

def build_reason(s):
    reasons = []
    reasons.append(f"原模型评分{s['score']:.1f}，基础初筛分{s['base_score']:.1f}；原模型折算{s.get('score_base_model', 0):.1f}分")

    vr1 = s.get("vr1", 0)
    volr = s.get("volr", 0)
    if 1.8 < vr1 < 2.5:
        vol_tag = "标准倍量"
    elif vr1 >= 3.5:
        vol_tag = "超倍量/极端放量，谨慎"
    elif vr1 >= 2.5:
        vol_tag = "偏强放量，降档看待"
    elif vr1 >= 1.5:
        vol_tag = "接近倍量"
    else:
        vol_tag = "非倍量"

    reasons.append(
        f"量能：昨比{vr1:.2f}倍（{vol_tag}），20日比{volr:.2f}倍，20日健康倍量{s['beiliang_count_20']:.0f}次，量价结构分{s.get('score_volume_structure', 0):.1f}"
    )
    reasons.append(
        f"阳阴量价：20/40/60日阳线{s.get('up_days_20', 0):.0f}/{s.get('up_days_40', 0):.0f}/{s.get('up_days_60', 0):.0f}天，阳量/阴量20/40/60日={s.get('up_down_vol_ratio_20', 0):.2f}/{s.get('up_down_vol_ratio_40', 0):.2f}/{s.get('up_down_vol_ratio_60', 0):.2f}，结构分{s.get('score_yang_yin_volume', 0):.1f}"
    )
    reasons.append(
        f"实体/突破：实体{s['body_ratio']:.2f}倍，实体涨幅{s['entity_pct']:.2f}%，突破{s['break_rate']:.2%}，收盘位置{s['pos']:.2%}"
    )
    reasons.append(
        f"位置/压力：年内位置{s['long_pos_250']:.2%}，近端压力{s.get('near_pressure_dist', 0):.2%}，中层压力{s.get('mid_pressure_dist', 0):.2%}，远端压力{s.get('overhead_pressure_dist', 0):.2%}，"
        f"压力空间分{s.get('score_pressure_space', 0):.1f}，离关键位{s.get('distance_to_key', 0):.2%}/舒适度{s.get('score_key_distance', 0):.1f}，20日乖离{s['bias20']:.2%}，60日乖离{s['bias60']:.2%}"
    )
    reasons.append(
        f"V11.1交易质量：结构关键位{s.get('structure_key_level', 0):.2f}（{s.get('defense_source', '')}），真实交易防守位{s.get('defense_level', 0):.2f}，"
        f"缓冲{s.get('defense_buffer_pct', 0):.1%}，离真实防守{s.get('defense_dist', 0):.2%}，第一目标/压力空间{s.get('target_dist', 0):.2%}，"
        f"风险收益比{s.get('risk_reward_ratio', 0):.2f}，买点质量分{s.get('score_trade_quality', 0):.1f}，月线高度/空间分{s.get('score_monthly_height_space', 0):.1f}，交易优先级{s.get('trade_priority_score', 0):.1f}"
    )
    if not s.get('next_day_strategy'):
        s.update(classify_next_day_strategy(s))
    reasons.append(
        f"V11.3次日策略：{s.get('next_day_strategy', '')}；禁止追高线{s.get('no_chase_line', 0):.2f}；"
        f"回踩观察区：{s.get('pullback_zone', '')}；确认条件：{s.get('confirm_rule', '')}；放弃条件：{s.get('abandon_rule', '')}"
    )
    if s.get("chase_risk_flags"):
        cap_text = f"，封顶{s.get('chase_score_cap', 0):.1f}" if s.get("chase_score_cap", 0) else ""
        reasons.append(
            f"追高闸门：{s.get('candidate_pool', '未知')}，{s.get('candidate_pool_reason', '')}；"
            f"风险项{s.get('chase_risk_count', 0):.0f}个：{s.get('chase_risk_flags', '')}，追高惩罚{s.get('score_chase_penalty', 0):.1f}{cap_text}"
        )
    reasons.append(
        f"行为频次：20日阳包阴{s['bull_engulf_count_20']:.0f}次，50日阳包阴{s.get('bull_engulf_count_50', 0):.0f}次/高质量{s.get('bull_engulf_quality_count_50', 0):.0f}次，"
        f"20日跳空{s['gap_up_count_20']:.0f}次/50日有效跳空{s.get('effective_gap_up_count_50', 0):.0f}次，"
        f"60日分散健康倍量{s.get('scattered_beiliang_count_60', 0):.0f}次，倍量后平量{s.get('flat_volume_count_60', 0):.0f}次，"
        f"50日涨停{s.get('limit_up_count_50', 0):.0f}次/活跃度{s.get('score_limitup_activity', 0):.1f}，频次分{s.get('score_count', 0):.1f}"
    )
    reasons.append(
        f"承接结构：涨停后三日{s.get('score_limitup_hold_3d', 0):.1f}分（{s.get('limitup_hold_level', '')}），"
        f"关键位回踩{s.get('score_key_pullback_hold', 0):.1f}分（{s.get('key_pullback_desc', '')}），承接合计{s.get('score_carry_structure', 0):.1f}分"
    )
    if s.get("score_stepwise_push", 0) > 0:
        reasons.append(f"台阶式资金推进：{s.get('stepwise_desc', '')}")
    if s.get("three_yin_tactic"):
        reasons.append("三阴战法：已识别，属于回撤中的轻度结构提示，需结合关键位与后续大阳修复")
    if s.get("double_yang_sandwich_yin"):
        reasons.append(
            f"双阳夹阴/分歧反包：{s.get('double_yang_sandwich_desc', '')}，得分{s.get('score_double_yang_sandwich', 0):.1f}；该项定位为短线承接确认，不替代主结构"
        )
    if s.get("beibeiliang_short_pullback_risk"):
        reasons.append(f"连续倍倍量后承接：后续量能缩量{s.get('beibeiliang_shrink_rate_after', 0):.1%}，仅提示短线承接风险，不代表中长期结构变坏")

    if s.get("structure_flags"):
        reasons.append(f"日线结构：{s.get('structure_flags')}，关键位{s.get('structure_neckline', 0):.2f}，日线结构分{s.get('score_structure_core', 0):.1f}")
    else:
        reasons.append("日线结构：暂无凹口/圆弧底/破底翻强确认")

    if s.get("score_break_k_quality", 0) > 0:
        reasons.append(f"关键结构突破K线质量：{s.get('score_break_k_quality', 0):.1f}分，说明突破K线实体/收盘/跳空或阳梯量配合较好")

    if abs(s.get("score_fibo_reclaim", 0)) > 0:
        reasons.append(
            f"首次倍量黄金扩展：{s.get('score_fibo_reclaim', 0):.1f}分，{s.get('fibo_reclaim_type', '')}；"
            f"75%={s.get('fibo_level_75', 0):.2f}，100%={s.get('fibo_level_100', 0):.2f}，150%={s.get('fibo_level_150', 0):.2f}，200%={s.get('fibo_level_200', 0):.2f}；"
            f"{s.get('fibo_reclaim_desc', '')}"
        )

    if s.get("score_advanced_ao_kou", 0) > 0:
        reasons.append(
            f"高级凹口二次倍量：{s.get('score_advanced_ao_kou', 0):.1f}分，"
            f"左侧平台{s.get('advanced_left_platform_level', '')}/{s.get('advanced_left_platform_score', 0):.1f}分，"
            f"第一次倍量实体中位{s.get('advanced_first_volume_mid', 0):.2f}，"
            f"150%目标{s.get('advanced_target_150', 0):.2f}（距当前{s.get('advanced_target_dist', 0):.1%}）；"
            f"{s.get('advanced_ao_kou_desc', '')}"
        )

    if s.get("monthly_flags"):
        reasons.append(
            f"月线BBI/BOLL：{s.get('monthly_flags')}，中轨支撑{s.get('monthly_support_months', 0):.0f}月，月线分{s.get('score_monthly_cycle', 0):.1f}；细分：{s.get('monthly_detail', '')}"
        )
    else:
        reasons.append(f"月线BBI/BOLL：暂无强确认，月线分{s.get('score_monthly_cycle', 0):.1f}；细分：{s.get('monthly_detail', '')}")

    reasons.append(f"交易阶段：{s.get('trade_stage', '未知')}，阶段调整{s.get('score_stage_adjustment', 0):.1f}")

    # 指标必须解释好坏，不能只报数。
    rsi = s.get('rsi', 0)
    cci = s.get('cci', 0)
    slope = s.get('ma20_slope_5', 0)
    if rsi >= 85:
        rsi_tag = "严重过热"
    elif rsi >= 80:
        rsi_tag = "明显过热"
    elif rsi >= 70:
        rsi_tag = "强势偏热"
    elif rsi >= 60:
        rsi_tag = "趋势偏强"
    elif rsi >= 45:
        rsi_tag = "健康修复"
    else:
        rsi_tag = "偏弱"

    if cci >= 300:
        cci_tag = "极端过热"
    elif cci >= 220:
        cci_tag = "明显过热"
    elif cci >= 180:
        cci_tag = "偏热"
    elif cci >= 100:
        cci_tag = "强势启动"
    elif cci >= 0:
        cci_tag = "温和修复"
    else:
        cci_tag = "偏弱"

    reasons.append(f"指标状态：MA20斜率{slope:.2%}，RSI{rsi:.1f}({rsi_tag})，CCI{cci:.1f}({cci_tag})，指标分{s.get('score_indicator', 0):.1f}")

    if s.get("risk_flags"):
        hard_text = "，硬剔除/降级" if s.get("risk_hard_exclude") else "，大幅降权"
        reasons.append(f"雷区筛查：命中{s.get('risk_flags')}，雷区扣分{s.get('score_regulatory_risk', 0):.1f}{hard_text}")
    else:
        reasons.append("雷区筛查：risk_flags.json未命中；一号员工仍按技术候选处理，后续建议持续扩充雷区库")

    return "；".join(reasons) + "。"

def build_message(final_signals, dates, stock_count=0, kline_success=0, kline_fail=0, deep_count=0):
    lines = []
    lines.append("📊 <b>一号员工-结构选股分析报告</b>")
    lines.append(f"🗓 排查日期：{', '.join(dates) if dates else '未知'}")
    lines.append(f"⏱ 运行时间：{bj_time_str()} 北京时间")
    lines.append(f"股票池：{stock_count}只 | K线成功：{kline_success}只 | 失败：{kline_fail}只")
    lines.append(f"深度评分：{deep_count}只 | 分析输出：<b>{len(final_signals)}</b>只")
    lines.append(f"评分阈值：综合分 ≥ {FINAL_SCORE_THRESHOLD:.0f}；80分以下不推送，不固定凑满数量。")
    lines.append(f"V11.3精简推送：默认只推前{RESULT_LIMIT}只；每只给出次日策略分类，候选≠开盘追。")
    lines.append("V11.1交易质量闸门：严格区分结构关键位/真实交易防守位；黄金倍量必须明显凹口干净突破；雷区筛查前置，强但不敢买或有雷的票降级观察。")
    lines.append("说明：一号员工只做结构分析，不提供复制代码；最终可操作代码由三号员工输出。")
    lines.append("━━━━━━━━━━━━━━")

    if not final_signals:
        lines.append("")
        lines.append("⚠️ 今日暂无符合综合评分条件的新股票。")
        return "\n".join(lines)

    stage_count = {}
    for s in final_signals:
        stage = s.get("trade_stage", "未知")
        stage_count[stage] = stage_count.get(stage, 0) + 1
    stage_text = "；".join([f"{k}{v}只" for k, v in stage_count.items()])
    lines.append(f"阶段分布：{stage_text}")
    lines.append("")
    lines.append("<b>详细结构诊断</b>")

    for i, s in enumerate(final_signals, 1):
        lines.append("")
        lines.append(f"{i}. <b>{html.escape(s['name'])}</b> ({html.escape(s['code'])})")
        lines.append(f"日期：{s['date']}")
        lines.append(f"收盘：{s['close']:.2f} | 涨幅：{s['pct_chg']:.2f}% | 成交额：{s['amount'] / 100000000:.2f}亿")
        if not s.get('next_day_strategy'):
            s.update(classify_next_day_strategy(s))
        lines.append(
            f"次日策略：{html.escape(str(s.get('next_day_strategy', '')))} | 禁止追高线：{s.get('no_chase_line', 0):.2f} | 回踩观察区：{html.escape(str(s.get('pullback_zone', '')))}"
        )
        lines.append(
            f"综合分：{s['total_score']:.2f} | 阶段：{html.escape(str(s.get('trade_stage', '未知')))} | 池：{html.escape(str(s.get('candidate_pool', '优先候选池')))} | "
            f"原模型：{s['score_base_model']:.1f} | 量能：{s['score_volume_structure']:.1f} | 行为：{s['score_behavior']:.1f} | "
            f"承接：{s.get('score_carry_structure', 0):.1f} | 台阶：{s.get('score_stepwise_push', 0):.1f} | "
            f"形态：{s['score_pattern']:.1f} | 日线结构：{s['score_structure_core']:.1f} | 高级凹口：{s.get('score_advanced_ao_kou', 0):.1f} | 月线：{s.get('score_monthly_cycle', 0):.1f} | "
            f"趋势：{s['score_trend_stage']:.1f} | 阳阴量价：{s.get('score_yang_yin_volume', 0):.1f} | 压力空间：{s.get('score_pressure_space', 0):.1f} | 关键位舒适度：{s.get('score_key_distance', 0):.1f} | 频次：{s['score_count']:.1f} | 指标：{s['score_indicator']:.1f} | "
            f"长周期：{s['score_long_cycle']:.1f} | 月线空间：{s.get('score_monthly_height_space', 0):.1f} | 买点质量：{s.get('score_trade_quality', 0):.1f} | 交易优先：{s.get('trade_priority_score', 0):.1f} | "
            f"阶段调整：{s.get('score_stage_adjustment', 0):.1f} | 风险：{s['score_penalty']:.1f} | 雷区：{s.get('score_regulatory_risk', 0):.1f} | 重复降权：{s['score_overlap_adjustment']:.1f}"
        )
        lines.append("诊断：" + build_reason(s))

    return "\n".join(lines)


def build_error_message(error_text):
    lines = []
    lines.append("⚠️ <b>每日选股脚本运行失败</b>")
    lines.append(f"⏱ 运行时间：{bj_time_str()} 北京时间")
    lines.append("━━━━━━━━━━━━━━")
    lines.append(html.escape(str(error_text))[:3000])
    return "\n".join(lines)


def main():
    start_ts = time.time()

    print(f"ENABLE_TELEGRAM={ENABLE_TELEGRAM}")
    print(f"RESULT_LIMIT_RAW={RESULT_LIMIT_RAW}")
    print(f"TOP_PUSH_LIMIT={TOP_PUSH_LIMIT}")
    print(f"RESULT_LIMIT_EFFECTIVE={RESULT_LIMIT}")
    print(f"FINAL_SCORE_THRESHOLD={FINAL_SCORE_THRESHOLD}")
    print(f"ONLY_PUSH_PRIORITY_POOL={ONLY_PUSH_PRIORITY_POOL}")
    print(f"SAVE_STRONG_WATCH_POOL={SAVE_STRONG_WATCH_POOL}")
    print(f"DEEP_SCORE_LIMIT_RAW={DEEP_SCORE_LIMIT_RAW}")
    print(f"DEEP_SCORE_HARD_CAP={DEEP_SCORE_HARD_CAP}")
    print(f"DEEP_SCORE_LIMIT_EFFECTIVE={DEEP_SCORE_LIMIT}")
    print(f"MAX_RUNTIME_SECONDS={MAX_RUNTIME_SECONDS}")
    print(f"BASIC_RUNTIME_SECONDS={BASIC_RUNTIME_SECONDS}")
    print(f"DEEP_RUNTIME_SECONDS={DEEP_RUNTIME_SECONDS}")
    print(f"MIN_VALID_KLINE={MIN_VALID_KLINE}")
    print(f"PROGRESS_INTERVAL={PROGRESS_INTERVAL}")
    print(f"SINGLE_STOCK_TIMEOUT_SECONDS={SINGLE_STOCK_TIMEOUT_SECONDS}")
    print(f"KLINE_MAX_RETRIES={KLINE_MAX_RETRIES}")
    print(f"BROKEN_PIPE_PAUSE_THRESHOLD={BROKEN_PIPE_PAUSE_THRESHOLD}")
    print(f"BROKEN_PIPE_PAUSE_SECONDS={BROKEN_PIPE_PAUSE_SECONDS}")
    print(f"RETRY_FAILED_KLINE_AFTER_SCAN={RETRY_FAILED_KLINE_AFTER_SCAN}")
    print(f"RETRY_FAILED_KLINE_LIMIT={RETRY_FAILED_KLINE_LIMIT}")
    print(f"MIN_FORMAL_COVERAGE_RATE={MIN_FORMAL_COVERAGE_RATE}")
    print(f"KLINE_LOOKBACK_DAYS={KLINE_LOOKBACK_DAYS}")
    print(f"北京时间：{bj_time_str()}")

    if not baostock_login():
        msg = "BaoStock登录失败，无法获取数据。"
        print(msg)
        send_telegram(build_error_message(msg))
        return

    try:
        history = load_signal_history()

        print("抓取A股列表...")
        stock_list = get_a_stock_list()

        if stock_list.empty:
            msg = build_message([], [], 0, 0, 0, 0)
            send_telegram(msg)
            return

        print(f"共抓取 {len(stock_list)} 只股票")
        print("V11交易质量版：全市场轻量闸门，分桶选取深度候选，按大周期空间/结构边界/买点质量/风险收益比/量能确认综合排名。")

        base_rows = []
        all_dates = set()
        kline_success = 0
        kline_fail = 0
        failed_symbols = []
        processed_count = 0

        for _, row in stock_list.iterrows():
            processed_count += 1

            if processed_count % PROGRESS_INTERVAL == 0:
                progress_line("基础评分", processed_count, len(stock_list), start_ts, kline_success, kline_fail)

            if time.time() - start_ts > BASIC_RUNTIME_SECONDS:
                print("达到基础评分阶段最大运行时间，停止继续拉取，保留已有基础候选进入深度评分。")
                break

            try:
                rows = process_stock_base(row)

                if rows:
                    kline_success += 1
                else:
                    kline_fail += 1
                    failed_symbols.append({"code": row.get("代码", ""), "name": row.get("名称", ""), "bs_code": row.get("bs_code", ""), "stage": "base"})

                for r in rows:
                    base_rows.append(r)
                    all_dates.add(r["date"])

            except Exception as e:
                kline_fail += 1
                failed_symbols.append({"code": row.get("代码", ""), "name": row.get("名称", ""), "bs_code": row.get("bs_code", ""), "stage": "base_exception", "error": str(e)[:200]})
                print(f"基础处理失败: {row.get('代码', '')} {row.get('名称', '')} {e}")

            if should_abort_for_data_source(processed_count, kline_success, kline_fail):
                break

        # V11.4：若数据源中途断流导致样本不足，先暂停重登并补拉部分失败股票，避免偶发网络问题直接废掉整次运行。
        if (
            RETRY_FAILED_KLINE_AFTER_SCAN == "1"
            and kline_success < MIN_VALID_KLINE
            and failed_symbols
            and MAX_STOCKS == 0
            and time.time() - start_ts < max(60, BASIC_RUNTIME_SECONDS - 600)
        ):
            retry_limit = min(RETRY_FAILED_KLINE_LIMIT, len(failed_symbols))
            print(f"样本不足，启动失败K线补拉：retry_limit={retry_limit}，当前成功={kline_success}，失败={kline_fail}")
            time.sleep(max(5, min(BROKEN_PIPE_PAUSE_SECONDS, 120)))
            if BAOSTOCK_RELOGIN_ON_BROKEN_PIPE == "1":
                baostock_relogin("retry_failed_kline_after_scan")
            retried_bs_codes = set()
            retry_success = 0
            retry_rows_added = 0
            for item in failed_symbols[:retry_limit]:
                bs_code_retry = item.get("bs_code", "")
                if not bs_code_retry or bs_code_retry in retried_bs_codes:
                    continue
                retried_bs_codes.add(bs_code_retry)
                matched = stock_list[stock_list["bs_code"] == bs_code_retry]
                if matched.empty:
                    continue
                try:
                    rows_retry = process_stock_base(matched.iloc[0])
                    if rows_retry:
                        retry_success += 1
                        kline_success += 1
                        kline_fail = max(0, kline_fail - 1)
                        for rr in rows_retry:
                            base_rows.append(rr)
                            all_dates.add(rr["date"])
                            retry_rows_added += 1
                    if retry_success >= MIN_VALID_KLINE:
                        break
                    if retry_success > 0 and retry_success % 100 == 0:
                        print(f"失败K线补拉进度：成功补拉{retry_success}只，新增基础记录{retry_rows_added}条")
                except Exception as e:
                    if retry_success <= 5:
                        print(f"失败K线补拉仍失败: {bs_code_retry} {e}")
            print(f"失败K线补拉完成：成功补拉{retry_success}只，新增基础记录{retry_rows_added}条，当前K线成功={kline_success}，失败={kline_fail}")

        dates = sorted(list(all_dates), reverse=True)[:CHECK_DAYS]

        progress_line("基础评分完成", processed_count, len(stock_list), start_ts, kline_success, kline_fail)
        coverage_rate = kline_success / max(1, kline_success + kline_fail)
        print(f"{summarize_kline_source_stats()}，覆盖率={coverage_rate:.1%}")
        if failed_symbols:
            save_json_file(FAILED_KLINE_FILE, {"generated_at_bj": bj_time_str(), "failed": failed_symbols})
            print(f"K线失败清单已保存：{FAILED_KLINE_FILE}")

        if kline_success < MIN_VALID_KLINE and MAX_STOCKS == 0:
            warning = (
                f"样本不足：本次K线成功仅{kline_success}只，低于最低有效样本{MIN_VALID_KLINE}只。"
                f"为避免小样本误判，本次不推送正式选股结果。\n"
                f"{summarize_kline_source_stats()}"
            )
            print(warning)
            send_telegram(build_error_message(warning))
            return

        if not base_rows:
            msg = build_message([], dates, len(stock_list), kline_success, kline_fail, 0)
            send_telegram(msg)
            return

        base_rows = sorted(
            base_rows,
            key=lambda x: (
                x["date"],
                safe_float(x.get("base_bucket_rank_score", x.get("base_score", 0))),
                safe_float(x.get("base_total_score", x.get("base_score", 0))),
                x["score"],
            ),
            reverse=True
        )

        deep_targets, base_bucket_stats = select_deep_targets_v10(base_rows, DEEP_SCORE_LIMIT)

        print(f"基础评分完成：{len(base_rows)}条")
        print(f"V11基础分桶后进入深度评分：{len(deep_targets)}条")
        print("V11基础候选分桶统计：")
        for _bucket, _st in base_bucket_stats.items():
            print(f"  {_bucket}: 可用{_st.get('available', 0)} | 配额{_st.get('quota', 0)} | 入选{_st.get('selected', 0)}")

        deep_rows = []
        deep_success = 0
        deep_fail = 0
        deep_skip = 0
        deep_start_ts = time.time()

        for idx, r in enumerate(deep_targets, 1):
            if idx % max(1, PROGRESS_INTERVAL // 2) == 0:
                progress_line("深度评分", idx, len(deep_targets), deep_start_ts, len(deep_rows), 0)

            if time.time() - deep_start_ts > DEEP_RUNTIME_SECONDS:
                print("达到深度评分阶段最大运行时间，停止深度评分，使用已有深度评分结果。")
                break

            if time.time() - start_ts > MAX_RUNTIME_SECONDS:
                print("达到总最大运行时间，停止深度评分，使用已有深度评分结果。")
                break

            try:
                rows = process_stock_deep(r)
                if rows:
                    deep_success += 1
                else:
                    deep_skip += 1

                for rr in rows:
                    deep_rows.append(rr)

            except Exception as e:
                deep_fail += 1
                print(f"深度处理失败: {r.get('code', '')} {r.get('name', '')} {e}")

        deep_rows = sorted(
            deep_rows,
            key=lambda x: (
                x["date"],
                safe_float(x.get("trade_priority_score", 0)),
                safe_float(x.get("score_trade_quality", 0)),
                x["total_score"],
                x["base_score"],
                x["score"],
            ),
            reverse=True
        )

        if deep_targets and not deep_rows:
            warning = (
                "深度评分结果为0，本次不是正常无票，而是深度评分阶段未有效完成。"
                f"基础评分候选{len(base_rows)}条，深度目标{len(deep_targets)}条。"
                "为避免误判，本次不推送正式选股结果。"
            )
            print(warning)
            send_telegram(build_error_message(warning))
            return

        final_signals = []
        strong_watch_pool = []

        for r in deep_rows:
            if r.get("risk_hard_exclude", False):
                continue
            if float(r.get("total_score", 0)) < FINAL_SCORE_THRESHOLD:
                continue

            pool = str(r.get("candidate_pool", "优先候选池"))
            if pool != "优先候选池":
                if SAVE_STRONG_WATCH_POOL == "1":
                    strong_watch_pool.append(r)
                if ONLY_PUSH_PRIORITY_POOL == "1":
                    continue

            key = f"{r['date']}_{r['code']}"

            if key not in history:
                final_signals.append(r)
                history[key] = {
                    "date": r["date"],
                    "code": r["code"],
                    "name": r["name"],
                    "score": r["score"],
                    "base_score": r["base_score"],
                    "total_score": r["total_score"],
                    "candidate_pool": pool,
                }

            if len(final_signals) >= RESULT_LIMIT:
                break

        print(f"近{CHECK_DAYS}个交易日排查完成：{dates}（默认仅最新有行情日；可用CHECK_DAYS调整）")
        print(f"K线成功：{kline_success} 只 | K线失败：{kline_fail} 只")
        print(f"基础评分数量：{len(base_rows)} 条")
        print(f"深度评分数量：{len(deep_rows)} 条 | 输入：{len(deep_targets)} | 成功：{deep_success} | 失败：{deep_fail} | 跳过：{deep_skip} | 有效样本：{len(deep_rows)}")
        print(f"最终推送数量：{len(final_signals)} 只")
        print(f"强势观察池数量：{len(strong_watch_pool)} 只（默认不推送，只保存候选JSON）")

        save_candidates_payload(base_rows, deep_rows, final_signals, strong_watch_pool)
        save_signal_history(history)

        msg = build_message(
            final_signals,
            dates,
            stock_count=len(stock_list),
            kline_success=kline_success,
            kline_fail=kline_fail,
            deep_count=len(deep_rows)
        )

        send_telegram(msg)

        print("全部完成!")

    except Exception as e:
        print(f"主流程失败：{e}")
        send_telegram(build_error_message(e))

    finally:
        baostock_logout()


if __name__ == "__main__":
    main()
