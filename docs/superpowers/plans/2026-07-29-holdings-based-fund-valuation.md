# 基金持仓自算估值 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 盘中未结算时，用基金前十大重仓股 + 占净值比 + 股票实时行情自算估算净值（gsz_est）替代已下线的接口 GSZ，已结算仍用 NAVCHGRT。

**Architecture:** 保持单文件 `workflow/fund.py`，新增 `fetch_holdings`（含季报倒推 + 本地缓存）、`fetch_stock_quotes`（批量）、`estimate_gsz`（口径 B：覆盖率缩放）三个函数；`parse_fund` 增加 `estimate` 入参与 `est_source` 出参，`main`/`cmd_sum` 在 `fetch_funds` 后补拉持仓+行情并注入估算值。

**Tech Stack:** Python 3 stdlib only（`urllib`/`ssl`/`json`/`re`/`html`/`concurrent.futures`/`unittest`），零第三方依赖。

## Global Constraints

- 零依赖，仅 stdlib；系统 `/usr/bin/python3` 可跑。
- 保持单文件 `workflow/fund.py`（不拆模块）。
- 常量默认值（来自 spec）：`SCALE_TO_FULL=True`、`MIN_COVERAGE=20.0`、`HOLDINGS_CACHE_DAYS=7`。
- 数据源端点（已实测可用）：
  - 持仓 `https://fundf10.eastmoney.com/FundArchivesDatas.aspx?type=jjcc&code=<code>&topline=10&year=<Y>&month=<M>`（month 取季度末月 3/6/9/12）
  - 行情 `https://push2.eastmoney.com/api/qt/ulist.np/get?fltt=2&secids=<市场.代码,...>&fields=f12,f14,f2,f3`（`f3`=涨跌幅%）
- 字段口径：持仓 HTML「占净值比」相对基金净值；行情 `f3` 为当日涨跌幅%。
- 中式红涨绿跌 emoji 不变；现有 `fund`/`fund N`/`fund sum`/`fund config` 行为不变。

---

## File Structure

- `workflow/fund.py` — 修改：新增 holdings/quotes/estimate 函数与常量；改 `parse_fund`/`item_for_fund`/`item_total`/`main`/`cmd_sum`；新增 `fund refresh` 子命令。
- `tests/test_fund.py` — 新建：stdlib `unittest`，覆盖纯逻辑（`_parse_holdings_html`、`_recent_quarter_ends`、`estimate_gsz`、`parse_fund` 各分支）。通过 `sys.path` 导入 `workflow/fund.py`。

---

## Task 1: 持仓解析、季报倒推、缓存

**Files:**
- Modify: `workflow/fund.py`（顶部 import + 常量 + 新增函数）
- Test: `tests/test_fund.py`

**Interfaces:**
- Produces:
  - `get_holdings_cache_path() -> str`
  - `load_holdings_cache() -> dict`
  - `save_holdings_cache(cache: dict) -> None`
  - `_recent_quarter_ends(n=4) -> list[tuple[int,int]]`（季度末月，newest-first）
  - `_parse_holdings_html(raw: str) -> list[dict]`（每项 `{secid, name, weight}`，secid 形如 `"1.600519"`）
  - `fetch_holdings(code: str) -> list[dict]`（缓存命中直返，否则远程拉取并写缓存；失败返 `[]`）

- [ ] **Step 1: 新建 `tests/test_fund.py` 骨架与 `_parse_holdings_html` 失败测试**

```python
import sys, os, unittest
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

if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试验证失败**

Run: `python3 tests/test_fund.py`
Expected: `AttributeError: module 'fund' has no attribute '_parse_holdings_html'`

- [ ] **Step 3: 在 `workflow/fund.py` 顶部补 import 与常量**

在 `import urllib.request` 后追加 `import re`、`import html`、`import time`；在 `from datetime import datetime` 后补 `from concurrent.futures import ThreadPoolExecutor`（Task 4 用，先导入无妨）。

在 `# ---------- 常量 ----------` 区 `TIMEOUT` 行后追加：

```python
# 持仓自算估值常量
SCALE_TO_FULL = True       # True=口径B(放大到满仓); False=口径A(其余按0)
MIN_COVERAGE = 20.0        # 前十大占净值比低于此(%)视为不可信, 降级
HOLDINGS_CACHE_DAYS = 7    # 持仓缓存有效期(天); 持仓是季报数据, 季度才变
HOLDINGS_API = "https://fundf10.eastmoney.com/FundArchivesDatas.aspx"
STOCK_QUOTE_API = "https://push2.eastmoney.com/api/qt/ulist.np/get"
```

- [ ] **Step 4: 在 `get_config_path` 函数后新增缓存与持仓函数**

```python
def get_holdings_cache_path() -> str:
    return os.path.join(get_data_dir(), "holdings_cache.json")


def load_holdings_cache() -> dict:
    path = get_holdings_cache_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (ValueError, OSError):
        return {}


def save_holdings_cache(cache: dict) -> None:
    with open(get_holdings_cache_path(), "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False)


def _recent_quarter_ends(n: int = 4):
    """返回最近 n 个季度末月 (year, month)，newest-first。month ∈ {3,6,9,12}。"""
    now = datetime.now()
    qe = [3, 6, 9, 12]
    si = 0
    for i, qm in enumerate(qe):
        if qm <= now.month:
            si = i
    out, i, year = [], si, now.year
    while len(out) < n:
        out.append((year, qe[i]))
        i -= 1
        if i < 0:
            i = len(qe) - 1
            year -= 1
    return out


def _parse_holdings_html(raw: str):
    """解析 FundArchivesDatas jjcc HTML, 提取前十大重仓股。
    返回 [{secid, name, weight}, ...]，secid 形如 '1.600519'。"""
    m = re.search(r"<tbody>(.*?)</tbody>", raw, re.S)
    if not m:
        return []
    rows = re.findall(r"<tr>(.*?)</tr>", m.group(1), re.S)
    out = []
    for r in rows[:10]:
        sec = re.search(r"unify/r/([01]\.\d{6})", r)
        nm = re.search(r"class='tol'><a[^>]*>([^<]+)", r)
        pct = re.search(r"class='tor'>([0-9]+\.[0-9]+)%", r)
        if sec and nm and pct:
            out.append({
                "secid": sec.group(1),
                "name": html.unescape(nm.group(1)),
                "weight": float(pct.group(1)),
            })
    return out


def _cache_fresh(entry: dict) -> bool:
    fetched = entry.get("fetched_at")
    if not fetched:
        return False
    age_days = (datetime.now() - datetime.fromtimestamp(fetched)).days
    return age_days < HOLDINGS_CACHE_DAYS


def _fetch_holdings_remote(code: str):
    """逐季报倒推拉取, 返回 [{secid,name,weight}] 或 []。"""
    for year, month in _recent_quarter_ends(4):
        qs = urllib.parse.urlencode({
            "type": "jjcc", "code": code, "topline": "10",
            "year": str(year), "month": str(month),
        })
        req = urllib.request.Request(
            f"{HOLDINGS_API}?{qs}",
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Alfred funds-alfred)",
                "Referer": "https://fundf10.eastmoney.com/",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT, context=_SSL_CTX) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
        except Exception:
            continue
        stocks = _parse_holdings_html(raw)
        if stocks:
            return stocks
    return []


def fetch_holdings(code: str):
    """取基金前十大重仓股(带缓存)。失败/无季报返 []。"""
    cache = load_holdings_cache()
    entry = cache.get(code)
    if entry and _cache_fresh(entry):
        return entry.get("stocks") or []
    stocks = _fetch_holdings_remote(code)
    if stocks:
        cache[code] = {
            "stocks": stocks,
            "fetched_at": datetime.now().timestamp(),
        }
        save_holdings_cache(cache)
        return stocks
    if entry:  # 远程失败时回退旧缓存(即便过期)
        return entry.get("stocks") or []
    return []
```

- [ ] **Step 5: 运行测试验证通过**

Run: `python3 tests/test_fund.py`
Expected: `OK`（2 tests pass）

- [ ] **Step 6: 提交**

```bash
git add workflow/fund.py tests/test_fund.py
git commit -m "feat: 基金重仓股持仓拉取与缓存

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 2: 股票行情批量拉取 + 估算净值

**Files:**
- Modify: `workflow/fund.py`
- Test: `tests/test_fund.py`（追加 `EstimateTest`）

**Interfaces:**
- Consumes: `fetch_holdings` 的返回结构 `{secid, name, weight}`
- Produces:
  - `fetch_stock_quotes(secids: list[str]) -> dict[str, float]`（key 为裸 6 位代码，value 为涨跌幅%）
  - `estimate_gsz(nav, holdings, quotes, scale_to_full=SCALE_TO_FULL, min_coverage=MIN_COVERAGE) -> dict | None`，返回 `{"gsz", "rate", "cov"}` 或 `None`

- [ ] **Step 1: 追加 `estimate_gsz` 失败测试**

在 `tests/test_fund.py` 追加：

```python
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
        # contrib = 9.46*4.74 + 8.86*3.51 + 3.38*5.45 = 123.07; cov=21.70
        # est% = 123.07/21.70 = 5.6756
        self.assertAlmostEqual(est["rate"], 123.07 / 21.70, places=3)
        self.assertAlmostEqual(est["gsz"], 1.0 * (1 + est["rate"] / 100), places=4)
        self.assertAlmostEqual(est["cov"], 21.70, places=2)

    def test_no_scale_A(self):
        quotes = {"300308": 4.74, "300502": 3.51, "001301": 5.45}
        est = fund.estimate_gsz(1.0, self.HOLDINGS, quotes, scale_to_full=False)
        # est% = contrib/100 = 1.2307
        self.assertAlmostEqual(est["rate"], 1.2307, places=3)

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
```

- [ ] **Step 2: 运行测试验证失败**

Run: `python3 tests/test_fund.py`
Expected: FAIL `AttributeError: module 'fund' has no attribute 'estimate_gsz'`

- [ ] **Step 3: 在 `fetch_holdings` 后新增 `fetch_stock_quotes` 与 `estimate_gsz`**

```python
def fetch_stock_quotes(secids):
    """批量拉取股票当日涨跌幅。返回 {裸代码: 涨跌幅%}。失败返 {}。"""
    if not secids:
        return {}
    qs = urllib.parse.urlencode({
        "fltt": "2",
        "secids": ",".join(secids),
        "fields": "f12,f14,f2,f3",
        "_": str(int(time.time())),
    })
    req = urllib.request.Request(
        f"{STOCK_QUOTE_API}?{qs}",
        headers={"User-Agent": "Mozilla/5.0 (Macintosh; Alfred funds-alfred)"},
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=_SSL_CTX) as resp:
            payload = json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception:
        return {}
    out = {}
    for d in ((payload.get("data") or {}).get("diff")) or []:
        code = d.get("f12")
        if code:
            out[code] = to_float(d.get("f3"), default=0.0)
    return out


def estimate_gsz(nav, holdings, quotes, scale_to_full=SCALE_TO_FULL, min_coverage=MIN_COVERAGE):
    """基于前十大重仓股 + 实时行情估算净值。

    holdings: [{secid('1.600519'), name, weight(%)}, ...]
    quotes: {裸代码: 涨跌幅%}
    返回 {"gsz": 估算净值, "rate": 估算涨幅%, "cov": 覆盖率%} 或 None。
    口径B(scale_to_full=True): est% = Σ(wᵢ×rᵢ)/cov  放大到满仓
    口径A(scale_to_full=False): est% = Σ(wᵢ×rᵢ)/100  其余按0
    """
    if not holdings or nav is None:
        return None
    cov = sum(h["weight"] for h in holdings)
    if cov < min_coverage:
        return None
    contrib = 0.0
    for h in holdings:
        code = h["secid"].split(".")[-1]
        r = quotes.get(code)
        contrib += h["weight"] * (r if r is not None else 0.0)
    if scale_to_full:
        if cov <= 0:
            return None
        est = contrib / cov
    else:
        est = contrib / 100.0
    return {
        "gsz": nav * (1 + est / 100.0),
        "rate": est,
        "cov": cov,
    }
```

- [ ] **Step 4: 运行测试验证通过**

Run: `python3 tests/test_fund.py`
Expected: `OK`（7 tests pass）

- [ ] **Step 5: 提交**

```bash
git add workflow/fund.py tests/test_fund.py
git commit -m "feat: 股票行情批量拉取与持仓估算净值

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 3: parse_fund 注入估算值 + 展示标记

**Files:**
- Modify: `workflow/fund.py`（`parse_fund`、`item_for_fund`、`item_total` 的 `when` 前缀逻辑）
- Test: `tests/test_fund.py`（追加 `ParseFundEstimateTest`）

**Interfaces:**
- Consumes: `estimate_gsz` 的 `{"gsz","rate","cov"}`
- Produces: `parse_fund(..., estimate=None)` 增加 `estimate` 入参；返回 dict 新增 `"est_source"` ∈ `{"settled","holdings","api","none"}`，`"gsz"` 字段改为「有效盘中估算净值」（holdings/api 分支为该估值，settled/none 为 None）。

- [ ] **Step 1: 追加 `parse_fund` 估算分支失败测试**

在 `tests/test_fund.py` 追加：

```python
class ParseFundEstimateTest(unittest.TestCase):
    def _row(self, gsz=None, nav="1.0000", pdate="2026-07-28", navchgrt="1.50", gztime=None):
        return {"fundcode": "240011", "name": "X", "dwjz": nav, "jzrq": pdate,
                "gsz": gsz, "gszzl": None, "gztime": gztime, "navchgrt": navchgrt}

    def test_holdings_branch(self):
        # 未结算(净值日 7-28 != 今日), 估算注入 -> est_source=holdings
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
```

- [ ] **Step 2: 运行测试验证失败**

Run: `python3 tests/test_fund.py`
Expected: FAIL（`est_source` 不存在 / `estimate` 入参未接受）

- [ ] **Step 3: 改 `parse_fund` 签名与分支**

将 `parse_fund` 签名改为：

```python
def parse_fund(api_row: dict, holding: dict, expansion_gztime: Optional[str] = None, estimate: Optional[dict] = None) -> dict:
```

在函数体内，将原三态分支替换为四态（保留前面的 `nav`/`gsz`/`gszzl`/`navchgrt`/`gztime`/`pdate`/`num`/`cost` 解析与 `settled` 判定不变）：

```python
    est_gsz = estimate.get("gsz") if estimate else None
    est_rate = estimate.get("rate") if estimate else None

    if settled:
        rate = navchgrt
        if nav is not None and navchgrt is not None:
            gains = (nav - nav / (1 + navchgrt / 100)) * num
        else:
            gains = None
        base_price = nav
        gsz_out = None
        est_source = "settled"
    elif est_gsz is not None:
        rate = est_rate
        gains = (est_gsz - nav) * num if nav is not None else None
        base_price = est_gsz
        gsz_out = est_gsz
        est_source = "holdings"
    elif gsz is not None:
        rate = gszzl
        gains = (gsz - nav) * num if nav is not None else None
        base_price = gsz
        gsz_out = gsz
        est_source = "api"
    else:
        rate = None
        gains = None
        base_price = nav
        gsz_out = None
        est_source = "none"
```

返回 dict 中把 `"gsz": gsz` 改为 `"gsz": gsz_out`，并新增 `"est_source": est_source`：

```python
    return {
        "code": code,
        "name": name,
        "nav": nav,
        "gsz": gsz_out,
        "rate": rate,
        "settled": settled,
        "est_source": est_source,
        "amount": amount,
        "gains": gains,
        "cost": cost,
        "cost_gains": cost_gains,
        "cost_rate": cost_rate,
        "pdate": pdate,
        "gztime": gztime,
    }
```

（`amount`/`cost_gains`/`cost_rate` 的计算逻辑块在 `base_price` 赋值之后、`return` 之前，保持原样不动。）

- [ ] **Step 4: 改 `item_for_fund` 的 flag**

```python
def item_for_fund(f: dict) -> dict:
    if f["settled"]:
        flag = " ✓"
    elif f.get("est_source") == "holdings":
        flag = " [自算]"
    elif f["gsz"] is not None:
        flag = " [估算]"
    else:
        flag = " [无估值]"
```

（`nav_part` 的 `f["gsz"] is not None and not f["settled"]` 判断不变——holdings/api 都已置 `gsz_out`。）

- [ ] **Step 5: 运行测试验证通过**

Run: `python3 tests/test_fund.py`
Expected: `OK`（11 tests pass）

- [ ] **Step 6: 提交**

```bash
git add workflow/fund.py tests/test_fund.py
git commit -m "feat: parse_fund 注入持仓估算值与 [自算] 标记

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 4: 编排——main / cmd_sum 拉持仓+行情并注入 + fund refresh

**Files:**
- Modify: `workflow/fund.py`（`main`、`cmd_sum`、`when` 前缀逻辑、新增 `fund refresh` 子命令）

**Interfaces:**
- Consumes: `fetch_holdings`、`fetch_stock_quotes`、`estimate_gsz`、改造后的 `parse_fund`

- [ ] **Step 1: 抽出公共编排函数 `build_estimates`**

在 `item_total` 之前新增（供 `main` 与 `cmd_sum` 共用）：

```python
def build_estimates(codes, results):
    """对给定 codes 拉持仓+股票行情, 返回 {code: estimate_dict_or_None}。
    缓存命中时 holdings 零请求; 股票行情一次批量。"""
    if not codes:
        return {}
    holdings_by_code = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        for code, hs in zip(codes, ex.map(fetch_holdings, codes)):
            holdings_by_code[code] = hs
    secids = sorted({h["secid"] for hs in holdings_by_code.values() for h in hs})
    quotes = fetch_stock_quotes(secids)
    out = {}
    for code in codes:
        nav = to_float((results.get(code) or {}).get("dwjz"))
        out[code] = estimate_gsz(nav, holdings_by_code.get(code, []), quotes)
    return out
```

- [ ] **Step 2: 改 `when` 前缀逻辑（`main` 与 `cmd_sum` 共用模式）**

`main` 中原 `has_realtime` 段改为：

```python
    if latest_gztime:
        has_self = any((not f["settled"] and f.get("est_source") == "holdings") for f in parsed)
        has_api = any((not f["settled"] and f.get("est_source") == "api") for f in parsed)
        if has_self:
            prefix = "自算"
        elif has_api:
            prefix = "估算"
        else:
            prefix = "净值"
        when = f"{prefix} @ {latest_gztime}"
```

`cmd_sum` 中对应 `has_realtime` 段同样替换为上述三态（`has_self`/`has_api`/`prefix`）。

- [ ] **Step 3: 在 `main` 的 `fetch_funds` 成功块后注入估算**

在 `parsed = []` / `missing = []` 之前，加：

```python
    estimates = build_estimates(codes, results)
```

并把循环里 `parse_fund(r, holdings_by_code[code], expansion_gztime)` 改为：

```python
        parsed.append(parse_fund(r, holdings_by_code[code], expansion_gztime, estimate=estimates.get(code)))
```

- [ ] **Step 4: 在 `cmd_sum` 同样注入**

`cmd_sum` 在 `fetch_funds` 成功后（`group_parsed` 循环前）加：

```python
    estimates = build_estimates(unique_codes, results)
```

循环里 `parse_fund(r, h, expansion_gztime)` 改为：

```python
        f = parse_fund(r, h, expansion_gztime, estimate=estimates.get(code))
```

- [ ] **Step 5: 新增 `fund refresh` 子命令**

在 `main` 的 `config` 子命令分支后追加：

```python
    if query in ("refresh", "刷新", "更新持仓", "清缓存"):
        path = get_holdings_cache_path()
        removed = os.path.exists(path)
        if removed:
            os.remove(path)
        out = {
            "items": [{
                "uid": "fund-refresh",
                "title": "♻️ 持仓缓存已清空" if removed else "♻️ 无缓存可清",
                "subtitle": "下次查询会重新拉取重仓股持仓（季报数据）",
                "arg": "",
                "valid": True,
            }]
        }
        print(json.dumps(out, ensure_ascii=False))
        return
```

- [ ] **Step 6: 全量测试 + 烟雾测试**

Run: `python3 tests/test_fund.py`
Expected: `OK`（11 tests pass）

Run: `python3 workflow/fund.py 240011` 不适用（240011 需在 funds.json 配置）。改用单基金临时配置验证：

```bash
python3 -c "
import sys; sys.argv=['fund']
import os
os.environ['alfred_workflow_data']='/tmp/funds-test'
sys.path.insert(0,'workflow')
# 临时写一个单基金配置
import json
os.makedirs('/tmp/funds-test', exist_ok=True)
json.dump({'groups':[{'name':'t','funds':[{'code':'240011','num':100}]}]}, open('/tmp/funds-test/funds.json','w'), ensure_ascii=False)
import fund; fund.main()
"
```

Expected: 输出 JSON，240011 行带 `[自算]` 标记与估算涨跌幅（盘中）或 `✓`（已结算）。

- [ ] **Step 7: 提交**

```bash
git add workflow/fund.py
git commit -m "feat: 盘中持仓估算编排 + fund refresh 子命令

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 5: 集成校准（240011）+ 文档

**Files:**
- Modify: `README.md`（数据接口节、计算逻辑节、FAQ、关键词表）
- No new tests（人工对比）

- [ ] **Step 1: 用 240011 实跑并记录估算值**

Run（盘中）：

```bash
python3 -c "
import os,sys,json
os.environ['alfred_workflow_data']='/tmp/funds-cal'
os.makedirs('/tmp/funds-cal',exist_ok=True)
json.dump({'groups':[{'name':'t','funds':[{'code':'240011','num':100}]}]}, open('/tmp/funds-cal/funds.json','w'), ensure_ascii=False)
sys.path.insert(0,'workflow'); import fund; fund.main()
" | python3 -c "import sys,json; d=json.load(sys.stdin); [print(i['title'],'|',i.get('subtitle')) for i in d['items']]"
```

记录 `自算` 涨跌幅，与用户观察到的真实盘中估值/收盘涨幅对比：
- 若真实值明显接近口径 A（`SCALE_TO_FULL=False`），把 `SCALE_TO_FULL` 改 `False` 重测。
- 默认 B，仅当 A 明显更准才翻转。

- [ ] **Step 2: 更新 README**

在「数据接口」节末尾追加一段说明持仓自算估值的数据源（持仓 + 行情端点）；在「计算逻辑」表新增一行「盘中估算（持仓自算）= NAV × (1 + Σ(权重ᵢ×涨幅ᵢ)/覆盖率 ÷ 100)」；在「关键词」表加 `fund refresh`；在 FAQ 加一条「Q: 持仓估算准吗？A: 对指数/行业基金较准，基于季报前十大重仓股，覆盖率不足或调仓后会失真，仅供参考」。

- [ ] **Step 3: 提交**

```bash
git add README.md
git commit -m "docs: 补充持仓自算估值说明与 fund refresh

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage**：§五数据流→Task1-4；§六组件→Task1-2；§七计算口径→Task2（A/B 都测）；§八数据源→Task1/2；§九季报倒推→Task1 `_recent_quarter_ends`；§十兜底降级→Task3 四态分支 + Task1 `fetch_holdings` 失败返[] + Task2 `estimate_gsz` 低覆盖率返 None；§十一展示标记→Task3 `item_for_fund` + Task4 `when` 前缀；§十二缓存→Task1；§十三性能→Task4 `ThreadPoolExecutor` + 批量行情；§十四常量→Task1；§十五测试→Task1-3 单测 + Task5 集成；§十六局限性→README FAQ。✓ 全覆盖。

**Placeholder scan**：无 TBD/TODO；每个代码步骤均有完整代码。✓

**Type consistency**：`estimate_gsz` 返回 `{"gsz","rate","cov"}`，Task3 `parse_fund` 读取 `estimate.get("gsz")`/`estimate.get("rate")` 一致；`est_source` 在 Task3 定义、Task4 读取一致；`fetch_holdings` 返回 `[{secid,name,weight}]`，Task2 `estimate_gsz` 读取 `h["secid"]`/`h["weight"]` 一致。✓
