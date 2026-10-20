from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import production_metrics as metrics
import production as prod


class AcceptedCostMetrics(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.root = Path(t.name)
        p = self.root / "invoice.txt"
        p.write_text("TEST ONLY billing fixture")
        self.evidence = {"path": p.name, "sha256": prod.hashlib.sha256(p.read_bytes()).hexdigest()}

    def job(self, amount, state="completed", sha="result1"):
        return {"kind": "VideoGeneration", "model": "ltx-2-5-fast", "provider": "ltx", "status": state,
            "output_sha256": sha, "quote": {"currency": "USD", "amount": 9},
            "actual_charge": {"currency": "USD", "amount": amount, "source": "test-only-invoice", "evidence": self.evidence}}

    def test_failed_attempt_cost_is_included_in_success_cost(self):
        jobs = [self.job(1), self.job(.5, "failed", None)]
        r = metrics.summarize(jobs, {"result1": 8}, self.root)["groups"][0]
        self.assertEqual(r["attempts"], 2)
        self.assertEqual(r["accepted_result_count"], 1)
        self.assertEqual(r["actual_cost_per_accepted_result"], 1.5)
        self.assertEqual(r["actual_cost_per_accepted_second"], 1.5 / 8)

    def test_unknown_actual_charge_does_not_become_zero_or_estimate(self):
        job = self.job(1)
        job["actual_charge"] = None
        r = metrics.summarize([job], {"result1": 8}, self.root)["groups"][0]
        self.assertIsNone(r["actual_total"])
        self.assertIsNone(r["actual_cost_per_accepted_result"])
        self.assertEqual(r["estimate_total"], 9)

    def test_missing_invoice_and_unassessed_adoption_remain_unknown(self):
        (self.root / "invoice.txt").unlink()
        r = metrics.summarize([self.job(1)], None, self.root)["groups"][0]
        self.assertIsNone(r["actual_total"])
        self.assertIsNone(r["accepted_result_count"])

    def test_different_provider_units_are_never_added(self):
        a, b = self.job(1), self.job(1)
        b.update(provider="higgsfield", quote={"credits": 8}, actual_charge=None)
        r = metrics.summarize([a, b], {"result1": 8}, self.root)
        self.assertEqual(len(r["groups"]), 2)
        self.assertIsNone(r["cross_provider_or_currency_total"])

    def test_legacy_quote_and_saved_result_remain_estimate_not_actual(self):
        legacy = {"status": "completed", "quoted_credits": 4, "actual_credits": None, "result_sha256": "result1"}
        r = metrics.summarize([legacy], None, self.root)["groups"][0]
        self.assertEqual(r["estimate_total"], 4)
        self.assertEqual(r["currency_or_unit"], "credits")
        self.assertEqual(r["downloaded"], 1)
        self.assertIsNone(r["actual_total"])


if __name__ == "__main__":
    unittest.main()
