"use strict";

const samples = [
  {
    "target": "2025-01-27",
    "expected_complete": true,
    "basis": "SSE 2025春节休市自01-28开始，故01-27为当月最后交易日",
    "source": "https://www.sse.com.cn/disclosure/announcement/general/c/c_20250123_10770570.shtml"
  },
  {
    "target": "2025-02-28",
    "expected_complete": true,
    "basis": "自然月末且为交易日",
    "source": "calendar"
  },
  {
    "target": "2025-03-31",
    "expected_complete": true,
    "basis": "自然月末且为交易日",
    "source": "calendar"
  },
  {
    "target": "2025-04-30",
    "expected_complete": true,
    "basis": "自然月末；SSE劳动节休市自05-01开始",
    "source": "https://www.sse.com.cn/disclosure/announcement/general/c/c_20250424_10777645.shtml"
  },
  {
    "target": "2025-05-30",
    "expected_complete": true,
    "basis": "05-31为周六且SSE端午节休市05-31至06-02，故05-30为当月最后交易日",
    "source": "https://www.sse.com.cn/disclosure/announcement/general/c/c_20241223_10767108.shtml"
  }
];

function daysInMonth(year, month) {
  return new Date(Date.UTC(year, month, 0)).getUTCDate();
}

function qingtianKeepsLatest(target) {
  const [year, month, day] = target.split("-").map(Number);
  return day >= daysInMonth(year, month);
}

function employee3KeepsLatest(_target) {
  // main@c80a8e1 employee3_runner.py choose_resonance_line:
  // completed = raw_k.iloc[:-1].reset_index(drop=True)
  return false;
}

function wilson(k, n, z = 1.959963984540054) {
  const p = k / n;
  const denominator = 1 + z * z / n;
  const center = (p + z * z / (2 * n)) / denominator;
  const half = z * Math.sqrt((p * (1 - p) + z * z / (4 * n)) / n) / denominator;
  return [center - half, center + half];
}

const rows = samples.map((item) => {
  const qingtianKeeps = qingtianKeepsLatest(item.target);
  const employee3Keeps = employee3KeepsLatest(item.target);
  return {
    ...item,
    qingtian_keeps_latest: qingtianKeeps,
    employee3_keeps_latest: employee3Keeps,
    qingtian_false_exclusion: item.expected_complete && !qingtianKeeps,
    employee3_false_exclusion: item.expected_complete && !employee3Keeps,
    paths_disagree: qingtianKeeps !== employee3Keeps
  };
});

const qingtianFalse = rows.filter((x) => x.qingtian_false_exclusion).length;
const employee3False = rows.filter((x) => x.employee3_false_exclusion).length;
const disagreement = rows.filter((x) => x.paths_disagree).length;
const checks = [
  ["sample_count", rows.length === 5],
  ["all_expected_complete", rows.every((x) => x.expected_complete)],
  ["qingtian_false_exclusion_count", qingtianFalse === 2],
  ["employee3_false_exclusion_count", employee3False === 5],
  ["path_disagreement_count", disagreement === 3],
  ["jan27_qingtian_drop", rows[0].qingtian_false_exclusion],
  ["may30_qingtian_drop", rows[4].qingtian_false_exclusion],
  ["no_model_gate", true]
];

if (!checks.every(([, ok]) => ok)) {
  throw new Error(JSON.stringify(checks));
}

const result = {
  audit_id: "E3-R46-QINGTIAN-COMPLETED-MONTH-BOUNDARY-20260929-0350-BJ",
  sample_identity: "2025-01至2025-05五个已完成交易月边界；日历/交易时效审计，不是股票结果样本",
  rows,
  metrics: {
    boundary_dates: rows.length,
    qingtian_false_exclusions: qingtianFalse,
    qingtian_false_exclusion_rate: qingtianFalse / rows.length,
    qingtian_wilson95: wilson(qingtianFalse, rows.length),
    employee3_false_exclusions: employee3False,
    employee3_false_exclusion_rate: employee3False / rows.length,
    employee3_wilson95: wilson(employee3False, rows.length),
    path_disagreements: disagreement,
    path_disagreement_rate: disagreement / rows.length
  },
  assertions: { passed: checks.filter(([, ok]) => ok).length, total: checks.length }
};

process.stdout.write(JSON.stringify(result, null, 2) + "\n");
