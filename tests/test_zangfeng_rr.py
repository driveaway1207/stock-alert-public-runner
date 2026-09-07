"""Pure calculation tests: no scanner, entry, network or production writes."""
import importlib.util
import math
from pathlib import Path
import unittest
import pandas as pd

spec = importlib.util.spec_from_file_location("zangfeng_rr_test", Path(__file__).resolve().parents[1] / "藏锋.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

def history():
    return pd.DataFrame({"date": pd.bdate_range("2024-01-02", periods=160).strftime("%Y-%m-%d"), "open": 9.9, "high": 10., "low": 9.8, "close": 10., "volume": 1e6, "amount": 5e7, "name": "测试"})

def lines(trigger=10.25, support=9., target=11.):
    return {"pressure": trigger, "trigger": trigger, "support": support, "next_pressure": target, "pressure_info": {"resonance": 5, "weighted_resonance": 7, "cut": 0}, "support_info": {"resonance": 4}}

class RRTest(unittest.TestCase):
    def test_near_high_preserved(self):
        d=history(); d.loc[20,"high"]=10.26
        r=m.estimate_lines(d)
        self.assertEqual(r["next_pressure"],10.26)
        self.assertEqual(r["next_pressure_info"]["source"],"historical_high_candidate")
    def test_absent_high_unknown(self):
        r=m.estimate_lines(history())
        self.assertEqual(r["next_pressure"],0.)
        self.assertIsNone(r["next_pressure_info"]["quality"])
        v=m.calculate_zangfeng(history(),r)
        self.assertFalse(v["eligible"])
        self.assertIn("上方压力未知，无法核验RR",v["reject_reasons"])
    def test_no_skip_weak_near_high(self):
        d=history(); d.loc[20,"high"]=10.26; d.loc[30:40,"high"]=11.
        r=m.estimate_lines(d)
        self.assertEqual(r["next_pressure"],10.26)
        self.assertLess(r["next_pressure_info"]["quality"]["resonance"],m.MIN_LINE_RESONANCE)
    def test_trigger_entry(self):
        r=m.calculate_zangfeng(history(),lines())["metrics"]
        self.assertEqual(r["rr_entry_price"],10.25)
        self.assertEqual(r["rr_ratio"],.6)
    def test_no_cheaper_past_entry(self):
        r=m.calculate_zangfeng(history(),lines(trigger=9.95))["metrics"]
        self.assertEqual(r["rr_entry_price"],10.)
        self.assertEqual(r["rr_ratio"],1.)
    def test_no_space_above_entry(self):
        r=m.calculate_zangfeng(history(),lines(trigger=11.,target=10.8))
        self.assertEqual(r["metrics"]["rr_status"],"no_space_above_entry")
        self.assertFalse(r["eligible"])
    def test_unknown_metadata_rejected(self):
        v=lines(target=15.); v["next_pressure_info"]={"source":"unknown"}
        r=m.calculate_zangfeng(history(),v)
        self.assertEqual(r["metrics"]["rr_status"],"unknown_pressure")
        self.assertEqual(r["metrics"]["rr_ratio"],0.)
    def test_nonfinite_and_missing_target(self):
        for x in [None,float("nan"),float("inf"),0,-1]:
            with self.subTest(x=x):
                r=m.calculate_zangfeng(history(),lines(target=x))
                self.assertEqual(r["metrics"]["rr_status"],"unknown_pressure")
                self.assertTrue(math.isfinite(r["metrics"]["rr_ratio"]))
                self.assertFalse(r["eligible"])
    def test_invalid_support(self):
        for x in [0,10,10.5,None]:
            with self.subTest(x=x):
                r=m.calculate_zangfeng(history(),lines(support=x))
                self.assertEqual(r["metrics"]["rr_status"],"invalid_support")
                self.assertEqual(r["metrics"]["rr_ratio"],0.)
    def test_original_pure_self_check(self):
        self.assertTrue(m.self_check()["ok"])

if __name__=="__main__":
    unittest.main()
