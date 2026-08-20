import sys
import os
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "workflow"))
import fund


class CurlGetTest(unittest.TestCase):
    def test_failure_returns_empty(self):
        # 不可达地址 -> 返回 "" (不抛异常)
        self.assertEqual(fund._curl_get_text("http://127.0.0.1:1/x", timeout=2), "")


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


class IndexSecidTest(unittest.TestCase):
    def test_sh_index(self):
        # 000688 科创50 -> 沪市 1.
        self.assertEqual(fund._index_secid("000688"), "1.000688")

    def test_sz_index(self):
        # 399997 中证白酒 -> 深市 0.
        self.assertEqual(fund._index_secid("399997"), "0.399997")

    def test_sz_etf(self):
        # 159 开头深市 ETF (如 159915) -> 深市 0.
        self.assertEqual(fund._index_secid("159915"), "0.159915")

    def test_linked_etf_mapping(self):
        # 标普指数 SPCLLHCP 无法直接查行情 -> 映射到母 ETF 515450 (沪市 1.)
        self.assertEqual(fund._index_secid("SPCLLHCP"), "1.515450")

    def test_unknown_letter_code_empty(self):
        # 未知字母代码 (非港股/母ETF映射) -> 空
        self.assertEqual(fund._index_secid("NOPEXX"), "")


class IndexQuoteCodeTest(unittest.TestCase):
    """INDEXCODE -> 可查行情载体 (裸代码)。数字/港股字母原样, 其余查母ETF映射。"""

    def test_numeric_passthrough(self):
        self.assertEqual(fund._index_quote_code("000688"), "000688")
        self.assertEqual(fund._index_quote_code("399997"), "399997")

    def test_hk_letter_passthrough(self):
        self.assertEqual(fund._index_quote_code("HSTECH"), "HSTECH")

    def test_linked_etf_resolved(self):
        self.assertEqual(fund._index_quote_code("SPCLLHCP"), "515450")

    def test_unknown_empty(self):
        self.assertEqual(fund._index_quote_code("NOPEXX"), "")
        self.assertEqual(fund._index_quote_code(""), "")


class IndexEstimateTest(unittest.TestCase):
    def test_index_estimate(self):
        # 跟踪指数 000688 当日 -0.87%, nav=1.3065
        quotes = {"000688": -0.87}
        est = fund.estimate_gsz_by_index(1.3065, "000688", quotes)
        self.assertIsNotNone(est)
        self.assertAlmostEqual(est["rate"], -0.87, places=2)
        self.assertAlmostEqual(est["gsz"], 1.3065 * (1 - 0.87 / 100), places=4)

    def test_index_missing_quote_returns_none(self):
        self.assertIsNone(fund.estimate_gsz_by_index(1.0, "000688", {}))

    def test_index_no_nav_returns_none(self):
        self.assertIsNone(fund.estimate_gsz_by_index(None, "000688", {"000688": 1.0}))


class ParseFundEstimateTest(unittest.TestCase):
    def _row(self, gsz=None, gszzl=None, nav="1.0000", pdate="2026-07-28", navchgrt="1.50", gztime=None):
        return {"fundcode": "240011", "name": "X", "dwjz": nav, "jzrq": pdate,
                "gsz": gsz, "gszzl": gszzl, "gztime": gztime, "navchgrt": navchgrt}

    def test_holdings_branch(self):
        # 未结算(净值日 7-28 != 估值日 7-29), 估算注入 -> est_source=holdings
        row = self._row(pdate="2026-07-28")
        est = {"gsz": 1.0191, "rate": 1.91, "cov": 64.33}
        f = fund.parse_fund(row, {"num": 100, "cost": None}, expansion_gztime="2026-07-29 10:00", estimate=est)
        self.assertEqual(f["est_source"], "holdings")
        self.assertAlmostEqual(f["gsz"], 1.0191, places=4)
        self.assertAlmostEqual(f["rate"], 1.91, places=2)
        self.assertAlmostEqual(f["gains"], (1.0191 - 1.0) * 100, places=2)

    def test_api_gsz_branch_when_no_estimate(self):
        row = self._row(gsz="1.0200", gszzl="2.00", pdate="2026-07-28")
        f = fund.parse_fund(row, {"num": 100}, estimate=None)
        self.assertEqual(f["est_source"], "api")
        self.assertAlmostEqual(f["gsz"], 1.0200, places=4)

    def test_settled_overrides_estimate(self):
        # 净值日 == 估值日 -> 已结算, 用 NAVCHGRT, 忽略 estimate
        row = self._row(pdate="2026-07-29", nav="1.0150", navchgrt="1.50", gztime="2026-07-29 15:00")
        est = {"gsz": 1.0191, "rate": 1.91, "cov": 64.33}
        f = fund.parse_fund(row, {"num": 100}, expansion_gztime="2026-07-29 15:00", estimate=est)
        self.assertEqual(f["est_source"], "settled")
        self.assertIsNone(f["gsz"])

    def test_none_when_nothing(self):
        row = self._row(gsz=None, pdate="2026-07-28")
        f = fund.parse_fund(row, {"num": 100}, estimate=None)
        self.assertEqual(f["est_source"], "none")
        self.assertIsNone(f["gsz"])


class BuildEstimatesLinkedEtfTest(unittest.TestCase):
    """008163 场景: 持仓覆盖不足 + INDEXCODE=SPCLLHCP 无法直查行情
    -> build_estimates 经母 ETF 515450 行情回退出估值。"""

    RESULT = {"008163": {"fundcode": "008163", "dwjz": "1.0301", "jzrq": "2026-08-14",
                         "gsz": None, "gszzl": None, "gztime": None, "navchgrt": "-0.48"}}

    def test_falls_back_to_linked_etf(self):
        with mock.patch.object(fund, "fetch_holdings", return_value=[]), \
             mock.patch.object(fund, "fetch_fund_detail", return_value="SPCLLHCP"), \
             mock.patch.object(fund, "fetch_stock_quotes", return_value={"515450": 1.23}):
            est = fund.build_estimates(["008163"], self.RESULT)["008163"]
        self.assertIsNotNone(est)
        self.assertAlmostEqual(est["rate"], 1.23, places=2)
        self.assertAlmostEqual(est["gsz"], 1.0301 * (1 + 1.23 / 100), places=4)

    def test_linked_etf_missing_quote_still_none(self):
        # 行情里没有母 ETF 代码 -> 保持无估值 (不编造)
        with mock.patch.object(fund, "fetch_holdings", return_value=[]), \
             mock.patch.object(fund, "fetch_fund_detail", return_value="SPCLLHCP"), \
             mock.patch.object(fund, "fetch_stock_quotes", return_value={"600519": 2.0}):
            est = fund.build_estimates(["008163"], self.RESULT)["008163"]
        self.assertIsNone(est)


if __name__ == "__main__":
    unittest.main()
