# Employee1 Stock Alert V26.1 | Updated: 2026-05-19
# FIRST LINE FORMAT LOCKED: only version number and Updated date may change; do not rename entrypoint stock_alert.py
# V19.3.3 AUDITED HOTFIX - base observation subscores + deep score 200 + static audit passed
import os
import json
import time
import html
import warnings
import signal
import io
import argparse
from contextlib import contextmanager, redirect_stdout, redirect_stderr
from datetime import datetime, timedelta

try:
    import baostock as bs
except Exception:
    bs = None


def fmt_seconds(sec):
    """把秒数格式化成人能看懂的耗时，供进度日志/深度评分watchdog使用。"""
    try:
        sec = int(max(float(sec), 0))
    except Exception:
        sec = 0
    h = sec // 3600
    m = (sec % 3600) // 60
    ss = sec % 60
    if h > 0:
        return f"{h}小时{m}分{ss}秒"
    if m > 0:
        return f"{m}分{ss}秒"
    return f"{ss}秒"
import pandas as pd
import numpy as np
import requests

# Telegram发送函数兜底引用，防止异常处理阶段 NameError。
_ORIGINAL_SEND_TELEGRAM = None
# V25.3 HOTFIX：图片待发送队列必须全局初始化，否则异常消息阶段会触发 NameError。
TELEGRAM_PENDING_IMAGES = []
# V25.3 数据审计：记录每只股票本次读到/拉到的K线最新日期。
KLINE_DATE_AUDIT = []


try:
    import akshare as ak
except Exception:
    ak = None

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

# ========================= V11.5 股票池获取超时/备用通道增量优化说明 =========================
# 1）针对“抓取A股列表/使用股票池日期”阶段卡住的问题，新增股票池获取超时保护；
# 2）BaoStock query_all_stock 超时或失败时自动重试、重登；
# 3）BaoStock 股票列表持续异常时，自动切换 AkShare A股实时列表作为备用股票池；
# 4）股票池阶段不再无限等待，失败会发送诊断或走备用通道，避免 GitHub Actions 空转数十分钟；
# 5）不改变原有评分、推送、V11.4 K线稳定性保护和候选输出逻辑。
# ===========================================================================


# ========================= V12 统一多周期关键结构/舒服买点/活跃度/硬排雷说明 =========================
# 1）正式推送从“突破当天”改为“突破后回踩确认”：弱突破和强突破都先入后台跟踪池，
#    只有回踩 BBIBOLL/BBI、MA5/MA10、突破大阳线实体中位/实底、结构关键位并出现承接时才正式推送；
# 2）重构交易优先级：不能只看离防守位近和风险收益比，必须先判断关键位动作是否坚决、是否已有回踩承接；
# 3）新增100日活跃度/松散度模块：100日涨停次数、大阳/大阴、跳空、振幅弹性、K线黏密度共同评分；
# 4）报告改为交易语言：为什么能看、为什么不能追、等什么买点、什么情况放弃；
# 5）强化财务/审计雷区：审计报告保留意见、无法表示、否定意见、非标审计等一律硬剔除或重大扣分；
# 6）V12统一多周期关键结构位：凹口/平台/最大量阳K实底/最大量阳K高点/箱体边界，
#    不再按日线、周线、月线、季线重复打分，而是先统一识别关键位，再做突破质量、回踩承接和同源封顶；
# 7）最大量K必须是有效阳K：阳线、有实体、实体长度不小于上下影线总和的一半，才能取实底线/高点作为关键箱体边界；
# 8）回踩确认按“回踩段”动态识别，不写死1个月/2个月/3个月；第一次、第二次多为后台记录，第三次及以后成熟回踩段叠加日线转强才更适合正式推送。
# ===========================================================================

# ========================= V12.1 机构级框架重构/同源合并说明 =========================
# 本版不是删减交易逻辑，而是把前面讨论过的有效逻辑重新放进更清晰的机构级分层框架：
# 1）风险硬过滤层：先处理审计/财务/监管雷区，重大风险不再浪费深度算力；
# 2）共享特征层：均线、量能、涨停、活跃度、长周期位置只算一次，多模块复用；
# 3）多周期结构种子层：日/周/月/季的凹口、平台、箱体、最大量阳K实底/高点统一生成关键位；
# 4）突破质量层：所有关键位突破统一判断实体质量、跳空、收盘位置、上影线、量能健康度；
# 5）回踩承接层：所有BBIBOLL/BBI/均线/强阳实体/涨停实体/大量阳K实底回踩统一判断承接质量；
# 6）量能确认层：标准倍量、倍量后平量、分散健康倍量、阳梯量、极端爆量统一归类，避免重复加分；
# 7）活跃度弹性层：100日涨停、大阳/大阴、跳空、ATR、K线黏密度统一输出活跃度等级；
# 8）同源信号合并层：结构类、量能类、承接类、活跃度类、风险类分组封顶，只取最强项+少量共振；
# 9）交易决策层：大周期负责是否值得跟踪，日线回踩承接负责是否正式推送，最终只推前5只。
# ===========================================================================


# ========================= V12.6 标准提速版：多周期时间窗口/重心平量/充分率模型说明 =========================
# 本版不是删减交易逻辑，而是在V12.4“风险先行、基础轻扫、候选深算、同源合并”的计算路径上，
# 新增并体系化三类专业模型：
# 1）爆发前夜时间窗口模型：时间对称/倍数周期、平台蓄势长度、关键位贴近、量能从乱到稳、波动率收缩、日线触发；
# 2）启动前平量压缩模型：不是只看当前量平，而是比较“前段量能混乱”与“平台末端量能稳定”，识别分歧降低/抛压衰竭；
# 3）台阶平台量能均值抬升模型：价格平台抬高、平台均量抬高、平台内平量比例高、守启动柱中位/实底、再放量突破。
# 新模型只在深度候选/种子票运行，不进入4937只全市场基础轻扫，避免重复筛查；评分归入时间/承接/量能同源组并封顶。
# ===========================================================================

# ========================= V14 原主模型完整底座 + 增量体系重构说明 =========================
# 本版不是重写模型，而是在V12.6完整底座上做后置增量：
# 1）保留原有倍量、倍量后平量、分散健康倍量、凹口、平台、破底翻、BBI/BOLL中轨修复、
#    近区精准线、缺口、阳包阴、双阳夹阴、三阴战法、台阶推进、右侧平台均量、多周期最大阳量K、
#    日线不追高、财务硬雷区、防守位/RR等所有已确认逻辑；
# 2）新增阳包阴精细评分：按跳空越过前阴开盘、实体内高开收复、低/平开完全反包、仅修复中位等分档，
#    同时评估上下影线、收盘位置、量能比和结构位置；
# 3）新增V14后置分项表：原深度总分为主，V14只做风险扣分、追高校准、量能确认、可操作性校准和解释；
# 4）最终三选不再被“优先池/80分/买点未到封顶”机械杀光，财务硬雷区仍一票否决，普通缺点只扣分。
# ===========================================================================


# ========================= V15 选股模型多周期供需压力带突破模型说明 =========================
# 本版继续遵守“原主模型完整底座 + 手术式增量优化”：不删除V12/V14任何有效逻辑。
# 新增 Multi-Timeframe Supply-Demand Zone Engine：
# 1）用百分比/对数价格桶构建日/周/月/季/年 Volume Profile，识别HVN/POC/价值区；
# 2）把上影线共振、下影线共振、跳空缺口共振、假突破记忆、凹口/平台/峰值/次高/次低、AVWAP成本共振
#    作为结构反应与边界校准，不再机械用单点画线；
# 3）各周期独立生成压力密集区后，投影到统一百分比价格桶，寻找多周期重叠最密集供需压力带；
# 4）同时保留各周期压力区并集后的最终压力上沿。S级必须同一根日K同时突破核心重叠压力带与最终上沿；
# 5）压力带本身分级、突破日K分级、模型分级三层矩阵融合；A/S才可作为选股模型模型正式候选资格，B/C/D最多观察。
# ===========================================================================

# ========================= V9.1 追高闸门增量优化说明 =========================
# 本文件基于用户提供的V8/V9源码继续做“手术式增量优化”，原则是：
# 1）BaoStock数据源、主流程、Telegram变量、缓存、基础评分底座不动；
# 2）原有结构、月线、台阶、频次、雷区等模块不删除，只对追高风险做硬约束；
# 3）新增：追高风险闸门、强攻票综合分封顶、优先候选池/强势观察池分流、
#    涨停/大阳阶段标签修正、近端压力硬约束、极端放量/高乖离/过热组合封顶。
# ===========================================================================

# ============================================================
# PROTECTED CREDENTIAL / PAT AREA - DO NOT MODIFY / FORMAT / REPLACE
# PAT字段与外部推送密钥保护区：禁止修改、清空、格式化、替换。
# 说明：如workflow或环境变量中存在 GitHub PAT / Telegram Token，
#      本代码只读取环境变量，绝不在重构中改写字段名或默认值。
# ============================================================
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", os.environ.get("TELEGRAM_BOT_TOKEN", ""))
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
ENABLE_TELEGRAM = os.environ.get("ENABLE_TELEGRAM", "0")
# ============================================================
# END PROTECTED CREDENTIAL / PAT AREA
# ============================================================

SIGNAL_FILE = "signals_history.json"
CANDIDATE_FILE = "stock_candidates.json"
CACHE_DIR = "kline_cache"
MODEL_VERSION = "V26.0一号员工选股模型｜爆发前夜最终买入池+机构评分卡+动态仓位自学习版"
SEED_POOL_FILE = os.environ.get("SEED_POOL_FILE", "stock_seed_pool.json")


N = 20
CHECK_DAYS = int(os.environ.get("CHECK_DAYS", "1"))  # V11.1：默认只扫描最新有行情交易日；如需回看可在workflow设置为3

MAX_STOCKS = int(os.environ.get("MAX_STOCKS", "0"))
RESULT_LIMIT_RAW = int(os.environ.get("RESULT_LIMIT", "20"))
# V12：一号员工正式报告默认只推前5只；后台候选池/跟踪池仍保留更多记录。
TOP_PUSH_LIMIT = int(os.environ.get("TOP_PUSH_LIMIT", "5"))
RESULT_LIMIT = min(RESULT_LIMIT_RAW, TOP_PUSH_LIMIT) if TOP_PUSH_LIMIT > 0 else RESULT_LIMIT_RAW
DEEP_SCORE_LIMIT_RAW = int(os.environ.get("DEEP_SCORE_LIMIT", "500"))
# V19.2：深度评分硬上限默认200。V19最终Top3需要更宽候选池，但仍控制运行时间；如需扩大可设置 DEEP_SCORE_HARD_CAP=250/300。
DEEP_SCORE_HARD_CAP = int(os.environ.get("DEEP_SCORE_HARD_CAP", "200"))
DEEP_SCORE_LIMIT = min(DEEP_SCORE_LIMIT_RAW, DEEP_SCORE_HARD_CAP) if DEEP_SCORE_HARD_CAP > 0 else DEEP_SCORE_LIMIT_RAW

# V23.3-OPT：爆发前夜通道只承担“基础召回/入池增强”，避免压缩票未触发就过度抬高总分。
BASE_EXPLOSION_EVE_TOTAL_WEIGHT = float(os.environ.get("BASE_EXPLOSION_EVE_TOTAL_WEIGHT", "0.42"))
BASE_EXPLOSION_EVE_RANK_WEIGHT = float(os.environ.get("BASE_EXPLOSION_EVE_RANK_WEIGHT", "0.10"))
BASE_EXPLOSION_EVE_BUCKET_BONUS = float(os.environ.get("BASE_EXPLOSION_EVE_BUCKET_BONUS", "3"))

# V24：大周期供应吸收后供需压力带突破模型。基础层只做召回增强，深度层再做确认与风险反证。
BASE_SUPPLY_ABSORB_TOTAL_WEIGHT = float(os.environ.get("BASE_SUPPLY_ABSORB_TOTAL_WEIGHT", "0.38"))
BASE_SUPPLY_ABSORB_RANK_WEIGHT = float(os.environ.get("BASE_SUPPLY_ABSORB_RANK_WEIGHT", "0.12"))
BASE_SUPPLY_ABSORB_BUCKET_BONUS = float(os.environ.get("BASE_SUPPLY_ABSORB_BUCKET_BONUS", "3"))
DEEP_SUPPLY_ABSORB_SCORE_WEIGHT = float(os.environ.get("DEEP_SUPPLY_ABSORB_SCORE_WEIGHT", "0.55"))
DEEP_SUPPLY_ABSORB_PRIORITY_WEIGHT = float(os.environ.get("DEEP_SUPPLY_ABSORB_PRIORITY_WEIGHT", "0.25"))
BASE_ACTIVITY_TOTAL_WEIGHT = float(os.environ.get("BASE_ACTIVITY_TOTAL_WEIGHT", "0.45"))
BASE_ACTIVITY_RANK_WEIGHT = float(os.environ.get("BASE_ACTIVITY_RANK_WEIGHT", "0.30"))

# V12.4：标准版默认启用“先过滤、后深算”。这不是减少逻辑，而是避免对明显无效股票重复跑昂贵模型。
ENABLE_V122_BASE_GATE = os.environ.get("ENABLE_V122_BASE_GATE", "1")
ENABLE_RISK_EARLY_EXIT = os.environ.get("ENABLE_RISK_EARLY_EXIT", "1")
V122_MIN_BASE_SCORE_FOR_DEEP = float(os.environ.get("V122_MIN_BASE_SCORE_FOR_DEEP", "42"))
V122_STRONG_SEED_SCORE = float(os.environ.get("V122_STRONG_SEED_SCORE", "10"))
V122_STRONG_TRIGGER_SCORE = float(os.environ.get("V122_STRONG_TRIGGER_SCORE", "12"))

REQUEST_SLEEP = float(os.environ.get("REQUEST_SLEEP", "0.02"))
# V9：为月线BBI/BOLL缩口与多次中轨修复提供足够样本；默认约6年，仍可通过环境变量调小以节省时间。
# V12.4：基础/深度分层取数。全市场基础轻扫只取较短日线，候选深算再补长周期，避免4937只全量拉2200天。
BASE_KLINE_LOOKBACK_DAYS = int(os.environ.get("BASE_KLINE_LOOKBACK_DAYS", "520"))
DEEP_KLINE_LOOKBACK_DAYS = int(os.environ.get("DEEP_KLINE_LOOKBACK_DAYS", os.environ.get("KLINE_LOOKBACK_DAYS", "2200")))
KLINE_LOOKBACK_DAYS = DEEP_KLINE_LOOKBACK_DAYS
MONTHLY_STRUCT_LOOKBACK_MONTHS = int(os.environ.get("MONTHLY_STRUCT_LOOKBACK_MONTHS", "100"))
SEED_POOL_MAX_STOCKS = int(os.environ.get("SEED_POOL_MAX_STOCKS", "300"))
SEED_POOL_DAILY_CHECK_LIMIT = int(os.environ.get("SEED_POOL_DAILY_CHECK_LIMIT", "120"))
ENABLE_V124_ACCELERATED_PIPELINE = os.environ.get("ENABLE_V124_ACCELERATED_PIPELINE", "1")
MAX_RUNTIME_SECONDS = int(os.environ.get("MAX_RUNTIME_SECONDS", "5400"))
# 分阶段限时：避免基础评分耗尽全部时间后，深度评分被误杀。
BASIC_RUNTIME_SECONDS = int(os.environ.get("BASIC_RUNTIME_SECONDS", str(max(1800, MAX_RUNTIME_SECONDS - 3600))))
DEEP_RUNTIME_SECONDS = int(os.environ.get("DEEP_RUNTIME_SECONDS", "3600"))
MIN_VALID_KLINE = int(os.environ.get("MIN_VALID_KLINE", "1000"))
PROGRESS_INTERVAL = int(os.environ.get("PROGRESS_INTERVAL", "100"))
SINGLE_STOCK_TIMEOUT_SECONDS = int(os.environ.get("SINGLE_STOCK_TIMEOUT_SECONDS", "12"))
# 深度评分单票总超时：防止某个深度模块/个股卡死整个流程。0表示关闭。
DEEP_SINGLE_STOCK_TIMEOUT_SECONDS = int(os.environ.get("DEEP_SINGLE_STOCK_TIMEOUT_SECONDS", "90"))

# V11.4：数据源稳定性保护。公开行情源偶发 Broken pipe / 接收异常时，先暂停重登，再补拉失败样本。
KLINE_MAX_RETRIES = int(os.environ.get("KLINE_MAX_RETRIES", "2"))
BROKEN_PIPE_PAUSE_THRESHOLD = int(os.environ.get("BROKEN_PIPE_PAUSE_THRESHOLD", "8"))
BROKEN_PIPE_PAUSE_SECONDS = int(os.environ.get("BROKEN_PIPE_PAUSE_SECONDS", "35"))
BAOSTOCK_RELOGIN_ON_BROKEN_PIPE = os.environ.get("BAOSTOCK_RELOGIN_ON_BROKEN_PIPE", "1")
SUPPRESS_BAOSTOCK_VERBOSE = os.environ.get("SUPPRESS_BAOSTOCK_VERBOSE", "1")
DATA_SOURCE_FAIL_FAST_AFTER = int(os.environ.get("DATA_SOURCE_FAIL_FAST_AFTER", "450"))
DATA_SOURCE_MIN_SUCCESS_RATE = float(os.environ.get("DATA_SOURCE_MIN_SUCCESS_RATE", "0.20"))
DATA_SOURCE_CONSECUTIVE_FAIL_LIMIT = int(os.environ.get("DATA_SOURCE_CONSECUTIVE_FAIL_LIMIT", "120"))
RETRY_FAILED_KLINE_AFTER_SCAN = os.environ.get("RETRY_FAILED_KLINE_AFTER_SCAN", "1")
RETRY_FAILED_KLINE_LIMIT = int(os.environ.get("RETRY_FAILED_KLINE_LIMIT", "1800"))
MIN_FORMAL_COVERAGE_RATE = float(os.environ.get("MIN_FORMAL_COVERAGE_RATE", "0.80"))

# V12.7：稳定提速补丁。评分逻辑不变，只增强数据获取层。
# 1）BaoStock失败时可切换AkShare补拉；2）远程失败时允许使用旧缓存；
# 3）连续失败时更快熔断，避免成功数卡死后继续空跑几千只。
KLINE_FALLBACK_AKSHARE = os.environ.get("KLINE_FALLBACK_AKSHARE", "0")
ALLOW_STALE_KLINE_CACHE = os.environ.get("ALLOW_STALE_KLINE_CACHE", "1")
STALE_CACHE_MAX_DAYS = int(os.environ.get("STALE_CACHE_MAX_DAYS", "10"))
AKSHARE_FALLBACK_MAX_RETRIES = int(os.environ.get("AKSHARE_FALLBACK_MAX_RETRIES", "2"))
AKSHARE_FALLBACK_SLEEP = float(os.environ.get("AKSHARE_FALLBACK_SLEEP", "0.18"))
DATA_SOURCE_RECOVERY_PAUSE_SECONDS = int(os.environ.get("DATA_SOURCE_RECOVERY_PAUSE_SECONDS", "45"))
BASE_EMPTY_CACHE_MIN_ROWS = int(os.environ.get("BASE_EMPTY_CACHE_MIN_ROWS", "120"))
DEEP_EMPTY_CACHE_MIN_ROWS = int(os.environ.get("DEEP_EMPTY_CACHE_MIN_ROWS", "500"))

# ========================= 全历史缓存只读模式 / 每日18点前推送专用配置 =========================
# 说明：不改V16评分模型，只把数据入口改为优先读取全量缓存 kline_cache/000001.csv。
USE_FULL_HISTORY_CACHE = os.environ.get("USE_FULL_HISTORY_CACHE", "1")
FULL_HISTORY_CACHE_DIR = os.environ.get("FULL_HISTORY_CACHE_DIR", CACHE_DIR)
MODEL_UNIVERSE_FILE = os.environ.get("MODEL_UNIVERSE_FILE", "")
FULL_CACHE_MAX_STALE_DAYS = int(os.environ.get("FULL_CACHE_MAX_STALE_DAYS", "7"))
FULL_CACHE_BASE_TAIL_ROWS = int(os.environ.get("FULL_CACHE_BASE_TAIL_ROWS", "760"))
FULL_CACHE_DEEP_USE_ALL = os.environ.get("FULL_CACHE_DEEP_USE_ALL", "1")
FULL_CACHE_MIN_ROWS_BASE = int(os.environ.get("FULL_CACHE_MIN_ROWS_BASE", "120"))
FULL_CACHE_MIN_ROWS_DEEP = int(os.environ.get("FULL_CACHE_MIN_ROWS_DEEP", "250"))
EXCLUDE_MODEL_CODES = set(
    x.strip().zfill(6) for x in os.environ.get("EXCLUDE_MODEL_CODES", "600415,603407").split(",") if x.strip()
)

# 每日数据闸门信息，由 workflow 从 daily_kline_update_state.json 传入，写入日志和Telegram报告。
DATA_GATE_TARGET_DATE = os.environ.get("DATA_GATE_TARGET_DATE", "").strip()
DATA_GATE_COVERAGE = os.environ.get("DATA_GATE_COVERAGE", "").strip()
DATA_GATE_MODEL_COUNT = os.environ.get("DATA_GATE_MODEL_COUNT", "").strip()
DATA_GATE_CACHE_COUNT = os.environ.get("DATA_GATE_CACHE_COUNT", "").strip()
DATA_GATE_REASON = os.environ.get("DATA_GATE_REASON", "").strip()
DATA_GATE_STALE_COUNT = os.environ.get("DATA_GATE_STALE_COUNT", "").strip()
DATA_GATE_FAILED_COUNT = os.environ.get("DATA_GATE_FAILED_COUNT", "").strip()

# V11.5：股票池获取保护。BaoStock 股票列表阶段也可能卡住，必须有超时、重试、备用 AkShare 通道。
STOCK_LIST_QUERY_TIMEOUT_SECONDS = int(os.environ.get("STOCK_LIST_QUERY_TIMEOUT_SECONDS", "120"))
STOCK_LIST_MAX_RETRIES = int(os.environ.get("STOCK_LIST_MAX_RETRIES", "2"))
STOCK_LIST_FALLBACK_AKSHARE = os.environ.get("STOCK_LIST_FALLBACK_AKSHARE", "0")
STOCK_LIST_RELOGIN_ON_FAIL = os.environ.get("STOCK_LIST_RELOGIN_ON_FAIL", "1")

SCORE_LIMIT = 75
# 最终推送阈值：新评分体系下，80分以下不再推送；基础初筛仍沿用原SCORE_LIMIT，不改原模型。
FINAL_SCORE_THRESHOLD = float(os.environ.get("FINAL_SCORE_THRESHOLD", "0"))
# V9.1：是否只推送“优先候选池”。默认1，避免一号员工把短线强攻/涨停追高票混入正式候选。
ONLY_PUSH_PRIORITY_POOL = os.environ.get("ONLY_PUSH_PRIORITY_POOL", "0")
# V9.1：强势观察池不作为正式推送，可在候选JSON中保留，供三号员工或人工复盘。
SAVE_STRONG_WATCH_POOL = os.environ.get("SAVE_STRONG_WATCH_POOL", "1")

# ========================= V14：原主模型完整底座 + 增量分层三选 =========================
# 最高原则：不推翻V12.6主模型、不删除任何已讨论有效逻辑，只做增量细化、同源合并、权重校准、
# 后置风控审核、报告打分表和最终三选优化。
# V14正式三选：先剔除财务/审计/监管硬雷区，再在剩余深度候选中按“原深度总分为主、V14校准为辅”
# 尽可能选出相对最优3只。普通缺点（右侧量能不足、买点不完美、日线略高）做扣分/降级，不再一刀切杀光。
V14_ENABLE_FINAL_RESCUE = os.environ.get("V14_ENABLE_FINAL_RESCUE", "1")
V14_TARGET_PUSH_COUNT = int(os.environ.get("V14_TARGET_PUSH_COUNT", str(RESULT_LIMIT)))
V14_MIN_ABSOLUTE_SCORE = float(os.environ.get("V14_MIN_ABSOLUTE_SCORE", "0"))
V14_PREFERRED_SCORE = float(os.environ.get("V14_PREFERRED_SCORE", "0"))
V14_IGNORE_HISTORY_FOR_RERUN = os.environ.get("V14_IGNORE_HISTORY_FOR_RERUN", "1")
V14_BLOCK_SEVERE_NO_DEFENSE = os.environ.get("V14_BLOCK_SEVERE_NO_DEFENSE", "0")

# ========================= V19.1：每日固定Top3 + 候选池归因底座 =========================
# 说明：不再用80分作为正式推荐硬门槛。深度候选完成V16/V14审计后，
# 从非硬雷区/非硬失败候选中按风险调整后综合排序固定输出Top3。
# 压力带突破、倍量、回踩确认、空头钝化、时间窗口等均为评分项，不作为单独必要条件。
V19_ENABLE_TOP3_FIXED = os.environ.get("V19_ENABLE_TOP3_FIXED", "1")
V19_FIXED_TOP_N = int(os.environ.get("FORCE_TOP_N", os.environ.get("MIN_PUSH_COUNT", os.environ.get("TOP_PUSH_LIMIT", "5"))))
V19_SCORE_CARDS_FILE = os.environ.get("V19_SCORE_CARDS_FILE", "v19_4_score_cards.json")
V19_DAILY_REPORT_FILE = os.environ.get("V19_DAILY_REPORT_FILE", "v19_4_daily_report.txt")
V19_REVIEW_REPORT_FILE = os.environ.get("V19_REVIEW_REPORT_FILE", "v19_4_review_report.txt")


# ========================= V20.1：精简七层评分 + 条件概率反馈闭环配置 =========================
# 说明：V20.1 不推翻 V19.4.1 / V20，而是在原底座上做“手术式增量优化”：
# 1）把零散K线形态合并为“触发质量分”，把零散量能指标合并为“资金行为分”；
# 2）最终使用七层评分：风险过滤、结构位置、压力支撑、资金行为、触发确认、交易质量、反馈校准；
# 3）A档门槛收紧：压力带D不能A；压力带C必须有强承接/强大周期修复；买点未触发不A；
# 4）新增“日线小平台低量精准触发线”字段与报告口径：只作为短线触发线，不替代大周期最终压力上沿；
# 5）保留固定Top3输出，但允许Top3里出现B/B+/C，并明确“观察/不交易”，避免固定输出被误解为每天必须买。
V20_ENABLE_TIERED_OUTPUT = os.environ.get("V20_ENABLE_TIERED_OUTPUT", "1")
V20_ENABLE_CONDITION_FEEDBACK = os.environ.get("V20_ENABLE_CONDITION_FEEDBACK", "1")
V20_FIXED_TOP_N = int(os.environ.get("V20_FIXED_TOP_N", os.environ.get("FORCE_TOP_N", os.environ.get("MIN_PUSH_COUNT", os.environ.get("TOP_PUSH_LIMIT", "5")))))
V20_SCORE_CARDS_FILE = os.environ.get("V20_SCORE_CARDS_FILE", "v20_3_1_score_cards.json")
V20_DAILY_REPORT_FILE = os.environ.get("V20_DAILY_REPORT_FILE", "v20_3_1_daily_report.txt")
V20_REVIEW_REPORT_FILE = os.environ.get("V20_REVIEW_REPORT_FILE", "v20_3_1_review_report.txt")
V20_CONDITION_TABLE_FILE = os.environ.get("V20_CONDITION_TABLE_FILE", "v20_3_1_condition_probability_table.json")
V20_SIGNAL_FEEDBACK_CSV = os.environ.get("V20_SIGNAL_FEEDBACK_CSV", "v20_3_1_signal_feedback_stats.csv")
V20_MODEL_AUDIT_LOG_FILE = os.environ.get("V20_MODEL_AUDIT_LOG_FILE", "v20_3_1_model_audit_log.json")
V20_SIGNAL_LIFECYCLE_FILE = os.environ.get("V20_SIGNAL_LIFECYCLE_FILE", "v20_3_1_signal_lifecycle.json")
V20_ENABLE_SIGNAL_LIFECYCLE = os.environ.get("V20_ENABLE_SIGNAL_LIFECYCLE", "1")
V20_LIFECYCLE_LOOKBACK_DAYS = int(os.environ.get("V20_LIFECYCLE_LOOKBACK_DAYS", "25"))
V20_LIFECYCLE_MAX_ITEMS = int(os.environ.get("V20_LIFECYCLE_MAX_ITEMS", "60"))

# ========================= V26.0：爆发前夜最终买入池 + 机构评分卡 =========================
# 最高原则：不推翻旧底座；旧模型继续负责提取特征，V26负责最终评分出口、同源去重、定价、环境、仓位和复盘字段。
# Top5是最终买入池，不是观察池；允许少于5只，宁缺毋滥。
V26_ENABLE_INSTITUTIONAL_SCORECARD = os.environ.get("V26_ENABLE_INSTITUTIONAL_SCORECARD", "1")
V26_MIN_BUY_SCORE = float(os.environ.get("V26_MIN_BUY_SCORE", "80"))
V26_STRONG_CONFIRM_SCORE = float(os.environ.get("V26_STRONG_CONFIRM_SCORE", "82"))
V26_STANDARD_POSITION_SCORE = float(os.environ.get("V26_STANDARD_POSITION_SCORE", "88"))
V26_ALLOW_EMPTY_TOP5 = os.environ.get("V26_ALLOW_EMPTY_TOP5", "1")
V26_MAX_INDUSTRY_EXPOSURE = float(os.environ.get("V26_MAX_INDUSTRY_EXPOSURE", "0.40"))
V26_MAX_SAME_HYPOTHESIS_EXPOSURE = float(os.environ.get("V26_MAX_SAME_HYPOTHESIS_EXPOSURE", "0.60"))
V26_MIN_RR = float(os.environ.get("V26_MIN_RR", "1.35"))
V26_IDEAL_RR = float(os.environ.get("V26_IDEAL_RR", "2.00"))
V26_MAX_DEFENSE_DIST = float(os.environ.get("V26_MAX_DEFENSE_DIST", "0.105"))
V26_MAX_NEAR_PRESSURE = float(os.environ.get("V26_MAX_NEAR_PRESSURE", "0.045"))
V26_FAILURE_RISK_BLOCK = float(os.environ.get("V26_FAILURE_RISK_BLOCK", "7.0"))
V26_SIGNAL_MAX_AGE_DAYS = int(os.environ.get("V26_SIGNAL_MAX_AGE_DAYS", "13"))
V26_ENABLE_PORTFOLIO_DECORR = os.environ.get("V26_ENABLE_PORTFOLIO_DECORR", "1")
# V26.2.1：只允许最终报告阶段发送Telegram；运行中诊断/空样本/异常分支只写日志和artifact，避免中途误推送。
SUPPRESS_MIDRUN_TELEGRAM = os.environ.get("SUPPRESS_MIDRUN_TELEGRAM", "1")
# ===========================================================================


# ========================= V20.3 基础筛选重构 + 动态风险指标库 =========================
# 基础层重召回、深度层重精度、最终层重交易。
# V20.3 不删除V20.2任何结构逻辑，只把基础筛选从“单一总分前200”升级为：
# 多通道召回 + 风险前置过滤 + 种子/近期推荐强制跟踪 + 入围原因可审计。
V203_ENABLE_BASE_RECALL_REBUILD = os.environ.get("V203_ENABLE_BASE_RECALL_REBUILD", "1")
V203_ENABLE_DYNAMIC_RISK_LIBRARY = os.environ.get("V203_ENABLE_DYNAMIC_RISK_LIBRARY", "1")
V203_BASE_AUDIT_FILE = os.environ.get("V203_BASE_AUDIT_FILE", "v20_3_1_base_screen_audit.json")
V203_BASE_RISK_AUDIT_FILE = os.environ.get("V203_BASE_RISK_AUDIT_FILE", "v20_3_1_base_risk_audit.json")
V203_FORCE_RECENT_TRACKING_IN_POOL = os.environ.get("V203_FORCE_RECENT_TRACKING_IN_POOL", "1")
V203_RECENT_TRACKING_LOOKBACK_DAYS = int(os.environ.get("V203_RECENT_TRACKING_LOOKBACK_DAYS", "20"))
V203_R3_TO_DEEP_LIMIT = int(os.environ.get("V203_R3_TO_DEEP_LIMIT", "0"))  # 默认R3不进正式深评，保留风险审计
# V24.1：把“正式候选成交额”从软提示升级为实盘可执行门槛。
# 说明：V203_MIN_AMOUNT_FOR_FORMAL 保留兼容旧变量；V24.1 默认 8000 万，建议区间 5000 万~1 亿。
V24_1_MIN_AMOUNT_FOR_FORMAL = float(os.environ.get("V24_1_MIN_AMOUNT_FOR_FORMAL", os.environ.get("V203_MIN_AMOUNT_FOR_FORMAL", "80000000")))
V24_1_STRICT_AMOUNT_FOR_FORMAL = float(os.environ.get("V24_1_STRICT_AMOUNT_FOR_FORMAL", "100000000"))
V24_1_ABSOLUTE_MIN_AMOUNT = float(os.environ.get("V24_1_ABSOLUTE_MIN_AMOUNT", "50000000"))
V24_1_ENABLE_LIQUIDITY_HARD_GATE = os.environ.get("V24_1_ENABLE_LIQUIDITY_HARD_GATE", "1")
V24_1_ENABLE_DYNAMIC_POSITION = os.environ.get("V24_1_ENABLE_DYNAMIC_POSITION", "1")
V24_1_ENABLE_MARKET_REGIME = os.environ.get("V24_1_ENABLE_MARKET_REGIME", "1")
V24_1_MARKET_REGIME = os.environ.get("V24_1_MARKET_REGIME", "neutral").lower().strip()  # bull / neutral / range / bear / panic
V24_1_BEAR_MAX_FORMAL = int(os.environ.get("V24_1_BEAR_MAX_FORMAL", "1"))
V24_1_PANIC_MAX_FORMAL = int(os.environ.get("V24_1_PANIC_MAX_FORMAL", "0"))
V24_1_BACKTEST_CONFIG_FILE = os.environ.get("V24_1_BACKTEST_CONFIG_FILE", "v24_1_backtest_config.json")
V24_1_RUNTIME_AUDIT_FILE = os.environ.get("V24_1_RUNTIME_AUDIT_FILE", "v24_1_runtime_audit.json")
# 兼容旧代码引用：后续统一使用 V24_1_MIN_AMOUNT_FOR_FORMAL。
V203_MIN_AMOUNT_FOR_FORMAL = V24_1_MIN_AMOUNT_FOR_FORMAL


# ========================= V25 真实Walk-Forward回测与可实操报告 =========================
# 使用方式：RUN_V25_BACKTEST=1 python stock_alert_v25_full.py
# 注意：V25回测不混入每日推送流程；默认只读全历史缓存，避免联网导致不可复现。
V25_ENABLE_BACKTEST = os.environ.get("RUN_V25_BACKTEST", "0")
V25_BACKTEST_DATA_START = os.environ.get("V25_BACKTEST_DATA_START", "2016-01-01")
V25_BACKTEST_START = os.environ.get("V25_BACKTEST_START", "2020-01-01")
V25_BACKTEST_END = os.environ.get("V25_BACKTEST_END", "2025-12-31")
V25_BACKTEST_WINDOWS = [int(x) for x in os.environ.get("V25_BACKTEST_WINDOWS", "1,3,5,8,13,20").split(",") if str(x).strip()]
V25_BACKTEST_TOP_N = int(os.environ.get("V25_BACKTEST_TOP_N", "5"))
V25_BACKTEST_DEEP_LIMIT = int(os.environ.get("V25_BACKTEST_DEEP_LIMIT", str(DEEP_SCORE_LIMIT)))
V25_BACKTEST_MAX_STOCKS = int(os.environ.get("V25_BACKTEST_MAX_STOCKS", "0"))
V25_BACKTEST_MAX_DATES = int(os.environ.get("V25_BACKTEST_MAX_DATES", "0"))
V25_BACKTEST_DATE_STEP = int(os.environ.get("V25_BACKTEST_DATE_STEP", "1"))
V25_BACKTEST_COST_SINGLE_SIDE = float(os.environ.get("V25_BACKTEST_COST_SINGLE_SIDE", "0.0015"))
V25_BACKTEST_SLIPPAGE_SINGLE_SIDE = float(os.environ.get("V25_BACKTEST_SLIPPAGE_SINGLE_SIDE", "0.0005"))
V25_BACKTEST_STOP_LOSS = float(os.environ.get("V25_BACKTEST_STOP_LOSS", "0.055"))
V25_BACKTEST_TAKE_PROFIT = float(os.environ.get("V25_BACKTEST_TAKE_PROFIT", "0.12"))
V25_BACKTEST_USE_DYNAMIC_EXIT = os.environ.get("V25_BACKTEST_USE_DYNAMIC_EXIT", "1")
V25_BACKTEST_OUTPUT_DIR = os.environ.get("V25_BACKTEST_OUTPUT_DIR", "outputs/v25_backtest")
V25_BACKTEST_MIN_AMOUNT = float(os.environ.get("V25_BACKTEST_MIN_AMOUNT", str(V24_1_MIN_AMOUNT_FOR_FORMAL)))
V25_1_STRICT_NO_LOOKAHEAD = os.environ.get("V25_1_STRICT_NO_LOOKAHEAD", "1")
V25_1_BACKTEST_BASE_LIMIT = int(os.environ.get("V25_1_BACKTEST_BASE_LIMIT", "500"))
V25_1_REQUIRE_CACHE_ONLY_BACKTEST = os.environ.get("V25_1_REQUIRE_CACHE_ONLY_BACKTEST", "1")
V25_1_REPORT_MAX_LOSERS = int(os.environ.get("V25_1_REPORT_MAX_LOSERS", "30"))
V25_BACKTEST_STRICT_NO_LOOKAHEAD = os.environ.get("V25_BACKTEST_STRICT_NO_LOOKAHEAD", "1")
V25_2_RANDOM_SEED = int(os.environ.get("V25_2_RANDOM_SEED", "20250514"))
V25_2_PREFLIGHT_REQUIRED_FUNCTIONS = [
    "process_stock_base", "process_stock_deep", "select_deep_targets_v10",
    "v14_candidate_audit", "attach_data_quality_to_row", "apply_v212_opportunity_to_row",
    "build_confirm_condition", "build_giveup_condition", "v241_effective_amount",
    "v241_liquidity_profile", "v241_position_plan", "v241_market_regime_multiplier",
]
V25_BACKTEST_DISABLE_TELEGRAM = os.environ.get("V25_BACKTEST_DISABLE_TELEGRAM", "1")
V25_BACKTEST_MARKET_REGIME_MODE = os.environ.get("V25_BACKTEST_MARKET_REGIME_MODE", "simple_index_proxy")
V25_BACKTEST_REPORT_MD = os.environ.get("V25_BACKTEST_REPORT_MD", "v25_backtest_report.md")
V25_BACKTEST_REPORT_HTML = os.environ.get("V25_BACKTEST_REPORT_HTML", "v25_backtest_report.html")
V25_BACKTEST_TRADES_CSV = os.environ.get("V25_BACKTEST_TRADES_CSV", "v25_backtest_trades.csv")
V25_BACKTEST_DAILY_CSV = os.environ.get("V25_BACKTEST_DAILY_CSV", "v25_backtest_daily_portfolio.csv")
V25_BACKTEST_SUMMARY_CSV = os.environ.get("V25_BACKTEST_SUMMARY_CSV", "v25_backtest_summary.csv")
V25_BACKTEST_FAILED_CSV = os.environ.get("V25_BACKTEST_FAILED_CSV", "v25_backtest_failed.csv")
V25_BACKTEST_PROGRESS_EVERY = int(os.environ.get("V25_BACKTEST_PROGRESS_EVERY", "1"))
V20_REVIEW_WINDOWS = [1, 3, 5, 8, 13, 20]

# V20.1 A档硬门槛：宁可少给A，也不要把“买点未到/压力未破”的票写成正式可交易。
V20_A_MIN_SCORE = float(os.environ.get("V20_A_MIN_SCORE", "82"))
# V25.8：正式A档不降级；新增A- / B+动态分层，避免把观察票包装成正式A。
V20_A_MINUS_MIN_SCORE = float(os.environ.get("V20_A_MINUS_MIN_SCORE", "79"))
V20_BPLUS_MIN_SCORE = float(os.environ.get("V20_BPLUS_MIN_SCORE", "75"))
V20_A_MIN_RR = float(os.environ.get("V20_A_MIN_RR", "1.5"))
V20_A_MIN_TRADE_QUALITY = float(os.environ.get("V20_A_MIN_TRADE_QUALITY", "5"))
V20_MAX_DEFENSE_DIST_A = float(os.environ.get("V20_MAX_DEFENSE_DIST_A", "0.08"))
V20_MAX_DEFENSE_DIST_A_STRICT = float(os.environ.get("V20_MAX_DEFENSE_DIST_A_STRICT", "0.05"))
V20_MAX_NEAR_PRESSURE_A = float(os.environ.get("V20_MAX_NEAR_PRESSURE_A", "0.05"))
V20_TRADE_QUALITY_FLOOR_BPLUS = float(os.environ.get("V20_TRADE_QUALITY_FLOOR_BPLUS", "3"))
V20_TRADE_QUALITY_FLOOR_A_MINUS = float(os.environ.get("V20_TRADE_QUALITY_FLOOR_A_MINUS", "5"))
V20_OVERHEAT_RSI = float(os.environ.get("V20_OVERHEAT_RSI", "85"))
V20_OVERHEAT_CCI = float(os.environ.get("V20_OVERHEAT_CCI", "250"))
V20_BUY_ZONE_MISS_PCT = float(os.environ.get("V20_BUY_ZONE_MISS_PCT", "0.035"))
V20_CONFIRM_FAR_PCT = float(os.environ.get("V20_CONFIRM_FAR_PCT", "0.06"))
V20_PRESSURE_GRADE_BLOCK_A = os.environ.get("V20_PRESSURE_GRADE_BLOCK_A", "D")
# ===========================================================================

# V19.4压力/共振线合并规则：
# 距离<=3%：默认视为同一压力区；3%-5%：若结构/成交密集区相关则合并；>5%：通常拆层。
# 若两线合并为同一区域，有效突破确认价取更高线/更高上沿；仅突破较低线不给高分。
try:
    V19_PRESSURE_MERGE_NEAR_PCT = float(os.environ.get("V19_PRESSURE_MERGE_NEAR_PCT", "0.03") or 0.03)
except Exception:
    V19_PRESSURE_MERGE_NEAR_PCT = 0.03
try:
    V19_PRESSURE_MERGE_MID_PCT = float(os.environ.get("V19_PRESSURE_MERGE_MID_PCT", "0.05") or 0.05)
except Exception:
    V19_PRESSURE_MERGE_MID_PCT = 0.05

# V15：选股模型多周期供需压力带突破模型开关与参数。
ENABLE_XHU_PRESSURE_BREAKOUT = os.environ.get("ENABLE_XHU_PRESSURE_BREAKOUT", "1")
XHU_PRESSURE_MIN_QUALITY = float(os.environ.get("XHU_PRESSURE_MIN_QUALITY", "35"))
XHU_PRESSURE_DEFAULT_BUCKET_PCT = float(os.environ.get("XHU_PRESSURE_DEFAULT_BUCKET_PCT", "0.005"))
XHU_PRESSURE_MAX_ZONES_PER_PERIOD = int(os.environ.get("XHU_PRESSURE_MAX_ZONES_PER_PERIOD", "5"))
XHU_PRESSURE_ENABLE_YEARLY = os.environ.get("XHU_PRESSURE_ENABLE_YEARLY", "1")

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
    "stock_list_timeout": 0,
    "stock_list_fallback": 0,
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


def build_data_gate_header_lines():
    """Telegram/日志展示用：让用户一眼看到今天用的K线日期、覆盖率和模型池口径。"""
    lines = []
    if DATA_GATE_TARGET_DATE or DATA_GATE_COVERAGE or DATA_GATE_MODEL_COUNT or DATA_GATE_CACHE_COUNT:
        lines.append(
            "数据口径："
            f"使用K线目标日={DATA_GATE_TARGET_DATE or '未知'}；"
            f"最新K覆盖率={DATA_GATE_COVERAGE or '未知'}；"
            f"模型池={DATA_GATE_MODEL_COUNT or '未知'}；"
            f"缓存文件={DATA_GATE_CACHE_COUNT or '未知'}"
        )
        if DATA_GATE_STALE_COUNT or DATA_GATE_FAILED_COUNT:
            lines.append(f"数据限制：疑似停牌/无新K={DATA_GATE_STALE_COUNT or '0'}；增量失败={DATA_GATE_FAILED_COUNT or '0'}")
        if DATA_GATE_REASON:
            lines.append(f"数据闸门：{DATA_GATE_REASON}")
    return lines


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
        f"pause={KLINE_SOURCE_STATS.get('pause_count', 0)}，relogin={KLINE_SOURCE_STATS.get('relogin_count', 0)}，"
        f"stockListTimeout={KLINE_SOURCE_STATS.get('stock_list_timeout', 0)}，stockListFallback={KLINE_SOURCE_STATS.get('stock_list_fallback', 0)}"
    )


def _normalize_stock_list_df(df, source="baostock"):
    """
    V11.5：统一 BaoStock / AkShare 股票池字段，输出：代码、名称、bs_code。
    """
    if df is None or df.empty:
        return pd.DataFrame(columns=["代码", "名称", "bs_code"])

    d = df.copy()

    if source == "baostock":
        if "code" not in d.columns:
            print("BaoStock股票列表字段异常")
            print(d.head())
            return pd.DataFrame(columns=["代码", "名称", "bs_code"])
        name_col = "code_name" if "code_name" in d.columns else None
        if name_col is None:
            d["code_name"] = ""
            name_col = "code_name"
        d = d[["code", name_col]].copy()
        d = d.rename(columns={"code": "bs_code", name_col: "名称"})
        d["bs_code"] = d["bs_code"].astype(str)
        d["代码"] = d["bs_code"].str.split(".").str[-1]
    else:
        # AkShare stock_zh_a_spot_em 常见字段：代码、名称；不同版本可能略有差异。
        code_col = None
        name_col = None
        for c in ["代码", "code", "symbol"]:
            if c in d.columns:
                code_col = c
                break
        for c in ["名称", "name", "股票简称"]:
            if c in d.columns:
                name_col = c
                break
        if code_col is None:
            print("AkShare股票列表字段异常：未找到代码列")
            print(d.head())
            return pd.DataFrame(columns=["代码", "名称", "bs_code"])
        if name_col is None:
            d["名称"] = ""
            name_col = "名称"
        d = d[[code_col, name_col]].copy()
        d = d.rename(columns={code_col: "代码", name_col: "名称"})
        d["代码"] = d["代码"].astype(str).str.extract(r"(\d{6})", expand=False)
        d = d.dropna(subset=["代码"])

        def to_bs_code(code):
            code = str(code).zfill(6)
            if code.startswith(("600", "601", "603", "605", "688")):
                return "sh." + code
            if code.startswith(("000", "001", "002", "003", "300", "301")):
                return "sz." + code
            return ""

        d["bs_code"] = d["代码"].apply(to_bs_code)

    d["代码"] = d["代码"].astype(str).str.zfill(6)
    d["名称"] = d["名称"].astype(str)
    d["bs_code"] = d["bs_code"].astype(str)
    d = d[d["bs_code"].str.startswith(VALID_STOCK_PREFIXES)]
    d = d[~d["名称"].astype(str).str.contains("ST|\\*ST|退", regex=True, na=False)]
    d = d.drop_duplicates(subset=["代码"])

    if MAX_STOCKS > 0:
        d = d.head(MAX_STOCKS)

    return d[["代码", "名称", "bs_code"]]


def _query_all_stock_with_timeout(day, label="stock_list"):
    """
    V11.5：BaoStock query_all_stock 也加超时，避免卡在“抓取A股列表”。
    """
    with stock_query_timeout(STOCK_LIST_QUERY_TIMEOUT_SECONDS, f"{label}:{day}"):
        rs = bs.query_all_stock(day)
        return rs.get_data()


def get_last_trade_day():
    today = datetime.now()

    for i in range(10):
        day = (today - timedelta(days=i)).strftime("%Y-%m-%d")
        for attempt in range(1, STOCK_LIST_MAX_RETRIES + 1):
            try:
                df = _query_all_stock_with_timeout(day, label=f"last_trade_day_attempt{attempt}")
                if df is not None and not df.empty:
                    return day
            except StockDataTimeout as e:
                KLINE_SOURCE_STATS["stock_list_timeout"] = KLINE_SOURCE_STATS.get("stock_list_timeout", 0) + 1
                print(f"股票池交易日查询超时：source=baostock stage=query_trade_day day={day} retry={attempt} timeout={STOCK_LIST_QUERY_TIMEOUT_SECONDS}s")
                if STOCK_LIST_RELOGIN_ON_FAIL == "1":
                    baostock_relogin("stock_list_trade_day_timeout")
            except Exception as e:
                print(f"股票池交易日查询失败：source=baostock stage=query_trade_day day={day} retry={attempt} error={str(e)[:120]}")
                if STOCK_LIST_RELOGIN_ON_FAIL == "1":
                    baostock_relogin("stock_list_trade_day_error")
            time.sleep(0.8)

    # 如果 BaoStock 交易日查询持续失败，不在这里无限等待；返回今天，后续股票池可走 AkShare 备用。
    fallback_day = today.strftime("%Y-%m-%d")
    print(f"BaoStock交易日查询持续异常，暂用当前日期作为股票池日期：{fallback_day}")
    return fallback_day


def get_a_stock_list_from_baostock(trade_day):
    for attempt in range(1, STOCK_LIST_MAX_RETRIES + 1):
        try:
            print(f"股票池获取：source=baostock stage=query_all_stock day={trade_day} retry={attempt}/{STOCK_LIST_MAX_RETRIES}")
            df = _query_all_stock_with_timeout(trade_day, label=f"stock_list_attempt{attempt}")
            out = _normalize_stock_list_df(df, source="baostock")
            if not out.empty:
                return out
            print("BaoStock股票列表为空或字段异常")
        except StockDataTimeout:
            KLINE_SOURCE_STATS["stock_list_timeout"] = KLINE_SOURCE_STATS.get("stock_list_timeout", 0) + 1
            print(f"股票池获取超时：source=baostock stage=query_all_stock day={trade_day} retry={attempt} timeout={STOCK_LIST_QUERY_TIMEOUT_SECONDS}s")
        except Exception as e:
            print(f"股票池获取失败：source=baostock stage=query_all_stock day={trade_day} retry={attempt} error={str(e)[:160]}")

        if STOCK_LIST_RELOGIN_ON_FAIL == "1":
            baostock_relogin("stock_list_query_fail")
        time.sleep(1.5 * attempt)

    return pd.DataFrame(columns=["代码", "名称", "bs_code"])


def get_a_stock_list_from_akshare():
    if STOCK_LIST_FALLBACK_AKSHARE != "1":
        return pd.DataFrame(columns=["代码", "名称", "bs_code"])
    if ak is None:
        print("AkShare未成功导入，无法使用股票池备用通道")
        return pd.DataFrame(columns=["代码", "名称", "bs_code"])

    try:
        print("股票池获取：source=akshare stage=stock_zh_a_spot_em fallback=1")
        with stock_query_timeout(STOCK_LIST_QUERY_TIMEOUT_SECONDS, "akshare_stock_list"):
            df = ak.stock_zh_a_spot_em()
        out = _normalize_stock_list_df(df, source="akshare")
        if not out.empty:
            KLINE_SOURCE_STATS["stock_list_fallback"] = KLINE_SOURCE_STATS.get("stock_list_fallback", 0) + 1
            print(f"AkShare备用股票池获取成功：{len(out)} 只")
        return out
    except StockDataTimeout:
        KLINE_SOURCE_STATS["stock_list_timeout"] = KLINE_SOURCE_STATS.get("stock_list_timeout", 0) + 1
        print(f"AkShare备用股票池获取超时：timeout={STOCK_LIST_QUERY_TIMEOUT_SECONDS}s")
    except Exception as e:
        print(f"AkShare备用股票池获取失败：error={str(e)[:160]}")

    return pd.DataFrame(columns=["代码", "名称", "bs_code"])


def get_a_stock_list_remote():
    global LAST_TRADE_DAY

    trade_day = get_last_trade_day()
    LAST_TRADE_DAY = trade_day

    print(f"使用股票池日期：{trade_day}")

    df = get_a_stock_list_from_baostock(trade_day)
    source_used = "baostock"

    if df.empty:
        print("BaoStock股票池获取失败，准备切换备用 AkShare 股票池。")
        df = get_a_stock_list_from_akshare()
        source_used = "akshare" if not df.empty else "none"

    if df.empty:
        print("股票池获取失败：BaoStock 与 AkShare 均未返回有效A股列表")
        return pd.DataFrame(columns=["代码", "名称", "bs_code"])

    print(f"A股个股列表获取成功：{len(df)} 只，source={source_used}")
    print("股票池前20只：")
    print(df[["代码", "名称", "bs_code"]].head(20).to_string(index=False))

    return df[["代码", "名称", "bs_code"]]



def _plain_code_from_bs_code(bs_code):
    try:
        return str(bs_code).split(".")[-1].zfill(6)
    except Exception:
        return ""


def _bs_code_from_plain_code(code):
    code = str(code).zfill(6)
    if code.startswith(("600", "601", "603", "605", "688")):
        return "sh." + code
    if code.startswith(("000", "001", "002", "003", "300", "301")):
        return "sz." + code
    return ""


def _stock_code_to_plain(value):
    text = str(value).strip()
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) >= 6:
        return digits[-6:].zfill(6)
    return ""


def _latest_csv_by_prefix(directory, prefix):
    try:
        if not os.path.exists(directory):
            return ""
        files = [os.path.join(directory, f) for f in os.listdir(directory) if f.startswith(prefix) and f.lower().endswith(".csv")]
        if not files:
            return ""
        return max(files, key=lambda x: os.path.getmtime(x))
    except Exception:
        return ""


def _load_model_universe_file():
    """优先读取验收脚本生成的 model_usable_universe_*.csv；没有则返回空。"""
    candidates = []
    if MODEL_UNIVERSE_FILE:
        candidates.append(MODEL_UNIVERSE_FILE)
    candidates.append(_latest_csv_by_prefix("outputs", "model_usable_universe_"))
    candidates.append(_latest_csv_by_prefix(".", "model_usable_universe_"))

    for path in candidates:
        if not path or not os.path.exists(path):
            continue
        try:
            df = pd.read_csv(path, dtype=str)
            if df is None or df.empty:
                continue
            raw_count = len(df)
            code_col = None
            for c in ["原始代码", "代码", "股票代码", "code", "symbol"]:
                if c in df.columns:
                    code_col = c
                    break
            if code_col is None:
                continue
            name_col = "股票名称" if "股票名称" in df.columns else ("名称" if "名称" in df.columns else None)
            usable_col = "是否进入一号员工模型池" if "是否进入一号员工模型池" in df.columns else None
            if usable_col:
                before = len(df)
                df = df[df[usable_col].astype(str).str.strip() == "是"].copy()
                excluded_unusable = before - len(df)
            else:
                excluded_unusable = 0
            df["代码"] = df[code_col].apply(_stock_code_to_plain)
            before = len(df)
            df = df[df["代码"].str.match(r"^\d{6}$", na=False)].copy()
            excluded_bad_code = before - len(df)
            df["名称"] = df[name_col].astype(str) if name_col else ""
            # 严格使用每日目标交易日：停牌/无新K/数据未更新的票不进入当天正式扫描。
            excluded_not_target_date = 0
            if DATA_GATE_TARGET_DATE and "K线最新日期" in df.columns:
                before = len(df)
                latest = pd.to_datetime(df["K线最新日期"], errors="coerce")
                target_ts = pd.to_datetime(DATA_GATE_TARGET_DATE, errors="coerce")
                if not pd.isna(target_ts):
                    df = df[latest >= target_ts].copy()
                    excluded_not_target_date = before - len(df)
            df["bs_code"] = df["代码"].apply(_bs_code_from_plain_code)
            before = len(df)
            df = df[df["bs_code"].astype(str).str.startswith(VALID_STOCK_PREFIXES)].copy()
            excluded_market = before - len(df)
            before = len(df)
            df = df[~df["代码"].isin(EXCLUDE_MODEL_CODES)].copy()
            excluded_manual = before - len(df)
            before = len(df)
            df = df[~df["名称"].astype(str).str.contains("ST|\\*ST|退", regex=True, na=False)].copy()
            excluded_st = before - len(df)
            df = df.drop_duplicates("代码")
            if MAX_STOCKS > 0:
                df = df.head(MAX_STOCKS)
            if not df.empty:
                print(
                    f"只读缓存股票池：使用模型验收股票池 file={path} "
                    f"raw={raw_count} usable_excluded={excluded_unusable} bad_code={excluded_bad_code} "
                    f"not_target_date={excluded_not_target_date} market_excluded={excluded_market} "
                    f"manual_excluded={excluded_manual} st_excluded={excluded_st} final_stocks={len(df)}"
                )
                return df[["代码", "名称", "bs_code"]]
        except Exception as e:
            print(f"模型验收股票池读取失败：file={path} error={str(e)[:160]}")
    return pd.DataFrame(columns=["代码", "名称", "bs_code"])


def _load_universe_from_flat_cache():
    """没有验收CSV时，使用 kline_cache/*.csv + _full_history_status.csv 自动生成股票池。"""
    if not os.path.exists(FULL_HISTORY_CACHE_DIR):
        return pd.DataFrame(columns=["代码", "名称", "bs_code"])

    meta = pd.DataFrame()
    meta_path = os.path.join(FULL_HISTORY_CACHE_DIR, "_full_history_status.csv")
    if os.path.exists(meta_path):
        try:
            meta = pd.read_csv(meta_path, dtype={"code": str})
            if not meta.empty and "code" in meta.columns:
                meta["code"] = meta["code"].astype(str).str.zfill(6)
        except Exception as e:
            print(f"只读缓存股票池：状态文件读取失败 {e}")
            meta = pd.DataFrame()

    codes = []
    for f in os.listdir(FULL_HISTORY_CACHE_DIR):
        if f.lower().endswith(".csv") and not f.startswith("_"):
            code = f[:6]
            if code.isdigit():
                codes.append(code.zfill(6))
    df = pd.DataFrame({"代码": sorted(set(codes))})
    if df.empty:
        return pd.DataFrame(columns=["代码", "名称", "bs_code"])

    if not meta.empty:
        name_col = "name" if "name" in meta.columns else None
        status_col = "status" if "status" in meta.columns else None
        keep_cols = ["code"] + ([name_col] if name_col else []) + ([status_col] if status_col else [])
        mm = meta[keep_cols].drop_duplicates("code").copy()
        rename = {"code": "代码"}
        if name_col:
            rename[name_col] = "名称"
        if status_col:
            rename[status_col] = "缓存状态"
        mm = mm.rename(columns=rename)
        df = df.merge(mm, on="代码", how="left")
    if "名称" not in df.columns:
        df["名称"] = ""
    df["名称"] = df["名称"].fillna("").astype(str)
    df["bs_code"] = df["代码"].apply(_bs_code_from_plain_code)
    df = df[df["bs_code"].astype(str).str.startswith(VALID_STOCK_PREFIXES)].copy()
    df = df[~df["代码"].isin(EXCLUDE_MODEL_CODES)].copy()
    df = df[~df["名称"].astype(str).str.contains("ST|\\*ST|退", regex=True, na=False)].copy()
    if MAX_STOCKS > 0:
        df = df.head(MAX_STOCKS)
    print(f"只读缓存股票池：使用 kline_cache 扁平缓存生成股票池 stocks={len(df)} exclude={sorted(EXCLUDE_MODEL_CODES)}")
    return df[["代码", "名称", "bs_code"]]


def get_a_stock_list_from_full_cache_universe():
    df = _load_model_universe_file()
    if df is not None and not df.empty:
        return df
    return _load_universe_from_flat_cache()


def get_a_stock_list():
    """V16只读缓存版：优先使用验收股票池/全历史缓存股票池；失败才走旧联网股票池。"""
    global LAST_TRADE_DAY
    LAST_TRADE_DAY = datetime.now().strftime("%Y-%m-%d")

    if USE_FULL_HISTORY_CACHE == "1":
        df = get_a_stock_list_from_full_cache_universe()
        if df is not None and not df.empty:
            print(f"一号员工只读缓存股票池启用：stocks={len(df)}，不再联网获取全市场股票列表。")
            print("股票池前20只：")
            print(df[["代码", "名称", "bs_code"]].head(20).to_string(index=False))
            return df[["代码", "名称", "bs_code"]]
        print("只读缓存股票池为空，回退旧版联网股票池。")

    return get_a_stock_list_remote()


def cache_path(bs_code, cache_scope="deep"):
    """V12.4：基础/深度缓存分开。基础轻扫不污染深度长周期缓存。"""
    scope = "base" if str(cache_scope).lower() == "base" else "deep"
    subdir = os.path.join(CACHE_DIR, scope)
    os.makedirs(subdir, exist_ok=True)
    return os.path.join(subdir, f"{bs_code.replace('.', '_')}.csv")


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


def read_cached_kline(bs_code, cache_scope="deep", min_rows=0):
    path = cache_path(bs_code, cache_scope=cache_scope)

    if not os.path.exists(path):
        return None

    try:
        df = pd.read_csv(path, dtype={"date": str})
        df = normalize_kline_df(df)

        if df is None or df.empty or "date" not in df.columns:
            return None

        if min_rows and len(df) < int(min_rows):
            return None

        last_date = str(df["date"].max())

        if LAST_TRADE_DAY and last_date >= LAST_TRADE_DAY:
            return df

        return None

    except Exception as e:
        print(f"读取缓存失败 {bs_code} scope={cache_scope}: {e}")
        return None




def read_stale_cached_kline(bs_code, cache_scope="deep", min_rows=0):
    """
    V12.7：旧缓存兜底。
    read_cached_kline 只接受已经更新到 LAST_TRADE_DAY 的缓存；
    但公开数据源中途断流时，旧缓存至少能保留该股进入基础/深度评分，
    防止“成功数不动、失败数暴增”。旧缓存默认只允许近10天内使用。
    """
    if ALLOW_STALE_KLINE_CACHE != "1":
        return None
    path = cache_path(bs_code, cache_scope=cache_scope)
    if not os.path.exists(path):
        return None
    try:
        df = pd.read_csv(path, dtype={"date": str})
        df = normalize_kline_df(df)
        if df is None or df.empty or "date" not in df.columns:
            return None
        if min_rows and len(df) < int(min_rows):
            return None
        last_date = str(df["date"].max())
        if LAST_TRADE_DAY:
            try:
                delta_days = (pd.to_datetime(LAST_TRADE_DAY) - pd.to_datetime(last_date)).days
                if delta_days < 0:
                    delta_days = 0
                if delta_days > STALE_CACHE_MAX_DAYS:
                    return None
            except Exception:
                pass
        print(f"V12.7旧缓存兜底：{bs_code} scope={cache_scope} last_date={last_date}")
        return df
    except Exception as e:
        print(f"读取旧缓存失败 {bs_code} scope={cache_scope}: {e}")
        return None


def _akshare_symbol_from_bs_code(bs_code):
    try:
        return str(bs_code).split(".")[-1].zfill(6)
    except Exception:
        return ""


def get_daily_kline_akshare_fallback(bs_code, lookback_days=None, cache_scope="deep"):
    """V12.7：BaoStock失败后的AkShare补拉通道。只做数据源兜底，不改变评分逻辑。"""
    if KLINE_FALLBACK_AKSHARE != "1":
        return None
    if ak is None:
        return None
    if lookback_days is None:
        lookback_days = DEEP_KLINE_LOOKBACK_DAYS if cache_scope == "deep" else BASE_KLINE_LOOKBACK_DAYS
    symbol = _akshare_symbol_from_bs_code(bs_code)
    if not symbol:
        return None
    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=int(lookback_days))).strftime("%Y%m%d")
    for attempt in range(1, max(1, AKSHARE_FALLBACK_MAX_RETRIES) + 1):
        try:
            time.sleep(AKSHARE_FALLBACK_SLEEP * attempt)
            with stock_query_timeout(SINGLE_STOCK_TIMEOUT_SECONDS, f"akshare:{bs_code}"):
                df = ak.stock_zh_a_hist(
                    symbol=symbol,
                    period="daily",
                    start_date=start_date,
                    end_date=end_date,
                    adjust="qfq"
                )
            if df is None or df.empty:
                continue
            # AkShare常见中文字段转为本模型统一字段。
            rename_map = {
                "日期": "date",
                "开盘": "open",
                "收盘": "close",
                "最高": "high",
                "最低": "low",
                "成交量": "volume",
                "成交额": "amount",
                "涨跌幅": "pct_chg",
                "换手率": "turnover",
            }
            df = df.rename(columns=rename_map)
            needed = ["date", "open", "close", "high", "low", "volume"]
            if not all(c in df.columns for c in needed):
                print(f"AkShare补拉字段异常：{bs_code} columns={list(df.columns)[:20]}")
                continue
            if "amount" not in df.columns:
                df["amount"] = 0
            if "pct_chg" not in df.columns:
                df["pct_chg"] = 0
            if "turnover" not in df.columns:
                df["turnover"] = 0
            df = df[["date", "open", "close", "high", "low", "volume", "amount", "pct_chg", "turnover"]].copy()
            df["date"] = df["date"].astype(str)
            for col in ["open", "high", "low", "close", "volume", "amount", "pct_chg", "turnover"]:
                df[col] = pd.to_numeric(df[col], errors="coerce")
            df = normalize_kline_df(df)
            if df is None or df.empty:
                continue
            write_cached_kline(bs_code, df, cache_scope=cache_scope)
            reset_kline_success_streak()
            print(f"V12.7 AkShare补拉成功：source=akshare stage=fetch_kline symbol={bs_code} rows={len(df)} scope={cache_scope}")
            return df
        except StockDataTimeout as e:
            KLINE_SOURCE_STATS["timeout"] += 1
            KLINE_SOURCE_STATS["consecutive_fail"] += 1
            if attempt <= 2:
                print(f"AkShare补拉超时：symbol={bs_code} retry={attempt} error={e}")
        except Exception as e:
            KLINE_SOURCE_STATS["other_error"] += 1
            KLINE_SOURCE_STATS["consecutive_fail"] += 1
            if attempt <= 2:
                print(f"AkShare补拉失败：symbol={bs_code} retry={attempt} error={str(e)[:160]}")
    return None

def write_cached_kline(bs_code, df, cache_scope="deep"):
    try:
        path = cache_path(bs_code, cache_scope=cache_scope)
        df.to_csv(path, index=False, encoding="utf-8")
    except Exception as e:
        print(f"写入缓存失败 {bs_code} scope={cache_scope}: {e}")


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
        "行政处罚", "非标", "非标准审计", "非标审计", "无法表示", "无法表示意见",
        "保留意见", "审计保留意见", "审计报告保留意见", "出具保留意见", "带保留意见",
        "否定意见", "审计报告否定意见", "退市", "ST", "债务违约",
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
    monthly["open"] = m["open"].resample("ME").first()
    monthly["high"] = m["high"].resample("ME").max()
    monthly["low"] = m["low"].resample("ME").min()
    monthly["close"] = m["close"].resample("ME").last()
    monthly["volume"] = m["volume"].resample("ME").sum()
    monthly["amount"] = m["amount"].resample("ME").sum() if "amount" in m.columns else 0
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
    monthly["boll_width"] = (monthly["boll_upper"] - monthly["boll_lower"]) / monthly["boll_mid"].replace(0, np.nan)
    ma_max = monthly[["ma3", "ma6", "ma12", "ma24"]].max(axis=1)
    ma_min = monthly[["ma3", "ma6", "ma12", "ma24"]].min(axis=1)
    monthly["bbi_dispersion"] = (ma_max - ma_min) / monthly["bbi_mid"].replace(0, np.nan)
    monthly["mid"] = monthly["bbi_mid"].where(monthly["bbi_mid"].notna(), monthly["boll_mid"])
    monthly["vol_ma5"] = monthly["volume"].rolling(5).mean()
    monthly["body_pct"] = (monthly["close"] - monthly["open"]) / monthly["open"].replace(0, np.nan)
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
    if len(valid_width) >= 24 and pd.notna(current.get("boll_width", np.nan)):
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

    if len(valid_disp) >= 24 and pd.notna(current.get("bbi_dispersion", np.nan)):
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



def flat_full_cache_path(bs_code):
    code = _plain_code_from_bs_code(bs_code)
    if not code:
        return ""
    return os.path.join(FULL_HISTORY_CACHE_DIR, f"{code}.csv")


def normalize_full_history_cache_df(df):
    if df is None or df.empty:
        return None
    d = df.copy()
    rename_map = {
        "日期": "date", "交易日期": "date", "trade_date": "date", "Date": "date", "datetime": "date", "time": "date",
        "开盘": "open", "开盘价": "open", "Open": "open",
        "收盘": "close", "收盘价": "close", "Close": "close",
        "最高": "high", "最高价": "high", "High": "high",
        "最低": "low", "最低价": "low", "Low": "low",
        "成交量": "volume", "成交量(手)": "volume", "vol": "volume", "Volume": "volume",
        "成交额": "amount", "成交额(元)": "amount", "turnover": "amount", "Amount": "amount",
    }
    d = d.rename(columns={c: rename_map.get(c, c) for c in d.columns})
    required = ["date", "open", "high", "low", "close", "volume"]
    if not all(c in d.columns for c in required):
        return None
    if "amount" not in d.columns:
        d["amount"] = 0
    d = d[["date", "open", "close", "high", "low", "volume", "amount"]].copy()
    d["date"] = pd.to_datetime(d["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    for col in ["open", "high", "low", "close", "volume", "amount"]:
        d[col] = pd.to_numeric(d[col], errors="coerce")
    d = d.dropna(subset=["date", "open", "high", "low", "close", "volume"])
    d = d.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)
    if d.empty:
        return None
    # Q类股票/前复权副作用：裁剪掉早期非正价，避免均线、BOLL、压力带被污染。
    before = len(d)
    d = d[(d["open"] > 0) & (d["high"] > 0) & (d["low"] > 0) & (d["close"] > 0)].copy()
    if d.empty:
        return None
    if len(d) < before:
        pass
    d["pct_chg"] = d["close"].pct_change().fillna(0) * 100
    d["turnover"] = 0.0
    d = d[["date", "open", "close", "high", "low", "volume", "amount", "pct_chg", "turnover"]]
    return normalize_kline_df(d)


def read_full_history_flat_cache(bs_code, cache_scope="deep", min_rows=0):
    if USE_FULL_HISTORY_CACHE != "1":
        return None
    code = _plain_code_from_bs_code(bs_code)
    if code in EXCLUDE_MODEL_CODES:
        return None
    path = flat_full_cache_path(bs_code)
    if not path or not os.path.exists(path):
        return None
    try:
        df = pd.read_csv(path, dtype={"date": str})
        df = normalize_full_history_cache_df(df)
        if df is None or df.empty:
            return None
        if min_rows and len(df) < int(min_rows):
            return None
        last_date = str(df["date"].max())
        try:
            gap_days = (datetime.now().date() - pd.to_datetime(last_date).date()).days
            if gap_days > FULL_CACHE_MAX_STALE_DAYS:
                print(f"全历史缓存偏旧：{bs_code} last_date={last_date} gap={gap_days}d，跳过只读缓存并进入旧通道兜底。")
                return None
        except Exception:
            pass
        if str(cache_scope).lower() == "base" and FULL_CACHE_BASE_TAIL_ROWS > 0:
            return df.tail(max(FULL_CACHE_BASE_TAIL_ROWS, int(min_rows or 0))).reset_index(drop=True)
        if str(cache_scope).lower() == "deep" and FULL_CACHE_DEEP_USE_ALL != "1":
            # 如需极速深评，可通过环境变量关闭全量深评，只取最近约3000根。
            return df.tail(3000).reset_index(drop=True)
        return df
    except Exception as e:
        print(f"读取全历史扁平缓存失败 {bs_code}: {e}")
        return None


def get_daily_kline(bs_code, lookback_days=None, cache_scope="deep"):
    """V16只读缓存版：优先读取全历史扁平缓存；失败才走旧联网/旧缓存通道。"""
    if lookback_days is None:
        lookback_days = DEEP_KLINE_LOOKBACK_DAYS if cache_scope == "deep" else BASE_KLINE_LOOKBACK_DAYS
    min_rows = FULL_CACHE_MIN_ROWS_BASE if cache_scope == "base" else FULL_CACHE_MIN_ROWS_DEEP

    full_cached = read_full_history_flat_cache(bs_code, cache_scope=cache_scope, min_rows=min_rows)
    if full_cached is not None:
        return full_cached

    # 回退旧版分层缓存/联网通道，保留原有稳定性保护。
    min_rows = 180 if cache_scope == "base" else 700
    cached = read_cached_kline(bs_code, cache_scope=cache_scope, min_rows=min_rows)

    if cached is not None:
        return cached

    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=int(lookback_days))).strftime("%Y-%m-%d")

    fields = "date,open,high,low,close,volume,amount,pctChg,turn,tradestatus"

    max_retries = max(1, KLINE_MAX_RETRIES)
    for i in range(max_retries):
        try:
            # V12.7：连续失败过多时，先短暂停顿+重登，避免从某只以后全失败。
            if KLINE_SOURCE_STATS.get("consecutive_fail", 0) > 0 and KLINE_SOURCE_STATS.get("consecutive_fail", 0) % DATA_SOURCE_CONSECUTIVE_FAIL_LIMIT == 0:
                print(
                    f"V12.7连续失败熔断：source=baostock consecutive_fail={KLINE_SOURCE_STATS.get('consecutive_fail', 0)} "
                    f"pause={DATA_SOURCE_RECOVERY_PAUSE_SECONDS}s symbol={bs_code}"
                )
                time.sleep(max(5, DATA_SOURCE_RECOVERY_PAUSE_SECONDS))
                if BAOSTOCK_RELOGIN_ON_BROKEN_PIPE == "1":
                    baostock_relogin("v127_consecutive_fail_recovery")

            with stock_query_timeout(SINGLE_STOCK_TIMEOUT_SECONDS, bs_code):
                if SUPPRESS_BAOSTOCK_VERBOSE == "1":
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
                KLINE_SOURCE_STATS["other_error"] += 1
                KLINE_SOURCE_STATS["consecutive_fail"] += 1
                time.sleep(0.15 + 0.15 * i)
                continue

            df = df[df["tradestatus"] == "1"].copy()

            if df.empty:
                KLINE_SOURCE_STATS["other_error"] += 1
                KLINE_SOURCE_STATS["consecutive_fail"] += 1
                continue

            for col in ["open", "high", "low", "close", "volume", "amount", "pctChg", "turn"]:
                df[col] = pd.to_numeric(df[col], errors="coerce")

            df = df.dropna(subset=["open", "high", "low", "close", "volume"])

            if df.empty:
                KLINE_SOURCE_STATS["other_error"] += 1
                KLINE_SOURCE_STATS["consecutive_fail"] += 1
                continue

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
                KLINE_SOURCE_STATS["other_error"] += 1
                KLINE_SOURCE_STATS["consecutive_fail"] += 1
                continue

            write_cached_kline(bs_code, df, cache_scope=cache_scope)
            time.sleep(REQUEST_SLEEP)
            reset_kline_success_streak()

            return df

        except StockDataTimeout as e:
            KLINE_SOURCE_STATS["timeout"] += 1
            KLINE_SOURCE_STATS["consecutive_fail"] += 1
            if KLINE_SOURCE_STATS["timeout"] <= 5 or KLINE_SOURCE_STATS["timeout"] % 20 == 0:
                print(f"K线获取超时：source=baostock stage=fetch_kline symbol={bs_code} retry={i + 1}/{max_retries} error={e}")
            time.sleep(0.45 + 0.25 * i)
            continue

        except Exception as e:
            handle_kline_source_error(bs_code, f"{i + 1}/{max_retries}", e)
            time.sleep(0.45 + 0.25 * i)

    # V12.7：BaoStock失败后，先走AkShare补拉；再不行才用旧缓存兜底。
    fallback_df = get_daily_kline_akshare_fallback(bs_code, lookback_days=lookback_days, cache_scope=cache_scope)
    if fallback_df is not None and not fallback_df.empty:
        return fallback_df

    stale_min_rows = BASE_EMPTY_CACHE_MIN_ROWS if cache_scope == "base" else DEEP_EMPTY_CACHE_MIN_ROWS
    stale_df = read_stale_cached_kline(bs_code, cache_scope=cache_scope, min_rows=stale_min_rows)
    if stale_df is not None and not stale_df.empty:
        return stale_df

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
    entity_down_pct = ((seg["open"] - seg["close"]) / preclose.replace(0, np.nan)).where(seg["close"] < seg["open"], 0)
    range_pos = ((seg["close"] - seg["low"]) / (seg["high"] - seg["low"]).replace(0, np.nan)).fillna(0.5)
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
        down_body = ((after_seg["open"] - after_seg["close"]) / preclose.replace(0, np.nan)).where(after_seg["close"] < after_seg["open"], 0)
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

    # V12.4：基础层只返回最近CHECK_DAYS，昂贵的历史回看型入口模型只对最终可返回的尾部K线计算。
    # 这不是删逻辑，而是避免对每只股票的上千根历史K线重复跑同一类“当前候选”判断。
    _active_base_indices = set(range(max(0, len(df) - max(1, CHECK_DAYS)), len(df)))

    # ===== 原模型底座字段保留：用于诊断，也用于V10基础攻击质量的子项 =====
    df["vol_ma"] = df["volume"].rolling(N).mean()
    df["volr"] = df["volume"] / df["vol_ma"].replace(0, np.nan)

    df["upbody"] = (df["close"] - df["open"]).where(df["close"] > df["open"], 0)
    df["upcount"] = (df["close"] > df["open"]).rolling(N).sum()
    df["upbody_sum"] = df["upbody"].rolling(N).sum()
    df["upbody_ma"] = df["upbody_sum"] / df["upcount"].replace(0, np.nan)

    df["body"] = df["close"] - df["open"]
    df["body_ratio"] = df["body"] / df["upbody_ma"].replace(0, np.nan)

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

    df["volscore"] = 0.0
    df.loc[df["volr"] >= 1.2, "volscore"] = 10
    df.loc[df["volr"] >= 1.5, "volscore"] = 20
    df.loc[df["volr"] >= 2.0, "volscore"] = 25
    df.loc[df["volr"] >= 2.5, "volscore"] = 30

    up = df["close"] > df["open"]
    df["bodyscore"] = 0.0
    df.loc[up & (df["body"] >= df["upbody_ma"]), "bodyscore"] = 10
    df.loc[up & (df["body"] >= df["upbody_ma"] * 1.2), "bodyscore"] = 15
    df.loc[up & (df["body"] >= df["upbody_ma"] * 1.5), "bodyscore"] = 20

    df["posscore"] = 0.0
    df.loc[df["pos"] >= 0.6, "posscore"] = 10
    df.loc[df["pos"] >= 0.7, "posscore"] = 15
    df.loc[df["pos"] >= 0.8, "posscore"] = 20

    df["brscore"] = 0.0
    df.loc[df["high"] >= df["prehigh"], "brscore"] = 5
    df.loc[df["close"] > df["prehigh"], "brscore"] = 15
    df.loc[df["close"] > df["prehigh"] * 1.01, "brscore"] = 20

    df["structscore"] = 0.0
    df.loc[(df["volr"] >= 2.5) & (df["pos"] >= 0.8), "structscore"] = 2
    df.loc[df["uptrend"], "structscore"] = 5
    df.loc[df["close"] > df["prehigh"], "structscore"] = 8
    df.loc[(df["close"] > df["prehigh"]) & (df["volr"] >= 2), "structscore"] = 10

    df["score"] = df["volscore"] + df["bodyscore"] + df["posscore"] + df["brscore"] + df["structscore"]
    df["score_base_model_legacy"] = (df["score"] / 100 * 22).clip(0, 22)

    df["vr1"] = df["volume"] / df["volume"].shift(1).replace(0, np.nan)
    df["xg0"] = (df["score"] >= SCORE_LIMIT) & (df["vr1"] >= VR1_MIN) & (df["vr1"] <= VR1_MAX)
    df["xg"] = df["xg0"]

    df["preclose"] = df["close"].shift(1)
    df["entity_pct"] = ((df["close"] - df["open"]) / df["preclose"].replace(0, np.nan) * 100).fillna(0)
    df["break_rate"] = (df["close"] / df["prehigh"].replace(0, np.nan) - 1).fillna(0)

    df["bias20"] = (df["close"] / df["ma20"].replace(0, np.nan) - 1).fillna(0)
    df["bias60"] = (df["close"] / df["ma60"].replace(0, np.nan) - 1).fillna(0)

    df["high_250"] = df["high"].rolling(250).max()
    df["low_250"] = df["low"].rolling(250).min()
    df["long_pos_250"] = ((df["close"] - df["low_250"]) / (df["high_250"] - df["low_250"]).replace(0, np.nan)).fillna(0)

    # 压力分层前移：基础入口就要知道近端压力是不是贴脸。
    df["overhead_high_60"] = df["high"].shift(1).rolling(60).max()
    df["overhead_high_120"] = df["high"].shift(1).rolling(120).max()
    df["overhead_high_250"] = df["high"].shift(1).rolling(250).max()
    df["near_pressure_dist"] = (df["overhead_high_60"] / df["close"].replace(0, np.nan) - 1).fillna(0)
    df["mid_pressure_dist"] = (df["overhead_high_120"] / df["close"].replace(0, np.nan) - 1).fillna(0)
    df["overhead_pressure_dist"] = (df["overhead_high_250"] / df["close"].replace(0, np.nan) - 1).fillna(0)
    for _c in ["near_pressure_dist", "mid_pressure_dist", "overhead_pressure_dist"]:
        df.loc[df[_c] < 0, _c] = 0

    df["just_cross_ma120"] = (df["close"] > df["ma120"]) & (df["close"].shift(1) <= df["ma120"].shift(1))
    df["just_cross_ma250"] = (df["close"] > df["ma250"]) & (df["close"].shift(1) <= df["ma250"].shift(1))
    df["ma20_slope_5"] = (df["ma20"] / df["ma20"].shift(5).replace(0, np.nan) - 1).fillna(0)

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
        up_vol = df["volume"].where(df["is_up"], 0).rolling(_w).sum() / up_days.replace(0, np.nan)
        down_vol = df["volume"].where(df["is_down"], 0).rolling(_w).sum() / down_days.replace(0, np.nan)
        df[f"base_up_days_{_w}"] = up_days.fillna(0)
        df[f"base_up_down_vol_ratio_{_w}"] = (up_vol / down_vol.replace(0, np.nan)).fillna(0)

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
        if _i not in _active_base_indices:
            limit_hold_base_scores.append(0.0)
            continue
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
    df["amp20"] = ((df["high_20_prev"] - df["low_20_prev"]) / df["close"].replace(0, np.nan)).fillna(0)
    df["amp40"] = ((df["high_40_prev"] - df["low_40_prev"]) / df["close"].replace(0, np.nan)).fillna(0)
    df["platform20_break_base"] = (df["amp20"] <= 0.14) & (df["close"] > df["high_20_prev"] * 1.005)
    df["platform40_break_base"] = (df["amp40"] <= 0.22) & (df["close"] > df["high_40_prev"] * 1.005)
    df["break_bottom_reclaim_base"] = (
        (df["low"] <= df["low"].shift(1).rolling(40).min() * 0.985)
        & (df["close"] >= df["low_40_prev"] * 1.003)
        & df["is_up"]
        & (df["pos"] >= 0.60)
    )
    df["key_level_base"] = df["high_40_prev"].where(df["platform40_break_base"], df["prehigh"])
    df["distance_to_key_base"] = (df["close"] / df["key_level_base"].replace(0, np.nan) - 1).fillna(0)

    # V11.1 基础版黄金倍量入口模型：
    # 第一倍量必须在明显平台/凹口上沿干净突破，不能只用“左侧高点±5%”宽松识别；
    # 调整后第二倍量还要干净突破首倍高点。基础层只给入口，深度层再精确分类。
    fibo_scores = []
    fibo_descs = []
    fibo_first_highs = []
    fibo_level_150s = []
    fibo_target_dists = []
    for _i in range(len(df)):
        if _i not in _active_base_indices:
            fibo_scores.append(0.0)
            fibo_descs.append("")
            fibo_first_highs.append(0.0)
            fibo_level_150s.append(0.0)
            fibo_target_dists.append(0.0)
            continue
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
    rs = avg_gain / avg_loss.replace(0, np.nan)
    df["base_rsi"] = (100 - (100 / (1 + rs))).fillna(50)
    tp = (df["high"] + df["low"] + df["close"]) / 3
    tp_ma = tp.rolling(14).mean()
    tp_md = (tp - tp_ma).abs().rolling(14).mean()
    df["base_cci"] = ((tp - tp_ma) / (0.015 * tp_md.replace(0, np.nan))).fillna(0)

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
    amp120 = ((df["high"].rolling(120).max() - df["low"].rolling(120).min()) / df["close"].replace(0, np.nan)).fillna(0)
    amp60 = ((df["high"].rolling(60).max() - df["low"].rolling(60).min()) / df["close"].replace(0, np.nan)).fillna(0)
    df.loc[(amp60 < amp120 * 0.75) & (amp60 > 0), "base_long_cycle_potential_score"] += 1
    df.loc[(df["long_pos_250"] > 0.85) & ((df["vr1"] > 3.0) | (df["entity_pct"] > 6)), "base_long_cycle_potential_score"] -= 4
    df["base_long_cycle_potential_score"] = df["base_long_cycle_potential_score"].clip(-5, 10)

    # V11.1：大周期高度与买点质量轻量闸门。
    # 这里只做全市场低成本粗筛：不跑完整月线闭环，只识别“月线/年内偏高、远离防守位、空间不足”的不敢买问题。
    df["base_defense_level"] = df[["key_level_base", "ma20", "ma60", "ma120"]].max(axis=1).fillna(0)
    df["base_defense_dist"] = (df["close"] / df["base_defense_level"].replace(0, np.nan) - 1).fillna(0)
    df.loc[df["base_defense_dist"] < 0, "base_defense_dist"] = 0
    df["base_target_dist"] = df["near_pressure_dist"].where(df["near_pressure_dist"] > 0, df["mid_pressure_dist"]).fillna(0)
    df.loc[df["base_target_dist"] <= 0, "base_target_dist"] = df["overhead_pressure_dist"]
    df["base_risk_reward_ratio"] = (df["base_target_dist"] / df["base_defense_dist"].replace(0, np.nan)).fillna(0)

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

    # ===== V19.3 基础海选观察值子分 =====
    # 目的：基础海选层要“宽观察、严降权、强兜底”，防止真正有资金记忆/结构弹性的票被挡在深度评分200只之外。
    # 这些观察值不直接决定买入，只提升进入深度评分的概率；高位乱波动/放量滞涨会被风险活跃扣分抵消。

    base_rng = (df["high"] - df["low"]).replace(0, np.nan)
    df["base_upper_shadow_ratio"] = ((df["high"] - df[["open", "close"]].max(axis=1)) / base_rng).fillna(0)
    df["base_lower_shadow_ratio"] = ((df[["open", "close"]].min(axis=1) - df["low"]) / base_rng).fillna(0)
    df["base_close_pos"] = ((df["close"] - df["low"]) / base_rng).fillna(0)
    df["base_real_entity_pct"] = ((df["close"] - df["open"]).abs() / df["preclose"].replace(0, np.nan) * 100).fillna(0)

    df["base_big_bull_k"] = (df["pct_chg"] >= 3.0) & (df["base_close_pos"] >= 0.65) & (df["base_upper_shadow_ratio"] <= 0.35)
    # V25.3.1：海选活跃度不再依赖涨停数量，改用“7%及以上大阳线”作为主要攻击记忆。
    # 这里要求相对昨收涨幅>=7%、实体为阳线、收盘位置不弱，避免把高开低走/长上影滞涨误算为强攻击。
    df["base_big_bull7_k"] = (df["pct_chg"] >= 7.0) & (df["close"] > df["open"]) & (df["base_close_pos"] >= 0.60) & (df["base_upper_shadow_ratio"] <= 0.45)
    df["base_strong_close_k"] = (df["base_close_pos"] >= 0.80) & (df["close"] >= df["preclose"])
    df["base_up_gap"] = (df["low"] > df["high"].shift(1) * 1.003) & (df["close"] >= df["preclose"])
    df["base_gap_not_quick_fill"] = df["base_up_gap"].shift(1).fillna(False) & (df["low"] >= df["low"].shift(1) * 0.995)
    df["base_bullish_engulfing"] = (
        df["is_up"]
        & df["is_down"].shift(1).fillna(False)
        & (df["close"] >= df["open"].shift(1))
        & (df["open"] <= df["close"].shift(1) * 1.01)
        & (df["base_close_pos"] >= 0.60)
    )
    df["base_long_lower_repair"] = (df["base_lower_shadow_ratio"] >= 0.30) & (df["base_close_pos"] >= 0.55)
    df["base_upper_supply_k"] = (df["base_upper_shadow_ratio"] >= 0.35) & ((df["vr1"] >= 1.8) | (df["volr"] >= 2.0))
    df["base_high_volume_stall"] = ((df["vr1"] >= 2.5) | (df["volr"] >= 3.0)) & (df["pct_chg"] < 2.0) & (df["base_upper_shadow_ratio"] >= 0.25)
    df["base_break_fail_memory"] = (
        (df["high"] >= df["high_40_prev"] * 0.995)
        & (df["close"] < df["high_40_prev"])
        & (df["base_upper_shadow_ratio"] >= 0.25)
    )
    df["base_key_test_memory"] = (
        (df["high"] >= df["high_40_prev"] * 0.985)
        & (df["close"] >= df["low_40_prev"] * 1.01)
    )
    df["base_mid_reclaim_memory"] = (
        ((df["low"] <= df["ma20"] * 1.01) & (df["close"] >= df["ma20"]))
        | ((df["low"] <= df["ma60"] * 1.01) & (df["close"] >= df["ma60"]))
    )
    df["base_low_lift_memory"] = df["low"].rolling(5).min() >= df["low"].shift(5).rolling(5).min() * 0.995

    df["base_observe_fund_event_score"] = (
        (df["beiliang_count_60_base"].clip(0, 5) * 0.9)
        + (df["flat_volume_count_60_base"].clip(0, 4) * 1.4)
        + (df["base_limitup_hold_score"].clip(0, 3) * 1.1)
        + ((df["base_up_down_vol_ratio_60"] >= 1.05).astype(float) * 1.2)
        + ((df["base_up_down_vol_ratio_40"] >= 1.10).astype(float) * 0.8)
    ).clip(0, 10)

    df["base_observe_price_attack_score"] = (
        (df["base_up_gap"].rolling(60).sum().fillna(0).clip(0, 5) * 0.9)
        + (df["base_gap_not_quick_fill"].rolling(60).sum().fillna(0).clip(0, 4) * 0.8)
        + (df["base_big_bull_k"].rolling(60).sum().fillna(0).clip(0, 8) * 0.35)
        + (df["base_big_bull7_k"].rolling(100).sum().fillna(0).clip(0, 8) * 0.55)
        + (df["base_strong_close_k"].rolling(60).sum().fillna(0).clip(0, 10) * 0.25)
    ).clip(0, 8)

    df["base_observe_k_repair_score"] = (
        (df["base_bullish_engulfing"].rolling(60).sum().fillna(0).clip(0, 6) * 0.7)
        + (df["base_long_lower_repair"].rolling(60).sum().fillna(0).clip(0, 8) * 0.35)
        + (df["break_bottom_reclaim_base"].rolling(60).sum().fillna(0).clip(0, 4) * 0.8)
        + (df["base_mid_reclaim_memory"].rolling(60).sum().fillna(0).clip(0, 8) * 0.35)
    ).clip(0, 7)

    df["base_observe_structure_density_score"] = (
        (df["base_key_test_memory"].rolling(60).sum().fillna(0).clip(0, 8) * 0.45)
        + (df["platform20_break_base"].rolling(60).sum().fillna(0).clip(0, 4) * 0.9)
        + (df["platform40_break_base"].rolling(60).sum().fillna(0).clip(0, 3) * 1.0)
        + (df["base_mid_reclaim_memory"].rolling(60).sum().fillna(0).clip(0, 8) * 0.30)
        + (df["base_low_lift_memory"].astype(float) * 1.2)
    ).clip(0, 8)

    df["base_observe_active_memory_score"] = (
        (df["base_big_bull7_k"].rolling(100).sum().fillna(0).clip(0, 8) * 0.42)
        + (df["base_big_bull_k"].rolling(100).sum().fillna(0).clip(0, 10) * 0.18)
        + (df["beiliang_count_60_base"].clip(0, 5) * 0.35)
    ).clip(0, 5)

    # ========================= V25.3.1：基础海选层“7%大阳线活跃度/股性/攻击记忆”前置 =========================
    # 目标：全市场海选阶段先识别近期是否有足够资金攻击记忆和波动弹性。
    # 用户修正：海选活跃度不再把“涨停次数”作为主指标，改看最近100日“7%及以上大阳线数量”。
    # 理由：7%大阳线能覆盖非涨停但资金攻击强的票，避免模型过度偏向涨停板股。
    # 注意：活跃度是召回与过滤维度，不是买点；高位乱震、放量长上影、放量滞涨会被风险项抵消。
    df["base_limitup_count_100"] = df["limit_up_base"].rolling(100).sum().fillna(0)  # 保留观察字段，不作为海选活跃度主评分
    df["base_big_bull7_count_100"] = df["base_big_bull7_k"].rolling(100).sum().fillna(0)
    df["base_big_yang_count_100"] = ((df["pct_chg"] >= 5.0) & (df["close"] > df["open"]) & (df["base_close_pos"] >= 0.65)).rolling(100).sum().fillna(0)
    df["base_big_yin_count_100"] = ((df["pct_chg"] <= -5.0) & (df["close"] < df["open"]) & (df["base_close_pos"] <= 0.35)).rolling(100).sum().fillna(0)
    df["base_gap_count_100"] = df["base_up_gap"].rolling(100).sum().fillna(0)
    df["base_range20_pct"] = ((df["high"] - df["low"]) / df["close"].replace(0, np.nan)).rolling(20).mean().fillna(0)
    df["base_small_body_ratio_60"] = (((df["close"] - df["open"]).abs() / df["preclose"].replace(0, np.nan) <= 0.015) & (((df["high"] - df["low"]) / df["close"].replace(0, np.nan)) <= 0.035)).rolling(60).mean().fillna(0)

    df["base_activity_memory_score"] = 0.0
    # 主评分：100日7%大阳线数量。0-1次说明攻击记忆弱；4次以上说明股性/资金活跃度较好。
    df.loc[df["base_big_bull7_count_100"] <= 0, "base_activity_memory_score"] -= 2.5
    df.loc[df["base_big_bull7_count_100"] == 1, "base_activity_memory_score"] -= 0.8
    df.loc[df["base_big_bull7_count_100"].between(2, 3), "base_activity_memory_score"] += 1.0
    df.loc[df["base_big_bull7_count_100"].between(4, 5), "base_activity_memory_score"] += 2.8
    df.loc[df["base_big_bull7_count_100"] >= 6, "base_activity_memory_score"] += 4.2
    # 辅助评分：5%大阳、跳空、适度波动弹性。避免单纯无涨停但大阳攻击多的票被漏掉。
    df.loc[df["base_big_yang_count_100"] >= 4, "base_activity_memory_score"] += 0.8
    df.loc[df["base_big_yang_count_100"] >= 7, "base_activity_memory_score"] += 0.9
    df.loc[df["base_gap_count_100"] >= 3, "base_activity_memory_score"] += 0.8
    df.loc[df["base_range20_pct"].between(0.025, 0.060), "base_activity_memory_score"] += 1.0
    # 风险修正：黏性过强、阴线攻击更多、高位攻击过密/乱震，均降权。
    df.loc[(df["base_small_body_ratio_60"] >= 0.62) & (df["base_range20_pct"] < 0.022), "base_activity_memory_score"] -= 2.5
    df.loc[(df["base_big_yin_count_100"] >= df["base_big_yang_count_100"] + 2) & (df["base_big_yin_count_100"] >= 4), "base_activity_memory_score"] -= 2.5
    df.loc[(df["long_pos_250"] > 0.78) & (df["base_big_bull7_count_100"] >= 6), "base_activity_memory_score"] -= 2.0
    df.loc[(df["long_pos_250"] > 0.82) & (df["base_range20_pct"] > 0.065), "base_activity_memory_score"] -= 2.0
    df["base_activity_memory_score"] = df["base_activity_memory_score"].clip(-7, 8)
    df["base_activity_label"] = "活跃度一般"
    df.loc[df["base_activity_memory_score"] >= 4, "base_activity_label"] = "活跃度较好：近100日多次7%大阳攻击"
    df.loc[df["base_activity_memory_score"] <= -3, "base_activity_label"] = "活跃度不足：7%大阳攻击记忆弱或股性偏黏"
    df.loc[(df["long_pos_250"] > 0.78) & (df["base_activity_memory_score"] > 0), "base_activity_label"] = "活跃但位置偏高：防高位乱震"

    # 风险活跃扣分：防止高位乱波动、放量长上影、派发型活跃股进入深度候选前排.
    df["base_observe_risk_active_penalty"] = 0.0
    df.loc[df["base_upper_supply_k"].rolling(40).sum().fillna(0) >= 3, "base_observe_risk_active_penalty"] -= 2.5
    df.loc[df["base_high_volume_stall"].rolling(40).sum().fillna(0) >= 2, "base_observe_risk_active_penalty"] -= 3.0
    df.loc[df["base_break_fail_memory"].rolling(60).sum().fillna(0) >= 3, "base_observe_risk_active_penalty"] -= 2.0
    df.loc[(df["long_pos_250"] > 0.80) & (df["base_observe_price_attack_score"] >= 5), "base_observe_risk_active_penalty"] -= 2.0
    df.loc[(df["bias20"] > 0.18) | (df["bias60"] > 0.20), "base_observe_risk_active_penalty"] -= 1.5
    df["base_observe_risk_active_penalty"] = df["base_observe_risk_active_penalty"].clip(-8, 0)

    df["base_observation_subscore"] = (
        df["base_observe_fund_event_score"] * 0.28
        + df["base_observe_price_attack_score"] * 0.20
        + df["base_observe_k_repair_score"] * 0.18
        + df["base_observe_structure_density_score"] * 0.22
        + df["base_observe_active_memory_score"] * 0.08
        + df["base_activity_memory_score"].clip(0, 8) * 0.16
        + df["base_observe_risk_active_penalty"]
    ).clip(0, 12)

    df["base_observation_high_quality"] = (
        (df["base_observation_subscore"] >= 6.5)
        & (df["base_risk_penalty"] > -12)
        & (df["long_pos_250"] <= 0.75)
        & (df["base_trade_quality_score"] >= -2)
    )

    # ===== V23.3 大级别吸收后的日线爆发前夜基础召回通道 =====
    # 目标：在基础海选层提前召回“年/月/长周期供应吸收完成、日线启动前波动压缩、平量增多、
    # 重心抬高、攻击记忆增加、温和放量初启动”的股票。
    # 定位：只提高进入深度评分池概率，不直接等同正式推荐；深度层仍需压力带/回踩/防守位/RR/风险确认。

    # 1）长周期/大级别背景代理分 0~6：用日线长窗口低点抬高、MA250、量能/成交额中枢抬升。
    vol_mean_60 = df["volume"].rolling(60).mean()
    vol_mean_250 = df["volume"].rolling(250).mean()
    amount_mean_60 = df["amount"].rolling(60).mean() if "amount" in df.columns else df["volume"].rolling(60).mean() * df["close"].rolling(60).mean()
    amount_mean_250 = df["amount"].rolling(250).mean() if "amount" in df.columns else df["volume"].rolling(250).mean() * df["close"].rolling(250).mean()
    df["base_exeve_big_cycle_score"] = 0.0
    df.loc[(df["low"].rolling(120).min() > df["low"].shift(120).rolling(120).min() * 0.98), "base_exeve_big_cycle_score"] += 1.5
    df.loc[(df["close"].rolling(60).median() > df["close"].shift(60).rolling(60).median() * 1.02), "base_exeve_big_cycle_score"] += 1.0
    df.loc[(df["ma250"] > df["ma250"].shift(60) * 0.98) & (df["close"] >= df["ma250"] * 0.95), "base_exeve_big_cycle_score"] += 1.0
    df.loc[(vol_mean_60 / vol_mean_250.replace(0, np.nan) >= 1.10), "base_exeve_big_cycle_score"] += 1.0
    df.loc[(amount_mean_60 / amount_mean_250.replace(0, np.nan) >= 1.10), "base_exeve_big_cycle_score"] += 0.9
    df.loc[(df["base_activity_memory_score"] >= 3) & (df["long_pos_250"] <= 0.75), "base_exeve_big_cycle_score"] += 0.6
    df["base_exeve_big_cycle_score"] = df["base_exeve_big_cycle_score"].clip(0, 6)

    # 2）日线波动压缩 0~5：ATR/振幅下降、长阴减少、无放量破位长阴。
    tr1 = (df["high"] - df["low"]).abs()
    tr2 = (df["high"] - df["close"].shift(1)).abs()
    tr3 = (df["low"] - df["close"].shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    df["base_atr20_pct"] = (tr.rolling(20).mean() / df["close"].replace(0, np.nan)).fillna(0)
    df["base_atr60_pct"] = (tr.rolling(60).mean() / df["close"].replace(0, np.nan)).fillna(0)
    df["base_amp20_mean"] = ((df["high"] - df["low"]) / df["close"].replace(0, np.nan)).rolling(20).mean().fillna(0)
    df["base_amp60_mean"] = ((df["high"] - df["low"]) / df["close"].replace(0, np.nan)).rolling(60).mean().fillna(0)
    df["base_long_bear_k"] = (df["pct_chg"] <= -4.0) & (df["base_close_pos"] <= 0.35)
    df["base_heavy_break_bear_k"] = df["base_long_bear_k"] & ((df["vr1"] >= 1.8) | (df["volr"] >= 2.0))
    df["base_exeve_volatility_compression_score"] = 0.0
    df.loc[(df["base_atr20_pct"] > 0) & (df["base_atr60_pct"] > 0) & (df["base_atr20_pct"] <= df["base_atr60_pct"] * 0.85), "base_exeve_volatility_compression_score"] += 2.0
    df.loc[(df["base_amp20_mean"] > 0) & (df["base_amp60_mean"] > 0) & (df["base_amp20_mean"] <= df["base_amp60_mean"] * 0.85), "base_exeve_volatility_compression_score"] += 1.5
    df.loc[(df["base_long_bear_k"].rolling(20).sum().fillna(0) <= df["base_long_bear_k"].shift(20).rolling(20).sum().fillna(0)), "base_exeve_volatility_compression_score"] += 1.0
    df.loc[df["base_heavy_break_bear_k"].rolling(20).sum().fillna(0) == 0, "base_exeve_volatility_compression_score"] += 0.5
    df["base_exeve_volatility_compression_score"] = df["base_exeve_volatility_compression_score"].clip(0, 5)

    # 3）好平量/量能稳定 0~5：量能CV下降、围绕均量窄幅波动、极端量柱减少；必须结合平台/重心抬升背景。
    vol_cv20 = (df["volume"].rolling(20).std() / df["volume"].rolling(20).mean().replace(0, np.nan)).fillna(0)
    vol_cv60 = (df["volume"].rolling(60).std() / df["volume"].rolling(60).mean().replace(0, np.nan)).fillna(0)
    vol_ma10 = df["volume"].rolling(10).mean()
    df["base_volume_near_ma10"] = ((df["volume"] / vol_ma10.replace(0, np.nan) - 1).abs() <= 0.18)
    df["base_extreme_volume_k"] = (df["vr1"] >= 3.0) | (df["volr"] >= 4.0)
    df["base_exeve_flat_volume_score"] = 0.0
    df.loc[(vol_cv20 > 0) & (vol_cv60 > 0) & (vol_cv20 <= vol_cv60 * 0.80), "base_exeve_flat_volume_score"] += 2.0
    df.loc[df["base_volume_near_ma10"].rolling(20).sum().fillna(0) >= 9, "base_exeve_flat_volume_score"] += 1.5
    df.loc[df["base_extreme_volume_k"].rolling(20).sum().fillna(0) <= 1, "base_exeve_flat_volume_score"] += 0.8
    df.loc[((df["amp40"] <= 0.25) | (df["base_low_lift_memory"])) & (df["close"] >= df["ma20"] * 0.97), "base_exeve_flat_volume_score"] += 0.7
    df["base_exeve_flat_volume_score"] = df["base_exeve_flat_volume_score"].clip(0, 5)

    # 4）价格重心抬高/回撤变浅 0~5。
    close_med20 = df["close"].rolling(20).median()
    close_med20_prev = df["close"].shift(20).rolling(20).median()
    low20 = df["low"].rolling(20).min()
    low20_prev = df["low"].shift(20).rolling(20).min()
    dd20 = (df["close"] / df["high"].rolling(20).max().replace(0, np.nan) - 1).fillna(0).abs()
    dd60 = (df["close"] / df["high"].rolling(60).max().replace(0, np.nan) - 1).fillna(0).abs()
    df["base_exeve_center_lift_score"] = 0.0
    df.loc[(low20 >= low20_prev * 0.995), "base_exeve_center_lift_score"] += 2.0
    df.loc[(close_med20 >= close_med20_prev * 1.01), "base_exeve_center_lift_score"] += 1.5
    df.loc[(dd20 <= dd60 * 0.75) & (dd60 > 0), "base_exeve_center_lift_score"] += 1.0
    df.loc[((df["low"] >= df["ma20"] * 0.97) | (df["low"] >= df["low_40_prev"] * 0.98)), "base_exeve_center_lift_score"] += 0.5
    df["base_exeve_center_lift_score"] = df["base_exeve_center_lift_score"].clip(0, 5)

    # 5）攻击记忆密度 0~4：中阳/强收盘/小突破试盘增多，同时不能快速回吐。
    df["base_mid_bull_k"] = (df["pct_chg"] >= 2.0) & (df["base_close_pos"] >= 0.60) & (df["base_upper_shadow_ratio"] <= 0.40)
    mid_bull_recent = df["base_mid_bull_k"].rolling(60).sum().fillna(0)
    mid_bull_prev = df["base_mid_bull_k"].shift(60).rolling(60).sum().fillna(0)
    attack_not_giveback = (df["base_mid_bull_k"].shift(1).fillna(False) & (df["close"] >= df["close"].shift(1) * 0.97))
    small_probe = (df["high"] >= df["high_40_prev"] * 0.985) & (df["close"] >= df["close"].shift(1) * 0.98)
    df["base_exeve_attack_memory_score"] = 0.0
    df.loc[mid_bull_recent >= np.maximum(2, mid_bull_prev + 1), "base_exeve_attack_memory_score"] += 1.5
    df.loc[attack_not_giveback.rolling(60).sum().fillna(0) >= 2, "base_exeve_attack_memory_score"] += 1.0
    df.loc[df["base_up_down_vol_ratio_60"] >= 1.05, "base_exeve_attack_memory_score"] += 1.0
    df.loc[small_probe.rolling(60).sum().fillna(0) >= 2, "base_exeve_attack_memory_score"] += 0.5
    df["base_exeve_attack_memory_score"] = df["base_exeve_attack_memory_score"].clip(0, 4)

    # 6）启动初期量价质量 0~5：温和放量、收盘强、MACD零轴附近粘合后扩张，而不是高位极端爆量。
    ema12 = df["close"].ewm(span=12, adjust=False).mean()
    ema26 = df["close"].ewm(span=26, adjust=False).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9, adjust=False).mean()
    macd_hist = (dif - dea) * 2
    macd_near_zero_before = (dif.shift(5).abs() / df["close"].shift(5).replace(0, np.nan) <= 0.025)
    macd_expanding = (dif > dea) & (dif > dif.shift(3)) & (macd_hist > macd_hist.shift(3))
    mild_volume_start = ((df["vr1"] >= 1.15) & (df["vr1"] <= 2.5)) | ((df["volr"] >= 1.10) & (df["volr"] <= 3.0))
    first_stage_break = df["platform20_break_base"] | df["platform40_break_base"] | ((df["close"] > df["high_20_prev"] * 1.003) & (df["amp20"] <= 0.18))
    df["base_exeve_launch_quality_score"] = 0.0
    df.loc[first_stage_break & mild_volume_start, "base_exeve_launch_quality_score"] += 1.5
    df.loc[df["base_close_pos"] >= 0.70, "base_exeve_launch_quality_score"] += 1.0
    df.loc[macd_near_zero_before & macd_expanding, "base_exeve_launch_quality_score"] += 1.0
    df.loc[mild_volume_start & (~df["base_extreme_volume_k"]), "base_exeve_launch_quality_score"] += 1.0
    df.loc[(df["close"] >= df["high_20_prev"] * 0.99) & (df["close"] >= df["ma20"] * 0.98), "base_exeve_launch_quality_score"] += 0.5
    df["base_exeve_launch_quality_score"] = df["base_exeve_launch_quality_score"].clip(0, 5)

    df["base_explosion_eve_score_raw"] = (
        df["base_exeve_big_cycle_score"]
        + df["base_exeve_volatility_compression_score"]
        + df["base_exeve_flat_volume_score"]
        + df["base_exeve_center_lift_score"]
        + df["base_exeve_attack_memory_score"]
        + df["base_exeve_launch_quality_score"]
    ).clip(0, 30)

    # 追高/过热修正：爆发前夜模型要抓“启动前/初启动”，不是右侧暴涨后。
    df["base_explosion_eve_penalty"] = 0.0
    df.loc[(df["bias20"] > 0.15) | (df["bias60"] > 0.22), "base_explosion_eve_penalty"] -= 3.0
    df.loc[(df["long_pos_250"] > 0.88) & (df["bias20"] > 0.10), "base_explosion_eve_penalty"] -= 2.0
    df.loc[(df["base_rsi"] > 82) | (df["base_cci"] > 260), "base_explosion_eve_penalty"] -= 2.0
    df.loc[df["base_high_volume_stall"].rolling(20).sum().fillna(0) >= 1, "base_explosion_eve_penalty"] -= 1.5
    # V23.3-OPT：二次闸门。爆发前夜必须是“压缩 + 平量 + 重心 + 轻触发”的组合；
    # 只有压缩/平量，没有启动触发或攻击记忆，不应把基础总分推到前排。
    no_exeve_trigger = (df["base_exeve_launch_quality_score"] < 1.0) & (df["base_exeve_attack_memory_score"] < 1.0)
    weak_exeve_context = (df["base_exeve_center_lift_score"] < 2.0) | (df["base_exeve_big_cycle_score"] < 3.0)
    high_position_no_break = (df["long_pos_250"] > 0.75) & (~first_stage_break)
    df.loc[no_exeve_trigger, "base_explosion_eve_penalty"] -= 2.0
    df.loc[weak_exeve_context, "base_explosion_eve_penalty"] -= 1.5
    df.loc[high_position_no_break, "base_explosion_eve_penalty"] -= 2.0
    df.loc[df["base_observe_risk_active_penalty"] <= -4, "base_explosion_eve_penalty"] -= 2.0

    df["base_channel_explosion_eve_score"] = (df["base_explosion_eve_score_raw"] + df["base_explosion_eve_penalty"]).clip(0, 30)
    # 未触发/弱上下文/主动风险明显的压缩票只保留召回价值，不允许靠该通道独自抬升到高优先级。
    df.loc[no_exeve_trigger, "base_channel_explosion_eve_score"] = df.loc[no_exeve_trigger, "base_channel_explosion_eve_score"].clip(upper=18)
    df.loc[weak_exeve_context, "base_channel_explosion_eve_score"] = df.loc[weak_exeve_context, "base_channel_explosion_eve_score"].clip(upper=20)
    df.loc[df["base_observe_risk_active_penalty"] <= -4, "base_channel_explosion_eve_score"] = df.loc[df["base_observe_risk_active_penalty"] <= -4, "base_channel_explosion_eve_score"].clip(upper=16)

    df["base_explosion_eve_valid"] = (
        (df["base_channel_explosion_eve_score"] >= 20)
        & (df["base_exeve_big_cycle_score"] >= 3)
        & (df["base_exeve_volatility_compression_score"] >= 2)
        & (df["base_exeve_flat_volume_score"] >= 2)
        & (df["base_exeve_center_lift_score"] >= 2)
        & ((df["base_exeve_launch_quality_score"] >= 1) | (df["base_exeve_attack_memory_score"] >= 1.5))
        & (df["base_risk_penalty"] > -12)
        & (df["base_observe_risk_active_penalty"] > -4)
        & (df["bias20"] <= 0.16)
        & (df["bias60"] <= 0.18)
        & (df["long_pos_250"] <= 0.78)
    )
    df["base_explosion_eve_desc"] = ""
    df.loc[df["base_channel_explosion_eve_score"] >= 17, "base_explosion_eve_desc"] = "大级别吸收后日线波动压缩/平量/重心抬高（召回观察）"
    df.loc[df["base_explosion_eve_valid"], "base_explosion_eve_desc"] = "爆发前夜有效：大级别吸收+日线压缩平量+攻击记忆增强"



    # ========================= V24：基础层“供应吸收召回”轻量版 =========================
    # 目标：识别“大周期历史供应区被反复测试/消化、当前接近核心上沿”的股票，把它们拉进深度池。
    # 注意：这里只做召回增强，不直接等同买点；深度层再确认压力带、突破K、承接、RR与风险反证。
    df["base_supply_core_upper"] = df["overhead_high_250"].where(df["overhead_high_250"] > 0, df["overhead_high_120"])
    df["base_supply_dist_to_upper"] = (df["base_supply_core_upper"] / df["close"].replace(0, np.nan) - 1).fillna(0)
    df.loc[df["base_supply_dist_to_upper"] < 0, "base_supply_dist_to_upper"] = 0
    df["base_supply_band_compact_pct"] = ((df["overhead_high_250"] - df["overhead_high_120"]) / df["close"].replace(0, np.nan)).abs().fillna(0)
    df["base_supply_touch_count_250"] = 0.0
    df["base_supply_fake_break_count_250"] = 0.0
    df["base_supply_recent_repair_count_120"] = 0.0
    for _i in range(len(df)):
        if _i not in _active_base_indices:
            continue
        _upper = safe_float(df["base_supply_core_upper"].iloc[_i])
        if _upper <= 0:
            continue
        _past = df.iloc[max(0, _i - 250):_i]
        if _past.empty:
            continue
        _touch = ((_past["high"] >= _upper * 0.94) & (_past["high"] <= _upper * 1.015)).sum()
        _fake = ((_past["high"] >= _upper * 0.995) & (_past["close"] <= _upper * 0.985)).sum()
        _recent = _past.tail(120)
        _repair = ((_recent["high"] >= _upper * 0.94) & (_recent["close"] >= _upper * 0.90)).sum()
        df.iat[_i, df.columns.get_loc("base_supply_touch_count_250")] = float(_touch)
        df.iat[_i, df.columns.get_loc("base_supply_fake_break_count_250")] = float(_fake)
        df.iat[_i, df.columns.get_loc("base_supply_recent_repair_count_120")] = float(_repair)

    df["base_supply_pressure_clarity_score"] = 0.0
    df.loc[(df["base_supply_dist_to_upper"] > 0) & (df["base_supply_dist_to_upper"] <= 0.12), "base_supply_pressure_clarity_score"] += 3.0
    df.loc[(df["base_supply_dist_to_upper"] > 0) & (df["base_supply_dist_to_upper"] <= 0.06), "base_supply_pressure_clarity_score"] += 2.0
    df.loc[df["base_supply_band_compact_pct"] <= 0.08, "base_supply_pressure_clarity_score"] += 2.0
    df.loc[df["base_supply_touch_count_250"] >= 2, "base_supply_pressure_clarity_score"] += 2.0
    df.loc[df["base_supply_touch_count_250"] >= 4, "base_supply_pressure_clarity_score"] += 1.5
    df.loc[df["base_supply_fake_break_count_250"] >= 1, "base_supply_pressure_clarity_score"] += 1.0
    df["base_supply_pressure_clarity_score"] = df["base_supply_pressure_clarity_score"].clip(0, 10)

    # 供应吸收/回撤承接：冲高后没有跌死、平台低点/中枢抬高、量价没有派发式破坏。
    df["base_supply_absorb_context_score"] = 0.0
    df.loc[df["base_supply_touch_count_250"] >= 3, "base_supply_absorb_context_score"] += 2.5
    df.loc[df["base_supply_recent_repair_count_120"] >= 5, "base_supply_absorb_context_score"] += 1.5
    df.loc[df["close"] >= df["ma120"] * 0.98, "base_supply_absorb_context_score"] += 1.5
    df.loc[df["close"] >= df["ma250"] * 0.95, "base_supply_absorb_context_score"] += 1.0
    df.loc[(df["low"].rolling(60).min() >= df["low"].shift(60).rolling(60).min() * 0.98), "base_supply_absorb_context_score"] += 1.5
    df.loc[(df["close"].rolling(20).median() >= df["close"].shift(40).rolling(20).median() * 0.98), "base_supply_absorb_context_score"] += 1.0
    df.loc[df["base_high_volume_stall"].rolling(60).sum().fillna(0) >= 2, "base_supply_absorb_context_score"] -= 2.0
    df.loc[(df["base_up_down_vol_ratio_60"] < 0.90) & (df["base_up_down_vol_ratio_40"] < 0.90), "base_supply_absorb_context_score"] -= 2.0
    df["base_supply_absorb_context_score"] = df["base_supply_absorb_context_score"].clip(0, 9)

    # 量能中枢与平台压缩：只作为背景质量，避免和突破K/倍量单独重复大加分。
    df["base_supply_volume_platform_score"] = 0.0
    df.loc[df["base_volume_carry_score"] >= 5, "base_supply_volume_platform_score"] += 2.0
    df.loc[df["base_volume_carry_score"] >= 8, "base_supply_volume_platform_score"] += 1.5
    df.loc[(df["base_up_down_vol_ratio_40"] >= 1.10) & (df["base_up_down_vol_ratio_60"] >= 1.05), "base_supply_volume_platform_score"] += 2.0
    df.loc[df["flat_volume_count_60_base"] >= 1, "base_supply_volume_platform_score"] += 1.0
    df.loc[df["beiliang_count_60_base"] >= 2, "base_supply_volume_platform_score"] += 1.0
    df["base_supply_volume_platform_score"] = df["base_supply_volume_platform_score"].clip(0, 7)

    df["base_supply_compression_trigger_score"] = 0.0
    df.loc[(df["amp40"] <= 0.22) & (df["amp40"] > 0), "base_supply_compression_trigger_score"] += 1.5
    df.loc[(df["amp20"] <= 0.14) & (df["amp20"] > 0), "base_supply_compression_trigger_score"] += 1.0
    df.loc[(df["close"] >= df["base_supply_core_upper"] * 0.92) & (df["base_supply_core_upper"] > 0), "base_supply_compression_trigger_score"] += 1.5
    df.loc[(df["close"] >= df["base_supply_core_upper"] * 0.97) & (df["base_supply_core_upper"] > 0), "base_supply_compression_trigger_score"] += 1.0
    df.loc[(df["platform20_break_base"] | df["platform40_break_base"] | (df["break_rate"] >= 0.005)) & (df["pos"] >= 0.65), "base_supply_compression_trigger_score"] += 1.5
    df["base_supply_compression_trigger_score"] = df["base_supply_compression_trigger_score"].clip(0, 6)

    df["base_supply_absorption_score_raw"] = (
        df["base_supply_pressure_clarity_score"]
        + df["base_supply_absorb_context_score"]
        + df["base_supply_volume_platform_score"]
        + df["base_supply_compression_trigger_score"]
    ).clip(0, 30)
    df["base_supply_absorption_penalty"] = 0.0
    df.loc[(df["bias20"] > 0.16) | (df["bias60"] > 0.22), "base_supply_absorption_penalty"] -= 2.5
    df.loc[(df["base_rsi"] > 82) | (df["base_cci"] > 260), "base_supply_absorption_penalty"] -= 2.0
    df.loc[(df["base_supply_dist_to_upper"] > 0.18) | (df["base_supply_core_upper"] <= 0), "base_supply_absorption_penalty"] -= 3.0
    df.loc[df["base_observe_risk_active_penalty"] <= -4, "base_supply_absorption_penalty"] -= 2.0
    df["base_channel_supply_absorption_score"] = (df["base_supply_absorption_score_raw"] + df["base_supply_absorption_penalty"]).clip(0, 30)

    no_supply_trigger = df["base_supply_compression_trigger_score"] < 2.0
    weak_supply_pressure = df["base_supply_pressure_clarity_score"] < 4.0
    df.loc[no_supply_trigger, "base_channel_supply_absorption_score"] = df.loc[no_supply_trigger, "base_channel_supply_absorption_score"].clip(upper=18)
    df.loc[weak_supply_pressure, "base_channel_supply_absorption_score"] = df.loc[weak_supply_pressure, "base_channel_supply_absorption_score"].clip(upper=17)
    df.loc[df["base_risk_penalty"] <= -12, "base_channel_supply_absorption_score"] = df.loc[df["base_risk_penalty"] <= -12, "base_channel_supply_absorption_score"].clip(upper=14)
    df["base_supply_absorption_valid"] = (
        (df["base_channel_supply_absorption_score"] >= 20)
        & (df["base_supply_pressure_clarity_score"] >= 4)
        & (df["base_supply_absorb_context_score"] >= 3)
        & (df["base_supply_compression_trigger_score"] >= 2)
        & (df["base_supply_dist_to_upper"] <= 0.15)
        & (df["base_risk_penalty"] > -12)
        & (df["base_observe_risk_active_penalty"] > -4)
    )
    df["base_supply_absorption_desc"] = ""
    df.loc[df["base_channel_supply_absorption_score"] >= 17, "base_supply_absorption_desc"] = "历史供应区反复测试/吸收，当前接近核心上沿（基础召回）"
    df.loc[df["base_supply_absorption_valid"], "base_supply_absorption_desc"] = "供应吸收有效召回：压力清晰+多次测试+量能平台+临近触发"

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
        + df["base_observation_subscore"] * 0.90
        + df["base_activity_memory_score"].clip(0, 8) * BASE_ACTIVITY_TOTAL_WEIGHT
        + df["base_channel_explosion_eve_score"] * BASE_EXPLOSION_EVE_TOTAL_WEIGHT
        + df["base_channel_supply_absorption_score"] * BASE_SUPPLY_ABSORB_TOTAL_WEIGHT
        + df["base_risk_penalty"] * 1.20
    ).clip(0, 100)

    # 强势但交易质量差的票只能进入观察，不让其仅凭倍量/强阳挤占前排。
    poor_trade_quality_base = (df["base_trade_quality_score"] < 0) | ((df["base_risk_reward_ratio"] > 0) & (df["base_risk_reward_ratio"] < 1.2))
    high_attack_without_structure_base = (df["base_attack_quality_score"] >= 26) & (df["base_structure_potential_score"] < 6) & (df["base_monthly_height_proxy_score"] < 2)
    df.loc[poor_trade_quality_base & high_attack_without_structure_base, "base_total_score"] = df.loc[poor_trade_quality_base & high_attack_without_structure_base, "base_total_score"].clip(upper=62)

    # 兼容旧字段：base_score 改为V10基础总分；原模型折算另存 score_base_model_legacy。
    df["base_score"] = df["base_total_score"]

    # V19.2基础候选分桶：从旧“战法/表现桶”升级为“机会假设桶”。
    # 注意：交易质量不再作为独立桶，而是全局排序/过滤层；压力带突破、倍量、时间窗口等都只是评分项。
    # 目标：让深度评分200只覆盖回踩确认、资金承接、低位修复、爆发前夜等V19偏好的机会，而不是只追当天健康攻击。
    v19_low_trigger_cond = (
        (df["long_pos_250"] <= 0.60)
        & ((df["short_ma_volume_entity_start"]) | (df["platform20_break_base"]) | (df["platform40_break_base"]) | (df["just_cross_ma120"]) | (df["just_cross_ma250"]))
        & (df["base_risk_penalty"] > -12)
    )
    v19_pullback_confirm_cond = (
        (df["base_trade_quality_score"] >= 7)
        & (df["base_defense_dist"] <= 0.08)
        & ((df["base_volume_carry_score"] >= 5) | (df["base_structure_potential_score"] >= 5))
        & (df["base_risk_penalty"] > -12)
    )
    v19_multi_cycle_repair_cond = (
        ((df["base_long_cycle_potential_score"] >= 5) | (df["base_monthly_height_proxy_score"] >= 7) | (df["just_cross_ma120"]) | (df["just_cross_ma250"]))
        & (df["long_pos_250"] <= 0.65)
        & (df["base_risk_penalty"] > -14)
    )
    v19_capital_carry_cond = (
        ((df["base_volume_carry_score"] >= 8) | (df["base_limitup_hold_score"] >= 2))
        & (df["base_attack_quality_score"] < 30)
        & (df["base_risk_penalty"] > -12)
    )
    v19_compression_cond = (
        ((df["base_structure_potential_score"] >= 5) | (df["base_observe_structure_density_score"] >= 5) | ((df["amp40"] <= 0.22) & (df["close"] >= df["high_40_prev"] * 0.96)))
        & (df["base_attack_quality_score"] < 26)
        & (df["base_risk_penalty"] > -12)
    )
    v233_explosion_eve_cond = (
        df["base_explosion_eve_valid"]
        & (df["base_attack_quality_score"] < 30)
        & (df["long_pos_250"] <= 0.78)
    )
    v24_supply_absorb_cond = (
        df["base_supply_absorption_valid"]
        & (df["base_attack_quality_score"] < 32)
        & (df["base_supply_dist_to_upper"] <= 0.15)
    )
    v19_structure_break_cond = (
        ((df["platform40_break_base"]) | (df["platform20_break_base"]) | (df["base_fibo_second_confirm_score"] >= 6) | (df["base_structure_potential_score"] >= 10))
        & (df["base_risk_penalty"] > -14)
    )
    v19_observation_fallback_cond = (
        df["base_observation_high_quality"]
        & ((df["base_observe_fund_event_score"] >= 5) | (df["base_observe_structure_density_score"] >= 5) | (df["base_observe_k_repair_score"] >= 4))
    )
    v24_active_quality_cond = (
        (df["base_activity_memory_score"] >= 4)
        & (df["base_observe_risk_active_penalty"] > -4)
        & (df["base_risk_penalty"] > -12)
        & (df["long_pos_250"] <= 0.75)
        & ((df["base_structure_potential_score"] >= 4) | (df["base_volume_carry_score"] >= 5) | (df["base_observation_subscore"] >= 5.5))
    )
    v19_active_watch_cond = (
        ((df["pct_chg"] >= 7) | df["limit_up_base"] | (df["entity_pct"] >= 7))
        & ((df["base_risk_penalty"] <= -4) | (df["base_trade_quality_score"] < 0) | (df["long_pos_250"] > 0.75))
    )

    # 默认放入低位强启动/关键位触发，是因为它仍保留原健康攻击入口；随后按更强机会假设覆盖。
    df["base_bucket"] = "低位强启动/关键位触发"
    df.loc[v19_multi_cycle_repair_cond, "base_bucket"] = "大周期修复/多周期共振"
    df.loc[v19_capital_carry_cond, "base_bucket"] = "资金承接/倍量后平量/台阶推进"
    df.loc[v19_compression_cond, "base_bucket"] = "平台蓄势/爆发前夜/左侧钝化"
    df.loc[v233_explosion_eve_cond, "base_bucket"] = "大级别吸收/日线爆发前夜"
    df.loc[v24_supply_absorb_cond, "base_bucket"] = "供应吸收/供需压力带临界"
    df.loc[v19_structure_break_cond, "base_bucket"] = "结构突破/压力支撑带突破"
    df.loc[v19_observation_fallback_cond, "base_bucket"] = "观察值兜底/资金记忆"
    df.loc[v24_active_quality_cond & df["base_bucket"].eq("低位强启动/关键位触发"), "base_bucket"] = "活跃股性/资金攻击记忆"
    df.loc[v19_pullback_confirm_cond, "base_bucket"] = "回踩确认/二买候选"
    df.loc[v19_low_trigger_cond & (df["base_bucket"].eq("低位强启动/关键位触发")), "base_bucket"] = "低位强启动/关键位触发"
    df.loc[v19_active_watch_cond, "base_bucket"] = "活跃股性/强势观察"

    # 排序分：保留原base_total_score，但把V19偏好的“确认、承接、修复、蓄势”作为排序增强。
    df["base_bucket_rank_score"] = df["base_total_score"].copy()
    df.loc[df["base_bucket"].eq("回踩确认/二买候选"), "base_bucket_rank_score"] += 5
    df.loc[df["base_bucket"].eq("资金承接/倍量后平量/台阶推进"), "base_bucket_rank_score"] += 4
    df.loc[df["base_bucket"].eq("大周期修复/多周期共振"), "base_bucket_rank_score"] += 4
    df.loc[df["base_bucket"].eq("平台蓄势/爆发前夜/左侧钝化"), "base_bucket_rank_score"] += 3
    df.loc[df["base_bucket"].eq("大级别吸收/日线爆发前夜"), "base_bucket_rank_score"] += BASE_EXPLOSION_EVE_BUCKET_BONUS
    df.loc[df["base_bucket"].eq("供应吸收/供需压力带临界"), "base_bucket_rank_score"] += BASE_SUPPLY_ABSORB_BUCKET_BONUS
    df.loc[df["base_bucket"].eq("结构突破/压力支撑带突破"), "base_bucket_rank_score"] += 3
    df.loc[df["base_bucket"].eq("观察值兜底/资金记忆"), "base_bucket_rank_score"] += 3
    df.loc[df["base_bucket"].eq("低位强启动/关键位触发"), "base_bucket_rank_score"] += 2
    df.loc[df["base_bucket"].eq("活跃股性/资金攻击记忆"), "base_bucket_rank_score"] += 2
    df.loc[df["base_bucket"].eq("活跃股性/强势观察"), "base_bucket_rank_score"] -= 5
    df["base_bucket_rank_score"] += (df["base_observation_subscore"].clip(0, 12) * 0.32)
    df["base_bucket_rank_score"] += (df["base_activity_memory_score"].clip(0, 8) * BASE_ACTIVITY_RANK_WEIGHT)
    df["base_bucket_rank_score"] += (df["base_channel_explosion_eve_score"].clip(0, 30) * BASE_EXPLOSION_EVE_RANK_WEIGHT)
    df["base_bucket_rank_score"] += (df["base_channel_supply_absorption_score"].clip(0, 30) * BASE_SUPPLY_ABSORB_RANK_WEIGHT)
    df.loc[df["base_observe_risk_active_penalty"] <= -4, "base_bucket_rank_score"] -= 3

    # 交易质量不是分桶，但对所有桶统一生效。
    df.loc[df["base_trade_quality_score"] >= 10, "base_bucket_rank_score"] += 3
    df.loc[(df["base_risk_reward_ratio"] >= 1.5) & (df["base_risk_reward_ratio"] < 2.0), "base_bucket_rank_score"] += 2
    df.loc[df["base_risk_reward_ratio"] >= 2.0, "base_bucket_rank_score"] += 4
    df.loc[poor_trade_quality_base, "base_bucket_rank_score"] -= 5

    # 原有重要入口保留，但变成排序增强，不再单独形成旧桶。
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


def _resample_ohlcv(df, rule):
    rule = {"M": "ME", "Q": "QE", "Y": "YE"}.get(str(rule), rule)
    """把日线聚合成周/月/季K。只在深度层对候选股调用，避免全市场重复慢扫。"""
    if df is None or df.empty or "date" not in df.columns:
        return pd.DataFrame()
    d = df.copy()
    d["date"] = pd.to_datetime(d["date"], errors="coerce")
    d = d.dropna(subset=["date"]).sort_values("date")
    if d.empty:
        return pd.DataFrame()
    d = d.set_index("date")
    out = pd.DataFrame()
    out["open"] = d["open"].resample(rule).first()
    out["high"] = d["high"].resample(rule).max()
    out["low"] = d["low"].resample(rule).min()
    out["close"] = d["close"].resample(rule).last()
    out["volume"] = d["volume"].resample(rule).sum()
    out["amount"] = d["amount"].resample(rule).sum() if "amount" in d.columns else 0
    out = out.dropna(subset=["open", "high", "low", "close", "volume"]).reset_index()
    if not out.empty:
        out["date"] = out["date"].dt.strftime("%Y-%m-%d")
    return out


def _is_valid_max_volume_bull_candle(row):
    """
    最大量关键K必须是有效阳K：
    1）收盘大于开盘；2）必须有实体；3）实体长度不小于上下影线总和的一半。
    符合后，实体实底才可作为箱体底部，最高点才可作为箱体上沿/突破线。
    """
    op = safe_float(row.get("open", 0))
    cl = safe_float(row.get("close", 0))
    hi = safe_float(row.get("high", 0))
    lo = safe_float(row.get("low", 0))
    if op <= 0 or cl <= 0 or hi <= 0 or lo <= 0:
        return False
    if cl <= op:
        return False
    body = abs(cl - op)
    upper = max(0.0, hi - max(op, cl))
    lower = max(0.0, min(op, cl) - lo)
    shadows = upper + lower
    if body <= 0:
        return False
    # 实体不能太小：至少达到影线总长度的一半；极端长影小实体不画关键黄线。
    if shadows > 0 and body < shadows * 0.50:
        return False
    return True


def _find_valid_max_volume_bull_levels(period_df, tf_name, lookback=80):
    """在指定周期内寻找有效最大量阳K，并返回实底线和最高点。"""
    empty = {
        "valid": False,
        "timeframe": tf_name,
        "date": "",
        "floor": 0.0,
        "high": 0.0,
        "body_mid": 0.0,
        "body_top": 0.0,
        "volume": 0.0,
        "reason": "无有效最大量阳K",
    }
    if period_df is None or period_df.empty or len(period_df) < 8:
        return empty
    w = period_df.tail(lookback).copy().reset_index(drop=True)
    if w.empty or "volume" not in w.columns:
        return empty
    w["volume"] = pd.to_numeric(w["volume"], errors="coerce")
    w = w.dropna(subset=["volume", "open", "high", "low", "close"])
    if w.empty:
        return empty
    # 按成交量从大到小找第一根有效阳K，避免最大量是阴线/小实体长影时误画线。
    for _, row in w.sort_values("volume", ascending=False).iterrows():
        if not _is_valid_max_volume_bull_candle(row):
            continue
        op = safe_float(row.get("open", 0))
        cl = safe_float(row.get("close", 0))
        hi = safe_float(row.get("high", 0))
        floor = min(op, cl)
        top = max(op, cl)
        return {
            "valid": True,
            "timeframe": tf_name,
            "date": str(row.get("date", "")),
            "floor": float(floor),
            "high": float(hi),
            "body_mid": float((floor + top) / 2),
            "body_top": float(top),
            "volume": safe_float(row.get("volume", 0)),
            "reason": f"{tf_name}有效最大量阳K：实底{floor:.2f}，高点{hi:.2f}",
        }
    return empty


def _count_dynamic_pullback_segments(period_df, level, near_pct=0.055, reclaim_pct=0.985, leave_pct=0.07):
    """
    按“回踩段”统计，而不是写死回踩几根K。
    先站上线，再明显离开，再回到线附近并守住，算一次有效回踩段。
    连续贴线的多根K只算同一段。
    """
    if period_df is None or period_df.empty or level <= 0:
        return {"count": 0, "dates": [], "last_quality": "", "last_near": False, "failed": False}
    w = period_df.copy().reset_index(drop=True)
    required = ["open", "high", "low", "close", "volume"]
    for c in required:
        if c not in w.columns:
            return {"count": 0, "dates": [], "last_quality": "", "last_near": False, "failed": False}
        w[c] = pd.to_numeric(w[c], errors="coerce")
    w = w.dropna(subset=required).reset_index(drop=True)
    if w.empty:
        return {"count": 0, "dates": [], "last_quality": "", "last_near": False, "failed": False}

    above_seen = False
    left_zone = False
    in_test = False
    dates = []
    last_quality = ""
    failed = False
    vol_ma = w["volume"].rolling(5).mean()

    for i, row in w.iterrows():
        cl = safe_float(row["close"])
        lo = safe_float(row["low"])
        op = safe_float(row["open"])
        vol = safe_float(row["volume"])
        if cl >= level:
            above_seen = True
        if not above_seen:
            continue
        if cl >= level * (1 + leave_pct):
            left_zone = True
            in_test = False
        near = (lo <= level * (1 + near_pct)) and (cl >= level * reclaim_pct)
        hard_break = cl < level * 0.970
        big_down = (cl < op) and ((op - cl) / max(level, 1e-9) >= 0.04) and (pd.notna(vol_ma.iloc[i]) and vol > safe_float(vol_ma.iloc[i]) * 1.35)
        if hard_break and big_down:
            failed = True
            in_test = False
        if left_zone and near and not in_test and not (hard_break and big_down):
            dates.append(str(row.get("date", "")))
            in_test = True
            if cl >= level:
                last_quality = "收盘守住关键线"
            else:
                last_quality = "轻微跌破后收回观察"
        if not near and cl >= level * (1 + leave_pct * 0.6):
            in_test = False
    current = w.iloc[-1]
    last_near = bool((safe_float(current["low"]) <= level * (1 + near_pct)) and (safe_float(current["close"]) >= level * reclaim_pct))
    return {"count": len(dates), "dates": dates[-5:], "last_quality": last_quality, "last_near": last_near, "failed": failed}


def _daily_break_quality_against_level(df, level):
    """
    日线突破多周期关键高点的质量判断：影线突破不算。
    有效突破必须实体漂亮站上，或直接跳空越过，并且强收盘、上影短、量能健康。
    """
    if df is None or df.empty or level <= 0:
        return {"score": 0.0, "valid": False, "label": "无关键突破", "desc": ""}
    cur = df.iloc[-1]
    prev = df.iloc[-2] if len(df) >= 2 else cur
    op = safe_float(cur.get("open", 0))
    cl = safe_float(cur.get("close", 0))
    hi = safe_float(cur.get("high", 0))
    lo = safe_float(cur.get("low", 0))
    vol = safe_float(cur.get("volume", 0))
    prev_vol = safe_float(prev.get("volume", 0))
    if min(op, cl, hi, lo) <= 0:
        return {"score": 0.0, "valid": False, "label": "无效K线", "desc": ""}
    rng = max(hi - lo, 1e-9)
    body_bottom = min(op, cl)
    body_top = max(op, cl)
    body_len = max(body_top - body_bottom, 1e-9)
    close_pos = (cl - lo) / rng
    upper_shadow_ratio = (hi - body_top) / rng
    body_above_ratio = max(0.0, body_top - max(body_bottom, level)) / body_len
    vr1 = vol / prev_vol if prev_vol > 0 else 0.0
    vol_ma20 = safe_float(df["volume"].tail(20).mean()) if "volume" in df.columns and len(df) >= 20 else 0.0
    volr = vol / vol_ma20 if vol_ma20 > 0 else 0.0
    volume_ok = (vr1 >= 1.5 or volr >= 1.3) and (vr1 <= 3.5) and (volr <= 5.5)
    gap_break = op >= level * 1.003 and lo >= level * 0.995 and cl >= level * 1.003 and close_pos >= 0.70 and upper_shadow_ratio <= 0.28 and volume_ok
    entity_break = cl >= level * 1.005 and body_above_ratio >= 0.50 and close_pos >= 0.78 and upper_shadow_ratio <= 0.25 and volume_ok and cl > op
    strong_entity = entity_break and body_above_ratio >= 0.70 and close_pos >= 0.85 and upper_shadow_ratio <= 0.18
    wick_probe = hi >= level and (cl < level or body_above_ratio < 0.50 or upper_shadow_ratio > 0.32)
    score = 0.0
    label = "未突破"
    desc = ""
    valid = False
    if gap_break:
        score = 10.0
        label = "跳空越过关键高点"
        desc = f"日线跳空越过{level:.2f}，收盘仍站稳，量能健康"
        valid = True
    elif strong_entity:
        score = 11.0
        label = "实体漂亮突破关键高点"
        desc = f"日线实体大半站上{level:.2f}，收盘接近最高，量能健康"
        valid = True
    elif entity_break:
        score = 8.0
        label = "实体有效突破关键高点"
        desc = f"日线实体至少一半站上{level:.2f}，不是影线试探"
        valid = True
    elif wick_probe:
        score = -2.0
        label = "影线试探/假突破风险"
        desc = f"盘中摸到{level:.2f}但实体/收盘/上影不达标，不能按有效突破高分"
    return {"score": float(score), "valid": bool(valid), "label": label, "desc": desc}


def detect_multi_timeframe_key_structure(df, code=""):
    """
    V12统一多周期关键结构位系统。
    目的：保留凹口、最大量阳K实底/高点、箱体边界、回踩承接等逻辑，
    但把日/周/月/季同类关键位合并，避免重复扫描、重复打分。
    """
    empty = {
        "score_multi_tf_key_structure": 0.0,
        "score_multi_tf_break_quality": 0.0,
        "multi_tf_key_desc": "",
        "multi_tf_best_floor": 0.0,
        "multi_tf_best_high": 0.0,
        "multi_tf_best_timeframe": "",
        "multi_tf_pullback_count": 0,
        "multi_tf_pullback_stage": "",
        "multi_tf_high_break_label": "",
        "multi_tf_high_break_desc": "",
        "multi_tf_levels_json": "[]",
    }
    if df is None or len(df) < 120:
        return empty
    contexts = []
    tf_defs = [
        ("日线", df.copy().reset_index(drop=True), 250, 1.0),
        ("周线", _resample_ohlcv(df, "W-FRI"), 180, 1.5),
        ("月线", _resample_ohlcv(df, "ME"), MONTHLY_STRUCT_LOOKBACK_MONTHS, 2.2),
        ("季线", _resample_ohlcv(df, "QE"), 40, 2.8),
    ]
    cur_close = safe_float(df.iloc[-1].get("close", 0))
    for tf_name, pdf, lookback, weight in tf_defs:
        level = _find_valid_max_volume_bull_levels(pdf, tf_name, lookback=lookback)
        if not level.get("valid"):
            continue
        floor = safe_float(level.get("floor", 0))
        high = safe_float(level.get("high", 0))
        pull = _count_dynamic_pullback_segments(pdf, floor)
        brk = _daily_break_quality_against_level(df, high)
        floor_status_score = 0.0
        if floor > 0 and cur_close > 0:
            dist_floor = cur_close / floor - 1
            if cur_close >= floor and dist_floor <= 0.12:
                floor_status_score += 2.0 * weight
            elif -0.02 <= dist_floor < 0:
                floor_status_score += 1.0 * weight
            if pull.get("count", 0) >= 1:
                floor_status_score += min(4.0 * weight, (1.2 + 0.9 * pull.get("count", 0)) * weight)
            if pull.get("count", 0) >= 3 and pull.get("last_near", False):
                floor_status_score += 2.0 * weight
            if pull.get("failed", False):
                floor_status_score -= 2.0 * weight
        break_score = safe_float(brk.get("score", 0)) * weight
        total = max(0.0, floor_status_score + max(0.0, break_score))
        contexts.append({
            "timeframe": tf_name,
            "date": level.get("date", ""),
            "floor": floor,
            "high": high,
            "pullback_count": int(pull.get("count", 0)),
            "pullback_dates": pull.get("dates", []),
            "pullback_stage": "成熟回踩段" if pull.get("count", 0) >= 3 else ("二次跟踪" if pull.get("count", 0) == 2 else ("首次记录" if pull.get("count", 0) == 1 else "等待回踩")),
            "break_label": brk.get("label", ""),
            "break_desc": brk.get("desc", ""),
            "break_score": float(break_score),
            "score": float(total),
            "reason": level.get("reason", ""),
        })
    if not contexts:
        return empty
    contexts = sorted(contexts, key=lambda x: x.get("score", 0), reverse=True)
    best = contexts[0]
    # 同源合并：取最高级别关键结构为主，其他周期只给少量共振，不重复叠加。
    base_score = min(16.0, safe_float(best.get("score", 0)))
    resonance_count = sum(1 for x in contexts[1:] if safe_float(x.get("score", 0)) >= 3.0)
    resonance_bonus = min(4.0, resonance_count * 1.2)
    total_score = min(20.0, base_score + resonance_bonus)
    desc_parts = []
    for x in contexts[:3]:
        desc_parts.append(
            f"{x['timeframe']}最大量阳K实底{x['floor']:.2f}/高点{x['high']:.2f}，{x['pullback_stage']}，{x['break_label']}"
        )
    return {
        "score_multi_tf_key_structure": float(total_score),
        "score_multi_tf_break_quality": float(max([safe_float(x.get("break_score", 0)) for x in contexts] or [0.0])),
        "multi_tf_key_desc": "；".join(desc_parts),
        "multi_tf_best_floor": float(best.get("floor", 0.0)),
        "multi_tf_best_high": float(best.get("high", 0.0)),
        "multi_tf_best_timeframe": str(best.get("timeframe", "")),
        "multi_tf_pullback_count": int(best.get("pullback_count", 0)),
        "multi_tf_pullback_stage": str(best.get("pullback_stage", "")),
        "multi_tf_high_break_label": str(best.get("break_label", "")),
        "multi_tf_high_break_desc": str(best.get("break_desc", "")),
        "multi_tf_levels_json": json.dumps(contexts[:6], ensure_ascii=False),
    }



def _find_valid_max_volume_bull_row(period_df, lookback=100):
    """V12.4：返回指定窗口内有效最大量阳K及其位置，用于远期绿线模型。"""
    if period_df is None or period_df.empty:
        return None
    w = period_df.tail(int(lookback)).copy().reset_index(drop=True)
    if len(w) < 12:
        return None
    for c in ["open", "high", "low", "close", "volume"]:
        w[c] = pd.to_numeric(w[c], errors="coerce")
    w = w.dropna(subset=["open", "high", "low", "close", "volume"]).reset_index(drop=True)
    if w.empty:
        return None
    for idx, row in w.sort_values("volume", ascending=False).iterrows():
        if _is_valid_max_volume_bull_candle(row):
            return idx, row, w
    return None


def _probe_k_quality(row, green_high, green_volume):
    """9号试盘K质量：创绿线更高高点，量价不能差。"""
    op = safe_float(row.get("open", 0))
    cl = safe_float(row.get("close", 0))
    hi = safe_float(row.get("high", 0))
    lo = safe_float(row.get("low", 0))
    vol = safe_float(row.get("volume", 0))
    if min(op, cl, hi, lo) <= 0 or green_high <= 0:
        return 0.0, "试盘K无效"
    rng = max(hi - lo, 1e-9)
    body = abs(cl - op)
    upper = hi - max(op, cl)
    close_pos = (cl - lo) / rng
    vol_ratio = vol / green_volume if green_volume > 0 else 0.0
    score = 0.0
    parts = []
    if hi >= green_high * 1.003:
        score += 2.0
        parts.append("高点突破绿线")
    if vol_ratio >= 0.45:
        score += 1.2
        parts.append(f"量能/绿线{vol_ratio:.2f}")
    if vol_ratio >= 0.65:
        score += 0.8
    if body >= rng * 0.35:
        score += 1.0
        parts.append("实体不弱")
    if close_pos >= 0.45:
        score += 0.8
    if upper / rng <= 0.45:
        score += 0.7
    if body < rng * 0.18 or upper / rng > 0.60 or vol_ratio < 0.35:
        score -= 2.0
        parts.append("试盘质量偏弱")
    return max(0.0, min(6.0, score)), "、".join(parts)


def _time_ratio_score(n, m):
    if n <= 0 or m <= 0:
        return 0.0, "时间结构不足", 0.0
    ratio = m / n
    score = 0.0
    label = f"消化周期{ratio:.2f}倍"
    if ratio < 0.8:
        score = -1.5
        label += "，偏短"
    elif ratio < 1.3:
        score = 1.2
        label += "，一倍窗口"
    elif ratio < 1.8:
        score = 1.8
        label += "，消化较充分"
    elif ratio <= 2.4:
        score = 3.0
        label += "，二倍时间窗口"
    elif ratio <= 3.0:
        score = 1.8
        label += "，长周期消化"
    else:
        score = 0.5
        label += "，结构需重新评估"
    return score, label, ratio


def detect_v124_probe_high_second_confirm_model(df):
    """
    V12.4 远期最大量高点 + 高质量9号试盘高点 + 时间倍数 + 日线二次确认模型。
    只在深度层/种子层运行，不进入全市场基础重扫。
    """
    empty = {
        "score_v124_probe_second_confirm": 0.0,
        "v124_probe_stage": "无远期试盘结构",
        "v124_probe_desc": "",
        "v124_green_price": 0.0,
        "v124_probe_price": 0.0,
        "v124_parent_pressure": 0.0,
        "v124_parent_distance": 0.0,
        "v124_time_ratio": 0.0,
        "v124_daily_break_valid": False,
        "v124_daily_break_label": "",
    }
    if df is None or len(df) < 700:
        return empty
    tf_defs = [
        ("月线", _resample_ohlcv(df, "ME"), MONTHLY_STRUCT_LOOKBACK_MONTHS, 2.4),
        ("周线", _resample_ohlcv(df, "W-FRI"), 220, 1.6),
        ("季线", _resample_ohlcv(df, "QE"), 45, 3.0),
    ]
    best = None
    cur_close = safe_float(df.iloc[-1].get("close", 0))
    for tf_name, pdf, lookback, weight in tf_defs:
        found = _find_valid_max_volume_bull_row(pdf, lookback=lookback)
        if found is None:
            continue
        green_idx, green_row, w = found
        green_high = safe_float(green_row.get("high", 0))
        green_vol = safe_float(green_row.get("volume", 0))
        if green_high <= 0 or green_vol <= 0 or green_idx >= len(w) - 8:
            continue
        after = w.iloc[green_idx + 1:].copy().reset_index(drop=False).rename(columns={"index":"orig_idx"})
        # 9号：绿线之后创出更高高点的高质量试盘K，优先选择质量高且时间间隔合理的。
        probes = []
        for _, r in after.iterrows():
            orig_idx = int(r.get("orig_idx", -1))
            if orig_idx <= green_idx:
                continue
            hi = safe_float(r.get("high", 0))
            if hi < green_high * 1.003:
                continue
            q, qdesc = _probe_k_quality(r, green_high, green_vol)
            if q < 3.0:
                continue
            n = orig_idx - green_idx
            if n < 3:
                continue
            probes.append((q, orig_idx, r, qdesc, n))
        if not probes:
            continue
        probes = sorted(probes, key=lambda x: (x[0], x[4]), reverse=True)
        q, probe_idx, probe_row, qdesc, n = probes[0]
        probe_high = safe_float(probe_row.get("high", 0))
        m = len(w) - 1 - probe_idx
        tscore, tlabel, tratio = _time_ratio_score(n, m)
        # 父级压力：绿线之前更高的历史大凹口/大高点，若离红线过近则降级。
        before_green = w.iloc[:green_idx].copy()
        parent_pressure = 0.0
        if not before_green.empty:
            parent_pressure = safe_float(before_green["high"].max())
            if parent_pressure <= probe_high * 1.03:
                parent_pressure = 0.0
        parent_distance = (parent_pressure / cur_close - 1) if parent_pressure > 0 and cur_close > 0 else 9.99
        parent_penalty = 0.0
        parent_label = "无近端上级大凹口压制"
        if 0 < parent_distance < 0.08:
            parent_penalty = -5.0
            parent_label = "上级大凹口压力贴脸"
        elif 0 < parent_distance < 0.15:
            parent_penalty = -2.5
            parent_label = "上方父级压力较近"
        elif 0 < parent_distance < 0.22:
            parent_penalty = -0.8
            parent_label = "上方仍有父级压力"
        # 历史假突破次数：多次失败后，要求更强日线突破或回踩确认。
        false_cnt = 0
        for _, rr in w.iloc[green_idx + 1:].iterrows():
            if safe_float(rr.get("high", 0)) >= green_high * 1.003 and safe_float(rr.get("close", 0)) < green_high * 1.005:
                false_cnt += 1
        brk = _daily_break_quality_against_level(df, probe_high)
        break_score = safe_float(brk.get("score", 0.0))
        valid = bool(brk.get("valid", False))
        # 多次假突破但本次突破不强，不能高分。
        if false_cnt >= 2 and not valid:
            break_score -= 2.0
        score = (q * 1.2 + tscore + max(0.0, break_score) + parent_penalty) * weight
        if valid:
            score += 2.0 * weight
        if parent_penalty <= -5.0:
            score = min(score, 4.0 * weight)
        score = max(0.0, min(18.0, score))
        stage = "后台重点跟踪"
        if valid and score >= 10 and parent_penalty > -5.0:
            stage = "日线突破9号线二次确认"
        elif parent_penalty <= -5.0:
            stage = "突破小门但父级压力贴脸"
        desc = (
            f"{tf_name}绿线{green_high:.2f}，9号试盘高点{probe_high:.2f}；"
            f"9号质量{q:.1f}({qdesc})；N={n}，M={m}，{tlabel}；"
            f"日线{brk.get('label','')}；{parent_label}"
        )
        cand = {
            "score_v124_probe_second_confirm": float(score),
            "v124_probe_stage": stage,
            "v124_probe_desc": desc,
            "v124_green_price": float(green_high),
            "v124_probe_price": float(probe_high),
            "v124_parent_pressure": float(parent_pressure),
            "v124_parent_distance": float(parent_distance if parent_distance != 9.99 else 0.0),
            "v124_time_ratio": float(tratio),
            "v124_daily_break_valid": bool(valid),
            "v124_daily_break_label": str(brk.get("label", "")),
        }
        if best is None or cand["score_v124_probe_second_confirm"] > best["score_v124_probe_second_confirm"]:
            best = cand
    return best if best is not None else empty


def _v125_volume_cv(vol_series):
    v = pd.to_numeric(vol_series, errors="coerce").dropna()
    if len(v) < 3 or safe_float(v.mean()) <= 0:
        return 9.99
    return float(v.std() / max(v.mean(), 1e-9))


def _v125_flat_volume_ratio(vol_series, band=0.15):
    v = pd.to_numeric(vol_series, errors="coerce").dropna()
    if len(v) < 3 or safe_float(v.mean()) <= 0:
        return 0.0
    mean_v = safe_float(v.mean())
    return float(((v >= mean_v * (1 - band)) & (v <= mean_v * (1 + band))).sum() / len(v))


def _v125_detect_platform_window(df, end_idx=None, min_len=8, max_len=35, max_range_pct=0.22):
    """
    识别最近一段整理平台。用于V12.5台阶平台/爆发前夜模型。
    平台不是固定天数，而是在多个窗口里找“价格波动相对收敛、低点不破坏”的窗口。
    """
    if df is None or len(df) < min_len + 5:
        return None
    d = df.copy().reset_index(drop=True)
    if end_idx is None:
        end_idx = len(d) - 1
    best = None
    for win in range(min(max_len, end_idx + 1), min_len - 1, -1):
        start = end_idx - win + 1
        if start < 0:
            continue
        w = d.iloc[start:end_idx + 1]
        hi = safe_float(w["high"].max())
        lo = safe_float(w["low"].min())
        center = safe_float(w["close"].median())
        if center <= 0 or hi <= 0 or lo <= 0:
            continue
        range_pct = (hi - lo) / center
        if range_pct > max_range_pct:
            continue
        vol_mean = safe_float(w["volume"].mean())
        vol_cv = _v125_volume_cv(w["volume"])
        flat_ratio = _v125_flat_volume_ratio(w["volume"], band=0.15)
        close_near_high = safe_float(d.iloc[end_idx]["close"]) >= hi * 0.88
        score = win * 0.10 + max(0.0, (max_range_pct - range_pct) * 10) + flat_ratio * 1.5 + (1.0 if close_near_high else 0.0)
        cand = {
            "start": int(start), "end": int(end_idx), "length": int(win), "high": float(hi), "low": float(lo),
            "center": float(center), "range_pct": float(range_pct), "vol_mean": float(vol_mean),
            "vol_cv": float(vol_cv), "flat_ratio": float(flat_ratio), "score": float(score),
            "start_date": str(w.iloc[0].get("date", "")), "end_date": str(w.iloc[-1].get("date", "")),
        }
        if best is None or cand["score"] > best["score"]:
            best = cand
    return best


def _v125_find_prior_platform(df, before_idx, min_len=8, max_len=35):
    if before_idx is None or before_idx < min_len + 5:
        return None
    best = None
    # 在前一段历史里滑动寻找上一平台，避免把连续贴线几天误判为两个平台。
    for end_idx in range(max(min_len - 1, before_idx - 8), min_len - 2, -3):
        cand = _v125_detect_platform_window(df, end_idx=end_idx, min_len=min_len, max_len=max_len, max_range_pct=0.24)
        if not cand:
            continue
        if cand["end"] >= before_idx - 3:
            continue
        if best is None or cand["score"] > best["score"]:
            best = cand
    return best


def _v125_find_launch_candle_before_platform(df, platform_start, lookback=12):
    if df is None or platform_start is None or platform_start <= 0:
        return {"valid": False, "mid": 0.0, "bottom": 0.0, "top": 0.0, "date": "", "desc": "无前置启动柱"}
    d = df.copy().reset_index(drop=True)
    start = max(0, platform_start - lookback)
    sub = d.iloc[start:platform_start]
    if sub.empty:
        return {"valid": False, "mid": 0.0, "bottom": 0.0, "top": 0.0, "date": "", "desc": "无前置启动柱"}
    best = None
    for i, r in sub.iterrows():
        op = safe_float(r.get("open", 0)); cl = safe_float(r.get("close", 0)); hi = safe_float(r.get("high", 0)); lo = safe_float(r.get("low", 0))
        vol = safe_float(r.get("volume", 0))
        prev_vol = safe_float(d.iloc[i-1].get("volume", 0)) if i > 0 else 0.0
        vr = vol / prev_vol if prev_vol > 0 else 0.0
        pct = (cl / op - 1) if op > 0 else 0.0
        rng = max(hi - lo, 1e-9)
        pos = (cl - lo) / rng
        strong = (cl > op and pct >= 0.035 and pos >= 0.65) or (cl > op and vr >= 1.5 and pos >= 0.65)
        if not strong:
            continue
        score = pct * 100 + min(vr, 3.0) + pos
        if best is None or score > best[0]:
            bottom = min(op, cl); top = max(op, cl); mid = (bottom + top) / 2
            best = (score, {"valid": True, "mid": float(mid), "bottom": float(bottom), "top": float(top), "date": str(r.get("date", "")), "desc": f"前置启动柱{r.get('date','')}，中位{mid:.2f}/实底{bottom:.2f}"})
    return best[1] if best else {"valid": False, "mid": 0.0, "bottom": 0.0, "top": 0.0, "date": "", "desc": "无前置启动柱"}


def detect_v125_timing_window_model(df, v124_ctx=None):
    """
    V12.5 爆发前夜时间窗口模型。
    机构化拆分：时间对称/倍数周期、平台蓄势长度、关键位贴近、量能从乱到稳、波动率收缩、日线触发。
    注意：这是优先级放大器，不是单独买入理由；正式推送仍需日线触发/回踩确认/风险过滤。
    """
    empty = {
        "score_v125_timing_window": 0.0,
        "v125_timing_label": "无明显爆发时间窗口",
        "v125_timing_desc": "",
        "v125_volume_stability_score": 0.0,
        "v125_vol_cv_prev": 0.0,
        "v125_vol_cv_recent": 0.0,
        "v125_flat_volume_ratio": 0.0,
        "v125_timing_trigger": False,
        "v125_platform_days": 0,
    }
    if df is None or len(df) < 80:
        return empty
    d = df.copy().reset_index(drop=True)
    for c in ["open", "high", "low", "close", "volume"]:
        d[c] = pd.to_numeric(d[c], errors="coerce")
    d = d.dropna(subset=["open", "high", "low", "close", "volume"]).reset_index(drop=True)
    if len(d) < 80:
        return empty
    cur = d.iloc[-1]
    cur_close = safe_float(cur["close"])
    # 1）时间对称：优先引用V12.4绿线/9号线模型输出，避免重复重算远期结构。
    time_score = 0.0
    time_desc = "无远期时间对称"
    if isinstance(v124_ctx, dict):
        ratio = safe_float(v124_ctx.get("v124_time_ratio", 0))
        if ratio > 0:
            if 0.85 <= ratio <= 1.15:
                time_score = 3.0; time_desc = f"一倍时间窗口{ratio:.2f}"
            elif 1.75 <= ratio <= 2.25:
                time_score = 4.0; time_desc = f"二倍时间窗口{ratio:.2f}"
            elif 2.70 <= ratio <= 3.30:
                time_score = 2.5; time_desc = f"三倍时间窗口{ratio:.2f}"
            elif 1.30 <= ratio < 1.75 or 2.25 < ratio < 2.70:
                time_score = 1.5; time_desc = f"时间消化较充分{ratio:.2f}"
    # 2）平台蓄势与关键位贴近。
    platform = _v125_detect_platform_window(d.iloc[:-1] if len(d) > 90 else d, end_idx=len(d)-2 if len(d)>90 else len(d)-1, min_len=12, max_len=80, max_range_pct=0.24)
    platform_score = 0.0
    proximity_score = 0.0
    platform_desc = "无清晰平台"
    platform_days = 0
    if platform:
        platform_days = int(platform["length"])
        if 20 <= platform_days < 40:
            platform_score = 1.5
        elif 40 <= platform_days <= 120:
            platform_score = 3.5
        elif 120 < platform_days <= 180:
            platform_score = 2.5
        elif platform_days > 180:
            platform_score = 1.0
        dist_to_high = cur_close / max(platform["high"], 1e-9) - 1
        if -0.03 <= dist_to_high <= 0.06:
            proximity_score = 3.0
        elif -0.08 <= dist_to_high < -0.03:
            proximity_score = 1.5
        platform_desc = f"平台{platform_days}日，距上沿{dist_to_high*100:.1f}%"
    # 3）量能从乱到稳：比较前段CV和近段CV，而不是只看当前平量。
    prev_vol = d["volume"].iloc[-38:-10]
    recent_vol = d["volume"].iloc[-10:]
    cv_prev = _v125_volume_cv(prev_vol)
    cv_recent = _v125_volume_cv(recent_vol)
    flat_ratio = _v125_flat_volume_ratio(recent_vol, band=0.15)
    extreme_prev = int((prev_vol > prev_vol.mean() * 1.8).sum()) if len(prev_vol) >= 5 and prev_vol.mean() > 0 else 0
    extreme_recent = int((recent_vol > recent_vol.mean() * 1.8).sum()) if len(recent_vol) >= 5 and recent_vol.mean() > 0 else 0
    volume_stability_score = 0.0
    vol_desc = f"量CV前{cv_prev:.2f}/近{cv_recent:.2f}，平量{flat_ratio*100:.0f}%"
    if cv_prev < 9 and cv_recent < 9:
        if cv_recent <= cv_prev * 0.70 and cv_prev >= 0.35:
            volume_stability_score += 2.2
        elif cv_recent <= cv_prev * 0.85:
            volume_stability_score += 1.2
        if flat_ratio >= 0.70:
            volume_stability_score += 1.5
        elif flat_ratio >= 0.55:
            volume_stability_score += 0.8
        if extreme_recent <= max(1, extreme_prev // 2):
            volume_stability_score += 0.7
    # 4）波动率收缩：ATR/振幅从大到小，且价格不破平台。
    tr = (d["high"] - d["low"]) / d["close"].replace(0, np.nan)
    atr_prev = safe_float(tr.iloc[-40:-12].mean())
    atr_recent = safe_float(tr.iloc[-10:].mean())
    contraction_score = 0.0
    contraction_desc = f"振幅前{atr_prev*100:.1f}%/近{atr_recent*100:.1f}%"
    if atr_prev > 0 and atr_recent > 0:
        if atr_recent <= atr_prev * 0.72:
            contraction_score += 2.0
        elif atr_recent <= atr_prev * 0.85:
            contraction_score += 1.0
    # 5）触发：健康放量/标准倍量/实体阳线突破平台上沿或关键位。
    prev = d.iloc[-2]
    vr1 = safe_float(cur["volume"]) / max(safe_float(prev["volume"]), 1e-9)
    pos = (safe_float(cur["close"]) - safe_float(cur["low"])) / max(safe_float(cur["high"]) - safe_float(cur["low"]), 1e-9)
    entity = (safe_float(cur["close"]) - safe_float(cur["open"])) / max(safe_float(cur["open"]), 1e-9)
    trigger = False
    trigger_score = 0.0
    trigger_desc = "未触发"
    platform_high = safe_float(platform.get("high", 0)) if platform else 0.0
    if platform_high > 0 and cur_close >= platform_high * 1.006 and cur_close > safe_float(cur["open"]) and pos >= 0.72 and (1.5 <= vr1 <= 3.5 or safe_float(cur["volume"]) >= safe_float(d["volume"].tail(20).mean()) * 1.3):
        trigger = True
        trigger_score = 3.0
        trigger_desc = "平台末端健康放量突破"
        if entity >= 0.035 and pos >= 0.82:
            trigger_score += 1.0
    score = time_score + platform_score + proximity_score + volume_stability_score + contraction_score + trigger_score
    # 防止坏平量：无平台、无接近关键位且无触发，仅量平不加高分。
    if platform_score <= 0 and proximity_score <= 0 and not trigger:
        score = min(score, 5.0)
    score = max(0.0, min(20.0, score))
    label = "后台时间窗口"
    if score >= 16:
        label = "爆发前夜"
    elif score >= 12:
        label = "明显时间窗口"
    elif score >= 8:
        label = "进入观察窗口"
    elif score < 5:
        label = "时间窗口不明显"
    desc = "；".join([time_desc, platform_desc, vol_desc, contraction_desc, trigger_desc])
    return {
        "score_v125_timing_window": float(score),
        "v125_timing_label": label,
        "v125_timing_desc": desc,
        "v125_volume_stability_score": float(volume_stability_score),
        "v125_vol_cv_prev": float(cv_prev if cv_prev < 9 else 0.0),
        "v125_vol_cv_recent": float(cv_recent if cv_recent < 9 else 0.0),
        "v125_flat_volume_ratio": float(flat_ratio),
        "v125_timing_trigger": bool(trigger),
        "v125_platform_days": int(platform_days),
    }


def detect_v125_step_platform_volume_lift_model(df):
    """
    V12.5 台阶平台量能均值抬升模型（日线为主）。
    核心：价格平台上移、平台均量也上移、平台内平量比例高、守前启动柱中位/实底、无放量长阴破坏、再突破。
    这是承接/台阶资金推进同源组的质量提升项，不单独无限加分。
    """
    empty = {
        "score_v125_step_platform_lift": 0.0,
        "v125_step_platform_label": "无台阶平台量能抬升",
        "v125_step_platform_desc": "",
        "v125_step_volume_ratio": 0.0,
        "v125_step_price_lift": 0.0,
        "v125_step_flat_ratio": 0.0,
        "v125_step_hold_launch_level": False,
        "v125_step_break_trigger": False,
    }
    if df is None or len(df) < 90:
        return empty
    d = df.copy().reset_index(drop=True)
    for c in ["open", "high", "low", "close", "volume"]:
        d[c] = pd.to_numeric(d[c], errors="coerce")
    d = d.dropna(subset=["open", "high", "low", "close", "volume"]).reset_index(drop=True)
    if len(d) < 90:
        return empty
    # 最近平台不含当前突破日，避免把大阳突破日混进平台均量。
    p2 = _v125_detect_platform_window(d.iloc[:-1], end_idx=len(d)-2, min_len=8, max_len=35, max_range_pct=0.20)
    if not p2:
        return empty
    p1 = _v125_find_prior_platform(d, before_idx=p2["start"] - 3, min_len=8, max_len=35)
    if not p1:
        return empty
    vol_ratio = safe_float(p2["vol_mean"]) / max(safe_float(p1["vol_mean"]), 1e-9)
    price_lift = safe_float(p2["center"]) / max(safe_float(p1["center"]), 1e-9) - 1
    flat_ratio = safe_float(p2["flat_ratio"])
    launch = _v125_find_launch_candle_before_platform(d, p2["start"], lookback=14)
    p2_low = safe_float(p2["low"])
    hold_mid = launch.get("valid") and p2_low >= safe_float(launch.get("mid", 0)) * 0.985
    hold_bottom = launch.get("valid") and p2_low >= safe_float(launch.get("bottom", 0)) * 0.985
    hold_ok = bool(hold_mid or hold_bottom)
    p2_df = d.iloc[p2["start"]:p2["end"]+1]
    p2_vol_ma = safe_float(p2_df["volume"].mean())
    bad_long_down = 0
    for _, r in p2_df.iterrows():
        op = safe_float(r["open"]); cl = safe_float(r["close"]); vol = safe_float(r["volume"])
        down_pct = (op - cl) / max(op, 1e-9)
        if cl < op and down_pct >= 0.035 and vol >= p2_vol_ma * 1.35:
            bad_long_down += 1
    cur = d.iloc[-1]
    prev = d.iloc[-2]
    vr1 = safe_float(cur["volume"]) / max(safe_float(prev["volume"]), 1e-9)
    pos = (safe_float(cur["close"]) - safe_float(cur["low"])) / max(safe_float(cur["high"]) - safe_float(cur["low"]), 1e-9)
    break_trigger = bool(safe_float(cur["close"]) >= safe_float(p2["high"]) * 1.006 and safe_float(cur["close"]) > safe_float(cur["open"]) and pos >= 0.70 and (vr1 >= 1.45 or safe_float(cur["volume"]) >= p2_vol_ma * 1.35))
    score = 0.0
    if price_lift >= 0.08:
        score += 2.5
    elif price_lift >= 0.04:
        score += 1.2
    if 1.10 <= vol_ratio <= 1.50:
        score += 2.0
    elif 1.50 < vol_ratio <= 2.50:
        score += 3.0
    elif 0.85 <= vol_ratio < 1.10:
        score += 0.8
    elif vol_ratio > 2.50:
        score += 1.0  # 高位平台巨量分歧，需要后续质量确认，不能直接高分。
    if flat_ratio >= 0.75:
        score += 2.0
    elif flat_ratio >= 0.60:
        score += 1.2
    if safe_float(p2["vol_cv"]) <= safe_float(p1["vol_cv"]) * 0.85:
        score += 1.0
    if hold_mid:
        score += 2.5
    elif hold_bottom:
        score += 1.4
    if bad_long_down == 0:
        score += 1.5
    elif bad_long_down >= 2:
        score -= 2.5
    if break_trigger:
        score += 3.0
    # 坏平量过滤：平台抬高不足、均量没抬高，只靠平量不加高分。
    if price_lift < 0.04 or vol_ratio < 0.85:
        score = min(score, 5.0)
    score = max(0.0, min(16.0, score))
    label = "台阶平台观察"
    if score >= 12 and break_trigger:
        label = "高质量台阶平台再突破"
    elif score >= 10:
        label = "台阶平台资金承接较强"
    elif score >= 7:
        label = "台阶平台量能抬升"
    desc = (
        f"前平台{p1['start_date']}~{p1['end_date']}，后平台{p2['start_date']}~{p2['end_date']}；"
        f"价格中枢抬升{price_lift*100:.1f}%，均量比{vol_ratio:.2f}，后平台平量{flat_ratio*100:.0f}%；"
        f"{launch.get('desc','')}，守位={'是' if hold_ok else '否'}，放量长阴{bad_long_down}次，触发={'是' if break_trigger else '否'}"
    )
    return {
        "score_v125_step_platform_lift": float(score),
        "v125_step_platform_label": label,
        "v125_step_platform_desc": desc,
        "v125_step_volume_ratio": float(vol_ratio),
        "v125_step_price_lift": float(price_lift),
        "v125_step_flat_ratio": float(flat_ratio),
        "v125_step_hold_launch_level": bool(hold_ok),
        "v125_step_break_trigger": bool(break_trigger),
    }


# ========================= V12.6：多周期重心/平量/时间窗口/底部修复充分率模型 =========================
def _v126_typical_center(period_df):
    if period_df is None or period_df.empty:
        return pd.Series(dtype=float)
    return (pd.to_numeric(period_df["high"], errors="coerce") + pd.to_numeric(period_df["low"], errors="coerce") + pd.to_numeric(period_df["close"], errors="coerce")) / 3.0


def _v126_slope_score(values):
    v = pd.to_numeric(values, errors="coerce").dropna()
    if len(v) < 4:
        return 0.0
    x = np.arange(len(v), dtype=float)
    y = v.values.astype(float)
    if np.nanmean(y) <= 0:
        return 0.0
    slope = np.polyfit(x, y, 1)[0] / np.nanmean(y)
    return float(slope)


def _v126_center_up_quality(period_df, window=8):
    """精确定义“重心上移”：typical price斜率为正、低点抬高、近期中枢高于前段，且不能只靠单根异常大阳。"""
    if period_df is None or len(period_df) < max(6, window):
        return {"score": 0.0, "label": "重心不足", "desc": "样本不足", "slope": 0.0, "low_lift": 0.0}
    d = period_df.copy().reset_index(drop=True)
    for c in ["open", "high", "low", "close", "volume"]:
        d[c] = pd.to_numeric(d[c], errors="coerce")
    d = d.dropna(subset=["open", "high", "low", "close", "volume"]).reset_index(drop=True)
    if len(d) < max(6, window):
        return {"score": 0.0, "label": "重心不足", "desc": "样本不足", "slope": 0.0, "low_lift": 0.0}
    recent = d.tail(window)
    prev = d.iloc[max(0, len(d)-window*2):len(d)-window]
    center = _v126_typical_center(recent)
    slope = _v126_slope_score(center)
    rec_mid = safe_float(center.median())
    prev_mid = safe_float(_v126_typical_center(prev).median()) if len(prev) >= 3 else 0.0
    center_lift = rec_mid / prev_mid - 1 if prev_mid > 0 else 0.0
    low_first = safe_float(recent["low"].head(max(2, window//3)).median())
    low_last = safe_float(recent["low"].tail(max(2, window//3)).median())
    low_lift = low_last / low_first - 1 if low_first > 0 else 0.0
    # 避免单根异常大阳硬拉：最近窗口最大单根涨幅占整体重心抬升过高则降权。
    pct = recent["close"].pct_change().fillna(0)
    one_bar_boost = safe_float(pct.max())
    score = 0.0
    if slope > 0.006:
        score += 2.0
    elif slope > 0.0025:
        score += 1.0
    if center_lift > 0.08:
        score += 2.0
    elif center_lift > 0.03:
        score += 1.0
    if low_lift > 0.05:
        score += 1.5
    elif low_lift > 0.015:
        score += 0.8
    if one_bar_boost > 0.18 and center_lift < one_bar_boost * 0.55:
        score *= 0.65
    score = max(0.0, min(6.0, score))
    label = "重心上移明显" if score >= 4.2 else ("重心温和上移" if score >= 2.2 else "重心上移不足")
    desc = f"重心斜率{slope:.3f}，中枢抬升{center_lift:.1%}，低点抬升{low_lift:.1%}"
    return {"score": float(score), "label": label, "desc": desc, "slope": float(slope), "low_lift": float(low_lift)}


def _v126_volume_stability_quality(period_df, window=8, prev_window=8):
    """精确定义“平量/量能稳定”：当前段CV下降、平量比例高、极端量柱减少，并和前段对比。"""
    if period_df is None or len(period_df) < max(6, window + prev_window):
        return {"score": 0.0, "label": "平量不足", "desc": "样本不足", "cv_prev": 0.0, "cv_recent": 0.0, "flat_ratio": 0.0}
    d = period_df.copy().reset_index(drop=True)
    d["volume"] = pd.to_numeric(d["volume"], errors="coerce")
    d = d.dropna(subset=["volume"]).reset_index(drop=True)
    if len(d) < window + prev_window:
        return {"score": 0.0, "label": "平量不足", "desc": "样本不足", "cv_prev": 0.0, "cv_recent": 0.0, "flat_ratio": 0.0}
    prev = d["volume"].iloc[-(window+prev_window):-window]
    recent = d["volume"].iloc[-window:]
    cv_prev = _v125_volume_cv(prev)
    cv_recent = _v125_volume_cv(recent)
    flat_ratio = _v125_flat_volume_ratio(recent, band=0.18)
    prev_mean = safe_float(prev.mean())
    recent_mean = safe_float(recent.mean())
    mean_ratio = recent_mean / prev_mean if prev_mean > 0 else 0.0
    extreme_prev = int((prev > prev_mean * 1.8).sum()) if prev_mean > 0 else 0
    extreme_recent = int((recent > recent_mean * 1.8).sum()) if recent_mean > 0 else 0
    score = 0.0
    if cv_recent <= cv_prev * 0.72 and cv_prev >= 0.25:
        score += 2.0
    elif cv_recent <= cv_prev * 0.88:
        score += 1.0
    if flat_ratio >= 0.72:
        score += 2.0
    elif flat_ratio >= 0.58:
        score += 1.0
    if extreme_recent <= max(1, extreme_prev // 2):
        score += 0.8
    if 0.85 <= mean_ratio <= 2.4:
        score += 0.7
    elif mean_ratio < 0.55:
        score -= 1.2  # 防死平量：量太低不是稳定承接。
    score = max(0.0, min(5.5, score))
    label = "高质量平量" if score >= 4.0 else ("量能趋稳" if score >= 2.2 else "平量质量一般")
    desc = f"量CV前{cv_prev:.2f}/近{cv_recent:.2f}，平量{flat_ratio:.0%}，均量比{mean_ratio:.2f}"
    return {"score": float(score), "label": label, "desc": desc, "cv_prev": float(cv_prev), "cv_recent": float(cv_recent), "flat_ratio": float(flat_ratio)}


def detect_v126_multiframe_center_volume_model(df):
    """
    V12.6 多周期重心上移 + 平量稳定模型。
    日/周/月/季独立计算，择优而不重复加分；高周期负责种子，日线负责触发。
    """
    empty = {
        "score_v126_multiframe_center_volume": 0.0,
        "v126_mtf_cv_label": "无多周期重心平量",
        "v126_mtf_cv_desc": "",
        "v126_best_timeframe": "",
        "v126_center_up_score": 0.0,
        "v126_volume_flat_score": 0.0,
        "v126_center_slope": 0.0,
        "v126_flat_ratio": 0.0,
    }
    if df is None or len(df) < 160:
        return empty
    tf_defs = [
        ("日线", df.copy().reset_index(drop=True), 20, 16, 1.0),
        ("周线", _resample_ohlcv(df, "W-FRI"), 13, 10, 1.25),
        ("月线", _resample_ohlcv(df, "ME"), 8, 8, 1.55),
        ("季线", _resample_ohlcv(df, "QE"), 6, 5, 1.85),
    ]
    cands = []
    for tf, pdf, w_center, w_vol, weight in tf_defs:
        if pdf is None or len(pdf) < max(w_center*2, w_vol*2, 6):
            continue
        cq = _v126_center_up_quality(pdf, window=w_center)
        vq = _v126_volume_stability_quality(pdf, window=w_vol, prev_window=w_vol)
        # 价格重心和平量必须同时有；单独平量不高分，避免死水。
        raw = (safe_float(cq.get("score", 0))*0.58 + safe_float(vq.get("score", 0))*0.42) * weight
        if safe_float(cq.get("score", 0)) < 1.5:
            raw *= 0.55
        if safe_float(vq.get("score", 0)) < 1.2:
            raw *= 0.70
        cands.append({
            "timeframe": tf,
            "score": max(0.0, min(12.0, raw)),
            "center": cq,
            "volume": vq,
            "desc": f"{tf}：{cq.get('label')}，{vq.get('label')}（{cq.get('desc')}；{vq.get('desc')}）",
        })
    if not cands:
        return empty
    cands = sorted(cands, key=lambda x: x["score"], reverse=True)
    best = cands[0]
    resonance = sum(1 for x in cands[1:] if x["score"] >= 4.0)
    score = min(14.0, best["score"] + resonance * 1.0)
    label = "多周期资金重心稳定推进" if score >= 10 else ("高周期重心/平量种子" if score >= 6 else "重心平量观察")
    return {
        "score_v126_multiframe_center_volume": float(score),
        "v126_mtf_cv_label": label,
        "v126_mtf_cv_desc": "；".join([x["desc"] for x in cands[:2]]),
        "v126_best_timeframe": best["timeframe"],
        "v126_center_up_score": float(best["center"].get("score", 0)),
        "v126_volume_flat_score": float(best["volume"].get("score", 0)),
        "v126_center_slope": float(best["center"].get("slope", 0)),
        "v126_flat_ratio": float(best["volume"].get("flat_ratio", 0)),
    }


def detect_v126_major_high_1000d_window(df):
    """重大高点后980-1020交易日窄口时间窗口。只轻度加分，但报告必须提示已运行多少个交易日。"""
    empty = {
        "score_v126_1000d_window": 0.0,
        "v126_1000d_label": "无1000日窗口",
        "v126_1000d_desc": "",
        "v126_days_from_major_high": 0,
        "v126_major_high_price": 0.0,
        "v126_major_high_date": "",
    }
    if df is None or len(df) < 980:
        return empty
    d = df.copy().reset_index(drop=True)
    for c in ["high", "close", "volume"]:
        d[c] = pd.to_numeric(d[c], errors="coerce")
    d = d.dropna(subset=["high", "close", "volume"]).reset_index(drop=True)
    if len(d) < 980:
        return empty
    lookback = min(len(d)-1, 2200)
    hist = d.iloc[-lookback-1:-1].copy().reset_index(drop=True)
    if hist.empty:
        return empty
    # 重大高点：优先找近2200日内显著高点，要求之后有明显回撤，避免把近期小高点误作周期高点。
    candidates = []
    for i, r in hist.iterrows():
        days = len(hist) - i
        if not (940 <= days <= 1060):
            continue
        hi = safe_float(r.get("high", 0))
        if hi <= 0:
            continue
        after_low = safe_float(hist.iloc[i+1:]["low"].min()) if "low" in hist.columns and i+1 < len(hist) else 0.0
        if after_low > 0 and (hi - after_low) / hi < 0.28:
            continue
        # 局部高点确认。
        left = hist.iloc[max(0, i-20):i+1]["high"].max()
        right = hist.iloc[i:min(len(hist), i+21)]["high"].max()
        if hi >= safe_float(left)*0.995 and hi >= safe_float(right)*0.995:
            candidates.append((abs(days-1000), days, i, r))
    if not candidates:
        return empty
    _, days, idx, row = sorted(candidates, key=lambda x: x[0])[0]
    score = 0.0
    if 980 <= days <= 1020:
        score = 2.0
        if 990 <= days <= 1010:
            score = 2.6
    desc = f"距离周期性高点已{days}个交易日"
    return {
        "score_v126_1000d_window": float(score),
        "v126_1000d_label": "1000日窄口时间窗口" if score > 0 else "无1000日窗口",
        "v126_1000d_desc": desc,
        "v126_days_from_major_high": int(days),
        "v126_major_high_price": safe_float(row.get("high", 0)),
        "v126_major_high_date": str(row.get("date", "")),
    }


def detect_v126_bottom_exhaustion_repair_seed(df):
    """
    底部衰竭修复种子：只做风控/后台观察，不能凭底部大阳直接正式推送。
    目标是区分“底部好换手”和“底部坏换手”。
    """
    empty = {"score_v126_bottom_repair_seed": 0.0, "v126_bottom_repair_label": "无底部修复种子", "v126_bottom_repair_desc": "", "v126_bottom_repair_trigger": False}
    if df is None or len(df) < 260:
        return empty
    d = df.copy().reset_index(drop=True)
    for c in ["open", "high", "low", "close", "volume"]:
        d[c] = pd.to_numeric(d[c], errors="coerce")
    d = d.dropna(subset=["open", "high", "low", "close", "volume"]).reset_index(drop=True)
    if len(d) < 260:
        return empty
    recent = d.tail(80)
    prev = d.iloc[-180:-80]
    cur = d.iloc[-1]
    long_high = safe_float(d["high"].tail(520).max()) if len(d) >= 520 else safe_float(d["high"].max())
    long_low = safe_float(d["low"].tail(520).min()) if len(d) >= 520 else safe_float(d["low"].min())
    close = safe_float(cur["close"])
    pos = (close - long_low) / max(long_high - long_low, 1e-9)
    low_not_new = safe_float(recent["low"].tail(20).min()) >= safe_float(recent["low"].head(40).min()) * 0.97
    body_prev = ((prev["close"] - prev["open"]).abs() / prev["open"].replace(0, np.nan)).dropna()
    body_recent = ((recent["close"] - recent["open"]).abs() / recent["open"].replace(0, np.nan)).dropna()
    body_shrink = safe_float(body_recent.tail(20).mean()) < safe_float(body_prev.tail(40).mean()) * 0.85 if len(body_prev) >= 10 and len(body_recent) >= 10 else False
    vol_cv_prev = _v125_volume_cv(prev["volume"].tail(40))
    vol_cv_recent = _v125_volume_cv(recent["volume"].tail(20))
    vol_stable = vol_cv_recent <= vol_cv_prev * 0.85 if vol_cv_prev < 9 else False
    vol_ma20 = safe_float(d["volume"].tail(20).mean())
    down_long = (recent["close"] < recent["open"]) & (((recent["open"]-recent["close"]) / recent["open"].replace(0, np.nan)) > 0.035) & (recent["volume"] > vol_ma20 * 1.4)
    bad_down = int(down_long.sum())
    platform = _v125_detect_platform_window(d.iloc[:-1], end_idx=len(d)-2, min_len=12, max_len=80, max_range_pct=0.26)
    platform_high = safe_float(platform.get("high", 0)) if platform else 0.0
    vr1 = safe_float(cur["volume"]) / max(safe_float(d.iloc[-2]["volume"]), 1e-9)
    rng = max(safe_float(cur["high"])-safe_float(cur["low"]), 1e-9)
    close_pos = (close-safe_float(cur["low"])) / rng
    trigger = bool(platform_high > 0 and close >= platform_high * 1.01 and close > safe_float(cur["open"]) and close_pos >= 0.72 and (vr1 >= 1.5 or safe_float(cur["volume"]) >= vol_ma20*1.35))
    score = 0.0
    if pos <= 0.35:
        score += 2.0
    elif pos <= 0.50:
        score += 1.0
    if low_not_new:
        score += 1.3
    if body_shrink:
        score += 1.0
    if vol_stable:
        score += 1.2
    if bad_down == 0:
        score += 1.3
    elif bad_down >= 2:
        score -= 2.5
    if platform:
        score += 1.2
    if trigger:
        score += 2.5
    score = max(0.0, min(10.0, score))
    label = "底部放量修复触发" if trigger and score >= 6 else ("底部衰竭修复种子" if score >= 5 else "底部修复证据不足")
    desc = f"长期位置{pos:.0%}，低点不新低={'是' if low_not_new else '否'}，实体收敛={'是' if body_shrink else '否'}，量CV前{vol_cv_prev:.2f}/近{vol_cv_recent:.2f}，放量长阴{bad_down}次，触发={'是' if trigger else '否'}"
    return {"score_v126_bottom_repair_seed": float(score), "v126_bottom_repair_label": label, "v126_bottom_repair_desc": desc, "v126_bottom_repair_trigger": bool(trigger)}


def detect_v126_system_timing_suite(df, v124_ctx=None):
    """V12.6 统一时间窗口套件：多周期重心平量、1000日窄口、底部衰竭修复。"""
    out = {}
    out.update(detect_v126_multiframe_center_volume_model(df))
    out.update(detect_v126_major_high_1000d_window(df))
    out.update(detect_v126_bottom_exhaustion_repair_seed(df))
    # 统一V12.6时间/充分率分：轻加分，正式推送仍需日线触发/回踩确认。
    score = (
        safe_float(out.get("score_v126_multiframe_center_volume", 0))*0.55
        + safe_float(out.get("score_v126_1000d_window", 0))*0.80
        + safe_float(out.get("score_v126_bottom_repair_seed", 0))*0.35
    )
    out["score_v126_timing_sufficiency"] = float(max(0.0, min(16.0, score)))
    out["v126_timing_sufficiency_desc"] = "；".join([x for x in [out.get("v126_mtf_cv_desc", ""), out.get("v126_1000d_desc", ""), out.get("v126_bottom_repair_desc", "")] if x])
    return out


def _v12_latest_prior_event_value(df, event_mask, value_series, lookback=10, default=0.0):
    """
    V12：找到当前日前lookback天内最近一次事件对应的值。
    用于“突破大阳线实体中位/实底”“突破日量能”等回踩确认。
    """
    out = []
    ev = list(event_mask.fillna(False).astype(bool).values)
    vals = list(pd.to_numeric(value_series, errors="coerce").fillna(default).values)
    for i in range(len(df)):
        found = default
        start = max(0, i - lookback)
        for j in range(i - 1, start - 1, -1):
            if ev[j]:
                found = vals[j]
                break
        out.append(found)
    return pd.Series(out, index=df.index)


def _v12_count_recent_event(event_mask, lookback=8):
    return event_mask.fillna(False).astype(int).shift(1).rolling(lookback).sum().fillna(0)


def _v12_bool_recent_event(event_mask, lookback=8):
    return _v12_count_recent_event(event_mask, lookback) > 0


def _v12_human_level(value, low_text="偏弱", mid_text="一般", high_text="较好", top_text="很好"):
    v = safe_float(value)
    if v >= 10:
        return top_text
    if v >= 6:
        return high_text
    if v >= 2:
        return mid_text
    return low_text



# ========================= V15：选股模型多周期供需压力带/支撑带生成引擎 =========================

def _xhu_clip(v, lo, hi):
    return max(lo, min(hi, safe_float(v, 0.0)))


def _xhu_letter_grade(score, cuts=(75, 60, 42, 25)):
    score = safe_float(score, 0.0)
    if score >= cuts[0]:
        return "S"
    if score >= cuts[1]:
        return "A"
    if score >= cuts[2]:
        return "B"
    if score >= cuts[3]:
        return "C"
    return "D"


def _xhu_period_weight(period):
    return {"D": 1.0, "W": 1.6, "M": 2.3, "Q": 3.0, "Y": 3.8}.get(str(period), 1.0)


def _xhu_bucket_pct_for_df(pdf, period="D"):
    """百分比/对数价格桶：默认0.5%，再按周期和波动率自适应。"""
    if pdf is None or pdf.empty:
        return XHU_PRESSURE_DEFAULT_BUCKET_PCT
    d = pdf.copy()
    for c in ["high", "low", "close"]:
        d[c] = pd.to_numeric(d[c], errors="coerce")
    d = d.dropna(subset=["high", "low", "close"])
    if d.empty:
        return XHU_PRESSURE_DEFAULT_BUCKET_PCT
    tr_pct = ((d["high"] - d["low"]) / d["close"].replace(0, np.nan)).tail(20).mean()
    tr_pct = safe_float(tr_pct, 0.02)
    base = {"D": 0.005, "W": 0.007, "M": 0.010, "Q": 0.015, "Y": 0.020}.get(str(period), 0.005)
    lo = {"D": 0.003, "W": 0.005, "M": 0.008, "Q": 0.010, "Y": 0.015}.get(str(period), 0.003)
    hi = {"D": 0.012, "W": 0.018, "M": 0.025, "Q": 0.035, "Y": 0.045}.get(str(period), 0.012)
    return _xhu_clip(max(base, tr_pct * 0.22), lo, hi)


def _xhu_make_log_edges(min_price, max_price, bucket_pct):
    min_price = max(0.01, safe_float(min_price, 0.01))
    max_price = max(min_price * 1.01, safe_float(max_price, min_price * 1.01))
    step = max(0.001, np.log1p(bucket_pct))
    lo = np.floor(np.log(min_price) / step) * step
    hi = np.ceil(np.log(max_price) / step) * step
    edges = np.exp(np.arange(lo, hi + step * 1.5, step))
    if len(edges) < 4:
        edges = np.linspace(min_price, max_price, 8)
    return edges


def _xhu_gap_zones(pdf):
    gaps = []
    if pdf is None or len(pdf) < 2:
        return gaps
    d = pdf.reset_index(drop=True)
    for i in range(1, len(d)):
        prev = d.iloc[i - 1]
        cur = d.iloc[i]
        ph = safe_float(prev.get("high", 0)); pl = safe_float(prev.get("low", 0))
        ch = safe_float(cur.get("high", 0)); cl = safe_float(cur.get("low", 0))
        if cl > ph * 1.002:
            gaps.append({"type": "gap_up", "lower": ph, "upper": cl, "date": str(cur.get("date", ""))})
        elif ch < pl * 0.998:
            gaps.append({"type": "gap_down", "lower": ch, "upper": pl, "date": str(cur.get("date", ""))})
    return gaps


def _xhu_assign_ohlcv_to_profile(pdf, edges):
    """OHLCV近似分配到对数价格桶：实体/收盘加权，上下影参与反应证据。"""
    n = len(edges) - 1
    prof = pd.DataFrame({
        "lower": edges[:-1], "upper": edges[1:],
        "volume": np.zeros(n), "amount": np.zeros(n), "bar_count": np.zeros(n),
        "close_count": np.zeros(n), "body_weight": np.zeros(n),
        "upper_wick_hits": np.zeros(n), "lower_wick_hits": np.zeros(n),
        "gap_up_hits": np.zeros(n), "gap_down_hits": np.zeros(n),
        "recent_volume": np.zeros(n),
    })
    if pdf is None or pdf.empty:
        return prof
    d = pdf.copy().reset_index(drop=True)
    for c in ["open", "high", "low", "close", "volume"]:
        d[c] = pd.to_numeric(d[c], errors="coerce")
    if "amount" not in d.columns:
        d["amount"] = d["close"] * d["volume"]
    d["amount"] = pd.to_numeric(d["amount"], errors="coerce").fillna(d["close"] * d["volume"])
    d = d.dropna(subset=["open", "high", "low", "close", "volume"])
    total_len = max(1, len(d))
    for i, r in d.iterrows():
        op = safe_float(r["open"]); hi = safe_float(r["high"]); lo = safe_float(r["low"]); cl = safe_float(r["close"])
        vol = max(0.0, safe_float(r["volume"])); amt = max(0.0, safe_float(r.get("amount", cl * vol)))
        if min(op, hi, lo, cl) <= 0 or hi < lo or vol <= 0:
            continue
        a = max(0, np.searchsorted(edges, lo, side="right") - 1)
        b = min(n - 1, np.searchsorted(edges, hi, side="left"))
        if b < a:
            continue
        idxs = np.arange(a, b + 1)
        # 基础覆盖权重：实体权重高，收盘桶权重高，影线权重低。
        weights = np.ones(len(idxs)) * 0.35
        body_lo = min(op, cl); body_hi = max(op, cl)
        for k, idx in enumerate(idxs):
            bl = edges[idx]; bu = edges[idx + 1]
            overlap_body = max(0.0, min(bu, body_hi) - max(bl, body_lo))
            overlap_all = max(1e-9, min(bu, hi) - max(bl, lo))
            if overlap_body > 0:
                weights[k] += 1.1 * (overlap_body / max(overlap_all, 1e-9))
            if bl <= cl <= bu:
                weights[k] += 0.75
        if weights.sum() <= 0:
            weights = np.ones(len(idxs))
        weights = weights / weights.sum()
        age_weight = 0.35 + 0.65 * ((i + 1) / total_len) ** 1.5
        prof.loc[idxs, "volume"] += vol * weights
        prof.loc[idxs, "amount"] += amt * weights
        prof.loc[idxs, "recent_volume"] += vol * weights * age_weight
        prof.loc[idxs, "bar_count"] += 1
        close_idx = np.searchsorted(edges, cl, side="right") - 1
        if 0 <= close_idx < n:
            prof.loc[close_idx, "close_count"] += 1
        body_a = max(0, np.searchsorted(edges, body_lo, side="right") - 1)
        body_b = min(n - 1, np.searchsorted(edges, body_hi, side="left"))
        if body_b >= body_a:
            prof.loc[body_a:body_b, "body_weight"] += 1
        rng = max(hi - lo, 1e-9)
        upper_len = hi - max(op, cl)
        lower_len = min(op, cl) - lo
        # 长上/下影共振：记录上影/下影所在区间触碰，不直接当成交密度。
        if upper_len / rng >= 0.35:
            ua = max(0, np.searchsorted(edges, max(op, cl), side="right") - 1)
            ub = min(n - 1, np.searchsorted(edges, hi, side="left"))
            if ub >= ua:
                prof.loc[ua:ub, "upper_wick_hits"] += 1
        if lower_len / rng >= 0.35:
            la = max(0, np.searchsorted(edges, lo, side="right") - 1)
            lb = min(n - 1, np.searchsorted(edges, min(op, cl), side="left"))
            if lb >= la:
                prof.loc[la:lb, "lower_wick_hits"] += 1
    for g in _xhu_gap_zones(pdf):
        a = max(0, np.searchsorted(edges, g["lower"], side="right") - 1)
        b = min(n - 1, np.searchsorted(edges, g["upper"], side="left"))
        if b >= a:
            col = "gap_up_hits" if g["type"] == "gap_up" else "gap_down_hits"
            prof.loc[a:b, col] += 1
    prof["mid"] = (prof["lower"] + prof["upper"]) / 2
    return prof



# ========================= V19.4 共振供需锚点/支撑转压力融合模块 =========================
# 目的：
# 1）不再只用右侧近端前高作为“最终压力”；
# 2）先寻找共振点最多、级别最高的供需锚点，再用该供需锚点校准供需压力带；
# 3）次高/次低收盘价共振优先级最高，其次实体顶/实体底，再其次上下影线高低点；
# 4）该逻辑不是单独打分项，而是直接融入压力带定位与报告价格计划。

def _xhu_local_turning_points(pdf, n=5, tail=260):
    """提取局部次高点/次低点。返回索引列表，避免只看绝对最高/最低。"""
    if pdf is None or pdf.empty:
        return [], []
    d = pdf.tail(int(tail)).copy().reset_index(drop=True)
    for c in ["open", "high", "low", "close", "volume"]:
        if c in d.columns:
            d[c] = pd.to_numeric(d[c], errors="coerce")
    d = d.dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)
    if len(d) < n * 2 + 3:
        return [], []
    highs, lows = [], []
    for i in range(n, len(d) - n):
        hi = safe_float(d.loc[i, "high"])
        lo = safe_float(d.loc[i, "low"])
        if hi <= 0 or lo <= 0:
            continue
        if hi >= safe_float(d.loc[i-n:i+n, "high"].max()):
            highs.append(i)
        if lo <= safe_float(d.loc[i-n:i+n, "low"].min()):
            lows.append(i)
    return highs, lows


def _xhu_cluster_price_points(points, tolerance_pct=0.008):
    """对价格点做百分比聚类。收盘/实体点自然获得更高定位权。"""
    pts = [p for p in points if safe_float(p.get("price", 0)) > 0]
    if not pts:
        return []
    pts = sorted(pts, key=lambda x: safe_float(x.get("price", 0)))
    clusters = []
    for p in pts:
        price = safe_float(p.get("price", 0))
        placed = False
        for cl in clusters:
            center = safe_float(cl.get("center", 0))
            if center > 0 and abs(price / center - 1) <= tolerance_pct:
                cl["points"].append(p)
                total_w = sum(max(0.1, safe_float(x.get("weight", 1))) for x in cl["points"])
                cl["center"] = sum(safe_float(x["price"]) * max(0.1, safe_float(x.get("weight", 1))) for x in cl["points"]) / max(total_w, 1e-9)
                cl["lower"] = min(safe_float(x["price"]) for x in cl["points"])
                cl["upper"] = max(safe_float(x["price"]) for x in cl["points"])
                placed = True
                break
        if not placed:
            clusters.append({"center": price, "lower": price, "upper": price, "points": [p]})

    for cl in clusters:
        kinds = {}
        weighted = 0.0
        close_points = 0
        entity_points = 0
        wick_points = 0
        for p in cl["points"]:
            k = p.get("kind", "")
            kinds[k] = kinds.get(k, 0) + 1
            w = max(0.1, safe_float(p.get("weight", 1)))
            weighted += w
            if "收盘" in k:
                close_points += 1
            elif "实体" in k:
                entity_points += 1
            elif "影线" in k or "高点" in k or "低点" in k:
                wick_points += 1
        priority_bonus = close_points * 2.6 + entity_points * 1.6 + wick_points * 0.8
        cl["weighted_count"] = float(weighted)
        cl["priority_score"] = float(weighted + priority_bonus)
        cl["kinds"] = kinds
        cl["close_points"] = int(close_points)
        cl["entity_points"] = int(entity_points)
        cl["wick_points"] = int(wick_points)
        cl["desc"] = "、".join([f"{k}{v}次" for k, v in sorted(kinds.items(), key=lambda kv: (-kv[1], kv[0]))])
    return sorted(clusters, key=lambda x: (safe_float(x.get("priority_score", 0)), len(x.get("points", []))), reverse=True)


def _xhu_resonance_core_lines(pdf, period="D", current_close=0.0, lookback=260):
    """
    V19.4：寻找“共振点最多、级别最高”的供需压力/支撑线。
    重点：次高/次低收盘价共振 > 实体顶/实体底共振 > 上下影线共振。
    输出的是锚点/校准线，不是单独打分项。
    """
    if pdf is None or pdf.empty:
        return []
    d = pdf.tail(int(lookback)).copy().reset_index(drop=True)
    for c in ["open", "high", "low", "close", "volume"]:
        if c in d.columns:
            d[c] = pd.to_numeric(d[c], errors="coerce")
    d = d.dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)
    if len(d) < 40:
        return []

    cur = current_close if current_close > 0 else safe_float(d.iloc[-1].get("close", 0))
    if cur <= 0:
        cur = safe_float(d.iloc[-1].get("close", 0))
    n = 3 if period == "D" else (4 if period == "W" else 2)
    highs, lows = _xhu_local_turning_points(d, n=n, tail=min(len(d), lookback))

    points = []
    for idx in highs:
        r = d.iloc[idx]
        op = safe_float(r.get("open", 0)); hi = safe_float(r.get("high", 0)); lo = safe_float(r.get("low", 0)); cl = safe_float(r.get("close", 0))
        if hi <= 0 or lo <= 0:
            continue
        body_top = max(op, cl)
        body_bottom = min(op, cl)
        rng = max(hi - lo, 1e-9)
        upper_ratio = max(0.0, hi - body_top) / rng
        close_pos = (cl - lo) / rng
        points.append({"price": cl, "weight": 4.2, "kind": "次高收盘共振", "period": period})
        points.append({"price": body_top, "weight": 2.8, "kind": "次高实体顶共振", "period": period})
        if upper_ratio >= 0.22 or close_pos <= 0.68:
            points.append({"price": hi, "weight": 1.5 + min(1.4, upper_ratio * 2.0), "kind": "次高上影线共振", "period": period})
        points.append({"price": body_bottom, "weight": 1.5, "kind": "次高实体底参考", "period": period})

    for idx in lows:
        r = d.iloc[idx]
        op = safe_float(r.get("open", 0)); hi = safe_float(r.get("high", 0)); lo = safe_float(r.get("low", 0)); cl = safe_float(r.get("close", 0))
        if hi <= 0 or lo <= 0:
            continue
        body_top = max(op, cl)
        body_bottom = min(op, cl)
        rng = max(hi - lo, 1e-9)
        lower_ratio = max(0.0, body_bottom - lo) / rng
        close_pos = (cl - lo) / rng
        points.append({"price": cl, "weight": 4.5, "kind": "次低收盘共振", "period": period})
        points.append({"price": body_bottom, "weight": 3.0, "kind": "次低实体底共振", "period": period})
        points.append({"price": body_top, "weight": 1.6, "kind": "次低实体顶参考", "period": period})
        if lower_ratio >= 0.22 or close_pos >= 0.38:
            points.append({"price": lo, "weight": 1.4 + min(1.4, lower_ratio * 2.0), "kind": "次低下影线共振", "period": period})

    try:
        plat = evaluate_platform_quality(d.tail(min(len(d), 120)))
        if safe_float(plat.get("score", 0)) >= 3.5:
            top = safe_float(plat.get("top", 0))
            bottom = safe_float(plat.get("bottom", 0))
            if top > 0:
                points.append({"price": top, "weight": 3.5, "kind": "平台/凹口上沿共振", "period": period})
            if bottom > 0:
                points.append({"price": bottom, "weight": 4.0, "kind": "平台下沿/支撑转压力共振", "period": period})
    except Exception:
        pass

    tol = 0.006 if period == "D" else (0.008 if period == "W" else 0.012)
    clusters = _xhu_cluster_price_points(points, tolerance_pct=tol)
    anchors = []
    for cl in clusters[:8]:
        center = safe_float(cl.get("center", 0))
        if center <= 0:
            continue
        dist = center / max(cur, 1e-9) - 1
        if dist < -0.06 or dist > 0.16:
            continue
        pscore = safe_float(cl.get("priority_score", 0))
        count = len(cl.get("points", []))
        if count < 3 and pscore < 8:
            continue
        line_type = "共振供需压力线" if center >= cur * 0.985 else "共振核心支撑线"
        anchors.append({
            "price": float(center),
            "type": f"{line_type}:{cl.get('desc','')}",
            "weight": float(min(4.8, 2.2 + pscore / 8.0)),
            "resonance_core": True,
            "resonance_desc": cl.get("desc", ""),
            "resonance_count": int(count),
            "resonance_priority": float(pscore),
            "band_low": float(center * (1 - max(0.0045, tol * 0.75))),
            "band_high": float(center * (1 + max(0.0065, tol * 0.95))),
            "period": period,
        })
    return anchors


def _xhu_support_resistance_flip_lines(pdf, period="D", current_close=0.0, lookback=260):
    """
    识别左侧平台下沿支撑转压力。该线优先融入主压力带。
    条件：历史平台/密集区下沿 + 多次支撑/收盘共振 + 后续有效跌破 + 当前从下方接近。
    """
    if pdf is None or pdf.empty:
        return []
    d = pdf.tail(int(lookback)).copy().reset_index(drop=True)
    for c in ["open", "high", "low", "close", "volume"]:
        if c in d.columns:
            d[c] = pd.to_numeric(d[c], errors="coerce")
    d = d.dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)
    if len(d) < 80:
        return []
    cur = current_close if current_close > 0 else safe_float(d.iloc[-1].get("close", 0))
    if cur <= 0:
        return []

    lines = []
    for w in [60, 90, 120, 180, min(250, len(d))]:
        if w < 50 or len(d) < w:
            continue
        seg = d.iloc[-w:].copy().reset_index(drop=True)
        hist = seg.iloc[:-10] if len(seg) > 70 else seg
        if len(hist) < 40:
            continue
        body_bottom = pd.concat([hist["open"], hist["close"]], axis=1).min(axis=1)
        candidate_levels = [
            safe_float(body_bottom.quantile(0.20)),
            safe_float(hist["low"].quantile(0.25)),
            safe_float(hist["close"].quantile(0.25)),
        ]
        for level in candidate_levels:
            if level <= 0:
                continue
            dist = level / max(cur, 1e-9) - 1
            if dist < -0.03 or dist > 0.10:
                continue
            tol = 0.010 if period == "D" else 0.014
            bb = pd.concat([hist["open"], hist["close"]], axis=1).min(axis=1)
            touch_mask = (hist["low"] <= level * (1 + tol)) & (hist["close"] >= level * (1 - tol))
            close_res = (abs(hist["close"] / level - 1) <= tol)
            entity_res = (abs(bb / level - 1) <= tol)
            touch_count = int(touch_mask.sum())
            resonance_count = int(close_res.sum() * 2 + entity_res.sum() + touch_count)
            after = d.tail(60)
            broken_days = int((after["close"] < level * 0.992).sum()) if len(after) else 0
            recent_reclaimed = bool(len(d.tail(10)[d.tail(10)["close"] > level * 1.006]) >= 3)
            if touch_count >= 3 and resonance_count >= 8 and broken_days >= 2 and not recent_reclaimed:
                priority = resonance_count + touch_count * 1.5 + (3 if 0 <= dist <= 0.05 else 0)
                lines.append({
                    "price": float(level),
                    "type": f"支撑转压力供需锚点:平台下沿/收盘实体共振{resonance_count}点",
                    "weight": float(min(5.2, 3.2 + priority / 18.0)),
                    "resonance_core": True,
                    "support_resistance_flip": True,
                    "resonance_desc": f"平台下沿支撑转压力，触碰{touch_count}次，共振{resonance_count}点",
                    "resonance_count": int(resonance_count),
                    "resonance_priority": float(priority),
                    "band_low": float(level * 0.995),
                    "band_high": float(level * 1.007),
                    "period": period,
                })
    if not lines:
        return []
    clusters = _xhu_cluster_price_points(lines, tolerance_pct=0.008 if period == "D" else 0.012)
    out = []
    for cl in clusters[:4]:
        pts = cl.get("points", [])
        best = sorted(pts, key=lambda x: safe_float(x.get("resonance_priority", x.get("weight", 0))), reverse=True)[0]
        center = safe_float(cl.get("center", best.get("price", 0)))
        best = dict(best)
        best["price"] = float(center)
        best["band_low"] = float(min(safe_float(p.get("band_low", p.get("price", center))) for p in pts))
        best["band_high"] = float(max(safe_float(p.get("band_high", p.get("price", center))) for p in pts))
        out.append(best)
    return out

# ======================= V19.4 共振供需锚点/支撑转压力融合模块 END =======================


def _xhu_structural_anchors(pdf, period="D", current_close=0.0):
    anchors = []
    if pdf is None or pdf.empty:
        return anchors
    d = pdf.copy().reset_index(drop=True)
    for c in ["open", "high", "low", "close", "volume"]:
        d[c] = pd.to_numeric(d[c], errors="coerce")
    d = d.dropna(subset=["open", "high", "low", "close", "volume"])
    if d.empty:
        return anchors
    # 最大量有效阳K、阶段最高/次高、平台上沿/凹口、假突破高点。
    mv = _find_valid_max_volume_bull_levels(d, f"{period}线", lookback=min(len(d), 120 if period in ("D", "W") else 100))
    if mv.get("valid"):
        anchors.append({"price": safe_float(mv.get("high")), "type": "有效最大量阳K高点", "weight": 2.2})
        anchors.append({"price": safe_float(mv.get("floor")), "type": "有效最大量阳K实底", "weight": 1.0})
    highs = d["high"].tail(min(len(d), 120)).dropna()
    if len(highs) >= 10:
        anchors.append({"price": safe_float(highs.max()), "type": "阶段最高点", "weight": 1.6})
        anchors.append({"price": safe_float(highs.quantile(0.92)), "type": "次高密集区", "weight": 1.4})
    try:
        plat = evaluate_platform_quality(d.tail(min(len(d), 80)))
        if safe_float(plat.get("score", 0)) >= 4 and safe_float(plat.get("top", 0)) > 0:
            anchors.append({"price": safe_float(plat.get("top")), "type": "平台/凹口上沿", "weight": 1.8})
    except Exception:
        pass
    # 假突破/上影高点：高点越过近60日分位但收盘回落，作为供应记忆。
    if len(d) >= 30:
        roll_high = d["high"].rolling(30).max().shift(1)
        rng = (d["high"] - d["low"]).replace(0, np.nan)
        body_top = pd.concat([d["open"], d["close"]], axis=1).max(axis=1)
        upper_ratio = (d["high"] - body_top) / rng
        fb = d[(d["high"] >= roll_high * 1.003) & (d["close"] < d["high"] * 0.985) & (upper_ratio >= 0.30)]
        for _, r in fb.tail(3).iterrows():
            anchors.append({"price": safe_float(r.get("high")), "type": "假突破/长上影高点", "weight": 1.9})
    # V19.4：把“共振点最多、级别最高”的供需锚点直接并入结构锚点。
    # 收盘共振/实体共振优先于影线共振；支撑转压力主位优先级高于普通近端小高点。
    try:
        anchors.extend(_xhu_resonance_core_lines(d, period=period, current_close=current_close, lookback=min(len(d), 260 if period in ("D", "W") else 160)))
    except Exception:
        pass
    try:
        anchors.extend(_xhu_support_resistance_flip_lines(d, period=period, current_close=current_close, lookback=min(len(d), 260 if period in ("D", "W") else 160)))
    except Exception:
        pass
    return [a for a in anchors if safe_float(a.get("price", 0)) > 0]


def _xhu_score_zone_reaction(prof_seg):
    if prof_seg is None or prof_seg.empty:
        return 0.0, ""
    uw = safe_float(prof_seg["upper_wick_hits"].sum())
    lw = safe_float(prof_seg["lower_wick_hits"].sum())
    gd = safe_float(prof_seg["gap_down_hits"].sum())
    gu = safe_float(prof_seg["gap_up_hits"].sum())
    supply = min(18.0, uw * 1.4 + gd * 2.2)
    demand = min(10.0, lw * 1.0 + gu * 1.5)
    desc = []
    if uw >= 2:
        desc.append(f"上影共振{int(uw)}次")
    if gd >= 1:
        desc.append(f"向下缺口共振{int(gd)}次")
    if lw >= 2:
        desc.append(f"下影承接{int(lw)}次")
    if gu >= 1:
        desc.append(f"向上缺口承接{int(gu)}次")
    return max(0.0, supply - demand * 0.25), "、".join(desc)


def _xhu_extract_period_pressure_zones(period_df, period="D", lookback=250, current_close=0.0):
    """单周期：百分比桶Volume Profile -> 高成交密集区 -> 供应反应/结构锚点校准 -> 压力区。"""
    if period_df is None or period_df.empty:
        return []
    d = period_df.tail(int(lookback)).copy().reset_index(drop=True)
    if len(d) < 8:
        return []
    for c in ["open", "high", "low", "close", "volume"]:
        d[c] = pd.to_numeric(d[c], errors="coerce")
    d = d.dropna(subset=["open", "high", "low", "close", "volume"]).reset_index(drop=True)
    if len(d) < 8:
        return []
    cur = current_close if current_close > 0 else safe_float(d.iloc[-1].get("close", 0))
    minp = max(0.01, safe_float(d["low"].min()) * 0.98)
    maxp = max(minp * 1.05, safe_float(d["high"].max()) * 1.02)
    bucket_pct = _xhu_bucket_pct_for_df(d, period)
    edges = _xhu_make_log_edges(minp, maxp, bucket_pct)
    prof = _xhu_assign_ohlcv_to_profile(d, edges)
    if prof.empty:
        return []
    # 成交密度：成交量、成交额、收盘/实体接受度、近期权重。
    for col in ["volume", "amount", "recent_volume", "close_count", "body_weight"]:
        mx = max(safe_float(prof[col].max()), 1e-9)
        prof[col + "_rank"] = prof[col] / mx
    prof["density_score"] = (
        prof["volume_rank"] * 36 + prof["amount_rank"] * 24 + prof["recent_volume_rank"] * 18
        + prof["close_count_rank"] * 12 + prof["body_weight_rank"] * 10
    )
    dens = prof["density_score"].replace([np.inf, -np.inf], np.nan).dropna()
    if dens.empty:
        return []
    thr = max(float(dens.quantile(0.78)), float(dens.mean() + dens.std() * 0.35))
    mask = prof["density_score"] >= thr
    zones = []
    start = None
    for i, flag in enumerate(mask.tolist() + [False]):
        if flag and start is None:
            start = i
        if (not flag) and start is not None:
            end = i - 1
            seg = prof.iloc[start:end + 1]
            lower = safe_float(seg["lower"].min()); upper = safe_float(seg["upper"].max())
            if upper <= 0 or lower <= 0:
                start = None; continue
            # 只关心当前价上方/附近的供应区；已经远在下方的归为支撑，不参与压力突破。
            if upper < cur * 0.965:
                start = None; continue
            density = safe_float(seg["density_score"].mean())
            peak = safe_float(seg["density_score"].max())
            reaction, rdesc = _xhu_score_zone_reaction(seg)
            anchors = _xhu_structural_anchors(d, period, current_close=cur)
            anchor_hits = []
            tol = max(bucket_pct * 2.2, 0.012)
            adj_lower, adj_upper = lower, upper
            anchor_score = 0.0
            for a in anchors:
                price = safe_float(a.get("price", 0)); wt = safe_float(a.get("weight", 1.0))
                # V19.4：共振供需锚点/支撑转压力线允许以自己的窄压力带参与校准，而不被普通成交密集桶吞没。
                a_low = safe_float(a.get("band_low", price))
                a_high = safe_float(a.get("band_high", price))
                in_zone = (lower * (1 - tol) <= price <= upper * (1 + tol))
                band_overlap = (a_high >= lower * (1 - tol) and a_low <= upper * (1 + tol))
                if in_zone or band_overlap:
                    anchor_hits.append(a.get("type", "锚点"))
                    anchor_score += wt * (5.2 if a.get("resonance_core") else 4.0)
                    # 锚点可校准边界，但不能把区间无限拉宽；共振供需锚点优先校准核心带。
                    if a.get("resonance_core"):
                        adj_lower = min(adj_lower, max(a_low, lower * (1 - tol * 1.8)))
                        adj_upper = max(adj_upper, min(a_high, upper * (1 + tol * 1.8)))
                    else:
                        if price >= upper * 0.985 and price <= upper * (1 + tol):
                            adj_upper = max(adj_upper, price)
                        if price <= lower * 1.015 and price >= lower * (1 - tol):
                            adj_lower = min(adj_lower, price)
            width = adj_upper / max(adj_lower, 1e-9) - 1
            width_score = 8.0 if width <= bucket_pct * 8 else (4.0 if width <= bucket_pct * 14 else -4.0)
            freshness = 4.0 if period in ("M", "Q", "Y") else 2.0
            quality = max(0.0, min(100.0, density * 0.55 + peak * 0.25 + reaction + anchor_score + width_score + freshness))
            if quality >= max(18.0, XHU_PRESSURE_MIN_QUALITY * 0.45):
                zones.append({
                    "period": period,
                    "lower": float(adj_lower), "upper": float(adj_upper), "mid": float((adj_lower + adj_upper) / 2),
                    "density_score": float(density), "peak_density": float(peak),
                    "reaction_score": float(reaction), "anchor_score": float(anchor_score),
                    "quality_score": float(quality), "width_pct": float(width),
                    "bucket_pct": float(bucket_pct), "anchor_hits": list(sorted(set(anchor_hits))),
                    "reaction_desc": rdesc, "period_weight": float(_xhu_period_weight(period)),
                })
            start = None
    # V19.4：若共振供需锚点/支撑转压力线没有落进成交密集桶，也必须作为候选压力带进入合并。
    # 这解决“模型只识别右侧近端小压力，漏掉左侧平台下沿主压力”的问题。
    try:
        resonance_anchors = [a for a in anchors if a.get("resonance_core")]
        for a in resonance_anchors:
            price = safe_float(a.get("price", 0))
            if price <= 0:
                continue
            dist = price / max(cur, 1e-9) - 1
            if dist < -0.04 or dist > 0.12:
                continue
            b_low = safe_float(a.get("band_low", price * 0.995))
            b_high = safe_float(a.get("band_high", price * 1.007))
            if b_high <= 0 or b_low <= 0:
                continue
            exists = any((z.get("upper", 0) >= b_low * 0.995 and z.get("lower", 0) <= b_high * 1.005) for z in zones)
            if not exists:
                q = 38.0 + min(38.0, safe_float(a.get("resonance_priority", 0)) * 1.7) + (8.0 if a.get("support_resistance_flip") else 0.0)
                zones.append({
                    "period": period,
                    "lower": float(b_low), "upper": float(b_high), "mid": float(price),
                    "density_score": 0.0, "peak_density": 0.0,
                    "reaction_score": 0.0, "anchor_score": float(safe_float(a.get("weight", 1)) * 6.0),
                    "quality_score": float(min(100.0, q)), "width_pct": float(b_high / max(b_low, 1e-9) - 1),
                    "bucket_pct": float(bucket_pct), "anchor_hits": [a.get("type", "共振供需锚点")],
                    "reaction_desc": a.get("resonance_desc", ""), "period_weight": float(_xhu_period_weight(period) + 0.25),
                    "resonance_core_line": float(price),
                    "resonance_desc": a.get("resonance_desc", ""),
                    "support_resistance_flip": bool(a.get("support_resistance_flip", False)),
                })
    except Exception:
        pass

    # 保留离当前近且质量高的压力区。当前正在内部的也保留。
    zones = sorted(zones, key=lambda z: (safe_float(z["quality_score"]) - max(0.0, z["lower"] / max(cur, 1e-9) - 1) * 20), reverse=True)
    return zones[:max(1, XHU_PRESSURE_MAX_ZONES_PER_PERIOD)]


def _xhu_period_dfs(df):
    out = [("D", df.copy(), 500)]
    try:
        out.append(("W", _resample_ohlcv(df, "W-FRI"), 220))
        out.append(("M", _resample_ohlcv(df, "ME"), 120))
        out.append(("Q", _resample_ohlcv(df, "QE"), 60))
        if XHU_PRESSURE_ENABLE_YEARLY == "1":
            out.append(("Y", _resample_ohlcv(df, "YE"), 25))
    except Exception:
        pass
    return out


def _xhu_merge_multi_period_zones(zones, current_close):
    """核心：投影各周期压力区到统一百分比桶，找多周期重叠最密集供需压力带，同时计算并集最高上沿。"""
    empty = {
        "valid": False, "core_lower": 0.0, "core_upper": 0.0, "union_lower": 0.0, "union_upper": 0.0,
        "final_union_upper": 0.0, "core_periods": [], "dominant_period": "", "overlap_score": 0.0,
        "pressure_zone_grade": "D", "pressure_quality_score": 0.0, "period_count": 0, "desc": "无有效多周期压力带"
    }
    if not zones or current_close <= 0:
        return empty
    minp = min(z["lower"] for z in zones) * 0.995
    maxp = max(z["upper"] for z in zones) * 1.005
    edges = _xhu_make_log_edges(minp, maxp, max(0.0035, min(0.008, XHU_PRESSURE_DEFAULT_BUCKET_PCT)))
    n = len(edges) - 1
    scores = np.zeros(n)
    period_sets = [set() for _ in range(n)]
    zone_ids = [set() for _ in range(n)]
    for zi, z in enumerate(zones):
        a = max(0, np.searchsorted(edges, z["lower"], side="right") - 1)
        b = min(n - 1, np.searchsorted(edges, z["upper"], side="left"))
        if b < a:
            continue
        add = safe_float(z.get("period_weight", 1.0)) * max(1.0, safe_float(z.get("quality_score", 0.0)) / 25.0)
        for i in range(a, b + 1):
            scores[i] += add
            period_sets[i].add(z.get("period", ""))
            zone_ids[i].add(zi)
    if scores.max() <= 0:
        return empty
    max_score = scores.max()
    # 核心重叠区：得分最高的连续区间；至少取最高桶，避免交集为空。
    mask = scores >= max(max_score * 0.74, max_score - 1e-9 if max_score < 2 else max_score * 0.74)
    # 优先选择当前价上方或当前所在的核心区。
    best = None
    start = None
    for i, flag in enumerate(mask.tolist() + [False]):
        if flag and start is None:
            start = i
        if (not flag) and start is not None:
            end = i - 1
            lower = edges[start]; upper = edges[end + 1]
            seg_score = float(scores[start:end + 1].mean())
            if upper >= current_close * 0.97:
                dist_penalty = max(0.0, lower / current_close - 1) * 2
                cand = (seg_score - dist_penalty, start, end, lower, upper)
                if best is None or cand[0] > best[0]:
                    best = cand
            start = None
    if best is None:
        i = int(np.argmax(scores)); best = (scores[i], i, i, edges[i], edges[i+1])
    _, s, e, core_lower, core_upper = best
    core_periods = sorted(set().union(*period_sets[s:e+1]))
    related_ids = set().union(*zone_ids[s:e+1])
    # 相关压力区：与核心区有重叠或边界距离较近的周期带。用其并集计算最终压力上沿。
    for zi, z in enumerate(zones):
        # V19.4 near/far rule:
        # - 与核心区<=3%默认合并；
        # - 3%-5%之间，如果同属压力密集/共振结构，也合并为同一区域；
        # - >5%通常拆层，不把远端压力硬拉入当前确认价。
        z_low = safe_float(z.get("lower", 0)); z_up = safe_float(z.get("upper", 0))
        if z_up <= 0 or z_low <= 0:
            continue
        gap_up = max(0.0, z_low / max(core_upper, 1e-9) - 1.0)
        gap_down = max(0.0, core_lower / max(z_up, 1e-9) - 1.0)
        gap = max(gap_up, gap_down)
        overlap = z_up >= core_lower * 0.985 and z_low <= core_upper * 1.015
        near = gap <= V19_PRESSURE_MERGE_NEAR_PCT
        mid_related = gap <= V19_PRESSURE_MERGE_MID_PCT and (
            bool(z.get("resonance_core_line", 0))
            or safe_float(z.get("anchor_score", 0)) >= 8
            or safe_float(z.get("density_score", 0)) >= 25
            or safe_float(z.get("quality_score", 0)) >= 45
        )
        if overlap or near or mid_related:
            related_ids.add(zi)
    related = [zones[i] for i in sorted(related_ids)] if related_ids else zones

    # V19.4：共振供需锚点具有“定位权”。如果相关压力区中存在收盘/实体/影线多点共振线，
    # 则供需压力带围绕该线校准，而不是只沿用右侧近端小压力。
    resonance_related = [z for z in related if safe_float(z.get("resonance_core_line", 0)) > 0]
    resonance_core_line = 0.0
    resonance_desc = ""
    if resonance_related:
        best_res = sorted(
            resonance_related,
            key=lambda z: (safe_float(z.get("quality_score", 0)), bool(z.get("support_resistance_flip", False)), safe_float(z.get("resonance_core_line", 0))),
            reverse=True
        )[0]
        resonance_core_line = safe_float(best_res.get("resonance_core_line", best_res.get("mid", 0)))
        resonance_desc = best_res.get("resonance_desc", "") or "共振供需锚点"
        rlow = safe_float(best_res.get("lower", 0)); rhigh = safe_float(best_res.get("upper", 0))
        if rlow > 0 and rhigh > 0:
            if rhigh >= current_close * 0.965 and rlow <= current_close * 1.12:
                core_lower = min(core_lower, rlow)
                core_upper = max(core_upper, rhigh)

    union_lower = min(z["lower"] for z in related)
    union_upper = max(z["upper"] for z in related)
    period_count = len(sorted(set(z["period"] for z in related)))
    raw_quality = (
        max_score * 11.0 + period_count * 8.0 + sum(safe_float(z.get("quality_score", 0)) for z in related) / max(1, len(related)) * 0.35
    )
    if resonance_core_line > 0:
        raw_quality += 8.0
    # 年线只做终极压力校验：若年线参与相关区，增加等级但不让过宽区间失真。
    if "Y" in [z.get("period") for z in related]:
        raw_quality += 6.0
    pressure_quality = max(0.0, min(100.0, raw_quality))
    grade = _xhu_letter_grade(pressure_quality)
    dominant = sorted(related, key=lambda z: safe_float(z.get("period_weight", 1)) * safe_float(z.get("quality_score", 0)), reverse=True)[0].get("period", "")
    desc = (
        f"核心重叠压力带{core_lower:.2f}-{core_upper:.2f}，整体压力区{union_lower:.2f}-{union_upper:.2f}，"
        f"最终上沿{union_upper:.2f}，参与周期{','.join(sorted(set(z['period'] for z in related)))}，等级{grade}"
    )
    if resonance_core_line > 0:
        gap_to_final = abs(safe_float(union_upper) / max(resonance_core_line, 1e-9) - 1.0)
        if gap_to_final <= V19_PRESSURE_MERGE_NEAR_PCT:
            merge_note = "同区近线，确认价取高线"
        elif gap_to_final <= V19_PRESSURE_MERGE_MID_PCT:
            merge_note = "同区中距，突破高线才给较高分"
        else:
            merge_note = "与上方压力拆层"
        desc += f"；共振供需锚点{resonance_core_line:.2f}（{resonance_desc}，{merge_note}）"
    return {
        "valid": True,
        "core_lower": float(core_lower), "core_upper": float(core_upper),
        "union_lower": float(union_lower), "union_upper": float(union_upper), "final_union_upper": float(union_upper),
        "core_periods": core_periods, "dominant_period": dominant,
        "overlap_score": float(max_score), "pressure_quality_score": float(pressure_quality),
        "pressure_zone_grade": grade, "period_count": int(period_count), "desc": desc,
        "resonance_core_line": float(resonance_core_line),
        "resonance_core_desc": resonance_desc,
        "effective_confirm_price": float(union_upper),
        "pressure_merge_gap_pct": float(abs(safe_float(union_upper) / max(resonance_core_line, 1e-9) - 1.0) if resonance_core_line > 0 else 0.0),
        "related_zones": related[:8],
    }


def _xhu_detect_fake_breakout_memory(df, level):
    if df is None or df.empty or level <= 0:
        return {"count": 0, "high": 0.0, "desc": ""}
    d = df.tail(180).copy().reset_index(drop=True)
    for c in ["open", "high", "low", "close", "volume"]:
        d[c] = pd.to_numeric(d[c], errors="coerce")
    d = d.dropna(subset=["open", "high", "low", "close", "volume"])
    if len(d) < 5:
        return {"count": 0, "high": 0.0, "desc": ""}
    rng = (d["high"] - d["low"]).replace(0, np.nan)
    body_top = pd.concat([d["open"], d["close"]], axis=1).max(axis=1)
    upper_ratio = (d["high"] - body_top) / rng
    vol_ma = d["volume"].rolling(20).mean()
    mask = (d["high"] >= level * 1.002) & ((d["close"] < level * 1.003) | (upper_ratio >= 0.35))
    mask = mask & ((d["volume"] >= vol_ma * 1.05) | (upper_ratio >= 0.40))
    hits = d[mask]
    if hits.empty:
        return {"count": 0, "high": 0.0, "desc": ""}
    h = safe_float(hits["high"].max())
    return {"count": int(len(hits)), "high": float(h), "desc": f"假突破/长上影记忆{len(hits)}次，高点{h:.2f}"}


def _xhu_grade_breakout_day(df, composite):
    empty = {"breakout_day_grade": "D", "breakout_score": 0.0, "setup_grade": "D", "setup_score": 0.0, "desc": "无有效突破"}
    if df is None or df.empty or not composite.get("valid"):
        return empty
    cur = df.iloc[-1]
    prev = df.iloc[-2] if len(df) >= 2 else cur
    op = safe_float(cur.get("open", 0)); cl = safe_float(cur.get("close", 0)); hi = safe_float(cur.get("high", 0)); lo = safe_float(cur.get("low", 0))
    vol = safe_float(cur.get("volume", 0)); prev_vol = safe_float(prev.get("volume", 0))
    core_upper = safe_float(composite.get("core_upper", 0)); union_upper = safe_float(composite.get("final_union_upper", 0))
    if min(op, cl, hi, lo, core_upper, union_upper) <= 0:
        return empty
    fake = _xhu_detect_fake_breakout_memory(df.iloc[:-1], union_upper)
    fake_high = safe_float(fake.get("high", 0.0))
    must_high = max(union_upper, fake_high)
    rng = max(hi - lo, 1e-9)
    body_top = max(op, cl); body_bottom = min(op, cl); body_len = max(body_top - body_bottom, 1e-9)
    close_pos = (cl - lo) / rng
    upper_shadow_ratio = (hi - body_top) / rng
    body_above_union = max(0.0, body_top - max(body_bottom, union_upper)) / body_len
    body_above_core = max(0.0, body_top - max(body_bottom, core_upper)) / body_len
    vr1 = vol / prev_vol if prev_vol > 0 else 0.0
    vol_ma20 = safe_float(df["volume"].tail(20).mean()) if "volume" in df.columns and len(df) >= 20 else 0.0
    volr = vol / vol_ma20 if vol_ma20 > 0 else 0.0
    healthy_vol = ((1.45 <= vr1 <= 3.2) or (1.35 <= volr <= 4.2)) and not (vr1 > 4.5 and volr > 5.5)
    is_limit = safe_float(cur.get("pct_chg", 0)) >= 9.3
    if is_limit and cl >= union_upper * 1.003 and close_pos >= 0.85:
        healthy_vol = healthy_vol or (volr >= 1.1 and vr1 <= 5.5)
    breakout_core = cl >= core_upper * 1.003
    breakout_union = cl >= union_upper * 1.003
    breakout_fake = (fake_high <= 0) or (cl >= fake_high * 1.002)
    wick_probe = hi >= core_upper and (not breakout_core or close_pos < 0.60 or upper_shadow_ratio > 0.35)
    score = 0.0
    reasons = []
    if breakout_core:
        score += 18; reasons.append("突破核心重叠压力带")
    if breakout_union:
        score += 26; reasons.append("突破最终压力上沿")
    if breakout_fake and fake_high > 0:
        score += 12; reasons.append("突破前次假突破高点")
    elif fake_high > 0 and cl < fake_high:
        score -= 8; reasons.append("仍未突破前次假突破高点")
    if body_above_union >= 0.65:
        score += 12; reasons.append("实体大部在最终上沿之上")
    elif body_above_core >= 0.55:
        score += 6; reasons.append("实体站上供需压力")
    if close_pos >= 0.85:
        score += 8; reasons.append("强收盘")
    elif close_pos >= 0.70:
        score += 4; reasons.append("较强收盘")
    if upper_shadow_ratio <= 0.18:
        score += 5
    elif upper_shadow_ratio >= 0.35:
        score -= 7; reasons.append("上影偏长")
    if healthy_vol:
        score += 10; reasons.append("量能健康确认")
    else:
        score -= 6; reasons.append("量能未达健康突破")
    if wick_probe:
        score -= 18; reasons.append("影线试探/冲关不稳")
    if cl < union_upper and hi >= union_upper:
        score -= 20; reasons.append("冲击最终上沿失败")
    score = max(0.0, min(100.0, score))
    # 日K等级严格看是否完整穿透。
    if breakout_core and breakout_union and breakout_fake and score >= 72:
        day_grade = "S"
    elif ((breakout_core and score >= 58) or (breakout_union and score >= 55)):
        day_grade = "A"
    elif (hi >= core_upper or cl >= composite.get("union_lower", 0) * 1.003):
        day_grade = "B" if score >= 36 else "C"
    elif wick_probe:
        day_grade = "D"
    else:
        day_grade = "D"
    return {
        "breakout_day_grade": day_grade,
        "breakout_score": float(score),
        "breakout_core": bool(breakout_core),
        "breakout_union_upper": bool(breakout_union),
        "breakout_fake_high": bool(breakout_fake),
        "fake_breakout_count": int(fake.get("count", 0)),
        "fake_breakout_high": float(fake_high),
        "body_above_union_ratio": float(body_above_union),
        "close_position": float(close_pos),
        "upper_shadow_ratio": float(upper_shadow_ratio),
        "volume_confirm": bool(healthy_vol),
        "desc": "；".join(reasons[:8]) + ("；" + fake.get("desc", "") if fake.get("desc") else ""),
    }


def _xhu_combine_pressure_setup_grade(zone_grade, day_grade, zone_score, day_score):
    order = {"S": 4, "A": 3, "B": 2, "C": 1, "D": 0}
    z = order.get(zone_grade, 0); d = order.get(day_grade, 0)
    # 高等级压力带 + 高质量日K才可S；低级压力带即使日K强也不直接S。
    if z >= 4 and d >= 4:
        return "S", 18.0
    if z >= 3 and d >= 4:
        return "A", 15.0
    if z >= 4 and d >= 3:
        return "A", 14.0
    if z >= 3 and d >= 3:
        return "A", 12.0
    if z >= 2 and d >= 4:
        return "B", 9.0
    if z >= 2 and d >= 3:
        return "B", 7.0
    if d <= 0:
        return "D", 0.0
    return "C", 3.0




# ========================= V25.8 去单线主轴 + 缠论级别递归 + 量价时空机构评分 =========================
# 重要：本段已彻底移除“供需压力带”主模型。
# 保留的是多周期供需压力带、平台/凹口/破底翻、量价承接、时间成熟度、空间赔率、风险执行。
# 机构化原则：机会分类 -> 价量时空四轴 -> 同源封顶 -> 风险收益比 -> 执行过滤 -> 复盘校准。


def _v258_pressure_empty():
    return {
        "score_xhu_pressure_breakout": 0.0,
        "xhu_pressure_model_grade": "D",
        "xhu_pressure_zone_grade": "D",
        "xhu_breakout_day_grade": "D",
        "xhu_pressure_core_lower": 0.0,
        "xhu_pressure_core_upper": 0.0,
        "xhu_pressure_union_lower": 0.0,
        "xhu_pressure_union_upper": 0.0,
        "xhu_final_union_upper": 0.0,
        "xhu_pressure_quality_score": 0.0,
        "xhu_pressure_overlap_score": 0.0,
        "xhu_pressure_periods": "",
        "xhu_pressure_desc": "无有效多周期供需压力带",
        "xhu_breakout_desc": "无供需压力带高级突破",
        "xhu_fake_breakout_count": 0,
        "xhu_fake_breakout_high": 0.0,
        # 兼容旧字段：不再代表供需锚点，仅作为共振锚点/供需锚点价格。
        "xhu_resonance_core_line": 0.0,
        "xhu_resonance_core_desc": "",
        "xhu_effective_confirm_price": 0.0,
        "xhu_pressure_merge_gap_pct": 0.0,
        "xhu_pressure_json": "[]",
        # 兼容旧字段：彻底去供需锚点后统一置零/空，不再参与主评分。
        "xhu_coreline_meat_space_pct": 0.0,
        "xhu_coreline_next_major_pressure": 0.0,
        "xhu_coreline_prep_score20": 0.0,
        "xhu_coreline_prep_desc": "",
        "xhu_coreline_role": "",
        "xhu_coreline_core_score": 0.0,
        "xhu_coreline_neural_score": 0.0,
        "xhu_coreline_hvn_score": 0.0,
        "xhu_coreline_lvn_above_score": 0.0,
        "xhu_coreline_upper_supply_thinness": 0.0,
    }


def _v258_collect_supply_pressure_zones(df, current_close=0.0):
    zones = []
    try:
        for period, pdf, lookback in _xhu_period_dfs(df):
            try:
                zs = _xhu_extract_period_pressure_zones(
                    pdf, period=period, lookback=lookback, current_close=current_close
                )
                zones.extend(zs or [])
            except Exception as e:
                print(f"供需压力带单周期识别失败：period={period} error={str(e)[:80]}")
    except Exception as e:
        print(f"供需压力带周期构造失败：error={str(e)[:120]}")
    return zones


def detect_xuanhu_pressure_band_breakout_model(df, code=""):
    """
    V25.8：多周期供需压力带突破模型。
    已彻底移除“供需压力带”主逻辑，不再寻找某一条神奇线。
    本模型只做：多周期供需区 -> 压力带质量 -> 最终上沿 -> 突破/回踩/假突破记忆。
    字段名保留 xhu_* 是为了兼容旧主流程与报告，不代表继续使用供需锚点体系。
    """
    empty = _v258_pressure_empty()
    if ENABLE_XHU_PRESSURE_BREAKOUT != "1" or df is None or len(df) < 180:
        return empty
    try:
        d = df.copy().reset_index(drop=True)
        for c in ["open", "high", "low", "close", "volume"]:
            if c not in d.columns:
                return empty
            d[c] = pd.to_numeric(d[c], errors="coerce")
        d = d.dropna(subset=["open", "high", "low", "close", "volume"]).reset_index(drop=True)
        if len(d) < 180:
            return empty
        current_close = safe_float(d["close"].iloc[-1])
        if current_close <= 0:
            return empty
        zones = _v258_collect_supply_pressure_zones(d, current_close=current_close)
        if not zones:
            return empty
        comp = _xhu_merge_multi_period_zones(zones, current_close)
        if not comp.get("valid"):
            return empty
        day = _xhu_grade_breakout_day(d, comp)
        zone_grade = comp.get("pressure_zone_grade", "D")
        day_grade = day.get("breakout_day_grade", "D")
        zone_score = safe_float(comp.get("pressure_quality_score", 0.0))
        day_score = safe_float(day.get("breakout_score", 0.0))
        model_grade, model_score = _xhu_combine_pressure_setup_grade(zone_grade, day_grade, zone_score, day_score)
        fake = _xhu_detect_fake_breakout_memory(d.iloc[:-1], safe_float(comp.get("effective_confirm_price", comp.get("final_union_upper", 0.0))))
        score = max(0.0, min(100.0, model_score + min(8.0, safe_float(comp.get("overlap_score", 0.0)) * 0.8)))
        desc = str(comp.get("desc", ""))
        desc = desc.replace("核心重叠压力带", "多周期重叠供需压力带").replace("共振供需锚点", "共振锚点")
        return {
            "score_xhu_pressure_breakout": float(score),
            "xhu_pressure_model_grade": model_grade,
            "xhu_pressure_zone_grade": zone_grade,
            "xhu_breakout_day_grade": day_grade,
            "xhu_pressure_core_lower": float(comp.get("core_lower", 0.0)),
            "xhu_pressure_core_upper": float(comp.get("core_upper", 0.0)),
            "xhu_pressure_union_lower": float(comp.get("union_lower", 0.0)),
            "xhu_pressure_union_upper": float(comp.get("union_upper", 0.0)),
            "xhu_final_union_upper": float(comp.get("final_union_upper", comp.get("union_upper", 0.0))),
            "xhu_pressure_quality_score": float(zone_score),
            "xhu_pressure_overlap_score": float(comp.get("overlap_score", 0.0)),
            "xhu_pressure_periods": ",".join(comp.get("core_periods", [])),
            "xhu_pressure_desc": desc,
            "xhu_breakout_desc": str(day.get("desc", "")).replace("供需锚点", "供需压力带"),
            "xhu_fake_breakout_count": int(fake.get("count", 0) or 0),
            "xhu_fake_breakout_high": float(fake.get("high", 0.0) or 0.0),
            "xhu_resonance_core_line": float(comp.get("resonance_core_line", 0.0)),
            "xhu_resonance_core_desc": str(comp.get("resonance_core_desc", "")).replace("供需锚点", "锚点"),
            "xhu_effective_confirm_price": float(comp.get("effective_confirm_price", comp.get("final_union_upper", 0.0))),
            "xhu_pressure_merge_gap_pct": float(comp.get("pressure_merge_gap_pct", 0.0)),
            "xhu_pressure_json": json.dumps(comp.get("related_zones", [])[:8], ensure_ascii=False)[:2200],
            "xhu_coreline_meat_space_pct": 0.0,
            "xhu_coreline_next_major_pressure": 0.0,
            "xhu_coreline_prep_score20": 0.0,
            "xhu_coreline_prep_desc": "",
            "xhu_coreline_role": "",
            "xhu_coreline_core_score": 0.0,
            "xhu_coreline_neural_score": 0.0,
            "xhu_coreline_hvn_score": 0.0,
            "xhu_coreline_lvn_above_score": 0.0,
            "xhu_coreline_upper_supply_thinness": 0.0,
        }
    except Exception as e:
        print(f"V25.8供需压力带模型失败：code={code} error={str(e)[:120]}")
        return empty


def _v258_prepare_daily(df, tail=260):
    if df is None or len(df) < 30:
        return pd.DataFrame()
    d = df.copy().reset_index(drop=True)
    for c in ["open", "high", "low", "close", "volume", "amount"]:
        if c in d.columns:
            d[c] = pd.to_numeric(d[c], errors="coerce")
    d = d.dropna(subset=["open", "high", "low", "close", "volume"]).reset_index(drop=True)
    return d.tail(tail).reset_index(drop=True)


def _chan_fractals(d, order=2):
    highs = pd.to_numeric(d["high"], errors="coerce").values
    lows = pd.to_numeric(d["low"], errors="coerce").values
    out = []
    n = len(d)
    for i in range(order, n - order):
        hi = highs[i]; lo = lows[i]
        if not np.isfinite(hi) or not np.isfinite(lo):
            continue
        if hi >= np.nanmax(highs[i-order:i+order+1]) and hi > max(highs[i-1], highs[i+1]):
            out.append({"idx": i, "type": "top", "price": float(hi)})
        if lo <= np.nanmin(lows[i-order:i+order+1]) and lo < min(lows[i-1], lows[i+1]):
            out.append({"idx": i, "type": "bottom", "price": float(lo)})
    out = sorted(out, key=lambda x: x["idx"])
    # 去掉连续同类分型，只保留更极端的一个，形成更稳定的笔候选。
    cleaned = []
    for f in out:
        if not cleaned or cleaned[-1]["type"] != f["type"]:
            cleaned.append(f)
        else:
            last = cleaned[-1]
            if f["type"] == "top" and f["price"] >= last["price"]:
                cleaned[-1] = f
            elif f["type"] == "bottom" and f["price"] <= last["price"]:
                cleaned[-1] = f
    return cleaned


def _chan_bi_list(d):
    fs = _chan_fractals(d, order=2)
    bis = []
    for a, b in zip(fs[:-1], fs[1:]):
        if a["type"] == b["type"]:
            continue
        if b["idx"] - a["idx"] < 3:
            continue
        direction = "up" if a["type"] == "bottom" and b["type"] == "top" else "down"
        start = int(a["idx"]); end = int(b["idx"])
        seg = d.iloc[start:end+1]
        v0 = safe_float(seg["volume"].sum())
        amount0 = safe_float(seg["amount"].sum()) if "amount" in seg.columns else 0.0
        ret = b["price"] / max(a["price"], 1e-9) - 1.0
        bis.append({
            "start_idx": start,
            "end_idx": end,
            "direction": direction,
            "start_price": float(a["price"]),
            "end_price": float(b["price"]),
            "duration": int(end - start + 1),
            "return_pct": float(ret),
            "volume_sum": float(v0),
            "amount_sum": float(amount0),
        })
    return bis


def _chan_recent_pivot(d, bis):
    if d is None or d.empty:
        return {"valid": False, "upper": 0.0, "lower": 0.0, "mid": 0.0, "duration": 0, "amp": 0.0, "desc": "无中枢"}
    w = d.tail(45).copy()
    if len(bis) >= 5:
        recent = bis[-5:]
        upper = min(max(x["start_price"], x["end_price"]) for x in recent)
        lower = max(min(x["start_price"], x["end_price"]) for x in recent)
        if upper > lower > 0:
            start = min(x["start_idx"] for x in recent)
            duration = int(len(d) - start)
            mid = (upper + lower) / 2
            amp = (upper - lower) / max(mid, 1e-9)
            return {"valid": True, "upper": float(upper), "lower": float(lower), "mid": float(mid), "duration": duration, "amp": float(amp), "desc": f"近5笔重叠中枢{lower:.2f}-{upper:.2f}"}
    # 兜底：用近期收盘/实体密集区代表供需均衡区。
    close = pd.to_numeric(w["close"], errors="coerce")
    upper = safe_float(close.quantile(0.75)); lower = safe_float(close.quantile(0.25))
    mid = (upper + lower) / 2 if upper > lower else safe_float(close.median())
    amp = (upper - lower) / max(mid, 1e-9) if mid > 0 else 0.0
    valid = upper > lower > 0 and len(w) >= 20
    return {"valid": valid, "upper": float(upper), "lower": float(lower), "mid": float(mid), "duration": int(len(w)), "amp": float(amp), "desc": f"近{len(w)}日价格中枢{lower:.2f}-{upper:.2f}" if valid else "无中枢"}


def _chan_divergence(d):
    if d is None or len(d) < 80:
        return {"score": 0.0, "desc": "背驰样本不足"}
    w = d.tail(120).copy().reset_index(drop=True)
    close = pd.to_numeric(w["close"], errors="coerce")
    volume = pd.to_numeric(w["volume"], errors="coerce")
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9, adjust=False).mean()
    hist = dif - dea
    lows = w["low"].rolling(5, center=True).min()
    highs = w["high"].rolling(5, center=True).max()
    low_idx = [i for i in range(3, len(w)-3) if safe_float(w["low"].iloc[i]) <= safe_float(lows.iloc[i])]
    high_idx = [i for i in range(3, len(w)-3) if safe_float(w["high"].iloc[i]) >= safe_float(highs.iloc[i])]
    score = 0.0; reasons = []
    if len(low_idx) >= 2:
        a, b = low_idx[-2], low_idx[-1]
        if safe_float(w["low"].iloc[b]) < safe_float(w["low"].iloc[a]) and safe_float(hist.iloc[b]) > safe_float(hist.iloc[a]):
            score += 3.0; reasons.append("价格新低但MACD柱不新低")
        if safe_float(w["low"].iloc[b]) < safe_float(w["low"].iloc[a]) and safe_float(volume.iloc[b]) <= safe_float(volume.iloc[a]) * 0.85:
            score += 2.0; reasons.append("价格新低但杀跌量能衰减")
    if len(high_idx) >= 2:
        a, b = high_idx[-2], high_idx[-1]
        if safe_float(w["high"].iloc[b]) > safe_float(w["high"].iloc[a]) and safe_float(hist.iloc[b]) < safe_float(hist.iloc[a]) and safe_float(w["close"].iloc[-1]) < safe_float(w["high"].iloc[b]) * 0.985:
            score -= 3.0; reasons.append("价格新高但动能背离/冲高回落")
        if safe_float(w["high"].iloc[b]) > safe_float(w["high"].iloc[a]) and safe_float(volume.iloc[b]) > safe_float(volume.iloc[a]) * 1.3 and safe_float(w["close"].iloc[-1]) < safe_float(w["high"].iloc[b]) * 0.985:
            score -= 2.0; reasons.append("新高放量效率下降")
    if not reasons:
        reasons.append("未见明确背驰")
    return {"score": float(max(-5.0, min(5.0, score))), "desc": "；".join(reasons)}


def detect_chan_structure_model(df, code=""):
    """缠论量化近似：分型-笔-中枢-背驰-二/三买。该模块只做级别递归与买点分类，不替代原有平台/凹口/压力带。"""
    empty = {
        "chan_score": 0.0,
        "chan_fractal_state": "样本不足",
        "chan_bi_direction": "",
        "chan_bi_strength": 0.0,
        "chan_segment_state": "",
        "chan_pivot_upper": 0.0,
        "chan_pivot_lower": 0.0,
        "chan_pivot_mid": 0.0,
        "chan_pivot_duration": 0,
        "chan_pivot_volume_stability": 0.0,
        "chan_leave_pivot_quality": 0.0,
        "chan_pullback_to_pivot_quality": 0.0,
        "chan_divergence_score": 0.0,
        "chan_divergence_desc": "",
        "chan_buy_point_type": "无明确缠论买点",
        "chan_buy_point_score": 0.0,
        "multi_level_alignment": "",
        "time_maturity_score": 0.0,
        "time_maturity_desc": "",
        "volume_efficiency_score": 0.0,
        "volume_efficiency_desc": "",
        "space_payoff_score": 0.0,
        "space_payoff_desc": "",
    }
    d = _v258_prepare_daily(df, tail=260)
    if len(d) < 80:
        return empty
    bis = _chan_bi_list(d)
    pivot = _chan_recent_pivot(d, bis)
    div = _chan_divergence(d)
    cur = d.iloc[-1]
    close = safe_float(cur["close"]); high = safe_float(cur["high"]); low = safe_float(cur["low"]); openp = safe_float(cur["open"])
    vol = safe_float(cur["volume"]); vol_ma20 = safe_float(d["volume"].rolling(20).mean().iloc[-1])
    rng = max(high - low, 1e-9)
    close_pos = (close - low) / rng
    vr20 = vol / vol_ma20 if vol_ma20 > 0 else 0.0
    last_bi = bis[-1] if bis else {}
    bi_dir = last_bi.get("direction", "")
    bi_strength = abs(safe_float(last_bi.get("return_pct", 0.0))) * 100.0 / max(1.0, safe_float(last_bi.get("duration", 1)))
    pivot_upper = safe_float(pivot.get("upper", 0.0)); pivot_lower = safe_float(pivot.get("lower", 0.0)); pivot_mid = safe_float(pivot.get("mid", 0.0))
    pivot_duration = int(pivot.get("duration", 0) or 0)
    w = d.tail(max(20, min(60, pivot_duration if pivot_duration else 45))).copy()
    vol_cv = safe_float(w["volume"].std() / max(w["volume"].mean(), 1e-9)) if not w.empty else 9.9
    vol_stability = max(0.0, min(5.0, (1.25 - vol_cv) * 4.0))
    # 离开中枢/回抽中枢质量。
    leave_quality = 0.0; pullback_quality = 0.0; buy_type = "无明确缠论买点"; buy_score = 0.0
    if pivot_upper > 0 and close > pivot_upper * 1.005:
        leave_quality += 2.0
        if close_pos >= 0.70:
            leave_quality += 1.5
        if 1.15 <= vr20 <= 3.0:
            leave_quality += 1.5
        if bi_dir == "up":
            leave_quality += 1.0
        buy_type = "中枢离开观察"
        buy_score = max(buy_score, min(5.0, leave_quality))
    recent = d.tail(8)
    if pivot_upper > 0 and len(recent) >= 3:
        touched = bool((recent["low"] <= pivot_upper * 1.018).any())
        held = bool(recent["close"].min() >= pivot_upper * 0.985)
        turn_up = close >= safe_float(recent["close"].iloc[-2]) and close_pos >= 0.60
        if touched and held:
            pullback_quality += 3.0
            if turn_up:
                pullback_quality += 2.0
            if vol_ma20 > 0 and safe_float(recent["volume"].mean()) <= vol_ma20 * 1.20:
                pullback_quality += 1.0
            buy_type = "三买：离开中枢后回抽上沿不破"
            buy_score = max(buy_score, min(7.0, pullback_quality + 1.0))
    if div["score"] > 0 and bi_dir == "up" and close_pos >= 0.60 and pivot_lower > 0 and close >= pivot_lower:
        # 一买风险更高；若随后回调不创新低则升级二买。
        recent_low = safe_float(d.tail(12)["low"].min())
        prior_low = safe_float(d.iloc[-55:-12]["low"].min()) if len(d) >= 67 else recent_low
        if recent_low >= prior_low * 0.995:
            buy_type = "二买：背驰后回调不创新低并转强"
            buy_score = max(buy_score, 6.5 + min(1.5, div["score"] * 0.25))
        else:
            buy_type = "一买观察：下跌背驰后初步转折"
            buy_score = max(buy_score, 3.5 + min(1.5, div["score"] * 0.25))
    # 时间成熟度：中枢/平台持续、量能从乱到稳、回踩窗口。
    time_score = 0.0; time_reasons = []
    if 18 <= pivot_duration <= 65:
        time_score += 4.0; time_reasons.append(f"中枢/平台{pivot_duration}日")
    elif pivot_duration > 65:
        time_score += 3.0; time_reasons.append(f"长周期消化{pivot_duration}日")
    elif 8 <= pivot_duration < 18:
        time_score += 1.5; time_reasons.append(f"中枢时间偏短{pivot_duration}日")
    if vol_stability >= 3.0:
        time_score += 3.0; time_reasons.append("量能稳定/平量增多")
    elif vol_stability >= 1.5:
        time_score += 1.5; time_reasons.append("量能稳定性一般")
    if buy_type.startswith("三买") or buy_type.startswith("二买"):
        time_score += 3.0; time_reasons.append("买点已过承接确认窗口")
    if div["score"] > 0:
        time_score += 1.0; time_reasons.append("背驰衰竭提示")
    time_score = max(0.0, min(15.0, time_score))
    # 量价效率：单位量推进、承接、滞涨反证。
    pct = safe_float(cur.get("pct_chg", 0.0)) if "pct_chg" in d.columns else (close / max(safe_float(d["close"].iloc[-2]), 1e-9) - 1.0) * 100.0
    upper_shadow = (high - max(openp, close)) / rng
    volume_eff = 0.0; vol_reasons = []
    if close > openp and pct >= 2.0 and close_pos >= 0.65 and 1.15 <= vr20 <= 3.0:
        volume_eff += 5.0; vol_reasons.append("健康放量有效推进")
    if pullback_quality >= 3.0:
        volume_eff += 4.0; vol_reasons.append("回抽缩量/不破承接")
    if vol_stability >= 3.0:
        volume_eff += 3.0; vol_reasons.append("平台量能趋稳")
    if vr20 >= 2.5 and (pct < 1.5 or upper_shadow >= 0.35 or close_pos < 0.45):
        volume_eff -= 5.0; vol_reasons.append("放量滞涨/上影供应")
    volume_eff = max(0.0, min(22.0, volume_eff))
    # 空间赔率：到供需上沿/近期压力与中枢防守。
    high120 = safe_float(d["high"].shift(1).rolling(120).max().iloc[-1])
    target_dist = high120 / close - 1.0 if high120 > close > 0 else 0.0
    defense = pivot_lower if pivot_lower > 0 and close > pivot_lower else safe_float(d["low"].tail(10).min())
    defense_dist = close / max(defense, 1e-9) - 1.0 if close > 0 and defense > 0 else 0.0
    rr = target_dist / max(defense_dist, 1e-6) if target_dist > 0 and defense_dist > 0 else 0.0
    space_score = 0.0; space_reasons = []
    if target_dist >= 0.15:
        space_score += 5.0; space_reasons.append(f"上方空间{target_dist:.1%}")
    elif target_dist >= 0.08:
        space_score += 3.0; space_reasons.append(f"上方空间一般{target_dist:.1%}")
    elif 0 < target_dist < 0.06:
        space_score -= 3.0; space_reasons.append("压力贴近")
    if 0 < defense_dist <= 0.06:
        space_score += 4.0; space_reasons.append(f"防守距离{defense_dist:.1%}")
    elif defense_dist > 0.10:
        space_score -= 4.0; space_reasons.append(f"防守偏远{defense_dist:.1%}")
    if rr >= 1.8:
        space_score += 4.0; space_reasons.append(f"RR {rr:.2f}")
    elif 0 < rr < 1.2:
        space_score -= 3.0; space_reasons.append(f"RR不足{rr:.2f}")
    space_score = max(0.0, min(15.0, space_score))
    # 级别联立：日线中枢状态 + 周/月修复代理。
    ma60 = safe_float(d["close"].rolling(60).mean().iloc[-1])
    ma120 = safe_float(d["close"].rolling(120).mean().iloc[-1]) if len(d) >= 120 else ma60
    alignment = []
    if close >= ma60:
        alignment.append("日线站上60均")
    if ma120 > 0 and close >= ma120:
        alignment.append("中周期修复")
    if pivot_upper > 0 and close >= pivot_upper:
        alignment.append("中枢上沿之上")
    total = min(30.0, buy_score + time_score * 0.35 + volume_eff * 0.25 + space_score * 0.20 + max(0.0, div["score"]) * 0.6)
    return {
        "chan_score": float(total),
        "chan_fractal_state": f"分型{len(_chan_fractals(d))}个/笔{len(bis)}段",
        "chan_bi_direction": str(bi_dir),
        "chan_bi_strength": float(bi_strength),
        "chan_segment_state": "上涨笔延续" if bi_dir == "up" else ("下跌笔反抽/修复" if bi_dir == "down" else "未成笔"),
        "chan_pivot_upper": float(pivot_upper),
        "chan_pivot_lower": float(pivot_lower),
        "chan_pivot_mid": float(pivot_mid),
        "chan_pivot_duration": int(pivot_duration),
        "chan_pivot_volume_stability": float(vol_stability),
        "chan_leave_pivot_quality": float(leave_quality),
        "chan_pullback_to_pivot_quality": float(pullback_quality),
        "chan_divergence_score": float(div["score"]),
        "chan_divergence_desc": str(div["desc"]),
        "chan_buy_point_type": str(buy_type),
        "chan_buy_point_score": float(buy_score),
        "multi_level_alignment": "；".join(alignment) if alignment else "级别联立一般",
        "time_maturity_score": float(time_score),
        "time_maturity_desc": "；".join(time_reasons) if time_reasons else "时间成熟度不足",
        "volume_efficiency_score": float(volume_eff),
        "volume_efficiency_desc": "；".join(vol_reasons) if vol_reasons else "量价效率一般",
        "space_payoff_score": float(space_score),
        "space_payoff_desc": "；".join(space_reasons) if space_reasons else "空间赔率一般",
    }
# ======================= V25.8 去单线主轴 + 缠论级别递归 + 量价时空机构评分 END =======================


# ========================= V20.2 底部反转强势形态模块 =========================
def _v202_local_lows(w, order=3):
    """返回局部低点索引，避免依赖 scipy。"""
    lows = []
    try:
        arr = pd.to_numeric(w["low"], errors="coerce").values
        n = len(arr)
        for i in range(order, n - order):
            if not np.isfinite(arr[i]):
                continue
            if arr[i] <= np.nanmin(arr[i-order:i]) and arr[i] <= np.nanmin(arr[i+1:i+order+1]):
                lows.append(i)
    except Exception:
        return []
    return lows


def _v202_local_high_between(w, a, b):
    try:
        seg = w.iloc[min(a, b):max(a, b)+1]
        if seg.empty:
            return 0.0, ""
        idx = int(pd.to_numeric(seg["high"], errors="coerce").idxmax())
        return safe_float(w.loc[idx, "high"]), str(w.loc[idx, "date"] if "date" in w.columns else "")
    except Exception:
        return 0.0, ""


def detect_v202_bottom_reversal_patterns(hist):
    """
    V20.2：底部反转强势形态统一识别。
    覆盖：头肩底、W底/双底、V底/尖底反转。
    形态本身给结构分，颈线/确认线突破给触发分，量能配合给确认分；最终仍走结构同源封顶，避免重复堆分。
    """
    empty = {
        "hit": False, "type": "", "score": 0.0, "neckline": 0.0, "confirmed": False,
        "volume_quality": 0.0, "trigger_quality": 0.0, "retest_quality": 0.0,
        "desc": "无明确头肩底/W底/V底结构",
    }
    if hist is None or len(hist) < 70:
        return empty
    w = hist.tail(180).copy().reset_index(drop=True)
    for c in ["open", "high", "low", "close", "volume"]:
        if c not in w.columns:
            return empty
        w[c] = pd.to_numeric(w[c], errors="coerce")
    w = w.dropna(subset=["open", "high", "low", "close", "volume"]).reset_index(drop=True)
    if len(w) < 70:
        return empty
    cur = w.iloc[-1]
    cur_close = safe_float(cur["close"]); cur_high = safe_float(cur["high"]); cur_low = safe_float(cur["low"])
    cur_vol = safe_float(cur["volume"]); vol_ma20 = safe_float(w["volume"].tail(20).mean())
    pos = (cur_close - cur_low) / max(cur_high - cur_low, 1e-9)
    long_pos = safe_float(cur.get("long_pos_250", 0.0)) if "long_pos_250" in w.columns else 0.0
    candidates = []
    lows = [i for i in _v202_local_lows(w, order=3) if 8 <= i <= len(w) - 4]

    # W底 / 双底：第二底不有效破第一底，颈线突破或接近颈线；第二底缩量/突破放量加分。
    for a_i, i1 in enumerate(lows[:-1]):
        for i2 in lows[a_i+1:]:
            gap = i2 - i1
            if gap < 10 or gap > 90:
                continue
            low1 = safe_float(w.loc[i1, "low"]); low2 = safe_float(w.loc[i2, "low"])
            if low1 <= 0 or low2 <= 0 or abs(low2 / low1 - 1) > 0.10 or low2 < low1 * 0.94:
                continue
            neckline, _ = _v202_local_high_between(w, i1, i2)
            if neckline <= max(low1, low2) * 1.05:
                continue
            confirmed = cur_close >= neckline * 1.005
            near_neck = cur_close >= neckline * 0.97
            if not (confirmed or near_neck):
                continue
            vol1 = safe_float(w.loc[i1, "volume"]); vol2 = safe_float(w.loc[i2, "volume"])
            second_bottom_shrink = vol2 <= vol1 * 1.10 if vol1 > 0 else False
            breakout_vol = cur_vol >= vol_ma20 * 1.25 if vol_ma20 > 0 else False
            score = 2.5
            desc_bits = [f"W底/双底：第一底{low1:.2f}，第二底{low2:.2f}，颈线{neckline:.2f}"]
            if second_bottom_shrink: score += 1.5; desc_bits.append("第二底缩量承接")
            if near_neck: score += 1.0; desc_bits.append("已接近颈线")
            if confirmed: score += 3.0; desc_bits.append("实体/收盘突破颈线")
            if breakout_vol and confirmed: score += 1.5; desc_bits.append("突破量能健康放大")
            if pos >= 0.70 and confirmed: score += 0.8; desc_bits.append("收盘位置较强")
            if long_pos > 0.75: score -= 2.0; desc_bits.append("位置偏高降权")
            score = max(0.0, min(10.0, score))
            if score >= 4.0:
                candidates.append({"hit": True, "type": "W底/双底", "score": score, "neckline": neckline, "confirmed": bool(confirmed), "volume_quality": 1.5 if second_bottom_shrink else 0.0, "trigger_quality": 3.0 if confirmed else 1.0, "retest_quality": 0.0, "desc": "；".join(desc_bits)})

    # 头肩底：三低点，中间头部最低，右肩不破头；颈线突破或接近。
    for a in range(len(lows) - 2):
        i1, i2, i3 = lows[a], lows[a+1], lows[a+2]
        if i2 - i1 < 8 or i3 - i2 < 8 or i3 - i1 > 120:
            continue
        l1, l2, l3 = safe_float(w.loc[i1, "low"]), safe_float(w.loc[i2, "low"]), safe_float(w.loc[i3, "low"])
        if min(l1, l2, l3) <= 0 or not (l2 < l1 * 0.97 and l2 < l3 * 0.97):
            continue
        shoulder_sym = abs(l3 / l1 - 1)
        if shoulder_sym > 0.18 or l3 < l2 * 1.03:
            continue
        neck1, _ = _v202_local_high_between(w, i1, i2); neck2, _ = _v202_local_high_between(w, i2, i3)
        neckline = max(neck1, neck2)
        if neckline <= max(l1, l3) * 1.05:
            continue
        confirmed = cur_close >= neckline * 1.005
        near_neck = cur_close >= neckline * 0.97
        if not (confirmed or near_neck):
            continue
        right_vol = safe_float(w.loc[i3, "volume"]); head_vol = safe_float(w.loc[i2, "volume"])
        right_shrink = right_vol <= head_vol * 0.95 if head_vol > 0 else False
        breakout_vol = cur_vol >= vol_ma20 * 1.25 if vol_ma20 > 0 else False
        score = 3.0
        desc_bits = [f"头肩底：左肩{l1:.2f}，头部{l2:.2f}，右肩{l3:.2f}，颈线{neckline:.2f}"]
        if right_shrink: score += 1.2; desc_bits.append("右肩缩量")
        if shoulder_sym <= 0.10: score += 0.8; desc_bits.append("左右肩较对称")
        if near_neck: score += 1.0; desc_bits.append("已接近颈线")
        if confirmed: score += 3.0; desc_bits.append("突破颈线确认")
        if confirmed and breakout_vol: score += 1.5; desc_bits.append("突破量能配合")
        if pos >= 0.70 and confirmed: score += 0.8; desc_bits.append("收盘较强")
        if long_pos > 0.75: score -= 2.0; desc_bits.append("位置偏高降权")
        score = max(0.0, min(11.0, score))
        if score >= 4.0:
            candidates.append({"hit": True, "type": "头肩底", "score": score, "neckline": neckline, "confirmed": bool(confirmed), "volume_quality": 1.2 if right_shrink else 0.0, "trigger_quality": 3.0 if confirmed else 1.0, "retest_quality": 0.0, "desc": "；".join(desc_bits)})

    # V底 / 尖底：只给中等结构分；必须快速收回修复线，未回踩确认不重奖。
    recent = w.tail(100).copy().reset_index(drop=True)
    if len(recent) >= 55:
        low_idx = int(pd.to_numeric(recent["low"], errors="coerce").idxmin())
        if 12 <= low_idx <= len(recent) - 8:
            low_price = safe_float(recent.loc[low_idx, "low"])
            left_seg = recent.iloc[max(0, low_idx-25):low_idx]
            left_high = safe_float(left_seg["high"].max()) if not left_seg.empty else 0.0
            repair_line = safe_float(left_seg["high"].quantile(0.65)) if not left_seg.empty else 0.0
            if low_price > 0 and left_high > 0 and repair_line > 0:
                drop = 1 - low_price / left_high; rebound = cur_close / low_price - 1
                reclaimed = cur_close >= repair_line * 1.003
                vol_expand = cur_vol >= vol_ma20 * 1.20 if vol_ma20 > 0 else False
                if drop >= 0.15 and rebound >= 0.10 and reclaimed:
                    score = 2.5
                    desc_bits = [f"V底/尖底修复：低点{low_price:.2f}，修复线{repair_line:.2f}，跌幅{drop:.1%}后反弹{rebound:.1%}"]
                    if vol_expand: score += 1.5; desc_bits.append("右侧量能接力")
                    if pos >= 0.70: score += 0.8; desc_bits.append("收盘较强")
                    if cur_close >= left_high * 0.98: score += 1.5; desc_bits.append("接近/收复急跌前平台")
                    if long_pos > 0.70: score -= 1.5; desc_bits.append("中高位V修复降权")
                    score = max(0.0, min(8.0, score))
                    if score >= 4.0:
                        candidates.append({"hit": True, "type": "V底/尖底反转", "score": score, "neckline": repair_line, "confirmed": bool(reclaimed), "volume_quality": 1.5 if vol_expand else 0.0, "trigger_quality": 2.0 if reclaimed else 0.0, "retest_quality": 0.0, "desc": "；".join(desc_bits) + "；V底未回踩确认前只做中等结构分"})

    if not candidates:
        return empty
    best = sorted(candidates, key=lambda x: safe_float(x.get("score", 0)), reverse=True)[0]
    best["score"] = float(max(0.0, min(12.0, safe_float(best.get("score", 0)))))
    return best
# ======================= V20.2 底部反转强势形态模块 END ======================

def calc_deep_rows(df, code):
    if df is None or len(df) < 260:
        return pd.DataFrame()

    df = calc_base_full(df)

    # V12.4：深度层最终只输出最近CHECK_DAYS，所有“逐K回看型”昂贵逻辑只对尾部有效候选K线计算。
    # 向量化基础指标仍完整保留；减少的是无效历史行重复跑凹口/平台/黄金倍量/台阶等候选判断。
    _active_deep_indices = set(range(max(0, len(df) - max(1, CHECK_DAYS)), len(df)))

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

    extra["up_vol_avg_20"] = extra["up_vol"].rolling(20).sum() / extra["up_days_20"].replace(0, np.nan)
    extra["down_vol_avg_20"] = extra["down_vol"].rolling(20).sum() / extra["down_days_20"].replace(0, np.nan)
    extra["up_down_vol_ratio_20"] = extra["up_vol_avg_20"] / extra["down_vol_avg_20"].replace(0, np.nan)

    # 20/40/60日阳阴量价结构：真实参与后台评分，而不是只在报告中展示。
    for _w in [40, 60]:
        extra[f"up_days_{_w}"] = extra["is_up"].rolling(_w).sum()
        extra[f"down_days_{_w}"] = extra["is_down"].rolling(_w).sum()
        extra[f"up_vol_avg_{_w}"] = extra["up_vol"].rolling(_w).sum() / extra[f"up_days_{_w}"].replace(0, np.nan)
        extra[f"down_vol_avg_{_w}"] = extra["down_vol"].rolling(_w).sum() / extra[f"down_days_{_w}"].replace(0, np.nan)
        extra[f"up_down_vol_ratio_{_w}"] = extra[f"up_vol_avg_{_w}"] / extra[f"down_vol_avg_{_w}"].replace(0, np.nan)

    extra["up_ratio_20"] = extra["up_days_20"] / 20
    extra["up_ratio_40"] = extra["up_days_40"] / 40
    extra["up_ratio_60"] = extra["up_days_60"] / 60

    up_body_pct = ((df["close"] - df["open"]) / df["open"].replace(0, np.nan)).where(extra["is_up"], 0)
    down_body_pct = ((df["open"] - df["close"]) / df["open"].replace(0, np.nan)).where(extra["is_down"], 0)
    extra["up_body_avg_60"] = up_body_pct.rolling(60).sum() / extra["up_days_60"].replace(0, np.nan)
    extra["down_body_avg_60"] = down_body_pct.rolling(60).sum() / extra["down_days_60"].replace(0, np.nan)

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
    extra["beibeiliang_shrink_rate_after"] = 1 - (df["volume"] / extra["beibeiliang_peak_volume"].shift(2).replace(0, np.nan))
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

    extra["score_volume_structure"] = 0.0
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
        if _i not in _active_deep_indices:
            limit_hold_scores.append(0.0)
            limit_hold_levels.append("非当前候选K线，跳过昂贵承接回看")
            limit_hold_ref_dates.append("")
            continue
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

    # V14阳包阴精细评分：不仅判断是否反包，还分档评估开盘位置、实体覆盖、上下影线、收盘质量和量能质量。
    prev_open = df["open"].shift(1)
    prev_close = df["close"].shift(1)
    prev_high = df["high"].shift(1)
    prev_low = df["low"].shift(1)
    prev_body_top = pd.concat([prev_open, prev_close], axis=1).max(axis=1)
    prev_body_bottom = pd.concat([prev_open, prev_close], axis=1).min(axis=1)
    prev_body_mid = (prev_body_top + prev_body_bottom) / 2

    today_body_top = df[["open", "close"]].max(axis=1)
    today_body_bottom = df[["open", "close"]].min(axis=1)
    today_range = (df["high"] - df["low"]).replace(0, np.nan)
    prev_range = (prev_high - prev_low).replace(0, np.nan)
    today_upper_shadow = (df["high"] - today_body_top).clip(lower=0)
    today_lower_shadow = (today_body_bottom - df["low"]).clip(lower=0)
    prev_upper_shadow = (prev_high - prev_body_top).clip(lower=0)
    prev_lower_shadow = (prev_body_bottom - prev_low).clip(lower=0)
    extra["v14_today_upper_shadow_ratio"] = (today_upper_shadow / today_range).fillna(0)
    extra["v14_today_lower_shadow_ratio"] = (today_lower_shadow / today_range).fillna(0)
    extra["v14_prev_upper_shadow_ratio"] = (prev_upper_shadow / prev_range).fillna(0)
    extra["v14_prev_lower_shadow_ratio"] = (prev_lower_shadow / prev_range).fillna(0)

    prev_bear = prev_close < prev_open
    today_bull = df["close"] > df["open"]
    # 宽口径：至少修复前阴实体中位才纳入阳包阴候选；强弱由grade分层。
    extra["bull_engulf"] = today_bull & prev_bear & (df["close"] > prev_body_mid)
    extra["engulf_vol_ratio"] = df["volume"] / df["volume"].shift(1).replace(0, np.nan)

    extra["v14_bull_engulf_grade"] = 0
    # 4档最强：跳空/高开直接越过前阴开盘价（前阴实体实顶），开盘即站到空头成本上方。
    extra.loc[extra["bull_engulf"] & (df["open"] > prev_open) & (df["close"] > prev_open), "v14_bull_engulf_grade"] = 4
    # 3档：开在前阴实体内，收盘站上前阴开盘价。
    extra.loc[
        extra["bull_engulf"]
        & (extra["v14_bull_engulf_grade"] == 0)
        & (df["open"] > prev_close)
        & (df["open"] <= prev_open)
        & (df["close"] > prev_open),
        "v14_bull_engulf_grade"
    ] = 3
    # 2档：低/平开后收盘站上前阴开盘价，完成实体反包，但开盘主动性弱于前两档。
    extra.loc[
        extra["bull_engulf"]
        & (extra["v14_bull_engulf_grade"] == 0)
        & (df["close"] > prev_open),
        "v14_bull_engulf_grade"
    ] = 2
    # 1档：只修复前阴实体中位，尚未站上前阴开盘价。
    extra.loc[
        extra["bull_engulf"]
        & (extra["v14_bull_engulf_grade"] == 0)
        & (df["close"] > prev_body_mid),
        "v14_bull_engulf_grade"
    ] = 1

    extra["v14_bull_engulf_pattern_score"] = 0.0
    extra.loc[extra["v14_bull_engulf_grade"] == 4, "v14_bull_engulf_pattern_score"] = 10.0
    extra.loc[extra["v14_bull_engulf_grade"] == 3, "v14_bull_engulf_pattern_score"] = 8.0
    extra.loc[extra["v14_bull_engulf_grade"] == 2, "v14_bull_engulf_pattern_score"] = 7.0
    extra.loc[extra["v14_bull_engulf_grade"] == 1, "v14_bull_engulf_pattern_score"] = 4.0

    # 影线质量：阳线上影越短、收盘越靠近最高越强；长上影/收不住要降级。前阴长下影+次日强反包可轻微加分。
    extra["v14_bull_engulf_shadow_score"] = 0.0
    extra.loc[extra["bull_engulf"] & (extra["v14_today_upper_shadow_ratio"] <= 0.15) & (df["pos"] >= 0.85), "v14_bull_engulf_shadow_score"] += 4.0
    extra.loc[extra["bull_engulf"] & (extra["v14_today_upper_shadow_ratio"].between(0.15, 0.25, inclusive="right")) & (df["pos"] >= 0.70), "v14_bull_engulf_shadow_score"] += 2.0
    extra.loc[extra["bull_engulf"] & (extra["v14_today_lower_shadow_ratio"] >= 0.15) & (df["pos"] >= 0.70), "v14_bull_engulf_shadow_score"] += 1.0
    extra.loc[extra["bull_engulf"] & (extra["v14_prev_lower_shadow_ratio"] >= 0.25) & (extra["v14_bull_engulf_grade"] >= 2), "v14_bull_engulf_shadow_score"] += 1.0
    extra.loc[extra["bull_engulf"] & (extra["v14_today_upper_shadow_ratio"] > 0.35), "v14_bull_engulf_shadow_score"] -= 2.0
    extra.loc[extra["bull_engulf"] & (extra["v14_today_upper_shadow_ratio"] > 0.50), "v14_bull_engulf_shadow_score"] -= 2.0
    extra.loc[extra["bull_engulf"] & (extra["v14_prev_upper_shadow_ratio"] > 0.35) & (df["close"] < prev_high), "v14_bull_engulf_shadow_score"] -= 1.0
    extra.loc[extra["bull_engulf"] & (df["pos"] < 0.60), "v14_bull_engulf_shadow_score"] -= 2.0
    extra["v14_bull_engulf_shadow_score"] = extra["v14_bull_engulf_shadow_score"].clip(-4, 4)

    # 量能质量：阳量等于或略大于前阴量、1.5倍以内最佳；过度爆量不重奖，高位爆量还要谨慎。
    extra["v14_bull_engulf_volume_score"] = 0.0
    extra.loc[extra["bull_engulf"] & extra["engulf_vol_ratio"].between(1.0, 1.5, inclusive="both"), "v14_bull_engulf_volume_score"] = 3.0
    extra.loc[extra["bull_engulf"] & extra["engulf_vol_ratio"].between(0.8, 1.0, inclusive="left"), "v14_bull_engulf_volume_score"] = 1.0
    extra.loc[extra["bull_engulf"] & extra["engulf_vol_ratio"].between(1.5, 2.0, inclusive="right"), "v14_bull_engulf_volume_score"] = 1.5
    extra.loc[extra["bull_engulf"] & (extra["engulf_vol_ratio"] > 2.0) & (df["long_pos_250"] <= 0.45), "v14_bull_engulf_volume_score"] = 0.8
    extra.loc[extra["bull_engulf"] & (extra["engulf_vol_ratio"] > 2.0) & (df["long_pos_250"] > 0.65), "v14_bull_engulf_volume_score"] = -1.0

    # 位置轻加权：只作为阳包阴细项，不替代平台/凹口/倍量结构主分，防止重复堆分。
    extra["v14_bull_engulf_context_score"] = 0.0
    extra.loc[extra["bull_engulf"] & (df["long_pos_250"] <= 0.45), "v14_bull_engulf_context_score"] += 1.0
    extra.loc[extra["bull_engulf"] & (df["break_rate"] > 0) & (df["break_rate"] <= 0.08), "v14_bull_engulf_context_score"] += 1.0
    extra.loc[extra["bull_engulf"] & ((df["bias20"] > 0.12) | (df["long_pos_250"] > 0.80)), "v14_bull_engulf_context_score"] -= 2.0

    extra["v14_bull_engulf_score_current"] = (
        extra["v14_bull_engulf_pattern_score"]
        + extra["v14_bull_engulf_shadow_score"]
        + extra["v14_bull_engulf_volume_score"]
        + extra["v14_bull_engulf_context_score"]
    ).clip(0, 18)

    extra["v14_bull_engulf_desc"] = "无阳包阴"
    extra.loc[extra["v14_bull_engulf_grade"] == 1, "v14_bull_engulf_desc"] = "阳包阴弱修复：仅修复前阴实体中位，未完全站上前阴开盘价"
    extra.loc[extra["v14_bull_engulf_grade"] == 2, "v14_bull_engulf_desc"] = "阳包阴有效反包：低/平开后收盘站上前阴开盘价"
    extra.loc[extra["v14_bull_engulf_grade"] == 3, "v14_bull_engulf_desc"] = "阳包阴强反包：开在前阴实体内，收盘站上前阴开盘价"
    extra.loc[extra["v14_bull_engulf_grade"] == 4, "v14_bull_engulf_desc"] = "阳包阴最强档：跳空/高开越过前阴开盘价后继续上攻"
    extra.loc[extra["bull_engulf"] & (df["close"] > prev_high), "v14_bull_engulf_desc"] = extra.loc[extra["bull_engulf"] & (df["close"] > prev_high), "v14_bull_engulf_desc"] + "；收盘进一步站上前阴最高价"
    extra.loc[extra["bull_engulf"] & (extra["v14_today_upper_shadow_ratio"] > 0.35), "v14_bull_engulf_desc"] = extra.loc[extra["bull_engulf"] & (extra["v14_today_upper_shadow_ratio"] > 0.35), "v14_bull_engulf_desc"] + "；但阳线上影偏长，需降级观察"

    # 兼容原模型的阳包阴频次分：用V14精细分压缩到原score_engulf_quality，保留原有滚动统计，不另起炉灶。
    extra["score_engulf_quality"] = (extra["v14_bull_engulf_score_current"] / 3.0).clip(0, 6)
    extra.loc[~extra["bull_engulf"], "score_engulf_quality"] = 0.0

    extra["bull_engulf_score_20"] = extra["score_engulf_quality"].rolling(20).sum().clip(0, 6)
    extra["bull_engulf_count_20"] = extra["bull_engulf"].rolling(20).sum()
    # 50日高质量阳包阴：数量、量能、影线、收盘位置一起考虑，不能只数次数。
    extra["bull_engulf_quality"] = (
        extra["bull_engulf"]
        & (extra["v14_bull_engulf_score_current"] >= 9)
        & (extra["engulf_vol_ratio"] >= 0.9)
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
    middle_body_pct = ((df["open"].shift(1) - df["close"].shift(1)) / df["close"].shift(2).replace(0, np.nan) * 100).fillna(0)

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

    extra["score_behavior"] = 0.0
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

    extra["score_pattern"] = 0.0
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
    # V20.2：底部反转强势形态补全（头肩底/W底/V底）。保留细项识别，最终同源封顶。
    extra["score_bottom_reversal_pattern"] = 0.0
    extra["bottom_pattern_type"] = ""
    extra["bottom_pattern_neckline"] = 0.0
    extra["bottom_pattern_confirmed"] = False
    extra["bottom_pattern_volume_quality"] = 0.0
    extra["bottom_pattern_trigger_quality"] = 0.0
    extra["bottom_pattern_retest_quality"] = 0.0
    extra["bottom_pattern_desc"] = ""

    for idx in range(len(df)):
        if idx not in _active_deep_indices:
            continue
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
    body_range = (df["high"] - df["low"]).replace(0, np.nan)
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
        if _i not in _active_deep_indices:
            key_pullback_scores.append(0.0)
            key_pullback_descs.append("非当前候选K线，跳过关键位回踩精算")
            continue
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

    # ========================= V12：突破后舒服买点确认模型 =========================
    # 用户偏好：不在突破当天正式推送；无论弱突破/强突破，先后台跟踪，等回踩中轨/均线/强阳实体位/关键位有承接时再推送。
    extra["daily_bbi"] = (
        df["close"].rolling(3).mean()
        + df["close"].rolling(6).mean()
        + df["close"].rolling(12).mean()
        + df["close"].rolling(24).mean()
    ) / 4
    extra["daily_bbiboll_mid"] = extra["daily_bbi"].where(extra["daily_bbi"].notna(), df.get("ma20", pd.Series(0, index=df.index)))
    extra["daily_boll_mid"] = df.get("ma20", pd.Series(0, index=df.index))
    extra["v12_break_today_weak"] = (
        (df["close"] >= df["prehigh"] * 1.003)
        | ((extra["structure_neckline"] > 0) & (df["close"] >= extra["structure_neckline"] * 1.003))
        | extra["platform_break_vol"]
    )
    extra["v12_break_today_strong"] = (
        extra["v12_break_today_weak"]
        & (df["close"] > df["open"])
        & ((df["pct_chg"] >= 3.0) | (df["entity_pct"] >= 3.0))
        & (df["pos"] >= 0.70)
        & ((extra["is_beiliang"]) | (df["volr"] >= 1.5))
        & (df["long_pos_250"] <= 0.65)
    )
    extra["v12_recent_break_event"] = _v12_bool_recent_event(
        extra["v12_break_today_weak"] | extra["v12_break_today_strong"] | (extra["score_structure_core"] > 0) | (extra["score_fibo_reclaim"] >= 6) | (extra["score_advanced_ao_kou"] >= 7),
        lookback=10
    )
    extra["v12_prior_break_days"] = _v12_count_recent_event(
        extra["v12_break_today_weak"] | extra["v12_break_today_strong"] | (extra["score_structure_core"] > 0),
        lookback=10
    )

    prior_attack_event = (
        extra["v12_break_today_weak"]
        | extra["v12_break_today_strong"]
        | extra["strong_yang"]
        | extra["limit_up"]
        | (extra["score_structure_core"] > 0)
    )
    prior_body_top = pd.concat([df["open"], df["close"]], axis=1).max(axis=1)
    prior_body_bottom = pd.concat([df["open"], df["close"]], axis=1).min(axis=1)
    prior_body_mid = (prior_body_top + prior_body_bottom) / 2
    extra["v12_prior_attack_mid"] = _v12_latest_prior_event_value(df, prior_attack_event, prior_body_mid, lookback=10, default=0.0)
    extra["v12_prior_attack_bottom"] = _v12_latest_prior_event_value(df, prior_attack_event, prior_body_bottom, lookback=10, default=0.0)
    extra["v12_prior_attack_volume"] = _v12_latest_prior_event_value(df, prior_attack_event, df["volume"], lookback=10, default=0.0)

    low = df["low"]
    close = df["close"]
    openp = df["open"]
    volume = df["volume"]
    bbi_mid = extra["daily_bbiboll_mid"]
    ma5 = df.get("ma5", pd.Series(0, index=df.index))
    ma10 = df.get("ma10", pd.Series(0, index=df.index))
    struct_key = extra["structure_neckline"].where(extra["structure_neckline"] > 0, df["prehigh"])

    # ========================= V12：统一多周期关键结构位系统 =========================
    # 将日/周/月/季的凹口、箱体、大量阳K实底、高点突破统一成关键结构位，
    # 避免同类逻辑重复扫描、重复打分。正式推送仍必须叠加日线舒服买点。
    multi_tf_ctx = detect_multi_timeframe_key_structure(df, code)
    for _k, _v in multi_tf_ctx.items():
        extra[_k] = _v
    multi_tf_floor = pd.Series(float(multi_tf_ctx.get("multi_tf_best_floor", 0.0)), index=df.index)
    multi_tf_high = pd.Series(float(multi_tf_ctx.get("multi_tf_best_high", 0.0)), index=df.index)

    # ========================= V15：选股模型多周期供需压力带突破模型 =========================
    # 先用百分比/对数价格桶生成日/周/月/季/年供需密集区，再找多周期重叠供需压力带和最终压力上沿；
    # 只有A/S级压力带突破才作为选股模型正式候选资格之一，B/C/D只进入观察。
    xhu_pressure_ctx = detect_xuanhu_pressure_band_breakout_model(df, code)
    for _k, _v in xhu_pressure_ctx.items():
        extra[_k] = _v

    # ========================= V25.8：缠论级别递归 + 量价时空上下文 =========================
    chan_ctx = detect_chan_structure_model(df, code)
    for _k, _v in chan_ctx.items():
        extra[_k] = _v
    # 若多周期关键实底线存在，将它纳入“回踩关键位”体系，但只作为同源关键位之一，后面有封顶。
    struct_key = pd.concat([struct_key, multi_tf_floor.where(multi_tf_floor > 0, 0)], axis=1).max(axis=1)

    # ========================= V12.4：远期绿线+9号试盘高点+时间倍数+日线二次确认 =========================
    # 该模型只在深度候选/种子票上运行，避免全市场重复重扫100月K。
    v124_probe_ctx = detect_v124_probe_high_second_confirm_model(df)
    for _k, _v in v124_probe_ctx.items():
        extra[_k] = _v

    # ========================= V12.5/12.6：爆发前夜时间窗口 + 台阶平台均量抬升 + 多周期重心平量/1000日窗口 =========================
    # 只在深度候选/种子票运行，避免全市场基础轻扫重复计算。
    v125_timing_ctx = detect_v125_timing_window_model(df, v124_probe_ctx)
    for _k, _v in v125_timing_ctx.items():
        extra[_k] = _v
    v125_step_ctx = detect_v125_step_platform_volume_lift_model(df)
    for _k, _v in v125_step_ctx.items():
        extra[_k] = _v

    # ========================= V12.6：多周期重心平量 + 1000日窄口 + 底部衰竭修复充分率 =========================
    # 该套件只在深度候选/种子票运行。它是时间窗口/充分率加分项，不单独构成买点。
    v126_ctx = detect_v126_system_timing_suite(df, v124_probe_ctx)
    for _k, _v in v126_ctx.items():
        extra[_k] = _v

    extra["v12_pullback_to_bbiboll"] = (bbi_mid > 0) & (low <= bbi_mid * 1.018) & (close >= bbi_mid * 0.992)
    extra["v12_pullback_to_ma5_ma10"] = (
        ((ma5 > 0) & (low <= ma5 * 1.012) & (close >= ma5 * 0.992))
        | ((ma10 > 0) & (low <= ma10 * 1.015) & (close >= ma10 * 0.992))
    )
    extra["v12_pullback_to_prior_mid"] = (
        (extra["v12_prior_attack_mid"] > 0)
        & (low <= extra["v12_prior_attack_mid"] * 1.018)
        & (close >= extra["v12_prior_attack_mid"] * 0.992)
    )
    extra["v12_pullback_to_prior_bottom"] = (
        (extra["v12_prior_attack_bottom"] > 0)
        & (low <= extra["v12_prior_attack_bottom"] * 1.018)
        & (close >= extra["v12_prior_attack_bottom"] * 0.990)
    )
    extra["v12_pullback_to_structure_key"] = (
        (struct_key > 0)
        & (low <= struct_key * 1.025)
        & (close >= struct_key * 0.990)
    )
    extra["v12_pullback_shrink_volume"] = (
        (extra["v12_prior_attack_volume"] > 0)
        & (volume <= extra["v12_prior_attack_volume"] * 0.82)
    )
    extra["v12_pullback_small_body"] = (
        ((df["high"] - df["low"]) > 0)
        & ((df["close"] - df["open"]).abs() / (df["high"] - df["low"]).replace(0, np.nan) <= 0.55)
    ).fillna(False)
    extra["v12_pullback_turning"] = (close >= openp) | (df["pos"] >= 0.58) | (close >= close.shift(1) * 0.995)

    extra["score_v12_pullback_entry"] = 0.0
    extra.loc[extra["v12_recent_break_event"] & extra["v12_pullback_to_bbiboll"], "score_v12_pullback_entry"] += 5.0
    extra.loc[extra["v12_recent_break_event"] & extra["v12_pullback_to_ma5_ma10"], "score_v12_pullback_entry"] += 3.0
    extra.loc[extra["v12_recent_break_event"] & extra["v12_pullback_to_prior_mid"], "score_v12_pullback_entry"] += 3.0
    extra.loc[extra["v12_recent_break_event"] & extra["v12_pullback_to_prior_bottom"], "score_v12_pullback_entry"] += 2.0
    extra.loc[extra["v12_recent_break_event"] & extra["v12_pullback_to_structure_key"], "score_v12_pullback_entry"] += 2.0
    extra.loc[extra["v12_recent_break_event"] & (multi_tf_floor > 0) & (low <= multi_tf_floor * 1.025) & (close >= multi_tf_floor * 0.990), "score_v12_pullback_entry"] += 2.5
    extra.loc[extra["v12_recent_break_event"] & extra["v12_pullback_shrink_volume"], "score_v12_pullback_entry"] += 2.0
    extra.loc[extra["v12_recent_break_event"] & extra["v12_pullback_small_body"], "score_v12_pullback_entry"] += 1.0
    extra.loc[extra["v12_recent_break_event"] & extra["v12_pullback_turning"], "score_v12_pullback_entry"] += 1.5
    # 回踩有效但放量长阴/明显破中轨，不给高交易优先级。
    extra.loc[extra["v12_recent_break_event"] & (close < bbi_mid * 0.985) & (volume > df["vol_ma"] * 1.4), "score_v12_pullback_entry"] -= 5.0
    extra.loc[extra["v12_break_today_weak"] & (~extra["v12_recent_break_event"]), "score_v12_pullback_entry"] -= 3.0
    extra["score_v12_pullback_entry"] = extra["score_v12_pullback_entry"].clip(-6, 16)

    extra["v12_entry_label"] = "未到舒服买点"
    extra.loc[extra["v12_break_today_weak"], "v12_entry_label"] = "突破当天/刚摸关键位，后台跟踪，不正式推送"
    extra.loc[extra["score_v12_pullback_entry"] >= 8, "v12_entry_label"] = "突破后回踩确认，符合舒服买点"
    extra.loc[(extra["score_v12_pullback_entry"] >= 5) & (extra["score_v12_pullback_entry"] < 8), "v12_entry_label"] = "回踩承接初现，还需确认"
    extra["v12_entry_desc"] = ""
    extra.loc[extra["v12_pullback_to_bbiboll"], "v12_entry_desc"] += "回踩BBIBOLL/BBI中轨；"
    extra.loc[extra["v12_pullback_to_ma5_ma10"], "v12_entry_desc"] += "回踩MA5/MA10；"
    extra.loc[extra["v12_pullback_to_prior_mid"], "v12_entry_desc"] += "守住前突破阳线实体中部；"
    extra.loc[extra["v12_pullback_to_prior_bottom"], "v12_entry_desc"] += "守住前突破阳线实体底部；"
    extra.loc[extra["v12_pullback_to_structure_key"], "v12_entry_desc"] += "回踩结构关键位；"
    extra.loc[(multi_tf_floor > 0) & (low <= multi_tf_floor * 1.025) & (close >= multi_tf_floor * 0.990), "v12_entry_desc"] += "回踩多周期关键实底线；"
    extra.loc[extra["v12_pullback_shrink_volume"], "v12_entry_desc"] += "回踩缩量；"
    extra["v12_formal_push_ok"] = extra["score_v12_pullback_entry"] >= 8
    # 多周期关键实底线已形成成熟回踩段时，日线回踩确认分略低也可进入正式候选，但必须有日线承接。
    extra.loc[(extra["score_multi_tf_key_structure"] >= 10) & (extra["multi_tf_pullback_count"] >= 3) & (extra["score_v12_pullback_entry"] >= 6), "v12_formal_push_ok"] = True
    # 9号高质量试盘高点经过时间消化后，日线漂亮突破9号线，可以作为大涨前的二次确认候选；父级大凹口贴脸则不放行。
    extra.loc[(extra["score_v124_probe_second_confirm"] >= 10) & (extra["v124_daily_break_valid"] == True) & (extra["v124_parent_distance"].fillna(0) >= 0.08), "v12_formal_push_ok"] = True
    # V12.5：时间窗口本身不等于买点；必须叠加日线触发/回踩承接/台阶再突破，才允许进入正式候选。
    extra.loc[
        (extra["score_v125_timing_window"] >= 13)
        & (
            (extra["v125_timing_trigger"] == True)
            | (extra["v125_step_break_trigger"] == True)
            | (extra["score_v12_pullback_entry"] >= 6)
        )
        & (df["long_pos_250"] <= 0.78),
        "v12_formal_push_ok"
    ] = True
    # V12.6：时间窗口/重心平量/1000日窄口只做加分和提示；只有叠加日线触发/回踩/台阶突破时才放行。
    extra.loc[
        (extra["score_v126_timing_sufficiency"].fillna(0) >= 7)
        & (
            (extra["score_v12_pullback_entry"] >= 6)
            | (extra["v125_step_break_trigger"] == True)
            | (extra["v125_timing_trigger"] == True)
            | (extra["v126_bottom_repair_trigger"] == True)
        )
        & (df["long_pos_250"] <= 0.82),
        "v12_formal_push_ok"
    ] = True
    # V15：压力带突破模型是选股模型主战法之一。
    # S级完整穿透可直接作为正式候选资格；A级供需压力突破/消化突破也可入候选，但仍受V14/雷区/RR二次审核。
    extra.loc[extra["xhu_pressure_model_grade"].isin(["S", "A"]) & (extra["score_xhu_pressure_breakout"] >= 12), "v12_formal_push_ok"] = True
    extra.loc[extra["xhu_pressure_model_grade"].eq("S"), "v12_entry_label"] = "V15压力带完整穿透，选股模型S级突破候选"
    extra.loc[extra["xhu_pressure_model_grade"].eq("A") & (extra["score_xhu_pressure_breakout"] >= 12), "v12_entry_label"] = "V15压力带核心突破/消化突破，选股模型A级候选"


    # 承接结构总分：涨停后三日实体承接 + 普通关键位回踩承接.
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
        if _i not in _active_deep_indices:
            step_scores.append(0.0)
            step_descs.append("非当前候选K线，跳过台阶结构精算")
            continue
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
            down_body = ((seg["open"] - seg["close"]) / seg["open"].replace(0, np.nan)).where(seg["close"] < seg["open"], 0).max()
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

    extra["score_trend_stage"] = 0.0
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

    rs = avg_gain / avg_loss.replace(0, np.nan)
    extra["rsi"] = 100 - (100 / (1 + rs))

    tp = (df["high"] + df["low"] + df["close"]) / 3
    tp_ma = tp.rolling(14).mean()
    tp_md = (tp - tp_ma).abs().rolling(14).mean()
    extra["cci"] = (tp - tp_ma) / (0.015 * tp_md.replace(0, np.nan))

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

    extra["score_count"] = 0.0
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

    # ========================= V12：100日活跃度/松散度模块 =========================
    # 100日涨停次数是市场辨识度/攻击性的重要指标；同时用大阳/大阴、跳空、振幅、K线黏密度判断松散度。
    extra["limit_up_count_100"] = extra["limit_up"].rolling(100).sum()
    extra["big_yang_count_100"] = ((df["pct_chg"] >= 5.0) & (df["close"] > df["open"])).rolling(100).sum()
    extra["big_yin_count_100"] = ((df["pct_chg"] <= -5.0) & (df["close"] < df["open"])).rolling(100).sum()
    extra["gap_down"] = df["open"] < df["low"].shift(1)
    extra["gap_total_count_100"] = (extra["gap_up"] | extra["gap_down"]).rolling(100).sum()
    true_range = pd.concat([
        (df["high"] - df["low"]).abs(),
        (df["high"] - df["close"].shift(1)).abs(),
        (df["low"] - df["close"].shift(1)).abs(),
    ], axis=1).max(axis=1)
    extra["atr_pct_20"] = (true_range.rolling(20).mean() / df["close"].replace(0, np.nan)).fillna(0)
    body_pct_abs = ((df["close"] - df["open"]).abs() / df["close"].replace(0, np.nan)).fillna(0)
    extra["small_body_ratio_60"] = (body_pct_abs <= 0.012).rolling(60).mean().fillna(0)
    extra["score_v12_activity"] = 0.0

    extra.loc[extra["limit_up_count_100"] <= 0, "score_v12_activity"] -= 4.0
    extra.loc[extra["limit_up_count_100"] == 1, "score_v12_activity"] -= 1.5
    extra.loc[extra["limit_up_count_100"] == 2, "score_v12_activity"] += 0.5
    extra.loc[extra["limit_up_count_100"].between(3, 4), "score_v12_activity"] += 2.5
    extra.loc[extra["limit_up_count_100"] >= 5, "score_v12_activity"] += 4.0

    extra.loc[extra["big_yang_count_100"] >= 4, "score_v12_activity"] += 1.5
    extra.loc[extra["big_yang_count_100"] >= 7, "score_v12_activity"] += 1.5
    extra.loc[extra["gap_total_count_100"] >= 4, "score_v12_activity"] += 1.0
    extra.loc[extra["atr_pct_20"].between(0.025, 0.055), "score_v12_activity"] += 1.5
    extra.loc[(extra["atr_pct_20"] < 0.018) & (extra["small_body_ratio_60"] >= 0.55), "score_v12_activity"] -= 3.0
    extra.loc[(extra["small_body_ratio_60"] >= 0.70), "score_v12_activity"] -= 2.0
    # 大阴线明显多于大阳线，或高位剧烈波动，不把活跃误判成好事。
    extra.loc[(extra["big_yin_count_100"] >= extra["big_yang_count_100"] + 2) & (extra["big_yin_count_100"] >= 4), "score_v12_activity"] -= 2.5
    extra.loc[(df["long_pos_250"] > 0.75) & (extra["limit_up_count_100"] >= 5), "score_v12_activity"] -= 2.0
    extra.loc[(df["long_pos_250"] > 0.80) & (extra["atr_pct_20"] > 0.065), "score_v12_activity"] -= 2.5
    extra["score_v12_activity"] = extra["score_v12_activity"].clip(-8, 8)

    extra["v12_activity_label"] = "活跃度一般"
    extra.loc[extra["score_v12_activity"] >= 4, "v12_activity_label"] = "活跃度较好，有资金攻击记忆"
    extra.loc[extra["score_v12_activity"] <= -3, "v12_activity_label"] = "活跃度偏低，K线偏黏/攻击性不足"
    extra.loc[(df["long_pos_250"] > 0.75) & (extra["score_v12_activity"] > 0), "v12_activity_label"] = "活跃但位置偏高，防高位乱震"

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
    extra["distance_to_key"] = (df["close"] / key_level.replace(0, np.nan) - 1).fillna(0)
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

    extra["score_penalty"] = 0.0
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

    extra["score_long_cycle"] = 0.0
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
    extra["defense_dist"] = (df["close"] / extra["real_defense_level"].replace(0, np.nan) - 1).fillna(0)
    extra.loc[extra["defense_dist"] < 0, "defense_dist"] = 0
    extra["target_dist"] = df["near_pressure_dist"].where(df["near_pressure_dist"] > 0, df["mid_pressure_dist"]).fillna(0)
    extra.loc[extra["target_dist"] <= 0, "target_dist"] = df["overhead_pressure_dist"]
    # 黄金扩展150%如果还在上方，也可作为第一目标；若比近端压力更近，则取更保守目标。
    fibo_target_dist = (extra["fibo_level_150"] / df["close"].replace(0, np.nan) - 1).fillna(0)
    extra.loc[(fibo_target_dist > 0) & ((extra["target_dist"] <= 0) | (fibo_target_dist < extra["target_dist"])), "target_dist"] = fibo_target_dist
    extra["risk_reward_ratio"] = (extra["target_dist"] / extra["defense_dist"].replace(0, np.nan)).fillna(0)

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

    extra["score_overlap_adjustment"] = 0.0
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

    # ========================= V12：同源结构合并与封顶 =========================
    # 凹口/平台/黄金倍量/月线最大量阳K高点/大量阳K实底，本质都属于“关键结构位”。
    # 保留所有原逻辑，但用负向调整避免同一类行为重复堆分。
    _structure_raw_sum = (
        extra["score_structure_core"].clip(lower=0)
        + extra["score_advanced_ao_kou"].clip(lower=0)
        + extra["score_fibo_reclaim"].clip(lower=0)
        + extra["score_multi_tf_key_structure"].clip(lower=0)
        + extra["score_xhu_pressure_breakout"].fillna(0) * 1.15
        + extra["score_v124_probe_second_confirm"].fillna(0) * 0.85
        + extra["score_v125_timing_window"].fillna(0) * 0.35
        + extra["score_v125_step_platform_lift"].fillna(0) * 0.45
        + extra["score_v126_timing_sufficiency"].fillna(0) * 0.35
        + extra["score_v126_multiframe_center_volume"].fillna(0) * 0.25
        + extra["score_break_k_quality"].clip(lower=0)
    )
    _structure_max = pd.concat([
        extra["score_structure_core"].clip(lower=0),
        extra["score_advanced_ao_kou"].clip(lower=0),
        extra["score_fibo_reclaim"].clip(lower=0),
        extra["score_multi_tf_key_structure"].clip(lower=0),
        extra["score_xhu_pressure_breakout"].fillna(0) * 1.15,
        extra["score_break_k_quality"].clip(lower=0) * 1.5,
    ], axis=1).max(axis=1)
    _structure_hits = (pd.concat([
        extra["score_structure_core"].clip(lower=0),
        extra["score_advanced_ao_kou"].clip(lower=0),
        extra["score_fibo_reclaim"].clip(lower=0),
        extra["score_multi_tf_key_structure"].clip(lower=0),
    ], axis=1) >= 4).sum(axis=1)
    _structure_merged_allowance = (_structure_max + (_structure_hits - 1).clip(lower=0) * 2.0).clip(upper=26.0)
    extra["score_v12_same_source_adjustment"] = (_structure_merged_allowance - _structure_raw_sum).clip(lower=-18.0, upper=0.0)

    # ========================= V12.6：机构级分层打分框架 =========================
    # 目标：不删逻辑，但把同源信号合并成“结构、突破、承接、量能、活跃度、交易质量、风险”七个块，
    # 避免凹口/平台/最大量阳K高点/关键位突破等同一类行为重复堆分。
    extra["score_v121_risk_gate_block"] = (
        extra["score_penalty"].fillna(0)
        + extra["score_overlap_adjustment"].fillna(0) * 0.60
    ).clip(-32.0, 0.0)

    # 结构种子块：大周期、凹口/平台、最大量阳K实底/高点、黄金倍量等统一到关键结构位，不再逐项相加。
    extra["score_v121_structure_seed_block"] = (
        _structure_merged_allowance.fillna(0)
        + extra["score_monthly_cycle"].fillna(0) * 0.60
        + extra["score_long_cycle"].fillna(0) * 0.45
    ).clip(0.0, 34.0)

    # 突破质量块：所有关键位突破只判断一次突破质量。影线试探/假突破不得因为多个模块重复加分。
    _breakout_candidates = pd.concat([
        extra["score_break_k_quality"].fillna(0) * 1.50,
        extra["score_multi_tf_break_quality"].fillna(0) * 0.85,
        extra["score_pattern"].fillna(0) * 0.65,
    ], axis=1)
    extra["score_v121_breakout_quality_block"] = _breakout_candidates.max(axis=1).clip(0.0, 16.0)

    # 回踩承接块：BBIBOLL/BBI/均线/强阳实体/涨停实体/大量阳K实底/结构关键位回踩统一归为承接。
    extra["score_v121_pullback_confirm_block"] = (
        extra["score_v12_pullback_entry"].fillna(0) * 1.25
        + extra["score_carry_structure"].fillna(0) * 0.80
        + extra["score_stepwise_push"].fillna(0) * 0.35
        + extra["score_v125_step_platform_lift"].fillna(0) * 0.55
        + extra["score_v126_multiframe_center_volume"].fillna(0) * 0.35
        + extra["score_behavior"].fillna(0) * 0.20
    ).clip(0.0, 26.0)

    # 量能确认块：标准倍量、倍量后平量、分散健康倍量、阳梯量、阳阴量价统一归类。
    extra["score_v121_volume_confirm_block"] = (
        extra["score_volume_structure"].fillna(0) * 0.70
        + extra["score_yang_yin_volume"].fillna(0) * 0.50
        + extra["score_v125_timing_window"].fillna(0) * 0.25
        + extra["score_v126_multiframe_center_volume"].fillna(0) * 0.20
        + extra["score_count"].fillna(0) * 0.30
    ).clip(-5.0, 22.0)

    # 活跃度弹性块：100日涨停/大阳/大阴/跳空/ATR/黏密度统一输出，避免和量能、频次重复。
    extra["score_v121_activity_elasticity_block"] = (
        extra["score_v12_activity"].fillna(0)
        + extra["score_limitup_activity"].fillna(0)
    ).clip(-8.0, 12.0)

    # 交易质量块：防守位、空间、压力、指标、阶段统一排序；不再让盈亏比单独打满交易优先级。
    extra["score_v121_trade_quality_block"] = (
        extra["score_trade_quality"].fillna(0) * 0.85
        + extra["score_monthly_height_space"].fillna(0) * 0.60
        + extra["score_pressure_space"].fillna(0) * 0.50
        + extra["score_key_distance"].fillna(0) * 0.50
        + extra["score_indicator"].fillna(0) * 0.35
        + extra["score_stage_adjustment"].fillna(0) * 0.55
    ).clip(-32.0, 36.0)

    # V12.5/12.6时间窗口块：只作为优先级放大器，不能单独把无触发股票推上正式候选。
    extra["score_v125_timing_block"] = (
        extra["score_v125_timing_window"].fillna(0) * 0.60
        + extra["score_v125_step_platform_lift"].fillna(0) * 0.38
        + extra["score_v126_timing_sufficiency"].fillna(0) * 0.55
        + extra["score_v126_1000d_window"].fillna(0) * 0.45
    ).clip(0.0, 22.0)

    # V12.6总分：由机构级分层块组成。旧逻辑仍然计算并保留，但不再重复线性堆加。
    extra["total_score"] = (
        extra["score_base_model"].fillna(0)
        + extra["score_v121_structure_seed_block"]
        + extra["score_v121_breakout_quality_block"]
        + extra["score_v121_pullback_confirm_block"]
        + extra["score_v121_volume_confirm_block"]
        + extra["score_v121_activity_elasticity_block"]
        + extra["score_v121_trade_quality_block"]
        + extra["score_xhu_pressure_breakout"].fillna(0) * 0.85
        + extra["score_v125_timing_block"] * 0.55
        + extra["score_v121_risk_gate_block"]
    )

    extra["v121_framework_label"] = "后台跟踪"
    extra.loc[extra["score_v121_structure_seed_block"] >= 18, "v121_framework_label"] = "大周期种子"
    extra.loc[extra["score_v121_pullback_confirm_block"] >= 12, "v121_framework_label"] = "回踩确认"
    extra.loc[extra["v12_formal_push_ok"].fillna(False), "v121_framework_label"] = "正式推送候选"
    extra.loc[extra["score_v121_risk_gate_block"] <= -18, "v121_framework_label"] = "风险降级"
    extra["v121_framework_desc"] = (
        "结构" + extra["score_v121_structure_seed_block"].round(1).astype(str)
        + "/突破" + extra["score_v121_breakout_quality_block"].round(1).astype(str)
        + "/承接" + extra["score_v121_pullback_confirm_block"].round(1).astype(str)
        + "/量能" + extra["score_v121_volume_confirm_block"].round(1).astype(str)
        + "/活跃" + extra["score_v121_activity_elasticity_block"].round(1).astype(str)
        + "/交易" + extra["score_v121_trade_quality_block"].round(1).astype(str)
        + "/时窗" + extra["score_v125_timing_block"].round(1).astype(str)
        + "/充分" + extra["score_v126_timing_sufficiency"].round(1).astype(str)
        + "/风险" + extra["score_v121_risk_gate_block"].round(1).astype(str)
    )


    # ========================= V24：深度层“供应吸收后压力穿透确认”完整确认版 =========================
    # 这不是新增一个独立堆分包，而是把已有压力带、量能、回撤承接、突破K质量、风险反证整合为证据链。
    # 只有压力清晰 + 吸收充分 + 突破/临界触发 + 承接不过线，才允许加分；假突破/派发迹象会封顶或扣分。
    final_pressure_v24 = extra["xhu_pressure_union_upper"].where(extra["xhu_pressure_union_upper"] > 0, extra["xhu_pressure_core_upper"])
    core_pressure_v24 = extra["xhu_pressure_core_upper"].where(extra["xhu_pressure_core_upper"] > 0, final_pressure_v24)
    dist_to_final_v24 = (final_pressure_v24 / df["close"].replace(0, np.nan) - 1).replace([np.inf, -np.inf], np.nan).fillna(0)
    dist_to_final_v24 = dist_to_final_v24.clip(lower=-0.20, upper=1.00)
    # 月/周/日压力带已由V15负责生成，V24只把“是否清晰、是否接近、是否已穿透”标准化。
    extra["v24_supply_pressure_clarity_score"] = 0.0
    extra.loc[extra["xhu_pressure_zone_grade"].isin(["S", "A"]), "v24_supply_pressure_clarity_score"] += 5.0
    extra.loc[extra["xhu_pressure_zone_grade"].isin(["B"]), "v24_supply_pressure_clarity_score"] += 3.0
    extra.loc[extra["xhu_pressure_quality_score"] >= 10, "v24_supply_pressure_clarity_score"] += 2.0
    extra.loc[extra["xhu_pressure_quality_score"] >= 16, "v24_supply_pressure_clarity_score"] += 1.0
    extra.loc[(final_pressure_v24 > 0) & (dist_to_final_v24 <= 0.10), "v24_supply_pressure_clarity_score"] += 1.5
    extra["v24_supply_pressure_clarity_score"] = extra["v24_supply_pressure_clarity_score"].clip(0, 10)

    extra["v24_supply_absorb_context_score"] = 0.0
    extra.loc[extra["xhu_fake_breakout_count"] >= 1, "v24_supply_absorb_context_score"] += 1.5
    extra.loc[extra["score_v126_timing_sufficiency"] >= 4, "v24_supply_absorb_context_score"] += 2.0
    extra.loc[extra["score_v125_timing_block"] >= 4, "v24_supply_absorb_context_score"] += 1.5
    extra.loc[extra["score_v12_pullback_entry"] >= 5, "v24_supply_absorb_context_score"] += 2.0
    extra.loc[extra["score_key_pullback_hold"] >= 3, "v24_supply_absorb_context_score"] += 1.0
    extra.loc[extra["score_yang_yin_volume"] >= 3, "v24_supply_absorb_context_score"] += 1.0
    extra.loc[extra["score_volume_structure"] >= 8, "v24_supply_absorb_context_score"] += 1.0
    extra.loc[extra["score_penalty"] <= -8, "v24_supply_absorb_context_score"] -= 2.0
    extra["v24_supply_absorb_context_score"] = extra["v24_supply_absorb_context_score"].clip(0, 10)

    extra["v24_supply_breakout_quality_score"] = 0.0
    extra.loc[extra["xhu_pressure_model_grade"].eq("S"), "v24_supply_breakout_quality_score"] += 7.0
    extra.loc[extra["xhu_pressure_model_grade"].eq("A"), "v24_supply_breakout_quality_score"] += 5.0
    extra.loc[extra["xhu_breakout_day_grade"].isin(["S", "A"]), "v24_supply_breakout_quality_score"] += 3.0
    extra.loc[(final_pressure_v24 > 0) & (df["close"] > final_pressure_v24 * 1.003), "v24_supply_breakout_quality_score"] += 2.0
    extra.loc[(core_pressure_v24 > 0) & (df["close"] > core_pressure_v24 * 1.003), "v24_supply_breakout_quality_score"] += 1.0
    extra.loc[(df["pos"] >= 0.80) & (df["entity_pct"] >= 3.0), "v24_supply_breakout_quality_score"] += 1.0
    extra.loc[extra["score_break_k_quality"] >= 6, "v24_supply_breakout_quality_score"] += 1.0
    extra["v24_supply_breakout_quality_score"] = extra["v24_supply_breakout_quality_score"].clip(0, 14)

    # 承接验证：突破当天只给触发，真正正式推荐仍要看V12回踩/承接，或压力带S/A本身强到可观察。
    extra["v24_supply_post_hold_score"] = 0.0
    extra.loc[extra["v12_formal_push_ok"], "v24_supply_post_hold_score"] += 4.0
    extra.loc[extra["score_v12_pullback_entry"] >= 6, "v24_supply_post_hold_score"] += 2.0
    extra.loc[extra["score_limitup_hold_3d"] >= 5, "v24_supply_post_hold_score"] += 1.5
    extra.loc[(final_pressure_v24 > 0) & (df["low"] >= final_pressure_v24 * 0.985) & (df["close"] >= final_pressure_v24 * 0.995), "v24_supply_post_hold_score"] += 1.5
    extra["v24_supply_post_hold_score"] = extra["v24_supply_post_hold_score"].clip(0, 8)

    # 反面模型：历史压力区假突破/派发迹象。命中后不能一边拿突破高分一边进正式池。
    upper_shadow_ratio_v24 = ((df["high"] - pd.concat([df["open"], df["close"]], axis=1).max(axis=1)) / (df["high"] - df["low"]).replace(0, np.nan)).fillna(0)
    high_volume_stall_v24 = ((df["vr1"] >= 2.8) | (df["volr"] >= 4.0)) & ((df["pos"] < 0.55) | (upper_shadow_ratio_v24 > 0.38))
    failed_pressure_break_v24 = (final_pressure_v24 > 0) & (df["high"] >= final_pressure_v24 * 1.003) & (df["close"] < final_pressure_v24 * 0.995)
    fast_fall_back_v24 = (final_pressure_v24 > 0) & (df["close"] < final_pressure_v24 * 0.985) & (extra["xhu_pressure_model_grade"].isin(["B", "C", "D"]))
    extra["v24_supply_distribution_risk_score"] = 0.0
    extra.loc[high_volume_stall_v24, "v24_supply_distribution_risk_score"] -= 4.0
    extra.loc[failed_pressure_break_v24, "v24_supply_distribution_risk_score"] -= 5.0
    extra.loc[fast_fall_back_v24, "v24_supply_distribution_risk_score"] -= 3.0
    extra.loc[(df["bias20"] > 0.18) | (df["bias60"] > 0.24), "v24_supply_distribution_risk_score"] -= 2.0
    extra.loc[extra["risk_reward_ratio"].between(0.01, 1.20), "v24_supply_distribution_risk_score"] -= 2.0
    extra["v24_supply_distribution_risk_score"] = extra["v24_supply_distribution_risk_score"].clip(-16, 0)

    extra["score_v24_supply_absorption_confirm"] = (
        extra["v24_supply_pressure_clarity_score"]
        + extra["v24_supply_absorb_context_score"]
        + extra["v24_supply_breakout_quality_score"]
        + extra["v24_supply_post_hold_score"]
        + extra["v24_supply_distribution_risk_score"]
    ).clip(0, 30)
    extra["v24_supply_absorption_grade"] = "D"
    extra.loc[(extra["score_v24_supply_absorption_confirm"] >= 12) & (extra["v24_supply_pressure_clarity_score"] >= 4), "v24_supply_absorption_grade"] = "C"
    extra.loc[(extra["score_v24_supply_absorption_confirm"] >= 17) & (extra["v24_supply_breakout_quality_score"] >= 4), "v24_supply_absorption_grade"] = "B"
    extra.loc[(extra["score_v24_supply_absorption_confirm"] >= 22) & (extra["v24_supply_breakout_quality_score"] >= 7) & (extra["v24_supply_distribution_risk_score"] > -5), "v24_supply_absorption_grade"] = "A"
    extra.loc[(extra["score_v24_supply_absorption_confirm"] >= 25) & (extra["v24_supply_breakout_quality_score"] >= 9) & (extra["v24_supply_post_hold_score"] >= 2) & (extra["v24_supply_distribution_risk_score"] > -4), "v24_supply_absorption_grade"] = "S"
    extra["v24_supply_absorption_desc"] = (
        "V24供应吸收链：压力" + extra["v24_supply_pressure_clarity_score"].round(1).astype(str)
        + "/吸收" + extra["v24_supply_absorb_context_score"].round(1).astype(str)
        + "/突破" + extra["v24_supply_breakout_quality_score"].round(1).astype(str)
        + "/承接" + extra["v24_supply_post_hold_score"].round(1).astype(str)
        + "/风险" + extra["v24_supply_distribution_risk_score"].round(1).astype(str)
        + "，等级" + extra["v24_supply_absorption_grade"].astype(str)
    )

    # V11：交易质量加权调整。保留原有所有因子，但用大周期空间、买点质量、风险收益比重新排序。
    # 量能/强阳若没有结构和买点质量支撑，不再允许单独把总分推到前排。
    structure_strength_v11 = (
        extra["score_structure_core"]
        + extra["score_advanced_ao_kou"]
        + extra["score_fibo_reclaim"]
        + extra["score_multi_tf_key_structure"] * 0.75
        + extra["score_xhu_pressure_breakout"].fillna(0) * 0.85
        + extra["score_monthly_cycle"] * 0.8
        + extra["score_carry_structure"] * 0.6
    )
    # V12：交易优先级不能只看盈亏比。必须以“突破后回踩承接”或明确承接确认作为核心。
    key_action_quality_v12 = (
        extra["score_v12_pullback_entry"] * 1.60
        + extra["score_carry_structure"] * 0.80
        + extra["score_break_k_quality"] * 0.60
        + extra["score_v12_activity"] * 0.35
        + extra["score_v125_timing_block"] * 0.25
        + extra["score_v24_supply_absorption_confirm"] * DEEP_SUPPLY_ABSORB_PRIORITY_WEIGHT
    )
    extra["trade_priority_score"] = (
        structure_strength_v11 * 0.22
        + extra["score_trade_quality"] * 0.75
        + extra["score_monthly_height_space"] * 0.55
        + key_action_quality_v12
        + extra["score_yang_yin_volume"] * 0.35
        + extra["score_indicator"] * 0.25
        + extra["score_penalty"] * 0.90
    ).clip(-30, 40)
    extra["total_score"] = extra["total_score"] + extra["score_trade_quality"] + extra["score_monthly_height_space"] + extra["trade_priority_score"] * 0.35
    # V15：压力带突破A/S属于选股模型明确主战法，参与综合分与交易优先级；B/C/D只轻度提示，不可靠堆分入正式池。
    extra["total_score"] = extra["total_score"] + extra["score_xhu_pressure_breakout"].fillna(0) * 0.65
    # V24供应吸收确认分使用同源封顶：若只是B/C观察，不允许凭该项硬冲正式高分；A/S才可提高交易优先级。
    extra["total_score"] = extra["total_score"] + extra["score_v24_supply_absorption_confirm"].fillna(0) * DEEP_SUPPLY_ABSORB_SCORE_WEIGHT
    extra.loc[extra["v24_supply_absorption_grade"].isin(["B", "C", "D"]), "total_score"] = extra.loc[extra["v24_supply_absorption_grade"].isin(["B", "C", "D"]), "total_score"].clip(upper=84.0)
    extra.loc[extra["xhu_pressure_model_grade"].isin(["B", "C", "D"]), "total_score"] = extra.loc[extra["xhu_pressure_model_grade"].isin(["B", "C", "D"]), "total_score"].clip(upper=84.0)
    extra.loc[extra["v24_supply_distribution_risk_score"] <= -7, "total_score"] = extra.loc[extra["v24_supply_distribution_risk_score"] <= -7, "total_score"].clip(upper=78.0)

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


    # V12：正式推送必须偏向“突破后回踩确认/舒服买点”。
    # 突破当天（无论弱突破还是强突破）只进入后台跟踪池，除非已经同时出现明确回踩承接。
    breakthrough_today_no_pullback = extra["v12_break_today_weak"] & (~extra["v12_formal_push_ok"])
    extra.loc[breakthrough_today_no_pullback, "total_score"] = extra.loc[breakthrough_today_no_pullback, "total_score"].clip(upper=79.0)
    extra.loc[breakthrough_today_no_pullback, "trade_priority_score"] = extra.loc[breakthrough_today_no_pullback, "trade_priority_score"].clip(upper=18.0)
    # 月线/盈亏比很好但买点没到，也不能正式推成A类。
    good_structure_but_no_entry = (
        (structure_strength_v11 >= 8)
        & (extra["score_v12_pullback_entry"] < 5)
        & (~extra["v12_formal_push_ok"])
        & (~extra["limit_up"])
    )
    extra.loc[good_structure_but_no_entry, "total_score"] = extra.loc[good_structure_but_no_entry, "total_score"].clip(upper=79.0)
    extra.loc[extra["v12_formal_push_ok"] & (df["long_pos_250"] <= 0.70) & (extra["score_v12_activity"] >= -3), "total_score"] += 3.0

    merged = pd.concat([df, extra], axis=1)
    merged = apply_chase_risk_gate(merged)

    # V12：突破当天/买点未到的票进入后台跟踪池，不作为正式推送；舒服买点才保留优先候选。
    if "candidate_pool" in merged.columns:
        v12_no_entry = (~merged.get("v12_formal_push_ok", False)) & (~merged.get("xhu_pressure_model_grade", "D").astype(str).isin(["S", "A"])) & (merged.get("v12_break_today_weak", False) | ((merged.get("score_structure_core", 0) + merged.get("score_monthly_cycle", 0) + merged.get("score_multi_tf_key_structure", 0) + merged.get("score_xhu_pressure_breakout", 0)) >= 8))
        merged.loc[v12_no_entry, "candidate_pool"] = "后台跟踪池"
        merged.loc[v12_no_entry, "candidate_pool_reason"] = (merged.loc[v12_no_entry, "candidate_pool_reason"].astype(str) + "；V12买点未到：突破当天或尚未回踩确认").str.strip("；")
        merged.loc[v12_no_entry, "total_score"] = merged.loc[v12_no_entry, "total_score"].clip(upper=79.0)
        v12_entry_ok = merged.get("v12_formal_push_ok", False) & (merged.get("score_v12_activity", 0) > -6)
        merged.loc[v12_entry_ok & merged["candidate_pool"].astype(str).isin(["后台跟踪池", "结构观察池", "交易质量观察池"]), "candidate_pool"] = "优先候选池"
        merged.loc[v12_entry_ok, "candidate_pool_reason"] = "V12舒服买点：突破后回踩确认，位置比突破当天更适合观察"
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
    base["upbody_ma"] = base["upbody_sum"] / base["upcount"].replace(0, np.nan)

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

    base["volscore"] = 0.0
    base.loc[base["volr"] >= 1.2, "volscore"] = 10
    base.loc[base["volr"] >= 1.5, "volscore"] = 20
    base.loc[base["volr"] >= 2.0, "volscore"] = 25
    base.loc[base["volr"] >= 2.5, "volscore"] = 30

    up = df["close"] > df["open"]

    base["bodyscore"] = 0.0
    base.loc[up & (base["body"] >= base["upbody_ma"]), "bodyscore"] = 10
    base.loc[up & (base["body"] >= base["upbody_ma"] * 1.2), "bodyscore"] = 15
    base.loc[up & (base["body"] >= base["upbody_ma"] * 1.5), "bodyscore"] = 20

    base["posscore"] = 0.0
    base.loc[base["pos"] >= 0.6, "posscore"] = 10
    base.loc[base["pos"] >= 0.7, "posscore"] = 15
    base.loc[base["pos"] >= 0.8, "posscore"] = 20

    base["brscore"] = 0.0
    base.loc[df["high"] >= base["prehigh"], "brscore"] = 5
    base.loc[df["close"] > base["prehigh"], "brscore"] = 15
    base.loc[df["close"] > base["prehigh"] * 1.01, "brscore"] = 20

    base["structscore"] = 0.0
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



def v122_base_candidate_gate(row):
    """
    V12.4标准版基础候选闸门：
    只决定是否值得进入昂贵深度评分，不删除交易逻辑。
    机构思路：先看是否有“结构种子/量价触发/交易质量/低位修复”之一；
    对明显高位、极端过热、基础分过低且无结构种子的股票 early exit。
    """
    if ENABLE_V122_BASE_GATE != "1":
        return True, "gate_disabled"

    base_total = safe_float(row.get("base_total_score", row.get("base_score", 0)))
    attack = safe_float(row.get("base_attack_quality_score", 0))
    structure = safe_float(row.get("base_structure_potential_score", 0))
    long_cycle = safe_float(row.get("base_long_cycle_potential_score", 0))
    volume_carry = safe_float(row.get("base_volume_carry_score", 0))
    trade_quality = safe_float(row.get("base_trade_quality_score", 0))
    monthly_proxy = safe_float(row.get("base_monthly_height_proxy_score", 0))
    fibo = safe_float(row.get("base_fibo_second_confirm_score", 0))
    limit_hold = safe_float(row.get("base_limitup_hold_score", 0))
    risk_penalty = safe_float(row.get("base_risk_penalty", 0))
    long_pos = safe_float(row.get("long_pos_250", 0))
    bias20 = safe_float(row.get("bias20", 0))
    bias60 = safe_float(row.get("bias60", 0))
    near_pressure = safe_float(row.get("near_pressure_dist", 0))
    rsi = safe_float(row.get("base_rsi", 50))
    cci = safe_float(row.get("base_cci", 0))
    bucket = normalize_base_bucket_name(row.get("base_bucket", ""))

    seed_score = structure + long_cycle + monthly_proxy + fibo + limit_hold
    trigger_score = attack * 0.40 + volume_carry + trade_quality + fibo

    if risk_penalty <= -18 and base_total < 78 and seed_score < V122_STRONG_SEED_SCORE:
        return False, "基础风险过重且无强结构种子"
    if base_total < V122_MIN_BASE_SCORE_FOR_DEEP and seed_score < V122_STRONG_SEED_SCORE and trigger_score < V122_STRONG_TRIGGER_SCORE:
        return False, "基础分低且无结构/触发种子"
    if long_pos >= 0.88 and bias20 >= 0.18 and seed_score < 14:
        return False, "高位高乖离且结构种子不足"
    if near_pressure > 0 and near_pressure < 0.035 and bias20 >= 0.12 and trigger_score < 16:
        return False, "贴近压力且触发质量不足"
    if rsi >= 86 and cci >= 300 and seed_score < 14:
        return False, "指标重度过热且缺少大结构支撑"
    if ("强势观察" in bucket or "活跃股性" in bucket) and trade_quality < 0 and seed_score < 16:
        return False, "活跃股性/强势观察但交易质量不足"

    return True, "通过V12.5基础闸门"



def record_kline_date_audit(code, name, bs_code, df, stage="base"):
    """记录本次每只股票读到/拉到的K线最新日期，用于确认是否已到当天。"""
    try:
        last_date = ""
        rows = 0
        if df is not None and not getattr(df, "empty", True) and "date" in df.columns:
            last_date = str(df["date"].max())
            rows = int(len(df))
        KLINE_DATE_AUDIT.append({
            "code": str(code),
            "name": str(name),
            "bs_code": str(bs_code),
            "stage": str(stage),
            "last_date": last_date,
            "rows": rows,
        })
    except Exception:
        pass


def print_kline_date_audit(target_date=""):
    """打印K线日期覆盖率，避免只看到股票池日期却不知道K线是否更新到当天。"""
    try:
        items = list(globals().get("KLINE_DATE_AUDIT", []) or [])
        if not items:
            print("K线日期检查：没有可统计的K线数据。")
            return {"target_date": target_date, "total": 0, "latest": 0, "coverage": 0.0}
        dates = [x.get("last_date", "") for x in items if x.get("last_date")]
        if not target_date:
            target_date = LAST_TRADE_DAY or (max(dates) if dates else "")
        total = len(items)
        latest = [x for x in items if x.get("last_date") == target_date]
        stale = [x for x in items if x.get("last_date") and x.get("last_date") != target_date]
        failed = [x for x in items if not x.get("last_date")]
        coverage = len(latest) / max(1, total)
        print("========== K线日期检查 ==========")
        print(f"目标日期：{target_date or '未知'}")
        print(f"最新K线股票：{len(latest)}/{total}，覆盖率：{coverage:.2%}")
        print(f"旧K线股票：{len(stale)}")
        print(f"失败/无K线股票：{len(failed)}")
        if stale:
            print("旧K线示例：")
            for x in stale[:20]:
                print(f"{x.get('code')} {x.get('name')} 最新K线={x.get('last_date')} rows={x.get('rows')}")
        if failed:
            print("失败/无K线示例：")
            for x in failed[:20]:
                print(f"{x.get('code')} {x.get('name')} 无有效K线")
        save_json_file("kline_date_audit.json", {
            "generated_at_bj": bj_time_str(),
            "target_date": target_date,
            "total": total,
            "latest_count": len(latest),
            "stale_count": len(stale),
            "failed_count": len(failed),
            "coverage": coverage,
            "items": items,
        })
        print("K线日期检查文件已保存：kline_date_audit.json")
        print("================================")
        return {"target_date": target_date, "total": total, "latest": len(latest), "coverage": coverage}
    except Exception as e:
        print(f"K线日期检查失败：{e}")
        return {"target_date": target_date, "total": 0, "latest": 0, "coverage": 0.0}


def append_seed_pool_snapshot(rows, path=SEED_POOL_FILE):
    """保存后台种子/跟踪池快照，减少后续人工从Telegram反推。"""
    try:
        if not rows:
            return
        seeds = []
        for r in rows[:300]:
            seeds.append({
                "code": r.get("code", ""),
                "name": r.get("name", ""),
                "date": r.get("date", ""),
                "base_bucket": r.get("base_bucket", ""),
                "base_total_score": safe_float(r.get("base_total_score", r.get("base_score", 0))),
                "base_structure_potential_score": safe_float(r.get("base_structure_potential_score", 0)),
                "base_long_cycle_potential_score": safe_float(r.get("base_long_cycle_potential_score", 0)),
                "base_volume_carry_score": safe_float(r.get("base_volume_carry_score", 0)),
                "base_trade_quality_score": safe_float(r.get("base_trade_quality_score", 0)),
                "note": "V12.5后台种子池：正式推送仍需日线回踩/承接触发",
            })
        save_json_file(path, {"generated_at_bj": bj_time_str(), "seeds": seeds})
        print(f"V12.5后台种子池已保存：{path}，记录{len(seeds)}条")
    except Exception as e:
        print(f"V12.5后台种子池保存失败：{e}")

def process_stock_base(row):
    code = row["代码"]
    name = row["名称"]
    bs_code = row["bs_code"]

    if ENABLE_RISK_EARLY_EXIT == "1":
        risk = evaluate_regulatory_risk(code, name)
        if risk.get("hard_exclude"):
            print(f"V12.5风险硬过滤：{code} {name} 命中重大雷区，跳过K线深算：{'；'.join(risk.get('flags', []))[:80]}")
            return []

    df = get_daily_kline(bs_code, lookback_days=BASE_KLINE_LOOKBACK_DAYS, cache_scope="base")
    record_kline_date_audit(code, name, bs_code, df, stage="base")
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
            "base_channel_explosion_eve_score": float(r.get("base_channel_explosion_eve_score", 0)) if pd.notna(r.get("base_channel_explosion_eve_score", 0)) else 0,
            "base_explosion_eve_score_raw": float(r.get("base_explosion_eve_score_raw", 0)) if pd.notna(r.get("base_explosion_eve_score_raw", 0)) else 0,
            "base_explosion_eve_penalty": float(r.get("base_explosion_eve_penalty", 0)) if pd.notna(r.get("base_explosion_eve_penalty", 0)) else 0,
            "base_explosion_eve_valid": bool(r.get("base_explosion_eve_valid", False)),
            "base_explosion_eve_desc": str(r.get("base_explosion_eve_desc", "")) if pd.notna(r.get("base_explosion_eve_desc", "")) else "",
            "base_exeve_big_cycle_score": float(r.get("base_exeve_big_cycle_score", 0)) if pd.notna(r.get("base_exeve_big_cycle_score", 0)) else 0,
            "base_exeve_volatility_compression_score": float(r.get("base_exeve_volatility_compression_score", 0)) if pd.notna(r.get("base_exeve_volatility_compression_score", 0)) else 0,
            "base_exeve_flat_volume_score": float(r.get("base_exeve_flat_volume_score", 0)) if pd.notna(r.get("base_exeve_flat_volume_score", 0)) else 0,
            "base_exeve_center_lift_score": float(r.get("base_exeve_center_lift_score", 0)) if pd.notna(r.get("base_exeve_center_lift_score", 0)) else 0,
            "base_exeve_attack_memory_score": float(r.get("base_exeve_attack_memory_score", 0)) if pd.notna(r.get("base_exeve_attack_memory_score", 0)) else 0,
            "base_exeve_launch_quality_score": float(r.get("base_exeve_launch_quality_score", 0)) if pd.notna(r.get("base_exeve_launch_quality_score", 0)) else 0,
            "base_channel_supply_absorption_score": float(r.get("base_channel_supply_absorption_score", 0)) if pd.notna(r.get("base_channel_supply_absorption_score", 0)) else 0,
            "base_supply_absorption_valid": bool(r.get("base_supply_absorption_valid", False)),
            "base_supply_absorption_desc": str(r.get("base_supply_absorption_desc", "")) if pd.notna(r.get("base_supply_absorption_desc", "")) else "",
            "base_supply_pressure_clarity_score": float(r.get("base_supply_pressure_clarity_score", 0)) if pd.notna(r.get("base_supply_pressure_clarity_score", 0)) else 0,
            "base_supply_absorb_context_score": float(r.get("base_supply_absorb_context_score", 0)) if pd.notna(r.get("base_supply_absorb_context_score", 0)) else 0,
            "base_supply_volume_platform_score": float(r.get("base_supply_volume_platform_score", 0)) if pd.notna(r.get("base_supply_volume_platform_score", 0)) else 0,
            "base_supply_compression_trigger_score": float(r.get("base_supply_compression_trigger_score", 0)) if pd.notna(r.get("base_supply_compression_trigger_score", 0)) else 0,
            "base_supply_core_upper": float(r.get("base_supply_core_upper", 0)) if pd.notna(r.get("base_supply_core_upper", 0)) else 0,
            "base_supply_dist_to_upper": float(r.get("base_supply_dist_to_upper", 0)) if pd.notna(r.get("base_supply_dist_to_upper", 0)) else 0,
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

    df = get_daily_kline(bs_code, lookback_days=DEEP_KLINE_LOOKBACK_DAYS, cache_scope="deep")
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
            "base_entry_channels": row.get("base_entry_channels", []),
            "base_entry_reason": str(row.get("base_entry_reason", "")),
            "base_recall_score": float(row.get("base_recall_score", 0)) if row.get("base_recall_score", None) is not None else 0,
            "base_risk_score": float(row.get("base_risk_score", 0)) if row.get("base_risk_score", None) is not None else 0,
            "base_risk_level": str(row.get("base_risk_level", "R0")),
            "base_risk_flags": str(row.get("base_risk_flags", "")),
            "base_risk_action": str(row.get("base_risk_action", "")),
            "base_risk_reason": str(row.get("base_risk_reason", "")),
            "base_risk_pool": str(row.get("base_risk_pool", "")),
            "base_seed_pool_flag": bool(row.get("base_seed_pool_flag", False)),
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
            "score_multi_tf_key_structure": float(r.get("score_multi_tf_key_structure", 0)) if pd.notna(r.get("score_multi_tf_key_structure", 0)) else 0,
            "score_xhu_pressure_breakout": float(r.get("score_xhu_pressure_breakout", 0)) if pd.notna(r.get("score_xhu_pressure_breakout", 0)) else 0,
            "xhu_pressure_model_grade": str(r.get("xhu_pressure_model_grade", "")) if pd.notna(r.get("xhu_pressure_model_grade", "")) else "",
            "xhu_pressure_zone_grade": str(r.get("xhu_pressure_zone_grade", "")) if pd.notna(r.get("xhu_pressure_zone_grade", "")) else "",
            "xhu_breakout_day_grade": str(r.get("xhu_breakout_day_grade", "")) if pd.notna(r.get("xhu_breakout_day_grade", "")) else "",
            "xhu_pressure_core_lower": float(r.get("xhu_pressure_core_lower", 0)) if pd.notna(r.get("xhu_pressure_core_lower", 0)) else 0,
            "xhu_pressure_core_upper": float(r.get("xhu_pressure_core_upper", 0)) if pd.notna(r.get("xhu_pressure_core_upper", 0)) else 0,
            "xhu_pressure_union_lower": float(r.get("xhu_pressure_union_lower", 0)) if pd.notna(r.get("xhu_pressure_union_lower", 0)) else 0,
            "xhu_pressure_union_upper": float(r.get("xhu_pressure_union_upper", 0)) if pd.notna(r.get("xhu_pressure_union_upper", 0)) else 0,
            "xhu_final_union_upper": float(r.get("xhu_final_union_upper", 0)) if pd.notna(r.get("xhu_final_union_upper", 0)) else 0,
            "xhu_pressure_quality_score": float(r.get("xhu_pressure_quality_score", 0)) if pd.notna(r.get("xhu_pressure_quality_score", 0)) else 0,
            "xhu_pressure_periods": str(r.get("xhu_pressure_periods", "")) if pd.notna(r.get("xhu_pressure_periods", "")) else "",
            "xhu_pressure_desc": str(r.get("xhu_pressure_desc", "")) if pd.notna(r.get("xhu_pressure_desc", "")) else "",
            "xhu_breakout_desc": str(r.get("xhu_breakout_desc", "")) if pd.notna(r.get("xhu_breakout_desc", "")) else "",
            "score_v24_supply_absorption_confirm": float(r.get("score_v24_supply_absorption_confirm", 0)) if pd.notna(r.get("score_v24_supply_absorption_confirm", 0)) else 0,
            "v24_supply_absorption_grade": str(r.get("v24_supply_absorption_grade", "")) if pd.notna(r.get("v24_supply_absorption_grade", "")) else "",
            "v24_supply_absorption_desc": str(r.get("v24_supply_absorption_desc", "")) if pd.notna(r.get("v24_supply_absorption_desc", "")) else "",
            "v24_supply_pressure_clarity_score": float(r.get("v24_supply_pressure_clarity_score", 0)) if pd.notna(r.get("v24_supply_pressure_clarity_score", 0)) else 0,
            "v24_supply_absorb_context_score": float(r.get("v24_supply_absorb_context_score", 0)) if pd.notna(r.get("v24_supply_absorb_context_score", 0)) else 0,
            "v24_supply_breakout_quality_score": float(r.get("v24_supply_breakout_quality_score", 0)) if pd.notna(r.get("v24_supply_breakout_quality_score", 0)) else 0,
            "v24_supply_post_hold_score": float(r.get("v24_supply_post_hold_score", 0)) if pd.notna(r.get("v24_supply_post_hold_score", 0)) else 0,
            "v24_supply_distribution_risk_score": float(r.get("v24_supply_distribution_risk_score", 0)) if pd.notna(r.get("v24_supply_distribution_risk_score", 0)) else 0,
            "xhu_fake_breakout_count": float(r.get("xhu_fake_breakout_count", 0)) if pd.notna(r.get("xhu_fake_breakout_count", 0)) else 0,
            "xhu_fake_breakout_high": float(r.get("xhu_fake_breakout_high", 0)) if pd.notna(r.get("xhu_fake_breakout_high", 0)) else 0,
            "score_multi_tf_break_quality": float(r.get("score_multi_tf_break_quality", 0)) if pd.notna(r.get("score_multi_tf_break_quality", 0)) else 0,
            "multi_tf_key_desc": str(r.get("multi_tf_key_desc", "")) if pd.notna(r.get("multi_tf_key_desc", "")) else "",
            "multi_tf_best_floor": float(r.get("multi_tf_best_floor", 0)) if pd.notna(r.get("multi_tf_best_floor", 0)) else 0,
            "multi_tf_best_high": float(r.get("multi_tf_best_high", 0)) if pd.notna(r.get("multi_tf_best_high", 0)) else 0,
            "multi_tf_best_timeframe": str(r.get("multi_tf_best_timeframe", "")) if pd.notna(r.get("multi_tf_best_timeframe", "")) else "",
            "multi_tf_pullback_count": float(r.get("multi_tf_pullback_count", 0)) if pd.notna(r.get("multi_tf_pullback_count", 0)) else 0,
            "multi_tf_pullback_stage": str(r.get("multi_tf_pullback_stage", "")) if pd.notna(r.get("multi_tf_pullback_stage", "")) else "",
            "multi_tf_high_break_label": str(r.get("multi_tf_high_break_label", "")) if pd.notna(r.get("multi_tf_high_break_label", "")) else "",
            "multi_tf_high_break_desc": str(r.get("multi_tf_high_break_desc", "")) if pd.notna(r.get("multi_tf_high_break_desc", "")) else "",
            "score_v124_probe_second_confirm": float(r.get("score_v124_probe_second_confirm", 0)) if pd.notna(r.get("score_v124_probe_second_confirm", 0)) else 0,
            "v124_probe_stage": str(r.get("v124_probe_stage", "")) if pd.notna(r.get("v124_probe_stage", "")) else "",
            "v124_probe_desc": str(r.get("v124_probe_desc", "")) if pd.notna(r.get("v124_probe_desc", "")) else "",
            "v124_green_price": float(r.get("v124_green_price", 0)) if pd.notna(r.get("v124_green_price", 0)) else 0,
            "v124_probe_price": float(r.get("v124_probe_price", 0)) if pd.notna(r.get("v124_probe_price", 0)) else 0,
            "v124_parent_pressure": float(r.get("v124_parent_pressure", 0)) if pd.notna(r.get("v124_parent_pressure", 0)) else 0,
            "v124_parent_distance": float(r.get("v124_parent_distance", 0)) if pd.notna(r.get("v124_parent_distance", 0)) else 0,
            "v124_time_ratio": float(r.get("v124_time_ratio", 0)) if pd.notna(r.get("v124_time_ratio", 0)) else 0,
            "v124_daily_break_valid": bool(r.get("v124_daily_break_valid", False)),
            "v124_daily_break_label": str(r.get("v124_daily_break_label", "")) if pd.notna(r.get("v124_daily_break_label", "")) else "",
            "score_v125_timing_window": float(r.get("score_v125_timing_window", 0)) if pd.notna(r.get("score_v125_timing_window", 0)) else 0,
            "v125_timing_label": str(r.get("v125_timing_label", "")) if pd.notna(r.get("v125_timing_label", "")) else "",
            "v125_timing_desc": str(r.get("v125_timing_desc", "")) if pd.notna(r.get("v125_timing_desc", "")) else "",
            "v125_volume_stability_score": float(r.get("v125_volume_stability_score", 0)) if pd.notna(r.get("v125_volume_stability_score", 0)) else 0,
            "v125_flat_volume_ratio": float(r.get("v125_flat_volume_ratio", 0)) if pd.notna(r.get("v125_flat_volume_ratio", 0)) else 0,
            "v125_timing_trigger": bool(r.get("v125_timing_trigger", False)),
            "v125_platform_days": float(r.get("v125_platform_days", 0)) if pd.notna(r.get("v125_platform_days", 0)) else 0,
            "score_v125_step_platform_lift": float(r.get("score_v125_step_platform_lift", 0)) if pd.notna(r.get("score_v125_step_platform_lift", 0)) else 0,
            "v125_step_platform_label": str(r.get("v125_step_platform_label", "")) if pd.notna(r.get("v125_step_platform_label", "")) else "",
            "v125_step_platform_desc": str(r.get("v125_step_platform_desc", "")) if pd.notna(r.get("v125_step_platform_desc", "")) else "",
            "v125_step_volume_ratio": float(r.get("v125_step_volume_ratio", 0)) if pd.notna(r.get("v125_step_volume_ratio", 0)) else 0,
            "v125_step_price_lift": float(r.get("v125_step_price_lift", 0)) if pd.notna(r.get("v125_step_price_lift", 0)) else 0,
            "v125_step_flat_ratio": float(r.get("v125_step_flat_ratio", 0)) if pd.notna(r.get("v125_step_flat_ratio", 0)) else 0,
            "v125_step_break_trigger": bool(r.get("v125_step_break_trigger", False)),
            "score_v125_timing_block": float(r.get("score_v125_timing_block", 0)) if pd.notna(r.get("score_v125_timing_block", 0)) else 0,
            "score_v126_timing_sufficiency": float(r.get("score_v126_timing_sufficiency", 0)) if pd.notna(r.get("score_v126_timing_sufficiency", 0)) else 0,
            "v126_timing_sufficiency_desc": str(r.get("v126_timing_sufficiency_desc", "")) if pd.notna(r.get("v126_timing_sufficiency_desc", "")) else "",
            "score_v126_multiframe_center_volume": float(r.get("score_v126_multiframe_center_volume", 0)) if pd.notna(r.get("score_v126_multiframe_center_volume", 0)) else 0,
            "v126_mtf_cv_label": str(r.get("v126_mtf_cv_label", "")) if pd.notna(r.get("v126_mtf_cv_label", "")) else "",
            "v126_mtf_cv_desc": str(r.get("v126_mtf_cv_desc", "")) if pd.notna(r.get("v126_mtf_cv_desc", "")) else "",
            "v126_best_timeframe": str(r.get("v126_best_timeframe", "")) if pd.notna(r.get("v126_best_timeframe", "")) else "",
            "v126_center_up_score": float(r.get("v126_center_up_score", 0)) if pd.notna(r.get("v126_center_up_score", 0)) else 0,
            "v126_volume_flat_score": float(r.get("v126_volume_flat_score", 0)) if pd.notna(r.get("v126_volume_flat_score", 0)) else 0,
            "v126_flat_ratio": float(r.get("v126_flat_ratio", 0)) if pd.notna(r.get("v126_flat_ratio", 0)) else 0,
            "score_v126_1000d_window": float(r.get("score_v126_1000d_window", 0)) if pd.notna(r.get("score_v126_1000d_window", 0)) else 0,
            "v126_1000d_label": str(r.get("v126_1000d_label", "")) if pd.notna(r.get("v126_1000d_label", "")) else "",
            "v126_1000d_desc": str(r.get("v126_1000d_desc", "")) if pd.notna(r.get("v126_1000d_desc", "")) else "",
            "v126_days_from_major_high": float(r.get("v126_days_from_major_high", 0)) if pd.notna(r.get("v126_days_from_major_high", 0)) else 0,
            "v126_major_high_price": float(r.get("v126_major_high_price", 0)) if pd.notna(r.get("v126_major_high_price", 0)) else 0,
            "v126_major_high_date": str(r.get("v126_major_high_date", "")) if pd.notna(r.get("v126_major_high_date", "")) else "",
            "score_v126_bottom_repair_seed": float(r.get("score_v126_bottom_repair_seed", 0)) if pd.notna(r.get("score_v126_bottom_repair_seed", 0)) else 0,
            "v126_bottom_repair_label": str(r.get("v126_bottom_repair_label", "")) if pd.notna(r.get("v126_bottom_repair_label", "")) else "",
            "v126_bottom_repair_desc": str(r.get("v126_bottom_repair_desc", "")) if pd.notna(r.get("v126_bottom_repair_desc", "")) else "",
            "v126_bottom_repair_trigger": bool(r.get("v126_bottom_repair_trigger", False)),
            "score_v12_same_source_adjustment": float(r.get("score_v12_same_source_adjustment", 0)) if pd.notna(r.get("score_v12_same_source_adjustment", 0)) else 0,
            "score_v121_risk_gate_block": float(r.get("score_v121_risk_gate_block", 0)) if pd.notna(r.get("score_v121_risk_gate_block", 0)) else 0,
            "score_v121_structure_seed_block": float(r.get("score_v121_structure_seed_block", 0)) if pd.notna(r.get("score_v121_structure_seed_block", 0)) else 0,
            "score_v121_breakout_quality_block": float(r.get("score_v121_breakout_quality_block", 0)) if pd.notna(r.get("score_v121_breakout_quality_block", 0)) else 0,
            "score_v121_pullback_confirm_block": float(r.get("score_v121_pullback_confirm_block", 0)) if pd.notna(r.get("score_v121_pullback_confirm_block", 0)) else 0,
            "score_v121_volume_confirm_block": float(r.get("score_v121_volume_confirm_block", 0)) if pd.notna(r.get("score_v121_volume_confirm_block", 0)) else 0,
            "score_v121_activity_elasticity_block": float(r.get("score_v121_activity_elasticity_block", 0)) if pd.notna(r.get("score_v121_activity_elasticity_block", 0)) else 0,
            "score_v121_trade_quality_block": float(r.get("score_v121_trade_quality_block", 0)) if pd.notna(r.get("score_v121_trade_quality_block", 0)) else 0,
            "v121_framework_label": str(r.get("v121_framework_label", "")) if pd.notna(r.get("v121_framework_label", "")) else "",
            "v121_framework_desc": str(r.get("v121_framework_desc", "")) if pd.notna(r.get("v121_framework_desc", "")) else "",
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
            "v14_bull_engulf_score_current": float(r.get("v14_bull_engulf_score_current", 0)) if pd.notna(r.get("v14_bull_engulf_score_current", 0)) else 0,
            "v14_bull_engulf_grade": float(r.get("v14_bull_engulf_grade", 0)) if pd.notna(r.get("v14_bull_engulf_grade", 0)) else 0,
            "v14_bull_engulf_pattern_score": float(r.get("v14_bull_engulf_pattern_score", 0)) if pd.notna(r.get("v14_bull_engulf_pattern_score", 0)) else 0,
            "v14_bull_engulf_shadow_score": float(r.get("v14_bull_engulf_shadow_score", 0)) if pd.notna(r.get("v14_bull_engulf_shadow_score", 0)) else 0,
            "v14_bull_engulf_volume_score": float(r.get("v14_bull_engulf_volume_score", 0)) if pd.notna(r.get("v14_bull_engulf_volume_score", 0)) else 0,
            "v14_today_upper_shadow_ratio": float(r.get("v14_today_upper_shadow_ratio", 0)) if pd.notna(r.get("v14_today_upper_shadow_ratio", 0)) else 0,
            "v14_today_lower_shadow_ratio": float(r.get("v14_today_lower_shadow_ratio", 0)) if pd.notna(r.get("v14_today_lower_shadow_ratio", 0)) else 0,
            "v14_bull_engulf_desc": str(r.get("v14_bull_engulf_desc", "")) if pd.notna(r.get("v14_bull_engulf_desc", "")) else "",
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


def select_deep_targets_v10_legacy_v192(base_rows, limit):
    """
    V19.2基础候选分桶选择（遗留备份，V20.3.1主流程不调用）。
    仍保留函数名以兼容旧调用，但内部已从V12旧桶升级为V19机会假设桶。
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

    # V12.4：基础闸门先过滤明显无种子/无触发/高风险候选，避免对全市场重复深算。
    gated = []
    gated_out = []
    for r in dedup:
        ok, reason = v122_base_candidate_gate(r)
        if ok:
            gated.append(r)
        else:
            rr = dict(r)
            rr["v122_gate_reason"] = reason
            gated_out.append(rr)
    if gated:
        dedup = gated
    print(f"V19.2基础闸门：通过{len(dedup)}只，提前排除{len(gated_out)}只")
    append_seed_pool_snapshot(dedup)

    # V19.2配额：深度评分默认200只。
    # 重点扩大“回踩确认、资金承接、大周期修复、爆发前夜”，压缩单纯强势观察。
    quota_plan = [
        ("低位强启动/关键位触发", 0.15),
        ("回踩确认/二买候选", 0.20),
        ("大周期修复/多周期共振", 0.125),
        ("资金承接/倍量后平量/台阶推进", 0.175),
        ("平台蓄势/爆发前夜/左侧钝化", 0.125),
        ("结构突破/压力支撑带突破", 0.075),
        ("观察值兜底/资金记忆", 0.10),
        ("活跃股性/强势观察", 0.05),
    ]
    quotas = {name: max(3, int(round(limit * ratio))) for name, ratio in quota_plan}
    # 修正四舍五入误差。
    while sum(quotas.values()) > limit:
        # 优先从活跃观察、低位触发、结构突破里减，避免挤压回踩确认/资金承接/修复/蓄势。
        for name in ["活跃股性/强势观察", "结构突破/压力支撑带突破", "低位强启动/关键位触发", "观察值兜底/资金记忆", "平台蓄势/爆发前夜/左侧钝化", "大周期修复/多周期共振"]:
            if quotas.get(name, 0) > 3 and sum(quotas.values()) > limit:
                quotas[name] -= 1
    while sum(quotas.values()) < limit:
        for name in ["回踩确认/二买候选", "资金承接/倍量后平量/台阶推进", "观察值兜底/资金记忆", "大周期修复/多周期共振", "平台蓄势/爆发前夜/左侧钝化", "低位强启动/关键位触发"]:
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
    bucket_stats["V19.2基础闸门"] = {"available": len(dedup), "quota": limit, "selected": len(selected)}
    return selected, bucket_stats



# ========================= V20.3 基础筛选重构 / 动态风险指标库 =========================
def _v203_add_flag(flags, name, weight=0.0, severity="R1"):
    if name:
        flags.append({"name": str(name), "weight": float(weight), "severity": str(severity)})

def normalize_base_bucket_name(bucket):
    """统一基础分桶命名，避免“强势观察/活跃股性观察/活跃股性/强势观察”混用。"""
    b = str(bucket or "")
    if "强势观察" in b or "活跃股性" in b:
        return "活跃股性/强势观察"
    return b


def _v203_parse_date(text):
    try:
        return pd.to_datetime(str(text)).date()
    except Exception:
        return None


def v203_recent_tracking_codes():
    """读取近期推荐/生命周期文件，把未失效股票强制纳入基础深评候选。"""
    codes = set()
    today = datetime.now().date()

    def _maybe_add(code, date_text="", status=""):
        code = str(code or "").zfill(6)
        if not code or len(code) != 6 or not code.isdigit():
            return
        d = _v203_parse_date(date_text)
        if d is not None and (today - d).days > V203_RECENT_TRACKING_LOOKBACK_DAYS:
            return
        if any(x in str(status) for x in ["放弃", "硬风险", "剔除"]):
            return
        codes.add(code)

    # 1）signals_history.json：通常以 date_code 为键，也可能存dict。
    try:
        hist = load_signal_history()
        if isinstance(hist, dict):
            for k, v in hist.items():
                if isinstance(v, dict):
                    _maybe_add(v.get("code") or str(k).split("_")[-1], v.get("date") or v.get("signal_date") or str(k).split("_")[0], v.get("status", ""))
                else:
                    parts = str(k).split("_")
                    if len(parts) >= 2:
                        _maybe_add(parts[-1], parts[0], "")
    except Exception:
        pass

    # 2）V20生命周期文件：兼容 list/dict 两种格式。
    try:
        if os.path.exists(V20_SIGNAL_LIFECYCLE_FILE):
            with open(V20_SIGNAL_LIFECYCLE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            items = data.get("items", data.get("lifecycle", data)) if isinstance(data, dict) else data
            if isinstance(items, list):
                for it in items:
                    if isinstance(it, dict):
                        _maybe_add(it.get("code"), it.get("signal_date") or it.get("date"), it.get("status") or it.get("lifecycle_status", ""))
    except Exception:
        pass
    return codes


def evaluate_v203_dynamic_base_risk(row):
    """V20.3 动态风险指标库：基础层前置风控，不等深度评分/三号员工再处理。"""
    if V203_ENABLE_DYNAMIC_RISK_LIBRARY != "1":
        return {
            "base_risk_score": 0.0, "base_risk_level": "R0", "base_risk_flags": "",
            "base_risk_action": "动态风险库关闭", "base_risk_reason": "", "base_risk_blocked": False,
            "base_risk_pool": "正常候选池",
        }

    flags = []
    score = 0.0
    code = str(row.get("code", "") or row.get("代码", "")).zfill(6)
    name = str(row.get("name", "") or row.get("名称", ""))

    # 外部硬雷区/监管财务风险优先。
    try:
        reg = evaluate_regulatory_risk(code, name)
        if reg.get("hard_exclude"):
            _v203_add_flag(flags, "基本面/监管/审计硬雷区:" + "；".join(reg.get("flags", []))[:80], 100, "R4")
            score += 100
        elif safe_float(reg.get("penalty", 0)) <= -20:
            _v203_add_flag(flags, "基本面/治理中高风险:" + "；".join(reg.get("flags", []))[:80], 35, "R3")
            score += 35
    except Exception:
        pass

    pct = safe_float(row.get("pct_chg", 0))
    amount = safe_float(row.get("amount", 0))
    close = safe_float(row.get("close", 0))
    vr1 = safe_float(row.get("vr1", 0))
    volr = safe_float(row.get("volr", 0))
    pos = safe_float(row.get("pos", 0.5))
    entity_pct = safe_float(row.get("entity_pct", 0))
    break_rate = safe_float(row.get("break_rate", 0))
    bias20 = safe_float(row.get("bias20", 0))
    bias60 = safe_float(row.get("bias60", 0))
    long_pos = safe_float(row.get("long_pos_250", 0))
    near_p = safe_float(row.get("near_pressure_dist", 0))
    mid_p = safe_float(row.get("mid_pressure_dist", 0))
    overhead_p = safe_float(row.get("overhead_pressure_dist", 0))
    rr = safe_float(row.get("base_risk_reward_ratio", row.get("risk_reward_ratio", 0)))
    defense_dist = safe_float(row.get("base_defense_dist", row.get("defense_dist", 0)))
    target_dist = safe_float(row.get("base_target_dist", 0))
    base_rsi = safe_float(row.get("base_rsi", row.get("rsi", 50)))
    base_cci = safe_float(row.get("base_cci", row.get("cci", 0)))
    bucket = normalize_base_bucket_name(row.get("base_bucket", ""))
    limit_mode = str(row.get("limit_volume_mode", ""))

    # 1）技术/追高风险。
    if long_pos >= 0.90 and bias20 >= 0.15:
        _v203_add_flag(flags, "年内高位+20日高乖离", 18, "R2"); score += 18
    if bias20 >= 0.22 or (bias20 >= 0.18 and bias60 >= 0.18):
        _v203_add_flag(flags, "20/60日乖离过高", 20, "R2"); score += 20
    if base_rsi >= 86 and base_cci >= 300:
        _v203_add_flag(flags, "RSI/CCI重度过热", 24, "R3"); score += 24
    elif base_rsi >= 80 or base_cci >= 250:
        _v203_add_flag(flags, "RSI/CCI偏热", 10, "R1"); score += 10
    if break_rate > 0.08 and defense_dist > 0.08:
        _v203_add_flag(flags, "突破过远且离防守位远", 14, "R2"); score += 14

    # 2）量价派发/分歧风险。
    if (vr1 >= 5.0 or volr >= 5.0) and pct < 4:
        _v203_add_flag(flags, "极端放量但价格推进不足", 28, "R3"); score += 28
    elif vr1 >= 3.5 or volr >= 4.5:
        _v203_add_flag(flags, "量能极端放大", 15, "R2"); score += 15
    if pos < 0.35 and (vr1 >= 2.0 or volr >= 2.5):
        _v203_add_flag(flags, "放量收盘弱/长上影风险", 22, "R3"); score += 22
    if "分歧爆量" in limit_mode:
        _v203_add_flag(flags, "涨停分歧爆量", 15, "R2"); score += 15

    # 3）压力/空间/RR风险。
    if near_p > 0 and near_p < 0.035 and pct >= 2:
        _v203_add_flag(flags, "近端压力贴脸", 22, "R3"); score += 22
    elif near_p > 0 and near_p < 0.06:
        _v203_add_flag(flags, "近端压力偏近", 12, "R2"); score += 12
    if mid_p > 0 and mid_p < 0.035 and pct >= 3:
        _v203_add_flag(flags, "中层压力贴近且强攻", 12, "R2"); score += 12
    if rr > 0 and rr < 1.2:
        _v203_add_flag(flags, "风险收益比不足", 18, "R2"); score += 18
    if defense_dist > 0.12:
        _v203_add_flag(flags, "离真实防守位过远", 18, "R2"); score += 18
    elif defense_dist > 0.08:
        _v203_add_flag(flags, "防守距离偏远", 10, "R1"); score += 10
    if target_dist > 0 and target_dist < 0.05:
        _v203_add_flag(flags, "上方空间不足", 14, "R2"); score += 14

    # 4）流动性/可执行风险。
    # V20.3.1修复：部分缓存源没有amount字段或amount被填0，不能直接R3误伤。
    # 若close和volume可用，则用close*volume做保守估算，只给数据提示，不当作成交额缺失硬风险。
    volume = safe_float(row.get("volume", row.get("成交量", 0)))
    amount_effective = amount
    amount_missing_but_estimable = False
    if amount_effective <= 0 and close > 0 and volume > 0:
        amount_effective = close * volume
        amount_missing_but_estimable = True
        _v203_add_flag(flags, "成交额字段缺失，已用收盘价*成交量估算流动性", 3, "R1"); score += 3
    # V24.1：流动性分层。正式候选优先 0.8 亿以上；低于 0.5 亿默认不进正式池。
    if amount_effective > 0 and amount_effective < V24_1_ABSOLUTE_MIN_AMOUNT:
        _v203_add_flag(flags, f"成交额低于正式绝对底线<{V24_1_ABSOLUTE_MIN_AMOUNT/100000000:.2f}亿", 30, "R3"); score += 30
    elif amount_effective > 0 and amount_effective < V24_1_MIN_AMOUNT_FOR_FORMAL:
        _v203_add_flag(flags, f"成交额低于V24.1正式门槛<{V24_1_MIN_AMOUNT_FOR_FORMAL/100000000:.2f}亿", 16, "R2"); score += 16
    elif amount_effective > 0 and amount_effective < V24_1_STRICT_AMOUNT_FOR_FORMAL:
        _v203_add_flag(flags, f"成交额未达严格舒适线<{V24_1_STRICT_AMOUNT_FOR_FORMAL/100000000:.2f}亿", 5, "R1"); score += 5
    if close > 0 and close < 2.0:
        _v203_add_flag(flags, "低价股流动性/质量风险", 16, "R2"); score += 16
    if amount_effective <= 0:
        _v203_add_flag(flags, "成交额与成交量均缺失/为0", 25, "R3"); score += 25

    # 5）数据质量风险。
    try:
        date_text = str(row.get("date", ""))
        if DATA_GATE_TARGET_DATE and date_text and date_text < DATA_GATE_TARGET_DATE:
            _v203_add_flag(flags, "K线日期落后目标交易日", 25, "R3"); score += 25
    except Exception:
        pass
    if str(DATA_GATE_COVERAGE).lower().startswith("skip") or "restored" in str(DATA_GATE_COVERAGE).lower() or "restored" in str(DATA_GATE_REASON).lower():
        _v203_add_flag(flags, "使用恢复缓存/数据更新跳过", 6, "R1"); score += 6

    # 6）活跃强攻但结构弱，只做观察。
    seed_score = safe_float(row.get("base_structure_potential_score", 0)) + safe_float(row.get("base_long_cycle_potential_score", 0)) + safe_float(row.get("base_monthly_height_proxy_score", 0))
    if (pct >= 7 or entity_pct >= 7) and seed_score < 8 and long_pos > 0.60:
        _v203_add_flag(flags, "强攻但结构种子不足", 18, "R2"); score += 18

    # 风险等级和动作。
    max_sev = "R0"
    sev_rank = {"R0": 0, "R1": 1, "R2": 2, "R3": 3, "R4": 4}
    for f in flags:
        if sev_rank.get(f.get("severity", "R0"), 0) > sev_rank.get(max_sev, 0):
            max_sev = f.get("severity", "R0")
    if score >= 80 or max_sev == "R4":
        level = "R4"
    elif score >= 45 or max_sev == "R3":
        level = "R3"
    elif score >= 22 or max_sev == "R2":
        level = "R2"
    elif score > 0 or max_sev == "R1":
        level = "R1"
    else:
        level = "R0"

    if level == "R4":
        action, pool, blocked = "基础层硬剔除", "硬剔除池", True
    elif level == "R3":
        action, pool, blocked = "不进正式深评，仅风险观察", "风险观察池", True
    elif level == "R2":
        action, pool, blocked = "允许入池但明显降权/谨慎观察", "谨慎观察池", False
    elif level == "R1":
        action, pool, blocked = "允许入池但轻度降权", "正常候选池", False
    else:
        action, pool, blocked = "正常进入基础通道", "正常候选池", False

    # 某些结构优质但R3的候选，默认仍不进正式深评；如用户想诊断可设置V203_R3_TO_DEEP_LIMIT。
    names = [f["name"] for f in flags]
    return {
        "base_risk_score": float(score),
        "base_risk_level": level,
        "base_risk_flags": "；".join(names),
        "base_risk_action": action,
        "base_risk_reason": "；".join(names[:6]),
        "base_risk_blocked": bool(blocked),
        "base_risk_pool": pool,
    }


def infer_v203_base_entry_channels(row):
    """基础多通道召回：每只票可以命中多个机会入口，便于审计和配额选择。"""
    channels = []
    reasons = []
    def add(ch, why):
        if ch not in channels:
            channels.append(ch)
        if why:
            reasons.append(why)

    bucket = normalize_base_bucket_name(row.get("base_bucket", ""))
    if "回踩" in bucket or safe_float(row.get("base_trade_quality_score", 0)) >= 7 or safe_float(row.get("base_limitup_hold_score", 0)) >= 2:
        add("回踩确认/二买", "回踩/承接/交易质量入口")
    if "资金承接" in bucket or safe_float(row.get("base_volume_carry_score", 0)) >= 7 or safe_float(row.get("flat_volume_count_60_base", 0)) >= 1:
        add("资金承接/倍量后平量", "倍量后平量/阳量承接入口")
    if "大周期" in bucket or safe_float(row.get("base_long_cycle_potential_score", 0)) >= 5 or safe_float(row.get("base_monthly_height_proxy_score", 0)) >= 7:
        add("大周期修复", "长周期位置/年线修复入口")
    if "大级别吸收" in bucket or safe_float(row.get("base_channel_explosion_eve_score", 0)) >= 18 or bool(row.get("base_explosion_eve_valid", False)):
        add("大级别吸收/日线爆发前夜", row.get("base_explosion_eve_desc", "大级别吸收后日线压缩/平量/重心抬高入口"))
    if "供应吸收" in bucket or safe_float(row.get("base_channel_supply_absorption_score", 0)) >= 18 or bool(row.get("base_supply_absorption_valid", False)):
        add("供应吸收/供需压力带临界", row.get("base_supply_absorption_desc", "历史供应区反复测试吸收，接近供需压力上沿入口"))
    if "平台蓄势" in bucket or safe_float(row.get("base_observe_structure_density_score", 0)) >= 5 or safe_float(row.get("base_observation_subscore", 0)) >= 6.5:
        add("平台蓄势/爆发前夜", "平台收敛/观察值入口")
    if "压力" in bucket or (0 < safe_float(row.get("near_pressure_dist", 0)) <= 0.12 and safe_float(row.get("base_structure_potential_score", 0)) >= 5):
        add("压力带临界/精确触发", "靠近压力带/结构线入口")
    if safe_float(row.get("score_bottom_reversal_pattern", 0)) > 0 or safe_float(row.get("base_structure_potential_score", 0)) >= 8 or "观察值" in bucket:
        add("底部反转/结构种子", "底部反转/结构潜力入口")
    if "低位" in bucket or bool(row.get("short_ma_volume_entity_start", False)):
        add("低位强启动", "低位短均线/倍量启动入口")
    if "活跃" in bucket or safe_float(row.get("base_observe_active_memory_score", 0)) >= 3:
        add("活跃股性观察", "近期股性活跃入口")

    if not channels:
        add(bucket or "综合基础质量", "基础综合分入口")

    return channels, "；".join(reasons[:6])


def v203_enrich_base_row(row, recent_tracking_codes=None):
    rr = dict(row)
    recent_tracking_codes = recent_tracking_codes or set()
    risk = evaluate_v203_dynamic_base_risk(rr)
    rr.update(risk)
    channels, reason = infer_v203_base_entry_channels(rr)
    code = str(rr.get("code", "")).zfill(6)
    is_tracking = V203_FORCE_RECENT_TRACKING_IN_POOL == "1" and code in recent_tracking_codes
    if is_tracking:
        if "近期推荐跟踪" not in channels:
            channels.insert(0, "近期推荐跟踪")
        reason = ("近期推荐仍在生命周期内，强制进入基础跟踪；" + reason).strip("；")
        rr["base_seed_pool_flag"] = True
        # 近期推荐健康票不能被轻中度风险直接挤出，但R4硬雷区仍剔除。
        if rr.get("base_risk_level") in ["R1", "R2", "R3"] and rr.get("base_risk_level") != "R4":
            rr["base_risk_action"] = "近期推荐跟踪：保留跟踪，但按风险降权"
            rr["base_risk_blocked"] = False if rr.get("base_risk_level") != "R4" else True
    else:
        rr["base_seed_pool_flag"] = False
    rr["base_entry_channels"] = channels
    rr["base_entry_reason"] = reason or "基础综合质量入围"

    # V20.3召回分：风险前置降权，但不过度压制优质种子。
    recall = safe_float(rr.get("base_bucket_rank_score", rr.get("base_total_score", rr.get("base_score", 0))))
    recall += min(8.0, safe_float(rr.get("base_observation_subscore", 0)) * 0.6)
    recall += min(8.0, safe_float(rr.get("base_channel_explosion_eve_score", 0)) * 0.25)
    recall += min(7.0, safe_float(rr.get("base_channel_supply_absorption_score", 0)) * 0.22)
    recall += min(6.0, safe_float(rr.get("base_structure_potential_score", 0)) * 0.25)
    recall += min(5.0, safe_float(rr.get("base_volume_carry_score", 0)) * 0.25)
    if is_tracking:
        recall += 8.0
    level = str(rr.get("base_risk_level", "R0"))
    if level == "R1": recall -= 3
    elif level == "R2": recall -= 10
    elif level == "R3": recall -= 25
    elif level == "R4": recall -= 999
    rr["base_recall_score"] = float(recall)
    return rr


def v203_pick_channel_quota(limit):
    # V20.3配额：基础层重召回，偏向回踩、资金承接、周期修复、蓄势和压力临界，压缩单纯活跃强攻。
    plan = [
        ("回踩确认/二买", 0.18),
        ("资金承接/倍量后平量", 0.16),
        ("大周期修复", 0.14),
        ("大级别吸收/日线爆发前夜", 0.13),
        ("平台蓄势/爆发前夜", 0.12),
        ("压力带临界/精确触发", 0.115),
        ("底部反转/结构种子", 0.09),
        ("低位强启动", 0.055),
        ("活跃股性观察", 0.01),
    ]
    quotas = {name: max(2, int(round(limit * ratio))) for name, ratio in plan}
    while sum(quotas.values()) > limit:
        for name in ["活跃股性观察", "低位强启动", "底部反转/结构种子", "压力带临界/精确触发", "平台蓄势/爆发前夜"]:
            if quotas.get(name, 0) > 2 and sum(quotas.values()) > limit:
                quotas[name] -= 1
    while sum(quotas.values()) < limit:
        for name in ["回踩确认/二买", "资金承接/倍量后平量", "大周期修复", "大级别吸收/日线爆发前夜", "平台蓄势/爆发前夜", "压力带临界/精确触发"]:
            if sum(quotas.values()) < limit:
                quotas[name] += 1
    return plan, quotas


def save_v203_base_audit(enriched, selected, gated_out, bucket_stats):
    try:
        payload = {
            "generated_at_bj": bj_time_str(),
            "model_version": MODEL_VERSION,
            "summary": {
                "enriched": len(enriched),
                "selected": len(selected),
                "gated_out": len(gated_out),
                "bucket_stats": bucket_stats,
            },
            "selected": [
                {
                    "code": r.get("code", ""), "name": r.get("name", ""), "date": r.get("date", ""),
                    "base_recall_score": safe_float(r.get("base_recall_score", 0)),
                    "base_entry_channels": r.get("base_entry_channels", []),
                    "base_entry_reason": r.get("base_entry_reason", ""),
                    "base_risk_level": r.get("base_risk_level", ""),
                    "base_risk_action": r.get("base_risk_action", ""),
                    "base_risk_flags": r.get("base_risk_flags", ""),
                }
                for r in selected[:500]
            ],
            "gated_out": [
                {
                    "code": r.get("code", ""), "name": r.get("name", ""), "date": r.get("date", ""),
                    "base_recall_score": safe_float(r.get("base_recall_score", 0)),
                    "base_entry_channels": r.get("base_entry_channels", []),
                    "base_risk_level": r.get("base_risk_level", ""),
                    "base_risk_action": r.get("base_risk_action", ""),
                    "base_risk_flags": r.get("base_risk_flags", ""),
                    "v122_gate_reason": r.get("v122_gate_reason", ""),
                }
                for r in gated_out[:300]
            ],
        }
        with open(V203_BASE_AUDIT_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        # 风险审计单独保存所有R2以上。
        risk_rows = [r for r in enriched if str(r.get("base_risk_level", "R0")) in ["R2", "R3", "R4"]]
        with open(V203_BASE_RISK_AUDIT_FILE, "w", encoding="utf-8") as f:
            json.dump({"generated_at_bj": bj_time_str(), "risk_rows": risk_rows[:1000]}, f, ensure_ascii=False, indent=2)
        print(f"V20.3基础筛选审计已保存：{V203_BASE_AUDIT_FILE}；风险审计：{V203_BASE_RISK_AUDIT_FILE}")
    except Exception as e:
        print(f"V20.3基础筛选审计保存失败：{e}")


def select_deep_targets_v10(base_rows, limit):
    """
    V20.3基础筛选重构版：
    多通道召回 + 动态风险前置过滤 + 近期推荐跟踪强制入池 + 入围原因可审计。
    保留函数名以兼容主流程。
    """
    if not base_rows:
        return [], {}
    if V203_ENABLE_BASE_RECALL_REBUILD != "1":
        # 若关闭V20.3，则退回一个简单排序逻辑，避免递归调用旧函数不可用。
        rows = sorted(base_rows, key=lambda x: safe_float(x.get("base_bucket_rank_score", x.get("base_score", 0))), reverse=True)
        return rows[:int(max(1, limit))], {"V20.3关闭": {"available": len(rows), "quota": limit, "selected": min(len(rows), limit)}}

    limit = int(max(1, limit))
    recent_codes = v203_recent_tracking_codes()

    # 每只股票只保留最优基础候选。
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
    dedup, seen = [], set()
    for r in sorted_rows:
        code = str(r.get("code", ""))
        if not code or code in seen:
            continue
        seen.add(code)
        dedup.append(r)

    enriched = [v203_enrich_base_row(r, recent_codes) for r in dedup]
    if V27_ENABLE_CORE_ENGINE == "1" and V27_ENABLE_BASE_RECALL_OVERLAY == "1":
        enriched = [v27_base_recall_overlay(r) for r in enriched]

    # 风险前置过滤：R4硬剔除；R3默认风险观察，不进正式深评，除非近期推荐跟踪或用户设置允许少量诊断。
    gated, gated_out, r3_watch = [], [], []
    for r in enriched:
        level = str(r.get("base_risk_level", "R0"))
        if level == "R4":
            rr = dict(r); rr["v122_gate_reason"] = "V20.3动态风险R4硬剔除"; gated_out.append(rr); continue
        if level == "R3" and not bool(r.get("base_seed_pool_flag", False)):
            r3_watch.append(r); continue
        ok, reason = v122_base_candidate_gate(r)
        if ok or bool(r.get("base_seed_pool_flag", False)):
            gated.append(r)
        else:
            rr = dict(r); rr["v122_gate_reason"] = reason; gated_out.append(rr)

    # 如用户允许，少量R3进入诊断深评，但排在最后。
    if V203_R3_TO_DEEP_LIMIT > 0 and r3_watch:
        r3_sorted = sorted(r3_watch, key=lambda x: safe_float(x.get("base_recall_score", 0)), reverse=True)[:V203_R3_TO_DEEP_LIMIT]
        for r in r3_sorted:
            rr = dict(r); rr["base_risk_action"] = "R3诊断深评，禁止正式A档"; gated.append(rr)

    print(f"V20.3基础风险/闸门：原始{len(dedup)}只，通过{len(gated)}只，R4/闸门排除{len(gated_out)}只，R3风险观察{len(r3_watch)}只，近期跟踪{len(recent_codes)}只")
    append_seed_pool_snapshot(gated)

    plan, quotas = v203_pick_channel_quota(limit)
    selected, selected_codes, bucket_stats = [], set(), {}

    # 近期推荐跟踪先入池，防止健康回调/小涨后消失。
    tracking_rows = [r for r in gated if bool(r.get("base_seed_pool_flag", False))]
    tracking_rows = sorted(tracking_rows, key=lambda x: safe_float(x.get("base_recall_score", 0)), reverse=True)
    tracking_quota = min(max(3, int(limit * 0.08)), len(tracking_rows), max(3, limit // 8))
    for r in tracking_rows[:tracking_quota]:
        code = str(r.get("code", ""))
        if code not in selected_codes:
            selected.append(r); selected_codes.add(code)
    bucket_stats["近期推荐跟踪"] = {"available": len(tracking_rows), "quota": tracking_quota, "selected": min(len(tracking_rows), tracking_quota)}

    for channel, _ratio in plan:
        rows = [r for r in gated if channel in list(r.get("base_entry_channels", [])) and str(r.get("code", "")) not in selected_codes]
        # R2风险允许入池但排序降权，R3诊断靠后。
        rows = sorted(rows, key=lambda x: (safe_float(x.get("v27_base_deep_priority_score", x.get("base_recall_score", 0))), safe_float(x.get("v27_base_current_trigger_factor", 0)), safe_float(x.get("base_recall_score", 0)), safe_float(x.get("base_total_score", 0))), reverse=True)
        take = rows[:quotas.get(channel, 0)]
        for r in take:
            code = str(r.get("code", ""))
            if code not in selected_codes:
                selected.append(r); selected_codes.add(code)
        bucket_stats[channel] = {"available": len(rows), "quota": quotas.get(channel, 0), "selected": len(take)}

    # 不足则全局补齐：按召回分，避免某通道不足导致池子缩水。
    if len(selected) < limit:
        leftovers = [r for r in gated if str(r.get("code", "")) not in selected_codes]
        leftovers = sorted(leftovers, key=lambda x: (safe_float(x.get("v27_base_deep_priority_score", x.get("base_recall_score", 0))), safe_float(x.get("v27_base_current_trigger_factor", 0)), safe_float(x.get("base_recall_score", 0)), safe_float(x.get("base_total_score", 0))), reverse=True)
        for r in leftovers:
            code = str(r.get("code", ""))
            if code in selected_codes:
                continue
            selected.append(r); selected_codes.add(code)
            if len(selected) >= limit:
                break

    selected = sorted(
        selected,
        key=lambda x: (
            bool(x.get("base_seed_pool_flag", False)),
            safe_float(x.get("v27_base_deep_priority_score", x.get("base_recall_score", 0))),
            safe_float(x.get("v27_base_current_trigger_factor", 0)),
            safe_float(x.get("base_recall_score", 0)),
            safe_float(x.get("base_total_score", x.get("base_score", 0))),
            safe_float(x.get("score", 0)),
        ),
        reverse=True,
    )[:limit]

    bucket_stats["合计"] = {"available": len(gated), "quota": limit, "selected": len(selected)}
    bucket_stats["V20.3动态风险"] = {
        "R0": sum(1 for r in enriched if r.get("base_risk_level") == "R0"),
        "R1": sum(1 for r in enriched if r.get("base_risk_level") == "R1"),
        "R2": sum(1 for r in enriched if r.get("base_risk_level") == "R2"),
        "R3": sum(1 for r in enriched if r.get("base_risk_level") == "R3"),
        "R4": sum(1 for r in enriched if r.get("base_risk_level") == "R4"),
    }
    save_v203_base_audit(enriched, selected, gated_out + r3_watch, bucket_stats)
    return selected, bucket_stats

# ======================= V20.3 基础筛选重构 / 动态风险指标库 END ======================

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
    v12_entry_score = safe_float(s.get("score_v12_pullback_entry", 0))
    v12_formal_ok = bool(s.get("v12_formal_push_ok", False))
    v12_break_today = bool(s.get("v12_break_today_weak", False))

    if bool(s.get("risk_hard_exclude", False)) or risk_flags:
        return {
            "next_day_strategy": "E类：雷区剔除候选",
            "no_chase_line": 0.0,
            "pullback_zone": "不参与交易",
            "confirm_rule": "命中基本面/监管/治理雷区，一号员工阶段直接剔除优先池；技术形态不抵消重大雷区。",
            "abandon_rule": "不进入三号员工可交易候选。",
            "strategy_note": f"雷区标签：{risk_flags}" if risk_flags else "命中硬排雷规则",
        }

    if v12_break_today and not v12_formal_ok:
        key = structure_key if structure_key > 0 else defense
        return {
            "next_day_strategy": "T类：后台跟踪，等回踩确认",
            "no_chase_line": 0.0,
            "pullback_zone": f"等回踩BBIBOLL/BBI、MA5/MA10、强阳实体中部或关键位{key:.2f}附近" if key > 0 else "等回踩中轨/均线/突破阳线实体位",
            "confirm_rule": "突破当天不正式推送；只有后续回踩不破、缩量小阴小阳或重新转强，才进入正式候选。",
            "abandon_rule": "跌回关键位下方且收不回，或回踩放量长阴破中轨，放弃。",
            "strategy_note": "大结构可跟踪，但今天不是舒服买点。",
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
        v12_formal_ok
        and (pct_chg >= 8.5 or safe_float(s.get("score_limitup_activity", 0)) > 0 or score_limitup_hold > 0)
        and not high_chase_risk
        and target_dist >= 0.08
        and defense_dist <= 0.065
        and long_pos < 0.75
    )

    low_absorb = (
        v12_formal_ok
        and defense > 0
        and defense_dist <= 0.055
        and target_dist >= 0.12
        and rr >= 2.0
        and pct_chg <= 4.5
        and long_pos <= 0.65
        and score_trade_quality >= 12
    )

    pullback_confirm = (
        v12_formal_ok
        and not high_chase_risk
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
    if safe_float(s.get("score_v126_timing_sufficiency", 0)) > 0:
        parts126 = []
        if safe_float(s.get("score_v126_multiframe_center_volume", 0)) > 0:
            parts126.append(f"多周期重心/平量：{s.get('v126_mtf_cv_label', '')}，{s.get('v126_best_timeframe', '')}主导，{s.get('v126_mtf_cv_desc', '')}")
        if safe_float(s.get("score_v126_1000d_window", 0)) > 0:
            parts126.append(f"时间窗口：{s.get('v126_1000d_desc', '')}，仅作轻度提示，不单独构成买点")
        if safe_float(s.get("score_v126_bottom_repair_seed", 0)) > 0:
            parts126.append(f"底部修复充分率：{s.get('v126_bottom_repair_label', '')}，{s.get('v126_bottom_repair_desc', '')}")
        if parts126:
            reasons.append("V12.6时窗/充分率：" + "；".join(parts126))
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

    if s.get("score_v124_probe_second_confirm", 0) > 0:
        reasons.append(
            f"V12.4远期绿线/9号线二次确认：{s.get('score_v124_probe_second_confirm', 0):.1f}分，"
            f"阶段={s.get('v124_probe_stage', '')}，绿线{s.get('v124_green_price', 0):.2f}，9号线{s.get('v124_probe_price', 0):.2f}，"
            f"时间倍数{s.get('v124_time_ratio', 0):.2f}，父级压力距当前{s.get('v124_parent_distance', 0):.1%}；"
            f"{s.get('v124_probe_desc', '')}"
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

def build_reason_v12(s):
    """
    V12报告：减少分项堆砌，改用交易语言说明。
    """
    parts = []

    strategy = s.get("next_day_strategy", "")
    if not strategy:
        s.update(classify_next_day_strategy(s))
        strategy = s.get("next_day_strategy", "")

    # 结论
    if bool(s.get("risk_hard_exclude", False)) or s.get("risk_flags"):
        conclusion = "结论：命中财务/审计/监管雷区，技术形态再好也不参与。"
    elif bool(s.get("v12_formal_push_ok", False)):
        conclusion = "结论：突破后已经回踩确认，属于相对舒服的观察点。"
    elif bool(s.get("v12_break_today_weak", False)):
        conclusion = "结论：结构可跟踪，但今天只是突破/摸关键位，不是正式买点。"
    else:
        conclusion = "结论：可观察，但需要继续等更清楚的承接。"
    parts.append(conclusion)

    # 为什么能看
    watch = []
    if s.get("structure_flags"):
        watch.append(f"日线有{s.get('structure_flags')}，关键位约{s.get('structure_neckline', 0):.2f}")
    elif safe_float(s.get("structure_neckline", 0)) > 0:
        watch.append(f"日线接近结构关键位{s.get('structure_neckline', 0):.2f}")
    if safe_float(s.get("score_monthly_cycle", 0)) >= 8:
        watch.append("月线有缩口/中轨修复，大周期有修复基础")
    if safe_float(s.get("score_multi_tf_key_structure", 0)) >= 6:
        watch.append("多周期关键位有记录：" + str(s.get("multi_tf_key_desc", "")))
    if safe_float(s.get("score_multi_tf_break_quality", 0)) >= 8:
        watch.append(str(s.get("multi_tf_high_break_desc", "日线高质量突破多周期关键高点")))
    if safe_float(s.get("score_v124_probe_second_confirm", 0)) >= 6:
        watch.append("远期绿线/9号线二次确认模型：" + str(s.get("v124_probe_desc", "")))
    if safe_float(s.get("score_xhu_pressure_breakout", 0)) >= 7:
        watch.append("V15压力带突破：" + str(s.get("xhu_pressure_desc", "")) + "；" + str(s.get("xhu_breakout_desc", "")))
    if safe_float(s.get("score_v12_activity", 0)) >= 2:
        watch.append(str(s.get("v12_activity_label", "活跃度较好")))
    if safe_float(s.get("score_v12_pullback_entry", 0)) >= 5:
        watch.append(f"已经出现{s.get('v12_entry_desc', '').strip('；')}")
    if watch:
        parts.append("为什么能看：" + "；".join(watch) + "。")

    # 问题
    problems = []
    if safe_float(s.get("score_v12_pullback_entry", 0)) < 8:
        problems.append("买点还没完全到，不能把跟踪票当成今天可买")
    if bool(s.get("v12_break_today_weak", False)) and not bool(s.get("v12_formal_push_ok", False)):
        problems.append("今天属于突破当天/弱突破阶段，容易假突破或次日回落")
    if safe_float(s.get("entity_pct", 0)) < 1.0 and safe_float(s.get("break_rate", 0)) > 0:
        problems.append("突破实体偏小，资金态度不够坚决")
    if "假突破" in str(s.get("multi_tf_high_break_label", "")) or "影线试探" in str(s.get("multi_tf_high_break_label", "")):
        problems.append("多周期关键高点只是影线试探，不能按有效突破处理")
    if not (1.8 < safe_float(s.get("vr1", 0)) < 2.5) and safe_float(s.get("score_limitup_hold_3d", 0)) <= 0:
        problems.append("当日不是标准倍量，量能确认一般")
    if safe_float(s.get("score_v12_activity", 0)) <= -3:
        problems.append(str(s.get("v12_activity_label", "活跃度偏低")))
    if safe_float(s.get("near_pressure_dist", 0)) > 0 and safe_float(s.get("near_pressure_dist", 0)) < 0.08:
        problems.append("上方近端压力偏近")
    if str(s.get("xhu_pressure_model_grade", "")) in ["B", "C"] and safe_float(s.get("xhu_pressure_core_upper", 0)) > 0:
        problems.append("V15压力带尚未完整穿透，当前只是进入/试探复合压力区")
    if str(s.get("xhu_pressure_model_grade", "")) == "D" and safe_float(s.get("xhu_fake_breakout_count", 0)) > 0:
        problems.append("复合压力区存在假突破记忆，本次未完成强实体确认")
    if safe_float(s.get("v124_parent_distance", 0)) > 0 and safe_float(s.get("v124_parent_distance", 0)) < 0.08:
        problems.append("9号线/绿线上方仍有更大父级凹口压力贴脸，突破小门不等于打穿大门")
    if safe_float(s.get("rsi", 0)) >= 80 or safe_float(s.get("cci", 0)) >= 250:
        problems.append("指标偏热，不能追")
    if s.get("risk_flags"):
        problems.append("雷区：" + str(s.get("risk_flags", "")))
    if problems:
        parts.append("问题在哪里：" + "；".join(problems) + "。")

    # 明天怎么确认
    confirm = str(s.get("confirm_rule", ""))
    abandon = str(s.get("abandon_rule", ""))
    zone = str(s.get("pullback_zone", ""))
    if confirm or abandon:
        parts.append(f"明天怎么确认：{confirm}")
        parts.append(f"什么情况放弃：{abandon}")
    if zone:
        parts.append(f"重点观察区：{zone}")

    # 简明后台摘要
    v121_desc = str(s.get("v121_framework_desc", ""))
    if v121_desc:
        parts.append(f"V12.4框架摘要：{v121_desc}，状态={s.get('v121_framework_label', '')}。")
    parts.append(
        f"后台摘要：总分{s.get('total_score', 0):.1f}，买点{s.get('score_v12_pullback_entry', 0):.1f}，"
        f"多周期关键位{s.get('score_multi_tf_key_structure', 0):.1f}，9号线二确{s.get('score_v124_probe_second_confirm', 0):.1f}，"
        f"同源合并{s.get('score_v12_same_source_adjustment', 0):.1f}，活跃度{s.get('score_v12_activity', 0):.1f}，"
        f"交易优先{s.get('trade_priority_score', 0):.1f}，池={s.get('candidate_pool', '')}。"
    )
    return " ".join(parts)


def _v14_clip(value, low=0.0, high=100.0):
    try:
        return max(low, min(high, float(value)))
    except Exception:
        return low

def _grade_rank_v151(g):
    order = {"S": 5, "A": 4, "B": 3, "C": 2, "D": 1, "NONE": 0, "": 0}
    return order.get(str(g).upper(), 0)


def _v151_grade_from_score(score, s_line, a_line, b_line, c_line=0):
    score = safe_float(score, 0)
    if score >= s_line:
        return "S"
    if score >= a_line:
        return "A"
    if score >= b_line:
        return "B"
    if c_line and score >= c_line:
        return "C"
    return "D"


def evaluate_v151_holistic_model_grade(r):
    """
    V16整体模型评级融合：
    不是只把压力带突破评级塞进V14，而是把原一号员工所有主模型一起纳入“选股模型正式模型门槛”。

    输出含义：
    - strongest_model_grade：当前股票命中的最强主模型等级。
    - formal_model_ok：是否至少命中一个A/S主模型。
    - holistic_bonus：对总分的后置校准，不替代原深度总分。
    - model_cap：无A/S主模型时的综合分封顶，避免靠零碎小优点堆进前三。
    """
    models = []

    def add_model(name, grade, score, reason):
        grade = str(grade or "D").upper()
        if grade not in ["S", "A", "B", "C", "D"]:
            grade = "D"
        models.append({
            "name": name,
            "grade": grade,
            "score": round(safe_float(score, 0), 2),
            "reason": str(reason or ""),
            "rank": _grade_rank_v151(grade),
        })

    # 1）V15压力带突破主战法：已经内部融合“压力带等级 × 日K等级 × 模型等级”。
    p_grade = str(r.get("xhu_pressure_model_grade", "") or "D").upper()
    p_score = safe_float(r.get("score_xhu_pressure_breakout", 0))
    if p_grade in ["S", "A", "B", "C", "D"] and (p_score > 0 or p_grade in ["S", "A", "B"]):
        add_model("V15多周期压力带突破", p_grade, p_score, str(r.get("xhu_breakout_desc", "")))

    # 2）V12舒服买点/突破后回踩确认：这是原模型最符合“可下单”的主模型之一。
    pull = safe_float(r.get("score_v12_pullback_entry", 0))
    carry = safe_float(r.get("score_carry_structure", 0))
    trade = safe_float(r.get("score_trade_quality", 0))
    formal = bool(r.get("v12_formal_push_ok", False))
    pull_combo = pull + max(0, carry) * 0.35 + max(0, trade) * 0.20
    if formal and pull_combo >= 13:
        add_model("V12突破后回踩确认/舒服买点", "S", pull_combo, str(r.get("v12_entry_label", "")))
    elif formal or pull_combo >= 9:
        add_model("V12突破后回踩确认/舒服买点", "A", pull_combo, str(r.get("v12_entry_label", "")))
    elif pull_combo >= 6:
        add_model("V12回踩承接观察", "B", pull_combo, str(r.get("v12_entry_label", "")))

    # 3）黄金倍量/首次倍量高点二次确认。
    fibo = safe_float(r.get("score_fibo_reclaim", 0))
    fibo_type = str(r.get("fibo_reclaim_type", ""))
    if fibo > 0:
        if "高扩展位回落" in fibo_type:
            add_model("黄金倍量高位回抽风险", "C", fibo, fibo_type)
        else:
            add_model("黄金倍量二次确认", _v151_grade_from_score(fibo, 10.5, 7.5, 5.5), fibo, fibo_type)

    # 4）高级凹口二次倍量/平台级别突破。
    adv = safe_float(r.get("score_advanced_ao_kou", 0))
    if adv > 0:
        add_model("高级凹口二次倍量", _v151_grade_from_score(adv, 12.0, 8.0, 6.0), adv, str(r.get("advanced_ao_kou_reason", "")))

    # 5）多周期关键结构位：最大量阳K实底/高点、远期绿线/9号线二次确认等。
    mtf = safe_float(r.get("score_multi_tf_key_structure", 0))
    mtf_break = safe_float(r.get("score_multi_tf_break_quality", 0))
    v124 = safe_float(r.get("score_v124_probe_second_confirm", 0))
    mtf_combo = mtf + mtf_break * 0.55 + v124 * 0.45
    if mtf_combo > 0:
        if mtf >= 14 and (mtf_break >= 8 or v124 >= 10):
            grade = "S"
        elif mtf_combo >= 13:
            grade = "A"
        elif mtf_combo >= 8:
            grade = "B"
        else:
            grade = "C"
        add_model("多周期关键结构位突破/确认", grade, mtf_combo, str(r.get("multi_tf_key_desc", "")))

    # 6）月线BBI/BOLL中轨修复 + 日线触发。月线只是底座，必须结合日线动作。
    monthly = safe_float(r.get("score_monthly_cycle", 0))
    structure = safe_float(r.get("score_structure_core", 0))
    if monthly >= 8 and (structure >= 6 or pull >= 6 or p_grade in ["S", "A"]):
        m_combo = monthly + max(structure, pull, p_score) * 0.35
        add_model("月线中轨修复+日线触发", "A" if m_combo < 14 else "S", m_combo, str(r.get("monthly_midline_detail", r.get("monthly_cycle_detail", ""))))
    elif monthly >= 6:
        add_model("月线中轨修复观察", "B", monthly, str(r.get("monthly_midline_detail", "")))

    # 7）破底翻/圆弧底/凹口/平台等原结构核心。
    if structure > 0:
        s_grade = _v151_grade_from_score(structure, 20.0, 13.0, 8.0, 4.0)
        add_model("原结构核心模型", s_grade, structure, str(r.get("structure_reason", r.get("pattern_desc", ""))))

    # 8）时间窗口/爆发前夜只能作为放大器，不能单独成为A/S正式模型。
    timing = safe_float(r.get("score_v125_timing_window", 0)) + safe_float(r.get("score_v126_timing_sufficiency", 0)) * 0.45
    if timing >= 8:
        add_model("爆发前夜时间窗口辅助", "B", timing, str(r.get("v125_timing_desc", "")))

    if not models:
        add_model("无明确主模型", "D", 0, "未命中A/S主战法")

    models = sorted(models, key=lambda x: (x["rank"], x["score"]), reverse=True)
    strongest = models[0]
    grade = strongest["grade"]
    formal_ok = _grade_rank_v151(grade) >= _grade_rank_v151("A")

    # 总分校准：奖励明确主模型，限制无主模型堆分。
    if grade == "S":
        bonus = 5.0
        cap = 100.0
    elif grade == "A":
        bonus = 3.0
        cap = 96.0
    elif grade == "B":
        bonus = 0.5
        cap = 82.0
    elif grade == "C":
        bonus = -1.5
        cap = 78.0
    else:
        bonus = -3.0
        cap = 75.0

    # 多模型共振：两个以上A/S可以再加，但封顶，避免重复堆分。
    as_count = sum(1 for m in models if m["rank"] >= _grade_rank_v151("A"))
    if as_count >= 2:
        bonus += min(2.0, 0.8 * (as_count - 1))

    return {
        "strongest_model_name": strongest["name"],
        "strongest_model_grade": grade,
        "strongest_model_score": strongest["score"],
        "formal_model_ok": formal_ok,
        "holistic_bonus": float(bonus),
        "model_cap": float(cap),
        "as_model_count": int(as_count),
        "models": models[:8],
        "summary": "；".join([f"{m['name']}={m['grade']}({m['score']})" for m in models[:5]]),
    }


def v14_candidate_audit(s):
    """
    V14后置审核：不替代原主模型，只在原total_score基础上做风控/追高/可操作性/量能确认校准，
    并生成可读打分表。财务/审计/监管硬雷区仍一票否决；普通缺点只扣分，不再机械杀光候选。
    """
    r = dict(s)
    original = safe_float(r.get("total_score", 0))
    r["v14_original_total_score"] = original

    hard_reasons = []
    if bool(r.get("risk_hard_exclude", False)):
        hard_reasons.append("财务/审计/监管硬雷区")
    risk_text = str(r.get("risk_flags", ""))
    hard_keywords = ["审计", "保留意见", "无法表示", "否定意见", "退市", "ST", "立案", "信披", "资金占用", "违规担保", "债务违约", "-UW", "-U"]
    if risk_text and any(k in risk_text for k in hard_keywords):
        hard_reasons.append("风险标签命中硬雷区")

    # 大维度拆分：这些是报告解释口径，不从0推翻重算。
    long_cycle = _v14_clip(safe_float(r.get("score_monthly_cycle", 0)) + safe_float(r.get("score_long_cycle", 0)) * 0.30 + safe_float(r.get("score_monthly_height_space", 0)) * 0.30, 0, 15)
    multi_tf = _v14_clip(safe_float(r.get("score_multi_tf_key_structure", 0)) + safe_float(r.get("score_multi_tf_break_quality", 0)) * 0.50 + safe_float(r.get("score_v124_probe_second_confirm", 0)) * 0.35, 0, 12)
    near_structure = _v14_clip(safe_float(r.get("score_structure_core", 0)) + safe_float(r.get("score_pattern", 0)) * 0.35 + safe_float(r.get("score_key_pullback_hold", 0)) * 0.35 + safe_float(r.get("score_advanced_ao_kou", 0)) * 0.40, 0, 18)
    volume_fund = _v14_clip(safe_float(r.get("score_volume_structure", 0)) * 0.65 + safe_float(r.get("score_yang_yin_volume", 0)) * 0.45 + safe_float(r.get("score_count", 0)) * 0.35 + safe_float(r.get("score_v125_step_platform_lift", 0)) * 0.35, 0, 18)
    entry_hold = _v14_clip(safe_float(r.get("score_v12_pullback_entry", 0)) * 0.70 + safe_float(r.get("score_behavior", 0)) * 0.35 + safe_float(r.get("score_limitup_hold_3d", 0)) * 0.45 + safe_float(r.get("score_carry_structure", 0)) * 0.30, 0, 20)
    operability = _v14_clip(safe_float(r.get("score_trade_quality", 0)) * 0.55 + safe_float(r.get("trade_priority_score", 0)) * 0.35 + safe_float(r.get("score_pressure_space", 0)) * 0.35 + safe_float(r.get("score_key_distance", 0)) * 0.45, 0, 12)
    activity_aux = _v14_clip(safe_float(r.get("score_v12_activity", 0)) * 0.50 + safe_float(r.get("score_v125_timing_window", 0)) * 0.30 + safe_float(r.get("score_v126_timing_sufficiency", 0)) * 0.30, 0, 5)
    pressure_breakout = _v14_clip(safe_float(r.get("score_xhu_pressure_breakout", 0)) + safe_float(r.get("xhu_pressure_quality_score", 0)) * 0.03, 0, 14)

    # V16整体模型融合：压力带只是主战法之一；原模型里的回踩确认、黄金倍量、凹口、
    # 多周期关键位、月线修复等，都必须一起进入“选股模型主模型评级”。
    holistic = evaluate_v151_holistic_model_grade(r)
    holistic_bonus = safe_float(holistic.get("holistic_bonus", 0))
    model_cap = safe_float(holistic.get("model_cap", 100))
    pressure_grade = str(r.get("xhu_pressure_model_grade", ""))
    pressure_bonus = 0.0
    if pressure_grade == "S":
        pressure_bonus = 0.8
    elif pressure_grade == "A":
        pressure_bonus = 0.4

    # V14阳包阴细项：作为K线质量解释与少量校准，已纳入原行为分，不重复重奖。
    engulf_score = safe_float(r.get("v14_bull_engulf_score_current", 0))
    engulf_bonus = 0.0
    if engulf_score >= 14:
        engulf_bonus = 1.5
    elif engulf_score >= 10:
        engulf_bonus = 0.8
    elif engulf_score >= 6:
        engulf_bonus = 0.3

    # 追高/无操作性分级扣分：普通偏高扣分，极端才强封顶。
    chase_penalty = 0.0
    chase_reasons = []
    bias20 = safe_float(r.get("bias20", 0))
    bias60 = safe_float(r.get("bias60", 0))
    pct = safe_float(r.get("pct_chg", 0))
    dist_key = safe_float(r.get("distance_to_key", r.get("distance_to_key_base", 0)))
    near_pressure = safe_float(r.get("near_pressure_dist", 0))
    rsi = safe_float(r.get("rsi", 0))
    cci = safe_float(r.get("cci", 0))

    if bias20 > 0.25:
        chase_penalty += 10; chase_reasons.append("20日乖离>25%")
    elif bias20 > 0.18:
        chase_penalty += 7; chase_reasons.append("20日乖离偏高")
    elif bias20 > 0.12:
        chase_penalty += 4; chase_reasons.append("20日乖离略高")
    if bias60 > 0.25:
        chase_penalty += 6; chase_reasons.append("60日乖离偏高")
    if pct > 8 and safe_float(r.get("score_v12_pullback_entry", 0)) < 6:
        chase_penalty += 6; chase_reasons.append("大涨但承接买点不足")
    if dist_key > 0.15:
        chase_penalty += 7; chase_reasons.append("离关键位>15%")
    elif dist_key > 0.08:
        chase_penalty += 4; chase_reasons.append("离关键位偏远")
    if near_pressure > 0 and near_pressure < 0.05:
        chase_penalty += 5; chase_reasons.append("近端压力贴近")
    elif near_pressure > 0 and near_pressure < 0.08:
        chase_penalty += 3; chase_reasons.append("近端压力偏近")
    if rsi >= 85 or cci >= 300:
        chase_penalty += 6; chase_reasons.append("指标重度过热")
    elif rsi >= 80 or cci >= 250:
        chase_penalty += 3; chase_reasons.append("指标偏热")
    chase_penalty = min(chase_penalty, 25)

    # 可操作性扣分：无防守、RR过低、买点未到，只降级不乱杀。
    op_penalty = 0.0
    op_reasons = []
    defense_dist = safe_float(r.get("defense_dist", 0))
    rr = safe_float(r.get("risk_reward_ratio", 0))
    if defense_dist > 0.18:
        op_penalty += 6; op_reasons.append("防守距离过大")
    elif defense_dist > 0.12:
        op_penalty += 4; op_reasons.append("防守距离偏大")
    if rr > 0 and rr < 1.2:
        op_penalty += 5; op_reasons.append("风险收益比不足")
    elif rr > 0 and rr < 1.5:
        op_penalty += 2; op_reasons.append("RR一般")
    if safe_float(r.get("score_v12_pullback_entry", 0)) < 4 and safe_float(r.get("score_multi_tf_break_quality", 0)) < 5:
        op_penalty += 4; op_reasons.append("回踩/突破触发不足")
    op_penalty = min(op_penalty, 15)

    # 量能不足扣分：只做校准，不能一刀切。
    volume_penalty = 0.0
    volume_reasons = []
    if safe_float(r.get("flat_volume_count_60", 0)) <= 0 and safe_float(r.get("scattered_beiliang_count_60", 0)) <= 1 and safe_float(r.get("score_volume_structure", 0)) < 5:
        volume_penalty += 4; volume_reasons.append("健康倍量/倍平量不足")
    if safe_float(r.get("up_down_vol_ratio_60", 1)) > 0 and safe_float(r.get("up_down_vol_ratio_60", 1)) < 0.9:
        volume_penalty += 3; volume_reasons.append("60日阳量弱于阴量")
    volume_penalty = min(volume_penalty, 8)

    # 池子不是优先候选只扣分，不直接杀，保留原模型候选体系但避免0票。
    pool = str(r.get("candidate_pool", "优先候选池"))
    pool_penalty = 0.0
    if pool and pool != "优先候选池":
        pool_penalty = 3.0

    # 总分不再只做“压力带小加分”，而是做整体主模型门槛校准。
    # 有A/S主模型：允许原主模型分数释放；无A/S主模型：限制靠零碎因子堆分进前三。
    v14_adjustment = engulf_bonus + pressure_bonus + holistic_bonus - chase_penalty - op_penalty - volume_penalty - pool_penalty
    final_score = original + v14_adjustment
    if model_cap < 100:
        final_score = min(final_score, model_cap)

    # 严重追高/无防守可封顶，但默认不剔除，避免全市场0只。
    severe_chase = (bias20 > 0.25 and dist_key > 0.12) or (pct > 9 and defense_dist > 0.15)
    if severe_chase:
        final_score = min(final_score, 74.0)
        chase_reasons.append("严重追高封顶")
    if V14_BLOCK_SEVERE_NO_DEFENSE == "1" and defense_dist > 0.22 and rr > 0 and rr < 1.1:
        hard_reasons.append("无交易防守位且RR不足")

    if hard_reasons:
        r["v14_blocked"] = True
        r["v14_block_reason"] = "；".join(sorted(set(hard_reasons)))
        final_score = min(final_score, 59.0)
    else:
        r["v14_blocked"] = False
        r["v14_block_reason"] = ""

    final_score = _v14_clip(final_score, 0, 100)
    r["v14_final_score"] = final_score
    r["v14_adjustment"] = v14_adjustment
    r["v14_chase_penalty"] = -chase_penalty
    r["v14_operability_penalty"] = -op_penalty
    r["v14_volume_penalty"] = -volume_penalty
    r["v14_pool_penalty"] = -pool_penalty
    r["v14_engulf_bonus"] = engulf_bonus
    r["v15_pressure_bonus"] = pressure_bonus
    r["v151_holistic_model_bonus"] = holistic_bonus
    r["v151_model_cap"] = model_cap
    r["v151_strongest_model_name"] = holistic.get("strongest_model_name", "")
    r["v151_strongest_model_grade"] = holistic.get("strongest_model_grade", "")
    r["v151_formal_model_ok"] = bool(holistic.get("formal_model_ok", False))
    r["v151_as_model_count"] = int(holistic.get("as_model_count", 0))
    r["v151_model_summary"] = holistic.get("summary", "")
    try:
        r["v151_models_json"] = json.dumps(holistic.get("models", []), ensure_ascii=False)
    except Exception:
        r["v151_models_json"] = "[]"
    r["v14_chase_reasons"] = "；".join(chase_reasons) if chase_reasons else "无明显追高扣分"
    r["v14_operability_reasons"] = "；".join(op_reasons) if op_reasons else "可操作性未触发明显扣分"
    r["v14_volume_reasons"] = "；".join(volume_reasons) if volume_reasons else "量能未触发明显扣分"

    model_grade = str(holistic.get("strongest_model_grade", ""))
    model_name = str(holistic.get("strongest_model_name", ""))
    if final_score >= 82 and model_grade == "S":
        level = "S类核心候选"
    elif final_score >= 78 and model_grade in ["S", "A"]:
        level = "A类主模型候选"
    elif final_score >= 72 and model_grade in ["A", "B"]:
        level = "B类合格/跟踪候选"
    elif final_score >= 70:
        level = "C类弱候选/需确认"
    else:
        level = "低于正式三选底线"
    r["v14_level"] = f"{level}｜主模型:{model_name}{model_grade}"

    r["v14_score_breakdown"] = {
        "原主模型深度分": round(original, 2),
        "大周期结构": round(long_cycle, 2),
        "多周期最大阳量K/关键位": round(multi_tf, 2),
        "近区结构/精准线": round(near_structure, 2),
        "量能资金": round(volume_fund, 2),
        "承接买点": round(entry_hold, 2),
        "可操作性/RR": round(operability, 2),
        "活跃度/时间辅助": round(activity_aux, 2),
        "V15压力带突破": round(pressure_breakout, 2),
        "V15压力带校准": round(pressure_bonus, 2),
        "V16主模型融合": round(holistic_bonus, 2),
        "V16模型封顶": round(model_cap, 2),
        "阳包阴细项": round(engulf_score, 2),
        "V14阳包阴校准": round(engulf_bonus, 2),
        "追高惩罚": round(-chase_penalty, 2),
        "可操作性惩罚": round(-op_penalty, 2),
        "量能不足惩罚": round(-volume_penalty, 2),
        "池子降级": round(-pool_penalty, 2),
        "V14最终分": round(final_score, 2),
    }
    r["total_score"] = final_score
    return r


def v14_score_table_text(s):
    b = s.get("v14_score_breakdown", {}) or {}
    order = [
        "原主模型深度分", "大周期结构", "多周期最大阳量K/关键位", "近区结构/精准线", "量能资金", "承接买点", "可操作性/RR",
        "活跃度/时间辅助", "V15压力带突破", "V15压力带校准", "V16主模型融合", "V16模型封顶", "阳包阴细项", "V14阳包阴校准", "追高惩罚", "可操作性惩罚", "量能不足惩罚", "池子降级", "V14最终分"
    ]
    parts = []
    for k in order:
        if k in b:
            parts.append(f"{k}:{b[k]}")
    return " | ".join(parts)


def select_final_signals_v14(deep_rows, history=None, limit=None):
    """
    V19.1最终固定Top3：
    - 不再用80分作为正式推荐硬门槛；分数只负责排序。
    - 压力带突破不是必要条件，只是V16/V19评分维度之一。
    - 只剔除硬雷区/硬失败候选：v14_blocked=True。
    - 若非硬风险候选不足Top N，则输出实际可用数量，不用硬风险票补位。
    - 保留diagnostics，用于复盘为什么落选、为什么被阻断。
    """
    if history is None:
        history = {}
    limit = int(limit or V19_FIXED_TOP_N or V14_TARGET_PUSH_COUNT or RESULT_LIMIT or 3)
    # V26.2 LOGIC-ONLY PATCH：V14旧出口仅作兼容/诊断时，避免 effective_limit 未定义导致生产运行异常。
    # 不改变任何生产链路；正式出口仍由后续V26最终买入池控制。
    effective_limit = limit

    audited = [v14_candidate_audit(r) for r in deep_rows]

    blocked = [r for r in audited if r.get("v14_blocked")]
    eligible = [r for r in audited if not r.get("v14_blocked")]

    # V19.1：低于原绝对分数线不再阻断，只进入排序。保留低分原因，供报告和归因使用。
    for r in eligible:
        if safe_float(r.get("v16_final_score", 0)) < 80:
            r["v19_note"] = "低于旧80分线，但V19.1按每日Top3排序仍可参与候选；需在报告中提示确认条件。"

    eligible = sorted(
        eligible,
        key=lambda x: (
            safe_float(x.get("v16_final_score", x.get("v14_final_score", x.get("total_score", 0)))),
            _v16_grade_rank(x.get("v16_final_grade", "")),
            safe_float(x.get("v16_raw_20d_score", 0)),
            safe_float(x.get("trade_priority_score", 0)),
            safe_float(x.get("score_trade_quality", 0)),
            safe_float(x.get("score_v12_pullback_entry", 0)),
            safe_float(x.get("total_score", 0)),
        ),
        reverse=True,
    )

    final = []
    diagnostics = []
    for r in eligible:
        key = f"{r.get('date','')}_{r.get('code','')}"
        if V14_IGNORE_HISTORY_FOR_RERUN != "1" and key in history:
            rr = dict(r)
            rr["v14_skip_reason"] = "signals_history已推送过"
            diagnostics.append(rr)
            continue
        r["v19_pool"] = "正式推荐Top3"
        r["v19_rank"] = len(final) + 1
        final.append(r)
        if len(final) >= effective_limit:
            break

    selected_codes = {str(r.get("code")) for r in final}
    for r in eligible:
        if str(r.get("code")) not in selected_codes:
            rr = dict(r)
            rr["v19_pool"] = "后台跟踪"
            rr["v14_skip_reason"] = "V19.1未进入固定Top3，综合排序靠后，进入后台跟踪/复盘池"
            diagnostics.append(rr)

    for r in blocked[:30]:
        rr = dict(r)
        rr["v19_pool"] = "硬风险剔除"
        rr["v14_skip_reason"] = rr.get("v14_block_reason") or rr.get("v16_cap_reason") or "硬雷区/硬约束剔除"
        diagnostics.append(rr)

    return final, diagnostics[:30], audited

def _v16_load_font(size=18, bold=False):
    try:
        from matplotlib import font_manager
        candidates = [
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
            "/usr/share/fonts/truetype/arphic/uming.ttc",
            "/System/Library/Fonts/PingFang.ttc",
            "C:/Windows/Fonts/simhei.ttf",
        ]
        for fp in candidates:
            if os.path.exists(fp):
                return font_manager.FontProperties(fname=fp, size=size)
    except Exception:
        pass
    return None



def _v16_text_short(x, max_chars=16):
    """V16/V19表格文本截断兜底。"""
    try:
        s = "" if x is None else str(x)
    except Exception:
        s = ""
    s = s.replace("\n", " ").replace("\r", " ").strip()
    try:
        max_chars = int(max_chars)
    except Exception:
        max_chars = 16
    if max_chars <= 0:
        return s
    return s if len(s) <= max_chars else s[:max_chars-1] + "…"


def _v16_render_table_png(title, columns, rows, output_path, max_col_chars=None):
    """生成Telegram真正表格图片。若matplotlib不可用则写CSV兜底。"""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.table import Table
        font_prop = _v16_load_font(12)
        title_font = _v16_load_font(18)
        max_col_chars = max_col_chars or [16] * len(columns)
        clean_rows = []
        for row in rows:
            clean_rows.append([_v16_text_short(cell, max_col_chars[i] if i < len(max_col_chars) else 16) for i, cell in enumerate(row)])
        nrows = len(clean_rows) + 1
        fig_h = max(2.6, 0.42 * nrows + 1.2)
        fig_w = 15.5
        fig, ax = plt.subplots(figsize=(fig_w, fig_h), dpi=180)
        ax.set_axis_off()
        if title_font:
            ax.set_title(title, fontproperties=title_font, fontsize=18, pad=14)
        else:
            ax.set_title(title, fontsize=18, pad=14)
        table = ax.table(cellText=clean_rows, colLabels=columns, loc="center", cellLoc="left")
        table.auto_set_font_size(False)
        table.set_fontsize(10)
        table.scale(1, 1.35)
        for (r, c), cell in table.get_celld().items():
            cell.set_linewidth(0.6)
            if r == 0:
                cell.set_text_props(weight="bold")
                cell.set_facecolor("#e9eef6")
            elif r % 2 == 0:
                cell.set_facecolor("#f8f9fb")
            if font_prop:
                cell.get_text().set_fontproperties(font_prop)
        plt.tight_layout()
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        fig.savefig(output_path, bbox_inches="tight")
        plt.close(fig)
        return output_path
    except Exception as e:
        print(f"V16表格图片生成失败，改写CSV：{output_path} error={e}")
        csv_path = str(output_path).rsplit(".", 1)[0] + ".csv"
        try:
            import csv
            os.makedirs(os.path.dirname(csv_path) or ".", exist_ok=True)
            with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow(columns)
                writer.writerows(rows)
            return csv_path
        except Exception:
            return ""


def _v16_signal_dimensions(s):
    try:
        return json.loads(s.get("v16_dimensions_json", "[]"))
    except Exception:
        return []




def build_confirm_condition(s):
    try:
        direct = str(s.get("confirm_rule", "") or "").strip()
        if direct:
            return direct
        close = safe_float(s.get("close", 0))
        pressure_upper = safe_float(s.get("xhu_pressure_union_upper", s.get("xhu_final_union_upper", 0)))
        structure_key = safe_float(s.get("structure_key_level", 0))
        defense = safe_float(s.get("defense_level", s.get("real_defense_level", 0)))
        grade = str(s.get("xhu_pressure_model_grade", "") or "")
        parts = []
        if pressure_upper > 0:
            parts.append(f"放量站稳最终压力上沿{pressure_upper:.2f}，最好实体大半在压力带上方")
        elif structure_key > 0:
            parts.append(f"站稳关键结构位{structure_key:.2f}，回踩不有效跌破")
        elif defense > 0:
            parts.append(f"守住交易防守区{defense:.2f}附近，回踩缩量后重新转强")
        else:
            parts.append("次日不追高，等待放量确认或回踩关键位不破后再看")
        if grade:
            parts.append(f"压力带模型维持{grade}级或继续升级")
        if close > 0:
            parts.append("收盘位置保持强势，避免长上影放量滞涨")
        return "；".join(parts)
    except Exception:
        return "等待放量确认、回踩关键位不破或重新转强；不满足则不追。"


def build_giveup_condition(s):
    try:
        direct = str(s.get("abandon_rule", "") or "").strip()
        if direct:
            return direct
        defense = safe_float(s.get("defense_level", s.get("real_defense_level", 0)))
        structure_key = safe_float(s.get("structure_key_level", 0))
        pressure_upper = safe_float(s.get("xhu_pressure_union_upper", s.get("xhu_final_union_upper", 0)))
        parts = []
        if defense > 0:
            parts.append(f"有效跌破交易防守位{defense:.2f}")
        elif structure_key > 0:
            parts.append(f"跌回关键结构位{structure_key:.2f}下方且收不回")
        elif pressure_upper > 0:
            parts.append(f"突破失败并跌回压力上沿{pressure_upper:.2f}下方")
        else:
            parts.append("放量长阴、冲高回落或跌破短线承接位")
        parts.append("若次日放量滞涨、长上影、跌破BBI/MA5且无修复，放弃")
        return "；".join(parts)
    except Exception:
        return "跌破关键位、放量长阴或冲高回落不修复则放弃。"

def render_v16_summary_table_png(final_signals, output_path="telegram_tables/v16_summary.png"):
    rows = []
    for i, s in enumerate(final_signals, 1):
        s = attach_data_quality_to_row(dict(s or {}))
        rows.append([
            i,
            f"{s.get('name','')}({s.get('code','')})",
            s.get("v16_final_grade", ""),
            f"{safe_float(s.get('v16_final_score', 0)):.1f}",
            _v16_text_short(s.get("v16_main_signal", ""), 28),
            _v16_text_short(s.get("v16_cap_reason", ""), 24),
            _v16_text_short(build_confirm_condition(s), 30),
            _v16_text_short(build_giveup_condition(s), 30),
        ])
    return _v16_render_table_png(
        "一号员工选股模型 V16 今日三选总览",
        ["序号", "股票", "等级", "总分", "主导信号", "封顶/风险", "确认条件", "放弃条件"],
        rows,
        output_path,
        [4, 20, 5, 6, 30, 28, 34, 34],
    )


def render_v16_dimension_table_png(signal, output_path):
    dims = _v16_signal_dimensions(signal)
    rows = []
    for d in dims:
        rows.append([d.get("name", ""), d.get("score", ""), f"{float(d.get('weight',0))*100:.0f}%", d.get("reason", "")])
    title = f"{signal.get('name','')}({signal.get('code','')}) V16 20维评分｜{signal.get('v16_final_grade','')} {safe_float(signal.get('v16_final_score',0)):.1f}"
    return _v16_render_table_png(title, ["维度", "分数", "权重", "原因"], rows, output_path, [14, 6, 6, 44])


def send_telegram_photo(image_path, caption=""):
    if not image_path or not os.path.exists(image_path):
        return False
    if ENABLE_TELEGRAM != "1":
        print(f"[Telegram图片未发送: ENABLE_TELEGRAM={ENABLE_TELEGRAM}] {image_path} {caption}")
        return False
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print(f"[Telegram图片发送失败: TOKEN/CHAT_ID为空] {image_path}")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
    try:
        with open(image_path, "rb") as f:
            files = {"photo": f}
            data = {"chat_id": TELEGRAM_CHAT_ID, "caption": caption[:1000]}
            resp = requests.post(url, data=data, files=files, timeout=45)
        if resp.status_code == 200:
            print(f"Telegram图片发送成功：{image_path}")
            return True
        print(f"Telegram图片发送失败：status={resp.status_code} body={resp.text[:200]}")
        return False
    except Exception as e:
        print(f"Telegram图片发送异常：{image_path} error={e}")
        return False


_ORIGINAL_SEND_TELEGRAM = send_telegram


def send_telegram(message):
    ok = globals().get('_ORIGINAL_SEND_TELEGRAM')(message) if globals().get('_ORIGINAL_SEND_TELEGRAM') else False
    global TELEGRAM_PENDING_IMAGES
    pending_images = globals().get('TELEGRAM_PENDING_IMAGES', []) or []
    if pending_images:
        for img, cap in pending_images:
            send_telegram_photo(img, cap)
        TELEGRAM_PENDING_IMAGES = []
    return ok


def send_midrun_telegram(message, reason=""):
    """
    V26.2.1：中途推送保护。
    日常生产只允许最终正式报告调用 send_telegram；运行中异常/诊断/空样本分支
    只打印日志，不主动推送Telegram，避免未完成深度评分就误发报告。
    如需临时恢复旧行为，可设置 SUPPRESS_MIDRUN_TELEGRAM=0。
    """
    if str(globals().get("SUPPRESS_MIDRUN_TELEGRAM", "1")) == "1":
        print(f"[中途Telegram已抑制] reason={reason}")
        try:
            print(message)
        except Exception:
            pass
        return False
    return send_telegram(message)



def v14_diagnostics_text(diags, limit=5):
    """V14/V19诊断文本兜底，避免最终报告阶段因旧函数缺失失败。"""
    if not diags:
        return ""
    lines = []
    for i, d in enumerate(diags[:limit], 1):
        if isinstance(d, dict):
            code = d.get("code") or d.get("symbol") or d.get("股票代码") or ""
            name = d.get("name") or d.get("股票名称") or ""
            reason = (
                d.get("reason")
                or d.get("diagnosis")
                or d.get("v14_block_reason")
                or d.get("v19_note")
                or d.get("note")
                or ""
            )
            score = d.get("v16_final_score", d.get("v14_final_score", d.get("total_score", "")))
            lines.append(f"{i}. {code} {name} | 分数={score} | {reason}".strip())
        else:
            lines.append(f"{i}. {str(d)}")
    return "\n".join(lines)



def _v19_first_float(row, *keys, default=0.0):
    """按多个候选字段取第一个有效价格。"""
    for k in keys:
        try:
            if isinstance(row, dict) and k in row:
                v = safe_float(row.get(k), None)
                if v is not None and v > 0:
                    return float(v)
        except Exception:
            pass
    return float(default or 0.0)


def _v19_tick(price):
    """A股报告展示用最小报价步长。"""
    return 0.01


def _v19_fmt_price(price):
    try:
        price = float(price)
        if price <= 0:
            return "--"
        return f"{price:.2f}"
    except Exception:
        return "--"


def _v19_fmt_range(low, high):
    try:
        low = float(low); high = float(high)
        if low <= 0 or high <= 0:
            return "--"
        if high < low:
            low, high = high, low
        return f"{low:.2f}-{high:.2f}"
    except Exception:
        return "--"


def build_v19_price_plan(s):
    """
    V19.3 报告层价格计划。
    目标：Telegram不再输出“回踩关键位/跌破支撑”这种模糊话，而是给出可执行价格。
    说明：这是模型计划价，用于三号员工/人工复核，不代表无条件下单。
    """
    close = _v19_first_float(s, "close", "收盘", "last_close")
    prev_close = _v19_first_float(s, "prev_close", "pre_close", default=close)
    core_low = _v19_first_float(s, "xhu_pressure_core_lower", "core_pressure_lower", "pressure_core_lower")
    core_up = _v19_first_float(s, "xhu_pressure_core_upper", "core_pressure_upper", "pressure_core_upper")
    final_up = _v19_first_float(s, "xhu_effective_confirm_price", "xhu_pressure_union_upper", "xhu_final_union_upper", "final_pressure_upper", "pressure_final_upper")
    resonance_line = _v19_first_float(s, "xhu_resonance_core_line", "resonance_core_line", "main_resonance_line")
    structure_key = _v19_first_float(s, "structure_key_level", "key_level", "neckline", "platform_upper")
    support = _v19_first_float(s, "support_cluster", "key_support", "platform_support")
    defense = _v19_first_float(s, "defense_level", "real_defense_level", "defensive_price", "trade_defense")

    # 主锚点优先级：V19.4共振供需锚点 > 供需压力下沿 > 结构位 > 支撑 > 收盘。
    # 共振供需锚点用于避免把右侧近端小压力误当最终压力。
    anchor = resonance_line or core_low or structure_key or support or defense or close
    if anchor <= 0:
        anchor = close or 1.0

    if resonance_line > 0:
        # 围绕共振供需锚点生成主压力带；影线/密集区扩展上沿，收盘/实体共振锁定供需锚点。
        core_low = min(core_low, resonance_line * 0.996) if core_low > 0 else resonance_line * 0.996
        core_up = max(core_up, resonance_line * 1.006) if core_up > 0 else resonance_line * 1.006
        final_up = max(final_up, core_up, resonance_line * 1.006) if final_up > 0 else core_up

    if core_up <= 0:
        core_up = final_up if final_up > 0 else anchor * 1.035
    if final_up <= 0:
        final_up = core_up if core_up > 0 else anchor * 1.05

    # 如果防守位缺失，使用锚点下方缓冲估算，避免报告空缺。
    if defense <= 0:
        defense = support if support > 0 else anchor * 0.982
    short_defense = defense
    hard_stop = min(short_defense * 0.988, anchor * 0.972)

    # 买入价体系：以锚点/压力带下沿/结构位为核心。
    aggressive_low = anchor
    aggressive_high = min(anchor * 1.008, core_up if core_up > anchor else anchor * 1.012)
    standard_low = max(hard_stop * 1.012, anchor * 0.990)
    standard_high = anchor
    comfort_low = max(hard_stop * 1.003, anchor * 0.970)
    comfort_high = max(comfort_low, anchor * 0.990)

    # 突破与追高线。
    breakout_confirm = final_up + _v19_tick(final_up)
    add_confirm = final_up
    no_chase = max(core_up + _v19_tick(core_up), final_up * 0.994)

    # 目标价：第一目标优先供需压力上沿，第二目标最终压力上沿。
    target1 = core_up if core_up > 0 else anchor * 1.03
    target2 = final_up if final_up > target1 else target1 * 1.04
    target3 = target2 * 1.05

    # 高开不追阈值。
    gap_no_chase_pct = 3.0
    gap_no_chase_price = prev_close * (1 + gap_no_chase_pct / 100.0) if prev_close > 0 else no_chase

    plan = {
        "anchor": anchor,
        "aggressive_buy_low": aggressive_low,
        "aggressive_buy_high": aggressive_high,
        "standard_buy_low": standard_low,
        "standard_buy_high": standard_high,
        "comfortable_buy_low": comfort_low,
        "comfortable_buy_high": comfort_high,
        "breakout_confirm_price": breakout_confirm,
        "add_confirm_price": add_confirm,
        "no_chase_price": no_chase,
        "short_defense_price": short_defense,
        "hard_stop_price": hard_stop,
        "giveup_price": hard_stop,
        "target1_price": target1,
        "target2_price": target2,
        "target3_price": target3,
        "gap_no_chase_price": gap_no_chase_price,
        "gap_no_chase_pct": gap_no_chase_pct,
        "core_pressure_lower": core_low,
        "core_pressure_upper": core_up,
        "final_pressure_upper": final_up,
        "resonance_core_line": resonance_line,
        "resonance_core_desc": str(s.get("xhu_resonance_core_desc", s.get("resonance_core_desc", "")) or ""),
    }
    return plan


def v19_price_plan_lines(s, html_mode=False):
    p = build_v19_price_plan(s)
    esc = html.escape if html_mode else (lambda x: x)
    lines = []
    lines.append("【价格计划】")
    if safe_float(p.get("resonance_core_line", 0)) > 0:
        desc = str(p.get("resonance_core_desc", "") or "共振供需锚点")
        lines.append(f"共振供需锚点：{_v19_fmt_price(p['resonance_core_line'])}（{desc[:45]}）")
    lines.append(f"建议标准买入价：{_v19_fmt_range(p['standard_buy_low'], p['standard_buy_high'])}")
    lines.append(f"激进买入价：{_v19_fmt_range(p['aggressive_buy_low'], p['aggressive_buy_high'])}（只适合轻仓试探）")
    lines.append(f"舒服低吸价：{_v19_fmt_range(p['comfortable_buy_low'], p['comfortable_buy_high'])}")
    lines.append(f"突破确认价：{_v19_fmt_price(p['breakout_confirm_price'])} 上方放量站稳")
    lines.append(f"加仓确认价：突破后回踩 {_v19_fmt_price(p['add_confirm_price'])} 不破再转强")
    lines.append(f"不追价格：{_v19_fmt_price(p['no_chase_price'])} 以上不追")
    lines.append(f"短线防守价：{_v19_fmt_price(p['short_defense_price'])}")
    lines.append(f"硬止损价：{_v19_fmt_price(p['hard_stop_price'])}")
    lines.append(f"放弃价格：有效跌破 {_v19_fmt_price(p['giveup_price'])} 直接放弃")
    lines.append(f"第一目标价：{_v19_fmt_price(p['target1_price'])}")
    lines.append(f"第二目标价：{_v19_fmt_price(p['target2_price'])}")
    lines.append(f"高开处理：若开到 {_v19_fmt_price(p['gap_no_chase_price'])} 附近或高开超过{p['gap_no_chase_pct']:.0f}%，不追，等回踩确认")
    return [esc(x) for x in lines]



# ========================= V19.4.1 数据质量提示模块 =========================
# 只做报告层小优化：如果Top3来自B档/Q档数据，在Telegram与score_cards里提示。
# 不改变选股评分、不改变候选排序、不改变压力带/价格计划逻辑。

_DATA_QUALITY_CACHE = None

def _normalize_stock_code_for_quality(x):
    s = str(x or "").strip()
    if not s:
        return ""
    s = s.replace("sh.", "").replace("sz.", "").replace("bj.", "").replace("SH.", "").replace("SZ.", "").replace("BJ.", "")
    return s.zfill(6) if s.isdigit() and len(s) <= 6 else s

def _load_data_quality_map():
    global _DATA_QUALITY_CACHE
    if _DATA_QUALITY_CACHE is not None:
        return _DATA_QUALITY_CACHE
    mp = {}
    try:
        import glob
        # 优先读模型可用股票池，其次读缓存验收明细。
        files = []
        files.extend(sorted(glob.glob("outputs/model_usable_universe_*.csv"), reverse=True))
        files.extend(sorted(glob.glob("outputs/cache_acceptance_report_*.csv"), reverse=True))
        for path in files[:4]:
            try:
                dfq = pd.read_csv(path)
            except Exception:
                continue
            if dfq is None or dfq.empty:
                continue
            cols = {str(c).lower(): c for c in dfq.columns}
            code_col = None
            for k in ["code", "symbol", "股票代码", "代码"]:
                if k.lower() in cols:
                    code_col = cols[k.lower()]
                    break
            if code_col is None:
                # 兜底：找包含code/symbol的列
                for c in dfq.columns:
                    lc = str(c).lower()
                    if "code" in lc or "symbol" in lc or "代码" in str(c):
                        code_col = c
                        break
            if code_col is None:
                continue

            # 质量档位列可能命名不同，尽量兼容。
            tier_col = None
            for k in ["quality", "quality_tier", "data_quality", "acceptance_tier", "grade", "tier", "验收档位", "质量档位", "档位"]:
                if k.lower() in cols:
                    tier_col = cols[k.lower()]
                    break
            if tier_col is None:
                for c in dfq.columns:
                    name = str(c)
                    if ("档" in name) or ("tier" in name.lower()) or ("quality" in name.lower()) or ("grade" in name.lower()):
                        tier_col = c
                        break

            reason_col = None
            for k in ["reason", "quality_reason", "acceptance_reason", "note", "备注", "原因", "说明"]:
                if k.lower() in cols:
                    reason_col = cols[k.lower()]
                    break

            for _, rr in dfq.iterrows():
                cd = _normalize_stock_code_for_quality(rr.get(code_col, ""))
                if not cd:
                    continue
                tier = str(rr.get(tier_col, "")).strip().upper() if tier_col is not None else ""
                reason = str(rr.get(reason_col, "")).strip() if reason_col is not None else ""
                # 兼容布尔列/文本列里出现A/B/Q
                if not tier:
                    row_text = " ".join(str(rr.get(c, "")) for c in dfq.columns[:12]).upper()
                    if "Q" in row_text and ("复权" in row_text or "Q" in row_text):
                        tier = "Q"
                    elif "B" in row_text:
                        tier = "B"
                    elif "A" in row_text:
                        tier = "A"
                if tier:
                    # 不覆盖已经读到的更明确档位
                    mp.setdefault(cd, {"tier": tier[:8], "reason": reason[:80], "source": path})
    except Exception:
        pass
    _DATA_QUALITY_CACHE = mp
    return mp

def attach_data_quality_to_row(row):
    """给候选行附加数据质量字段。只影响报告提示，不影响打分排序。"""
    try:
        cd = _normalize_stock_code_for_quality(row.get("code") or row.get("symbol") or row.get("股票代码"))
        mp = _load_data_quality_map()
        info = mp.get(cd, {})
        tier = str(row.get("data_quality_tier") or row.get("quality_tier") or info.get("tier", "") or "").strip().upper()
        reason = str(row.get("data_quality_reason") or info.get("reason", "") or "").strip()
        if tier:
            row["data_quality_tier"] = tier
            row["data_quality_reason"] = reason
            if tier.startswith("B"):
                row["data_quality_note"] = "数据质量：B档，通过但需注意新鲜度/停牌/复权状态。"
            elif tier.startswith("Q"):
                row["data_quality_note"] = "数据质量：Q档，前复权/裁剪后通过，需额外留意复权与K线连续性。"
            elif tier.startswith("A"):
                row["data_quality_note"] = "数据质量：A档。"
        return row
    except Exception:
        return row

def data_quality_report_line(row):
    note = str(row.get("data_quality_note", "") or "").strip()
    if note and not note.endswith("。"):
        note += "。"
    return note

# ======================= V19.4.1 数据质量提示模块 END =======================



def _human_trade_tier(tier):
    """报告展示用：把后台分层压缩成人能直接看懂的操作等级。只影响报告，不影响评分/排序。"""
    t = str(tier or "").strip()
    if t.startswith("A"):
        return "A确认"
    if t.startswith("B+"):
        return "B+观察"
    if t.startswith("B"):
        return "B观察"
    if t.startswith("C"):
        return "C观察"
    if "硬风险" in t or "剔除" in t:
        return "硬风险剔除"
    return t or "未分层"


def _human_trade_warning(tier):
    """报告展示用：每只票标题下方必须第一眼提示是否能开盘直接买。"""
    t = str(tier or "").strip()
    if t.startswith("A"):
        return "可以重点盯盘，但仍要按计划价执行；高开太多或冲高回落不追。"
    if t.startswith("B+"):
        return "目前只是观察票，不是开盘直接买入票；不回踩、不确认、不追。"
    if t.startswith("B"):
        return "目前只是观察票，不是开盘直接买入票；等回踩或突破确认。"
    if t.startswith("C"):
        return "目前只做后台跟踪，不具备直接交易条件。"
    if "硬风险" in t or "剔除" in t:
        return "命中硬风险或硬约束，不作为正式交易候选。"
    return "按确认条件执行，不满足条件不追。"


def _report_base_score(row):
    """基础评分：全市场海选/旧主模型底座分。只用于报告展示。"""
    return safe_float(row.get('v16_final_score', row.get('v14_original_total_score', row.get('v14_final_score', row.get('total_score', 0)))))


def _report_deep_score(row):
    """深度评分：进入深度分析后的质量分。只用于报告展示。"""
    return safe_float(row.get('v20_final_score', row.get('v201_score', row.get('v212_final_score', 0))))


def _valid_score_field(row, key):
    """报告/排序共用：判断某个评分字段是否真实生成，避免综合分静默回退深度分。"""
    try:
        if key not in row:
            return False
        v = row.get(key)
        if v is None or v == "":
            return False
        return float(v) == float(v)
    except Exception:
        return False


def _report_composite_score(row):
    """综合评分：只展示真实生成的融合交易分；不再静默回退深度评分。"""
    if _valid_score_field(row, 'v22_composite_trade_score'):
        return safe_float(row.get('v22_composite_trade_score'))
    if _valid_score_field(row, 'v212_final_score'):
        return safe_float(row.get('v212_final_score'))
    return None


def _report_composite_score_source(row):
    if _valid_score_field(row, 'v22_composite_trade_score'):
        return 'V22融合交易分'
    if _valid_score_field(row, 'v212_final_score'):
        return 'V21.2交易机会分'
    return '未独立生成'

def build_message(final_signals, dates, stock_count=0, kline_success=0, kline_fail=0, deep_count=0, v14_diagnostics=None, lifecycle_tracking=None):
    global TELEGRAM_PENDING_IMAGES
    TELEGRAM_PENDING_IMAGES = []
    lines = []
    lines.append(f"📊 <b>{html.escape(MODEL_VERSION)}</b>")
    lines.append(f"🗓 使用K线日期：{', '.join(dates) if dates else '未知'}")
    lines.append(f"⏱ 运行时间：{bj_time_str()} 北京时间")
    lines.extend(build_data_gate_header_lines())
    lines.append(f"股票池：{stock_count}只 | K线成功：{kline_success}只 | 失败：{kline_fail}只 | 深度评分：{deep_count}只")
    lines.append(f"今日输出：<b>{len(final_signals)}</b>只，固定目标{V20_FIXED_TOP_N}只；包含观察级，不等于全部可直接买入。")
    if final_signals:
        _tier_stat = {}
        for _s in final_signals:
            _t = str(_s.get("v20_trade_tier", "未分层"))
            _tier_stat[_t] = _tier_stat.get(_t, 0) + 1
        lines.append(f"等级分布：{html.escape(str(_tier_stat))}")
    lines.append("口径：V25.6保留历史有效底座；在V25.5基础上把旧压力带主入口细化为“供需压力带双线模型”。供需锚点负责主供应/主需求边界与吃肉空间，共振锚点负责结构强弱中枢；大级别定线，中周期校准，日线只负责高级突破与回踩执行。")
    lines.append("说明：综合评分用于最终排序，括号里的基础评分/深度评分用于说明分数来源；分数不代表无脑买入，次日必须按确认条件和放弃条件执行。")
    lines.append("━━━━━━━━━━━━━━")

    if not final_signals:
        lines.append("⚠️ 今日没有可用Top3股票。通常代表深度评分为空或全部命中硬雷区/硬约束。")
        diag = v14_diagnostics_text(v14_diagnostics or [], 10)
        if diag:
            lines.append(html.escape(diag))
        return "\n".join(lines)

    # V19.4报告层默认不再发送旧V16图片表格：
    # 1）GitHub Actions缺中文字体时图片会乱码；2）旧表格字段与V19价格计划不匹配。
    # 如需临时开启旧PNG，可在workflow里设置 SEND_V16_PNG_TABLES=1。
    if os.environ.get("SEND_V16_PNG_TABLES", "0") == "1":
        try:
            summary_img = render_v16_summary_table_png(final_signals)
            if summary_img:
                TELEGRAM_PENDING_IMAGES.append((summary_img, "一号员工选股模型V19.3：今日固定Top3总览表"))
            for i, s in enumerate(final_signals, 1):
                img = render_v16_dimension_table_png(s, f"telegram_tables/v19_3_{i}_{s.get('code','')}_20d.png")
                if img:
                    TELEGRAM_PENDING_IMAGES.append((img, f"{i}. {s.get('name','')}({s.get('code','')}) 20维评分表"))
        except Exception as e:
            print(f"V19.3报告表格生成失败：{e}")

    for i, s in enumerate(final_signals, 1):
        lines.append(f"<b>{i}. {html.escape(str(s.get('name','')))}({html.escape(str(s.get('code','')))})</b>")
        _tier_raw = str(s.get('v20_trade_tier', '未分层'))
        _tier_show = _human_trade_tier(_tier_raw)
        _warn = _human_trade_warning(_tier_raw)
        _close_show = safe_float(s.get('v20_close', s.get('close', s.get('收盘', 0))))
        _date_show = str(s.get('date', '') or s.get('日期', '') or '').strip()
        if _close_show > 0:
            if _date_show:
                lines.append(f"当前收盘价：<b>{_close_show:.2f}</b>（截至{html.escape(_date_show)}收盘）")
            else:
                lines.append(f"当前收盘价：<b>{_close_show:.2f}</b>")
        _composite_score = _report_composite_score(s)
        _composite_source = _report_composite_score_source(s)
        _base_score = _report_base_score(s)
        _deep_score = _report_deep_score(s)
        if _composite_score is None:
            lines.append(f"操作等级：<b>{html.escape(_tier_show)}</b>｜深度评分<b>{_deep_score:.1f}</b>（基础评分{_base_score:.1f}，综合评分未独立生成）｜{html.escape(_warn)}")
        else:
            lines.append(f"操作等级：<b>{html.escape(_tier_show)}</b>｜综合评分<b>{_composite_score:.1f}</b>（基础评分{_base_score:.1f}，深度评分{_deep_score:.1f}，口径={html.escape(_composite_source)}）｜{html.escape(_warn)}")
        lines.append(f"入选/降级原因：{html.escape(str(s.get('v20_tier_reason','')))}")
        lines.append(f"V24.1实盘风控：流动性{html.escape(str(s.get('v241_liquidity_tier','')))}｜成交额{safe_float(s.get('v241_amount_effective',0))/100000000:.2f}亿｜仓位建议{html.escape(str(s.get('v241_position_text','')))}｜{html.escape(str(s.get('v241_position_reason',''))[:90])}")
        if safe_float(s.get('v201_precise_trigger_line',0)) > 0:
            _precise_status = "已计算" if bool(s.get('v201_precise_trigger_valid', False)) else "待日线平台精算"
            lines.append(f"日线精确触发线：{safe_float(s.get('v201_precise_trigger_line',0)):.2f}（{_precise_status}）")
        lines.append(f"主交易假设：{html.escape(str(s.get('v20_main_hypothesis','综合结构机会')))}")
        if safe_float(s.get('score_bottom_reversal_pattern', s.get('bottom_pattern_score', 0))) > 0 or str(s.get('bottom_pattern_type','')).strip():
            _bp_type = str(s.get('bottom_pattern_type','底部反转结构'))
            _bp_score = safe_float(s.get('score_bottom_reversal_pattern', s.get('bottom_pattern_score', 0)))
            _bp_neck = safe_float(s.get('bottom_pattern_neckline',0))
            _bp_confirm = '已确认' if bool(s.get('bottom_pattern_confirmed', False)) else '观察中'
            lines.append(f"底部反转形态：{html.escape(_bp_type)}｜分{_bp_score:.1f}｜颈线{_bp_neck:.2f}｜{_bp_confirm}")
            if str(s.get('bottom_pattern_desc','')).strip():
                lines.append(f"形态说明：{html.escape(str(s.get('bottom_pattern_desc',''))[:120])}")
        if str(s.get('v20_condition_probability_hint','')).strip():
            lines.append(f"条件概率参考：{html.escape(str(s.get('v20_condition_probability_hint','')))}")
        lines.append(f"RR/防守：RR={safe_float(s.get('v20_rr', s.get('risk_reward_ratio', s.get('rr', 0)))):.2f}；防守={safe_float(s.get('v20_defense', s.get('defensive_price', s.get('trade_defense', 0)))):.2f}；防守距离={safe_float(s.get('v20_defense_dist',0)):.1%}")
        main_signal = str(s.get('v16_main_signal','') or s.get('v19_main_signal','') or s.get('v20_main_hypothesis','V20综合机会'))
        lines.append(f"主导逻辑：{html.escape(main_signal)}")
        lines.append(f"供需压力带：{safe_float(s.get('xhu_pressure_core_lower',0)):.2f}-{safe_float(s.get('xhu_pressure_core_upper',0)):.2f}；最终压力上沿：{safe_float(s.get('xhu_pressure_union_upper', s.get('xhu_final_union_upper',0))):.2f}；压力带等级：{html.escape(str(s.get('xhu_pressure_model_grade','')))}")
        dq_line = data_quality_report_line(s)
        if dq_line and ("B档" in dq_line or "Q档" in dq_line):
            lines.append(html.escape(dq_line))
        for pl in v19_price_plan_lines(s, html_mode=True):
            lines.append(pl)
        lines.append(f"模型确认：{html.escape(build_confirm_condition(s))}")
        lines.append(f"模型放弃：{html.escape(build_giveup_condition(s))}")
        lines.append(f"主要封顶/风险：{html.escape(str(s.get('v16_cap_reason','无硬封顶')))}")
        lines.append("—")

    if lifecycle_tracking:
        lines.append("【近期推荐跟踪】")
        for j, tr in enumerate((lifecycle_tracking or [])[:8], 1):
            lines.append(
                f"{j}. {html.escape(str(tr.get('name','')))}({html.escape(str(tr.get('code','')))}) "
                f"| {html.escape(str(tr.get('lifecycle_status','')))} | T+{tr.get('t_window','?')} | "
                f"收益{safe_float(tr.get('return_since_signal',0)):.1%} | "
                f"说明：{html.escape(str(tr.get('lifecycle_reason',''))[:80])}"
            )
            lines.append(
                f"   处理：{html.escape(str(tr.get('lifecycle_action',''))[:90])}；"
                f"放弃：{html.escape(str(tr.get('giveup_condition',''))[:80])}"
            )
        lines.append("—")

    diag = v14_diagnostics_text(v14_diagnostics or [], 5)
    if diag:
        lines.append("落选/拦截诊断：")
        lines.append(html.escape(diag))
    return "\n".join(lines)


def _v19_compact_row(r, pool=""):
    """把候选压缩成复盘友好的score card，避免后续从Telegram文本反推。"""
    return {
        "date": r.get("date", ""),
        "code": r.get("code", ""),
        "name": r.get("name", ""),
        "pool": pool or r.get("v19_pool", ""),
        "v19_rank": r.get("v19_rank", ""),
        "v19_note": r.get("v19_note", ""),
        "v16_final_score": safe_float(r.get("v16_final_score", r.get("v14_final_score", r.get("total_score", 0)))),
        "v16_final_grade": r.get("v16_final_grade", ""),
        "v16_main_signal": r.get("v16_main_signal", ""),
        "v16_cap_reason": r.get("v16_cap_reason", ""),
        "v14_level": r.get("v14_level", ""),
        "v14_blocked": bool(r.get("v14_blocked", False)),
        "v14_block_reason": r.get("v14_block_reason", ""),
        "skip_reason": r.get("v14_skip_reason", ""),
        "total_score": safe_float(r.get("total_score", 0)),
        "trade_priority_score": safe_float(r.get("trade_priority_score", 0)),
        "score_trade_quality": safe_float(r.get("score_trade_quality", 0)),
        "score_v12_pullback_entry": safe_float(r.get("score_v12_pullback_entry", 0)),
        "rr": safe_float(r.get("risk_reward_ratio", r.get("rr", 0))),
        "close": safe_float(r.get("close", r.get("收盘", 0))),
        "defensive_price": safe_float(r.get("defensive_price", r.get("trade_defense", 0))),
        "core_pressure_upper": safe_float(r.get("xhu_pressure_core_upper", 0)),
        "final_pressure_upper": safe_float(r.get("xhu_pressure_union_upper", r.get("xhu_final_union_upper", 0))),
        "xhu_resonance_core_line": safe_float(r.get("xhu_resonance_core_line", r.get("resonance_core_line", 0))),
        "xhu_resonance_core_desc": r.get("xhu_resonance_core_desc", r.get("resonance_core_desc", "")),
        "xhu_effective_confirm_price": safe_float(r.get("xhu_effective_confirm_price", r.get("effective_confirm_price", 0))),
        "xhu_pressure_merge_gap_pct": safe_float(r.get("xhu_pressure_merge_gap_pct", r.get("pressure_merge_gap_pct", 0))),
        "confirm_condition": build_confirm_condition(r),
        "giveup_condition": build_giveup_condition(r),
        "price_plan": build_v19_price_plan(r),
        "generated_at_bj": bj_time_str(),
    }


def save_v19_1_outputs(final_signals, diagnostics, audited_rows, dates=None, meta=None):
    """保存V19.1每日Top3评分卡与复盘归因底座文件。"""
    try:
        meta = meta or {}
        dates = dates or []
        selected_codes = {str(x.get("code")) for x in final_signals}
        watch_rows = []
        blocked_rows = []
        for r in diagnostics or []:
            if str(r.get("code")) in selected_codes:
                continue
            if r.get("v14_blocked") or r.get("v19_pool") == "硬风险剔除":
                blocked_rows.append(_v19_compact_row(r, "硬风险剔除"))
            else:
                watch_rows.append(_v19_compact_row(r, "后台跟踪"))

        payload = {
            "model_version": "V19.3固定Top3+价格计划+候选池复盘归因底座",
            "generated_at_bj": bj_time_str(),
            "dates": dates,
            "meta": meta,
            "rule": {
                "fixed_top_n": V19_FIXED_TOP_N,
                "score_threshold_hard_gate": False,
                "old_80_score_line_removed": True,
                "pressure_breakout_required": False,
                "hard_risk_still_blocked": True,
            },
            "final_top3": [_v19_compact_row(r, "正式推荐Top3") for r in final_signals],
            "watch_pool": watch_rows[:80],
            "blocked_pool": blocked_rows[:50],
            "audit_count": len(audited_rows or []),
        }
        with open(V19_SCORE_CARDS_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f"V19.3评分卡已保存：{V19_SCORE_CARDS_FILE}")

        lines = []
        lines.append("一号员工 V19.3 今日固定Top3价格计划报告")
        lines.append(f"生成时间：{bj_time_str()} 北京时间")
        lines.append(f"排查日期：{', '.join(dates) if dates else '未知'}")
        lines.append(f"固定推送数量：{V19_FIXED_TOP_N}；实际输出：{len(final_signals)}")
        lines.append("口径：不再用80分硬门槛；压力带突破/倍量/回踩确认等均为评分项；硬雷区仍剔除。")
        lines.append("")
        if final_signals:
            lines.append("【今日Top3】")
            for i, r in enumerate(final_signals, 1):
                row = _v19_compact_row(r, "正式推荐Top3")
                lines.append(f"{i}. {row['name']}({row['code']}) | 分数 {row['v16_final_score']:.2f} | 等级 {row['v16_final_grade']} | 主导 {row['v16_main_signal']}")
                for pl in v19_price_plan_lines(r, html_mode=False):
                    lines.append(f"   {pl}")
                if row.get("v19_note"):
                    lines.append(f"   提示：{row['v19_note']}")
                lines.append(f"   模型确认：{row['confirm_condition']}")
                lines.append(f"   模型放弃：{row['giveup_condition']}")
                if row.get("v16_cap_reason"):
                    lines.append(f"   风险/封顶：{row['v16_cap_reason']}")
        else:
            lines.append("今日没有可用Top3：深度评分为空或全部命中硬雷区/硬约束。")
        lines.append("")
        lines.append("【后台跟踪前10】")
        for i, row in enumerate(watch_rows[:10], 1):
            lines.append(f"{i}. {row['name']}({row['code']}) | 分数 {row['v16_final_score']:.2f} | 原因 {row.get('skip_reason','')}")
        lines.append("")
        lines.append("【复盘归因预留】")
        lines.append("后续每日可按T+1/T+3/T+5/T+8/T+13/T+20读取本文件，对final_top3和watch_pool做路径归因、指标定义反查、阈值/权重/同源重复审计。")
        with open(V19_DAILY_REPORT_FILE, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        print(f"V19.3日报已保存：{V19_DAILY_REPORT_FILE}")

        review_lines = [
            "一号员工 V19.3 复盘归因报告占位",
            f"生成时间：{bj_time_str()} 北京时间",
            "当前版本已保存每日Top3与后台跟踪池；下一步接入历史score_cards后，可生成T+1/T+3/T+5/T+8/T+13/T+20复盘。",
        ]
        with open(V19_REVIEW_REPORT_FILE, "w", encoding="utf-8") as f:
            f.write("\n".join(review_lines))
        print(f"V19.3复盘报告占位已保存：{V19_REVIEW_REPORT_FILE}")
    except Exception as e:
        print(f"V19.3输出保存失败：{e}")



# ========================= V20.1 条件概率反馈闭环模块 =========================

# V20.1设计原则：
# - 不删除原模型任何有效逻辑，只在最终层做同源合并与分层收紧；
# - K线形态、量能形态不再零散堆分，而是归入“触发质量/资金行为”；
# - 压力带D、买点未触发、防守位过远、近端压力贴脸，不允许A档；
# - 条件概率反馈只做轻量校准，小样本不自动大幅调权。

def _v20_first_value(row, keys, default=0.0):
    for k in keys:
        try:
            v = row.get(k, None)
        except Exception:
            v = None
        if v is None or v == "":
            continue
        try:
            if pd.isna(v):
                continue
        except Exception:
            pass
        return v
    return default


def _v20_float(row, *keys, default=0.0):
    return safe_float(_v20_first_value(row, keys, default), default)


def _v20_text(row, *keys, default=""):
    v = _v20_first_value(row, keys, default)
    return str(v or default)


def _v20_pressure_grade_rank(grade):
    g = str(grade or "").strip().upper()
    if g.startswith("S"):
        return 5
    if g.startswith("A"):
        return 4
    if g.startswith("B"):
        return 3
    if g.startswith("C"):
        return 2
    if g.startswith("D"):
        return 1
    return 0


def v20_trade_metrics(row):
    """统一提取交易质量指标，兼容 V12/V14/V16/V19 不同字段名。"""
    close = _v20_float(row, "close", "收盘", "last_close")
    defense = _v20_float(row, "defensive_price", "trade_defense", "real_defense_level", "defense_level", "short_defense_price")
    rr = _v20_float(row, "risk_reward_ratio", "rr", "base_risk_reward_ratio")
    trade_q = _v20_float(row, "score_trade_quality", "trade_priority_score")
    pullback = _v20_float(row, "score_v12_pullback_entry", "score_pullback_entry")
    near_pressure = _v20_float(row, "near_pressure_dist")
    bias20 = _v20_float(row, "bias20")
    bias60 = _v20_float(row, "bias60")
    rsi = _v20_float(row, "rsi", "base_rsi")
    cci = _v20_float(row, "cci", "base_cci")
    long_pos = _v20_float(row, "long_pos_250")
    final_pressure = _v20_float(row, "xhu_pressure_union_upper", "xhu_final_union_upper", "final_pressure_upper")
    core_pressure = _v20_float(row, "xhu_pressure_core_upper", "core_pressure_upper")
    core_lower = _v20_float(row, "xhu_pressure_core_lower", "core_pressure_lower")
    confirm_price = _v20_float(row, "xhu_effective_confirm_price", "effective_confirm_price")
    pressure_grade = _v20_text(row, "xhu_pressure_model_grade", "v151_strongest_model_grade")

    plan = {}
    try:
        plan = build_v19_price_plan(row)
    except Exception:
        plan = {}

    if close <= 0:
        close = safe_float(plan.get("close", 0)) or close
    if defense <= 0:
        defense = safe_float(plan.get("short_defense_price", 0)) or safe_float(plan.get("hard_stop_price", 0))
    if final_pressure <= 0:
        final_pressure = safe_float(plan.get("target2_price", 0)) or safe_float(plan.get("break_confirm_price", 0))
    if core_pressure <= 0:
        core_pressure = safe_float(plan.get("target1_price", 0))
    if confirm_price <= 0:
        confirm_price = safe_float(plan.get("break_confirm_price", 0))
    if rr <= 0:
        target = safe_float(plan.get("target1_price", 0)) or core_pressure or final_pressure
        risk = close - defense if close > defense > 0 else 0
        reward = target - close if target > close > 0 else 0
        rr = reward / risk if risk > 0 else rr

    standard_low = safe_float(plan.get("standard_buy_low", 0))
    standard_high = safe_float(plan.get("standard_buy_high", 0))
    comfort_low = safe_float(plan.get("comfort_buy_low", 0))
    comfort_high = safe_float(plan.get("comfort_buy_high", 0))
    hard_stop = safe_float(plan.get("hard_stop_price", 0))
    giveup_price = safe_float(plan.get("giveup_price", hard_stop))
    target1 = safe_float(plan.get("target1_price", 0))
    target2 = safe_float(plan.get("target2_price", 0))

    defense_dist_signed = close / defense - 1 if close > 0 and defense > 0 else 0.0
    broken_defense = bool(close > 0 and defense > 0 and close < defense * 0.995)
    broken_hard_stop = bool(close > 0 and hard_stop > 0 and close < hard_stop * 0.998)
    broken_giveup = bool(close > 0 and giveup_price > 0 and close < giveup_price * 0.998)
    invalid_reasons = []
    if broken_hard_stop:
        invalid_reasons.append(f"收盘价{close:.2f}低于硬止损{hard_stop:.2f}")
    if broken_giveup and (giveup_price != hard_stop):
        invalid_reasons.append(f"收盘价{close:.2f}低于放弃价{giveup_price:.2f}")
    if broken_defense:
        invalid_reasons.append(f"收盘价{close:.2f}低于防守价{defense:.2f}")
    trade_invalidated = bool(broken_defense or broken_hard_stop or broken_giveup)
    # 评分用防守距离：跌破防守/硬止损时不能再被当作“距离防守很近”加分，直接给极大风险距离。
    defense_dist_for_score = 999.0 if trade_invalidated else max(0.0, defense_dist_signed)
    target_dist = 0.0
    if final_pressure > close > 0:
        target_dist = final_pressure / close - 1
    elif core_pressure > close > 0:
        target_dist = core_pressure / close - 1
    elif target1 > close > 0:
        target_dist = target1 / close - 1

    buy_zone_miss = False
    buy_zone_gap = 0.0
    if standard_high > 0 and close > standard_high:
        buy_zone_gap = close / standard_high - 1
        buy_zone_miss = buy_zone_gap > V20_BUY_ZONE_MISS_PCT

    confirm_far = False
    confirm_gap = 0.0
    if confirm_price > close > 0:
        confirm_gap = confirm_price / close - 1
        confirm_far = confirm_gap > V20_CONFIRM_FAR_PCT

    return {
        "close": float(close),
        "defense": float(defense),
        "hard_stop": float(hard_stop),
        "giveup_price": float(giveup_price),
        "defense_dist": float(defense_dist_for_score),
        "defense_dist_signed": float(defense_dist_signed),
        "broken_defense": bool(broken_defense),
        "broken_hard_stop": bool(broken_hard_stop),
        "broken_giveup": bool(broken_giveup),
        "trade_invalidated": bool(trade_invalidated),
        "trade_invalid_reason": "；".join(invalid_reasons),
        "rr": float(rr),
        "trade_q": float(trade_q),
        "pullback": float(pullback),
        "near_pressure": float(near_pressure),
        "target_dist": float(target_dist),
        "bias20": float(bias20),
        "bias60": float(bias60),
        "rsi": float(rsi),
        "cci": float(cci),
        "long_pos": float(long_pos),
        "core_lower": float(core_lower),
        "core_pressure": float(core_pressure),
        "final_pressure": float(final_pressure),
        "confirm_price": float(confirm_price),
        "confirm_gap": float(confirm_gap),
        "confirm_far": bool(confirm_far),
        "pressure_grade": pressure_grade,
        "pressure_grade_rank": _v20_pressure_grade_rank(pressure_grade),
        "standard_low": float(standard_low),
        "standard_high": float(standard_high),
        "comfort_low": float(comfort_low),
        "comfort_high": float(comfort_high),
        "buy_zone_gap": float(buy_zone_gap),
        "buy_zone_miss": bool(buy_zone_miss),
        "target1": float(target1),
        "target2": float(target2),
    }


def v2562_apply_trade_invalidation(row, reason=None):
    """V25.6.2硬风控闭环：交易假设已失效时，统一压分、空仓、剔除最终正式输出。
    只处理最终风控链路，不改原有结构/量价/压力带模型。
    """
    r = row if isinstance(row, dict) else dict(row)
    m = {}
    try:
        m = v20_trade_metrics(r)
    except Exception:
        m = {}
    invalid = bool(r.get('v20_trade_invalidated', False) or m.get('trade_invalidated', False))
    invalid_reason = reason or str(r.get('v20_trade_invalid_reason', '') or m.get('trade_invalid_reason', '') or '')
    if not invalid:
        return r
    r['v20_trade_invalidated'] = True
    r['v20_trade_invalid_reason'] = invalid_reason or '收盘价已跌破防守/硬止损，原交易假设失效'
    r['v20_trade_tier'] = 'C档已破位/放弃'
    r['v20_tier_reason'] = r['v20_trade_invalid_reason']
    r['v212_final_score'] = min(safe_float(r.get('v212_final_score', 0)), 35.0)
    r['v22_composite_trade_score'] = 0.0
    r['v22_score_valid'] = False
    r['v22_invalid_reason'] = r['v20_trade_invalid_reason']
    r['v212_action'] = '交易假设失效/不进入最终Top'
    r['v22_action'] = '交易假设失效/不进入最终Top'
    r['v241_position_pct'] = 0.0
    r['v241_position_text'] = '仓位0%｜已破位失效'
    r['v241_position_reason'] = r['v20_trade_invalid_reason']
    r['exclude_from_final'] = True
    return r


def detect_v201_low_volume_precise_trigger_line_from_row(row):
    """V20.1日线小平台低量精准触发线。
    说明：真正精算需要日线平台OHLCV；本函数先承接已有字段/价格计划，若未来深度层计算出
    v201_precise_trigger_line，将直接使用。没有该字段时，只做报告占位，不替代最终压力上沿。
    """
    explicit = _v20_float(row, "v201_precise_trigger_line", "low_volume_precise_trigger_line", "daily_precise_trigger_line")
    if explicit > 0:
        return {
            "line": float(explicit),
            "valid": True,
            "source": "已计算低量平台精准线",
            "note": str(row.get("v201_precise_trigger_note", "平台内低量K高点/实体顶/收盘共振线")),
        }
    m = v20_trade_metrics(row)
    # 若大周期压力带明确，但尚未有精算线，则用供需压力带下沿/价格计划标准买区上沿做弱占位。
    # 该占位只用于提示“需要日线精算”，不参与A档强确认。
    approx = 0.0
    if m["core_lower"] > 0 and m["core_pressure"] > 0:
        approx = m["core_lower"]
    elif m["standard_high"] > 0:
        approx = m["standard_high"]
    return {
        "line": float(approx),
        "valid": False,
        "source": "待深度层计算",
        "note": "需在大周期压力带明确时，切回日线小平台，寻找平台内低量分位K最高价与平台上沿/实体顶/收盘共振线。",
    }


def detect_v20_main_hypothesis(row):
    """把候选从指标堆叠归因到一个主交易假设。"""
    bucket = str(row.get("base_bucket", "") or "")
    main_signal = str(row.get("v16_main_signal", "") or row.get("v151_strongest_model_name", "") or "")
    pressure_grade = str(row.get("xhu_pressure_model_grade", "") or row.get("v151_strongest_model_grade", "") or "")
    monthly_score = _v20_float(row, "score_monthly_cycle", "score_monthly_midline", "monthly_score")
    pullback = _v20_float(row, "score_v12_pullback_entry", "score_pullback_entry")
    flat_cnt = _v20_float(row, "flat_volume_count_60", "flat_volume_count_60_base", "score_beiliang_flat")
    timing = _v20_float(row, "score_v126_timing_sufficiency", "score_timing_sufficiency")
    pressure_score = _v20_float(row, "xhu_pressure_breakout_score", "score_xhu_pressure_breakout", "v15_pressure_bonus")
    low_pos = _v20_float(row, "long_pos_250") <= 0.60

    bottom_score = _v20_float(row, "score_bottom_reversal_pattern", "bottom_pattern_score")
    bottom_type = str(row.get("bottom_pattern_type", "") or "")
    bottom_confirmed = bool(row.get("bottom_pattern_confirmed", False))
    if bottom_score >= 4:
        if bottom_confirmed:
            return f"底部反转确认/{bottom_type or '头肩底/W底/V底'}"
        return f"底部反转观察/{bottom_type or '头肩底/W底/V底'}"
    if "回踩" in bucket or "二买" in bucket or pullback > 0:
        return "回踩确认/二买候选"
    if "大周期" in bucket or "多周期" in bucket or monthly_score >= 6:
        return "大周期修复 + 日线触发"
    if "倍量后平量" in bucket or "资金承接" in bucket or flat_cnt > 0:
        return "资金承接/倍量后平量"
    if "平台蓄势" in bucket or "爆发前夜" in bucket or timing > 0:
        return "平台蓄势/爆发前夜"
    if "压力" in bucket or pressure_score > 0 or pressure_grade.upper()[:1] in ["S", "A", "B"]:
        return "压力带突破/供应吸收"
    if "低位" in bucket or (low_pos and ("突破" in main_signal or "启动" in main_signal)):
        return "低位强启动/关键位触发"
    if main_signal:
        return main_signal[:40]
    return "综合结构机会"



def v256_line_role_weight(row):
    """V25.8：突破对象权重。已移除旧线体系，改为供需压力带/买点质量权重。"""
    grade = str(row.get("xhu_pressure_model_grade", "") or "").upper()[:1]
    if grade == "S":
        return 1.00
    if grade == "A":
        return 0.86
    if grade == "B":
        return 0.62
    if grade == "C":
        return 0.36
    # 缠论二/三买可提高突破确认权重，但不再依赖任何线角色。
    bp = str(row.get("chan_buy_point_type", "") or "")
    if bp.startswith("三买") or bp.startswith("二买"):
        return 0.72
    return 0.30


def v256_market_regime_breakout_multiplier():
    """V25.6.1：市场环境只影响“突破确认”强度，不改主流程、不改基础扫描。"""
    regime = str(globals().get("V24_1_MARKET_REGIME", "neutral") or "neutral").lower().strip()
    if regime in ["panic", "crash"]:
        return 0.55
    if regime in ["bear", "weak"]:
        return 0.68
    if regime in ["range", "choppy"]:
        return 0.88
    if regime in ["bull", "strong"]:
        return 1.05
    return 1.00


def v256_breakout_day_quality(row):
    """V25.6：把跳空、涨停、放量、强收盘、短上影等合并为日线突破质量，不再分散重复加分。"""
    grade = str(row.get("xhu_breakout_day_grade", "") or row.get("breakout_day_grade", "")).upper()
    base = {"S": 10.0, "A": 7.5, "B": 4.0, "C": 1.5}.get(grade[:1], 0.0)
    # 兼容已有突破描述/质量字段，不单独重复奖励K线形态。
    explicit = safe_float(row.get("xhu_coreline_breakout_score", row.get("xhu_breakout_score", 0)))
    if explicit > 0:
        base = max(base, min(10.0, explicit))
    role_w = v256_line_role_weight(row)
    # V25.6.1：熊市/恐慌中压力突破假信号更多，只下调突破确认强度，不改线本身质量。
    regime_mul = v256_market_regime_breakout_multiplier()
    return max(0.0, min(12.0, base * (0.35 + role_w * 0.65) * regime_mul))


def v256_same_source_dedup(row, structure_position, pressure_support, volume_behavior, trigger_confirmation):
    """V25.6.1：同源去重与强封顶。

    只处理七层后置分内部的重复来源，不改原始基础/深度字段、不改主流程。
    目标：凹口/平台/最大量K/压力带/供需锚点属于同一结构簇时，只给“最强项+少量共振”；
    跳空/涨停/倍量/强收盘/突破线统一归入日线突破质量，避免重复堆分。
    """
    struct0 = float(structure_position)
    press0 = float(pressure_support)
    vol0 = float(volume_behavior)
    trig0 = float(trigger_confirmation)

    role = str(row.get("xhu_coreline_role", "") or "")
    core_score = safe_float(row.get("xhu_coreline_core_score", 0))
    neural_score = safe_float(row.get("xhu_coreline_neural_score", 0))
    xhu_score = safe_float(row.get("score_xhu_pressure_breakout", row.get("xhu_pressure_breakout_score", 0)))
    platform_like = max(
        safe_float(row.get("score_structure_core", 0)),
        safe_float(row.get("base_structure_potential_score", 0)),
        safe_float(row.get("score_advanced_ao_kou", 0)),
        safe_float(row.get("score_fibo_reclaim", 0)),
        safe_float(row.get("bottom_pattern_score", row.get("score_bottom_reversal_pattern", 0))),
    )

    notes = []

    # 结构/压力同源强封顶：只有“平台/凹口/底部结构”和“压力/供需锚点”同时很强时才压，避免误伤单一优质结构。
    if xhu_score > 0 and platform_like > 0 and struct0 > 8.0 and press0 > 8.0:
        total = struct0 + press0
        cap = min(total * 0.78, 29.0)
        if total > cap:
            # 供需锚点负责主供应/需求边界，结构位置负责大背景；两者保留但压掉重复来源。
            structure_position = min(struct0, cap * 0.50)
            pressure_support = min(press0, cap * 0.55)
            notes.append(f"结构/压力同源强封顶{total:.1f}->{cap:.1f}")

    # 普通日线触发线不能被包装成供需压力；共振锚点可判断强弱，但不能等价主供需区突破。
    if ("触发" in role or "普通" in role) and core_score < 55 and pressure_support > 10.5:
        pressure_support = 10.5
        notes.append("普通触发线压力分封顶")
    if "神经" in role and "供需共振" not in role and core_score < 65 and pressure_support > 13.5:
        pressure_support = 13.5
        notes.append("共振锚点非主供需压力分封顶")

    # HVN/LVN去重：HVN是压力/供应证据，LVN是突破后的加速通道证据；两者不能都当压力大加分。
    hvn = safe_float(row.get("xhu_coreline_hvn_score", 0))
    lvn = safe_float(row.get("xhu_coreline_lvn_above_score", row.get("xhu_coreline_upper_supply_thinness", 0)))
    fake_count = int(safe_float(row.get("xhu_fake_breakout_count", 0)))
    breakout_q = v256_breakout_day_quality(row)
    if hvn < 4.0 and pressure_support > 14.0 and core_score < 70:
        pressure_support = 14.0
        notes.append("HVN不足压制压力高分")
    if fake_count >= 2 and breakout_q < 6.5:
        pressure_support = max(0.0, pressure_support - min(4.0, 0.9 * fake_count))
        notes.append(f"假突破记忆惩罚{fake_count}次")
    if lvn > hvn * 1.6 and pressure_support > 16.0 and breakout_q < 5.0:
        # 上方低量真空本身不是压力；没有有效突破前不能把LVN误当压力质量高分。
        pressure_support = 16.0
        notes.append("LVN未突破前不当压力高分")

    # 触发/资金同源：同一日跳空、涨停、放量、强收盘、突破供需锚点统一归入突破质量。
    if breakout_q >= 7.0 and (vol0 + trig0) > 28.0:
        cap = 28.0
        ratio = cap / max(vol0 + trig0, 1e-9)
        volume_behavior = max(0.0, vol0 * ratio)
        trigger_confirmation = max(0.0, trig0 * ratio)
        notes.append(f"资金/触发同源封顶{vol0+trig0:.1f}->{cap:.1f}")

    return {
        "structure_position": float(max(0.0, min(20.0, structure_position))),
        "pressure_support": float(max(0.0, min(20.0, pressure_support))),
        "volume_behavior": float(max(0.0, min(20.0, volume_behavior))),
        "trigger_confirmation": float(max(0.0, min(15.0, trigger_confirmation))),
        "dedup_note": "；".join(notes),
        "breakout_quality_score": float(breakout_q),
    }

def _v258_opportunity_type(row):
    bp = str(row.get("chan_buy_point_type", "") or "")
    pressure_grade = str(row.get("xhu_pressure_model_grade", "") or "")
    pullback = _v20_float(row, "score_v12_pullback_entry", "score_pullback_entry")
    flat = _v20_float(row, "score_beiliang_flat", "flat_volume_count_60", "flat_volume_count_60_base")
    eve = _v20_float(row, "base_channel_explosion_eve_score", "base_explosion_eve_score_raw")
    struct = _v20_float(row, "score_structure_core", "base_structure_potential_score")
    if bp.startswith("三买"):
        return "缠论三买/中枢回抽确认"
    if bp.startswith("二买"):
        return "缠论二买/背驰后二次确认"
    if pressure_grade in ("S", "A"):
        return "多周期供需压力带突破"
    if pullback >= 5:
        return "突破后回踩确认"
    if flat >= 2:
        return "倍量后平量承接"
    if eve >= 18:
        return "爆发前夜/平台蓄势"
    if struct >= 5:
        return "低位结构修复"
    return "综合观察"


def _v258_negative_evidence(row, m):
    neg = []
    if bool(row.get("v14_blocked", False)) or bool(row.get("regulatory_hard_exclude", False)):
        neg.append("硬雷区/监管财务风险")
    if m["bias20"] > 0.18:
        neg.append("20日乖离偏高")
    if 0 < m["near_pressure"] < 0.05:
        neg.append("近端压力贴脸")
    if m["defense_dist"] > 0.10:
        neg.append("真实防守位偏远")
    if _v20_float(row, "volume_efficiency_score") <= 2 and _v20_float(row, "vr1") >= 2.5:
        neg.append("放量效率不足")
    if str(row.get("xhu_pressure_model_grade", "D")) == "D" and _v20_float(row, "score_xhu_pressure_breakout") > 0:
        neg.append("压力带冲击质量弱")
    if _v20_float(row, "chan_divergence_score") < 0:
        neg.append("高位动能/量能背离")
    return "；".join(neg) if neg else "暂无明显反证"


def v201_simplified_layer_scores(row):
    """
    V25.8机构级量价时空评分。
    已彻底移除供需压力带加分。旧字段如 xhu_coreline_* 仅兼容读取，不参与主评分。
    主骨架：硬风险 -> 机会分类 -> 价/量/时/空 -> 买点确认 -> 执行 -> 市场 -> 反馈。
    """
    m = v20_trade_metrics(row)
    base_score = _v20_float(row, "v16_final_score", "v14_final_score", "total_score")
    monthly = _v20_float(row, "score_monthly_cycle", "score_monthly_midline", "monthly_score")
    structure = _v20_float(row, "score_structure_core", "base_structure_potential_score", "score_advanced_ao_kou", "score_fibo_reclaim")
    bottom_pattern = _v20_float(row, "score_bottom_reversal_pattern", "bottom_pattern_score")
    pressure = _v20_float(row, "xhu_pressure_breakout_score", "score_xhu_pressure_breakout", "v15_pressure_bonus")
    pressure_quality = _v20_float(row, "xhu_pressure_quality_score", "xhu_pressure_quality", "xhu_pressure_zone_quality", "v15_pressure_quality")
    pressure_grade = str(row.get("xhu_pressure_model_grade", "D") or "D")
    flat = _v20_float(row, "score_beiliang_flat", "flat_volume_count_60", "flat_volume_count_60_base")
    carry = _v20_float(row, "score_carry_structure", "base_volume_carry_score")
    timing_old = _v20_float(row, "score_v126_timing_sufficiency", "score_timing_sufficiency")
    pullback = _v20_float(row, "score_v12_pullback_entry", "score_pullback_entry")
    kline_attack = _v20_float(row, "base_attack_quality_score", "score_kline_quality", "v16_kline_trigger_score")
    risk_penalty_old = _v20_float(row, "score_chase_penalty", "base_risk_penalty", default=0.0)

    chan_score = _v20_float(row, "chan_score")
    chan_buy_score = _v20_float(row, "chan_buy_point_score")
    chan_pivot_stability = _v20_float(row, "chan_pivot_volume_stability")
    time_maturity = _v20_float(row, "time_maturity_score")
    volume_efficiency = _v20_float(row, "volume_efficiency_score")
    space_payoff = _v20_float(row, "space_payoff_score")
    chan_divergence = _v20_float(row, "chan_divergence_score")

    # 0）硬风险过滤：重大风险一票否决，软风险进入扣分。
    risk_filter = 0.0
    hard_block = bool(row.get("v14_blocked", False)) or bool(row.get("regulatory_hard_exclude", False))
    if hard_block:
        risk_filter = -100.0
    else:
        if m["bias20"] > 0.18:
            risk_filter -= 4.0
        if m["rsi"] >= V20_OVERHEAT_RSI and m["cci"] >= V20_OVERHEAT_CCI:
            risk_filter -= 6.0
        if 0 < m["near_pressure"] < 0.05:
            risk_filter -= 5.0
        if m["defense_dist"] > 0.10:
            risk_filter -= 5.0
        if str(row.get("data_quality_tier", "")).upper().startswith("Q"):
            risk_filter -= 3.0
        if chan_divergence < 0:
            risk_filter += max(-5.0, chan_divergence)
        risk_filter += max(-8.0, min(0.0, risk_penalty_old))

    # 1）价：价格结构/级别位置 18分。
    price_structure = 0.0
    if m["long_pos"] <= 0.35:
        price_structure += 4.0
    elif m["long_pos"] <= 0.60:
        price_structure += 2.8
    elif m["long_pos"] > 0.80:
        price_structure -= 3.0
    price_structure += min(4.0, monthly * 0.30)
    price_structure += min(4.0, structure * 0.25)
    price_structure += min(2.5, bottom_pattern * 0.25)
    price_structure += min(3.5, chan_score * 0.12)
    if _v20_float(row, "chan_pivot_upper") > 0:
        price_structure += 1.5
    price_structure = max(0.0, min(18.0, price_structure))

    # 2）量：量价效率/承接/吸收 22分。
    volume_relation = 0.0
    volume_relation += min(5.0, volume_efficiency * 0.45)
    volume_relation += min(5.0, carry * 0.35)
    volume_relation += min(4.5, flat * 1.10)
    volume_relation += min(3.0, chan_pivot_stability * 0.65)
    volume_relation += min(2.5, timing_old * 0.18)
    if _v20_float(row, "vr1") > 3.5 and m["bias20"] > 0.12:
        volume_relation -= 4.0
    if chan_divergence < 0:
        volume_relation += max(-4.0, chan_divergence)
    volume_relation = max(0.0, min(22.0, volume_relation))

    # 3）时：时间成熟度/生命周期 15分。
    time_cycle = 0.0
    time_cycle += min(6.0, time_maturity * 0.55)
    time_cycle += min(3.0, timing_old * 0.25)
    if _v20_float(row, "chan_pivot_duration") >= 18:
        time_cycle += 2.0
    if chan_buy_score >= 6:
        time_cycle += 2.5
    if _v20_float(row, "base_channel_explosion_eve_score") >= 18:
        time_cycle += 1.5
    time_cycle = max(0.0, min(15.0, time_cycle))

    # 4）空：空间赔率 15分。
    space_score = 0.0
    space_score += min(6.0, space_payoff * 0.55)
    if m["target_dist"] >= 0.15:
        space_score += 3.5
    elif m["target_dist"] >= 0.08:
        space_score += 2.0
    elif 0 < m["target_dist"] < 0.06:
        space_score -= 3.0
    if m["rr"] >= 2.0:
        space_score += 3.5
    elif m["rr"] >= 1.5:
        space_score += 2.0
    elif 0 < m["rr"] < 1.2:
        space_score -= 3.5
    if 0 < m["defense_dist"] <= 0.05:
        space_score += 2.0
    elif m["defense_dist"] > 0.10:
        space_score -= 3.0
    space_score = max(0.0, min(15.0, space_score))

    # 5）买点确认：二买/三买、回踩、突破、涨停承接 12分。
    precise = detect_v201_low_volume_precise_trigger_line_from_row(row)
    breakout_quality = v256_breakout_day_quality(row)
    trigger_confirmation = 0.0
    trigger_confirmation += min(3.5, chan_buy_score * 0.45)
    trigger_confirmation += min(3.0, pullback * 0.45)
    trigger_confirmation += min(2.5, breakout_quality * 0.22)
    trigger_confirmation += min(1.5, kline_attack * 0.05)
    if precise["line"] > 0 and precise["valid"]:
        trigger_confirmation += 1.5
    if m["buy_zone_miss"]:
        trigger_confirmation -= 3.0
    if m["confirm_far"]:
        trigger_confirmation -= 2.0
    trigger_confirmation = max(0.0, min(12.0, trigger_confirmation))

    # 6）执行质量 8分。
    execution_quality = 0.0
    amount = _v20_float(row, "amount", "成交额")
    if amount >= V24_1_STRICT_AMOUNT_FOR_FORMAL:
        execution_quality += 2.5
    elif amount >= V24_1_MIN_AMOUNT_FOR_FORMAL:
        execution_quality += 1.8
    elif amount > 0 and amount < V24_1_ABSOLUTE_MIN_AMOUNT:
        execution_quality -= 2.5
    if 0 < m["defense_dist"] <= 0.05:
        execution_quality += 2.5
    elif m["defense_dist"] <= 0.08:
        execution_quality += 1.5
    if not m["buy_zone_miss"]:
        execution_quality += 1.5
    if m["rr"] >= 1.5:
        execution_quality += 1.5
    execution_quality = max(0.0, min(8.0, execution_quality))

    # 7）市场/板块 5分：当前版本保守处理，保留给三号员工板块龙头联动。
    market_board = 0.0
    regime = str(os.environ.get("V24_1_MARKET_REGIME", "neutral")).lower().strip()
    if regime in ("bull", "range", "neutral"):
        market_board += 2.0
    elif regime == "bear":
        market_board -= 2.0
    elif regime == "panic":
        market_board -= 5.0
    market_board += min(3.0, _v20_float(row, "sector_heat_score", "board_heat_score") * 0.20)
    market_board = max(0.0, min(5.0, market_board))

    # 8）反馈校准 5分。
    feedback = v20_condition_probability_hint(row) if V20_ENABLE_CONDITION_FEEDBACK == "1" else {"score_adj": 0.0, "text": "条件概率反馈关闭", "sample_count": 0}
    feedback_adj_raw = max(-10.0, min(10.0, float(feedback.get("score_adj", 0.0) or 0.0)))
    feedback_adj = max(-5.0, min(5.0, feedback_adj_raw * 0.50))

    opportunity_type = _v258_opportunity_type(row)
    negative_evidence = _v258_negative_evidence(row, m)

    # 同源封顶：价/量/触发强相关，不能重复把同一天强攻堆满。
    dedup_notes = []
    if volume_relation >= 18 and trigger_confirmation >= 10:
        total = volume_relation + trigger_confirmation
        cap = 28.0
        if total > cap:
            ratio = cap / max(total, 1e-9)
            volume_relation *= ratio
            trigger_confirmation *= ratio
            dedup_notes.append(f"量价/触发同源封顶{total:.1f}->{cap:.1f}")
    if pressure_grade in ("S", "A") and price_structure >= 16 and trigger_confirmation >= 10:
        price_structure = min(price_structure, 17.0)
        dedup_notes.append("结构/压力/触发同源降权")

    layer_raw = (
        price_structure + volume_relation + time_cycle + space_score + trigger_confirmation
        + execution_quality + market_board + feedback_adj + risk_filter
    )
    # 主分仍尊重原深度模型，但后置评分更强调可交易性与价量时空。
    v201_score = max(0.0, min(100.0, base_score * 0.42 + layer_raw * 0.78))

    return {
        "base_score": float(base_score),
        "risk_filter": float(risk_filter),
        "structure_position": float(price_structure),
        "pressure_support": float(space_score),
        "volume_behavior": float(volume_relation),
        "trigger_confirmation": float(trigger_confirmation),
        "trade_quality": float(execution_quality + space_score),
        "feedback_adj": float(feedback_adj),
        "v201_score": float(v201_score),
        "feedback_text": str(feedback.get("text", "")),
        "feedback_sample_count": int(feedback.get("sample_count", 0) or 0),
        "precise_trigger_line": float(precise["line"]),
        "precise_trigger_valid": bool(precise["valid"]),
        "precise_trigger_note": precise["note"],
        "bottom_pattern_score": float(bottom_pattern),
        "bottom_pattern_volume_quality": float(_v20_float(row, "bottom_pattern_volume_quality")),
        "bottom_pattern_trigger_quality": float(_v20_float(row, "bottom_pattern_trigger_quality")),
        "v256_same_source_dedup_note": "；".join(dedup_notes),
        "v256_breakout_quality_score": float(breakout_quality),
        "v256_line_role": "已删除供需压力带",
        "v256_core_score": 0.0,
        "v256_neural_score": 0.0,
        "v256_hvn_score": 0.0,
        "v256_lvn_score": 0.0,
        "v256_hvn_effective_score": 0.0,
        "v256_fake_breakout_count": int(safe_float(row.get("xhu_fake_breakout_count", 0))),
        "v256_regime_breakout_multiplier": float(v256_market_regime_breakout_multiplier()),
        # 新增V25.8审计字段
        "v258_opportunity_type": opportunity_type,
        "v258_price_structure_score": float(price_structure),
        "v258_volume_relation_score": float(volume_relation),
        "v258_time_cycle_score": float(time_cycle),
        "v258_space_payoff_score": float(space_score),
        "v258_buy_point_score": float(trigger_confirmation),
        "v258_execution_score": float(execution_quality),
        "v258_market_board_score": float(market_board),
        "v258_negative_evidence": negative_evidence,
        "v258_chan_buy_point_type": str(row.get("chan_buy_point_type", "")),
        "v258_chan_pivot": f"{_v20_float(row, 'chan_pivot_lower'):.2f}-{_v20_float(row, 'chan_pivot_upper'):.2f}",
        "v258_time_desc": str(row.get("time_maturity_desc", "")),
        "v258_volume_desc": str(row.get("volume_efficiency_desc", "")),
        "v258_space_desc": str(row.get("space_payoff_desc", "")),
    }






# ========================= V26 爆发前夜最终买入池｜机构评分卡增量层 =========================
# 本层只做“后置融合/分层约束/报告字段增强”，不删除、不改写 V12-V25 原始主模型。
# 核心定位：爆发型、Top5最终买入池、高胜率偏好、部分黑箱、动态仓位、半自动自学习闭环。
# 设计原则：
# 1）母因子负责打分，子信号只负责解释，避免平量/缩量/小阴小阳/波动压缩等同源重复堆分；
# 2）综合分>=80只是入池必要条件，仍必须通过RR、防守位、流动性、信号新鲜度、失败相似度、市场环境等硬条件；
# 3）重大财务/监管/治理/退市/ST/流动性硬风险一票否决；
# 4）正式Top5允许空缺，不凑数；仓位四档：观察仓、试仓、标准仓、重仓候选；
# 5）自学习默认半自动：只写审计字段和调参建议，不自动大幅改权重。
# ===========================================================================
V26_ENABLED = os.environ.get("V26_ENABLED", "1")
# V26.2 LOGIC-ONLY PATCH：V26参数只在选股逻辑出口处统一口径；不触碰入口/workflow/cache/PAT/Telegram/BaoStock。
# 这里优先继承文件前部已有V26配置，避免同名参数前后默认值不一致。
V26_MIN_BUY_SCORE = float(os.environ.get("V26_MIN_BUY_SCORE", str(globals().get("V26_MIN_BUY_SCORE", 80))))
V26_STRONG_CONFIRM_SCORE = float(os.environ.get("V26_STRONG_CONFIRM_SCORE", str(globals().get("V26_STRONG_CONFIRM_SCORE", 82))))
V26_STANDARD_SCORE = float(os.environ.get("V26_STANDARD_SCORE", str(globals().get("V26_STANDARD_POSITION_SCORE", globals().get("V26_STANDARD_SCORE", 88)))))
V26_MIN_RR = float(os.environ.get("V26_MIN_RR", str(globals().get("V26_MIN_RR", os.environ.get("V212_MIN_RR_FORMAL", "1.35")))))
V26_MIN_UPSIDE = float(os.environ.get("V26_MIN_UPSIDE", "0.10"))
V26_MAX_DEFENSE_DIST = float(os.environ.get("V26_MAX_DEFENSE_DIST", str(globals().get("V26_MAX_DEFENSE_DIST", 0.105))))
V26_MAX_FAILURE_SIM = float(os.environ.get("V26_MAX_FAILURE_SIM", "68"))
V26_MAX_SIGNAL_AGE_DAYS = int(os.environ.get("V26_MAX_SIGNAL_AGE_DAYS", str(globals().get("V26_SIGNAL_MAX_AGE_DAYS", 13))))
V26_ALLOW_EMPTY_TOP5 = os.environ.get("V26_ALLOW_EMPTY_TOP5", str(globals().get("V26_ALLOW_EMPTY_TOP5", "1")))
V26_ENABLE_PORTFOLIO_DECORRELATION = os.environ.get("V26_ENABLE_PORTFOLIO_DECORRELATION", "1")
V26_MAX_SAME_SECTOR = int(os.environ.get("V26_MAX_SAME_SECTOR", "2"))
V26_MAX_SAME_HYPOTHESIS = int(os.environ.get("V26_MAX_SAME_HYPOTHESIS", "2"))
# 旧模型只作为结构底座校准，不能反客为主把观察票推成买入票。
V26_LEGACY_BLEND_WEIGHT = float(os.environ.get("V26_LEGACY_BLEND_WEIGHT", "0.15"))
# 母因子硬门槛：防止只靠旧分/单一优点堆进最终买入池。
V26_MIN_CORE_MOTHER_SCORE = float(os.environ.get("V26_MIN_CORE_MOTHER_SCORE", "40"))
V26_MIN_PRICING_CARD = float(os.environ.get("V26_MIN_PRICING_CARD", "7"))
V26_MIN_EXECUTION_CARD = float(os.environ.get("V26_MIN_EXECUTION_CARD", "5"))
V26_MIN_ACCEPTANCE_OR_BREAKOUT_CARD = float(os.environ.get("V26_MIN_ACCEPTANCE_OR_BREAKOUT_CARD", "6"))
V26_AUTO_LEARN_MODE = os.environ.get("V26_AUTO_LEARN_MODE", "semi")  # off / semi / auto；默认半自动，只出建议不自动改权重
V26_SCORECARD_FILE = os.environ.get("V26_SCORECARD_FILE", "v26_institutional_scorecards.json")
V26_REVIEW_FILE = os.environ.get("V26_REVIEW_FILE", "v26_self_learning_review.json")


def _v26_bool(x):
    if isinstance(x, bool):
        return x
    return str(x).strip().lower() in ["1", "true", "yes", "y", "是", "真"]


def _v26_text(row, *keys):
    for k in keys:
        v = row.get(k, "")
        if v is not None and str(v).strip() != "":
            return str(v)
    return ""


def _v26_sector(row):
    return _v26_text(row, "industry", "行业", "sector", "板块", "concept", "概念", "v26_sector") or "未知板块"


def _v26_hypothesis(row):
    return _v26_text(row, "v20_main_hypothesis", "main_hypothesis", "v16_main_signal", "v19_main_signal") or "综合结构机会"


def _v26_clip(x, lo=0.0, hi=100.0):
    try:
        x = float(x)
    except Exception:
        x = lo
    if x != x:
        x = lo
    return max(lo, min(hi, x))


def _v26_last_date(row):
    v = row.get("date", row.get("日期", ""))
    try:
        return pd.to_datetime(v, errors="coerce")
    except Exception:
        return pd.NaT


def _v26_signal_age_days(row):
    # 优先使用已有生命周期字段；没有则按date到北京时间自然日粗算，避免因交易日历缺失而误杀。
    for k in ["signal_age", "v20_signal_age", "v212_signal_age", "v26_signal_age_days"]:
        if k in row and str(row.get(k, "")).strip() != "":
            return max(0, int(safe_float(row.get(k), 0)))
    d = _v26_last_date(row)
    if pd.isna(d):
        return 0
    try:
        now = pd.to_datetime(datetime.now())
        return max(0, int((now.normalize() - d.normalize()).days))
    except Exception:
        return 0


def _v26_regime():
    return str(globals().get("V24_1_MARKET_REGIME", os.environ.get("V24_1_MARKET_REGIME", "neutral")) or "neutral").lower().strip()


def _v26_market_env_score():
    regime = _v26_regime()
    if regime in ["bull", "strong", "risk_on", "risk-on"]:
        return 88.0, "Risk-on/强趋势环境，允许爆发型信号正常发挥"
    if regime in ["range", "neutral", "normal", "", "震荡"]:
        return 72.0, "中性/震荡环境，要求买点更舒服、RR更清楚"
    if regime in ["weak", "bear", "risk_off", "risk-off"]:
        return 42.0, "弱势/退潮环境，正式候选数量与仓位收缩"
    if regime in ["panic", "crash", "系统性风险"]:
        return 12.0, "恐慌/系统性风险环境，原则上空仓或仅保留观察"
    return 60.0, f"未知市场状态{regime}，保守处理"


def _v26_card_explosion_eve(row):
    # 爆发前夜：压缩、平稳量、资金攻击记忆、关键位贴近、时间窗口共同刻画。
    parts = []
    s = 0.0
    base_eve = max(
        safe_float(row.get("base_explosion_eve_score", 0)),
        safe_float(row.get("v23_explosion_eve_score", 0)),
        safe_float(row.get("explosion_eve_score", 0)),
        safe_float(row.get("v201_structure_position", 0)) * 1.2,
    )
    if base_eve > 0:
        s += min(7.0, base_eve / 2.0)
        parts.append("已有爆发前夜/结构位置种子")
    vol_abs = max(safe_float(row.get("v23_supply_absorption_score", 0)), safe_float(row.get("supply_absorption_score", 0)))
    if vol_abs >= 8:
        s += 4.0; parts.append("供应吸收/平台压缩较明显")
    elif vol_abs >= 4:
        s += 2.0; parts.append("存在供应吸收迹象")
    if safe_float(row.get("v20_target_dist", row.get("target_dist", 0))) >= 0.12:
        s += 2.5; parts.append("上方空间支持爆发")
    if safe_float(row.get("v20_defense_dist", row.get("defense_dist", 0))) <= 0.07 and safe_float(row.get("v20_defense_dist", row.get("defense_dist", 0))) > 0:
        s += 2.5; parts.append("距离防守位较舒服")
    if safe_float(row.get("time_window_score", row.get("v126_time_window_score", 0))) > 0:
        s += 2.0; parts.append("存在时间窗口/蓄势成熟线索")
    if safe_float(row.get("v20_rr", row.get("risk_reward_ratio", row.get("rr", 0)))) >= 1.8:
        s += 2.0; parts.append("赔率达到爆发前夜候选要求")
    return _v26_clip(s, 0, 20), parts or ["爆发前夜证据不足，主要依赖旧模型结构分"]


def _v26_card_key_structure(row):
    parts = []
    s = 0.0
    pressure_grade = str(row.get("v212_pressure_grade", row.get("v15_model_grade", row.get("pressure_zone_grade", ""))))
    if pressure_grade in ["S", "A"] or "S" in pressure_grade or "A" in pressure_grade:
        s += 5.0; parts.append("核心压力带/关键结构位质量高")
    elif pressure_grade:
        s += 2.0; parts.append("有压力带/关键结构位记录")
    if safe_float(row.get("v201_structure_position", 0)) >= 12:
        s += 4.0; parts.append("结构位置评分高")
    elif safe_float(row.get("v201_structure_position", 0)) >= 8:
        s += 2.5; parts.append("结构位置尚可")
    for k, label in [("v23_supply_absorption_score", "大级别供应吸收"), ("v231_shadow_acceptance_score", "长上影供应接受度"), ("notch_score", "凹口/平台"), ("monthly_repair_score", "大周期修复")]:
        if safe_float(row.get(k, 0)) > 0:
            s += 1.8; parts.append(label)
    return _v26_clip(s, 0, 15), parts or ["关键结构位证据一般"]


def _v26_card_supply_absorption(row):
    parts = []
    s = 0.0
    supply = max(safe_float(row.get("v23_supply_absorption_score", 0)), safe_float(row.get("supply_absorption_score", 0)))
    if supply >= 12:
        s += 6.0; parts.append("供应吸收评分高")
    elif supply >= 7:
        s += 4.0; parts.append("供应吸收成立")
    elif supply >= 3:
        s += 2.0; parts.append("存在轻度吸收")
    vb = safe_float(row.get("v201_volume_behavior", 0))
    if vb >= 10:
        s += 3.0; parts.append("量能行为较健康")
    elif vb >= 6:
        s += 1.5; parts.append("量能行为尚可")
    for k, label in [("flat_volume_score", "平量稳定"), ("compression_score", "波动压缩"), ("platform_volume_lift_score", "平台均量抬升")]:
        if safe_float(row.get(k, 0)) > 0:
            s += 1.0; parts.append(label)
    # 供应吸收内部同源封顶：最多12分。
    return _v26_clip(s, 0, 12), parts or ["供应吸收/平量压缩证据不足"]


def _v26_card_acceptance(row):
    parts = []
    s = 0.0
    if safe_float(row.get("v212_acceptance_score", 0)) > 0:
        s += min(4.0, safe_float(row.get("v212_acceptance_score", 0)) / 2.5); parts.append("V21.2承接确认")
    if safe_float(row.get("v201_trade_quality", 0)) >= 12:
        s += 3.5; parts.append("交易质量/承接质量高")
    elif safe_float(row.get("v201_trade_quality", 0)) >= 8:
        s += 2.0; parts.append("交易质量尚可")
    if safe_float(row.get("v20_pullback", row.get("pullback_score", 0))) > 0:
        s += 2.0; parts.append("回踩承接/二买线索")
    if safe_float(row.get("volume_after_flat_acceptance_score", row.get("v212_flat_acceptance_score", 0))) > 0:
        s += 2.0; parts.append("倍量后平量承接")
    if safe_float(row.get("v20_defense_dist", row.get("defense_dist", 0))) <= V26_MAX_DEFENSE_DIST and safe_float(row.get("v20_defense_dist", row.get("defense_dist", 0))) > 0:
        s += 1.2; parts.append("防守距离未过远")
    return _v26_clip(s, 0, 12), parts or ["承接验证不足，需要次日确认"]


def _v26_card_breakout_expansion(row):
    parts = []
    s = 0.0
    action = str(row.get("v212_action", ""))
    if action.startswith("V21.2正式"):
        s += 4.0; parts.append("V21.2正式交易触发")
    grade = str(row.get("v212_state", row.get("v15_day_grade", "")))
    if "突破" in action or "break" in action.lower() or grade in ["S", "A"]:
        s += 3.0; parts.append("突破扩张信号")
    if safe_float(row.get("v201_volume_behavior", 0)) >= 10:
        s += 2.0; parts.append("量能配合突破")
    if safe_float(row.get("v20_chase_risk", row.get("chase_risk", 0))) > 0.08:
        s -= 2.5; parts.append("追高/乖离对突破扩张降权")
    if _v26_bool(row.get("is_bad_stall", False)) or safe_float(row.get("stall_risk_score", 0)) > 0:
        s -= 2.0; parts.append("存在放量滞涨风险")
    return _v26_clip(s, 0, 12), parts or ["突破扩张不是主因，需靠承接/压缩取胜"]


def _v26_card_pricing(row):
    parts = []
    rr = safe_float(row.get("v20_rr", row.get("risk_reward_ratio", row.get("rr", 0))))
    defense = safe_float(row.get("v20_defense_dist", row.get("defense_dist", 0)))
    upside = safe_float(row.get("v20_target_dist", row.get("target_dist", row.get("v212_target_dist", 0))))
    space_score = safe_float(row.get("v212_space_score", 0))
    s = 0.0
    if rr >= 2.5:
        s += 4.5; parts.append("RR优秀")
    elif rr >= V26_MIN_RR:
        s += 3.2; parts.append("RR合格")
    elif rr > 0:
        s += 1.0; parts.append("RR偏低")
    if 0 < defense <= 0.055:
        s += 3.2; parts.append("防守距离舒服")
    elif 0 < defense <= V26_MAX_DEFENSE_DIST:
        s += 2.0; parts.append("防守距离可接受")
    elif defense > V26_MAX_DEFENSE_DIST:
        s -= 2.0; parts.append("离真实防守位偏远")
    if upside >= 0.18:
        s += 3.0; parts.append("上方空间较大")
    elif upside >= V26_MIN_UPSIDE:
        s += 2.0; parts.append("上方空间合格")
    elif upside > 0:
        s -= 1.0; parts.append("上方空间偏窄")
    if space_score >= 65:
        s += 1.3; parts.append("空间评分确认")
    return _v26_clip(s, 0, 12), parts or ["定价/RR信息不足"]


def _v26_card_sector(row):
    parts = []
    s = 0.0
    # 可由workflow/上游写入：sector_lifecycle=start/main/climax/decline, sector_heat_score=0-100。
    lifecycle = str(row.get("sector_lifecycle", row.get("v26_sector_lifecycle", ""))).lower().strip()
    heat = safe_float(row.get("sector_heat_score", row.get("v26_sector_heat_score", 0)))
    if lifecycle in ["start", "early", "启动", "启动初期"]:
        s += 4.0; parts.append("板块生命周期处于启动初期")
    elif lifecycle in ["main", "trend", "主升", "主升中段"]:
        s += 3.0; parts.append("板块处于主升/趋势阶段")
    elif lifecycle in ["climax", "late", "高潮", "高潮末端"]:
        s += 0.5; parts.append("板块可能高潮，谨慎加分")
    elif lifecycle in ["decline", "退潮", "down"]:
        s -= 3.0; parts.append("板块退潮，降权")
    if heat >= 80:
        s += 2.0; parts.append("行业热点强")
    elif heat >= 60:
        s += 1.2; parts.append("行业热度尚可")
    elif heat > 0 and heat < 35:
        s -= 0.8; parts.append("行业热度偏弱")
    # 没有行业数据时不扣太多，避免数据缺失误杀。
    if not parts:
        parts.append("行业热点数据缺失，按中性处理")
        s += 2.5
    return _v26_clip(s, 0, 6), parts


def _v26_card_market(row):
    m, txt = _v26_market_env_score()
    return _v26_clip(m / 20.0, 0, 5), [txt]


def _v26_card_execution(row):
    parts = []
    s = 0.0
    liq_score = safe_float(row.get("v241_liquidity_score", 0))
    if liq_score >= 90:
        s += 1.4; parts.append("流动性舒适")
    elif liq_score >= 70:
        s += 1.0; parts.append("流动性合格")
    elif liq_score > 0:
        s -= 1.2; parts.append("流动性偏弱")
    if _v26_bool(row.get("v241_formal_liquidity_ok", False)):
        s += 0.8
    defense = safe_float(row.get("v20_defense_dist", row.get("defense_dist", 0)))
    if 0 < defense <= 0.07:
        s += 0.8; parts.append("执行失败线清楚")
    if safe_float(row.get("v20_chase_risk", row.get("chase_risk", 0))) > 0.1:
        s -= 1.0; parts.append("追买执行难度高")
    return _v26_clip(s, 0, 3), parts or ["执行层中性"]


def _v26_failure_similarity(row):
    # 无真实失败样本库时，先用机构风险代理：追高、滞涨、压力近、RR差、信号过期、弱市。
    pts = 0.0
    reasons = []
    rr = safe_float(row.get("v20_rr", row.get("risk_reward_ratio", row.get("rr", 0))))
    defense = safe_float(row.get("v20_defense_dist", row.get("defense_dist", 0)))
    upside = safe_float(row.get("v20_target_dist", row.get("target_dist", 0)))
    age = _v26_signal_age_days(row)
    if rr > 0 and rr < V26_MIN_RR:
        pts += 18; reasons.append("RR低于最终买入池要求")
    if defense > V26_MAX_DEFENSE_DIST:
        pts += 16; reasons.append("离防守位过远，类似追高失败样本")
    if 0 < upside < V26_MIN_UPSIDE:
        pts += 14; reasons.append("上方空间偏窄")
    if age > V26_MAX_SIGNAL_AGE_DAYS:
        pts += 14; reasons.append("信号生命周期偏老")
    if safe_float(row.get("v20_chase_risk", row.get("chase_risk", 0))) > 0.10:
        pts += 14; reasons.append("追高风险高")
    if _v26_regime() in ["bear", "weak", "panic", "crash"]:
        pts += 10; reasons.append("市场环境弱，失败相似度上升")
    if str(row.get("v212_state", "")).find("失败") >= 0 or str(row.get("v20_tier_reason", "")).find("失败") >= 0:
        pts += 12; reasons.append("已有失败/假突破提示")
    return _v26_clip(pts, 0, 100), reasons or ["未命中明显历史失败代理特征"]


def _v26_freshness_score(row):
    age = _v26_signal_age_days(row)
    if age <= 3:
        return 100.0, f"信号新鲜，约{age}天"
    if age <= 8:
        return 78.0, f"信号仍在主观察窗口，约{age}天"
    if age <= V26_MAX_SIGNAL_AGE_DAYS:
        return 58.0, f"信号进入后段窗口，约{age}天"
    return 25.0, f"信号偏老/可能过期，约{age}天"


def v26_institutional_scorecard(row):
    """V26机构评分卡：输出100分最终买入池口径。只后置增强，不破坏原模型字段。"""
    r = dict(row)
    if V26_ENABLED != "1":
        r["v26_enabled"] = False
        return r
    cards = {}
    reasons = {}
    for name, func in [
        ("explosion_eve", _v26_card_explosion_eve),
        ("key_structure", _v26_card_key_structure),
        ("supply_absorption", _v26_card_supply_absorption),
        ("acceptance", _v26_card_acceptance),
        ("breakout_expansion", _v26_card_breakout_expansion),
        ("pricing", _v26_card_pricing),
        ("sector", _v26_card_sector),
        ("market", _v26_card_market),
        ("execution", _v26_card_execution),
    ]:
        try:
            sc, rs = func(r)
        except Exception as e:
            sc, rs = 0.0, [f"{name}评分异常：{str(e)[:60]}"]
        cards[name] = round(float(sc), 2)
        reasons[name] = rs
    fail_sim, fail_reasons = _v26_failure_similarity(r)
    fresh_score, fresh_text = _v26_freshness_score(r)
    freshness_points = _v26_clip(fresh_score / 100.0 * 3.0, 0, 3)
    failure_points = _v26_clip((100.0 - fail_sim) / 100.0 * 5.0, 0, 5)
    # 10个母因子满分100：20+15+12+12+12+12+6+5+3+8（失败相似度5+新鲜度3）
    raw = sum(cards.values()) + freshness_points + failure_points
    # 与旧融合分做轻度校准：旧模型只作为结构识别底座，V26才是最终买入池口径。
    # V26.2：旧分权重从隐性28%收敛为可配置小权重，避免旧模型堆分把观察票推成买入票。
    legacy = safe_float(r.get("v22_composite_trade_score", r.get("v212_final_score", r.get("v20_final_score", 0))))
    legacy_w = max(0.0, min(0.30, safe_float(globals().get("V26_LEGACY_BLEND_WEIGHT", 0.15), 0.15)))
    if legacy > 0 and legacy_w > 0:
        final = raw * (1.0 - legacy_w) + legacy * legacy_w
    else:
        final = raw
    # 环境硬调节：恐慌直接封顶，弱市封顶，避免好结构在坏环境里被误推重仓。
    regime = _v26_regime()
    if regime in ["panic", "crash"]:
        final = min(final, 68.0)
    elif regime in ["bear", "weak"]:
        final = min(final, 84.0)
    final = _v26_clip(final, 0, 100)

    rr = safe_float(r.get("v20_rr", r.get("risk_reward_ratio", r.get("rr", 0))))
    defense = safe_float(r.get("v20_defense_dist", r.get("defense_dist", 0)))
    upside = safe_float(r.get("v20_target_dist", r.get("target_dist", 0)))
    liq_ok = _v26_bool(r.get("v241_formal_liquidity_ok", True))
    hard_risk = bool(r.get("v14_blocked", False)) or bool(r.get("exclude_from_final", False)) or str(r.get("v20_trade_tier", "")).startswith("硬风险")
    invalid = bool(r.get("v20_trade_invalidated", False))
    formal_ok = True
    block_reasons = []
    # V26.2母因子硬门槛：正式买入池不能只靠旧综合分或同源信号堆分。
    core_mother_score = (
        safe_float(cards.get("explosion_eve", 0))
        + safe_float(cards.get("key_structure", 0))
        + safe_float(cards.get("supply_absorption", 0))
        + safe_float(cards.get("acceptance", 0))
        + safe_float(cards.get("breakout_expansion", 0))
        + safe_float(cards.get("pricing", 0))
        + safe_float(cards.get("execution", 0))
    )
    acceptance_or_breakout = max(safe_float(cards.get("acceptance", 0)), safe_float(cards.get("breakout_expansion", 0)), safe_float(cards.get("key_structure", 0)))
    if hard_risk:
        formal_ok = False; block_reasons.append("命中硬风险/综合分无效剔除")
    if invalid:
        formal_ok = False; block_reasons.append(str(r.get("v20_trade_invalid_reason", "交易假设失效")))
    if core_mother_score < V26_MIN_CORE_MOTHER_SCORE:
        formal_ok = False; block_reasons.append(f"V26核心母因子{core_mother_score:.1f}低于{V26_MIN_CORE_MOTHER_SCORE:.0f}，仅作观察")
    if safe_float(cards.get("pricing", 0)) < V26_MIN_PRICING_CARD:
        formal_ok = False; block_reasons.append("定价/RR母因子不足，不能进入最终买入池")
    if safe_float(cards.get("execution", 0)) < V26_MIN_EXECUTION_CARD:
        formal_ok = False; block_reasons.append("执行/买点母因子不足，不能进入最终买入池")
    if acceptance_or_breakout < V26_MIN_ACCEPTANCE_OR_BREAKOUT_CARD:
        formal_ok = False; block_reasons.append("承接/突破/核心结构至少一项确认不足")
    if final < V26_MIN_BUY_SCORE:
        formal_ok = False; block_reasons.append(f"V26最终买入池分{final:.1f}< {V26_MIN_BUY_SCORE:.0f}")
    if rr > 0 and rr < V26_MIN_RR:
        formal_ok = False; block_reasons.append(f"RR={rr:.2f}低于{V26_MIN_RR:.2f}")
    if defense > V26_MAX_DEFENSE_DIST:
        formal_ok = False; block_reasons.append(f"防守距离{defense:.1%}偏远")
    if 0 < upside < V26_MIN_UPSIDE:
        formal_ok = False; block_reasons.append(f"上方空间{upside:.1%}不足")
    if not liq_ok:
        formal_ok = False; block_reasons.append(str(r.get("v241_liquidity_reason", "流动性未达正式门槛")))
    if fail_sim > V26_MAX_FAILURE_SIM:
        formal_ok = False; block_reasons.append(f"失败相似度{fail_sim:.0f}过高")
    if _v26_signal_age_days(r) > V26_MAX_SIGNAL_AGE_DAYS:
        formal_ok = False; block_reasons.append("信号生命周期过期")
    if regime in ["panic", "crash"]:
        formal_ok = False; block_reasons.append("系统性风险/恐慌环境，最终买入池关闭")

    if not formal_ok:
        position = "观察仓"
        pos_pct = 0.0
    elif final >= V26_STANDARD_SCORE and rr >= 2.2 and fail_sim <= 35 and regime not in ["bear", "weak"]:
        position = "重仓候选"
        pos_pct = 0.18
    elif final >= V26_STRONG_CONFIRM_SCORE and rr >= 1.8:
        position = "标准仓"
        pos_pct = 0.10
    else:
        position = "试仓"
        pos_pct = 0.05
    # 继承V24.1市场乘数，但只作为仓位，不改变是否入池。
    try:
        mult, mult_text = v241_market_regime_multiplier()
        pos_pct = round(max(0.0, min(0.20, pos_pct * float(mult))), 4)
    except Exception:
        mult_text = "仓位环境乘数未计算"

    r.update({
        "v26_enabled": True,
        "v26_scorecards": cards,
        "v26_reasons": reasons,
        "v26_raw_score": round(raw, 2),
        "v26_legacy_score": round(legacy, 2),
        "v26_legacy_blend_weight": round(legacy_w, 4),
        "v26_core_mother_score": round(core_mother_score, 2),
        "v26_acceptance_or_breakout_score": round(acceptance_or_breakout, 2),
        "v26_final_buy_score": round(final, 2),
        "v26_formal_buy_ok": bool(formal_ok),
        "v26_pool_classification": "最终买入池" if formal_ok else "高质量观察/未入买入池",
        "v26_block_reasons": block_reasons,
        "v26_failure_similarity": round(fail_sim, 2),
        "v26_failure_similarity_reasons": fail_reasons,
        "v26_signal_age_days": _v26_signal_age_days(r),
        "v26_signal_freshness_score": round(fresh_score, 2),
        "v26_signal_freshness_text": fresh_text,
        "v26_sector": _v26_sector(r),
        "v26_hypothesis": _v26_hypothesis(r),
        "v26_position_tier": position,
        "v26_position_pct": pos_pct,
        "v26_position_reason": mult_text,
        "v26_self_learning_mode": V26_AUTO_LEARN_MODE,
        "v26_self_learning_note": "半自动自学习：记录T+1/T+3/T+5/T+8/T+13/T+20结果和调参建议，不自动大幅改权重。",
        "v26_dedupe_note": "母因子打分、子信号解释；同源信号组内封顶，避免重复堆分。",
    })
    return r


def v26_portfolio_accept(row, selected):
    """Top5组合去相关：不让最终买入池全部变成同一板块/同一假设。"""
    if V26_ENABLE_PORTFOLIO_DECORRELATION != "1":
        return True, "组合去相关关闭"
    sec = str(row.get("v26_sector", _v26_sector(row)))
    hyp = str(row.get("v26_hypothesis", _v26_hypothesis(row)))
    same_sec = sum(1 for x in selected if str(x.get("v26_sector", _v26_sector(x))) == sec)
    same_hyp = sum(1 for x in selected if str(x.get("v26_hypothesis", _v26_hypothesis(x))) == hyp)
    if same_sec >= V26_MAX_SAME_SECTOR:
        return False, f"组合约束：{sec}已达到{V26_MAX_SAME_SECTOR}只"
    if same_hyp >= V26_MAX_SAME_HYPOTHESIS:
        return False, f"组合约束：{hyp}已达到{V26_MAX_SAME_HYPOTHESIS}只"
    return True, "组合暴露可接受"


def v26_scorecard_report_line(row):
    cards = row.get("v26_scorecards", {}) if isinstance(row.get("v26_scorecards", {}), dict) else {}
    order = ["explosion_eve", "key_structure", "supply_absorption", "acceptance", "breakout_expansion", "pricing", "sector", "market", "execution"]
    return "｜".join([f"{k}:{safe_float(cards.get(k,0)):.1f}" for k in order])


def v26_apply_to_row(row):
    """兼容V20最终选择出口的V26包装器：调用原V26评分卡，并写入统一别名字段。"""
    r = v26_institutional_scorecard(row)
    cards = r.get("v26_scorecards", {}) if isinstance(r.get("v26_scorecards", {}), dict) else {}
    block_reasons = r.get("v26_block_reasons", [])
    if isinstance(block_reasons, list):
        block_text = "；".join([str(x) for x in block_reasons if str(x).strip()])
    else:
        block_text = str(block_reasons or "")
    r["v26_buy_eligible"] = bool(r.get("v26_formal_buy_ok", False))
    r["v26_hard_gate_pass"] = bool(r.get("v26_formal_buy_ok", False))
    r["v26_hard_gate_reasons"] = block_text
    r["v26_explosion_eve_score"] = safe_float(cards.get("explosion_eve", 0))
    r["v26_key_structure_score"] = safe_float(cards.get("key_structure", 0))
    r["v26_supply_absorption_mother_score"] = safe_float(cards.get("supply_absorption", 0))
    r["v26_support_defense_score"] = safe_float(cards.get("acceptance", 0))
    r["v26_breakout_expansion_score"] = safe_float(cards.get("breakout_expansion", 0))
    r["v26_pricing_rr_score"] = safe_float(cards.get("pricing", 0))
    r["v26_sector_lifecycle_score"] = safe_float(cards.get("sector", 0))
    r["v26_market_score"] = safe_float(cards.get("market", 0))
    r["v26_execution_score"] = safe_float(cards.get("execution", 0))
    r["v26_failure_similarity_risk"] = safe_float(r.get("v26_failure_similarity", 0))
    r["v26_signal_age"] = r.get("v26_signal_age_days", "")
    r["v26_same_source_dedup_note"] = r.get("v26_dedupe_note", "")
    return r



# ========================= V27.0 选股逻辑主引擎重构：A股海选 + 华尔街深度定价 START =========================
# 生产层锁死说明：本段只新增选股评分/排序/分流逻辑，不触碰入口、workflow、缓存、数据门控、BaoStock、Telegram、PAT、artifact。
# 架构原则：基础层只有召回权；深度层拥有定价权和否决权；最终买入池只接受正期望交易。
V27_ENABLE_CORE_ENGINE = os.environ.get("V27_ENABLE_CORE_ENGINE", "1")
V27_ENABLE_BASE_RECALL_OVERLAY = os.environ.get("V27_ENABLE_BASE_RECALL_OVERLAY", "1")
V27_ENABLE_FINAL_GATE = os.environ.get("V27_ENABLE_FINAL_GATE", "1")
V27_MIN_BUY_SCORE = float(os.environ.get("V27_MIN_BUY_SCORE", os.environ.get("V26_MIN_BUY_SCORE", "80")))
V27_BASE_TRIGGER_MIN_FOR_DEEP = float(os.environ.get("V27_BASE_TRIGGER_MIN_FOR_DEEP", "5.5"))
V27_DEEP_TRIGGER_MIN = float(os.environ.get("V27_DEEP_TRIGGER_MIN", "7.0"))
V27_DEEP_STRUCTURE_MIN = float(os.environ.get("V27_DEEP_STRUCTURE_MIN", "7.0"))
V27_DEEP_FUND_MIN = float(os.environ.get("V27_DEEP_FUND_MIN", "7.0"))
V27_CHASE_HARD_BLOCK_PCT5 = float(os.environ.get("V27_CHASE_HARD_BLOCK_PCT5", "0.18"))
V27_CHASE_HARD_BLOCK_BIAS20 = float(os.environ.get("V27_CHASE_HARD_BLOCK_BIAS20", "0.18"))
V27_MAX_DEFENSE_DIST = float(os.environ.get("V27_MAX_DEFENSE_DIST", os.environ.get("V26_MAX_DEFENSE_DIST", "0.095")))
V27_MAX_SOFT_DEFENSE_DIST = float(os.environ.get("V27_MAX_SOFT_DEFENSE_DIST", "0.08"))
V27_MIN_UPSIDE = float(os.environ.get("V27_MIN_UPSIDE", os.environ.get("V26_MIN_UPSIDE", "0.08")))
V27_MIN_RR = float(os.environ.get("V27_MIN_RR", os.environ.get("V26_MIN_RR", "1.35")))
V27_NEAR_PRESSURE_BLOCK = float(os.environ.get("V27_NEAR_PRESSURE_BLOCK", os.environ.get("V26_MAX_NEAR_PRESSURE", "0.05")))
V27_BASE_SCORE_FILE = os.environ.get("V27_BASE_SCORE_FILE", "v27_base_recall_overlay.json")


def _v27_clip(x, lo=0.0, hi=100.0):
    try:
        x = float(x)
    except Exception:
        x = lo
    if x != x:
        x = lo
    return max(lo, min(hi, x))


def _v27_norm(x, src_hi, dst_hi):
    src_hi = float(src_hi) if src_hi else 1.0
    return _v27_clip(safe_float(x, 0) / src_hi * float(dst_hi), 0, float(dst_hi))


def _v27_any_text(row, keys):
    vals = []
    for k in keys:
        v = row.get(k, "")
        if isinstance(v, (list, tuple)):
            vals.extend([str(x) for x in v if str(x).strip()])
        elif str(v).strip():
            vals.append(str(v))
    return "；".join(vals)


def _v27_base_factor_funds(row):
    """资金因子：资金攻击、承接、持续、效率、失败反证；海选层只做召回，不做买入结论。"""
    reasons = []
    attack = 0.0
    carry = 0.0
    sustain = 0.0
    eff = 0.0
    fail = 0.0
    # 攻击：不能让单日强攻无限加分。
    if safe_float(row.get("base_attack_quality_score", 0)) >= 22:
        attack += 2.5; reasons.append("资金攻击K质量强")
    elif safe_float(row.get("base_attack_quality_score", 0)) >= 14:
        attack += 1.5; reasons.append("资金攻击K质量尚可")
    if safe_float(row.get("base_big_bull7_count_100", 0)) >= 4:
        attack += 1.5; reasons.append("近100日多次7%大阳攻击记忆")
    elif safe_float(row.get("base_big_bull7_count_100", 0)) >= 2:
        attack += 0.8; reasons.append("近100日存在7%大阳攻击记忆")
    if safe_float(row.get("base_gap_count_100", 0)) >= 2:
        attack += 0.6; reasons.append("存在跳空攻击记忆")
    if safe_float(row.get("base_fibo_second_confirm_score", 0)) >= 6:
        attack += 1.0; reasons.append("首倍高点二次确认入口")

    # 承接：资金因子第一优先，不奖励一日游。
    carry += _v27_norm(row.get("base_volume_carry_score", 0), 15, 4.0)
    if safe_float(row.get("flat_volume_count_60_base", 0)) >= 1:
        carry += 1.6; reasons.append("倍量后平量/平量承接记忆")
    if safe_float(row.get("base_limitup_hold_score", row.get("base_limitup_hold_3d", 0))) > 0:
        carry += 1.2; reasons.append("涨停后三日实体承接线索")
    if safe_float(row.get("base_observe_k_repair_score", 0)) >= 4:
        carry += 1.0; reasons.append("K线修复/承接记忆")

    # 持续性/效率。
    sustain += min(3.0, safe_float(row.get("base_observe_fund_event_score", 0)) * 0.35)
    if safe_float(row.get("beiliang_count_60_base", 0)) >= 2:
        sustain += 1.0; reasons.append("分散健康倍量记忆")
    if safe_float(row.get("base_up_down_vol_ratio_60", 0)) >= 1.05:
        eff += 1.2; reasons.append("阳量强于阴量")
    if safe_float(row.get("base_up_down_vol_ratio_40", 0)) >= 1.10:
        eff += 0.8
    if safe_float(row.get("base_observe_price_attack_score", 0)) >= 5:
        eff += 0.8; reasons.append("价格攻击效率较好")

    # 失败反证：多次假突破/放量滞涨/长上影供给。
    risk_active = safe_float(row.get("base_observe_risk_active_penalty", 0))
    risk_penalty = safe_float(row.get("base_risk_penalty", 0))
    if risk_active < 0:
        fail += abs(risk_active) * 0.75; reasons.append("放量长上影/滞涨等失败记忆扣分")
    if risk_penalty < -8:
        fail += min(4.0, abs(risk_penalty) * 0.25); reasons.append("基础追高/量价风险扣分")
    if safe_float(row.get("base_big_yin_count_100", 0)) >= safe_float(row.get("base_big_yang_count_100", 0)) + 2:
        fail += 2.0; reasons.append("大阴攻击多于大阳攻击")

    score = _v27_clip(attack + carry + sustain + eff - fail, 0, 25)
    return score, reasons or ["资金因子中性：未发现强攻击/承接记忆"]


def _v27_base_factor_structure(row):
    reasons = []
    s = 0.0
    s += _v27_norm(row.get("base_structure_potential_score", 0), 22, 7.0)
    s += _v27_norm(row.get("base_long_cycle_potential_score", 0), 10, 3.0)
    s += _v27_norm(row.get("base_supply_pressure_clarity_score", 0), 10, 3.0)
    if safe_float(row.get("base_supply_dist_to_upper", 999)) <= 0.06 and safe_float(row.get("base_supply_core_upper", 0)) > 0:
        s += 2.0; reasons.append("接近供需/核心压力上沿")
    if bool(row.get("base_supply_absorption_valid", False)):
        s += 2.0; reasons.append("供应吸收结构有效")
    if bool(row.get("base_explosion_eve_valid", False)):
        s += 1.5; reasons.append("爆发前夜结构有效")
    if safe_float(row.get("base_fibo_second_confirm_score", 0)) >= 6:
        s += 1.0; reasons.append("黄金倍量/首倍结构入口")
    if not reasons and safe_float(row.get("base_structure_potential_score", 0)) > 0:
        reasons.append("平台/凹口/结构潜力存在")
    return _v27_clip(s, 0, 20), reasons or ["核心结构证据一般"]


def _v27_base_factor_explosion(row):
    reasons = []
    s = 0.0
    s += _v27_norm(row.get("base_channel_explosion_eve_score", 0), 30, 8.0)
    s += _v27_norm(row.get("base_channel_supply_absorption_score", 0), 30, 5.0)
    s += _v27_norm(row.get("base_supply_absorb_context_score", 0), 9, 2.0)
    s += _v27_norm(row.get("base_supply_volume_platform_score", 0), 7, 2.0)
    if safe_float(row.get("base_observation_subscore", 0)) >= 7:
        s += 1.0; reasons.append("观察值显示资金/结构/修复记忆较好")
    if bool(row.get("base_explosion_eve_valid", False)):
        s += 1.5; reasons.append("爆发前夜有效召回")
    if safe_float(row.get("base_explosion_eve_penalty", 0)) < 0:
        s += max(-2.0, safe_float(row.get("base_explosion_eve_penalty", 0)) * 0.4)
        reasons.append("爆发前夜存在追高/滞涨反证")
    return _v27_clip(s, 0, 20), reasons or ["爆发前夜证据不足"]


def _v27_base_factor_trigger(row):
    reasons = []
    s = 0.0
    s += _v27_norm(row.get("base_supply_compression_trigger_score", 0), 6, 3.0)
    if safe_float(row.get("break_rate", 0)) >= 0.005 and safe_float(row.get("break_rate", 0)) <= 0.035:
        s += 3.0; reasons.append("当前处于健康突破/临界触发")
    elif safe_float(row.get("break_rate", 0)) > 0.06:
        s -= 2.0; reasons.append("突破过远，触发降权")
    if bool(row.get("platform20_break_base", False)) or bool(row.get("platform40_break_base", False)):
        s += 2.0; reasons.append("平台上沿触发")
    if safe_float(row.get("base_trade_quality_score", 0)) >= 8:
        s += 2.0; reasons.append("基础买点质量较好")
    if 0 < safe_float(row.get("base_defense_dist", 0)) <= 0.07:
        s += 1.5; reasons.append("离基础防守位不远")
    if 0 < safe_float(row.get("near_pressure_dist", 0)) < 0.05:
        s -= 2.0; reasons.append("近端压力贴脸，触发降权")
    if safe_float(row.get("bias20", 0)) > 0.15:
        s -= 1.5; reasons.append("20日乖离偏高，防追涨")
    return _v27_clip(s, 0, 15), reasons or ["当前触发窗口不明显"]


def _v27_base_factor_market(row):
    reasons = []
    s = 0.0
    # 无板块实时数据时，使用相对强弱/市场环境代理；缺失中性，不误杀。
    if safe_float(row.get("base_activity_memory_score", 0)) >= 4:
        s += 2.0; reasons.append("股性活跃度适合A股短线生态")
    elif safe_float(row.get("base_activity_memory_score", 0)) <= -3:
        s -= 1.5; reasons.append("股性偏黏/活跃度不足")
    if safe_float(row.get("base_long_cycle_potential_score", 0)) > 0 and safe_float(row.get("long_pos_250", 0)) <= 0.65:
        s += 1.5; reasons.append("相对位置适合中低位修复")
    if _v26_regime() in ["bear", "weak", "panic", "crash"]:
        s -= 2.0; reasons.append("市场环境偏弱，海选降权")
    else:
        s += 2.0; reasons.append("市场环境中性/可交易")
    return _v27_clip(s + 4.0, 0, 10), reasons


def _v27_base_factor_risk_trade(row):
    reasons = []
    s = 10.0
    if safe_float(row.get("base_risk_penalty", 0)) < 0:
        s += safe_float(row.get("base_risk_penalty", 0)) * 0.45; reasons.append("基础风险扣分")
    if safe_float(row.get("base_observe_risk_active_penalty", 0)) < 0:
        s += safe_float(row.get("base_observe_risk_active_penalty", 0)) * 0.55; reasons.append("失败记忆/滞涨风险扣分")
    if safe_float(row.get("base_defense_dist", 0)) > 0.10:
        s -= 2.0; reasons.append("距离基础防守位过远")
    if 0 < safe_float(row.get("base_target_dist", 0)) < 0.06:
        s -= 2.0; reasons.append("上方第一空间偏窄")
    if safe_float(row.get("base_rsi", 50)) > 82 or safe_float(row.get("base_cci", 0)) > 260:
        s -= 1.5; reasons.append("短线过热")
    return _v27_clip(s, 0, 10), reasons or ["基础可交易风险中性"]


def v27_base_recall_overlay(row):
    """V27基础层：六大一级因子，只负责深度召回，不输出买入结论。"""
    if V27_ENABLE_CORE_ENGINE != "1" or V27_ENABLE_BASE_RECALL_OVERLAY != "1":
        return row
    r = dict(row)
    f1, r1 = _v27_base_factor_funds(r)
    f2, r2 = _v27_base_factor_structure(r)
    f3, r3 = _v27_base_factor_explosion(r)
    f4, r4 = _v27_base_factor_trigger(r)
    f5, r5 = _v27_base_factor_market(r)
    f6, r6 = _v27_base_factor_risk_trade(r)
    # 100分海选召回口径：资金25、结构20、爆发前夜20、当前触发15、市场10、可交易10。
    total = f1 + f2 + f3 + f4 + f5 + f6
    # 当前触发不足但历史记忆好：进入种子池，不抢深度前排。
    if f4 < V27_BASE_TRIGGER_MIN_FOR_DEEP and total >= 60:
        priority = total * 0.72
        tier = "V27种子池：历史记忆好但当前触发不足"
    else:
        priority = total
        tier = "V27深度池候选" if total >= 55 and f4 >= V27_BASE_TRIGGER_MIN_FOR_DEEP else "V27普通观察"
    r.update({
        "v27_base_fund_factor": round(f1, 2),
        "v27_base_structure_factor": round(f2, 2),
        "v27_base_explosion_factor": round(f3, 2),
        "v27_base_current_trigger_factor": round(f4, 2),
        "v27_base_market_factor": round(f5, 2),
        "v27_base_risk_trade_factor": round(f6, 2),
        "v27_base_recall_score": round(total, 2),
        "v27_base_deep_priority_score": round(priority, 2),
        "v27_base_tier": tier,
        "v27_base_reason": "｜".join((r1[:2] + r2[:2] + r3[:2] + r4[:2] + r6[:2])[:8]),
        "v27_base_dedup_note": "21个海选维度合并为6个一级因子；K线组合归入资金事件簇；同源组内封顶，基础层只召回不买入。",
    })
    return r


def _v27_deep_factor_funds(row):
    reasons = []
    s = 0.0
    # 资金因子：攻击、承接、持续、效率、失败反证。
    s += _v27_norm(row.get("v201_volume_behavior", 0), 15, 5.0)
    if safe_float(row.get("v212_acceptance_score", 0)) > 0:
        s += min(4.0, safe_float(row.get("v212_acceptance_score", 0)) * 0.35); reasons.append("V21.2承接确认")
    if safe_float(row.get("v26_support_defense_score", 0)) > 0:
        s += min(3.5, safe_float(row.get("v26_support_defense_score", 0)) * 0.35); reasons.append("V26承接/防守确认")
    for k, label, w in [
        ("flat_volume_score", "平量承接", 0.6),
        ("volume_after_flat_acceptance_score", "倍量后平量承接", 0.8),
        ("score_double_yang_sandwich", "双阳夹阴/多方炮", 0.7),
        ("base_fibo_second_confirm_score", "首倍二次确认", 0.25),
        ("v23_supply_absorption_score", "供应吸收资金", 0.22),
    ]:
        val = safe_float(row.get(k, 0))
        if val > 0:
            s += min(2.5, val * w); reasons.append(label)
    if safe_float(row.get("v20_chase_risk", row.get("chase_risk", 0))) > 0.10:
        s -= 3.0; reasons.append("资金强但追涨风险高，资金效率降权")
    if _v26_bool(row.get("is_bad_stall", False)) or safe_float(row.get("stall_risk_score", 0)) > 0:
        s -= 3.5; reasons.append("放量滞涨/供应压制")
    return _v27_clip(s, 0, 25), reasons or ["深度资金因子中性"]


def _v27_deep_factor_structure(row):
    reasons = []
    s = 0.0
    s += _v27_norm(row.get("v201_structure_position", 0), 15, 6.0)
    s += _v27_norm(row.get("v201_pressure_support", 0), 15, 4.0)
    s += _v27_norm(row.get("v26_key_structure_score", 0), 15, 4.0)
    for k, label, w in [
        ("xhu_coreline_core_score", "核心压力/支撑线", 0.20),
        ("v201_v256_core_score", "V25.6核心线", 0.18),
        ("monthly_repair_score", "大周期修复", 0.25),
        ("notch_score", "凹口/平台", 0.30),
        ("v23_supply_absorption_score", "大级别供应吸收", 0.16),
    ]:
        val = safe_float(row.get(k, 0))
        if val > 0:
            s += min(2.5, val * w); reasons.append(label)
    return _v27_clip(s, 0, 20), reasons or ["结构因子一般"]


def _v27_deep_factor_explosion(row):
    reasons = []
    s = 0.0
    s += _v27_norm(row.get("v26_explosion_eve_score", 0), 20, 6.0)
    s += _v27_norm(row.get("v26_supply_absorption_mother_score", 0), 12, 4.0)
    s += _v27_norm(row.get("v23_supply_absorption_score", 0), 20, 4.0)
    for k, label, w in [
        ("compression_score", "量价压缩", 0.50),
        ("platform_volume_lift_score", "平台量能均值抬升", 0.45),
        ("flat_volume_score", "平量稳定", 0.35),
        ("v231_shadow_acceptance_score", "上影供应接受", 0.25),
    ]:
        val = safe_float(row.get(k, 0))
        if val > 0:
            s += min(2.5, val * w); reasons.append(label)
    if safe_float(row.get("v20_chase_risk", row.get("chase_risk", 0))) > 0.10:
        s -= 2.5; reasons.append("已远离爆发前夜，防追涨降权")
    return _v27_clip(s, 0, 20), reasons or ["爆发前夜深度证据不足"]


def _v27_deep_factor_trigger(row):
    reasons = []
    s = 0.0
    s += _v27_norm(row.get("v201_trigger_confirmation", 0), 15, 4.0)
    action = str(row.get("v212_action", ""))
    if action.startswith("V21.2正式"):
        s += 3.0; reasons.append("V21.2正式触发")
    if safe_float(row.get("v26_breakout_expansion_score", 0)) >= 5:
        s += 2.0; reasons.append("突破扩张信号")
    if safe_float(row.get("v26_support_defense_score", 0)) >= 5:
        s += 2.0; reasons.append("回踩/承接确认")
    if _v26_bool(row.get("v201_precise_trigger_valid", False)):
        s += 1.5; reasons.append("低量精准触发线有效")
    defense = safe_float(row.get("v20_defense_dist", row.get("defense_dist", 0)))
    if 0 < defense <= 0.06:
        s += 1.0; reasons.append("触发后仍接近防守位")
    if safe_float(row.get("v20_chase_risk", row.get("chase_risk", 0))) > 0.10:
        s -= 3.0; reasons.append("触发已远离，追涨降权")
    return _v27_clip(s, 0, 15), reasons or ["当前触发不足"]


def _v27_deep_factor_market(row):
    reasons = []
    s = 0.0
    s += _v27_norm(row.get("v26_sector_lifecycle_score", 0), 6, 3.0)
    s += _v27_norm(row.get("v26_market_score", 0), 5, 2.5)
    if safe_float(row.get("sector_heat_score", 0)) >= 60:
        s += 1.5; reasons.append("板块热度配合")
    if safe_float(row.get("relative_strength_score", 0)) > 0:
        s += min(2.0, safe_float(row.get("relative_strength_score", 0)) * 0.25); reasons.append("相对强弱配合")
    if _v26_regime() in ["bear", "weak", "panic", "crash"]:
        s -= 2.0; reasons.append("市场环境弱，降低成功率假设")
    return _v27_clip(s + 2.0, 0, 10), reasons or ["市场/板块按中性处理"]


def _v27_deep_factor_risk_trade(row):
    reasons = []
    s = 10.0
    rr = safe_float(row.get("v20_rr", row.get("risk_reward_ratio", row.get("rr", 0))))
    defense = safe_float(row.get("v20_defense_dist", row.get("defense_dist", 0)))
    upside = safe_float(row.get("v20_target_dist", row.get("target_dist", 0)))
    nearp = safe_float(row.get("v20_near_pressure", row.get("near_pressure_dist", 0)))
    if rr >= 2.0:
        s += 2.0; reasons.append("RR优秀")
    elif rr >= V27_MIN_RR:
        s += 0.8; reasons.append("RR合格")
    elif rr > 0:
        s -= 3.0; reasons.append("RR不合格")
    if 0 < defense <= 0.055:
        s += 1.5; reasons.append("防守位舒服")
    elif defense > V27_MAX_DEFENSE_DIST:
        s -= 4.0; reasons.append("防守位过远")
    if upside >= 0.15:
        s += 1.2; reasons.append("上方空间较好")
    elif 0 < upside < V27_MIN_UPSIDE:
        s -= 3.0; reasons.append("上方空间不足")
    if 0 < nearp < V27_NEAR_PRESSURE_BLOCK:
        s -= 3.0; reasons.append("近端压力贴脸")
    if safe_float(row.get("v26_failure_similarity_risk", row.get("v26_failure_similarity", 0))) >= 45:
        s -= 2.5; reasons.append("失败相似度偏高")
    if safe_float(row.get("v241_liquidity_score", 70)) < 50:
        s -= 3.0; reasons.append("流动性/成交额不足")
    return _v27_clip(s, 0, 10), reasons or ["可交易风险中性"]


def v27_wallstreet_decision_engine(row):
    """V27深度层：华尔街定价器。旧模型只做特征库，V27负责买入池/观察池/剔除池分流。"""
    if V27_ENABLE_CORE_ENGINE != "1":
        return row
    r = dict(row)
    f1, r1 = _v27_deep_factor_funds(r)
    f2, r2 = _v27_deep_factor_structure(r)
    f3, r3 = _v27_deep_factor_explosion(r)
    f4, r4 = _v27_deep_factor_trigger(r)
    f5, r5 = _v27_deep_factor_market(r)
    f6, r6 = _v27_deep_factor_risk_trade(r)
    score = _v27_clip(f1 + f2 + f3 + f4 + f5 + f6, 0, 100)

    rr = safe_float(r.get("v20_rr", r.get("risk_reward_ratio", r.get("rr", 0))))
    defense = safe_float(r.get("v20_defense_dist", r.get("defense_dist", 0)))
    upside = safe_float(r.get("v20_target_dist", r.get("target_dist", 0)))
    nearp = safe_float(r.get("v20_near_pressure", r.get("near_pressure_dist", 0)))
    chase = safe_float(r.get("v20_chase_risk", r.get("chase_risk", 0)))
    bias20 = safe_float(r.get("bias20", r.get("base_bias20", 0)))
    pct_chg = safe_float(r.get("pct_chg", 0))
    block = []
    observe = []

    if bool(r.get("v14_blocked", False)) or bool(r.get("risk_hard_exclude", False)):
        block.append("硬雷区/重大风险剔除")
    if bool(r.get("exclude_from_final", False)) or bool(r.get("v20_trade_invalidated", False)):
        block.append(str(r.get("v20_trade_invalid_reason", r.get("v22_invalid_reason", "交易假设已失效"))))
    if rr > 0 and rr < V27_MIN_RR:
        block.append(f"RR {rr:.2f} 低于V27最低{V27_MIN_RR:.2f}")
    if defense > V27_MAX_DEFENSE_DIST:
        block.append(f"防守距离{defense:.1%}过远，追涨风险")
    elif defense > V27_MAX_SOFT_DEFENSE_DIST:
        observe.append(f"防守距离{defense:.1%}偏远，观察降级")
    if 0 < upside < V27_MIN_UPSIDE:
        block.append(f"上方空间{upside:.1%}不足")
    if 0 < nearp < V27_NEAR_PRESSURE_BLOCK:
        block.append(f"近端压力{nearp:.1%}贴脸")
    if chase > 0.12:
        block.append(f"追高风险{chase:.1%}过高")
    elif chase > 0.09:
        observe.append("追高风险偏高，需回踩确认")
    if bias20 > V27_CHASE_HARD_BLOCK_BIAS20 and defense > 0.06:
        block.append("20日乖离偏高且防守位偏远")
    if pct_chg >= 7.0 and defense > 0.075 and f4 < 10:
        block.append("当日强攻但防守位远、触发质量不足")
    if f4 < V27_DEEP_TRIGGER_MIN:
        observe.append("当前触发不足：基础好但买点未成熟")
    if f2 < V27_DEEP_STRUCTURE_MIN:
        observe.append("核心结构质量不足，不能作为正式买入主因")
    if f1 < V27_DEEP_FUND_MIN:
        observe.append("资金有效性/承接不足")

    formal_ok = (score >= V27_MIN_BUY_SCORE) and not block and f4 >= V27_DEEP_TRIGGER_MIN and f2 >= V27_DEEP_STRUCTURE_MIN and f1 >= V27_DEEP_FUND_MIN
    if formal_ok:
        pool = "V27最终买入池"
    elif block:
        pool = "V27剔除/禁止买入"
    else:
        pool = "V27观察池"

    # 交易假设分类：先分类，再定价。
    if f3 >= 13 and f4 >= 7:
        hypo = "爆发前夜临界触发"
    elif f2 >= 13 and f4 >= 9:
        hypo = "核心结构位突破/回踩确认"
    elif f1 >= 15 and f4 >= 8:
        hypo = "资金承接后转强"
    elif f3 >= 12 and f4 < 7:
        hypo = "爆发前夜种子，等待触发"
    elif chase > 0.10 or defense > V27_MAX_SOFT_DEFENSE_DIST:
        hypo = "强攻后追涨风险观察"
    else:
        hypo = "综合结构观察"

    r.update({
        "v27_enabled": True,
        "v27_fund_factor": round(f1, 2),
        "v27_structure_factor": round(f2, 2),
        "v27_explosion_eve_factor": round(f3, 2),
        "v27_current_trigger_factor": round(f4, 2),
        "v27_market_factor": round(f5, 2),
        "v27_risk_trade_factor": round(f6, 2),
        "v27_final_score": round(score, 2),
        "v27_buy_eligible": bool(formal_ok),
        "v27_pool": pool,
        "v27_trade_hypothesis": hypo,
        "v27_block_reasons": block,
        "v27_observe_reasons": observe,
        "v27_reason_summary": "｜".join((r1[:2] + r2[:2] + r3[:2] + r4[:2] + r6[:2])[:10]),
        "v27_dedup_note": "旧模型=特征库；V27=交易定价器。资金/结构/爆发/触发/市场/风险六大因子组内封顶，防止阳包阴、分手线、跳空等K线事件重复加分。",
        "v27_failure_path_note": "正式买入池必须满足RR、防守位、第一压力空间、当前触发、资金有效性；不满足则观察或剔除。",
    })
    return r


def v27_apply_to_row(row):
    """统一入口：深度层V27华尔街决策器。"""
    try:
        return v27_wallstreet_decision_engine(row)
    except Exception as e:
        r = dict(row)
        r["v27_error"] = str(e)[:200]
        r["v27_buy_eligible"] = False
        r["v27_pool"] = "V27评分异常观察"
        return r

# ========================= V27.0 选股逻辑主引擎重构：A股海选 + 华尔街深度定价 END =========================

# ========================= V26 END =========================


# ======================= V22.0 Signal Registry + V21.2 Unified Opportunity Engine START =======================
# 设计原则：
# 1）不删除V20.3.1旧颗粒口径；旧模型继续负责海选、深度评分、风险库、生命周期。
# 2）V21.2只做统一行为层融合：量、价、时、空、执行、股性。
# 3）供需压力带只是结构因子，不是唯一主轴；高质量突破只是加分项，不是唯一入口。
# 4）正式Top更强调“确定性可交易机会”：预判仓/确认仓/失败线/目标概率必须写清楚。

V212_ENABLED = os.environ.get("V212_ENABLED", "1")
V212_OUTPUT_FILE = os.environ.get("V23_OUTPUT_FILE", os.environ.get("V22_OUTPUT_FILE", os.environ.get("V212_OUTPUT_FILE", "v23_1_full_score_cards.json")))
V212_DAILY_REPORT_FILE = os.environ.get("V23_DAILY_REPORT_FILE", os.environ.get("V22_DAILY_REPORT_FILE", os.environ.get("V212_DAILY_REPORT_FILE", "v23_1_full_daily_report.txt")))
V212_MIN_FORMAL_SCORE = float(os.environ.get("V22_MIN_FORMAL_SCORE", os.environ.get("V212_MIN_FORMAL_SCORE", "78")))
V212_MAX_PREDICT_RISK = float(os.environ.get("V22_MAX_PREDICT_RISK", os.environ.get("V212_MAX_PREDICT_RISK", "0.075")))
V212_MAX_CONFIRM_RISK = float(os.environ.get("V22_MAX_CONFIRM_RISK", os.environ.get("V212_MAX_CONFIRM_RISK", "0.085")))
V212_MIN_RR_FORMAL = float(os.environ.get("V22_MIN_RR_FORMAL", os.environ.get("V212_MIN_RR_FORMAL", "1.70")))
V212_TARGET_PUSH_LIMIT = int(os.environ.get("V22_TARGET_PUSH_LIMIT", os.environ.get("V212_TARGET_PUSH_LIMIT", os.environ.get("TOP_PUSH_LIMIT", "5") or "3")))

# V22 信号归属登记：解决“保留好逻辑但不重复打分”的核心机制。
# owner_layer 只有一个；其他层只能引用 evidence/reference，不允许再拿满分。
V22_SIGNAL_REGISTRY = {
    "volume_standard_bull": {"owner_layer": "event", "reference_layers": ["structure", "execution"], "desc": "标准倍量阳K/健康放量启动"},
    "volume_after_flat_acceptance": {"owner_layer": "confirmation", "reference_layers": ["event", "execution"], "desc": "倍量后平量且后三日承接"},
    "platform_notch_structure": {"owner_layer": "structure", "reference_layers": ["price", "execution"], "desc": "平台/凹口/颈线结构"},
    "pressure_zone_breakout": {"owner_layer": "structure", "reference_layers": ["space", "execution"], "desc": "多周期供需压力带突破/消化"},
    "monthly_reclaim_repair": {"owner_layer": "context", "reference_layers": ["structure"], "desc": "月线BBI/BOLL中轨修复与大周期修复"},
    "gap_wick_resonance": {"owner_layer": "structure", "reference_layers": ["space"], "desc": "缺口/影线/实体共振形成供需区"},
    "risk_hard_filter": {"owner_layer": "risk", "reference_layers": ["ranking", "execution"], "desc": "基本面/监管/治理/技术硬风险前置拦截"},
    "trade_execution_plan": {"owner_layer": "execution", "reference_layers": ["report"], "desc": "确认线、失败线、仓位、目标概率"},
    "supply_absorption_regime_shift": {"owner_layer": "confirmation", "reference_layers": ["structure", "volume", "execution"], "desc": "大阴供应区突破后回踩确认与量能级别切换"},
    "long_upper_shadow_supply_acceptance": {"owner_layer": "confirmation", "reference_layers": ["structure", "volume", "execution"], "desc": "超大量长上影供应区的1/2位接受度、低点抬高、量能递减与回踩健康"},
}

def v22_signal_ownership_audit(row):
    """给报告/评分卡用的轻量审计字段：每个大类说明谁负责打分，谁只做引用。"""
    return {
        "version": "V23.1",
        "principle": "signal_owner_scores_once_reference_elsewhere",
        "score_owners": {
            "event": ["volume_standard_bull"],
            "structure": ["platform_notch_structure", "pressure_zone_breakout", "gap_wick_resonance"],
            "context": ["monthly_reclaim_repair"],
            "confirmation": ["volume_after_flat_acceptance", "supply_absorption_regime_shift", "long_upper_shadow_supply_acceptance"],
            "risk": ["risk_hard_filter"],
            "execution": ["trade_execution_plan"],
        },
        "dedupe_note": "V20负责候选质量；V21.2/V22负责交易状态，不重复给同一事件多层满分。"
    }

def v22_composite_trade_score(row):
    """最终排序分：只融合V20质量分与V21/V22交易分；不重新拆信号打分。"""
    v20 = safe_float(row.get("v20_final_score_raw", row.get("v20_final_score", 0)))
    v212 = safe_float(row.get("v212_final_score", 0))
    try:
        m = v20_trade_metrics(row)
        if bool(m.get("trade_invalidated", False)):
            return 0.0
    except Exception:
        pass
    if bool(row.get("v20_trade_invalidated", False)) or bool(row.get("exclude_from_final", False)):
        return 0.0
    risk_penalty = 0.0
    if row.get("v14_blocked") or str(row.get("v20_trade_tier", "")).startswith("硬风险"):
        risk_penalty += 100.0
    if safe_float(row.get("v212_risk_pct", 0)) > V212_MAX_CONFIRM_RISK:
        risk_penalty += 6.0
    if safe_float(row.get("v212_space_score", 0)) < 42:
        risk_penalty += 4.0
    # V20=股票/结构质量，V21.2=交易机会状态；二者合成，但风险前置。
    v23 = safe_float(row.get("v23_supply_absorption_score", 0))
    # V23供应吸收是确认层增强项，最高15分，折算为少量最终排序加成；不覆盖V20/V21。
    v23_bonus = min(6.0, v23 * 0.40)
    v231 = safe_float(row.get("v231_shadow_acceptance_score", 0))
    # V23.1长上影供应接受度是确认层增强/风险项：站上中轴并接受价格加分；反复放量长上影失败则扣分。
    v231_adjust = max(-5.0, min(4.5, v231 * 0.35))
    return round(_v212_clip(v20 * 0.52 + v212 * 0.42 + v23_bonus + v231_adjust - risk_penalty), 2)


def _v212_clip(x, lo=0.0, hi=100.0):
    try:
        x = float(x)
    except Exception:
        x = lo
    if x != x:
        x = lo
    return max(lo, min(hi, x))


def _v212_pct(a, b):
    a = safe_float(a, 0)
    b = safe_float(b, 0)
    if b <= 0:
        return 0.0
    return a / b - 1.0


def _v212_pct_dist(level, price):
    price = safe_float(price, 0)
    level = safe_float(level, 0)
    if price <= 0 or level <= 0:
        return 999.0
    return level / price - 1.0


def _v212_norm_df(df):
    if df is None or getattr(df, 'empty', True):
        return pd.DataFrame()
    x = df.copy()
    aliases = {
        'date': ['date', '日期', 'trade_date', '交易日期'],
        'open': ['open', '开盘', '开盘价'],
        'high': ['high', '最高', '最高价'],
        'low': ['low', '最低', '最低价'],
        'close': ['close', '收盘', '收盘价'],
        'volume': ['volume', 'vol', '成交量', '成交量(手)'],
        'amount': ['amount', '成交额', '成交额(元)'],
    }
    rename = {}
    for std, names in aliases.items():
        for n in names:
            if n in x.columns:
                rename[n] = std
                break
    x = x.rename(columns=rename)
    for c in ['open', 'high', 'low', 'close']:
        if c not in x.columns:
            return pd.DataFrame()
    if 'volume' not in x.columns:
        x['volume'] = 0.0
    if 'amount' not in x.columns:
        x['amount'] = x['volume'] * x['close']
    for c in ['open', 'high', 'low', 'close', 'volume', 'amount']:
        x[c] = pd.to_numeric(x[c], errors='coerce')
    if 'date' in x.columns:
        x['date'] = pd.to_datetime(x['date'], errors='coerce')
        x = x.dropna(subset=['date']).sort_values('date')
    else:
        x['date'] = pd.date_range('2000-01-01', periods=len(x), freq='B')
    x = x.dropna(subset=['open', 'high', 'low', 'close'])
    x = x[x['close'] > 0].copy()
    x['amount'] = x['amount'].fillna(x['volume'] * x['close'])
    if x.empty:
        return x
    rng = (x['high'] - x['low']).replace(0, np.nan)
    body = (x['close'] - x['open']).abs()
    top = x[['open', 'close']].max(axis=1)
    bot = x[['open', 'close']].min(axis=1)
    x['ret'] = x['close'].pct_change().fillna(0)
    x['body_ratio'] = (body / rng).fillna(0)
    x['close_pos'] = ((x['close'] - x['low']) / rng).fillna(0.5)
    x['upper_wick_ratio'] = ((x['high'] - top) / rng).fillna(0)
    x['lower_wick_ratio'] = ((bot - x['low']) / rng).fillna(0)
    x['vol_ma5'] = x['volume'].rolling(5, min_periods=2).mean()
    x['vol_ma10'] = x['volume'].rolling(10, min_periods=3).mean()
    x['vol_ma20'] = x['volume'].rolling(20, min_periods=5).mean()
    x['vol_ma60'] = x['volume'].rolling(60, min_periods=15).mean()
    x['amount_ma20'] = x['amount'].rolling(20, min_periods=5).mean()
    x['is_bull_quality'] = (x['close'] >= x['open']) & (x['close'] > x['close'].shift(1)) & (x['close_pos'] >= 0.62)
    x['is_fake_bear_bull'] = (x['close'] < x['open']) & (x['close'] > x['close'].shift(1)) & (x['close_pos'] >= 0.58)
    x['is_bad_stall'] = (x['upper_wick_ratio'] >= 0.42) & (x['close_pos'] <= 0.58) & (x['volume'] >= x['vol_ma20'] * 1.2)
    x['vol_ratio_prev'] = x['volume'] / x['volume'].shift(1).replace(0, np.nan)
    x['is_double_volume'] = (x['vol_ratio_prev'] >= 1.8) & (x['vol_ratio_prev'] <= 2.5)
    return x.reset_index(drop=True)


def _v212_resample(df, freq):
    df = _v212_norm_df(df)
    if df.empty:
        return pd.DataFrame()
    y = df.set_index('date').sort_index().resample(freq).agg({
        'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum', 'amount': 'sum'
    }).dropna(subset=['open', 'high', 'low', 'close']).reset_index()
    return _v212_norm_df(y)


def _v212_get_daily_df(row):
    """尽量只读缓存，不在最终选择阶段联网，避免输出阶段慢/不稳定。"""
    code = str(row.get('code', '') or '').zfill(6)
    if not code or code == '000000':
        return pd.DataFrame()
    try:
        bs_code = _bs_code_from_plain_code(code)
        if bs_code:
            df = read_full_history_flat_cache(bs_code, cache_scope='base', min_rows=80)
            df = _v212_norm_df(df)
            if len(df) >= 80:
                return df
    except Exception:
        pass
    # 如果深度行里本身带最近K线字段，无法构成长序列，只返回空，V21.2降级用旧字段。
    return pd.DataFrame()


def _v212_bucket_price(v, pct=0.012):
    v = safe_float(v, 0)
    if v <= 0:
        return 0.0
    step = max(v * pct, 0.01)
    return round(round(v / step) * step, 2)


def _v212_profile_zones(df, current, window=260, bucket_pct=0.012, tf='日线', weight=1.0):
    if df is None or df.empty or len(df) < 20:
        return []
    x = _v212_norm_df(df).tail(window).copy()
    if x.empty:
        return []
    tp = (x['high'] + x['low'] + x['close']) / 3.0
    x['_bucket'] = tp.apply(lambda z: _v212_bucket_price(z, bucket_pct))
    g = x.groupby('_bucket').agg(
        amount=('amount', 'sum'),
        volume=('volume', 'sum'),
        touches=('close', 'count'),
        wick=('upper_wick_ratio', 'mean'),
        high=('high', 'max'),
        low=('low', 'min'),
    ).reset_index()
    if g.empty or safe_float(g['amount'].max(), 0) <= 0:
        return []
    g['amount_pct'] = g['amount'].rank(pct=True)
    g['touch_pct'] = g['touches'].rank(pct=True)
    zones = []
    for _, r in g.sort_values(['amount_pct', 'touch_pct'], ascending=False).head(10).iterrows():
        core = safe_float(r['_bucket'], 0)
        if core <= 0:
            continue
        width = max(core * bucket_pct * 1.8, 0.03)
        strength = _v212_clip((60 * safe_float(r['amount_pct']) + 25 * safe_float(r['touch_pct']) + 15 * safe_float(r['wick'])) * weight, 0, 100)
        kind = 'supply' if core >= current * 0.98 else 'demand'
        zones.append({
            'low': round(core - width, 2),
            'high': round(core + width, 2),
            'core': round(core, 2),
            'strength': round(strength, 2),
            'kind': kind,
            'tf': tf,
            'source': 'volume_profile',
            'reason': f'{tf}成交密集桶/触碰{int(r["touches"])}'
        })
    return zones


def _v212_anchor_zones(df, current, window=260, tf='日线', weight=1.0):
    if df is None or df.empty or len(df) < 10:
        return []
    x = _v212_norm_df(df).tail(window).copy()
    zones = []
    # 最大量有效阳K高点/实体实底
    valid_bull = x[((x['is_bull_quality']) | (x['is_fake_bear_bull'])) & (x['body_ratio'] >= 0.28)]
    if not valid_bull.empty:
        row = valid_bull.loc[valid_bull['volume'].idxmax()]
        high = safe_float(row['high'])
        body_low = min(safe_float(row['open']), safe_float(row['close']))
        width = max(high * 0.014, 0.03)
        zones.append({'low': round(high-width,2), 'high': round(high+width,2), 'core': round(high,2), 'strength': _v212_clip(78*weight), 'kind': 'supply' if high >= current*0.98 else 'demand', 'tf': tf, 'source': 'max_bull_volume_high', 'reason': f'{tf}最大量有效阳K高点'})
        zones.append({'low': round(body_low-width,2), 'high': round(body_low+width,2), 'core': round(body_low,2), 'strength': _v212_clip(62*weight), 'kind': 'demand', 'tf': tf, 'source': 'max_bull_volume_body_low', 'reason': f'{tf}最大量有效阳K实体实底'})
    # 高点/次高点、长上影失败区、收盘共振
    highs = x.sort_values('high', ascending=False).head(5)
    for rank, (_, row) in enumerate(highs.iterrows(), 1):
        lv = safe_float(row['high'])
        if lv < current * 0.85:
            continue
        width = max(lv * 0.012, 0.03)
        zones.append({'low': round(lv-width,2), 'high': round(lv+width,2), 'core': round(lv,2), 'strength': _v212_clip((72-rank*4)*weight), 'kind': 'supply' if lv >= current*0.98 else 'demand', 'tf': tf, 'source': f'high_rank_{rank}', 'reason': f'{tf}阶段高点/次高点rank={rank}'})
    wick = x[(x['upper_wick_ratio'] >= 0.42) & (x['high'] >= current*0.95)]
    if len(wick) >= 2:
        lv = safe_float(wick['high'].median())
        width = max(lv * 0.018, 0.03)
        zones.append({'low': round(lv-width,2), 'high': round(lv+width,2), 'core': round(lv,2), 'strength': _v212_clip((70+min(18,len(wick)*3))*weight), 'kind': 'supply', 'tf': tf, 'source': 'upper_wick_resonance', 'reason': f'{tf}长上影/失败共振{len(wick)}次'})
    # 收盘共振，次高收盘比极端影线更稳定
    if len(x) >= 20:
        closes = x['close'].tail(min(len(x), window))
        q = closes.quantile([0.70,0.80,0.90]).tolist()
        for qv in q[-2:]:
            lv = safe_float(qv)
            if lv >= current*0.88:
                width = max(lv*0.010,0.03)
                zones.append({'low':round(lv-width,2),'high':round(lv+width,2),'core':round(lv,2),'strength':_v212_clip(64*weight),'kind':'supply' if lv>=current*0.98 else 'demand','tf':tf,'source':'close_resonance','reason':f'{tf}收盘共振区'})
    return zones


def _v212_merge_zones(zones):
    if not zones:
        return []
    zones = sorted(zones, key=lambda z: (z.get('kind',''), safe_float(z.get('low')), safe_float(z.get('high'))))
    out = []
    for z in zones:
        if not out:
            out.append(dict(z)); continue
        last = out[-1]
        if z.get('kind') == last.get('kind') and safe_float(z.get('low')) <= safe_float(last.get('high')) * 1.018:
            total = max(safe_float(last.get('strength')), safe_float(z.get('strength'))) + 3
            low = min(safe_float(last.get('low')), safe_float(z.get('low')))
            high = max(safe_float(last.get('high')), safe_float(z.get('high')))
            core = (safe_float(last.get('core')) * safe_float(last.get('strength')) + safe_float(z.get('core')) * safe_float(z.get('strength'))) / max(safe_float(last.get('strength')) + safe_float(z.get('strength')), 1)
            last.update({'low':round(low,2),'high':round(high,2),'core':round(core,2),'strength':round(_v212_clip(total),2),'tf':(str(last.get('tf',''))+'+'+str(z.get('tf','')))[:80],'source':(str(last.get('source',''))+'+'+str(z.get('source','')))[:100],'reason':(str(last.get('reason',''))+'；'+str(z.get('reason','')))[:260]})
        else:
            out.append(dict(z))
    return out


def _v212_build_zone_map(daily):
    daily = _v212_norm_df(daily)
    if daily.empty:
        return {'zones': [], 'core_supply_zone': None, 'nearest_supply': None, 'liquidity_void_score': 45}
    current = safe_float(daily['close'].iloc[-1])
    weekly = _v212_resample(daily, 'W-FRI')
    monthly = _v212_resample(daily, 'M')
    quarterly = _v212_resample(daily, 'Q')
    yearly = _v212_resample(daily, 'Y')
    specs = [
        (daily, '日线', 0.006, 260, 0.70),
        (weekly, '周线', 0.010, 156, 0.90),
        (monthly, '月线', 0.018, 84, 1.05),
        (quarterly, '季线', 0.030, 48, 1.15),
        (yearly, '年线', 0.050, 20, 1.25),
    ]
    zones = []
    for df, tf, bp, win, w in specs:
        zones += _v212_profile_zones(df, current, win, bp, tf, w)
        zones += _v212_anchor_zones(df, current, win, tf, w)
    zones = _v212_merge_zones(zones)
    supplies = sorted([z for z in zones if z.get('kind') == 'supply' and safe_float(z.get('high')) >= current*0.995], key=lambda z: (safe_float(z.get('low')), -safe_float(z.get('strength'))))
    nearest_supply = supplies[0] if supplies else None
    # 供需压力带不是唯一主轴，只选出最稳定的一条用于Acceptance/空间参考。
    # 优先：多周期+高强度+靠近当前价上方。
    core_candidates = []
    for z in supplies:
        dist = _v212_pct_dist(z.get('core'), current)
        if dist < -0.03 or dist > 0.45:
            continue
        multi = len(set(str(z.get('tf','')).split('+')))
        score = safe_float(z.get('strength')) + min(20, multi*4) - max(0, dist)*25
        if any(s in str(z.get('source','')) for s in ['max_bull','close_resonance','upper_wick']):
            score += 5
        core_candidates.append((score, z))
    core = sorted(core_candidates, key=lambda x: x[0], reverse=True)[0][1] if core_candidates else nearest_supply
    # 空间真空：当前到30%上方之间强HVN/供应越少，分越高。
    target = current * 1.30
    strong_count = 0
    first_strong = None
    for z in supplies:
        low = safe_float(z.get('low'))
        if current < low <= target and safe_float(z.get('strength')) >= 72:
            strong_count += 1
            if first_strong is None or low < safe_float(first_strong.get('low')):
                first_strong = z
    if strong_count == 0:
        void_score = 86
    elif first_strong and _v212_pct_dist(first_strong.get('low'), current) >= 0.18:
        void_score = 72
    elif first_strong and _v212_pct_dist(first_strong.get('low'), current) >= 0.10:
        void_score = 58
    else:
        void_score = 38
    return {'zones': zones[:40], 'core_supply_zone': core, 'nearest_supply': nearest_supply, 'liquidity_void_score': void_score, 'first_strong_supply_30pct': first_strong, 'current': current}


def _v212_volume_behavior(daily, row):
    daily = _v212_norm_df(daily)
    reasons = []
    if daily.empty or len(daily) < 60:
        # fallback保留旧字段颗粒
        old = safe_float(row.get('v201_volume_behavior', row.get('score_volume_behavior', 0)))
        return _v212_clip(old*6 if old <= 15 else old), ['K线不足，沿用旧资金行为分']
    x = daily.copy()
    last = x.iloc[-1]
    vol_ma5 = safe_float(last.get('vol_ma5'))
    vol_ma10 = safe_float(last.get('vol_ma10'))
    vol_ma20 = safe_float(last.get('vol_ma20'))
    vol_ma60 = safe_float(last.get('vol_ma60'))
    score = 45.0
    if vol_ma5 > vol_ma10 > vol_ma20 and vol_ma20 > 0:
        score += 12; reasons.append('短中期量能均线多头，资金热度右移')
    if len(x) >= 80 and safe_float(x['vol_ma20'].iloc[-1]) > safe_float(x['vol_ma20'].iloc[-20]) * 1.10:
        score += 10; reasons.append('20日量能中枢上移')
    if len(x) >= 120:
        right20 = safe_float(x['volume'].tail(20).mean())
        left60 = safe_float(x['volume'].iloc[-120:-60].mean())
        if left60 > 0 and right20 / left60 >= 1.25:
            score += 10; reasons.append(f'右侧平台均量/左侧约{right20/left60:.2f}')
    # 倍量/倍量后平量旧口径保留为资金事件，但不重复堆满。
    if bool(last.get('is_double_volume')) and (bool(last.get('is_bull_quality')) or bool(last.get('is_fake_bear_bull'))):
        score += 8; reasons.append('标准倍量且K线方向质量合格')
    # 近20日上涨日量效
    ret = x['close'].pct_change().fillna(0)
    up_amt = safe_float(x.loc[ret > 0, 'amount'].tail(60).mean())
    dn_amt = safe_float(x.loc[ret < 0, 'amount'].tail(60).mean())
    if up_amt > 0 and dn_amt > 0:
        ratio = up_amt / dn_amt
        if ratio >= 1.15:
            score += 8; reasons.append(f'阳量压阴量，阳/阴成交额比{ratio:.2f}')
        elif ratio < 0.85:
            score -= 8; reasons.append(f'阴量偏强，阳/阴成交额比{ratio:.2f}')
    # 回调量衰减
    if len(x) >= 30:
        recent_up_vol = safe_float(x.loc[ret > 0, 'volume'].tail(10).mean())
        recent_down_vol = safe_float(x.loc[ret < 0, 'volume'].tail(10).mean())
        if recent_up_vol > 0 and recent_down_vol / recent_up_vol < 0.65:
            score += 8; reasons.append('回调量明显小于推进量，抛压衰减')
    bad = int(((x['volume'] > x['vol_ma20']*1.3) & (x['is_bad_stall'])).tail(60).sum())
    if bad >= 3:
        score -= min(18, bad*4); reasons.append(f'近60日放量滞涨/长上影{bad}次')
    return _v212_clip(score), reasons[:6]


def _v212_price_structure(daily, zone_map, row):
    daily = _v212_norm_df(daily)
    reasons = []
    if daily.empty or len(daily) < 60:
        old = safe_float(row.get('v201_structure_position', row.get('score_structure', 0)))
        return _v212_clip(old*5 if old <= 20 else old), ['K线不足，沿用旧结构分']
    x = daily
    current = safe_float(x['close'].iloc[-1])
    score = 45.0
    # 低点抬高
    lows = []
    win = 10
    for i in range(win, len(x)-win):
        lv = safe_float(x['low'].iloc[i])
        if lv <= safe_float(x['low'].iloc[i-win:i].min()) and lv <= safe_float(x['low'].iloc[i+1:i+win+1].min()):
            lows.append((i, lv))
    lows = lows[-4:]
    if len(lows) >= 3 and lows[-1][1] > lows[-2][1] > lows[-3][1]:
        score += 14; reasons.append('最近摆动低点连续抬高')
    elif len(x) >= 120 and safe_float(x['low'].tail(60).min()) > safe_float(x['low'].iloc[-180:-60].min())*1.08:
        score += 10; reasons.append('近60日低点明显高于左侧低点')
    # 平台上半区/贴压力横盘
    if len(x) >= 80:
        low80 = safe_float(x['low'].tail(80).min()); high80 = safe_float(x['high'].tail(80).max())
        if high80 > low80:
            pos = (current-low80)/(high80-low80)
            if pos >= 0.65:
                score += 10; reasons.append('收盘处于平台上半区/上沿附近')
            range20 = safe_float(x['high'].tail(20).max()/max(x['low'].tail(20).min(),1)-1)
            range80 = safe_float(high80/max(low80,1)-1)
            if range80 > 0 and range20/range80 < 0.45:
                score += 8; reasons.append('短期振幅相对长期平台明显收敛')
    # 压力吸收：靠近供需压力后回撤浅、长上影减少
    core = zone_map.get('core_supply_zone') or {}
    if core:
        core_low = safe_float(core.get('low')); core_high=safe_float(core.get('high')); core_line=safe_float(core.get('core'))
        dist = _v212_pct_dist(core_low, current)
        if -0.03 <= dist <= 0.08:
            score += 10; reasons.append(f'贴近供需压力带{core_low:.2f}-{core_high:.2f}吸收')
            last30 = x.tail(30)
            wick_count = int((last30['upper_wick_ratio'] >= 0.45).sum())
            prev30 = x.iloc[-60:-30] if len(x)>=60 else pd.DataFrame()
            prev_wick = int((prev30['upper_wick_ratio'] >= 0.45).sum()) if not prev30.empty else wick_count
            if wick_count <= prev_wick:
                score += 6; reasons.append('靠近压力后长上影未增加，供应反应减弱')
    # 黄金柱/强柱承接：最近强放量柱后不破实体中位/上1/3
    recent = x.tail(40)
    attack = recent[(recent['volume'] > recent['vol_ma20']*1.35) & ((recent['is_bull_quality']) | (recent['is_fake_bear_bull']))]
    if not attack.empty:
        idx = attack.index[-1]
        r = x.loc[idx]
        body_low = min(safe_float(r['open']), safe_float(r['close']))
        body_high = max(safe_float(r['open']), safe_float(r['close']))
        mid = body_low + (body_high-body_low)*0.5
        upper13 = body_low + (body_high-body_low)*2/3
        after = x.loc[idx:].tail(13)
        if len(after) >= 3:
            if int((after['close'] >= upper13*0.995).sum()) >= max(2, len(after)-2):
                score += 12; reasons.append('强柱后多数收盘守实体上1/3，承接很强')
            elif int((after['close'] >= mid*0.995).sum()) >= max(2, len(after)-2):
                score += 8; reasons.append('强柱后多数收盘守实体中位，承接合格')
            elif safe_float(after['close'].iloc[-1]) < body_low*0.995:
                score -= 10; reasons.append('强柱后跌破实体实底，承接失败')
    return _v212_clip(score), reasons[:7]


def _v212_time_maturity(daily, row):
    daily = _v212_norm_df(daily)
    reasons=[]
    if daily.empty or len(daily)<80:
        return 50.0, ['K线不足，时间成熟度中性']
    x=daily
    score=45.0
    # 平台持续与波动压缩
    if len(x)>=160:
        r20 = safe_float(x['high'].tail(20).max()/max(x['low'].tail(20).min(),1)-1)
        r120 = safe_float(x['high'].tail(120).max()/max(x['low'].tail(120).min(),1)-1)
        if r120>0 and r20/r120<0.45:
            score += 15; reasons.append(f'20日/120日振幅压缩至{r20/r120:.2f}')
        # ATR压缩
        tr = pd.concat([(x['high']-x['low']), (x['high']-x['close'].shift(1)).abs(), (x['low']-x['close'].shift(1)).abs()], axis=1).max(axis=1)
        atr20 = safe_float(tr.rolling(20,min_periods=5).mean().iloc[-1])
        atr120 = safe_float(tr.rolling(120,min_periods=30).mean().iloc[-1])
        if atr120>0 and atr20/atr120<0.72:
            score += 12; reasons.append(f'ATR20/ATR120={atr20/atr120:.2f}，波动压缩成熟')
    # 长平台：过去120/180日有平台特征且低点不破
    if len(x)>=180:
        range120 = safe_float(x['high'].tail(120).max()/max(x['low'].tail(120).min(),1)-1)
        if range120 < 0.55:
            score += 8; reasons.append('120日平台蓄势较充分')
        range180 = safe_float(x['high'].tail(180).max()/max(x['low'].tail(180).min(),1)-1)
        if range180 < 0.75:
            score += 5; reasons.append('180日级别筹码消化较久')
    # 斐波那契/1000天只作为轻辅助，这里用大高点后天数近似
    if len(x)>=500:
        high_idx = int(x['high'].iloc[-500:].idxmax())
        bars_since_high = len(x)-1-high_idx
        for n in [233,377,610,987]:
            if n*0.97 <= bars_since_high <= n*1.03:
                score += 3; reasons.append(f'距近500日高点约{bars_since_high}日，接近{n}时间窗（轻辅助）')
                break
    return _v212_clip(score), reasons[:6]


def _v212_space_score(daily, zone_map, row):
    daily = _v212_norm_df(daily)
    reasons=[]
    current = safe_float(row.get('close', 0))
    if not daily.empty:
        current=safe_float(daily['close'].iloc[-1])
    score = 45.0
    void_score = safe_float(zone_map.get('liquidity_void_score'), 45)
    score += (void_score-45)*0.75
    if void_score>=72:
        reasons.append('上方10%-30%内供应较稀，存在价格真空')
    elif void_score<45:
        reasons.append('上方空间有厚供应，短线赔率受限')
    core=zone_map.get('core_supply_zone') or {}
    if core:
        core_high=safe_float(core.get('high')); core_low=safe_float(core.get('low'))
        if core_high>0:
            d=_v212_pct_dist(core_high,current)
            if d>0.12:
                score += 12; reasons.append(f'距供需压力带上沿仍有{d:.1%}空间')
            elif 0<=d<=0.05:
                score += 3; reasons.append('贴近供需压力带，等待吸收/突破')
            elif d<0:
                score += 10; reasons.append('已站上供需压力带上沿，进入扩张验证')
    # 旧RR保留
    rr = safe_float(row.get('v20_rr', row.get('risk_reward_ratio', row.get('rr', 0))))
    if rr>=2.0:
        score += 10; reasons.append(f'旧模型RR={rr:.2f}合格')
    elif 0<rr<1.3:
        score -= 10; reasons.append(f'旧模型RR={rr:.2f}不足')
    return _v212_clip(score), reasons[:6]


def _v212_stock_character(daily, row):
    daily=_v212_norm_df(daily)
    reasons=[]; flags=[]
    if daily.empty or len(daily)<80:
        old = safe_float(row.get('score_activity', row.get('active_score', 0)))
        return _v212_clip(old*5 if old<=20 else 50), '样本不足/沿用旧活跃', ['近一年K线不足，股性画像保守'], []
    x=daily.tail(250).copy()
    ret=x['close'].pct_change().fillna(0)
    amount=x['amount'].fillna(x['volume']*x['close'])
    amt60=safe_float(amount.tail(60).mean())
    amt_cv=safe_float(amount.std()/max(amount.mean(),1))
    if amt60>=200_000_000: liq=86
    elif amt60>=80_000_000: liq=72
    elif amt60>=30_000_000: liq=58
    else: liq=38; flags.append('低流动')
    if amt_cv>1.6:
        liq-=10; flags.append('成交额不稳定')
    big_up5=int((ret>=0.05).sum()); big_down5=int((ret<=-0.05).sum()); limit_like=int((ret>=0.095).sum())
    activity=_v212_clip(45+min(25,big_up5*2)+min(12,limit_like*3)-min(10,big_down5))
    # 趋势顺滑：MA20/MA60上方比例+突破延续
    ma20=x['close'].rolling(20,min_periods=5).mean(); ma60=x['close'].rolling(60,min_periods=15).mean()
    above20=safe_float((x['close']>ma20).mean()); above60=safe_float((x['close']>ma60).mean())
    streaks=[]; cur=0
    for r in ret:
        if r>0: cur+=1
        else:
            if cur: streaks.append(cur)
            cur=0
    if cur: streaks.append(cur)
    avg_streak=sum(streaks)/len(streaks) if streaks else 0
    bad_stall=int(((x['volume']>x['vol_ma20']*1.25)&x['is_bad_stall']).sum())
    trend=_v212_clip(38+above20*18+above60*20+min(16,avg_streak*4)-min(18,bad_stall*2))
    # 追高容错
    chase_checked=chase_bad=chase_ok=0
    for i in range(20,len(x)-6):
        cond = (ret.iloc[i]>=0.04) or (safe_float(x['close'].iloc[i]) > safe_float(x['high'].iloc[i-20:i].max())*1.005)
        if not cond: continue
        chase_checked+=1
        entry=safe_float(x['close'].iloc[i]); fut=x.iloc[i+1:i+6]
        dd=safe_float(fut['low'].min()/max(entry,1)-1); gain=safe_float(fut['high'].max()/max(entry,1)-1)
        if dd>-0.035 and gain>=0.03: chase_ok+=1
        if dd<=-0.05 or gain<0.01: chase_bad+=1
    ok_ratio=chase_ok/chase_checked if chase_checked else 0.35
    bad_ratio=chase_bad/chase_checked if chase_checked else 0.35
    chase=_v212_clip(50+ok_ratio*35-bad_ratio*35 - (10 if trend<55 else 0) - (10 if bad_stall>=5 else 0))
    # 历史扩张记忆：近3年或全样本最大涨幅
    y=daily.tail(750) if len(daily)>=250 else daily
    if not y.empty:
        min_low=safe_float(y['low'].min()); max_high=safe_float(y['high'].max())
        expansion=(max_high/min_low-1) if min_low>0 else 0
    else: expansion=0
    memory=50
    if expansion>=3.0: memory=88; reasons.append(f'历史扩张记忆强，近阶段最大振幅约{expansion:.1f}倍')
    elif expansion>=1.5: memory=72; reasons.append('历史攻击/扩张记忆较强')
    elif expansion>=0.8: memory=60
    else: memory=45
    score=_v212_clip(liq*0.20+activity*0.18+trend*0.22+chase*0.22+memory*0.18)
    if chase<50: reasons.append('近一年追高容错偏低，需回踩/确认买法'); flags.append('追高容错低')
    if trend<55: reasons.append('近一年趋势顺滑度一般'); flags.append('趋势顺滑一般')
    if activity>=68 and trend>=62: style='突破确认型'
    elif memory>=75 and activity>=62 and chase>=55: style='强股性预扩张型'
    elif activity>=62 and chase<55: style='弹性低吸/确认型'
    elif liq<45: style='低流动观察型'
    else: style='回踩/确认型'
    if not reasons:
        reasons.append(f'近一年股性：{style}')
    return score, style, reasons[:6], flags


def _v212_execution_plan(row, daily, zone_map, v_scores):
    daily=_v212_norm_df(daily)
    current=safe_float(row.get('close', 0))
    if not daily.empty:
        current=safe_float(daily['close'].iloc[-1])
    core=zone_map.get('core_supply_zone') or {}
    core_low=safe_float(core.get('low')); core_high=safe_float(core.get('high')); core_line=safe_float(core.get('core'))
    # 使用供需压力带/近高生成预判区和确认线，但不是模型主轴。
    if core_line<=0:
        core_line=safe_float(row.get('v201_precise_trigger_line', row.get('v20_confirm_price', 0)))
    if core_line<=0 and not daily.empty:
        core_line=safe_float(daily['high'].tail(60).max())
    confirm_line=core_high if core_high>0 else core_line
    if confirm_line<=0:
        confirm_line=current*1.05
    # 支撑/失败线：平台中轴、20日低点、旧防守位
    if not daily.empty and len(daily)>=30:
        low20=safe_float(daily['low'].tail(20).min()); low60=safe_float(daily['low'].tail(60).min())
        high60=safe_float(daily['high'].tail(60).max())
        platform_mid=(low60+high60)/2 if high60>low60 else current*0.95
        support=max(low20, platform_mid*0.98)
    else:
        support=safe_float(row.get('v20_defense', row.get('defensive_price', 0))) or current*0.93
    old_def=safe_float(row.get('v20_defense', row.get('defensive_price', 0)))
    if old_def>0:
        support=max(min(support,current*0.995), old_def)
    predict_fail=round(support*0.985,2)
    confirm_fail=round(confirm_line*0.975,2) if confirm_line>0 else round(current*0.94,2)
    trend_fail=round(max(confirm_fail, current*0.90),2)
    risk=(current/max(predict_fail,0.01)-1) if predict_fail<current else 0.03
    # 状态：预触发/确认/扩张/观察
    distance_to_confirm=_v212_pct_dist(confirm_line,current)
    volume_score=safe_float(v_scores.get('volume_score'))
    price_score=safe_float(v_scores.get('price_score'))
    time_score=safe_float(v_scores.get('time_score'))
    space_score=safe_float(v_scores.get('space_score'))
    char_score=safe_float(v_scores.get('character_score'))
    predict_win=_v212_clip(45 + (volume_score-50)*0.12 + (price_score-50)*0.16 + (time_score-50)*0.10 + (space_score-50)*0.10 + (char_score-50)*0.10, 35, 78)
    confirm_win=_v212_clip(predict_win + 8 + (8 if distance_to_confirm < 0 else 0), 45, 86)
    heavy_win=_v212_clip(confirm_win + 5, 50, 90)
    # 仓位百分比
    pred_pos='20%-30%' if predict_win>=60 and risk<=V212_MAX_PREDICT_RISK else ('10%-20%' if predict_win>=55 else '观察')
    conf_pos='50%-60%' if confirm_win>=70 else ('30%-40%' if confirm_win>=62 else '等待')
    heavy_pos='60%-70%' if heavy_win>=75 and confirm_win>=70 else '不建议重仓'
    # A/B确认规则
    a_rule=f'A类回踩确认：突破{confirm_line:.2f}后回踩{max(confirm_line*0.985, confirm_fail):.2f}-{confirm_line:.2f}不破，回踩量≤突破量70%，收盘重新站上确认线。'
    b_rule=f'B类强势不回踩：突破{confirm_line:.2f}后不回踩，盘中/次日持续站在{confirm_line*1.01:.2f}上方，收盘位置≥80%，上影短，量能健康。'
    # 操作分类
    if distance_to_confirm > 0.12:
        state='观察/等待贴近确认线'
    elif 0 <= distance_to_confirm <= 0.12:
        state='预判试仓区'
    elif -0.08 <= distance_to_confirm < 0:
        state='确认加仓区'
    else:
        state='扩张持仓区/不新开重仓'
    if risk>0.10:
        state='风险偏大/等待回踩'
    return {
        'v212_state': state,
        'v212_confirm_line': round(confirm_line,2),
        'v212_predict_fail_line': predict_fail,
        'v212_confirm_fail_line': confirm_fail,
        'v212_trend_fail_line': trend_fail,
        'v212_risk_pct': round(risk,4),
        'v212_predict_win_rate': round(predict_win/100,4),
        'v212_confirm_win_rate': round(confirm_win/100,4),
        'v212_heavy_win_rate': round(heavy_win/100,4),
        'v212_predict_position': pred_pos,
        'v212_confirm_position': conf_pos,
        'v212_heavy_position': heavy_pos,
        'v212_a_confirm_rule': a_rule,
        'v212_b_confirm_rule': b_rule,
    }


def _v212_target_plan(row, daily, zone_map, execution):
    daily=_v212_norm_df(daily)
    current=safe_float(row.get('close',0))
    if not daily.empty:
        current=safe_float(daily['close'].iloc[-1])
    confirm=safe_float(execution.get('v212_confirm_line'), current)
    # 用近期平台低点到确认线做扩展，不够则用10%目标。
    if not daily.empty and len(daily)>=60:
        base_low=safe_float(daily['low'].tail(60).min())
    else:
        base_low=current*0.88
    height=max(confirm-base_low, current*0.05)
    t1=confirm
    t2=confirm+height*0.5
    t3=confirm+height*1.0
    t4=confirm+height*2.0
    t5=confirm+height*3.0
    # 如果当前已越过确认线，第一目标至少当前上方附近，不倒退。
    targets=[]
    raw=[(t1,0.75,'确认线/前高突破位'),(t2,0.65,'1.5倍扩展/正常扩张'),(t3,0.55,'2倍扩展/主升目标'),(t4,0.40,'3倍扩展/趋势扩张'),(t5,0.25,'4倍扩展/情绪高潮')]
    for price, prob, reason in raw:
        # 结合空间真空微调概率
        if safe_float(zone_map.get('liquidity_void_score'))>=72:
            prob += 0.03
        if safe_float(zone_map.get('liquidity_void_score'))<45:
            prob -= 0.08
        targets.append({'price':round(max(price,0),2),'probability':round(max(0.05,min(0.9,prob)),2),'reason':reason})
    return targets




# ======================= V23.0 Supply Absorption + Volume Regime Shift START =======================
# 目标：把“瑞华泰式”大涨前结构模型化：
# 阴跌/大阴供应区 -> 长期消化 -> 突破大阴实体顶 -> 回踩收盘不破实体顶 -> 旧阴跌量 < 回踩量 < 突破量 -> 二次转强。
# 注意：该模块只在 confirmation_layer 拿主分；压力带/平台/普通回踩/普通量能模块只做证据引用，避免重复打分。

def _v23_tf_params(tf):
    params = {
        '日线': {'body_pct':0.045, 'body_ratio':0.55, 'vol_mult':1.20, 'future_n':40, 'future_min':16, 'digest_min':18, 'weight':0.78, 'reclaim_look':90, 'pullback_n':13},
        '周线': {'body_pct':0.075, 'body_ratio':0.60, 'vol_mult':1.35, 'future_n':20, 'future_min':8, 'digest_min':8, 'weight':1.00, 'reclaim_look':36, 'pullback_n':6},
        '月线': {'body_pct':0.110, 'body_ratio':0.65, 'vol_mult':1.35, 'future_n':18, 'future_min':7, 'digest_min':6, 'weight':1.12, 'reclaim_look':24, 'pullback_n':5},
        '季线': {'body_pct':0.160, 'body_ratio':0.65, 'vol_mult':1.20, 'future_n':12, 'future_min':4, 'digest_min':3, 'weight':0.88, 'reclaim_look':12, 'pullback_n':3},
    }
    return params.get(tf, params['日线'])


def _v23_find_effective_bear_supply_anchors(df, tf='日线', max_anchors=6):
    x = _v212_norm_df(df)
    if x.empty or len(x) < 30:
        return []
    prm = _v23_tf_params(tf)
    rng = (x['high'] - x['low']).replace(0, np.nan)
    body_abs = (x['open'] - x['close'])
    body_pct = (body_abs / x['open'].replace(0, np.nan)).fillna(0)
    body_ratio = (body_abs.abs() / rng).fillna(0)
    vol_base = x['volume'].rolling(12 if tf in ['月线','季线'] else 20, min_periods=4).mean().shift(1)
    vol_mult = (x['volume'] / vol_base.replace(0, np.nan)).fillna(0)
    pos250 = (x['high'] - x['low'].rolling(min(len(x), 60), min_periods=8).min()) / (x['high'].rolling(min(len(x), 60), min_periods=8).max() - x['low'].rolling(min(len(x), 60), min_periods=8).min()).replace(0, np.nan)
    anchors = []
    for i in range(0, len(x)-max(3, prm['future_min'])):
        if not (x['close'].iloc[i] < x['open'].iloc[i]):
            continue
        if body_pct.iloc[i] < prm['body_pct'] or body_ratio.iloc[i] < prm['body_ratio']:
            continue
        if vol_mult.iloc[i] < prm['vol_mult']:
            continue
        top = max(safe_float(x['open'].iloc[i]), safe_float(x['close'].iloc[i]))
        mid = (safe_float(x['open'].iloc[i]) + safe_float(x['close'].iloc[i])) / 2.0
        bot = min(safe_float(x['open'].iloc[i]), safe_float(x['close'].iloc[i]))
        fut = x.iloc[i+1:i+1+prm['future_n']]
        if len(fut) < prm['future_min']:
            continue
        below_days = int((fut['close'] < top * 0.995).sum())
        below_ratio = below_days / max(len(fut), 1)
        # 后续长期在大阴实体顶下方，才说明它是真供应锚点。
        if below_days < prm['future_min'] or below_ratio < 0.45:
            continue
        quality = 40
        quality += min(20, body_pct.iloc[i] / prm['body_pct'] * 8)
        quality += min(18, body_ratio.iloc[i] * 18)
        quality += min(18, vol_mult.iloc[i] * 5)
        quality += min(16, below_ratio * 16)
        # 高位/中高位大阴供应更有供应记忆；低位大阴更多是恐慌释放，权重下降。
        p = safe_float(pos250.iloc[i], 0.5)
        if p >= 0.55:
            quality += 8
        elif p <= 0.25:
            quality -= 5
        anchors.append({
            'tf': tf,
            'idx': int(i),
            'date': str(x['date'].iloc[i].date()) if hasattr(x['date'].iloc[i], 'date') else str(x['date'].iloc[i]),
            'top': round(top, 4), 'mid': round(mid, 4), 'bottom': round(bot, 4),
            'high': round(safe_float(x['high'].iloc[i]), 4), 'low': round(safe_float(x['low'].iloc[i]), 4),
            'volume': round(safe_float(x['volume'].iloc[i]), 2),
            'body_pct': round(float(body_pct.iloc[i]), 4),
            'body_ratio': round(float(body_ratio.iloc[i]), 4),
            'vol_mult': round(float(vol_mult.iloc[i]), 3),
            'below_ratio': round(float(below_ratio), 3),
            'quality': round(_v212_clip(quality * prm['weight']), 2),
        })
    anchors = sorted(anchors, key=lambda z: z['quality'], reverse=True)[:max_anchors]
    return anchors


def _v23_score_anchor_absorption(x, anchor, tf='日线'):
    x = _v212_norm_df(x)
    if x.empty:
        return {'score': 0.0, 'reasons': [], 'state': '无数据'}
    prm = _v23_tf_params(tf)
    idx = int(anchor.get('idx', -1))
    if idx < 0 or idx >= len(x)-2:
        return {'score': 0.0, 'reasons': [], 'state': '锚点无效'}
    top = safe_float(anchor.get('top'))
    mid = safe_float(anchor.get('mid'))
    bottom = safe_float(anchor.get('bottom'))
    if top <= 0:
        return {'score': 0.0, 'reasons': [], 'state': '实体顶无效'}
    after = x.iloc[idx+1:].copy()
    if after.empty:
        return {'score': 0.0, 'reasons': [], 'state': '无后续'}

    score = 0.0
    reasons = []
    state = '观察'
    # 1）供应锚点质量：只给基础分，避免和压力带重复。
    anchor_quality = safe_float(anchor.get('quality'), 0)
    if anchor_quality >= 70:
        score += 1.8; reasons.append(f'{tf}有效大阴供应锚点质量高')
    elif anchor_quality >= 55:
        score += 1.2; reasons.append(f'{tf}有效大阴供应锚点成立')
    else:
        score += 0.6; reasons.append(f'{tf}存在大阴供应记忆')

    # 2）长期消化：大阴后较长时间压在实体顶下方。
    below = after[after['close'] < top * 0.995]
    digest_len = len(below)
    if digest_len >= prm['digest_min'] * 2:
        score += 1.5; reasons.append(f'大阴实体顶下方消化充分({digest_len}{tf[0]})')
    elif digest_len >= prm['digest_min']:
        score += 0.8; reasons.append('大阴供应区下方已有消化')

    # 3）突破实体顶：找最近一次收盘站上实体顶。
    reclaim_mask = after['close'] > top * 1.003
    if not bool(reclaim_mask.any()):
        # 未突破但贴近实体顶，作为观察，不做主分。
        cur = safe_float(x['close'].iloc[-1])
        dist = top / cur - 1 if cur > 0 else 9
        if -0.03 <= dist <= 0.12:
            score += 0.8; reasons.append('接近大阴实体顶，进入供应测试观察')
            state = '接近供应顶'
        return {'score': round(min(score, 4.0),2), 'reasons': reasons[:8], 'state': state, 'anchor': anchor}

    reclaim_indices = list(after.index[reclaim_mask])
    first_rec = int(reclaim_indices[0])
    rec_bar = x.loc[first_rec]
    body_top = max(safe_float(rec_bar['open']), safe_float(rec_bar['close']))
    body_bottom = min(safe_float(rec_bar['open']), safe_float(rec_bar['close']))
    rec_body_break = body_bottom >= top * 0.995 or safe_float(rec_bar['close']) >= top * 1.018
    score += 2.0
    reasons.append('收盘突破大阴实体顶')
    if rec_body_break and safe_float(rec_bar.get('close_pos'), 0.5) >= 0.60:
        score += 1.0; reasons.append('突破K实体质量较好/非单纯影线试探')
    state = '已突破供应顶'

    # 成交量定义：旧阴跌均量、突破量、回踩量。
    pre_seg = x.iloc[idx+1:first_rec]
    old_down = pre_seg[pre_seg['close'] < top * 1.005]
    if len(old_down) >= 4:
        old_downtrend_vol = safe_float(old_down['volume'].tail(min(len(old_down), max(6, prm['digest_min']*2))).mean())
    else:
        old_downtrend_vol = safe_float(pre_seg['volume'].mean()) if len(pre_seg) else safe_float(x['volume'].iloc[max(0,idx-10):idx+1].mean())
    breakout_vol = safe_float(x['volume'].iloc[max(idx, first_rec-1):min(len(x), first_rec+2)].mean())

    pull = x.iloc[first_rec+1:min(len(x), first_rec+1+prm['pullback_n'])].copy()
    if len(pull) == 0:
        return {
            'score': round(min(score, 6.0),2), 'reasons': reasons[:8], 'state': state,
            'anchor': anchor, 'old_downtrend_vol': old_downtrend_vol, 'breakout_vol': breakout_vol, 'pullback_vol': 0,
            'bear_top': top, 'bear_mid': mid, 'bear_bottom': bottom,
        }

    pullback_vol = safe_float(pull['volume'].mean())
    min_pull_close = safe_float(pull['close'].min())
    min_pull_low = safe_float(pull['low'].min())
    last_close = safe_float(x['close'].iloc[-1])

    # 4）关键确认：突破后回踩收盘不破实体顶；盘中刺破但收回也算强维护。
    if min_pull_close >= top * 0.995:
        score += 3.0; reasons.append('突破后回踩收盘不破大阴实体顶，压力转支撑')
        state = '回踩确认成立'
    elif min_pull_close >= mid * 0.995:
        score += 1.2; reasons.append('回踩收盘未破大阴实体中位，承接尚可但低于实体顶确认')
        state = '回踩中位承接'
    elif min_pull_close < bottom * 0.995:
        score -= 4.0; reasons.append('回踩收盘跌破大阴实体底，突破失败风险')
        state = '突破失败风险'

    if min_pull_low < top * 0.995 and min_pull_close >= top * 0.995:
        score += 0.8; reasons.append('盘中刺破实体顶但收盘收回，资金维护明显')

    # 5）量能级别切换：旧阴跌均量 < 回踩均量 < 突破均量。
    if old_downtrend_vol > 0 and breakout_vol > 0 and pullback_vol > 0:
        regime_ratio = pullback_vol / old_downtrend_vol
        pb_break_ratio = pullback_vol / breakout_vol
        if regime_ratio >= 1.20 and pb_break_ratio <= 0.78:
            score += 4.0; reasons.append('量能级别切换成立：旧阴跌量 < 回踩量 < 突破量')
        elif regime_ratio >= 1.05 and pb_break_ratio <= 0.88:
            score += 2.0; reasons.append('量能级别切换偏成立，回踩量缩于突破且高于旧阴跌量')
        elif pb_break_ratio > 1.05:
            score -= 2.0; reasons.append('回踩量大于突破量，警惕放量回落/派发')
        elif regime_ratio < 0.85:
            score -= 0.8; reasons.append('回踩量缩回旧弱势量级，资金级别切换不足')

    # 6）回踩K线质量：无放量长阴、小实体、低点抬高。
    bad_bear = pull[(pull['close'] < pull['open']) & (pull['body_ratio'] >= 0.55) & (pull['volume'] > max(pullback_vol, 1) * 1.15)]
    if len(bad_bear) == 0:
        score += 1.0; reasons.append('回踩未见放量长阴破坏')
    else:
        score -= 1.8; reasons.append('回踩出现放量长阴，承接质量下降')
    if len(pull) >= 3:
        body_avg = safe_float((pull['close']-pull['open']).abs().mean())
        rec_body = abs(safe_float(rec_bar['close'])-safe_float(rec_bar['open']))
        if rec_body > 0 and body_avg <= rec_body * 0.55:
            score += 0.8; reasons.append('回踩小实体整理，供应释放温和')
        lows = pull['low'].tail(min(4, len(pull))).tolist()
        if len(lows) >= 3 and lows[-1] >= min(lows[:-1]) * 1.005:
            score += 0.6; reasons.append('回踩后段低点抬高/不再深杀')

    # 7）二次转强：突破回踩后重新上穿回踩平台/突破K高点附近。
    after_pull = x.iloc[min(len(x), first_rec+1+min(len(pull), prm['pullback_n'])):]
    if len(after_pull) >= 1:
        trigger = max(top, safe_float(pull['high'].max()))
        re_strong = after_pull[(after_pull['close'] > trigger * 1.003) & (after_pull['close_pos'] >= 0.60)]
        if len(re_strong) > 0:
            score += 1.5; reasons.append('回踩后重新转强，供应吸收进入二次确认')
            state = '二次转强确认'
            if safe_float(re_strong['volume'].iloc[0]) >= max(pullback_vol, 1) * 1.35:
                score += 0.7; reasons.append('二次转强量能重新放大')

    score = _v212_clip(score, 0, 15)
    return {
        'score': round(score, 2), 'reasons': reasons[:10], 'state': state, 'anchor': anchor,
        'old_downtrend_vol': round(old_downtrend_vol, 2), 'breakout_vol': round(breakout_vol, 2), 'pullback_vol': round(pullback_vol, 2),
        'bear_top': round(top, 4), 'bear_mid': round(mid, 4), 'bear_bottom': round(bottom, 4),
        'min_pull_close': round(min_pull_close, 4), 'min_pull_low': round(min_pull_low, 4), 'last_close': round(last_close, 4),
    }


def _v23_supply_absorption_regime_shift(daily, row=None):
    daily = _v212_norm_df(daily)
    if daily.empty or len(daily) < 120:
        return {'v23_supply_absorption_score': 0.0, 'v23_supply_state': '数据不足', 'v23_supply_reasons': []}
    frames = [
        ('日线', daily),
        ('周线', _v212_resample(daily, 'W-FRI')),
        ('月线', _v212_resample(daily, 'M')),
        ('季线', _v212_resample(daily, 'Q')),
    ]
    evaluated = []
    for tf, df in frames:
        if df is None or df.empty:
            continue
        anchors = _v23_find_effective_bear_supply_anchors(df, tf=tf, max_anchors=4)
        for a in anchors:
            ev = _v23_score_anchor_absorption(df, a, tf=tf)
            ev['tf'] = tf
            evaluated.append(ev)
    if not evaluated:
        return {'v23_supply_absorption_score': 0.0, 'v23_supply_state': '未识别有效大阴供应锚点', 'v23_supply_reasons': []}
    evaluated = sorted(evaluated, key=lambda z: safe_float(z.get('score')), reverse=True)
    best = evaluated[0]
    # 多周期共振只做小幅确认，不重复加各周期主分。
    resonance = sum(1 for e in evaluated[:8] if safe_float(e.get('score')) >= 6.0)
    resonance_bonus = min(2.0, max(0, resonance-1) * 0.8)
    final_score = _v212_clip(safe_float(best.get('score')) + resonance_bonus, 0, 15)
    reasons = list(best.get('reasons') or [])
    if resonance_bonus > 0:
        reasons.append(f'多周期供应吸收共振{resonance}处，仅小幅确认不重复加分')
    anchor = best.get('anchor') or {}
    return {
        'v23_supply_absorption_score': round(final_score, 2),
        'v23_supply_state': best.get('state', ''),
        'v23_supply_tf': best.get('tf', ''),
        'v23_supply_reasons': reasons[:10],
        'v23_bear_top': safe_float(best.get('bear_top')),
        'v23_bear_mid': safe_float(best.get('bear_mid')),
        'v23_bear_bottom': safe_float(best.get('bear_bottom')),
        'v23_bear_anchor_date': anchor.get('date', ''),
        'v23_bear_anchor_quality': safe_float(anchor.get('quality')),
        'v23_old_downtrend_vol': safe_float(best.get('old_downtrend_vol')),
        'v23_breakout_vol': safe_float(best.get('breakout_vol')),
        'v23_pullback_vol': safe_float(best.get('pullback_vol')),
        'v23_supply_all_candidates': [
            {'tf': e.get('tf'), 'score': safe_float(e.get('score')), 'state': e.get('state'), 'date': (e.get('anchor') or {}).get('date','')}
            for e in evaluated[:6]
        ],
    }

# ======================= V23.0 Supply Absorption + Volume Regime Shift END =======================

# ======================= V23.1 Long Upper Shadow Supply Acceptance START =======================
# 目标：处理云南能投这类“超大量长上影供应区”。
# 不是把长上影简单看成压力，也不是直接看涨；而是判断：
# 1）长上影1/2位是否被收盘接受；2）长上影后的低点是否抬高；
# 3）后续再冲高的长上影量是否递减；4）回踩量是否小于冲高量但高于旧低位死量；
# 5）若再次放大量长上影且跌破前低，则按派发/供应未吸收风险处理。

def _v231_tf_config(tf):
    # 不同周期使用不同阈值，避免日线/月线/季线硬套同一标准。
    cfg = {
        '日线': {'min_rows': 90, 'upper': 0.40, 'vol_mult': 1.8, 'lookahead': 20, 'weight': 0.75},
        '周线': {'min_rows': 45, 'upper': 0.42, 'vol_mult': 1.6, 'lookahead': 10, 'weight': 1.00},
        '月线': {'min_rows': 24, 'upper': 0.45, 'vol_mult': 1.45, 'lookahead': 6, 'weight': 1.18},
        '季线': {'min_rows': 12, 'upper': 0.48, 'vol_mult': 1.35, 'lookahead': 3, 'weight': 0.90},
        '年线': {'min_rows': 8,  'upper': 0.50, 'vol_mult': 1.25, 'lookahead': 2, 'weight': 0.65},
    }
    return cfg.get(tf, cfg['日线'])


def _v231_long_upper_shadow_anchors(df, tf='日线', max_anchors=5):
    x = _v212_norm_df(df)
    cfg = _v231_tf_config(tf)
    if x.empty or len(x) < cfg['min_rows']:
        return []
    x = x.copy().reset_index(drop=True)
    x['vol_base'] = x['volume'].rolling(12 if tf in ['月线','季线','年线'] else 20, min_periods=3).mean().shift(1)
    rng = (x['high'] - x['low']).replace(0, np.nan)
    body_top = x[['open','close']].max(axis=1)
    body_bottom = x[['open','close']].min(axis=1)
    upper = (x['high'] - body_top).clip(lower=0)
    x['v231_body_top'] = body_top
    x['v231_body_bottom'] = body_bottom
    x['v231_upper_abs'] = upper
    x['v231_shadow_mid'] = body_top + upper * 0.5
    x['v231_upper_ratio'] = (upper / rng).fillna(0)
    x['v231_close_pos'] = ((x['close'] - x['low']) / rng).fillna(0.5)
    x['v231_vol_mult'] = (x['volume'] / x['vol_base'].replace(0, np.nan)).replace([np.inf, -np.inf], np.nan).fillna(0)
    # 高位/近上方供应才有意义；低位长下影不是本模块处理对象。
    price_q = x['close'].rolling(min(len(x), 36), min_periods=5).apply(lambda z: pd.Series(z).rank(pct=True).iloc[-1], raw=False).fillna(0.5)
    x['v231_price_q'] = price_q
    cond = (
        (x['v231_upper_ratio'] >= cfg['upper']) &
        (x['v231_vol_mult'] >= cfg['vol_mult']) &
        (x['v231_price_q'] >= 0.45) &
        (x['high'] > x['close'] * 1.08)
    )
    hits = x[cond].copy()
    if hits.empty:
        return []
    anchors = []
    for idx, r in hits.tail(12).iterrows():
        i = int(idx)
        post = x.iloc[i+1:]
        if len(post) < max(1, cfg['lookahead']):
            continue
        # 后续多数收盘压在1/2位下方，说明这根长影线形成了真实供应记忆。
        mid = safe_float(r['v231_shadow_mid'])
        body_top_i = safe_float(r['v231_body_top'])
        high_i = safe_float(r['high'])
        below_mid_ratio = float((post['close'].head(cfg['lookahead']*3) < mid).mean()) if len(post) else 0
        if below_mid_ratio < 0.35:
            # 很快被收回的长上影，不作为长期供应锚点，只算试盘。
            continue
        quality = 0.0
        quality += min(3.0, safe_float(r['v231_upper_ratio']) * 3.0)
        quality += min(3.0, safe_float(r['v231_vol_mult']) * 0.9)
        quality += 1.0 if below_mid_ratio >= 0.60 else 0.4
        quality += 0.8 if safe_float(r['v231_price_q']) >= 0.70 else 0.2
        anchors.append({
            'tf': tf,
            'idx': i,
            'date': str(r.get('date',''))[:10],
            'high': high_i,
            'body_top': body_top_i,
            'body_bottom': safe_float(r['v231_body_bottom']),
            'shadow_mid': mid,
            'upper_ratio': safe_float(r['v231_upper_ratio']),
            'vol_mult': safe_float(r['v231_vol_mult']),
            'volume': safe_float(r['volume']),
            'quality': round(quality * cfg['weight'], 2),
            'below_mid_ratio': round(below_mid_ratio, 2),
        })
    anchors = sorted(anchors, key=lambda a: (a['quality'], a['idx']), reverse=True)[:max_anchors]
    return anchors


def _v231_evaluate_shadow_acceptance(df, anchor, tf='日线'):
    x = _v212_norm_df(df)
    if x.empty or not anchor:
        return {'score': 0.0, 'state': '数据不足', 'reasons': []}
    cfg = _v231_tf_config(tf)
    i = int(anchor.get('idx', -1))
    if i < 0 or i >= len(x)-1:
        return {'score': 0.0, 'state': '锚点位置无效', 'reasons': []}
    post = x.iloc[i+1:].copy()
    recent = x.tail(min(len(x), max(3, cfg['lookahead']*3))).copy()
    cur = x.iloc[-1]
    close = safe_float(cur['close'])
    mid = safe_float(anchor.get('shadow_mid'))
    top = safe_float(anchor.get('body_top'))
    high = safe_float(anchor.get('high'))
    anchor_vol = safe_float(anchor.get('volume'))
    score = 0.0
    reasons = []
    state = '长上影供应区观察'

    # 1）价格接受度：收盘站上长上影1/2位，是本模块最核心的正向确认。
    if close >= mid * 1.003:
        score += 3.2; reasons.append(f'{tf}收盘站上长上影1/2位({mid:.2f})，高价接受度提高')
        state = '长上影中轴上方接受'
        # 实体/收盘越靠近高位，越像供应被吸收。
        rng = max(safe_float(cur['high']) - safe_float(cur['low']), 1e-9)
        close_pos = (close - safe_float(cur['low'])) / rng
        if close_pos >= 0.70:
            score += 0.8; reasons.append('当前收盘位置偏强，不是单纯盘中试探')
    elif close >= top * 1.003:
        score += 1.2; reasons.append(f'{tf}站上长影K实体顶({top:.2f})但未过1/2位，属于修复未确认')
        state = '实体顶修复但中轴未过'
    else:
        score -= 1.0; reasons.append(f'{tf}仍在长上影1/2位下方，高位供应尚未被市场接受')

    # 2）长上影后的低点是否抬高：区分吸收和派发。
    if len(post) >= 3:
        blocks = np.array_split(post, min(3, len(post)))
        lows = [safe_float(b['low'].min()) for b in blocks if len(b)]
        if len(lows) >= 2:
            if lows[-1] >= lows[0] * 1.03:
                score += 1.8; reasons.append('长上影后回踩低点抬高，承接位置上移')
            elif lows[-1] < lows[0] * 0.97:
                score -= 1.8; reasons.append('长上影后低点下移，偏派发/供应压制')
        recent_low = safe_float(recent['low'].min())
        if recent_low >= top * 0.97:
            score += 1.0; reasons.append('近期回踩未明显跌回长影K实体顶下方')
        elif recent_low < safe_float(anchor.get('body_bottom')) * 0.98:
            score -= 2.2; reasons.append('近期回踩跌穿长影K实体底，供应区修复失败风险')

    # 3）后续冲高长上影量是否递减：供应递减为偏多；更大量长影为偏空。
    if len(post) >= 3:
        rng = (post['high'] - post['low']).replace(0, np.nan)
        bt = post[['open','close']].max(axis=1)
        post_upper_ratio = ((post['high'] - bt).clip(lower=0) / rng).fillna(0)
        near_zone = post[(post['high'] >= mid * 0.98) & (post_upper_ratio >= cfg['upper'] * 0.85)]
        if len(near_zone) >= 1:
            last_probe_vol = safe_float(near_zone['volume'].iloc[-1])
            if last_probe_vol < anchor_vol * 0.75:
                score += 1.3; reasons.append('后续长上影试探量低于原超大量，供应有递减迹象')
            elif last_probe_vol > anchor_vol * 1.05 and safe_float(near_zone['close'].iloc[-1]) < mid:
                score -= 2.5; reasons.append('再次放更大量长上影且收不回中轴，派发风险上升')
        else:
            score += 0.4; reasons.append('近期未再出现同级别放量长上影，供应反应暂未增强')

    # 4）回踩量能：小于冲高/锚点量，但高于旧低位死量，代表成交级别切换后的健康换手。
    if len(post) >= 3:
        pullback = recent[recent['close'] < recent['open']]
        if pullback.empty:
            pullback = recent
        pullback_vol = safe_float(pullback['volume'].tail(min(3, len(pullback))).mean())
        old = x.iloc[max(0, i-24):i]
        old_dead_vol = safe_float(old['volume'].quantile(0.35)) if len(old) else 0
        if pullback_vol > 0 and anchor_vol > 0:
            if pullback_vol < anchor_vol * 0.78:
                score += 0.9; reasons.append('回踩量低于长上影冲高量，未见同级别抛压')
            elif pullback_vol > anchor_vol * 1.05 and close < mid:
                score -= 1.6; reasons.append('回踩量反超冲高量且未站中轴，供应释放偏重')
            if old_dead_vol > 0 and pullback_vol > old_dead_vol * 1.15:
                score += 0.7; reasons.append('回踩量高于旧低位死量，成交级别未退回冷清状态')

    # 5）最终压力高点：站上长上影高点才是大周期彻底打穿；未站上不扣大分，只保持观察。
    if close >= high * 1.003:
        score += 1.5; reasons.append(f'收盘突破长上影高点({high:.2f})，供应区被完整打穿')
        state = '完整突破长上影供应区'
    elif close < mid and state.startswith('长上影'):
        state = '中轴未过，供应吸收未确认'

    # 周/月主锚点更重要，但全模块封顶，避免和压力带/普通回踩重复打分。
    score *= cfg['weight']
    score = max(-8.0, min(12.0, score))
    return {
        'score': round(score, 2),
        'state': state,
        'reasons': reasons[:8],
        'anchor': anchor,
        'shadow_mid': mid,
        'shadow_high': high,
        'shadow_body_top': top,
    }


def _v231_long_upper_shadow_supply_acceptance(daily, row=None):
    d = _v212_norm_df(daily)
    if d.empty or len(d) < 80:
        return {'v231_shadow_acceptance_score': 0.0, 'v231_shadow_state': '数据不足', 'v231_shadow_reasons': []}
    frames = [
        ('日线', d),
        ('周线', _v212_resample(d, 'W-FRI')),
        ('月线', _v212_resample(d, 'M')),
        ('季线', _v212_resample(d, 'Q')),
        ('年线', _v212_resample(d, 'Y')),
    ]
    evaluated = []
    for tf, df_tf in frames:
        anchors = _v231_long_upper_shadow_anchors(df_tf, tf=tf, max_anchors=4)
        for a in anchors:
            ev = _v231_evaluate_shadow_acceptance(df_tf, a, tf=tf)
            ev['tf'] = tf
            # 锚点质量与接受度相乘，不让低质量影线抢主导。
            ev['score'] = round(max(-8.0, min(12.0, safe_float(ev.get('score')) + min(2.0, safe_float(a.get('quality')) * 0.20))), 2)
            evaluated.append(ev)
    if not evaluated:
        return {'v231_shadow_acceptance_score': 0.0, 'v231_shadow_state': '未识别有效超大量长上影供应锚点', 'v231_shadow_reasons': []}
    # 选择最高有效周期/最高分作为主锚点，其他周期只做候选证据，不重复打分。
    best = sorted(evaluated, key=lambda e: (safe_float(e.get('score')), safe_float((e.get('anchor') or {}).get('quality'))), reverse=True)[0]
    score = safe_float(best.get('score'))
    state = best.get('state','')
    reasons = list(best.get('reasons') or [])
    tf = best.get('tf','')
    if score >= 7:
        final_state = f'{tf}长上影供应接受度改善'
    elif score >= 3:
        final_state = f'{tf}长上影供应区修复观察'
    elif score <= -3:
        final_state = f'{tf}长上影供应压制/派发风险'
    else:
        final_state = state or f'{tf}长上影供应区观察'
    anchor = best.get('anchor') or {}
    return {
        'v231_shadow_acceptance_score': round(score, 2),
        'v231_shadow_state': final_state,
        'v231_shadow_tf': tf,
        'v231_shadow_reasons': reasons,
        'v231_shadow_mid': safe_float(best.get('shadow_mid')),
        'v231_shadow_high': safe_float(best.get('shadow_high')),
        'v231_shadow_body_top': safe_float(best.get('shadow_body_top')),
        'v231_shadow_anchor_date': anchor.get('date',''),
        'v231_shadow_anchor_quality': safe_float(anchor.get('quality')),
        'v231_shadow_candidates': [
            {'tf': e.get('tf'), 'score': safe_float(e.get('score')), 'state': e.get('state'), 'date': (e.get('anchor') or {}).get('date','')}
            for e in evaluated[:8]
        ],
    }

# ======================= V23.1 Long Upper Shadow Supply Acceptance END =======================

def apply_v212_opportunity_to_row(row):
    if V212_ENABLED != '1':
        return row
    r=dict(row)
    daily=_v212_get_daily_df(r)
    zone_map=_v212_build_zone_map(daily) if not daily.empty else {'zones': [], 'core_supply_zone': None, 'nearest_supply': None, 'liquidity_void_score': 45, 'current': safe_float(r.get('close',0))}
    v23_supply=_v23_supply_absorption_regime_shift(daily, r) if not daily.empty else {'v23_supply_absorption_score':0.0,'v23_supply_state':'数据不足','v23_supply_reasons':[]}
    v231_shadow=_v231_long_upper_shadow_supply_acceptance(daily, r) if not daily.empty else {'v231_shadow_acceptance_score':0.0,'v231_shadow_state':'数据不足','v231_shadow_reasons':[]}
    volume_score, volume_reasons=_v212_volume_behavior(daily, r)
    price_score, price_reasons=_v212_price_structure(daily, zone_map, r)
    time_score, time_reasons=_v212_time_maturity(daily, r)
    space_score, space_reasons=_v212_space_score(daily, zone_map, r)
    character_score, style, character_reasons, character_flags=_v212_stock_character(daily, r)
    # 旧模型基础分保留，纳入但不主导所有细节。
    old_score=safe_float(r.get('v20_final_score', r.get('total_score', r.get('score',0))))
    old_norm=old_score if old_score<=100 else min(100, old_score)
    # Execution先根据前五层定仓位与胜率。
    v_scores={'volume_score':volume_score,'price_score':price_score,'time_score':time_score,'space_score':space_score,'character_score':character_score}
    execution=_v212_execution_plan(r, daily, zone_map, v_scores)
    targets=_v212_target_plan(r, daily, zone_map, execution)
    # 权重：旧模型+量价时空+股性+执行。供需压力带只通过price/space/execution体现。
    exec_score=_v212_clip(50 + (execution['v212_predict_win_rate']-0.60)*100 + (execution['v212_confirm_win_rate']-0.70)*60 - max(0, execution['v212_risk_pct']-0.07)*180)
    v23_score=safe_float(v23_supply.get('v23_supply_absorption_score',0))
    v23_norm=_v212_clip(v23_score/15*100)
    v231_score=safe_float(v231_shadow.get('v231_shadow_acceptance_score',0))
    v231_norm=_v212_clip(max(0.0, v231_score)/12*100)
    # V23.1只做长上影供应接受度确认/风险修正，不替代V20/V21，不和压力带重复打分。
    final=_v212_clip(old_norm*0.23 + volume_score*0.145 + price_score*0.145 + time_score*0.095 + space_score*0.125 + character_score*0.085 + exec_score*0.045 + v23_norm*0.085 + v231_norm*0.045)
    if v231_score <= -3:
        final -= min(7.0, abs(v231_score) * 0.8)
    # 硬降级：赔率/股性/风险
    gate=[]
    if execution['v212_risk_pct']>V212_MAX_CONFIRM_RISK:
        final-=8; gate.append('防守距离偏远')
    if character_score<45 or '低流动' in character_flags:
        final-=8; gate.append('近一年股性/流动性不支持正式Top')
    if space_score<42:
        final-=6; gate.append('上方空间/价格真空不足')
    if safe_float(v23_supply.get('v23_supply_absorption_score',0))>=10:
        gate.append('V23供应吸收/量能级别切换强确认')
    if safe_float(v231_shadow.get('v231_shadow_acceptance_score',0))>=7:
        gate.append('V23.1长上影供应接受度改善')
    elif safe_float(v231_shadow.get('v231_shadow_acceptance_score',0))<=-3:
        gate.append('V23.1长上影供应压制未解除')
    if str(r.get('v20_trade_tier','')).startswith('C档'):
        final-=6; gate.append('旧模型交易层为C档')
    final=_v212_clip(final)
    try:
        _m_inv = v20_trade_metrics(r)
    except Exception:
        _m_inv = {}
    if bool(_m_inv.get('trade_invalidated', False)):
        final = min(final, 35.0)
        gate.append(_m_inv.get('trade_invalid_reason', '收盘价低于防守/硬止损，交易假设失效'))
    # 推荐动作：只保留预判试仓/确认加仓，不输出已加速为新买点。
    state=execution['v212_state']
    if final>=V212_MIN_FORMAL_SCORE and state in ['预判试仓区','确认加仓区'] and execution['v212_risk_pct']<=V212_MAX_CONFIRM_RISK:
        action='V21.2正式候选'
    elif final>=70 and state in ['预判试仓区','确认加仓区']:
        action='V21.2试错/确认观察'
    elif state.startswith('扩张'):
        action='已扩张，不作为新开重仓推荐'
    else:
        action='观察等待'
    core=zone_map.get('core_supply_zone') or {}
    r.update({
        'v212_final_score': round(final,2),
        'v212_action': action,
        'v212_gate_notes': '；'.join(gate),
        'v212_volume_score': round(volume_score,2),
        'v212_volume_reasons': volume_reasons,
        'v212_price_score': round(price_score,2),
        'v212_price_reasons': price_reasons,
        'v212_time_score': round(time_score,2),
        'v212_time_reasons': time_reasons,
        'v212_space_score': round(space_score,2),
        'v212_space_reasons': space_reasons,
        'v212_stock_character_score': round(character_score,2),
        'v212_trade_style_tag': style,
        'v212_stock_character_reasons': character_reasons,
        'v212_stock_character_flags': character_flags,
        'v212_core_supply_low': safe_float(core.get('low',0)),
        'v212_core_supply_high': safe_float(core.get('high',0)),
        'v212_core_supply_line': safe_float(core.get('core',0)),
        'v212_core_supply_confidence': safe_float(core.get('strength',0)),
        'v212_core_supply_reason': core.get('reason',''),
        'v212_liquidity_void_score': safe_float(zone_map.get('liquidity_void_score',0)),
        'v212_targets': targets,
        'v23_supply_absorption_score': safe_float(v23_supply.get('v23_supply_absorption_score',0)),
        'v23_supply_state': v23_supply.get('v23_supply_state',''),
        'v23_supply_tf': v23_supply.get('v23_supply_tf',''),
        'v23_supply_reasons': v23_supply.get('v23_supply_reasons',[]),
        'v23_bear_top': safe_float(v23_supply.get('v23_bear_top',0)),
        'v23_bear_mid': safe_float(v23_supply.get('v23_bear_mid',0)),
        'v23_bear_bottom': safe_float(v23_supply.get('v23_bear_bottom',0)),
        'v23_bear_anchor_date': v23_supply.get('v23_bear_anchor_date',''),
        'v23_bear_anchor_quality': safe_float(v23_supply.get('v23_bear_anchor_quality',0)),
        'v23_old_downtrend_vol': safe_float(v23_supply.get('v23_old_downtrend_vol',0)),
        'v23_breakout_vol': safe_float(v23_supply.get('v23_breakout_vol',0)),
        'v23_pullback_vol': safe_float(v23_supply.get('v23_pullback_vol',0)),
        'v23_supply_all_candidates': v23_supply.get('v23_supply_all_candidates',[]),
        'v231_shadow_acceptance_score': safe_float(v231_shadow.get('v231_shadow_acceptance_score',0)),
        'v231_shadow_state': v231_shadow.get('v231_shadow_state',''),
        'v231_shadow_tf': v231_shadow.get('v231_shadow_tf',''),
        'v231_shadow_reasons': v231_shadow.get('v231_shadow_reasons',[]),
        'v231_shadow_mid': safe_float(v231_shadow.get('v231_shadow_mid',0)),
        'v231_shadow_high': safe_float(v231_shadow.get('v231_shadow_high',0)),
        'v231_shadow_body_top': safe_float(v231_shadow.get('v231_shadow_body_top',0)),
        'v231_shadow_anchor_date': v231_shadow.get('v231_shadow_anchor_date',''),
        'v231_shadow_anchor_quality': safe_float(v231_shadow.get('v231_shadow_anchor_quality',0)),
        'v231_shadow_candidates': v231_shadow.get('v231_shadow_candidates',[]),
    })
    r.update(execution)
    # 保留旧分，同时给最终排序一个融合分。
    r['v20_final_score_raw'] = safe_float(r.get('v20_final_score',0))
    # 保留旧V20分，不再用V21.2覆盖旧分，避免职责混乱；新增V22融合排序分单独承载最终排序。
    r['v22_signal_audit'] = v22_signal_ownership_audit(r)
    r['v22_composite_trade_score'] = v22_composite_trade_score(r)
    r['v22_score_valid'] = True
    r['v22_action'] = action.replace('V21.2', 'V22') if isinstance(action, str) else action
    try:
        r = v2562_apply_trade_invalidation(r)
    except Exception:
        pass
    return r


def v212_compact_fields(r):
    return {
        'v212_final_score': safe_float(r.get('v212_final_score',0)),
        'v212_action': r.get('v212_action',''),
        'v212_state': r.get('v212_state',''),
        'v212_volume_score': safe_float(r.get('v212_volume_score',0)),
        'v212_price_score': safe_float(r.get('v212_price_score',0)),
        'v212_time_score': safe_float(r.get('v212_time_score',0)),
        'v212_space_score': safe_float(r.get('v212_space_score',0)),
        'v212_stock_character_score': safe_float(r.get('v212_stock_character_score',0)),
        'v212_trade_style_tag': r.get('v212_trade_style_tag',''),
        'v212_core_supply_low': safe_float(r.get('v212_core_supply_low',0)),
        'v212_core_supply_high': safe_float(r.get('v212_core_supply_high',0)),
        'v212_core_supply_line': safe_float(r.get('v212_core_supply_line',0)),
        'v212_core_supply_confidence': safe_float(r.get('v212_core_supply_confidence',0)),
        'v212_confirm_line': safe_float(r.get('v212_confirm_line',0)),
        'v212_predict_fail_line': safe_float(r.get('v212_predict_fail_line',0)),
        'v212_confirm_fail_line': safe_float(r.get('v212_confirm_fail_line',0)),
        'v212_trend_fail_line': safe_float(r.get('v212_trend_fail_line',0)),
        'v212_risk_pct': safe_float(r.get('v212_risk_pct',0)),
        'v212_predict_win_rate': safe_float(r.get('v212_predict_win_rate',0)),
        'v212_confirm_win_rate': safe_float(r.get('v212_confirm_win_rate',0)),
        'v212_heavy_win_rate': safe_float(r.get('v212_heavy_win_rate',0)),
        'v212_predict_position': r.get('v212_predict_position',''),
        'v212_confirm_position': r.get('v212_confirm_position',''),
        'v212_heavy_position': r.get('v212_heavy_position',''),
        'v212_a_confirm_rule': r.get('v212_a_confirm_rule',''),
        'v212_b_confirm_rule': r.get('v212_b_confirm_rule',''),
        'v212_targets': r.get('v212_targets',[]),
        'v212_gate_notes': r.get('v212_gate_notes',''),
        'v22_composite_trade_score': safe_float(r.get('v22_composite_trade_score',0)),
        'v22_action': r.get('v22_action',''),
        'v23_supply_absorption_score': safe_float(r.get('v23_supply_absorption_score',0)),
        'v23_supply_state': r.get('v23_supply_state',''),
        'v23_supply_tf': r.get('v23_supply_tf',''),
        'v23_supply_reasons': r.get('v23_supply_reasons',[]),
        'v231_shadow_acceptance_score': safe_float(r.get('v231_shadow_acceptance_score',0)),
        'v231_shadow_state': r.get('v231_shadow_state',''),
        'v231_shadow_tf': r.get('v231_shadow_tf',''),
        'v231_shadow_reasons': r.get('v231_shadow_reasons',[]),
        'v231_shadow_mid': safe_float(r.get('v231_shadow_mid',0)),
        'v231_shadow_high': safe_float(r.get('v231_shadow_high',0)),
        'v231_shadow_body_top': safe_float(r.get('v231_shadow_body_top',0)),
        'v231_shadow_anchor_date': r.get('v231_shadow_anchor_date',''),
        'v23_bear_top': safe_float(r.get('v23_bear_top',0)),
        'v23_bear_mid': safe_float(r.get('v23_bear_mid',0)),
        'v23_bear_bottom': safe_float(r.get('v23_bear_bottom',0)),
        'v23_bear_anchor_date': r.get('v23_bear_anchor_date',''),
        'v22_signal_audit': r.get('v22_signal_audit',{}),
    }

# ======================= V22.0 Signal Registry + V21.2 Unified Opportunity Engine END =======================


# ========================= V24.1 实盘风控闭环：流动性、仓位、回测审计接口 =========================
def v241_effective_amount(row):
    """V24.1 成交额统一口径：优先amount，缺失时用close*volume保守估算。"""
    amount = safe_float(row.get("amount", row.get("成交额", 0)))
    close = safe_float(row.get("close", row.get("收盘", 0)))
    volume = safe_float(row.get("volume", row.get("成交量", 0)))
    if amount <= 0 and close > 0 and volume > 0:
        amount = close * volume
    return float(amount)


def v241_liquidity_profile(row):
    """V24.1 流动性硬门槛：基础层可入池，正式候选必须过实盘成交额门槛。"""
    amount = v241_effective_amount(row)
    if amount <= 0:
        return {
            "v241_amount_effective": 0.0,
            "v241_liquidity_tier": "L0数据缺失",
            "v241_liquidity_score": 0.0,
            "v241_formal_liquidity_ok": False,
            "v241_liquidity_reason": "成交额缺失，不能作为正式候选，只能人工复核。",
        }
    if amount < V24_1_ABSOLUTE_MIN_AMOUNT:
        return {
            "v241_amount_effective": amount,
            "v241_liquidity_tier": "L1低流动性",
            "v241_liquidity_score": 25.0,
            "v241_formal_liquidity_ok": False,
            "v241_liquidity_reason": f"成交额{amount/100000000:.2f}亿，低于绝对底线{V24_1_ABSOLUTE_MIN_AMOUNT/100000000:.2f}亿，正式候选剔除/降级。",
        }
    if amount < V24_1_MIN_AMOUNT_FOR_FORMAL:
        return {
            "v241_amount_effective": amount,
            "v241_liquidity_tier": "L2勉强可观察",
            "v241_liquidity_score": 50.0,
            "v241_formal_liquidity_ok": False,
            "v241_liquidity_reason": f"成交额{amount/100000000:.2f}亿，低于正式门槛{V24_1_MIN_AMOUNT_FOR_FORMAL/100000000:.2f}亿，最多观察不进Top3。",
        }
    if amount < V24_1_STRICT_AMOUNT_FOR_FORMAL:
        return {
            "v241_amount_effective": amount,
            "v241_liquidity_tier": "L3正式合格",
            "v241_liquidity_score": 75.0,
            "v241_formal_liquidity_ok": True,
            "v241_liquidity_reason": f"成交额{amount/100000000:.2f}亿，达到正式门槛但未达严格舒适线。",
        }
    return {
        "v241_amount_effective": amount,
        "v241_liquidity_tier": "L4舒适流动性",
        "v241_liquidity_score": 90.0 if amount < 300000000 else 100.0,
        "v241_formal_liquidity_ok": True,
        "v241_liquidity_reason": f"成交额{amount/100000000:.2f}亿，流动性满足实盘执行。",
    }


def v241_market_regime_multiplier():
    """V24.1 市场环境仓位乘数。默认neutral；workflow可传入 bull/range/bear/panic。"""
    regime = (V24_1_MARKET_REGIME or "neutral").lower()
    if V24_1_ENABLE_MARKET_REGIME != "1":
        return 1.0, "regime关闭"
    if regime in ["bull", "strong"]:
        return 1.10, "牛市/强势环境，允许正常偏积极仓位"
    if regime in ["range", "neutral", "震荡", "normal", ""]:
        return 1.00, "震荡/中性环境，按标准仓位执行"
    if regime in ["bear", "weak"]:
        return 0.45, "熊市/弱势环境，正式候选数量与仓位显著收缩"
    if regime in ["panic", "crash"]:
        return 0.0, "恐慌/系统性风险环境，原则上空仓或仅保留观察"
    return 0.85, f"未知regime={regime}，保守降仓"


def v241_position_plan(row):
    """V24.1 动态仓位：Top3不等权，结合等级、RR、流动性、买点距离、市场环境。"""
    if V24_1_ENABLE_DYNAMIC_POSITION != "1":
        return {"v241_position_pct": 0.0, "v241_position_text": "动态仓位关闭", "v241_position_reason": ""}
    try:
        _m_inv = v20_trade_metrics(row)
        if bool(row.get('v20_trade_invalidated', False)) or bool(_m_inv.get('trade_invalidated', False)) or bool(row.get('exclude_from_final', False)):
            _reason = str(row.get('v20_trade_invalid_reason', '') or _m_inv.get('trade_invalid_reason', '') or '收盘价低于防守/硬止损，交易假设失效')
            return {"v241_position_pct": 0.0, "v241_position_text": "仓位0%｜已破位失效", "v241_position_reason": _reason}
    except Exception:
        pass
    tier = str(row.get("v20_trade_tier", ""))
    rr = safe_float(row.get("v20_rr", row.get("risk_reward_ratio", row.get("rr", 0))))
    defense_dist = safe_float(row.get("v20_defense_dist", row.get("defense_dist", 0)))
    liq = v241_liquidity_profile(row)
    regime_mult, regime_text = v241_market_regime_multiplier()
    base = 0.0
    if tier.startswith("A档"):
        base = 0.18
    elif tier.startswith("B+档") or tier.startswith("B+重点"):
        base = 0.10
    elif tier.startswith("B档"):
        base = 0.05
    else:
        base = 0.0
    if rr >= 2.5:
        base += 0.04
    elif rr >= 1.8:
        base += 0.02
    elif rr > 0 and rr < 1.3:
        base -= 0.04
    if defense_dist > 0.10:
        base -= 0.05
    elif defense_dist > 0.07:
        base -= 0.025
    if not liq.get("v241_formal_liquidity_ok", False):
        base = min(base, 0.02)
    elif liq.get("v241_liquidity_score", 0) >= 90:
        base += 0.02
    position = max(0.0, min(0.22, base * regime_mult))
    if position <= 0:
        text = "观察/空仓"
    elif position <= 0.04:
        text = "轻观察仓≤4%"
    elif position <= 0.08:
        text = "轻仓5%-8%"
    elif position <= 0.14:
        text = "标准仓10%-14%"
    else:
        text = "强确认仓15%-22%"
    reason = f"{tier}；RR={rr:.2f}；防守距离={defense_dist:.1%}；{liq.get('v241_liquidity_tier')}；{regime_text}"
    return {"v241_position_pct": float(position), "v241_position_text": text, "v241_position_reason": reason}


def v241_write_backtest_config():
    """V24.1 输出回测配置：给独立walk-forward脚本读取，避免在日更流程里重跑历史。"""
    try:
        payload = {
            "model_version": MODEL_VERSION,
            "generated_at_bj": bj_time_str(),
            "period": "2020-01-01~2025-12-31",
            "method": "walk_forward",
            "review_windows": V20_REVIEW_WINDOWS,
            "cost_model": {"commission_tax_slippage_one_way": [0.001, 0.0015], "note": "A股按单边0.10%-0.15%成本压力测试"},
            "liquidity_gate": {
                "absolute_min_amount": V24_1_ABSOLUTE_MIN_AMOUNT,
                "formal_min_amount": V24_1_MIN_AMOUNT_FOR_FORMAL,
                "strict_amount": V24_1_STRICT_AMOUNT_FOR_FORMAL,
            },
            "metrics": ["win_rate", "expectancy_after_cost", "profit_factor", "max_drawdown", "median_return", "median_max_drawdown", "turnover", "capacity"],
            "slices": ["bull", "bear", "range", "high_liquidity", "low_liquidity", "A", "B+", "B", "hypothesis", "industry"],
        }
        with open(V24_1_BACKTEST_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"V24.1回测配置写入失败：{e}")

def classify_v20_trade_tier(row):
    """V20.1 A/B/C/B+分层：固定Top3仍可输出，但不能把买点未到票写成A档。"""
    if bool(row.get("v14_blocked", False)) or bool(row.get("regulatory_hard_exclude", False)):
        reason = row.get("v14_block_reason") or row.get("regulatory_risk_flags") or "命中硬风险/硬约束"
        return "硬风险剔除", str(reason)

    m = v20_trade_metrics(row)
    layers = v201_simplified_layer_scores(row)
    score = layers["v201_score"]
    if bool(m.get("trade_invalidated", False)):
        return "C档已破位/放弃", str(m.get("trade_invalid_reason", "收盘价低于防守/硬止损，交易假设失效"))
    tier_reasons = []

    severe_overheat = (m["rsi"] >= V20_OVERHEAT_RSI and m["cci"] >= V20_OVERHEAT_CCI) or m["bias20"] >= 0.22 or (m["bias20"] >= 0.18 and m["bias60"] >= 0.18)
    pressure_too_near = 0 < m["near_pressure"] < V20_MAX_NEAR_PRESSURE_A
    no_rr = 0 < m["rr"] < 1.20
    too_far_defense = m["defense_dist"] > 0.10 if m["defense_dist"] > 0 else False
    defense_not_comfy = m["defense_dist"] > V20_MAX_DEFENSE_DIST_A if m["defense_dist"] > 0 else False
    q_data = str(row.get("data_quality_tier", "")).upper().startswith("Q")
    liq_profile = v241_liquidity_profile(row)
    liquidity_not_formal = V24_1_ENABLE_LIQUIDITY_HARD_GATE == "1" and not bool(liq_profile.get("v241_formal_liquidity_ok", False))
    pressure_d = m["pressure_grade_rank"] == 1
    pressure_c = m["pressure_grade_rank"] == 2
    buy_zone_miss = bool(m["buy_zone_miss"])
    confirm_far = bool(m["confirm_far"])
    low_trade_quality_bplus = layers["trade_quality"] < V20_TRADE_QUALITY_FLOOR_BPLUS
    low_trade_quality_aminus = layers["trade_quality"] < V20_TRADE_QUALITY_FLOOR_A_MINUS

    if severe_overheat:
        tier_reasons.append("过热/乖离偏高")
    if pressure_too_near:
        tier_reasons.append("近端压力贴脸")
    if no_rr:
        tier_reasons.append("风险收益比不足")
    if too_far_defense:
        tier_reasons.append("离真实防守位超过10%")
    if q_data:
        tier_reasons.append("数据质量Q档需人工复核")
    if liquidity_not_formal:
        tier_reasons.append(str(liq_profile.get("v241_liquidity_reason", "流动性未达正式候选门槛")))
    if pressure_d:
        tier_reasons.append("压力带等级D，不能给A档")
    if buy_zone_miss:
        tier_reasons.append("当前价明显高于标准买区，买点未到/不舒服")
    if confirm_far and pressure_c:
        tier_reasons.append("仍未接近最终确认价，压力带C仅观察")
    if low_trade_quality_bplus:
        tier_reasons.append("交易质量过低，最高只能观察")

    # C档：硬性交易风险，不表达为正式买点。
    if severe_overheat or pressure_too_near or no_rr or too_far_defense:
        return "C档今日不交易", "；".join(tier_reasons) or "交易条件不足"

    # A档硬门槛。
    strong_context_for_c = (
        layers["structure_position"] >= 13.0 and layers["volume_behavior"] >= 8.0 and layers["trigger_confirmation"] >= 6.0
    ) or (m["pullback"] > 0 and m["rr"] >= 2.0 and m["defense_dist"] <= V20_MAX_DEFENSE_DIST_A_STRICT)

    can_a = True
    if score < V20_A_MIN_SCORE:
        can_a = False
    if not (m["rr"] >= V20_A_MIN_RR or m["target_dist"] >= 0.12):
        can_a = False
    if layers["trade_quality"] < 10.0 and m["trade_q"] < V20_A_MIN_TRADE_QUALITY:
        can_a = False
    if defense_not_comfy:
        can_a = False
    if low_trade_quality_aminus:
        can_a = False
    if q_data or pressure_d or buy_zone_miss or liquidity_not_formal:
        can_a = False
    if pressure_c and not strong_context_for_c:
        can_a = False
    if confirm_far and m["pressure_grade_rank"] <= 2 and not strong_context_for_c:
        can_a = False

    if can_a:
        return "A档正式可交易候选", "结构、承接、买点、防守位和风险收益比共同合格"

    # V25.6：A-是“高质量观察/相对最优救援”，不是正式A，不允许表达为开盘直接买。
    # 适用于分数接近A，但买点舒适度、压力确认或交易质量尚差一口气的情况。
    can_a_minus = (
        score >= V20_A_MINUS_MIN_SCORE
        and not liquidity_not_formal
        and not pressure_d
        and not severe_overheat
        and not pressure_too_near
        and not no_rr
        and not too_far_defense
        and not q_data
        and layers["trade_quality"] >= V20_TRADE_QUALITY_FLOOR_A_MINUS
        and (m["rr"] >= 1.35 or m["target_dist"] >= 0.10 or strong_context_for_c)
    )
    if can_a_minus:
        reason = "；".join(tier_reasons) if tier_reasons else "接近A档，但仍需突破/回踩确认，按观察执行"
        return "A-观察候选", reason

    # V24.1：流动性未达正式门槛时，最多观察，不给B+以上正式表达。
    if liquidity_not_formal:
        reason = "；".join(tier_reasons) if tier_reasons else str(liq_profile.get("v241_liquidity_reason", "流动性未达正式候选门槛"))
        if score >= 70 or strong_context_for_c:
            return "B档观察候选", reason
        return "C档今日不交易", reason

    # B+：质量较好，但因为压力未破/买点未触发/压力C等原因不能给A。
    # V25.6：B+可承接相对最优Top3，但交易质量太低时最高只能B。
    if (score >= V20_BPLUS_MIN_SCORE or layers["trade_quality"] >= 12 or strong_context_for_c) and not low_trade_quality_bplus:
        reason = "；".join(tier_reasons) if tier_reasons else "结构质量较好，但A档确认条件未全部满足"
        return "B+重点观察候选", reason

    # B：有结构或买点，但确认不足。
    if score >= 68 or m["pullback"] > 0 or m["trade_q"] >= 3 or m["rr"] >= 1.5:
        reason = "；".join(tier_reasons) if tier_reasons else "结构有看点，但确认/买点/RR未全部达到A档"
        return "B档观察候选", reason

    return "C档今日不交易", "综合质量不足，仅保留复盘观察"


def v20_condition_probability_hint(row):
    """读取已有条件概率表，给当前主假设一个轻量参考。样本不足时只提示不调权。"""
    hypo = str(row.get("v20_main_hypothesis", "") or detect_v20_main_hypothesis(row))
    try:
        if not os.path.exists(V20_CONDITION_TABLE_FILE):
            return {"sample_count": 0, "score_adj": 0.0, "text": "条件概率样本不足，暂不调权"}
        with open(V20_CONDITION_TABLE_FILE, "r", encoding="utf-8") as f:
            table = json.load(f)
        stats = table.get("by_hypothesis", {}).get(hypo, {}) if isinstance(table, dict) else {}
        n = int(stats.get("sample_count", 0) or 0)
        if n < 20:
            return {"sample_count": n, "score_adj": 0.0, "text": f"条件概率样本{n}，只观察不调权"}
        win = stats.get("T+5", {}) or stats.get("T+8", {}) or {}
        win_rate = safe_float(win.get("win_rate", 0))
        med_ret = safe_float(win.get("median_return", 0))
        med_dd = safe_float(win.get("median_max_drawdown", 0))
        adj = 0.0
        if win_rate >= 0.62 and med_ret > 0.025 and med_dd > -0.055:
            adj = 3.0 if n >= 50 else 1.5
        elif win_rate <= 0.45 and med_ret < 0 and med_dd < -0.06:
            adj = -3.0 if n >= 50 else -1.5
        text = f"{hypo}历史样本{n}，T+5胜率{win_rate:.1%}，中位收益{med_ret:.1%}，中位回撤{med_dd:.1%}"
        return {"sample_count": n, "score_adj": float(adj), "text": text}
    except Exception as e:
        return {"sample_count": 0, "score_adj": 0.0, "text": f"条件概率读取失败：{str(e)[:80]}"}


def select_final_signals_v20(deep_rows, history=None, limit=None):
    """V20.1最终选择：七层精简评分 + 严格A档 + 固定Top3分层输出。"""
    if history is None:
        history = {}
    limit = int(limit or V20_FIXED_TOP_N or V19_FIXED_TOP_N or V14_TARGET_PUSH_COUNT or RESULT_LIMIT or 3)
    audited = [v14_candidate_audit(r) for r in deep_rows]
    candidates = []
    diagnostics = []

    for r in audited:
        r = attach_data_quality_to_row(r)
        hypothesis = detect_v20_main_hypothesis(r)
        r["v20_main_hypothesis"] = hypothesis
        metrics = v20_trade_metrics(r)
        for k, v in metrics.items():
            r[f"v20_{k}"] = v
        layers = v201_simplified_layer_scores(r)
        for k, v in layers.items():
            r[f"v201_{k}"] = v
        tier, tier_reason = classify_v20_trade_tier(r)
        r["v20_trade_tier"] = tier
        r["v20_tier_reason"] = tier_reason
        # V24.1 实盘流动性与动态仓位。
        liq_profile = v241_liquidity_profile(r)
        r.update(liq_profile)
        r.update(v241_position_plan(r))
        r["v20_condition_sample_count"] = int(layers.get("feedback_sample_count", 0) or 0)
        r["v20_condition_probability_hint"] = str(layers.get("feedback_text", ""))
        r["v20_condition_score_adj"] = float(layers.get("feedback_adj", 0.0) or 0.0)
        r["v20_final_score"] = float(layers.get("v201_score", 0.0))
        # V21.2：在不删除旧V20.3.1颗粒口径的前提下，做统一机会引擎融合。
        try:
            r = apply_v212_opportunity_to_row(r)
        except Exception as _e:
            r["v212_error"] = str(_e)[:200]
        try:
            r = v2562_apply_trade_invalidation(r)
        except Exception:
            pass
        # V26：在旧底座特征全部生成后，统一进入“爆发前夜最终买入池”机构评分卡。
        try:
            r = v26_apply_to_row(r)
        except Exception as _e:
            r["v26_error"] = str(_e)[:200]
            r["v26_buy_eligible"] = False
        # V27：最终选股逻辑主引擎。旧模型/V26只做特征与审计，V27负责正式买入池/观察池/剔除池分流。
        try:
            r = v27_apply_to_row(r)
        except Exception as _e:
            r["v27_error"] = str(_e)[:200]
            r["v27_buy_eligible"] = False
        # 若V21.2/V22综合分生成失败，不再静默退回深度分参与最终正式排序；保留诊断。
        if not _valid_score_field(r, "v22_composite_trade_score") and not _valid_score_field(r, "v212_final_score"):
            r["v22_score_valid"] = False
            r["v22_invalid_reason"] = r.get("v212_error", "综合交易评分未独立生成")
            r["exclude_from_final"] = True

        if r.get("v14_blocked") or tier.startswith("硬风险"):
            r["v20_pool"] = "硬风险剔除"
            r["v20_skip_reason"] = tier_reason
            diagnostics.append(r)
        elif bool(r.get("exclude_from_final", False)) or bool(r.get("v20_trade_invalidated", False)):
            r["v20_pool"] = "风控剔除"
            r["v20_skip_reason"] = str(r.get("v20_trade_invalid_reason", r.get("v22_invalid_reason", "交易假设失效/综合分无效")))
            diagnostics.append(r)
        else:
            candidates.append(r)

    def tier_rank(x):
        t = str(x.get("v20_trade_tier", ""))
        if t.startswith("A档"):
            return 5
        if t.startswith("A-"):
            return 4
        if t.startswith("B+档") or t.startswith("B+重点"):
            return 3
        if t.startswith("B档"):
            return 2
        if t.startswith("C档"):
            return 1
        return 0

    candidates = sorted(
        candidates,
        key=lambda x: (
            1 if bool(x.get("v27_buy_eligible", False)) else 0,
            safe_float(x.get("v27_final_score", 0)),
            1 if bool(x.get("v26_buy_eligible", False)) else 0,
            safe_float(x.get("v26_final_buy_score", 0)),
            1 if str(x.get("v212_action", "")).startswith("V21.2正式") else 0,
            tier_rank(x),
            safe_float(x.get("v22_composite_trade_score", 0)) if _valid_score_field(x, "v22_composite_trade_score") else -1.0,
            safe_float(x.get("v212_final_score", 0)) if _valid_score_field(x, "v212_final_score") else -1.0,
            safe_float(x.get("v201_trade_quality", 0)),
            safe_float(x.get("v20_rr", 0)),
            safe_float(x.get("v201_structure_position", 0)) + safe_float(x.get("v201_volume_behavior", 0)),
        ),
        reverse=True,
    )

    # V24.1：市场regime控制正式输出数量。熊市减仓/少推，panic可空仓。
    regime = (V24_1_MARKET_REGIME or "neutral").lower()
    effective_limit = limit
    if V24_1_ENABLE_MARKET_REGIME == "1" and regime in ["bear", "weak"]:
        effective_limit = min(effective_limit, V24_1_BEAR_MAX_FORMAL)
    if V24_1_ENABLE_MARKET_REGIME == "1" and regime in ["panic", "crash"]:
        effective_limit = min(effective_limit, V24_1_PANIC_MAX_FORMAL)

    final = []
    for r in candidates:
        if bool(r.get("exclude_from_final", False)) or bool(r.get("v20_trade_invalidated", False)):
            rr = dict(r)
            rr["v20_pool"] = "风控剔除"
            rr["v20_skip_reason"] = str(rr.get("v20_trade_invalid_reason", "交易假设失效"))
            diagnostics.append(rr)
            continue
        if not _valid_score_field(r, "v22_composite_trade_score") and not _valid_score_field(r, "v212_final_score"):
            rr = dict(r)
            rr["v20_pool"] = "综合分无效剔除"
            rr["v20_skip_reason"] = str(rr.get("v22_invalid_reason", "综合交易评分未独立生成"))
            diagnostics.append(rr)
            continue
        if V27_ENABLE_CORE_ENGINE == "1" and V27_ENABLE_FINAL_GATE == "1" and not bool(r.get("v27_buy_eligible", False)):
            rr = dict(r)
            rr["v20_pool"] = str(rr.get("v27_pool", "V27观察池/未入最终买入池"))
            reasons = rr.get("v27_block_reasons", []) or rr.get("v27_observe_reasons", [])
            if isinstance(reasons, list):
                reasons = "；".join([str(x) for x in reasons if str(x).strip()])
            rr["v20_skip_reason"] = reasons or f"V27分{safe_float(rr.get('v27_final_score',0)):.2f}低于{V27_MIN_BUY_SCORE:.0f}或当前触发/RR/防守位未通过"
            diagnostics.append(rr)
            continue
        if V27_ENABLE_CORE_ENGINE != "1" and V26_ENABLE_INSTITUTIONAL_SCORECARD == "1" and not bool(r.get("v26_buy_eligible", False)):
            rr = dict(r)
            rr["v20_pool"] = "V26高质量观察/未入最终买入池"
            rr["v20_skip_reason"] = str(rr.get("v26_hard_gate_reasons", "V26最终买入池硬条件未通过")) or f"V26分{safe_float(rr.get('v26_final_buy_score',0)):.2f}低于{V26_MIN_BUY_SCORE:.0f}"
            diagnostics.append(rr)
            continue
        key = f"{r.get('date','')}_{r.get('code','')}"
        if V14_IGNORE_HISTORY_FOR_RERUN != "1" and key in history:
            rr = dict(r)
            rr["v20_pool"] = "后台跟踪"
            rr["v20_skip_reason"] = "signals_history已推送过"
            diagnostics.append(rr)
            continue
        ok_portfolio, portfolio_reason = v26_portfolio_accept(r, final)
        if not ok_portfolio:
            rr = dict(r)
            rr["v20_pool"] = "V26组合去相关降级"
            rr["v20_skip_reason"] = portfolio_reason
            diagnostics.append(rr)
            continue
        r["v20_pool"] = "V27最终买入池" if V27_ENABLE_CORE_ENGINE == "1" else "V26最终买入池"
        r["v20_rank"] = len(final) + 1
        final.append(r)
        if len(final) >= effective_limit:
            break

    selected_codes = {str(r.get("code")) for r in final}
    for r in candidates:
        if str(r.get("code")) not in selected_codes:
            rr = dict(r)
            rr["v20_pool"] = "后台跟踪"
            rr["v20_skip_reason"] = "未进入V26最终买入池，进入跟踪池用于条件概率复盘"
            diagnostics.append(rr)

    return final, diagnostics[:80], audited


def _v20_compact_row(r, pool=""):
    """V20.1 score card：既能复盘路径，也能做条件概率分组。"""
    base = _v19_compact_row(r, pool or r.get("v20_pool", ""))
    base.update({
        "model_version": MODEL_VERSION,
        "pool": pool or r.get("v20_pool", base.get("pool", "")),
        "v20_rank": r.get("v20_rank", ""),
        "v20_trade_tier": r.get("v20_trade_tier", ""),
        "v20_tier_reason": r.get("v20_tier_reason", ""),
        "v20_main_hypothesis": r.get("v20_main_hypothesis", ""),
        "v20_final_score": safe_float(r.get("v20_final_score", 0)),
        "v20_condition_sample_count": int(r.get("v20_condition_sample_count", 0) or 0),
        "v20_condition_probability_hint": r.get("v20_condition_probability_hint", ""),
        "v20_rr": safe_float(r.get("v20_rr", r.get("risk_reward_ratio", r.get("rr", 0)))),
        "v20_defense": safe_float(r.get("v20_defense", r.get("defensive_price", r.get("trade_defense", 0)))),
        "v20_defense_dist": safe_float(r.get("v20_defense_dist", 0)),
        "v20_near_pressure": safe_float(r.get("v20_near_pressure", r.get("near_pressure_dist", 0))),
        "v20_target_dist": safe_float(r.get("v20_target_dist", 0)),
        "v241_amount_effective": safe_float(r.get("v241_amount_effective", 0)),
        "v241_liquidity_tier": r.get("v241_liquidity_tier", ""),
        "v241_liquidity_score": safe_float(r.get("v241_liquidity_score", 0)),
        "v241_formal_liquidity_ok": bool(r.get("v241_formal_liquidity_ok", False)),
        "v241_position_pct": safe_float(r.get("v241_position_pct", 0)),
        "v241_position_text": r.get("v241_position_text", ""),
        "v241_position_reason": r.get("v241_position_reason", ""),
        "v201_risk_filter": safe_float(r.get("v201_risk_filter", 0)),
        "v201_structure_position": safe_float(r.get("v201_structure_position", 0)),
        "v201_pressure_support": safe_float(r.get("v201_pressure_support", 0)),
        "v201_volume_behavior": safe_float(r.get("v201_volume_behavior", 0)),
        "v201_trigger_confirmation": safe_float(r.get("v201_trigger_confirmation", 0)),
        "v201_trade_quality": safe_float(r.get("v201_trade_quality", 0)),
        "v201_feedback_adj": safe_float(r.get("v201_feedback_adj", 0)),
        "v201_precise_trigger_line": safe_float(r.get("v201_precise_trigger_line", 0)),
        "v201_precise_trigger_valid": bool(r.get("v201_precise_trigger_valid", False)),
        "v201_precise_trigger_note": r.get("v201_precise_trigger_note", ""),
        # V25.6供需压力带诊断字段：给回测、三号员工和人工复盘使用，不影响daily主流程。
        "v256_line_role": r.get("v201_v256_line_role", r.get("v256_line_role", r.get("xhu_coreline_role", ""))),
        "v256_core_score": safe_float(r.get("v201_v256_core_score", r.get("xhu_coreline_core_score", 0))),
        "v256_neural_score": safe_float(r.get("v201_v256_neural_score", r.get("xhu_coreline_neural_score", 0))),
        "v256_hvn_score": safe_float(r.get("v201_v256_hvn_score", r.get("xhu_coreline_hvn_score", 0))),
        "v256_lvn_score": safe_float(r.get("v201_v256_lvn_score", r.get("xhu_coreline_lvn_above_score", 0))),
        "v256_upper_supply_thinness": safe_float(r.get("xhu_coreline_upper_supply_thinness", 0)),
        "v256_fake_breakout_count": int(safe_float(r.get("v201_v256_fake_breakout_count", r.get("xhu_fake_breakout_count", 0)))),
        "v256_breakout_quality_score": safe_float(r.get("v201_v256_breakout_quality_score", 0)),
        "v256_same_source_dedup_note": r.get("v201_v256_same_source_dedup_note", ""),
        "xhu_pressure_core_lower": safe_float(r.get("xhu_pressure_core_lower", 0)),
        "xhu_pressure_core_upper": safe_float(r.get("xhu_pressure_core_upper", 0)),
        "xhu_effective_confirm_price": safe_float(r.get("xhu_effective_confirm_price", 0)),
        "xhu_pressure_desc": r.get("xhu_pressure_desc", ""),
        "bottom_pattern_type": r.get("bottom_pattern_type", ""),
        "bottom_pattern_score": safe_float(r.get("score_bottom_reversal_pattern", r.get("bottom_pattern_score", 0))),
        "bottom_pattern_neckline": safe_float(r.get("bottom_pattern_neckline", 0)),
        "bottom_pattern_confirmed": bool(r.get("bottom_pattern_confirmed", False)),
        "bottom_pattern_volume_quality": safe_float(r.get("bottom_pattern_volume_quality", 0)),
        "bottom_pattern_trigger_quality": safe_float(r.get("bottom_pattern_trigger_quality", 0)),
        "bottom_pattern_retest_quality": safe_float(r.get("bottom_pattern_retest_quality", 0)),
        "bottom_pattern_desc": r.get("bottom_pattern_desc", ""),
        "v26_final_buy_score": safe_float(r.get("v26_final_buy_score", 0)),
        "v26_buy_eligible": bool(r.get("v26_buy_eligible", False)),
        "v26_position_tier": r.get("v26_position_tier", ""),
        "v26_hard_gate_reasons": r.get("v26_hard_gate_reasons", ""),
        "v26_explosion_eve_score": safe_float(r.get("v26_explosion_eve_score", 0)),
        "v26_key_structure_score": safe_float(r.get("v26_key_structure_score", 0)),
        "v26_supply_absorption_mother_score": safe_float(r.get("v26_supply_absorption_mother_score", 0)),
        "v26_support_defense_score": safe_float(r.get("v26_support_defense_score", 0)),
        "v26_breakout_expansion_score": safe_float(r.get("v26_breakout_expansion_score", 0)),
        "v26_pricing_rr_score": safe_float(r.get("v26_pricing_rr_score", 0)),
        "v26_sector_lifecycle_score": safe_float(r.get("v26_sector_lifecycle_score", 0)),
        "v26_market_score": safe_float(r.get("v26_market_score", 0)),
        "v26_execution_score": safe_float(r.get("v26_execution_score", 0)),
        "v26_failure_similarity_risk": safe_float(r.get("v26_failure_similarity_risk", 0)),
        "v26_signal_age": r.get("v26_signal_age", ""),
        "v26_signal_freshness_score": safe_float(r.get("v26_signal_freshness_score", 0)),
        "v26_same_source_dedup_note": r.get("v26_same_source_dedup_note", ""),
        "data_quality_tier": r.get("data_quality_tier", ""),
        "data_quality_reason": r.get("data_quality_reason", ""),
    })
    try:
        base.update(v212_compact_fields(r))
    except Exception:
        pass
    return base


def _v20_signal_group_key(row):
    hypo = str(row.get("v20_main_hypothesis") or row.get("main_hypothesis") or "综合结构机会")
    tier = str(row.get("v20_trade_tier") or row.get("tier") or "")
    return f"{hypo}|{tier}"


def build_v20_condition_probability_placeholder(payload):
    """当前文件级统计占位：真实T+表现需在后续交易日读取历史score_cards与K线补全。"""
    by_hypothesis = {}
    rows = []
    for section in ["final_top3", "watch_pool"]:
        for r in payload.get(section, []) or []:
            hypo = str(r.get("v20_main_hypothesis") or "综合结构机会")
            d = by_hypothesis.setdefault(hypo, {"sample_count": 0, "pending_count": 0, "windows": V20_REVIEW_WINDOWS})
            d["sample_count"] += 1
            d["pending_count"] += 1
            rows.append({
                "hypothesis": hypo,
                "tier": r.get("v20_trade_tier", ""),
                "pool": r.get("pool", ""),
                "code": r.get("code", ""),
                "date": r.get("date", ""),
                "v20_final_score": r.get("v20_final_score", 0),
                "v201_precise_trigger_line": r.get("v201_precise_trigger_line", 0),
                "status": "pending_forward_kline",
            })
    table = {
        "model_version": MODEL_VERSION,
        "generated_at_bj": bj_time_str(),
        "note": "V20.1已保存条件概率分组底座；真实T+1/T+3/T+5/T+8/T+13/T+20收益需在后续交易日读取历史score_cards与K线后更新。",
        "review_windows": V20_REVIEW_WINDOWS,
        "by_hypothesis": by_hypothesis,
    }
    try:
        with open(V20_CONDITION_TABLE_FILE, "w", encoding="utf-8") as f:
            json.dump(table, f, ensure_ascii=False, indent=2)
        if rows:
            pd.DataFrame(rows).to_csv(V20_SIGNAL_FEEDBACK_CSV, index=False, encoding="utf-8-sig")
    except Exception as e:
        print(f"V20.1条件概率占位表保存失败：{e}")
    return table



def _v20_parse_date_safe(x):
    try:
        if not x:
            return None
        return pd.to_datetime(str(x)).date()
    except Exception:
        return None


def _v20_history_code(entry):
    code = str(entry.get("code", "") or "").strip()
    digits = "".join(ch for ch in code if ch.isdigit())
    if len(digits) >= 6:
        return digits[-6:].zfill(6)
    return code.zfill(6) if code.isdigit() else code


def _v20_find_current_row_for_code(code, current_rows):
    code = str(code).zfill(6)
    for r in current_rows or []:
        if str(r.get("code", "")).zfill(6) == code:
            return r
    return None


def _v20_read_latest_cache_for_tracking(code):
    """只读全历史缓存做跟踪，不在报告阶段联网补拉，避免输出阶段变慢/不稳定。"""
    try:
        bs_code = _bs_code_from_plain_code(str(code).zfill(6))
        if not bs_code:
            return None, None
        df = read_full_history_flat_cache(bs_code, cache_scope="base", min_rows=20)
        if df is None or df.empty:
            return None, None
        return df, df.iloc[-1].to_dict()
    except Exception:
        return None, None


def _v20_trading_days_since_from_df(df, signal_date, current_date=None):
    try:
        if df is None or df.empty or not signal_date:
            return None
        d = df.copy()
        d["date"] = pd.to_datetime(d["date"], errors="coerce")
        d = d.dropna(subset=["date"])
        sig = pd.to_datetime(signal_date, errors="coerce")
        if pd.isna(sig):
            return None
        if current_date:
            cur = pd.to_datetime(current_date, errors="coerce")
            if pd.isna(cur):
                cur = d["date"].max()
        else:
            cur = d["date"].max()
        return int(((d["date"] > sig) & (d["date"] <= cur)).sum())
    except Exception:
        return None


def _v20_kline_row_metrics(row_or_k):
    if not row_or_k:
        return {}
    close = _v20_float(row_or_k, "close", "收盘")
    open_ = _v20_float(row_or_k, "open", "开盘")
    high = _v20_float(row_or_k, "high", "最高")
    low = _v20_float(row_or_k, "low", "最低")
    pct = _v20_float(row_or_k, "pct_chg", "pct", "涨跌幅")
    vr1 = _v20_float(row_or_k, "vr1")
    volr = _v20_float(row_or_k, "volr")
    if pct == 0 and close > 0 and _v20_float(row_or_k, "preclose") > 0:
        pct = close / _v20_float(row_or_k, "preclose") - 1
    elif abs(pct) > 1.0:
        pct = pct / 100.0
    return {"close": close, "open": open_, "high": high, "low": low, "pct": pct, "vr1": vr1, "volr": volr}


def build_v20_signal_lifecycle(history, current_rows, final_signals=None, current_dates=None):
    """V20.2推荐生命周期跟踪：
    今日新Top3之外，继续跟踪T+1/T+3/T+5/T+8/T+13/T+20窗口内仍健康的旧推荐。
    目的：昨天推荐后今天健康回调/小涨，不应该从报告体系里消失。
    """
    if V20_ENABLE_SIGNAL_LIFECYCLE != "1":
        return []
    history = history or {}
    current_rows = current_rows or []
    final_codes = {str(x.get("code", "")).zfill(6) for x in (final_signals or [])}
    # V20.3.1修复：dates在主流程中通常是倒序（最新日期在前）。
    # 生命周期跟踪必须取最大/最新日期，而不是current_dates[-1]，否则CHECK_DAYS>1时会误取较旧日期。
    parsed_dates = [_v20_parse_date_safe(x) for x in (current_dates or [])]
    parsed_dates = [x for x in parsed_dates if x is not None]
    today = max(parsed_dates) if parsed_dates else datetime.now().date()
    current_date = str(today)

    # 每只代码只取最近一次历史推荐，避免重复刷屏。
    hist_items = []
    for key, entry in history.items():
        if not isinstance(entry, dict):
            continue
        code = _v20_history_code(entry)
        if not code or code in final_codes:
            continue
        sig_date = str(entry.get("date", "") or "")
        sd = _v20_parse_date_safe(sig_date)
        if sd is None or sd >= today:
            continue
        if (today - sd).days > max(3, V20_LIFECYCLE_LOOKBACK_DAYS):
            continue
        hist_items.append((sd, code, entry))
    hist_items = sorted(hist_items, key=lambda x: x[0], reverse=True)
    seen = set()
    out = []

    for sd, code, entry in hist_items:
        if code in seen:
            continue
        seen.add(code)
        if len(out) >= V20_LIFECYCLE_MAX_ITEMS:
            break

        cur_row = _v20_find_current_row_for_code(code, current_rows)
        df = None
        kline_row = None
        if cur_row is None:
            df, kline_row = _v20_read_latest_cache_for_tracking(code)
            source = "cache"
        else:
            source = "current_deep_row"
        k = _v20_kline_row_metrics(cur_row or kline_row or {})
        if not k or safe_float(k.get("close", 0)) <= 0:
            out.append({
                "code": code,
                "name": entry.get("name", ""),
                "signal_date": entry.get("date", ""),
                "t_window": "?",
                "lifecycle_status": "待复盘",
                "lifecycle_reason": "今日未进入深度评分且缓存缺少最新K线，暂无法判断是否健康。",
                "lifecycle_action": "保留历史记录，等待下一次有有效K线后复盘。",
                "giveup_condition": entry.get("giveup_condition", "跌破原防守位/硬止损则放弃"),
            })
            continue

        if df is None:
            df, _ = _v20_read_latest_cache_for_tracking(code)
        tdays = _v20_trading_days_since_from_df(df, entry.get("date", ""), current_date) if df is not None else None
        if tdays is None:
            # 回退为近似自然日窗口，不用于精确统计，只用于报告提示。
            tdays = max(1, (today - sd).days)

        entry_close = safe_float(entry.get("entry_close", entry.get("close", entry.get("v20_close", 0))))
        if entry_close <= 0:
            entry_close = safe_float(entry.get("price_plan", {}).get("close", 0)) if isinstance(entry.get("price_plan", {}), dict) else 0
        cur_close = safe_float(k.get("close", 0))
        cur_low = safe_float(k.get("low", 0))
        cur_high = safe_float(k.get("high", 0))
        pct = safe_float(k.get("pct", 0))
        vr1 = safe_float(k.get("vr1", 0))
        volr = safe_float(k.get("volr", 0))
        defense = safe_float(entry.get("v20_defense", entry.get("defensive_price", entry.get("trade_defense", 0))))
        hard_stop = safe_float(entry.get("hard_stop", entry.get("hard_stop_price", 0)))
        confirm_price = safe_float(entry.get("confirm_price", entry.get("xhu_effective_confirm_price", 0)))
        target1 = safe_float(entry.get("target1", entry.get("target1_price", 0)))
        ret = cur_close / entry_close - 1 if cur_close > 0 and entry_close > 0 else 0.0

        status = "等待二次确认"
        reason = "结构仍在跟踪窗口内，但今日缺少新的强确认。"
        action = "继续观察，不追；等待重新站上触发线/确认线，或回踩关键位不破后再评估。"
        if hard_stop > 0 and cur_close < hard_stop * 0.998:
            status = "放弃"
            reason = f"收盘{cur_close:.2f}跌破硬止损{hard_stop:.2f}附近。"
            action = "按放弃条件处理，不再作为跟踪候选。"
        elif defense > 0 and cur_close < defense * 0.995:
            status = "风险升高"
            reason = f"收盘{cur_close:.2f}跌破真实防守位{defense:.2f}附近。"
            action = "不加仓，不低吸；除非快速收回防守位，否则降级/放弃。"
        elif pct <= -0.035 and (vr1 >= 1.5 or volr >= 2.0):
            status = "风险升高"
            reason = f"当日放量下跌或分歧明显，pct={pct:.1%}，vr1={vr1:.2f}。"
            action = "只观察修复，不再作为新买点；次日不能收回关键位则放弃。"
        elif confirm_price > 0 and cur_close >= confirm_price * 1.002:
            status = "强确认"
            reason = f"收盘站上确认价{confirm_price:.2f}，推荐后进入强确认路径。"
            action = "可交给三号员工盘中/次日评估是否突破后回踩不破再加仓。"
        elif defense > 0 and cur_low <= defense * 1.035 and cur_close >= defense * 0.995:
            status = "健康回踩"
            reason = f"回踩防守区附近但收盘未有效跌破，低点{cur_low:.2f}，防守{defense:.2f}。"
            action = "继续跟踪；若缩量企稳后重新站上短线触发线，可视为二次买点观察。"
        elif ret >= 0 and pct >= -0.02:
            status = "继续健康"
            reason = f"推荐后收益{ret:.1%}，今日未出现破位或明显放量长阴。"
            action = "继续跟踪，不因未进今日Top3而删除；等待T+3/T+5路径验证。"
        elif ret > -0.035 and pct > -0.025:
            status = "健康震荡"
            reason = f"推荐后小幅回撤{ret:.1%}，仍属于正常震荡范围。"
            action = "继续观察关键位承接；不追高，等转强确认。"

        out.append({
            "code": code,
            "name": entry.get("name", ""),
            "signal_date": entry.get("date", ""),
            "current_date": current_date,
            "t_window": int(tdays) if str(tdays).isdigit() or isinstance(tdays, int) else tdays,
            "source": source,
            "entry_close": float(entry_close),
            "current_close": float(cur_close),
            "current_low": float(cur_low),
            "current_high": float(cur_high),
            "current_pct": float(pct),
            "return_since_signal": float(ret),
            "defense": float(defense),
            "hard_stop": float(hard_stop),
            "confirm_price": float(confirm_price),
            "target1": float(target1),
            "v20_trade_tier": entry.get("v20_trade_tier", ""),
            "v20_main_hypothesis": entry.get("v20_main_hypothesis", ""),
            "lifecycle_status": status,
            "lifecycle_reason": reason,
            "lifecycle_action": action,
            "confirm_condition": entry.get("confirm_condition", "站上原确认线/触发线并获得量能确认"),
            "giveup_condition": entry.get("giveup_condition", "有效跌破原防守位/硬止损则放弃"),
        })

    # 展示优先级：强确认/健康回踩/继续健康优先，其次风险升高/待复盘。
    order = {"强确认": 5, "健康回踩": 4, "继续健康": 3, "健康震荡": 3, "等待二次确认": 2, "风险升高": 1, "待复盘": 0, "放弃": -1}
    out = sorted(out, key=lambda x: (order.get(str(x.get("lifecycle_status")), 0), -int(x.get("t_window") if str(x.get("t_window")).isdigit() else 99)), reverse=True)
    try:
        with open(V20_SIGNAL_LIFECYCLE_FILE, "w", encoding="utf-8") as f:
            json.dump({"generated_at_bj": bj_time_str(), "model_version": MODEL_VERSION, "items": out}, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"V20.2生命周期跟踪保存失败：{e}")
    return out


def save_v20_outputs(final_signals, diagnostics, audited_rows, dates=None, meta=None, history=None, lifecycle_tracking=None):
    """保存 V20.1 score cards / 日报 / 复盘归因底座。"""
    try:
        meta = meta or {}
        dates = dates or []
        selected_codes = {str(x.get("code")) for x in final_signals}
        watch_rows = []
        blocked_rows = []
        for r in diagnostics or []:
            if str(r.get("code")) in selected_codes:
                continue
            if r.get("v14_blocked") or r.get("v20_pool") == "硬风险剔除":
                blocked_rows.append(_v20_compact_row(r, "硬风险剔除"))
            else:
                watch_rows.append(_v20_compact_row(r, "后台跟踪"))

        final_cards = [_v20_compact_row(r, "V26最终买入池") for r in final_signals]
        if lifecycle_tracking is None:
            lifecycle_tracking = build_v20_signal_lifecycle(history or {}, audited_rows or [], final_signals or [], current_dates=dates or [])
        tier_counts = {}
        for r in final_cards:
            tier_counts[r.get("v20_trade_tier", "未知")] = tier_counts.get(r.get("v20_trade_tier", "未知"), 0) + 1

        payload = {
            "model_version": MODEL_VERSION,
            "generated_at_bj": bj_time_str(),
            "dates": dates,
            "meta": meta,
            "rule": {
                "fixed_top_n": V20_FIXED_TOP_N,
                "tier_counts": tier_counts,
                "a_tier_is_strict": True,
                "strict_a_rules": [
                    "压力带D不能A",
                    "压力带C必须有强承接/大周期修复/舒服防守位",
                    "防守距离超过8%不能A",
                    "明显高于标准买区不能A",
                    "最终确认价太远且压力等级C/D不能A",
                    "今日新推荐与近期推荐跟踪分开，健康回踩/小涨不因未进Top3而消失",
                ],
                "condition_feedback_enabled": V20_ENABLE_CONDITION_FEEDBACK == "1",
                "review_windows": V20_REVIEW_WINDOWS,
            },
            "final_top3": final_cards,
            "watch_pool": watch_rows[:120],
            "blocked_pool": blocked_rows[:80],
            "lifecycle_tracking": lifecycle_tracking[:120] if lifecycle_tracking else [],
        }
        with open(V20_SCORE_CARDS_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f"V20.3评分卡已保存：{V20_SCORE_CARDS_FILE}")
        try:
            with open(V212_OUTPUT_FILE, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            print(f"V23完整融合评分卡已保存：{V212_OUTPUT_FILE}")
        except Exception as _e:
            print(f"V23完整融合评分卡保存失败：{_e}")
        build_v20_condition_probability_placeholder(payload)

        # 兼容旧workflow artifact名字。
        try:
            with open(V19_SCORE_CARDS_FILE, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

        lines = []
        lines.append("一号员工 V23.0 完整版日报｜V20生产底座 + V21.2交易机会层 + V23供应吸收量能级别切换")
        lines.append(f"生成时间：{bj_time_str()}")
        lines.append(f"日期：{', '.join(dates) if dates else '未知'}")
        lines.append(f"最终买入池目标上限：{V20_FIXED_TOP_N}；实际输出：{len(final_signals)}（允许少于上限，宁缺毋滥）")
        lines.append(f"分层统计：{tier_counts}")
        lines.append("口径：V20.3.1负责候选质量与风险前置；V21.2负责交易机会与执行计划；V23新增“供应吸收/大阴修复/量能级别切换”确认层，同一信号只在归属层打分，其他层只引用，避免重复堆分。")
        lines.append("")
        if final_cards:
            lines.append("【今日V22正式Top3】")
            for i, row in enumerate(final_cards, 1):
                lines.append(f"{i}. {row['name']}({row['code']}) | V26买入分 {safe_float(row.get('v26_final_buy_score',0)):.2f} | {row.get('v26_position_tier','')} | V22融合分 {safe_float(row.get('v22_composite_trade_score',0)):.2f} | {row['v20_trade_tier']} | 主假设：{row['v20_main_hypothesis']}")
                if safe_float(row.get('v26_final_buy_score',0)) > 0:
                    lines.append(f"   V26母因子：爆发前夜{safe_float(row.get('v26_explosion_eve_score',0)):.1f}/20｜关键位{safe_float(row.get('v26_key_structure_score',0)):.1f}/15｜供应吸收{safe_float(row.get('v26_supply_absorption_mother_score',0)):.1f}/12｜承接{safe_float(row.get('v26_support_defense_score',0)):.1f}/12｜突破{safe_float(row.get('v26_breakout_expansion_score',0)):.1f}/12｜定价{safe_float(row.get('v26_pricing_rr_score',0)):.1f}/12")
                if row.get("bottom_pattern_type"):
                    lines.append(f"   底部形态：{row.get('bottom_pattern_type')} 分{row.get('bottom_pattern_score',0):.1f} 颈线{row.get('bottom_pattern_neckline',0):.2f} {'已确认' if row.get('bottom_pattern_confirmed') else '观察中'}")
                lines.append(f"   七层：结构{row['v201_structure_position']:.1f} 压力{row['v201_pressure_support']:.1f} 资金{row['v201_volume_behavior']:.1f} 触发{row['v201_trigger_confirmation']:.1f} 交易{row['v201_trade_quality']:.1f} 风险{row['v201_risk_filter']:.1f}")
                if row.get("v201_precise_trigger_line", 0):
                    lines.append(f"   日线精确触发线：{row['v201_precise_trigger_line']:.2f}（{'已计算' if row.get('v201_precise_trigger_valid') else '待日线平台精算'}）")
                lines.append(f"   RR={row['v20_rr']:.2f}；防守={row['v20_defense']:.2f}；防守距离={row['v20_defense_dist']:.1%}；原因：{row['v20_tier_reason']}")
                if row.get('v212_final_score') is not None:
                    lines.append(f"   V22交易机会分={safe_float(row.get('v212_final_score')):.2f}｜{row.get('v212_action','')}｜状态：{row.get('v212_state','')}｜股性：{row.get('v212_trade_style_tag','')}")
                    if safe_float(row.get('v23_supply_absorption_score',0)) > 0:
                        lines.append(f"   V23供应吸收={safe_float(row.get('v23_supply_absorption_score')):.2f}/15｜{row.get('v23_supply_tf','')}｜{row.get('v23_supply_state','')}｜大阴顶={safe_float(row.get('v23_bear_top',0)):.2f}｜锚点={row.get('v23_bear_anchor_date','')}")
                    lines.append(f"   六层：量{safe_float(row.get('v212_volume_score')):.1f} 价{safe_float(row.get('v212_price_score')):.1f} 时{safe_float(row.get('v212_time_score')):.1f} 空{safe_float(row.get('v212_space_score')):.1f} 股性{safe_float(row.get('v212_stock_character_score')):.1f}")
                    if safe_float(row.get('v212_core_supply_line',0)) > 0:
                        lines.append(f"   核心结构因子：压力带{safe_float(row.get('v212_core_supply_low')):.2f}-{safe_float(row.get('v212_core_supply_high')):.2f}，确认线{safe_float(row.get('v212_confirm_line')):.2f}，置信{safe_float(row.get('v212_core_supply_confidence')):.0f}")
                    lines.append(f"   胜率/仓位：预判{safe_float(row.get('v212_predict_win_rate')):.0%} 仓位{row.get('v212_predict_position','')}；确认{safe_float(row.get('v212_confirm_win_rate')):.0%} 加到{row.get('v212_confirm_position','')}；重仓{safe_float(row.get('v212_heavy_win_rate')):.0%} 上限{row.get('v212_heavy_position','')}")
                    lines.append(f"   失败线：预判{safe_float(row.get('v212_predict_fail_line')):.2f}；确认{safe_float(row.get('v212_confirm_fail_line')):.2f}；趋势{safe_float(row.get('v212_trend_fail_line')):.2f}")
                    _tgts = row.get('v212_targets') or []
                    if _tgts:
                        lines.append('   目标位概率：' + '；'.join([f"{safe_float(t.get('price')):.2f}({safe_float(t.get('probability')):.0%},{t.get('reason','')})" for t in _tgts[:5]]))
                    if row.get('v212_a_confirm_rule'):
                        lines.append(f"   A/B加仓：{row.get('v212_a_confirm_rule')} {row.get('v212_b_confirm_rule')}")
                    _why = []
                    for _k in ['v212_volume_reasons','v212_price_reasons','v212_time_reasons','v212_space_reasons','v212_stock_character_reasons']:
                        _v = row.get(_k) or []
                        if isinstance(_v, list):
                            _why.extend(_v[:2])
                    if _why:
                        lines.append('   V22交易层原因：' + '；'.join(_why[:5]))
        if lifecycle_tracking:
            lines.append("")
            lines.append("【近期推荐生命周期跟踪】")
            for i, tr in enumerate((lifecycle_tracking or [])[:20], 1):
                lines.append(f"{i}. {tr.get('name','')}({tr.get('code','')}) | {tr.get('lifecycle_status','')} | T+{tr.get('t_window','?')} | 收益{safe_float(tr.get('return_since_signal',0)):.1%}")
                lines.append(f"   说明：{tr.get('lifecycle_reason','')}；处理：{tr.get('lifecycle_action','')}")
        if watch_rows:
            lines.append("")
            lines.append("【后台跟踪前20】")
            for i, row in enumerate(watch_rows[:20], 1):
                lines.append(f"{i}. {row['name']}({row['code']}) | V20.3分 {row['v20_final_score']:.2f} | {row['v20_trade_tier']} | {row['v20_main_hypothesis']}")
        with open(V20_DAILY_REPORT_FILE, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        try:
            with open(V19_DAILY_REPORT_FILE, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
        except Exception:
            pass
        print(f"V20.3日报已保存：{V20_DAILY_REPORT_FILE}")
        try:
            with open(V212_DAILY_REPORT_FILE, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
            print(f"V23完整融合日报已保存：{V212_DAILY_REPORT_FILE}")
        except Exception as _e:
            print(f"V23完整融合日报保存失败：{_e}")

        review_lines = [
            "一号员工 V20.1 复盘归因报告底座",
            f"生成时间：{bj_time_str()}",
            "复盘窗口：T+1/T+3/T+5/T+8/T+13/T+20",
            f"评分卡：{V20_SCORE_CARDS_FILE}",
            f"条件概率表：{V20_CONDITION_TABLE_FILE}",
            f"信号反馈CSV：{V20_SIGNAL_FEEDBACK_CSV}",
            f"推荐生命周期：{V20_SIGNAL_LIFECYCLE_FILE}",
            "说明：当前已加入推荐后生命周期跟踪；后续继续补充forward return、max drawdown、是否破防守位、是否触及目标位。",
        ]
        with open(V20_REVIEW_REPORT_FILE, "w", encoding="utf-8") as f:
            f.write("\n".join(review_lines))
        try:
            with open(V19_REVIEW_REPORT_FILE, "w", encoding="utf-8") as f:
                f.write("\n".join(review_lines))
        except Exception:
            pass
        print(f"V20.3复盘报告底座已保存：{V20_REVIEW_REPORT_FILE}")

        audit = {
            "generated_at_bj": bj_time_str(),
            "model_version": MODEL_VERSION,
            "audit_note": "V20.2初期不自动大幅调权；样本<20只只观察，20-50只轻微建议，>50只才允许中等调权。A档门槛已收紧，固定Top3不等于每天必须买；近期推荐进入生命周期跟踪，健康回踩/小涨不因未进Top3而消失。",
            "tier_counts": tier_counts,
            "simplification": {
                "kline_shapes": "阳包阴/双阳夹阴/跳空/强阳/假阴真阳统一归入触发质量，不再零散堆分。",
                "volume_shapes": "倍量/平量/阳梯量/平台均量/低量精准线归入资金行为与平台量能结构。",
                "trend_indicators": "RSI/CCI主要做过热扣分，普通均线多头只做背景。",
                "pressure_output": "报告只强调日线精确触发线、供需压力带、最终压力上沿、真实防守位。",
            },
        }
        with open(V20_MODEL_AUDIT_LOG_FILE, "w", encoding="utf-8") as f:
            json.dump(audit, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"V20.1输出保存失败：{e}")

# ======================= V20.1 条件概率反馈闭环模块 END =======================


def build_error_message(e):
    """Telegram错误诊断兜底，避免异常处理阶段再次 NameError。"""
    import html, traceback
    tb = traceback.format_exc()
    msg = [
        "❌ <b>一号员工运行异常</b>",
        "",
        f"错误类型：<code>{html.escape(type(e).__name__)}</code>",
        f"错误信息：<code>{html.escape(str(e)[:800])}</code>",
        "",
        "<b>Traceback 摘要：</b>",
        f"<pre>{html.escape(tb[-2500:])}</pre>",
    ]
    return "\n".join(msg)



def _v16_grade_rank(grade):
    """V16/V19候选等级排序兜底，避免旧版本函数名缺失导致主流程失败。"""
    if grade is None:
        return 0
    g = str(grade).strip().upper()
    order = {
        "S+": 90, "S": 85, "S-": 80,
        "A+": 75, "A": 70, "A-": 65,
        "B+": 55, "B": 50, "B-": 45,
        "C+": 35, "C": 30, "C-": 25,
        "D": 10, "": 0,
    }
    return order.get(g, 0)



# ========================= V25：真实Walk-Forward回测引擎 =========================
def _v25_plain_code(bs_code_or_code):
    try:
        s = str(bs_code_or_code)
        if '.' in s:
            return s.split('.')[-1].zfill(6)
        return ''.join([c for c in s if c.isdigit()])[-6:].zfill(6)
    except Exception:
        return ""


def v25_load_full_cache_for_code(bs_code):
    """读取单票全历史缓存，供回测按日期截断，避免未来函数。"""
    try:
        df = read_full_history_flat_cache(bs_code, cache_scope="deep", min_rows=120)
        if df is None or df.empty:
            # 兼容旧分层缓存
            df = read_cached_kline(bs_code, cache_scope="deep", min_rows=120)
        if df is None or df.empty:
            return None
        df = normalize_kline_df(df)
        if df is None or df.empty:
            return None
        df = df[df["date"] >= V25_BACKTEST_DATA_START].copy()
        if df.empty:
            return None
        df = df.sort_values("date").reset_index(drop=True)
        return df
    except Exception:
        return None


def v25_build_backtest_universe():
    stock_list = get_a_stock_list_from_full_cache_universe()
    if stock_list is None or stock_list.empty:
        stock_list = get_a_stock_list()
    if stock_list is None or stock_list.empty:
        return pd.DataFrame(columns=["代码", "名称", "bs_code"])
    stock_list = stock_list.copy()
    if V25_BACKTEST_MAX_STOCKS > 0:
        stock_list = stock_list.head(V25_BACKTEST_MAX_STOCKS)
    return stock_list[["代码", "名称", "bs_code"]]




def v25_1_parse_cli_args():
    """V25.1启动参数：同一套选股逻辑，daily/backtest两种执行模式。"""
    parser = argparse.ArgumentParser(description="V25.3 一号员工：daily选股 / backtest逐日回测 / preflight自检")
    parser.add_argument("--mode", choices=["daily", "backtest"], default=os.environ.get("V25_1_MODE", "backtest" if V25_ENABLE_BACKTEST == "1" else "daily"), help="运行模式：daily=今日选股；backtest=历史逐日回测")
    parser.add_argument("--start", default=os.environ.get("V25_BACKTEST_START", V25_BACKTEST_START), help="回测开始日期，例如 2022-04-08")
    parser.add_argument("--end", default=os.environ.get("V25_BACKTEST_END", V25_BACKTEST_END), help="回测结束日期，例如 2023-04-08")
    parser.add_argument("--data-start", default=os.environ.get("V25_BACKTEST_DATA_START", V25_BACKTEST_DATA_START), help="预热数据开始日期，建议至少提前3-5年")
    parser.add_argument("--topn", type=int, default=int(os.environ.get("V25_BACKTEST_TOP_N", str(V25_BACKTEST_TOP_N))), help="每日回测TopN")
    parser.add_argument("--deep-limit", type=int, default=int(os.environ.get("V25_BACKTEST_DEEP_LIMIT", str(V25_BACKTEST_DEEP_LIMIT))), help="每日进入深度评分数量")
    parser.add_argument("--base-limit", type=int, default=int(os.environ.get("V25_1_BACKTEST_BASE_LIMIT", str(V25_1_BACKTEST_BASE_LIMIT))), help="回测中基础层排序后进入深度候选的最大数量")
    parser.add_argument("--min-amount", type=float, default=float(os.environ.get("V25_BACKTEST_MIN_AMOUNT", str(V25_BACKTEST_MIN_AMOUNT))), help="正式回测信号最低成交额")
    parser.add_argument("--out-dir", default=os.environ.get("V25_BACKTEST_OUTPUT_DIR", V25_BACKTEST_OUTPUT_DIR), help="回测报告输出目录")
    parser.add_argument("--max-dates", type=int, default=int(os.environ.get("V25_BACKTEST_MAX_DATES", str(V25_BACKTEST_MAX_DATES))), help="最多回测交易日，0=不限制")
    parser.add_argument("--date-step", type=int, default=int(os.environ.get("V25_BACKTEST_DATE_STEP", str(V25_BACKTEST_DATE_STEP))), help="交易日步长，1=每天")
    parser.add_argument("--seed", type=int, default=int(os.environ.get("V25_2_RANDOM_SEED", str(V25_2_RANDOM_SEED))), help="回测随机种子，保证可复现")
    parser.add_argument("--self-check", action="store_true", default=os.environ.get("V25_2_SELF_CHECK", "0") == "1", help="只做启动前完整性/依赖/配置自检，不实际选股或回测")
    return parser.parse_args()


def v25_1_apply_runtime_args(args):
    """把CLI参数写回全局配置，确保daily/backtest共用同一个文件、同一套核心逻辑。"""
    global V25_ENABLE_BACKTEST, V25_BACKTEST_START, V25_BACKTEST_END, V25_BACKTEST_DATA_START
    global V25_BACKTEST_TOP_N, V25_BACKTEST_DEEP_LIMIT, V25_1_BACKTEST_BASE_LIMIT
    global V25_BACKTEST_MIN_AMOUNT, V25_BACKTEST_OUTPUT_DIR, V25_BACKTEST_MAX_DATES, V25_BACKTEST_DATE_STEP, V25_2_RANDOM_SEED
    if args.mode == "backtest":
        V25_ENABLE_BACKTEST = "1"
    else:
        V25_ENABLE_BACKTEST = "0"
    V25_BACKTEST_START = str(args.start)
    V25_BACKTEST_END = str(args.end)
    V25_BACKTEST_DATA_START = str(args.data_start)
    V25_BACKTEST_TOP_N = int(args.topn)
    V25_BACKTEST_DEEP_LIMIT = int(args.deep_limit)
    V25_1_BACKTEST_BASE_LIMIT = int(args.base_limit)
    V25_BACKTEST_MIN_AMOUNT = float(args.min_amount)
    V25_BACKTEST_OUTPUT_DIR = str(args.out_dir)
    V25_BACKTEST_MAX_DATES = int(args.max_dates)
    V25_BACKTEST_DATE_STEP = max(1, int(args.date_step))
    V25_2_RANDOM_SEED = int(args.seed)
    try:
        import random
        random.seed(V25_2_RANDOM_SEED)
        np.random.seed(V25_2_RANDOM_SEED)
    except Exception:
        pass
    return args.mode



def v25_3_core_function_inventory():
    """V25.3核心引擎体检：证明daily/backtest实际引用的是同一套生产选股函数，而不是空壳占位。"""
    import inspect
    rows = []
    required = list(dict.fromkeys(list(V25_2_PREFLIGHT_REQUIRED_FUNCTIONS) + [
        "select_final_signals_v20", "build_v20_signal_lifecycle", "build_v19_price_plan",
        "save_v20_outputs", "build_message", "get_daily_kline", "get_a_stock_list",
        "get_a_stock_list_from_full_cache_universe", "v25_run_one_day", "v25_make_trade_record",
        "v25_generate_backtest_report",
    ]))
    for name in required:
        obj = globals().get(name)
        exists = callable(obj)
        src_lines = 0
        src_chars = 0
        suspicious = ""
        if exists:
            try:
                src = inspect.getsource(obj)
                src_lines = len(src.splitlines())
                src_chars = len(src)
                low = src.lower()
                if "pass" in low and src_lines <= 6:
                    suspicious = "疑似空实现"
                if "notimplemented" in low or "todo" in low:
                    suspicious = (suspicious + ";" if suspicious else "") + "疑似未完成"
            except Exception as e:
                suspicious = f"无法读取源码:{str(e)[:60]}"
        rows.append({"function": name, "callable": exists, "source_lines": src_lines, "source_chars": src_chars, "warning": suspicious})
    return pd.DataFrame(rows)


def v25_2_preflight_check(mode="daily", raise_on_error=True):
    """V25.3启动前自检：函数完整性、配置安全、No-Lookahead约束、daily/backtest一致性。"""
    problems = []
    warnings = []
    inv = v25_3_core_function_inventory()
    missing = inv[~inv["callable"].astype(bool)] if inv is not None and not inv.empty else pd.DataFrame()
    if missing is not None and not missing.empty:
        for _, r in missing.iterrows():
            problems.append(f"缺少核心函数: {r.get('function')}")
    # 不是所有辅助函数都需要很长，但生产核心函数必须不是空壳。
    min_line_rules = {
        "process_stock_base": 30,
        "process_stock_deep": 30,
        "select_deep_targets_v10": 30,
        "select_final_signals_v20": 30,
        "v25_run_one_day": 30,
        "v25_generate_backtest_report": 30,
    }
    if inv is not None and not inv.empty:
        for fn, min_lines in min_line_rules.items():
            hit = inv[inv["function"] == fn]
            if not hit.empty and bool(hit.iloc[0].get("callable")):
                if int(hit.iloc[0].get("source_lines", 0)) < min_lines:
                    problems.append(f"核心函数疑似不完整: {fn} source_lines={int(hit.iloc[0].get('source_lines',0))} < {min_lines}")
        bad = inv[(inv["warning"].astype(str) != "") & (inv["warning"].astype(str) != "nan")]
        for _, r in bad.iterrows():
            warnings.append(f"函数源码警告: {r.get('function')} {r.get('warning')}")
    # 基础参数一致性
    if int(V25_BACKTEST_TOP_N) <= 0:
        problems.append("V25_BACKTEST_TOP_N必须>0")
    if int(V25_BACKTEST_DEEP_LIMIT) <= 0:
        problems.append("V25_BACKTEST_DEEP_LIMIT必须>0")
    if int(V25_1_BACKTEST_BASE_LIMIT) > 0 and int(V25_1_BACKTEST_BASE_LIMIT) < int(V25_BACKTEST_DEEP_LIMIT):
        problems.append("V25_1_BACKTEST_BASE_LIMIT不应小于V25_BACKTEST_DEEP_LIMIT，否则深度候选可能被过早截断")
    if int(V25_BACKTEST_TOP_N) > int(V25_BACKTEST_DEEP_LIMIT):
        problems.append("V25_BACKTEST_TOP_N不应大于V25_BACKTEST_DEEP_LIMIT")
    if mode == "backtest":
        if V25_1_STRICT_NO_LOOKAHEAD != "1":
            problems.append("回测模式必须开启V25_1_STRICT_NO_LOOKAHEAD=1")
        if str(V25_BACKTEST_DATA_START) >= str(V25_BACKTEST_START):
            problems.append("DATA_START_DATE应早于BACKTEST_START_DATE，用于历史预热")
        if float(V25_BACKTEST_MIN_AMOUNT) < float(V24_1_ABSOLUTE_MIN_AMOUNT):
            problems.append("回测最低成交额低于V24.1绝对流动性底线，可能导致结果失真")
        if V25_1_REQUIRE_CACHE_ONLY_BACKTEST != "1":
            warnings.append("回测建议使用只读全历史缓存，避免历史回测中临时拉取数据造成不一致")
    # 关键输出目录可写性
    try:
        os.makedirs(V25_BACKTEST_OUTPUT_DIR, exist_ok=True)
        test_path = os.path.join(V25_BACKTEST_OUTPUT_DIR, ".v25_3_write_test")
        with open(test_path, "w", encoding="utf-8") as f:
            f.write("ok")
        try:
            os.remove(test_path)
        except Exception:
            pass
    except Exception as e:
        problems.append(f"回测输出目录不可写: {V25_BACKTEST_OUTPUT_DIR} {e}")

    # 输出核心函数清单，便于审查时不再只看框架。
    try:
        os.makedirs(V25_BACKTEST_OUTPUT_DIR, exist_ok=True)
        inv.to_csv(os.path.join(V25_BACKTEST_OUTPUT_DIR, "v25_3_core_function_inventory.csv"), index=False, encoding="utf-8-sig")
    except Exception as e:
        warnings.append(f"核心函数清单保存失败: {e}")

    msg = "\n".join(problems)
    warn_msg = "\n".join(warnings)
    if warnings:
        print("V25.3启动前自检提示:\n" + warn_msg, flush=True)
    if problems and raise_on_error:
        raise RuntimeError("V25.3启动前自检失败:\n" + msg)
    if problems:
        print("V25.3启动前自检警告:\n" + msg, flush=True)
    else:
        print(f"V25.3启动前自检通过：mode={mode} required_functions={len(inv)} seed={V25_2_RANDOM_SEED} inventory={os.path.join(V25_BACKTEST_OUTPUT_DIR, 'v25_3_core_function_inventory.csv')}", flush=True)
    return {"ok": not problems, "problems": problems, "warnings": warnings, "inventory": inv}

def v25_1_assert_no_lookahead_df(df, as_of_date, symbol=""):
    """硬性防未来函数：任何模型输入K线都不能超过as_of_date。"""
    if V25_1_STRICT_NO_LOOKAHEAD != "1" or df is None or getattr(df, "empty", True):
        return True
    if "date" not in df.columns:
        return True
    max_date = str(pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d").max())
    if max_date > str(as_of_date):
        raise AssertionError(f"No-Lookahead Guard触发：{symbol} max_date={max_date} > as_of_date={as_of_date}")
    return True


def v25_1_classify_failure_reason(rec):
    """给亏损/失败样本做交易语言归因，便于报告看懂。"""
    try:
        ret = safe_float(rec.get("strategy_ret", 0))
        adverse = safe_float(rec.get("max_adverse", 0))
        exit_reason = str(rec.get("exit_reason", ""))
        risk_flags = str(rec.get("risk_flags", ""))
        hyp = str(rec.get("main_hypothesis", ""))
        amount = safe_float(rec.get("amount", 0))
        tier = str(rec.get("v20_trade_tier", ""))
        if ret >= 0:
            return "盈利/未失败"
        if exit_reason == "hard_stop" or adverse <= -0.08:
            return "触发硬止损/路径回撤过大"
        if "长上影" in risk_flags or "滞涨" in risk_flags or "假突破" in risk_flags:
            return "疑似假突破或放量滞涨"
        if "压力" in hyp and adverse < -0.04:
            return "压力带突破后承接失败"
        if amount > 0 and amount < V25_BACKTEST_MIN_AMOUNT:
            return "流动性不足"
        if "B" in tier or "C" in tier:
            return "低等级信号被验证不足"
        if adverse < -0.04 and ret < 0:
            return "买点偏追高/防守距离不舒服"
        return "普通失败/需人工复盘"
    except Exception:
        return "归因异常"

def v25_prepare_cache(stock_list):
    cache = {}
    failed = []
    for i, row in stock_list.iterrows():
        bs_code = row.get("bs_code", "")
        df = v25_load_full_cache_for_code(bs_code)
        if df is None or df.empty:
            failed.append({"bs_code": bs_code, "code": row.get("代码", ""), "name": row.get("名称", ""), "reason": "no_history_cache"})
            continue
        # 至少要能覆盖回测开始前的预热期和回测期间
        if str(df["date"].max()) < V25_BACKTEST_START or str(df["date"].min()) > V25_BACKTEST_END:
            failed.append({"bs_code": bs_code, "code": row.get("代码", ""), "name": row.get("名称", ""), "reason": "date_range_not_cover"})
            continue
        cache[str(bs_code)] = df
        if len(cache) % 500 == 0:
            print(f"V25回测缓存加载：{len(cache)}只", flush=True)
    return cache, failed


def v25_get_trading_dates(cache):
    dates = set()
    for df in cache.values():
        if df is None or df.empty:
            continue
        ds = df[(df["date"] >= V25_BACKTEST_START) & (df["date"] <= V25_BACKTEST_END)]["date"].astype(str).tolist()
        dates.update(ds)
    out = sorted(dates)
    if V25_BACKTEST_DATE_STEP > 1:
        out = out[::max(1, V25_BACKTEST_DATE_STEP)]
    if V25_BACKTEST_MAX_DATES > 0:
        out = out[:V25_BACKTEST_MAX_DATES]
    return out


def v25_make_get_daily_kline(cache, current_date):
    def _patched_get_daily_kline(bs_code, lookback_days=None, cache_scope="deep"):
        df = cache.get(str(bs_code))
        if df is None or df.empty:
            return None
        d = df[df["date"] <= current_date].copy()
        if d.empty:
            return None
        if cache_scope == "base":
            # 基础层只取尾部，模拟当日可见数据，避免全量慢算。
            out = d.tail(max(FULL_CACHE_BASE_TAIL_ROWS, BASE_KLINE_LOOKBACK_DAYS, 260)).reset_index(drop=True)
            v25_1_assert_no_lookahead_df(out, current_date, bs_code)
            return out
        out = d.tail(max(DEEP_KLINE_LOOKBACK_DAYS, 900)).reset_index(drop=True)
        v25_1_assert_no_lookahead_df(out, current_date, bs_code)
        return out
    return _patched_get_daily_kline


def v25_run_one_day(date, stock_list, cache):
    """对单个历史交易日完整跑基础->深度->最终TopN，所有K线截断到date。"""
    global get_daily_kline, CHECK_DAYS
    old_get_daily_kline = get_daily_kline
    old_check_days = CHECK_DAYS
    get_daily_kline = v25_make_get_daily_kline(cache, date)
    CHECK_DAYS = 1
    base_rows = []
    deep_rows = []
    failed = []
    # V25.1：预先构建当日可交易集合，避免逐票重复 set(df[date]) 导致极慢。
    traded_today = set()
    for _bs, _df in cache.items():
        try:
            if _df is not None and not _df.empty and str(date) in set(_df["date"].astype(str).values):
                traded_today.add(str(_bs))
        except Exception:
            pass
    try:
        for _, row in stock_list.iterrows():
            bs_code = str(row.get("bs_code", ""))
            if bs_code not in cache or bs_code not in traded_today:
                continue
            # 当天未交易/停牌的票不进入当天回测信号。
            df = cache.get(bs_code)
            if df is None or df.empty:
                continue
            try:
                rows = process_stock_base(row)
                for r in rows:
                    if str(r.get("date", "")) == str(date):
                        if V25_1_STRICT_NO_LOOKAHEAD == "1" and str(r.get("date", "")) > str(date):
                            raise AssertionError(f"基础信号日期越界：{r.get('code','')} signal={r.get('date')} asof={date}")
                        base_rows.append(r)
            except Exception as e:
                failed.append({"date": date, "stage": "base", "code": row.get("代码", ""), "name": row.get("名称", ""), "error": str(e)[:200]})
        if not base_rows:
            return [], [], [], failed, {}
        base_rows = sorted(base_rows, key=lambda x: (safe_float(x.get("base_bucket_rank_score", x.get("base_score", 0))), safe_float(x.get("base_total_score", x.get("base_score", 0))), safe_float(x.get("score", 0))), reverse=True)
        # V25.1：基础层仍全市场扫，但回测进入深度前先截取Top base_limit，避免逐日全市场深度灾难。
        base_rows_for_deep = base_rows[:max(V25_1_BACKTEST_BASE_LIMIT, V25_BACKTEST_DEEP_LIMIT)] if V25_1_BACKTEST_BASE_LIMIT > 0 else base_rows
        deep_targets, bucket_stats = select_deep_targets_v10(base_rows_for_deep, V25_BACKTEST_DEEP_LIMIT)
        for r in deep_targets:
            try:
                rows = process_stock_deep(r)
                for rr in rows:
                    if str(rr.get("date", "")) == str(date):
                        if V25_1_STRICT_NO_LOOKAHEAD == "1" and str(rr.get("date", "")) > str(date):
                            raise AssertionError(f"深度信号日期越界：{rr.get('code','')} signal={rr.get('date')} asof={date}")
                        deep_rows.append(rr)
            except Exception as e:
                failed.append({"date": date, "stage": "deep", "code": r.get("code", ""), "name": r.get("name", ""), "error": str(e)[:200]})
        deep_rows = sorted(deep_rows, key=lambda x: (safe_float(x.get("trade_priority_score", 0)), safe_float(x.get("score_trade_quality", 0)), safe_float(x.get("total_score", 0))), reverse=True)
        final_signals, diagnostics, audited = select_final_signals_v20(deep_rows, history={}, limit=V25_BACKTEST_TOP_N)
        # 回测强制应用成交额门槛，确保实盘可成交。
        final_filtered = []
        for s in final_signals:
            amount = v241_effective_amount(s)
            if amount >= V25_BACKTEST_MIN_AMOUNT:
                final_filtered.append(s)
        return base_rows, deep_rows, final_filtered[:V25_BACKTEST_TOP_N], failed, bucket_stats
    finally:
        get_daily_kline = old_get_daily_kline
        CHECK_DAYS = old_check_days


def v25_calc_forward_returns(signal, cache):
    bs_code = str(signal.get("bs_code", ""))
    date = str(signal.get("date", ""))
    df = cache.get(bs_code)
    if df is None or df.empty or not date:
        return {}
    d = df.sort_values("date").reset_index(drop=True)
    # 这里允许完整历史用于计算未来收益，但入口索引必须严格等于signal_date；模型输入端已被截断。
    idxs = d.index[d["date"].astype(str) == date].tolist()
    if not idxs:
        return {}
    i = int(idxs[-1])
    entry_close = safe_float(d.loc[i, "close"])
    if entry_close <= 0:
        return {}
    total_cost = 2 * (V25_BACKTEST_COST_SINGLE_SIDE + V25_BACKTEST_SLIPPAGE_SINGLE_SIDE)
    out = {
        "entry_close": entry_close,
        "entry_date": date,
        "code": signal.get("code", _v25_plain_code(bs_code)),
        "name": signal.get("name", ""),
        "bs_code": bs_code,
    }
    for w in V25_BACKTEST_WINDOWS:
        j = i + int(w)
        if j >= len(d):
            out[f"ret_t{w}"] = np.nan
            out[f"net_ret_t{w}"] = np.nan
            continue
        exit_close = safe_float(d.loc[j, "close"])
        raw = exit_close / entry_close - 1 if entry_close > 0 else np.nan
        out[f"ret_t{w}"] = raw
        out[f"net_ret_t{w}"] = raw - total_cost
    # 短线执行路径：默认最多持有到最大窗口，中途触发硬止损/止盈先出。
    max_w = max(V25_BACKTEST_WINDOWS) if V25_BACKTEST_WINDOWS else 20
    exit_reason = "time_exit"
    exit_i = min(i + max_w, len(d) - 1)
    path = d.iloc[i + 1: min(i + max_w, len(d) - 1) + 1].copy()
    if V25_BACKTEST_USE_DYNAMIC_EXIT == "1" and not path.empty:
        hard_stop_price = safe_float(signal.get("hard_stop", 0)) or safe_float(signal.get("v20_defense", 0)) or entry_close * (1 - V25_BACKTEST_STOP_LOSS)
        hard_stop_price = max(0.01, min(hard_stop_price, entry_close * (1 - 0.015)))
        take_profit_price = entry_close * (1 + V25_BACKTEST_TAKE_PROFIT)
        for k, r in path.iterrows():
            low = safe_float(r.get("low", 0)); high = safe_float(r.get("high", 0)); close = safe_float(r.get("close", 0))
            if low > 0 and low <= hard_stop_price:
                exit_i = int(k); exit_reason = "hard_stop"; break
            if high > 0 and high >= take_profit_price:
                exit_i = int(k); exit_reason = "take_profit"; break
    exit_close = safe_float(d.loc[exit_i, "close"])
    out["exit_date"] = str(d.loc[exit_i, "date"])
    out["exit_close"] = exit_close
    out["exit_reason"] = exit_reason
    out["hold_days"] = int(exit_i - i)
    out["strategy_ret"] = exit_close / entry_close - 1 - total_cost if entry_close > 0 and exit_close > 0 else np.nan
    # 路径风险：信号后最大浮盈/最大回撤
    fwd = d.iloc[i + 1: min(i + max_w, len(d) - 1) + 1]
    if not fwd.empty:
        out["max_fav"] = safe_float(fwd["high"].max()) / entry_close - 1
        out["max_adverse"] = safe_float(fwd["low"].min()) / entry_close - 1
    else:
        out["max_fav"] = np.nan; out["max_adverse"] = np.nan
    return out


def v25_make_trade_record(signal, cache, rank):
    ret = v25_calc_forward_returns(signal, cache)
    rec = {}
    rec.update(ret)
    rec.update({
        "rank": rank,
        "signal_date": signal.get("date", ret.get("entry_date", "")),
        "v20_trade_tier": signal.get("v20_trade_tier", signal.get("v14_level", "")),
        "v20_final_score": safe_float(signal.get("v20_final_score", signal.get("v14_final_score", signal.get("total_score", 0)))),
        "total_score": safe_float(signal.get("total_score", 0)),
        "trade_priority_score": safe_float(signal.get("trade_priority_score", 0)),
        "amount": safe_float(signal.get("amount", 0)),
        "liquidity_tier": signal.get("v241_liquidity_tier", ""),
        "position_pct": safe_float(signal.get("v241_position_pct", 0)),
        "main_hypothesis": signal.get("v20_main_hypothesis", signal.get("candidate_pool", "")),
        "base_entry_reason": str(signal.get("base_entry_reason", ""))[:300],
        "risk_flags": str(signal.get("base_risk_flags", ""))[:300],
        "confirm_condition": build_confirm_condition(signal) if 'build_confirm_condition' in globals() else "",
        "giveup_condition": build_giveup_condition(signal) if 'build_giveup_condition' in globals() else "",
    })
    rec["failure_reason"] = v25_1_classify_failure_reason(rec)
    return rec


def v25_summarize_trades(trades_df):
    rows = []
    if trades_df is None or trades_df.empty:
        return pd.DataFrame()
    for metric in ["strategy_ret"] + [f"net_ret_t{w}" for w in V25_BACKTEST_WINDOWS]:
        if metric not in trades_df.columns:
            continue
        s = pd.to_numeric(trades_df[metric], errors="coerce").dropna()
        if s.empty:
            continue
        rows.append({
            "metric": metric,
            "count": int(len(s)),
            "win_rate": float((s > 0).mean()),
            "avg_ret": float(s.mean()),
            "median_ret": float(s.median()),
            "payoff_ratio": float(s[s > 0].mean() / abs(s[s < 0].mean())) if (s > 0).any() and (s < 0).any() else np.nan,
            "best": float(s.max()),
            "worst": float(s.min()),
            "expectancy": float(s.mean()),
        })
    return pd.DataFrame(rows)


def v25_daily_portfolio(trades_df):
    if trades_df is None or trades_df.empty:
        return pd.DataFrame()
    d = trades_df.copy()
    d["signal_date"] = d["signal_date"].astype(str)
    # 动态仓位字段可能为0，回测组合默认TopN等权，再额外保留动态权重参考。
    rows = []
    for date, g in d.groupby("signal_date"):
        g = g.sort_values("rank").head(V25_BACKTEST_TOP_N)
        equal_ret = pd.to_numeric(g["strategy_ret"], errors="coerce").mean()
        # 动态权重：归一化 v241_position_pct；缺失则退回等权。
        w = pd.to_numeric(g.get("position_pct", pd.Series([0]*len(g))), errors="coerce").fillna(0)
        r = pd.to_numeric(g["strategy_ret"], errors="coerce").fillna(0)
        if w.sum() > 0:
            dyn_ret = float((r * (w / w.sum())).sum())
        else:
            dyn_ret = float(equal_ret) if pd.notna(equal_ret) else np.nan
        rows.append({"date": date, "signals": int(len(g)), "equal_weight_ret": float(equal_ret) if pd.notna(equal_ret) else np.nan, "dynamic_weight_ret": dyn_ret})
    out = pd.DataFrame(rows).sort_values("date")
    for col in ["equal_weight_ret", "dynamic_weight_ret"]:
        out[f"{col}_equity"] = (1 + out[col].fillna(0)).cumprod()
        peak = out[f"{col}_equity"].cummax()
        out[f"{col}_drawdown"] = out[f"{col}_equity"] / peak - 1
    return out


def v25_classify_market_regime_from_portfolio(daily_df):
    # 轻量版：无指数数据时，用组合信号表现区分风险环境，仅用于报告诊断，不反向参与信号生成。
    if daily_df is None or daily_df.empty:
        return "unknown"
    eq = daily_df.get("equal_weight_ret_equity")
    if eq is None or len(eq) < 20:
        return "insufficient"
    last = float(eq.iloc[-1] / max(eq.iloc[max(0, len(eq)-60)], 1e-9) - 1) if len(eq) > 60 else float(eq.iloc[-1]-1)
    dd = float(daily_df.get("equal_weight_ret_drawdown", pd.Series([0])).min())
    if dd < -0.25:
        return "bear/panic_like"
    if last > 0.15:
        return "bull_like"
    if abs(last) <= 0.08:
        return "range_like"
    return "neutral_like"


def v25_generate_backtest_report(trades_df, daily_df, failed_df, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    summary = v25_summarize_trades(trades_df)
    if summary is not None and not summary.empty:
        summary.to_csv(os.path.join(out_dir, V25_BACKTEST_SUMMARY_CSV), index=False, encoding="utf-8-sig")
    if trades_df is not None:
        trades_df.to_csv(os.path.join(out_dir, V25_BACKTEST_TRADES_CSV), index=False, encoding="utf-8-sig")
    if daily_df is not None:
        daily_df.to_csv(os.path.join(out_dir, V25_BACKTEST_DAILY_CSV), index=False, encoding="utf-8-sig")
    if failed_df is not None and not failed_df.empty:
        failed_df.to_csv(os.path.join(out_dir, V25_BACKTEST_FAILED_CSV), index=False, encoding="utf-8-sig")

    def pct(x):
        try:
            if pd.isna(x): return ""
            return f"{float(x)*100:.2f}%"
        except Exception:
            return ""
    total_signals = 0 if trades_df is None else len(trades_df)
    days = 0 if daily_df is None else len(daily_df)
    strat = pd.to_numeric(trades_df.get("strategy_ret", pd.Series(dtype=float)), errors="coerce").dropna() if trades_df is not None and not trades_df.empty else pd.Series(dtype=float)
    win = float((strat > 0).mean()) if len(strat) else np.nan
    avg = float(strat.mean()) if len(strat) else np.nan
    med = float(strat.median()) if len(strat) else np.nan
    worst = float(strat.min()) if len(strat) else np.nan
    best = float(strat.max()) if len(strat) else np.nan
    eq_ret = np.nan; max_dd = np.nan; dyn_ret = np.nan; dyn_dd = np.nan
    if daily_df is not None and not daily_df.empty:
        eq_ret = float(daily_df["equal_weight_ret_equity"].iloc[-1] - 1)
        max_dd = float(daily_df["equal_weight_ret_drawdown"].min())
        dyn_ret = float(daily_df["dynamic_weight_ret_equity"].iloc[-1] - 1)
        dyn_dd = float(daily_df["dynamic_weight_ret_drawdown"].min())
    conclusion = "暂不可用"
    if len(strat) >= 50 and avg > 0 and (not pd.isna(max_dd)) and max_dd > -0.25:
        conclusion = "谨慎可用"
    if len(strat) >= 100 and avg > 0.01 and win >= 0.52 and (not pd.isna(max_dd)) and max_dd > -0.18:
        conclusion = "可作为实盘候选池"

    # 分组诊断
    group_sections = []
    for group_col, title in [("v20_trade_tier", "按信号等级"), ("main_hypothesis", "按主导假设/通道"), ("failure_reason", "按失败/盈利归因"), ("exit_reason", "按退出原因")]:
        if trades_df is not None and not trades_df.empty and group_col in trades_df.columns:
            tmp = []
            for k, g in trades_df.groupby(group_col):
                s = pd.to_numeric(g["strategy_ret"], errors="coerce").dropna()
                if len(s) == 0:
                    continue
                tmp.append({"分类": str(k)[:40], "信号数": len(s), "胜率": pct((s>0).mean()), "平均收益": pct(s.mean()), "中位收益": pct(s.median()), "最差": pct(s.min())})
            if tmp:
                group_sections.append((title, pd.DataFrame(tmp).sort_values("信号数", ascending=False).head(20)))

    # 亏损归因样本
    losers = pd.DataFrame()
    if trades_df is not None and not trades_df.empty:
        losers = trades_df.sort_values("strategy_ret").head(V25_1_REPORT_MAX_LOSERS)[[c for c in ["signal_date","code","name","rank","strategy_ret","max_adverse","exit_reason","failure_reason","v20_trade_tier","main_hypothesis","risk_flags","giveup_condition"] if c in trades_df.columns]].copy()

    md = []
    md.append(f"# V25 一号员工真实Walk-Forward回测报告\n")
    md.append(f"生成时间：{bj_time_str()}\n")
    md.append(f"模型版本：{MODEL_VERSION}\n")
    md.append(f"## 1. 总览结论\n")
    md.append(f"**结论：{conclusion}**\n")
    md.append(f"- 数据预热：{V25_BACKTEST_DATA_START} 起\n- 正式统计：{V25_BACKTEST_START} 至 {V25_BACKTEST_END}\n- 交易日样本：{days}\n- 信号笔数：{total_signals}\n- TopN：{V25_BACKTEST_TOP_N}\n- 单边成本：{V25_BACKTEST_COST_SINGLE_SIDE*100:.2f}%\n- 单边滑点：{V25_BACKTEST_SLIPPAGE_SINGLE_SIDE*100:.2f}%\n- 正式成交额门槛：{V25_BACKTEST_MIN_AMOUNT/100000000:.2f}亿\n")
    md.append(f"## 1.1 回测真实性防护\n")
    md.append(f"- 同一套V24/V24.1核心选股函数：基础层、深度层、最终筛选与daily模式共用。\n")
    md.append(f"- No-Lookahead Guard：{V25_1_STRICT_NO_LOOKAHEAD}，每个历史交易日只向模型暴露 as_of_date 当日及以前K线；启动前会检查核心函数完整性与回测配置。\n")
    md.append(f"- 基础层全市场海选，进入深度前限制Top {V25_1_BACKTEST_BASE_LIMIT}，避免逐日深度全市场重算。\n")
    md.append(f"- 未来收益只在信号生成后单独计算，不回流到评分和排序。\n")

    md.append(f"## 2. 核心表现\n")
    md.append("| 指标 | 数值 |\n|---|---:|\n")
    for k,v in [("单笔胜率", pct(win)), ("单笔平均收益", pct(avg)), ("单笔中位收益", pct(med)), ("单笔最好", pct(best)), ("单笔最差", pct(worst)), ("TopN等权组合累计收益", pct(eq_ret)), ("TopN等权最大回撤", pct(max_dd)), ("动态权重组合累计收益", pct(dyn_ret)), ("动态权重最大回撤", pct(dyn_dd))]:
        md.append(f"| {k} | {v} |\n")
    md.append("\n## 3. T+窗口条件概率\n")
    if summary is not None and not summary.empty:
        md.append(summary.to_markdown(index=False))
        md.append("\n")
    else:
        md.append("暂无足够交易样本。\n")
    for title, df in group_sections:
        md.append(f"\n## {title}\n")
        md.append(df.to_markdown(index=False))
        md.append("\n")
    md.append(f"\n## 失败归因样本：亏损最大的{V25_1_REPORT_MAX_LOSERS}笔\n")
    if losers is not None and not losers.empty:
        losers2 = losers.copy()
        for c in ["strategy_ret", "max_adverse"]:
            if c in losers2.columns:
                losers2[c] = losers2[c].apply(pct)
        md.append(losers2.to_markdown(index=False))
        md.append("\n")
    else:
        md.append("暂无。\n")
    md.append("\n## 5. 实操建议\n")
    if conclusion == "暂不可用":
        md.append("- 当前参数在本次样本下未证明稳定正期望，正式实盘前应继续收紧流动性、等级和市场环境过滤。\n")
    else:
        md.append("- 保留S/A级为正式候选，B/C只观察；优先执行回撤小、成交额充足、RR合理的票。\n")
    if not pd.isna(max_dd) and max_dd < -0.20:
        md.append("- 组合最大回撤偏大，建议熊市/弱市减少TopN或降低仓位。\n")
    if not pd.isna(avg) and avg <= 0:
        md.append("- 单笔平均收益扣费后不佳，应优先检查失败样本中的假突破、放量滞涨、流动性不足。\n")
    md.append("- 短期收益最大化不等于追高：优先选择T+3/T+8条件概率最优的通道，并用硬止损控制尾部亏损。\n")
    md_text = "".join(md)
    md_path = os.path.join(out_dir, V25_BACKTEST_REPORT_MD)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_text)
    html_text = "<html><head><meta charset='utf-8'><title>V25回测报告</title><style>body{font-family:Arial,'Microsoft YaHei',sans-serif;max-width:1180px;margin:24px auto;line-height:1.6} table{border-collapse:collapse;width:100%;margin:12px 0} td,th{border:1px solid #ddd;padding:6px 8px} th{background:#f4f4f4} code{background:#f6f6f6;padding:2px 4px}</style></head><body>" + md_text.replace("\n", "<br>\n") + "</body></html>"
    html_path = os.path.join(out_dir, V25_BACKTEST_REPORT_HTML)
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html_text)
    return {"md": md_path, "html": html_path, "summary": os.path.join(out_dir, V25_BACKTEST_SUMMARY_CSV), "trades": os.path.join(out_dir, V25_BACKTEST_TRADES_CSV), "daily": os.path.join(out_dir, V25_BACKTEST_DAILY_CSV)}


def run_v25_walk_forward_backtest():
    """V25主回测入口：按历史交易日逐日生成当日TopN，随后评估T+窗口收益。"""
    global ENABLE_TELEGRAM, USE_FULL_HISTORY_CACHE, V24_1_MARKET_REGIME
    if V25_BACKTEST_DISABLE_TELEGRAM == "1":
        ENABLE_TELEGRAM = "0"
    USE_FULL_HISTORY_CACHE = "1"
    os.makedirs(V25_BACKTEST_OUTPUT_DIR, exist_ok=True)
    v25_2_preflight_check(mode="backtest", raise_on_error=True)
    print(f"V25.3回测启动：data_start={V25_BACKTEST_DATA_START} start={V25_BACKTEST_START} end={V25_BACKTEST_END} topN={V25_BACKTEST_TOP_N} deep_limit={V25_BACKTEST_DEEP_LIMIT} base_limit={V25_1_BACKTEST_BASE_LIMIT} seed={V25_2_RANDOM_SEED}", flush=True)
    stock_list = v25_build_backtest_universe()
    if stock_list is None or stock_list.empty:
        raise RuntimeError("V25回测失败：股票池为空，请检查FULL_HISTORY_CACHE_DIR或模型验收股票池。")
    cache, load_failed = v25_prepare_cache(stock_list)
    stock_list = stock_list[stock_list["bs_code"].astype(str).isin(set(cache.keys()))].copy()
    dates = v25_get_trading_dates(cache)
    if not dates:
        raise RuntimeError("V25回测失败：没有可用交易日。")
    print(f"V25回测股票池={len(stock_list)} 可用缓存={len(cache)} 交易日={len(dates)} 加载失败={len(load_failed)}", flush=True)
    all_trades = []
    all_failed = list(load_failed)
    daily_meta = []
    t0 = time.time()
    for idx, date in enumerate(dates, 1):
        if V25_BACKTEST_PROGRESS_EVERY > 0 and (idx == 1 or idx % V25_BACKTEST_PROGRESS_EVERY == 0):
            elapsed = time.time() - t0
            avg = elapsed / max(idx - 1, 1)
            remain = avg * max(len(dates) - idx + 1, 0)
            print(f"V25回测进度：{idx}/{len(dates)} date={date} 已耗时={fmt_seconds(elapsed)} 预计剩余={fmt_seconds(remain)}", flush=True)
        try:
            base_rows, deep_rows, final_signals, failed, bucket_stats = v25_run_one_day(date, stock_list, cache)
            all_failed.extend(failed)
            day_trades = []
            for rnk, sig in enumerate(final_signals, 1):
                rec = v25_make_trade_record(sig, cache, rnk)
                if rec:
                    all_trades.append(rec); day_trades.append(rec)
            daily_meta.append({"date": date, "base_count": len(base_rows), "deep_count": len(deep_rows), "signals": len(final_signals), "trade_records": len(day_trades)})
        except Exception as e:
            all_failed.append({"date": date, "stage": "day", "error": str(e)[:500]})
            print(f"V25回测单日失败：{date} {str(e)[:160]}", flush=True)
    trades_df = pd.DataFrame(all_trades)
    daily_df = v25_daily_portfolio(trades_df)
    # 合并日元数据，便于排查某日为何无票。
    meta_df = pd.DataFrame(daily_meta)
    if daily_df is not None and not daily_df.empty and not meta_df.empty:
        daily_df = daily_df.merge(meta_df, on="date", how="outer")
    elif not meta_df.empty:
        daily_df = meta_df
    failed_df = pd.DataFrame(all_failed)
    paths = v25_generate_backtest_report(trades_df, daily_df, failed_df, V25_BACKTEST_OUTPUT_DIR)
    print("V25回测完成，输出文件：")
    for k, v in paths.items():
        print(f"  {k}: {v}")
    return paths


def main():
    _args = v25_1_parse_cli_args()
    _mode = v25_1_apply_runtime_args(_args)
    if getattr(_args, "self_check", False):
        v25_2_preflight_check(mode=_mode, raise_on_error=True)
        return
    if V25_ENABLE_BACKTEST == "1":
        run_v25_walk_forward_backtest()
        return
    v25_2_preflight_check(mode="daily", raise_on_error=True)
    start_ts = time.time()

    print(f"ENABLE_TELEGRAM={ENABLE_TELEGRAM}")
    print(f"RESULT_LIMIT_RAW={RESULT_LIMIT_RAW}")
    print(f"TOP_PUSH_LIMIT={TOP_PUSH_LIMIT}")
    print(f"RESULT_LIMIT_EFFECTIVE={RESULT_LIMIT}")
    print(f"FINAL_SCORE_THRESHOLD={FINAL_SCORE_THRESHOLD}")
    print(f"V19_FIXED_TOP_N={V19_FIXED_TOP_N}")
    print(f"V19_ENABLE_TOP3_FIXED={V19_ENABLE_TOP3_FIXED}")
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
    print(f"V24.1流动性门槛：绝对底线={V24_1_ABSOLUTE_MIN_AMOUNT/100000000:.2f}亿，正式={V24_1_MIN_AMOUNT_FOR_FORMAL/100000000:.2f}亿，严格={V24_1_STRICT_AMOUNT_FOR_FORMAL/100000000:.2f}亿，regime={V24_1_MARKET_REGIME}")
    v241_write_backtest_config()
    print(f"KLINE_FALLBACK_AKSHARE={KLINE_FALLBACK_AKSHARE}")
    print(f"ALLOW_STALE_KLINE_CACHE={ALLOW_STALE_KLINE_CACHE}")
    print(f"STALE_CACHE_MAX_DAYS={STALE_CACHE_MAX_DAYS}")
    print(f"AKSHARE_FALLBACK_MAX_RETRIES={AKSHARE_FALLBACK_MAX_RETRIES}")
    print(f"BASE_KLINE_LOOKBACK_DAYS={BASE_KLINE_LOOKBACK_DAYS}")
    print(f"DEEP_KLINE_LOOKBACK_DAYS={DEEP_KLINE_LOOKBACK_DAYS}")
    print(f"MONTHLY_STRUCT_LOOKBACK_MONTHS={MONTHLY_STRUCT_LOOKBACK_MONTHS}")
    print(f"KLINE_LOOKBACK_DAYS={KLINE_LOOKBACK_DAYS}")
    print(f"STOCK_LIST_QUERY_TIMEOUT_SECONDS={STOCK_LIST_QUERY_TIMEOUT_SECONDS}")
    print(f"STOCK_LIST_MAX_RETRIES={STOCK_LIST_MAX_RETRIES}")
    print(f"STOCK_LIST_FALLBACK_AKSHARE={STOCK_LIST_FALLBACK_AKSHARE}")
    print(f"北京时间：{bj_time_str()}")

    # V19.4.1 HOTFIX：只读缓存模式下，不让 BaoStock 登录失败阻断主流程。
    # 说明：USE_FULL_HISTORY_CACHE=1 时，模型优先读取 kline_cache 全历史缓存；
    # BaoStock 只作为旧联网/兜底通道，不应在启动阶段一票否决。
    # 如果关闭只读缓存、需要联网取数，则仍然要求 BaoStock 登录成功。
    need_baostock_login = USE_FULL_HISTORY_CACHE != "1"

    if need_baostock_login:
        if not baostock_login():
            msg = "BaoStock登录失败，无法获取数据。"
            print(msg)
            send_midrun_telegram(build_error_message(msg), reason="baostock_login_failed")
            return
    else:
        try:
            ok = baostock_login()
            print(f"只读缓存模式：BaoStock登录尝试结果={ok}，不阻断主流程")
        except Exception as e:
            print(f"只读缓存模式：BaoStock登录异常但不阻断主流程：{e}")

    try:
        history = load_signal_history()

        print("抓取A股列表...")
        stock_list = get_a_stock_list()

        if stock_list.empty:
            msg = build_message([], [], 0, 0, 0, 0)
            send_midrun_telegram(msg, reason="early_empty_result")
            return

        print(f"共抓取 {len(stock_list)} 只股票")
        print("V25.3一号员工选股模型启动：当前为生产融合版，保留历史有效底座并由V25.3统一调度。")

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
        kline_date_audit = print_kline_date_audit(target_date=LAST_TRADE_DAY)
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
            send_midrun_telegram(build_error_message(warning), reason="runtime_warning")
            return

        if not base_rows:
            msg = build_message([], dates, len(stock_list), kline_success, kline_fail, 0)
            send_midrun_telegram(msg, reason="early_empty_result")
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
        print(f"V19.3基础海选机会分桶/闸门后进入深度评分：{len(deep_targets)}条")
        print("V19.3基础海选机会分桶统计：")
        for _bucket, _st in base_bucket_stats.items():
            print(f"  {_bucket}: 可用{_st.get('available', 0)} | 配额{_st.get('quota', 0)} | 入选{_st.get('selected', 0)}")

        deep_rows = []
        deep_success = 0
        deep_fail = 0
        deep_skip = 0
        deep_start_ts = time.time()

        deep_failed_records = []

        for idx, r in enumerate(deep_targets, 1):
            # V16.6：深度评分阶段必须高频打印，否则GitHub日志看起来像卡死。
            if idx == 1 or idx % 1 == 0:
                elapsed_deep = time.time() - deep_start_ts
                avg_deep = elapsed_deep / max(idx - 1, 1)
                remain_deep = avg_deep * max(len(deep_targets) - idx + 1, 0)
                print(
                    f"深度评分进度：{idx}/{len(deep_targets)} "
                    f"({idx / max(len(deep_targets), 1) * 100:.1f}%) | "
                    f"当前={r.get('code', '')} {r.get('name', '')} | "
                    f"成功={deep_success} 失败={deep_fail} 跳过={deep_skip} | "
                    f"已耗时={fmt_seconds(elapsed_deep)} | 预计剩余={fmt_seconds(remain_deep)}",
                    flush=True
                )

            if time.time() - deep_start_ts > DEEP_RUNTIME_SECONDS:
                print("达到深度评分阶段最大运行时间，停止深度评分，使用已有深度评分结果。")
                break

            if time.time() - start_ts > MAX_RUNTIME_SECONDS:
                print("达到总最大运行时间，停止深度评分，使用已有深度评分结果。")
                break

            try:
                # V16.6：深度评分单票总超时保护。即使某个模块/某只票卡住，也不能拖死全局。
                with stock_query_timeout(DEEP_SINGLE_STOCK_TIMEOUT_SECONDS, f"deep:{r.get('code', '')}"):
                    rows = process_stock_deep(r)

                if rows:
                    deep_success += 1
                else:
                    deep_skip += 1

                for rr in rows:
                    deep_rows.append(rr)

            except Exception as e:
                deep_fail += 1
                err_msg = str(e)[:500]
                deep_failed_records.append({
                    "code": r.get("code", ""),
                    "name": r.get("name", ""),
                    "error": err_msg,
                })
                print(f"深度处理失败/超时: {r.get('code', '')} {r.get('name', '')} {err_msg}", flush=True)

        if deep_failed_records:
            try:
                os.makedirs("outputs", exist_ok=True)
                pd.DataFrame(deep_failed_records).to_csv("outputs/deep_failed_symbols.csv", index=False, encoding="utf-8-sig")
                print(f"深度失败清单已保存: outputs/deep_failed_symbols.csv rows={len(deep_failed_records)}")
            except Exception as _e:
                print(f"深度失败清单保存失败: {_e}")

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
            send_midrun_telegram(build_error_message(warning), reason="runtime_warning")
            return

        # V14最终三选：原主模型完整跑完后，只做后置审核/分层扣分/相对最优三选；不重写、不删主模型逻辑。
        final_signals, v14_diagnostics, v14_audited_rows = select_final_signals_v20(deep_rows, history, limit=V20_FIXED_TOP_N)
        strong_watch_pool = [r for r in v14_audited_rows if (not r.get("v14_blocked")) and str(r.get("code")) not in {str(x.get("code")) for x in final_signals}][:80]
        lifecycle_tracking = build_v20_signal_lifecycle(history, v14_audited_rows, final_signals, current_dates=dates)

        for r in final_signals:
            key = f"{r.get('date','')}_{r.get('code','')}"
            history[key] = {
                "date": r.get("date", ""),
                "code": r.get("code", ""),
                "name": r.get("name", ""),
                "score": r.get("score", 0),
                "base_score": r.get("base_score", 0),
                "total_score": r.get("total_score", 0),
                "v14_final_score": r.get("v14_final_score", r.get("total_score", 0)),
                "v20_final_score": r.get("v20_final_score", r.get("v14_final_score", r.get("total_score", 0))),
                "v20_trade_tier": r.get("v20_trade_tier", ""),
                "v20_main_hypothesis": r.get("v20_main_hypothesis", ""),
                "entry_close": safe_float(r.get("close", 0)),
                "v20_defense": safe_float(r.get("v20_defense", r.get("defensive_price", r.get("trade_defense", 0)))),
                "hard_stop": safe_float(build_v19_price_plan(r).get("hard_stop_price", 0)),
                "confirm_price": safe_float(r.get("v20_confirm_price", r.get("xhu_effective_confirm_price", build_v19_price_plan(r).get("break_confirm_price", 0)))),
                "target1": safe_float(build_v19_price_plan(r).get("target1_price", 0)),
                "confirm_condition": build_confirm_condition(r),
                "giveup_condition": build_giveup_condition(r),
                "v14_level": r.get("v14_level", ""),
                "candidate_pool": r.get("candidate_pool", ""),
            }

        print(f"近{CHECK_DAYS}个交易日排查完成：{dates}（默认仅最新有行情日；可用CHECK_DAYS调整）")
        print(f"K线成功：{kline_success} 只 | K线失败：{kline_fail} 只")
        print(f"基础评分数量：{len(base_rows)} 条")
        print(f"深度评分数量：{len(deep_rows)} 条 | 输入：{len(deep_targets)} | 成功：{deep_success} | 失败：{deep_fail} | 跳过：{deep_skip} | 有效样本：{len(deep_rows)}")
        print(f"V26最终买入池数量：{len(final_signals)} 只 | 诊断候选：{len(v14_diagnostics)} 只")
        print(f"V20.1后备观察池数量：{len(strong_watch_pool)} 只（默认不推送，只保存候选JSON/条件概率跟踪底座）")

        save_candidates_payload(base_rows, deep_rows, final_signals, strong_watch_pool)
        save_v20_outputs(
            final_signals,
            v14_diagnostics,
            v14_audited_rows,
            dates=dates,
            meta={
                "stock_count": len(stock_list),
                "kline_success": kline_success,
                "kline_fail": kline_fail,
                "base_count": len(base_rows),
                "deep_count": len(deep_rows),
                "deep_targets": len(deep_targets),
                "deep_success": deep_success,
                "deep_fail": deep_fail,
                "deep_skip": deep_skip,
            },
            history=history,
            lifecycle_tracking=lifecycle_tracking,
        )
        save_signal_history(history)

        msg = build_message(
            final_signals,
            dates,
            stock_count=len(stock_list),
            kline_success=kline_success,
            kline_fail=kline_fail,
            deep_count=len(deep_rows),
            v14_diagnostics=v14_diagnostics,
            lifecycle_tracking=lifecycle_tracking
        )

        send_telegram(msg)

        print("全部完成!")

    except Exception as e:
        print(f"主流程失败：{e}")
        send_midrun_telegram(build_error_message(e), reason="main_exception")

    finally:
        baostock_logout()


if __name__ == "__main__":
    main()
