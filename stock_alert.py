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
import numpy as np
import requests

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
# 3）各周期独立生成压力密集区后，投影到统一百分比价格桶，寻找多周期重叠最密集核心压力带；
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

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
ENABLE_TELEGRAM = os.environ.get("ENABLE_TELEGRAM", "0")

SIGNAL_FILE = "signals_history.json"
CANDIDATE_FILE = "stock_candidates.json"
CACHE_DIR = "kline_cache"
MODEL_VERSION = "V16.4一号员工选股模型｜数据闸门+只读全历史缓存+验收股票池版"
SEED_POOL_FILE = os.environ.get("SEED_POOL_FILE", "stock_seed_pool.json")


N = 20
CHECK_DAYS = int(os.environ.get("CHECK_DAYS", "1"))  # V11.1：默认只扫描最新有行情交易日；如需回看可在workflow设置为3

MAX_STOCKS = int(os.environ.get("MAX_STOCKS", "0"))
RESULT_LIMIT_RAW = int(os.environ.get("RESULT_LIMIT", "20"))
# V12：一号员工正式报告默认只推前5只；后台候选池/跟踪池仍保留更多记录。
TOP_PUSH_LIMIT = int(os.environ.get("TOP_PUSH_LIMIT", "3"))
RESULT_LIMIT = min(RESULT_LIMIT_RAW, TOP_PUSH_LIMIT) if TOP_PUSH_LIMIT > 0 else RESULT_LIMIT_RAW
DEEP_SCORE_LIMIT_RAW = int(os.environ.get("DEEP_SCORE_LIMIT", "500"))
# V10：深度评分硬上限。即使 workflow 仍传 500，也默认只取基础分桶后的前150条深评，
# 避免 GitHub Actions 深度评分跑不完；如确需恢复500，可设置 DEEP_SCORE_HARD_CAP=0 或 500。
DEEP_SCORE_HARD_CAP = int(os.environ.get("DEEP_SCORE_HARD_CAP", "80"))
DEEP_SCORE_LIMIT = min(DEEP_SCORE_LIMIT_RAW, DEEP_SCORE_HARD_CAP) if DEEP_SCORE_HARD_CAP > 0 else DEEP_SCORE_LIMIT_RAW

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
KLINE_FALLBACK_AKSHARE = os.environ.get("KLINE_FALLBACK_AKSHARE", "1")
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
STOCK_LIST_FALLBACK_AKSHARE = os.environ.get("STOCK_LIST_FALLBACK_AKSHARE", "1")
STOCK_LIST_RELOGIN_ON_FAIL = os.environ.get("STOCK_LIST_RELOGIN_ON_FAIL", "1")

SCORE_LIMIT = 75
# 最终推送阈值：新评分体系下，80分以下不再推送；基础初筛仍沿用原SCORE_LIMIT，不改原模型。
FINAL_SCORE_THRESHOLD = float(os.environ.get("FINAL_SCORE_THRESHOLD", "80"))
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
V14_MIN_ABSOLUTE_SCORE = float(os.environ.get("V14_MIN_ABSOLUTE_SCORE", "80"))
V14_PREFERRED_SCORE = float(os.environ.get("V14_PREFERRED_SCORE", "80"))
V14_IGNORE_HISTORY_FOR_RERUN = os.environ.get("V14_IGNORE_HISTORY_FOR_RERUN", "1")
V14_BLOCK_SEVERE_NO_DEFENSE = os.environ.get("V14_BLOCK_SEVERE_NO_DEFENSE", "0")

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


def _xhu_structural_anchors(pdf, period="D"):
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
            anchors = _xhu_structural_anchors(d, period)
            anchor_hits = []
            tol = max(bucket_pct * 2.2, 0.012)
            adj_lower, adj_upper = lower, upper
            anchor_score = 0.0
            for a in anchors:
                price = safe_float(a.get("price", 0)); wt = safe_float(a.get("weight", 1.0))
                if lower * (1 - tol) <= price <= upper * (1 + tol):
                    anchor_hits.append(a.get("type", "锚点"))
                    anchor_score += wt * 4.0
                    # 锚点可校准边界，但不能把区间无限拉宽。
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
    """核心：投影各周期压力区到统一百分比桶，找多周期重叠最密集核心压力带，同时计算并集最高上沿。"""
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
        if z["upper"] >= core_lower * 0.985 and z["lower"] <= core_upper * 1.04:
            related_ids.add(zi)
    related = [zones[i] for i in sorted(related_ids)] if related_ids else zones
    union_lower = min(z["lower"] for z in related)
    union_upper = max(z["upper"] for z in related)
    period_count = len(sorted(set(z["period"] for z in related)))
    raw_quality = (
        max_score * 11.0 + period_count * 8.0 + sum(safe_float(z.get("quality_score", 0)) for z in related) / max(1, len(related)) * 0.35
    )
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
    return {
        "valid": True,
        "core_lower": float(core_lower), "core_upper": float(core_upper),
        "union_lower": float(union_lower), "union_upper": float(union_upper), "final_union_upper": float(union_upper),
        "core_periods": core_periods, "dominant_period": dominant,
        "overlap_score": float(max_score), "pressure_quality_score": float(pressure_quality),
        "pressure_zone_grade": grade, "period_count": int(period_count), "desc": desc,
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
        score += 6; reasons.append("实体站上核心压力")
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


def detect_xuanhu_pressure_band_breakout_model(df, code=""):
    """V15选股模型压力带突破主模型：底层压力区生成 -> 多周期重叠合并 -> 日K突破评级 -> 模型评级。"""
    empty = {
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
        "xhu_pressure_desc": "无有效多周期压力带",
        "xhu_breakout_desc": "无有效突破",
        "xhu_fake_breakout_count": 0,
        "xhu_fake_breakout_high": 0.0,
        "xhu_pressure_json": "[]",
    }
    if ENABLE_XHU_PRESSURE_BREAKOUT != "1" or df is None or len(df) < 180:
        return empty
    cur_close = safe_float(df.iloc[-1].get("close", 0))
    if cur_close <= 0:
        return empty
    zones = []
    for period, pdf, lookback in _xhu_period_dfs(df):
        try:
            zones.extend(_xhu_extract_period_pressure_zones(pdf, period=period, lookback=lookback, current_close=cur_close))
        except Exception as e:
            print(f"V15压力带单周期生成失败：period={period} code={code} error={str(e)[:80]}")
    if not zones:
        return empty
    composite = _xhu_merge_multi_period_zones(zones, cur_close)
    if not composite.get("valid"):
        return empty
    day = _xhu_grade_breakout_day(df, composite)
    setup_grade, setup_score = _xhu_combine_pressure_setup_grade(
        composite.get("pressure_zone_grade", "D"), day.get("breakout_day_grade", "D"),
        composite.get("pressure_quality_score", 0), day.get("breakout_score", 0)
    )
    # 空间与过热修正：完整穿透后若上方仍有年线/远端压力贴脸，不能过度抬分；这里先轻度修正，V14继续降级。
    close = cur_close
    final_upper = safe_float(composite.get("final_union_upper", 0))
    dist_after = close / final_upper - 1 if final_upper > 0 else 0.0
    if setup_grade == "S" and dist_after < 0.003:
        setup_score -= 1.0
    # 输出给一号员工：A/S可作为正式候选资格之一；B/C/D只观察。
    score = max(0.0, min(18.0, setup_score))
    related = composite.get("related_zones", [])
    return {
        "score_xhu_pressure_breakout": float(score),
        "xhu_pressure_model_grade": setup_grade,
        "xhu_pressure_zone_grade": composite.get("pressure_zone_grade", "D"),
        "xhu_breakout_day_grade": day.get("breakout_day_grade", "D"),
        "xhu_pressure_core_lower": float(composite.get("core_lower", 0.0)),
        "xhu_pressure_core_upper": float(composite.get("core_upper", 0.0)),
        "xhu_pressure_union_lower": float(composite.get("union_lower", 0.0)),
        "xhu_pressure_union_upper": float(composite.get("union_upper", 0.0)),
        "xhu_final_union_upper": float(composite.get("final_union_upper", 0.0)),
        "xhu_pressure_quality_score": float(composite.get("pressure_quality_score", 0.0)),
        "xhu_pressure_overlap_score": float(composite.get("overlap_score", 0.0)),
        "xhu_pressure_periods": ",".join(sorted(set(z.get("period", "") for z in related))),
        "xhu_pressure_desc": composite.get("desc", ""),
        "xhu_breakout_desc": day.get("desc", ""),
        "xhu_fake_breakout_count": int(day.get("fake_breakout_count", 0)),
        "xhu_fake_breakout_high": float(day.get("fake_breakout_high", 0.0)),
        "xhu_pressure_json": json.dumps(related, ensure_ascii=False)[:1800],
    }

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
    # 先用百分比/对数价格桶生成日/周/月/季/年供需密集区，再找多周期重叠核心压力带和最终压力上沿；
    # 只有A/S级压力带突破才作为选股模型正式候选资格之一，B/C/D只进入观察。
    xhu_pressure_ctx = detect_xuanhu_pressure_band_breakout_model(df, code)
    for _k, _v in xhu_pressure_ctx.items():
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
    # S级完整穿透可直接作为正式候选资格；A级核心压力突破/消化突破也可入候选，但仍受V14/雷区/RR二次审核。
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
    extra.loc[extra["xhu_pressure_model_grade"].isin(["B", "C", "D"]), "total_score"] = extra.loc[extra["xhu_pressure_model_grade"].isin(["B", "C", "D"]), "total_score"].clip(upper=84.0)

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
    bucket = str(row.get("base_bucket", ""))

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
    if bucket == "强势观察" and trade_quality < 0 and seed_score < 16:
        return False, "强势观察但交易质量不足"

    return True, "通过V12.5基础闸门"


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
    print(f"V12.5基础闸门：通过{len(dedup)}只，提前排除{len(gated_out)}只")
    append_seed_pool_snapshot(dedup)

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
    bucket_stats["V12.5基础闸门"] = {"available": len(dedup), "quota": limit, "selected": len(selected)}
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
    V14最终三选：保留原主模型排序逻辑的基础上，进行后置审核与相对最优三选。
    只硬剔除财务/审计/监管硬雷区；普通缺点扣分，尽量每天选出科学合理的3只。
    """
    if history is None:
        history = {}
    limit = int(limit or V14_TARGET_PUSH_COUNT or RESULT_LIMIT or 3)
    audited = [v14_candidate_audit(r) for r in deep_rows]
    blocked = [r for r in audited if r.get("v14_blocked")]
    eligible = [r for r in audited if not r.get("v14_blocked")]
    eligible = sorted(
        eligible,
        key=lambda x: (
            _grade_rank_v151(x.get("v151_strongest_model_grade", "")),
            int(bool(x.get("v151_formal_model_ok", False))),
            safe_float(x.get("v14_final_score", x.get("total_score", 0))),
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
            rr = dict(r); rr["v14_skip_reason"] = "signals_history已推送过"; diagnostics.append(rr); continue
        if safe_float(r.get("v14_final_score", 0)) < V14_MIN_ABSOLUTE_SCORE:
            rr = dict(r); rr["v14_skip_reason"] = f"V14最终分低于{V14_MIN_ABSOLUTE_SCORE}"; diagnostics.append(rr); continue
        final.append(r)
        if len(final) >= limit:
            break
    # 如果70分以上不足3只，仍保留诊断，不强行把硬雷或极低分票推入正式三选。
    selected_codes = {str(r.get("code")) for r in final}
    for r in eligible:
        if str(r.get("code")) not in selected_codes:
            rr = dict(r)
            if not rr.get("v14_skip_reason"):
                rr["v14_skip_reason"] = "未进入前三，相对分数靠后"
            diagnostics.append(rr)
    for r in blocked[:20]:
        rr = dict(r); rr["v14_skip_reason"] = rr.get("v14_block_reason", "硬雷区剔除"); diagnostics.append(rr)
    return final, diagnostics[:20], audited


def v14_diagnostics_text(rows, n=8):
    if not rows:
        return ""
    lines = ["V14拦截/落选诊断前{}只：".format(min(n, len(rows)))]
    for i, r in enumerate(rows[:n], 1):
        lines.append(
            f"{i}. {r.get('name','')}({r.get('code','')}) 分{safe_float(r.get('v14_final_score', r.get('total_score', 0))):.1f}："
            f"{r.get('v14_skip_reason', r.get('v14_block_reason', '未入选'))}；追高={r.get('v14_chase_reasons','')}；操作={r.get('v14_operability_reasons','')}"
        )
    return "\n".join(lines)


def build_message(final_signals, dates, stock_count=0, kline_success=0, kline_fail=0, deep_count=0, v14_diagnostics=None):
    lines = []
    lines.append("📊 <b>一号员工V16机构级20维机会评分三选报告</b>")
    lines.append(f"🗓 排查日期：{', '.join(dates) if dates else '未知'}")
    lines.append(f"⏱ 运行时间：{bj_time_str()} 北京时间")
    lines.extend(build_data_gate_header_lines())
    lines.append(f"股票池：{stock_count}只 | K线成功：{kline_success}只 | 失败：{kline_fail}只")
    lines.append(f"深度评分：{deep_count}只 | 分析输出：<b>{len(final_signals)}</b>只")
    lines.append(f"V16三选口径：财务/审计/监管硬雷区一票否决；原V12.6/V14深度分保留，但必须融合主模型等级。压力带、回踩确认、黄金倍量、凹口、多周期关键位、月线修复等统一评级，尽量选出相对最优{V14_TARGET_PUSH_COUNT}只。")
    lines.append("保留底座：倍量/倍量后平量/分散健康倍量/凹口/平台/破底翻/BBI-BOLL中轨修复/近区精准线/缺口/阳包阴/双阳夹阴/台阶/多周期最大阳量K/不追高/RR等均不删除。")
    lines.append("V14新增：阳包阴按跳空越过前阴开盘、实体内高开收复、低/平开完全反包、仅修复中位四档，并纳入上下影线、收盘位置、量能质量。")
    lines.append("说明：一号员工只做结构分析，不提供复制代码；最终可操作代码由三号员工输出。")
    lines.append("━━━━━━━━━━━━━━")

    if not final_signals:
        lines.append("")
        lines.append("⚠️ 今日暂无正式三选股票。V14按设计会尽量三选；若仍为0，通常代表硬雷区/样本/覆盖率或代码覆盖异常，需要看下方诊断。")
        diag = v14_diagnostics_text(v14_diagnostics or [], 10)
        if diag:
            lines.append(html.escape(diag))
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
            f"V14最终分：{s.get('v14_final_score', s.get('total_score', 0)):.2f} | 原主模型分：{s.get('v14_original_total_score', s.get('total_score', 0)):.2f} | 等级：{html.escape(str(s.get('v14_level', '')))} | "
            f"阶段：{html.escape(str(s.get('trade_stage', '未知')))} | 池：{html.escape(str(s.get('candidate_pool', '优先候选池')))} | "
            f"买点：{s.get('score_v12_pullback_entry', 0):.1f} | 台阶：{s.get('score_v125_step_platform_lift', 0):.1f} | 活跃度：{s.get('score_v12_activity', 0):.1f} | "
            f"日线结构：{s.get('score_structure_core', 0):.1f} | 月线：{s.get('score_monthly_cycle', 0):.1f} | 量价：{s.get('score_volume_structure', 0):.1f} | 雷区：{s.get('score_regulatory_risk', 0):.1f}"
        )
        lines.append("V16主模型融合：" + html.escape(str(s.get("v151_model_summary", ""))))
        lines.append("V14/V15打分表：" + html.escape(v14_score_table_text(s)))
        if safe_float(s.get('v14_bull_engulf_score_current', 0)) > 0:
            lines.append(
                "阳包阴细项："
                + html.escape(str(s.get('v14_bull_engulf_desc', '')))
                + f"；总{s.get('v14_bull_engulf_score_current',0):.1f}/18，形态{s.get('v14_bull_engulf_pattern_score',0):.1f}，影线{s.get('v14_bull_engulf_shadow_score',0):.1f}，量能{s.get('v14_bull_engulf_volume_score',0):.1f}，上影占比{s.get('v14_today_upper_shadow_ratio',0):.0%}。"
            )
        lines.append("V14扣分原因：追高=" + html.escape(str(s.get('v14_chase_reasons', ''))) + "；操作=" + html.escape(str(s.get('v14_operability_reasons', ''))) + "；量能=" + html.escape(str(s.get('v14_volume_reasons', ''))))
        lines.append("诊断：" + build_reason_v12(s))

    if v14_diagnostics:
        lines.append("")
        lines.append(html.escape(v14_diagnostics_text(v14_diagnostics, 8)))
    return "\n".join(lines)


def build_error_message(error_text):
    lines = []
    lines.append("⚠️ <b>每日选股脚本运行失败</b>")
    lines.append(f"⏱ 运行时间：{bj_time_str()} 北京时间")
    lines.append("━━━━━━━━━━━━━━")
    lines.append(html.escape(str(error_text))[:3000])
    return "\n".join(lines)


# ========================= V16：一号员工选股模型 20维机构级机会评分 + Telegram真表格 =========================
# 说明：本段为后置增量覆盖层，不删除原V12/V14/V15任何有效逻辑。
# 原有战法均作为信号库，统一映射到20维机会评分：
# 数据/可交易/雷区/供需带/结构/长周期/量能/趋势/修复/时间/突破/回踩/分时/日K/空间/下行/过热/执行/环境/组合。
# 最终按风险调整后的机会分和封顶规则输出 S/A/B+/观察；Telegram正文保持简洁，表格以PNG图片发送。
# ==================================================================================================

MODEL_VERSION = "V16.4一号员工选股模型｜数据闸门+只读全历史缓存+验收股票池版"
TELEGRAM_PENDING_IMAGES = []

try:
    _ORIGINAL_SEND_TELEGRAM = send_telegram
except Exception:
    _ORIGINAL_SEND_TELEGRAM = None


def _v16_clip(x, lo=0.0, hi=100.0):
    try:
        return max(lo, min(hi, float(x)))
    except Exception:
        return lo


def _v16_grade_rank(g):
    g = str(g or "").upper()
    return {"S": 5, "A": 4, "B+": 3, "B": 2, "C": 1, "D": 0}.get(g, 0)


def _v16_score_to_grade(score):
    score = safe_float(score, 0)
    if score >= 90:
        return "S"
    if score >= 82:
        return "A"
    if score >= 75:
        return "B+"
    if score >= 68:
        return "B"
    if score >= 60:
        return "C"
    return "D"


def _v16_text_short(x, n=32):
    s = str(x or "").replace("\n", " ").strip()
    return s if len(s) <= n else s[:n-1] + "…"


def _v16_has_text(r, field, keywords):
    text = str(r.get(field, "") or "")
    return any(k in text for k in keywords)


def _v16_eval_dimensions(r):
    """把原V12/V14/V15全部信号映射到20维机构级机会评分。每维0~100，分数越高越好。"""
    dims = []

    def add(key, name, score, weight, reason):
        dims.append({
            "key": key,
            "name": name,
            "score": round(_v16_clip(score), 1),
            "weight": float(weight),
            "reason": _v16_text_short(reason, 42),
        })

    # 常用字段
    risk_hard = bool(r.get("risk_hard_exclude", False)) or bool(r.get("v14_blocked", False))
    risk_flags = str(r.get("risk_flags", "") or "") + "；" + str(r.get("v14_block_reason", "") or "")
    amount = safe_float(r.get("amount", 0))
    turnover = safe_float(r.get("turnover", 0))
    pct = safe_float(r.get("pct_chg", 0))
    vr1 = safe_float(r.get("vr1", 0))
    volr = safe_float(r.get("volr", 0))
    pos = safe_float(r.get("pos", 0.5))
    entity_pct = safe_float(r.get("entity_pct", 0))
    bias20 = safe_float(r.get("bias20", 0))
    bias60 = safe_float(r.get("bias60", 0))
    rsi = safe_float(r.get("rsi", r.get("base_rsi", 50)))
    cci = safe_float(r.get("cci", r.get("base_cci", 0)))
    rr = safe_float(r.get("risk_reward_ratio", 0))
    defense_dist = safe_float(r.get("defense_dist", 0))
    near_p = safe_float(r.get("near_pressure_dist", 0))
    mid_p = safe_float(r.get("mid_pressure_dist", 0))
    overhead_p = safe_float(r.get("overhead_pressure_dist", 0))
    long_pos = safe_float(r.get("long_pos_250", 0))

    # 1 数据质量：深度样本已经过主流程，这里默认较高；月线/长周期缺失时略降。
    data_score = 82
    if safe_float(r.get("score_monthly_cycle", 0)) <= 0 and safe_float(r.get("score_long_cycle", 0)) <= 0:
        data_score -= 8
    if not str(r.get("date", "")):
        data_score -= 15
    add("data_quality", "数据质量", data_score, 0.02, "样本与近期K线完整性")

    # 2 可交易性：成交额/换手/非一字可买性。无成交额字段时保持中性。
    trad_score = 68
    if amount >= 500000000:
        trad_score += 18
    elif amount >= 150000000:
        trad_score += 12
    elif amount >= 50000000:
        trad_score += 6
    elif amount > 0:
        trad_score -= 8
    if turnover >= 1.0:
        trad_score += 5
    if bool(r.get("limit_up", False)) and str(r.get("limit_volume_mode", "")).find("锁量") >= 0:
        trad_score -= 4
    add("tradability", "可交易性", trad_score, 0.03, "成交额/换手/涨停可买性")

    # 3 雷区硬过滤：不是普通扣分，后续还会封顶。
    hard_score = 0 if risk_hard else 100
    if risk_flags and not risk_hard:
        hard_score = 78
    add("hard_risk", "雷区约束", hard_score, 0.08, "未命中硬雷区" if not risk_hard else _v16_text_short(risk_flags, 42))

    # 4 供需压力/支撑带：V15复合压力带 + 多周期重叠。
    pressure_quality = safe_float(r.get("xhu_pressure_quality_score", 0))
    pressure_score = pressure_quality
    pg = str(r.get("xhu_pressure_model_grade", "") or "").upper()
    if pg == "S": pressure_score = max(pressure_score, 92)
    elif pg == "A": pressure_score = max(pressure_score, 80)
    elif pg == "B": pressure_score = max(pressure_score, 65)
    pressure_score += min(8, safe_float(r.get("score_xhu_pressure_breakout", 0)) * 0.25)
    add("supply_demand", "供需压力带", pressure_score, 0.10, str(r.get("xhu_pressure_desc", "复合压力带/成交密集区")))

    # 5 市场结构：凹口/平台/最大量K/近区结构。
    structure_score = (
        safe_float(r.get("score_structure_core", 0)) * 3.2 +
        safe_float(r.get("score_multi_tf_key_structure", 0)) * 2.6 +
        safe_float(r.get("score_advanced_ao_kou", 0)) * 2.2 +
        safe_float(r.get("score_pattern", 0)) * 1.8 +
        safe_float(r.get("score_fibo_reclaim", 0)) * 2.0
    )
    add("market_structure", "结构优势", structure_score, 0.08, "凹口/平台/最大量K/黄金倍量等结构")

    # 6 长周期修复：月线/季线/年线、BBI/BOLL中轨修复。
    long_score = (
        safe_float(r.get("score_monthly_cycle", 0)) * 4.2 +
        safe_float(r.get("score_long_cycle", 0)) * 1.8 +
        safe_float(r.get("score_monthly_height_space", 0)) * 2.0 +
        safe_float(r.get("score_v124_probe_second_confirm", 0)) * 2.0
    )
    add("long_cycle", "长周期修复", long_score, 0.05, str(r.get("monthly_midline_detail", "月线/季线/年线结构修复")))

    # 7 量能结构。
    volume_score = (
        safe_float(r.get("score_volume_structure", 0)) * 3.4 +
        safe_float(r.get("score_yang_yin_volume", 0)) * 2.6 +
        safe_float(r.get("score_count", 0)) * 1.6 +
        safe_float(r.get("score_v125_step_platform_lift", 0)) * 2.4
    )
    if 1.8 <= vr1 <= 2.5:
        volume_score += 8
    elif vr1 >= 3.5 or volr >= 5:
        volume_score -= 10
    add("volume_structure", "量能参与", volume_score, 0.06, "倍量/倍平/阳量压阴量/平台均量")

    # 8 趋势动量。
    trend_score = 55
    if safe_float(r.get("ma20_slope_5", 0)) >= 0: trend_score += 8
    if safe_float(r.get("score_trend", 0)) > 0: trend_score += safe_float(r.get("score_trend", 0)) * 2.0
    if entity_pct >= 3 and pos >= 0.7: trend_score += 10
    if pct >= 7 and bias20 > 0.18: trend_score -= 8
    add("trend_momentum", "趋势动量", trend_score, 0.05, "趋势斜率/强阳/收盘位置")

    # 9 修复反转。
    reversal_score = (
        safe_float(r.get("score_bottom_reclaim", 0)) * 4.0 +
        safe_float(r.get("score_arc_bottom", 0)) * 3.0 +
        safe_float(r.get("score_v12_pullback_entry", 0)) * 2.0 +
        safe_float(r.get("score_key_pullback_hold", 0)) * 2.0
    )
    add("reversal_repair", "修复反转", reversal_score, 0.04, "破底翻/月线修复/回踩转强")

    # 10 时间窗口。
    timing_score = safe_float(r.get("score_v125_timing_window", 0)) * 4.0 + safe_float(r.get("score_v126_timing_sufficiency", 0)) * 3.5
    add("timing_window", "时间窗口", timing_score, 0.03, str(r.get("v125_timing_desc", "时间窗口/爆发前夜")))

    # 11 突破触发：压力带最终上沿、假突破高点、关键位突破。
    trigger_score = safe_float(r.get("score_xhu_pressure_breakout", 0)) * 5.5 + safe_float(r.get("score_multi_tf_break_quality", 0)) * 3.5
    dg = str(r.get("xhu_breakout_day_grade", "") or "").upper()
    if dg == "S": trigger_score = max(trigger_score, 90)
    elif dg == "A": trigger_score = max(trigger_score, 78)
    elif dg == "B": trigger_score = max(trigger_score, 62)
    if safe_float(r.get("break_rate", 0)) > 0.005 and safe_float(r.get("break_rate", 0)) <= 0.06:
        trigger_score += 8
    add("breakout_trigger", "突破触发", trigger_score, 0.10, str(r.get("xhu_breakout_desc", "关键位/压力带突破")))

    # 12 回踩承接。
    retest_score = (
        safe_float(r.get("score_v12_pullback_entry", 0)) * 4.0 +
        safe_float(r.get("score_limitup_hold_3d", 0)) * 4.0 +
        safe_float(r.get("score_carry_structure", 0)) * 2.5 +
        safe_float(r.get("score_key_pullback_hold", 0)) * 3.0
    )
    add("retest_confirmation", "回踩承接", retest_score, 0.06, "突破后回踩/涨停后三日/关键位承接")

    # 13 分时确认：暂未必有分时数据，默认中性；有字段则使用。
    intraday_score = safe_float(r.get("intraday_breakout_quality_score", 55), 55)
    if intraday_score == 0:
        intraday_score = 55
    add("intraday", "分时确认", intraday_score, 0.02, "未取分时则中性；后续用VWAP/突破时间校验")

    # 14 日K质量。
    candle_score = 50 + min(25, max(0, entity_pct) * 2.8) + min(18, pos * 20)
    if pos >= 0.85: candle_score += 8
    if pos < 0.55 and pct > 0: candle_score -= 10
    if _v16_has_text(r, "xhu_breakout_desc", ["长上影", "假突破", "试探"]): candle_score -= 15
    add("candle_quality", "日K质量", candle_score, 0.05, "实体/收盘位置/上影/缺口")

    # 15 上方空间。
    target_dist = safe_float(r.get("target_dist", 0))
    if target_dist <= 0:
        target_dist = near_p if near_p > 0 else (mid_p if mid_p > 0 else overhead_p)
    upside_score = 50
    if target_dist >= 0.25: upside_score += 30
    elif target_dist >= 0.15: upside_score += 22
    elif target_dist >= 0.08: upside_score += 12
    elif 0 < target_dist < 0.05: upside_score -= 20
    if safe_float(r.get("xhu_pressure_union_upper", 0)) > 0 and safe_float(r.get("close", 0)) > safe_float(r.get("xhu_pressure_union_upper", 0)):
        upside_score += 6
    add("upside_reward", "上方空间", upside_score, 0.06, f"下一压力/目标距离约{target_dist:.1%}")

    # 16 下行风险/防守。
    downside_score = 78
    if defense_dist > 0.18: downside_score -= 28
    elif defense_dist > 0.12: downside_score -= 18
    elif 0 < defense_dist <= 0.06: downside_score += 8
    if rr >= 2.0: downside_score += 10
    elif 0 < rr < 1.2: downside_score -= 20
    add("downside_risk", "下行防守", downside_score, 0.08, f"防守距离{defense_dist:.1%}，RR={rr:.2f}")

    # 17 过热风险，分数越高越安全。
    overheat_score = 85
    if bias20 > 0.25: overheat_score -= 32
    elif bias20 > 0.18: overheat_score -= 22
    elif bias20 > 0.12: overheat_score -= 12
    if bias60 > 0.25: overheat_score -= 15
    if rsi >= 85 or cci >= 300: overheat_score -= 25
    elif rsi >= 80 or cci >= 250: overheat_score -= 12
    if vr1 >= 3.5 or volr >= 5: overheat_score -= 12
    add("overheat_risk", "过热安全", overheat_score, 0.04, f"乖离20={bias20:.1%}, RSI={rsi:.0f}, CCI={cci:.0f}")

    # 18 执行成本。
    exec_score = trad_score
    if bool(r.get("limit_up", False)) and pct >= 9.3:
        exec_score -= 5
    if amount > 0 and amount < 30000000:
        exec_score -= 12
    add("execution_cost", "执行成本", exec_score, 0.03, "成交额/换手/涨停可买性/滑点")

    # 19 市场环境/板块：当前无板块实时强度时，用活跃度和涨停次数近似。
    regime_score = 55 + safe_float(r.get("score_v12_activity", 0)) * 4.0
    if safe_float(r.get("limit_count_100", 0)) >= 3: regime_score += 8
    add("regime_theme", "市场环境", regime_score, 0.02, "板块/活跃度/市场风格；无板块数据时弱化")

    # 20 组合约束：单票阶段先中性；三选后可做同板块/同题材去重。
    portfolio_score = safe_float(r.get("portfolio_constraint_score", 82), 82)
    add("portfolio", "组合约束", portfolio_score, 0.02, "三只候选之间题材/风险暴露去重")

    return dims


def _v16_composite_score_and_caps(r, dims):
    total_weight = sum(float(d.get("weight", 0)) for d in dims) or 1.0
    raw_score = sum(float(d.get("score", 0)) * float(d.get("weight", 0)) for d in dims) / total_weight
    cap = 100.0
    cap_reasons = []
    ds = {d["key"]: float(d["score"]) for d in dims}

    if ds.get("hard_risk", 100) < 50:
        cap = min(cap, 59); cap_reasons.append("硬雷区/重大约束")
    if ds.get("breakout_trigger", 100) < 45 and ds.get("retest_confirmation", 100) < 55:
        cap = min(cap, 69); cap_reasons.append("触发确认不足")
    if ds.get("downside_risk", 100) < 50:
        cap = min(cap, 79); cap_reasons.append("下行防守不足")
    if ds.get("upside_reward", 100) < 45:
        cap = min(cap, 78); cap_reasons.append("上方空间不足")
    if ds.get("tradability", 100) < 40 or ds.get("execution_cost", 100) < 40:
        cap = min(cap, 78); cap_reasons.append("流动性/执行成本不足")
    if ds.get("overheat_risk", 100) < 45:
        cap = min(cap, 76); cap_reasons.append("过热风险封顶")
    if str(r.get("xhu_pressure_model_grade", "")).upper() == "D" and safe_float(r.get("xhu_fake_breakout_count", 0)) > 0:
        cap = min(cap, 68); cap_reasons.append("压力带冲关失败/假突破记忆")

    final_score = min(raw_score, cap)
    grade = _v16_score_to_grade(final_score)
    if cap < 75 and grade in ["S", "A", "B+"]:
        grade = _v16_score_to_grade(cap)
    return float(round(final_score, 2)), grade, float(cap), "；".join(cap_reasons) if cap_reasons else "无硬封顶"


def _v16_main_signal_summary(r):
    signals = []
    if safe_float(r.get("score_xhu_pressure_breakout", 0)) >= 10 or str(r.get("xhu_pressure_model_grade", "")).upper() in ["S", "A"]:
        signals.append(f"复合压力带{str(r.get('xhu_pressure_model_grade',''))}")
    if safe_float(r.get("score_fibo_reclaim", 0)) >= 7:
        signals.append("黄金倍量二次确认")
    if safe_float(r.get("score_advanced_ao_kou", 0)) >= 7:
        signals.append("高级凹口二次倍量")
    if safe_float(r.get("score_v12_pullback_entry", 0)) >= 6:
        signals.append("回踩确认")
    if safe_float(r.get("score_monthly_cycle", 0)) >= 8:
        signals.append("月线中轨修复")
    if safe_float(r.get("score_multi_tf_key_structure", 0)) >= 8:
        signals.append("多周期关键K结构")
    if not signals:
        signals.append(str(r.get("v151_strongest_model_name", "原模型综合优选")) or "原模型综合优选")
    return " + ".join(signals[:4])


def v16_candidate_audit(s):
    """在原V14/V15审核之后，增加20维机构级机会评分、封顶和统一评级。"""
    r = v14_candidate_audit(s)
    dims = _v16_eval_dimensions(r)
    score, grade, cap, cap_reason = _v16_composite_score_and_caps(r, dims)

    # 不完全抹掉原主模型分：如果原V14最终分明显更高但20维未触发，作为B+观察上限；如果20维更高，以20维为准。
    original_final = safe_float(r.get("v14_final_score", r.get("total_score", 0)))
    blended = score * 0.78 + original_final * 0.22
    blended = min(blended, cap)
    final_grade = _v16_score_to_grade(blended)

    # A/S必须有较强Alpha或触发，不允许单靠安全分堆出来。
    ds = {d["key"]: float(d["score"]) for d in dims}
    if final_grade in ["S", "A"] and not ((ds.get("supply_demand", 0) >= 75 or ds.get("market_structure", 0) >= 75 or ds.get("long_cycle", 0) >= 75) and (ds.get("breakout_trigger", 0) >= 65 or ds.get("retest_confirmation", 0) >= 65)):
        final_grade = "B+"
        blended = min(blended, 81.9)
        cap_reason = (cap_reason + "；" if cap_reason != "无硬封顶" else "") + "A/S缺少强结构+强触发共振"

    r["v16_final_score"] = round(float(blended), 2)
    r["v16_final_grade"] = final_grade
    r["v16_raw_20d_score"] = round(float(score), 2)
    r["v16_cap"] = cap
    r["v16_cap_reason"] = cap_reason
    r["v16_main_signal"] = _v16_main_signal_summary(r)
    r["v16_dimensions_json"] = json.dumps(dims, ensure_ascii=False)
    r["v16_dimension_summary"] = "；".join([f"{d['name']}:{d['score']}" for d in dims[:20]])
    r["total_score"] = r["v16_final_score"]
    r["v14_final_score"] = r["v16_final_score"]
    r["v14_level"] = f"{final_grade}｜主导:{r['v16_main_signal']}"
    return r


def select_final_signals_v14(deep_rows, history=None, limit=None):
    """V16最终三选：按20维风险调整后机会分排序。尽量推3只，S/A优先，B+可补位，硬雷区不推。"""
    if history is None:
        history = {}
    limit = int(limit or V14_TARGET_PUSH_COUNT or RESULT_LIMIT or 3)
    audited = [v16_candidate_audit(r) for r in deep_rows]
    blocked = [r for r in audited if r.get("v14_blocked") or safe_float(r.get("v16_final_score", 0)) < V14_MIN_ABSOLUTE_SCORE]
    eligible = [r for r in audited if not r.get("v14_blocked") and safe_float(r.get("v16_final_score", 0)) >= V14_MIN_ABSOLUTE_SCORE]
    eligible = sorted(
        eligible,
        key=lambda x: (
            _v16_grade_rank(x.get("v16_final_grade", "")),
            safe_float(x.get("v16_final_score", 0)),
            safe_float(x.get("v16_raw_20d_score", 0)),
            safe_float(x.get("trade_priority_score", 0)),
            safe_float(x.get("score_trade_quality", 0)),
        ),
        reverse=True,
    )
    final = []
    diagnostics = []
    for r in eligible:
        key = f"{r.get('date','')}_{r.get('code','')}"
        if V14_IGNORE_HISTORY_FOR_RERUN != "1" and key in history:
            rr = dict(r); rr["v14_skip_reason"] = "signals_history已推送过"; diagnostics.append(rr); continue
        final.append(r)
        if len(final) >= limit:
            break
    selected_codes = {str(r.get("code")) for r in final}
    for r in eligible:
        if str(r.get("code")) not in selected_codes:
            rr = dict(r); rr["v14_skip_reason"] = "未进入三选，20维机会分/等级靠后"; diagnostics.append(rr)
    for r in blocked[:20]:
        rr = dict(r); rr["v14_skip_reason"] = rr.get("v14_block_reason") or rr.get("v16_cap_reason") or "低于V16底线/硬约束"; diagnostics.append(rr)
    return final, diagnostics[:20], audited


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


def send_telegram(message):
    ok = _ORIGINAL_SEND_TELEGRAM(message) if _ORIGINAL_SEND_TELEGRAM else False
    global TELEGRAM_PENDING_IMAGES
    if TELEGRAM_PENDING_IMAGES:
        for img, cap in TELEGRAM_PENDING_IMAGES:
            send_telegram_photo(img, cap)
        TELEGRAM_PENDING_IMAGES = []
    return ok


def build_message(final_signals, dates, stock_count=0, kline_success=0, kline_fail=0, deep_count=0, v14_diagnostics=None):
    global TELEGRAM_PENDING_IMAGES
    TELEGRAM_PENDING_IMAGES = []
    lines = []
    lines.append("📊 <b>一号员工选股模型 V16 机构级20维机会评分报告</b>")
    lines.append(f"🗓 排查日期：{', '.join(dates) if dates else '未知'}")
    lines.append(f"⏱ 运行时间：{bj_time_str()} 北京时间")
    lines.extend(build_data_gate_header_lines())
    lines.append(f"股票池：{stock_count}只 | K线成功：{kline_success}只 | 失败：{kline_fail}只 | 深度评分：{deep_count}只")
    lines.append(f"正式输出：<b>{len(final_signals)}</b>只，目标{V14_TARGET_PUSH_COUNT}只。")
    lines.append("口径：原V12/V14所有有效战法保留为信号库；新增复合压力带/供需带后，统一映射到20维评分，按风险调整后的机会等级排序。")
    lines.append("等级说明：S/A=正式高质量机会；B+=观察补位；C/D不作为正式推送。硬雷区、流动性/执行、风险收益比会封顶。")
    lines.append("完整评分表已作为PNG图片发送，不再用杂乱文字冒充表格。")
    lines.append("━━━━━━━━━━━━━━")

    if not final_signals:
        lines.append("⚠️ 今日暂无正式三选股票。通常代表硬雷区、数据覆盖、触发不足或风险封顶。")
        diag = v14_diagnostics_text(v14_diagnostics or [], 10)
        if diag:
            lines.append(html.escape(diag))
        return "\n".join(lines)

    try:
        summary_img = render_v16_summary_table_png(final_signals)
        if summary_img:
            TELEGRAM_PENDING_IMAGES.append((summary_img, "一号员工选股模型V16：今日三选总览表"))
        for i, s in enumerate(final_signals, 1):
            img = render_v16_dimension_table_png(s, f"telegram_tables/v16_{i}_{s.get('code','')}_20d.png")
            if img:
                TELEGRAM_PENDING_IMAGES.append((img, f"{i}. {s.get('name','')}({s.get('code','')}) 20维评分表"))
    except Exception as e:
        print(f"V16报告表格生成失败：{e}")

    for i, s in enumerate(final_signals, 1):
        lines.append(f"<b>{i}. {html.escape(str(s.get('name','')))}({html.escape(str(s.get('code','')))})</b>")
        lines.append(f"等级/总分：<b>{html.escape(str(s.get('v16_final_grade','')))}</b> / {safe_float(s.get('v16_final_score', 0)):.1f}；原深度分{safe_float(s.get('v14_original_total_score', s.get('total_score', 0))):.1f}")
        lines.append(f"主导信号：{html.escape(str(s.get('v16_main_signal','')))}")
        lines.append(f"核心压力带：{safe_float(s.get('xhu_pressure_core_lower',0)):.2f}-{safe_float(s.get('xhu_pressure_core_upper',0)):.2f}；最终压力上沿：{safe_float(s.get('xhu_pressure_union_upper',0)):.2f}；压力带等级：{html.escape(str(s.get('xhu_pressure_model_grade','')))}")
        lines.append(f"确认条件：{html.escape(build_confirm_condition(s))}")
        lines.append(f"放弃条件：{html.escape(build_giveup_condition(s))}")
        lines.append(f"主要封顶/风险：{html.escape(str(s.get('v16_cap_reason','无硬封顶')))}")
        lines.append("—")

    diag = v14_diagnostics_text(v14_diagnostics or [], 5)
    if diag:
        lines.append("落选/拦截诊断：")
        lines.append(html.escape(diag))
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
        print("V14原主模型完整底座+增量体系版：不删V12.6主模型任何有效逻辑；新增阳包阴精细分层、V14后置审核、相对最优三选、分项打分表；财务硬雷区一票否决，普通缺点扣分不杀光。")

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
        print(f"V12.7基础分桶/闸门后进入深度评分：{len(deep_targets)}条")
        print("V12.7基础候选分桶统计：")
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
            send_telegram(build_error_message(warning))
            return

        # V14最终三选：原主模型完整跑完后，只做后置审核/分层扣分/相对最优三选；不重写、不删主模型逻辑。
        final_signals, v14_diagnostics, v14_audited_rows = select_final_signals_v14(deep_rows, history, limit=V14_TARGET_PUSH_COUNT)
        strong_watch_pool = [r for r in v14_audited_rows if (not r.get("v14_blocked")) and str(r.get("code")) not in {str(x.get("code")) for x in final_signals}][:80]

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
                "v14_level": r.get("v14_level", ""),
                "candidate_pool": r.get("candidate_pool", ""),
            }

        print(f"近{CHECK_DAYS}个交易日排查完成：{dates}（默认仅最新有行情日；可用CHECK_DAYS调整）")
        print(f"K线成功：{kline_success} 只 | K线失败：{kline_fail} 只")
        print(f"基础评分数量：{len(base_rows)} 条")
        print(f"深度评分数量：{len(deep_rows)} 条 | 输入：{len(deep_targets)} | 成功：{deep_success} | 失败：{deep_fail} | 跳过：{deep_skip} | 有效样本：{len(deep_rows)}")
        print(f"V14最终三选数量：{len(final_signals)} 只 | 诊断候选：{len(v14_diagnostics)} 只")
        print(f"V14后备观察池数量：{len(strong_watch_pool)} 只（默认不推送，只保存候选JSON）")

        save_candidates_payload(base_rows, deep_rows, final_signals, strong_watch_pool)
        save_signal_history(history)

        msg = build_message(
            final_signals,
            dates,
            stock_count=len(stock_list),
            kline_success=kline_success,
            kline_fail=kline_fail,
            deep_count=len(deep_rows),
            v14_diagnostics=v14_diagnostics
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
