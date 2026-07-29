import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "workflow"))
import fund


class ParseHoldingsTest(unittest.TestCase):
    SAMPLE = (
        "<table><tbody>"
        "<tr><td>1</td><td><a href='//quote.eastmoney.com/unify/r/1.600519'>600519</a></td>"
        "<td class='tol'><a href='//quote.eastmoney.com/unify/r/1.600519'>贵州茅台</a></td>"
        "<td class='tor'>17.28%</td></tr>"
        "<tr><td>2</td><td><a href='//quote.eastmoney.com/unify/r/0.000858'>000858</a></td>"
        "<td class='tol'><a href='//quote.eastmoney.com/unify/r/0.000858'>五 粮 液</a></td>"
        "<td class='tor'>9.10%</td></tr>"
        "</tbody></table>"
    )

    def test_parse_top10(self):
        out = fund._parse_holdings_html(self.SAMPLE)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["secid"], "1.600519")
        self.assertEqual(out[0]["name"], "贵州茅台")
        self.assertAlmostEqual(out[0]["weight"], 17.28)
        self.assertEqual(out[1]["secid"], "0.000858")

    def test_parse_empty(self):
        self.assertEqual(fund._parse_holdings_html("var apidata={ content:''}"), [])


class EstimateTest(unittest.TestCase):
    HOLDINGS = [
        {"secid": "0.300308", "name": "中际旭创", "weight": 9.46},
        {"secid": "0.300502", "name": "新易盛",   "weight": 8.86},
        {"secid": "0.001301", "name": "尚太科技", "weight": 3.38},
    ]  # cov = 21.70

    def test_scale_to_full_B(self):
        # 中际旭创 +4.74, 新易盛 +3.51, 尚太科技 +5.45
        quotes = {"300308": 4.74, "300502": 3.51, "001301": 5.45}
        est = fund.estimate_gsz(1.0, self.HOLDINGS, quotes, scale_to_full=True)
        self.assertIsNotNone(est)
        # contrib = 9.46*4.74 + 8.86*3.51 + 3.38*5.45 = 94.36; cov=21.70
        # est% = 94.36/21.70 = 4.3484
        self.assertAlmostEqual(est["rate"], 94.36 / 21.70, places=3)
        self.assertAlmostEqual(est["gsz"], 1.0 * (1 + est["rate"] / 100), places=4)
        self.assertAlmostEqual(est["cov"], 21.70, places=2)

    def test_no_scale_A(self):
        quotes = {"300308": 4.74, "300502": 3.51, "001301": 5.45}
        est = fund.estimate_gsz(1.0, self.HOLDINGS, quotes, scale_to_full=False)
        # est% = contrib/100 = 0.9436
        self.assertAlmostEqual(est["rate"], 0.9436, places=3)

    def test_low_coverage_returns_none(self):
        tiny = [{"secid": "0.300308", "name": "x", "weight": 5.0}]  # cov=5 < 20
        self.assertIsNone(fund.estimate_gsz(1.0, tiny, {"300308": 1.0}))

    def test_empty_holdings(self):
        self.assertIsNone(fund.estimate_gsz(1.0, [], {}))

    def test_missing_quote_treated_as_zero(self):
        # 缺新易盛行情 -> 该股按 0; 仍按 cov 缩放
        quotes = {"300308": 4.74, "001301": 5.45}  # 300502 缺失
        est = fund.estimate_gsz(1.0, self.HOLDINGS, quotes, scale_to_full=True)
        self.assertIsNotNone(est)  # 不因缺失而整体失败


if __name__ == "__main__":
    unittest.main()
