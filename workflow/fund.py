#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Alfred Script Filter: 查看自选基金当前情况
数据源: 天天基金 FundMNFInfo 批量接口 (与 choose-funds / LiuRabt 扩展同款)
  https://fundmobapi.eastmoney.com/FundMNewApi/FundMNFInfo
  参数 plat=Android&appType=ttjj&product=EFund&Version=1, Fcodes 批量请求

接口提供两套涨跌幅:
  - 盘中估算 GSZ/GSZZL/GZTIME (交易时段才有; 主动管理型基金可能始终没有)
  - 净值涨跌幅 NAVCHGRT (结算后即当日真实涨跌幅)
三态判定 (与 choose-funds 一致): 已结算 (净值日=估值日) 用 NAVCHGRT;
有 GSZ 用盘中实时估算; 否则留空显示「-」, 不用昨日 NAVCHGRT 冒充今日。
"""

import json
import os
import random
import re
import html
import subprocess
import sys
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Optional

# ---------- 常量 ----------
# 估值接口: 天天基金 FundMNFInfo (批量, 与 choose-funds 扩展同款)
API_URL = "https://fundmobapi.eastmoney.com/FundMNewApi/FundMNFInfo"
DEVICE_ID = "00000000-0000-4000-8000-000000000000"  # 原项目用随机 UUID, 固定值即可
TIMEOUT = 6  # seconds per request

# 持仓自算估值常量
SCALE_TO_FULL = True       # True=口径B(放大到满仓); False=口径A(其余按0)
MIN_COVERAGE = 20.0        # 前十大占净值比低于此(%)视为不可信, 降级
HOLDINGS_CACHE_DAYS = 7    # 持仓缓存有效期(天); 持仓是季报数据, 季度才变
HOLDINGS_API = "https://fundf10.eastmoney.com/FundArchivesDatas.aspx"
STOCK_QUOTE_API = "https://push2.eastmoney.com/api/qt/ulist.np/get"

# 中式红涨绿跌
EMOJI_UP = "📈"
EMOJI_DOWN = "📉"
EMOJI_FLAT = "➖"


# ---------- 配置文件 ----------
def get_data_dir() -> str:
    """Alfred 5 会设置 alfred_workflow_data。本地直接跑时用 ~/Library/... 兜底。"""
    d = os.environ.get("alfred_workflow_data")
    if not d:
        bundle = os.environ.get("alfred_workflow_bundleid", "com.denis.funds-alfred")
        d = os.path.expanduser(
            f"~/Library/Application Support/Alfred/Workflow Data/{bundle}"
        )
    os.makedirs(d, exist_ok=True)
    return d


def get_config_path() -> str:
    return os.path.join(get_data_dir(), "funds.json")


SAMPLE_CONFIG = {
    "_doc": (
        "groups=分组列表, 每组有 name 与 funds; "
        "funds[*]: code=基金代码(必填), num=持有份额(必填), cost=成本价(可选,有则显示持仓总收益). "
        "兼容旧版顶层 funds (会被当作单个默认分组)."
    ),
    "groups": [
        {
            "name": "核心持仓",
            "funds": [
                {"code": "161725", "num": 10000, "cost": 0.5162},
                {"code": "110022", "num": 200, "cost": 2.50},
            ],
        },
        {
            "name": "卫星仓",
            "funds": [
                {"code": "001618", "num": 500},
            ],
        },
    ],
}


def load_config() -> dict:
    """读取配置；不存在则创建样例文件。"""
    path = get_config_path()
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(SAMPLE_CONFIG, f, ensure_ascii=False, indent=2)
        return SAMPLE_CONFIG
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def normalize_groups(cfg: dict) -> list:
    """将配置统一成 [{name, funds:[...]}, ...] 形式。

    优先读取 cfg['groups']; 不存在但有顶层 cfg['funds'] 时
    包装为单个名为 '默认' 的分组以保持向后兼容。
    每个分组的 funds 字段缺失/非列表时按空列表处理。
    """
    groups = cfg.get("groups")
    if isinstance(groups, list) and groups:
        out = []
        for i, g in enumerate(groups):
            if not isinstance(g, dict):
                continue
            name = (g.get("name") or f"分组 {i + 1}").strip() or f"分组 {i + 1}"
            funds = g.get("funds") if isinstance(g.get("funds"), list) else []
            out.append({"name": name, "funds": funds})
        if out:
            return out
    # 旧格式兼容
    legacy = cfg.get("funds")
    if isinstance(legacy, list):
        return [{"name": "默认", "funds": legacy}]
    return []


# ---------- HTTP ----------
def _curl_get_text(url: str, headers: Optional[dict] = None, timeout: int = TIMEOUT,
                   retries: int = 2) -> str:
    """用 curl 子进程发起 GET, 返回响应文本, 失败返 ''。

    天天基金 push2 行情服务器对 Python urllib 的 TLS 指纹反爬 (直接 RST 连接,
    urllib 全部 RemoteDisconnected), 而 curl 的 TLS 指纹可通过。curl 为 macOS
    自带, 不破坏零依赖。fundmobapi/fundf10 当前 urllib 仍可用, 但统一走 curl
    更稳, 避免后续反爬升级再次"突然全 0"。

    retries: 额外重试次数 (总共 retries+1 次), 指数退避 + 随机抖动。
    """
    for attempt in range(retries + 1):
        if attempt > 0:
            delay = (2 ** (attempt - 1)) + random.uniform(0, 0.5)
            time.sleep(delay)
        cmd = ["curl", "-sS", "-m", str(timeout), "--compressed", url]
        for k, v in (headers or {}).items():
            cmd += ["-H", f"{k}: {v}"]
        try:
            cp = subprocess.run(cmd, capture_output=True, timeout=timeout + 3)
            text = cp.stdout.decode("utf-8", errors="replace")
            if text.strip():
                return text
        except Exception:
            pass
    return ""


def _normalize_row(row: dict) -> dict:
    """把 FundMNFInfo 字段名归一化, 复用下游 parse_fund。"""
    return {
        "fundcode": row.get("FCODE"),
        "name": row.get("SHORTNAME"),
        "dwjz": row.get("NAV"),
        "jzrq": row.get("PDATE"),
        "gsz": row.get("GSZ"),
        "gszzl": row.get("GSZZL"),
        "gztime": row.get("GZTIME"),
        "navchgrt": row.get("NAVCHGRT"),  # 净值涨跌幅, GSZ 缺失时兜底
    }


def fetch_funds(codes, retries=2):
    """批量拉取基金估值。返回 (results, expansion_gztime)。

    results: {code: normalized_row_or_None}
    expansion_gztime: API 响应级 GZTIME（单只基金 GZTIME 为 null 时的 fallback）

    使用天天基金 FundMNFInfo 批量端点; 不存在的 code 不会出现在 Datas 中 -> None (missing)。
    遇到空响应或大量缺失时自动重试 (retries 次额外尝试)。
    """
    out = {c: None for c in codes}
    expansion_gztime = None
    if not codes:
        return out, expansion_gztime
    qs = urllib.parse.urlencode({
        "pageIndex": "1",
        "pageSize": "200",
        "plat": "Android",
        "appType": "ttjj",
        "product": "EFund",
        "Version": "1",
        "deviceid": DEVICE_ID,
        "Fcodes": ",".join(codes),
    })
    req_headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Alfred funds-alfred)",
        "Accept": "*/*",
        "Referer": "https://fund.eastmoney.com/",
    }
    for attempt in range(retries + 1):
        if attempt > 0:
            delay = (2 ** (attempt - 1)) + random.uniform(0, 0.5)
            time.sleep(delay)
        payload = json.loads(_curl_get_text(f"{API_URL}?{qs}", req_headers) or "{}")
        expansion = payload.get("Expansion") or {}
        expansion_gztime = expansion.get("GZTIME")
        datas = payload.get("Datas") or []
        if datas:  # 有数据直接返回, 不再重试
            for row in datas:
                c = row.get("FCODE")
                if c:
                    out[c] = _normalize_row(row)
            return out, expansion_gztime
    return out, expansion_gztime


# ---------- 持仓与估算 ----------
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
    """返回最近 n 个季度末月 (year, month), newest-first。month ∈ {3,6,9,12}。"""
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
    返回 [{secid, name, weight}, ...], secid 形如 '1.600519'。"""
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


def _fetch_holdings_remote(code: str, retries: int = 1):
    """逐季报倒推拉取, 返回 [{secid, name, weight}] 或 []。
    全部季报失败时重试一轮 (retries 次额外尝试), 应对服务器临时错误页。"""
    for attempt in range(retries + 1):
        if attempt > 0:
            time.sleep(1.0 + random.uniform(0, 0.5))
        for year, month in _recent_quarter_ends(4):
            qs = urllib.parse.urlencode({
                "type": "jjcc",
                "code": code,
                "topline": "10",
                "year": str(year),
                "month": str(month),
            })
            raw = _curl_get_text(
                f"{HOLDINGS_API}?{qs}",
                {
                    "User-Agent": "Mozilla/5.0 (Macintosh; Alfred funds-alfred)",
                    "Referer": "https://fundf10.eastmoney.com/",
                },
            )
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


def fetch_stock_quotes(secids, retries=2):
    """批量拉取股票当日涨跌幅。返回 {裸代码: 涨跌幅%}。失败返 {}。

    遇到空行情时自动重试 (retries 次额外尝试), 因为空行情会导致所有基金自算估值降级。
    """
    if not secids:
        return {}
    qs = urllib.parse.urlencode({
        "fltt": "2",
        "secids": ",".join(secids),
        "fields": "f12,f14,f2,f3",
        "_": str(int(time.time())),
    })
    for attempt in range(retries + 1):
        if attempt > 0:
            delay = (2 ** (attempt - 1)) + random.uniform(0, 0.5)
            time.sleep(delay)
        try:
            payload = json.loads(_curl_get_text(f"{STOCK_QUOTE_API}?{qs}", {"User-Agent": "Mozilla/5.0"}) or "{}")
        except (ValueError, TypeError):
            continue
        out = {}
        for d in ((payload.get("data") or {}).get("diff")) or []:
            code = d.get("f12")
            if code:
                out[code] = to_float(d.get("f3"), default=0.0)
        if out:  # 有行情数据直接返回
            return out
    return {}


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


def build_estimates(codes, results):
    """对给定 codes 拉持仓+股票行情, 返回 {code: estimate_dict_or_None}。
    缓存命中时 holdings 零请求; 股票行情一次批量。供 main / cmd_sum 共用。"""
    if not codes:
        return {}
    holdings_by_code = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        for code, hs in zip(codes, ex.map(fetch_holdings, codes)):
            holdings_by_code[code] = hs
    # 跟踪指数 (用于联接/指数基金: 重仓覆盖不足时回退)
    detail_by_code = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        for code, idx in zip(codes, ex.map(fetch_fund_detail, codes)):
            detail_by_code[code] = idx
    secids = sorted(
        {h["secid"] for hs in holdings_by_code.values() for h in hs}
        | {_index_secid(idx) for idx in detail_by_code.values() if idx}
    )
    quotes = fetch_stock_quotes(secids)
    out = {}
    # 行情接口整体故障(quotes 空)时, 不产生假的 0% 估算, 全部降级为无估值
    if not quotes:
        return {code: None for code in codes}
    for code in codes:
        nav = to_float((results.get(code) or {}).get("dwjz"))
        # 优先: 重仓股加权估算 (指数/行业基金)
        est = estimate_gsz(nav, holdings_by_code.get(code, []), quotes)
        # 回退: 跟踪指数估算 (ETF联接/指数基金, 重仓覆盖不足)
        if est is None:
            idx = detail_by_code.get(code)
            if idx:
                est = estimate_gsz_by_index(nav, idx, quotes)
        out[code] = est
    return out


# 港股指数代码 -> push2 secid。前缀不统一: 恒指/国企 100., 恒生科技 124.。
# 仅收录 QDII 联接/指数基金可能跟踪的恒生系列指数, 其余未知代码返回空。
HK_INDEX_SECID = {
    "HSI": "100.HSI",
    "HSCEI": "100.HSCEI",
    "HSTECH": "124.HSTECH",
}


def _index_secid(index_code: str) -> str:
    """指数代码 -> push2 secid。399xxx 深市(0.), 其余沪市(1.); 港股指数走映射表。"""
    if not index_code:
        return ""
    if index_code.isdigit():
        market = "0" if index_code.startswith("399") else "1"
        return f"{market}.{index_code}"
    return HK_INDEX_SECID.get(index_code, "")


def estimate_gsz_by_index(nav, index_code, quotes):
    """用跟踪指数当日涨跌估算净值 (联接/指数基金)。
    返回 {"gsz", "rate", "cov"} 或 None。cov=100 (指数全覆盖)。"""
    if nav is None or not index_code:
        return None
    rate = quotes.get(index_code)
    if rate is None:
        return None
    return {
        "gsz": nav * (1 + rate / 100.0),
        "rate": rate,
        "cov": 100.0,
    }


def fetch_fund_detail(code: str):
    """取基金跟踪指数代码 (FundMNDetailInformation.INDEXCODE), 带缓存。
    非指数基金/失败返 None。缓存并入 holdings_cache (跟踪标的基本不变)。"""
    cache = load_holdings_cache()
    entry = cache.get(code) or {}
    idx = entry.get("indexcode")
    # 过滤无效缓存值 (旧版可能缓存了 "--" 等占位符)
    if idx and idx != "--" and _cache_fresh(entry):
        return idx
    qs = urllib.parse.urlencode({
        "FCODE": code,
        "deviceid": DEVICE_ID,
        "plat": "Android",
        "appType": "ttjj",
        "product": "EFund",
        "Version": "1",
    })
    try:
        payload = json.loads(_curl_get_text(
            f"https://fundmobapi.eastmoney.com/FundMNewApi/FundMNDetailInformation?{qs}",
            {"User-Agent": "Mozilla/5.0 (Macintosh; Alfred funds-alfred)"},
        ) or "{}")
        datas = payload.get("Datas") or {}
        raw_idx = (datas.get("INDEXCODE") or "").strip()
        # 过滤无效占位符 (空 / "--" 等), 保留数字与港股字母代码(如 HSTECH)
        idx = raw_idx if (raw_idx and raw_idx != "--") else None
    except Exception:
        idx = None
    if idx is not None:
        cache[code] = {**entry, "indexcode": idx, "fetched_at": datetime.now().timestamp()}
        save_holdings_cache(cache)
        return idx
    # 远程失败回退: 仅当旧缓存为有效值时才使用
    old_idx = entry.get("indexcode")
    if old_idx and old_idx != "--":
        return old_idx
    return None


# ---------- 计算 ----------
def to_float(x, default=None):
    if x is None or x == "" or x == "--":
        return default
    try:
        return float(x)
    except (ValueError, TypeError):
        return default


def parse_fund(api_row: dict, holding: dict, expansion_gztime: Optional[str] = None, estimate: Optional[dict] = None) -> dict:
    """将 FundMNFInfo 返回 (经 _normalize_row 归一化) + 持仓信息合成一行展示用数据。

    归一化字段: fundcode, name, jzrq (净值日期), dwjz (单位净值),
      gsz (估算净值), gszzl (估算涨跌幅%), gztime (估算时间), navchgrt (净值涨跌幅%)
    数据源有三套涨跌幅来源 (优先级从高到低):
      - 净值涨跌幅 navchgrt (已结算, 当日真实)
      - 持仓自算估算 estimate (盘中, 基于重仓股+实时行情)
      - 接口盘中估算 gsz/gszzl (盘中, 接口已下线场景的兜底)
    四态: 已结算用 navchgrt; 有自算用 estimate; 有接口 gsz 用 gszzl; 否则留空显示「-」。

    estimate: estimate_gsz() 返回的 {"gsz", "rate", "cov"} 或 None。
    expansion_gztime: API 响应级 Expansion.GZTIME，当单只基金 gztime 为 null 时
    作为 fallback 用于判定是否已结算（不影响最终展示的 gztime 字段）。
    """
    code = api_row.get("fundcode")
    name = api_row.get("name", code)
    nav = to_float(api_row.get("dwjz"))
    gsz = to_float(api_row.get("gsz"))
    gszzl = to_float(api_row.get("gszzl"))
    navchgrt = to_float(api_row.get("navchgrt"))
    gztime = api_row.get("gztime")
    pdate = api_row.get("jzrq")

    num = float(holding.get("num") or 0)
    cost = to_float(holding.get("cost"))

    # 是否已结算 (今日净值已公布): 净值日 == 估值日, 与 choose-funds 一致。
    # 单只基金 GZTIME 在非交易时段为 null，此时用 API 响应级 Expansion.GZTIME 兜底判定。
    gztime_for_settled = gztime or expansion_gztime
    settled = bool(pdate and pdate != "--" and gztime_for_settled and pdate == gztime_for_settled[:10])

    est_gsz = estimate.get("gsz") if estimate else None
    est_rate = estimate.get("rate") if estimate else None

    if settled:
        # 已结算: 涨跌幅与今日收益以净值涨跌幅为准 (当日真实)
        # 今日收益 = 今日净值 - 昨日净值; 昨日净值 = nav / (1 + 涨幅/100)
        rate = navchgrt
        if nav is not None and navchgrt is not None:
            gains = (nav - nav / (1 + navchgrt / 100)) * num
        else:
            gains = None
        base_price = nav
        gsz_out = None
        est_source = "settled"
    elif est_gsz is not None:
        # 盘中持仓自算: 涨跌幅用 est_rate, 收益 = (估算净值 - 昨净值) × 份额
        rate = est_rate
        if nav is not None:
            gains = (est_gsz - nav) * num
        else:
            gains = None
        base_price = est_gsz
        gsz_out = est_gsz
        est_source = "holdings"
    elif gsz is not None:
        # 接口盘中估算(兜底): 涨跌幅用 gszzl, 收益 = (估算净值 - 昨净值) × 份额
        rate = gszzl
        if nav is not None:
            gains = (gsz - nav) * num
        else:
            gains = None
        base_price = gsz
        gsz_out = gsz
        est_source = "api"
    else:
        # 无自算/无盘中估算且未结算 (主动型基金无盘中估值 / 收盘后净值未出):
        # 不用昨日 NAVCHGRT 冒充今日, 涨跌幅与今日收益留空, 显示「-」。
        rate = None
        gains = None
        base_price = nav  # 持有额按最近净值
        gsz_out = None
        est_source = "none"

    # 持有额
    amount = (base_price * num) if (base_price is not None) else None

    # 持仓总收益 (cost 必填)
    cost_gains = None
    cost_rate = None
    if cost is not None and cost != 0 and base_price is not None:
        cost_gains = (base_price - cost) * num
        cost_rate = (base_price - cost) / cost * 100

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


# ---------- 格式化 ----------
def fmt_money(v, prefix="¥"):
    if v is None:
        return "—"
    return f"{prefix}{v:,.2f}"


def fmt_signed(v, decimals=2):
    if v is None:
        return "—"
    return f"{v:+,.{decimals}f}"


def fmt_rate(v):
    if v is None:
        return "—"
    return f"{v:+.2f}%"


def rate_emoji(rate):
    if rate is None:
        return EMOJI_FLAT
    if rate > 0:
        return EMOJI_UP
    if rate < 0:
        return EMOJI_DOWN
    return EMOJI_FLAT


# ---------- 渲染 Alfred Items ----------
def item_for_fund(f: dict) -> dict:
    if f["settled"]:
        flag = " ✓"
    elif f.get("est_source") == "holdings":
        flag = " [自算]"
    elif f["gsz"] is not None:
        flag = " [估算]"
    else:
        flag = " [无估值]"
    title = f"{rate_emoji(f['rate'])} {f['name']} · {fmt_rate(f['rate'])}{flag}"

    parts = [f"持有 {fmt_money(f['amount'])}"]
    parts.append(f"今日 {fmt_signed(f['gains'])}")
    if f["cost_gains"] is not None:
        parts.append(f"持仓 {fmt_signed(f['cost_gains'])} ({fmt_rate(f['cost_rate'])})")
    nav_part = f"净值 {f['nav']:.4f}" if f["nav"] is not None else "净值 —"
    if f["gsz"] is not None and not f["settled"]:
        nav_part += f" → {f['gsz']:.4f}"
    parts.append(nav_part)
    subtitle = "  ·  ".join(parts)

    # 按 ⏎ 时复制到剪贴板
    arg = (
        f"{f['name']} {f['code']}  "
        f"{fmt_rate(f['rate'])}  "
        f"今日 {fmt_signed(f['gains'])}  "
        f"持有 {fmt_money(f['amount'])}"
    )
    if f["cost_gains"] is not None:
        arg += f"  持仓 {fmt_signed(f['cost_gains'])} ({fmt_rate(f['cost_rate'])})"

    return {
        "title": title,
        "subtitle": subtitle,
        "arg": arg,
        "match": f"{f['name']} {f['code']}",
        "text": {"copy": arg, "largetype": arg},
        "valid": True,
    }


def item_total(funds: list, when_text: str, group_label: str = "") -> dict:
    total_amount = sum((f["amount"] or 0) for f in funds)
    total_gains = sum((f["gains"] or 0) for f in funds)
    total_cost_gains = sum(
        (f["cost_gains"] or 0) for f in funds if f["cost_gains"] is not None
    )
    have_cost = any(f["cost_gains"] is not None for f in funds)

    # 日涨跌总比例 = 今日总收益 / (总持有额 - 今日总收益)  即昨日基准
    base = total_amount - total_gains
    rate = (total_gains * 100 / base) if base else None

    # 全部基金无今日收益 (无盘中估值且未结算) 时, 不显示误导性的 0, 留空
    if funds and all(f["gains"] is None for f in funds):
        show_gains, show_rate = None, None
    else:
        show_gains, show_rate = total_gains, rate

    label = f"[{group_label}] " if group_label else ""
    title = (
        f"{rate_emoji(show_rate)} {label}持有 {fmt_money(total_amount)}  ·  "
        f"今日 {fmt_signed(show_gains)} ({fmt_rate(show_rate)})"
    )
    parts = [f"{len(funds)} 只基金"]
    if have_cost:
        parts.append(f"持仓累计 {fmt_signed(total_cost_gains)}")
    parts.append(when_text)
    subtitle = "  ·  ".join(parts)

    arg = (
        f"{label}持有 {fmt_money(total_amount)}  "
        f"今日 {fmt_signed(show_gains)} ({fmt_rate(show_rate)})"
    )
    return {
        "title": title,
        "subtitle": subtitle,
        "arg": arg,
        "text": {"copy": arg, "largetype": arg},
        "valid": True,
    }


def cmd_sum(groups: list) -> None:
    """跨所有分组合计 (fund sum)。

    展示:
      - 顶部: 全部分组合计行
      - 接着每个分组的合计行 (不展示单只基金明细)
      - 空组用占位行标注
    """
    # 收集所有持仓; code 在 (group_idx, holding) 维度跨组可能重复
    holdings_ctx = []  # list of (gi, code, holding)
    unique_codes = []
    seen = set()
    for gi, g in enumerate(groups):
        for h in g.get("funds") or []:
            if not h.get("code"):
                continue
            code = str(h["code"]).zfill(6)
            holdings_ctx.append((gi, code, h))
            if code not in seen:
                seen.add(code)
                unique_codes.append(code)

    if not holdings_ctx:
        out = {
            "items": [
                item_error(
                    "所有分组都没有基金",
                    "运行 `fund config` 编辑配置, 给某个分组的 funds 添加 {code, num, cost?}",
                )
            ]
        }
        print(json.dumps(out, ensure_ascii=False))
        return

    # 并发拉一次, 全部分组共享
    try:
        results, expansion_gztime = fetch_funds(unique_codes)
    except Exception as e:
        out = {"items": [item_error("网络请求失败", str(e))]}
        print(json.dumps(out, ensure_ascii=False))
        return

    # 持仓自算估值 (盘中未结算时替代已下线的接口 GSZ)
    estimates = build_estimates(unique_codes, results)

    # 按分组聚合 parse_fund 结果
    group_parsed = [[] for _ in groups]
    latest_gztime = None
    has_self = False   # 是否有持仓自算 (决定合计行标「自算」)
    has_api = False    # 是否有接口盘中估算 (兜底)
    for gi, code, h in holdings_ctx:
        r = results.get(code)
        if not r:
            continue
        f = parse_fund(r, h, expansion_gztime, estimate=estimates.get(code))
        group_parsed[gi].append(f)
        gt = f.get("gztime") or f.get("pdate")
        if gt and (latest_gztime is None or gt > latest_gztime):
            latest_gztime = gt
        if not f["settled"]:
            if f.get("est_source") == "holdings":
                has_self = True
            elif f.get("est_source") == "api":
                has_api = True

    if latest_gztime:
        if has_self:
            # 自算基于今日盘中实时行情, 时间用当前; latest_gztime 仅是净值基准日(昨日)
            prefix = "自算"
            ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        elif has_api:
            prefix = "估算"
            ts = latest_gztime
        else:
            prefix = "净值"
            ts = latest_gztime
        when = f"{prefix} @ {ts}"
    else:
        when = datetime.now().strftime("更新 @ %H:%M")

    items = []
    # 顶部: 全部分组总合计
    all_parsed = [f for fs in group_parsed for f in fs]
    if all_parsed:
        items.append(item_total(all_parsed, when, "全部分组"))

    # 每个分组的合计行 (无明细基金)
    for gi, g in enumerate(groups):
        funds = group_parsed[gi]
        configured = len(g.get("funds") or [])
        if funds:
            items.append(item_total(funds, when, g["name"]))
        else:
            items.append(
                {
                    "title": f"➖ [{g['name']}] 无基金可估算",
                    "subtitle": f"配置 {configured} 只, 0 只取到估值",
                    "valid": False,
                }
            )

    print(json.dumps({"items": items}, ensure_ascii=False))


def item_open_config(reason: str = "") -> dict:
    path = get_config_path()
    subtitle = reason or "回车在默认文本编辑器中打开"
    return {
        "title": "⚙️  编辑 funds.json",
        "subtitle": f"{subtitle}  ·  {path}",
        "arg": path,
        "variables": {"action": "open_config"},
        "valid": True,
    }


def item_error(title: str, subtitle: str) -> dict:
    return {
        "title": f"❌ {title}",
        "subtitle": subtitle,
        "arg": "",
        "valid": False,
    }


# ---------- 主流程 ----------
def main():
    query = (sys.argv[1] if len(sys.argv) > 1 else "").strip().lower()

    # 子命令: `fund config` 直接打开配置
    if query in ("config", "conf", "edit", "设置", "配置"):
        out = {"items": [item_open_config("打开配置文件以增删基金")]}
        print(json.dumps(out, ensure_ascii=False))
        return

    # 子命令: `fund refresh` 清空持仓缓存, 下次查询重新拉取重仓股
    if query in ("refresh", "刷新", "更新持仓", "清缓存"):
        path = get_holdings_cache_path()
        removed = os.path.exists(path)
        if removed:
            os.remove(path)
        out = {
            "items": [{
                "title": "♻️ 持仓缓存已清空" if removed else "♻️ 无缓存可清",
                "subtitle": "下次查询会重新拉取重仓股持仓（季报数据）",
                "arg": "",
                "valid": True,
            }]
        }
        print(json.dumps(out, ensure_ascii=False))
        return

    # 1. 读配置
    try:
        cfg = load_config()
    except Exception as e:
        out = {
            "items": [
                item_error("配置文件解析失败", f"{e} — {get_config_path()}"),
                item_open_config("修复 JSON 语法后再试"),
            ]
        }
        print(json.dumps(out, ensure_ascii=False))
        return

    groups = normalize_groups(cfg)
    if not groups:
        out = {
            "items": [
                item_error("尚未配置任何基金", "在配置文件中添加 groups 或 funds 后再试"),
                item_open_config(),
            ]
        }
        print(json.dumps(out, ensure_ascii=False))
        return

    # 子命令: `fund sum` 跨所有分组合计
    if query in ("sum", "汇总", "总计", "合计", "all"):
        cmd_sum(groups)
        return

    # 解析查询里的分组序号: query 第一个 token 若是数字 -> 切到对应组 (1-based)
    selected_idx = 0  # 默认第 1 组
    out_of_range_msg = ""
    if query:
        first = query.split()[0]
        if first.isdigit():
            n = int(first)
            if 1 <= n <= len(groups):
                selected_idx = n - 1
            else:
                out_of_range_msg = (
                    f"分组序号 {n} 越界 (共 {len(groups)} 组), 已显示第 1 组"
                )

    cur_group = groups[selected_idx]
    holdings = cur_group["funds"]
    group_label = cur_group["name"]

    if not holdings:
        items = [
            item_error(
                f"分组「{cur_group['name']}」尚未配置任何基金",
                "运行 `fund config` 编辑配置, 给该组的 funds 添加 {code, num, cost?}",
            ),
        ]
        print(json.dumps({"items": items}, ensure_ascii=False))
        return

    codes = [str(h.get("code")).zfill(6) for h in holdings if h.get("code")]
    holdings_by_code = {
        str(h["code"]).zfill(6): h for h in holdings if h.get("code")
    }

    # 2. 并发拉取
    try:
        results, expansion_gztime = fetch_funds(codes)
    except Exception as e:
        out = {"items": [item_error("网络请求失败", str(e))]}
        print(json.dumps(out, ensure_ascii=False))
        return

    # 持仓自算估值 (盘中未结算时替代已下线的接口 GSZ)
    estimates = build_estimates(codes, results)

    # 3. 整理 + 排序: 涨跌幅降序 (涨得多的在前)
    parsed = []
    missing = []
    for code in codes:
        r = results.get(code)
        if not r:
            missing.append(code)
            continue
        parsed.append(parse_fund(r, holdings_by_code[code], expansion_gztime, estimate=estimates.get(code)))
    parsed.sort(key=lambda f: (f["rate"] if f["rate"] is not None else -999), reverse=True)

    # 4. 拼参考时间 (用最新的 gztime)
    when = ""
    latest_gztime = None
    for f in parsed:
        gt = f.get("gztime") or f.get("pdate")
        if gt and (latest_gztime is None or gt > latest_gztime):
            latest_gztime = gt
    if latest_gztime:
        has_self = any((not f["settled"] and f.get("est_source") == "holdings") for f in parsed)
        has_api = any((not f["settled"] and f.get("est_source") == "api") for f in parsed)
        if has_self:
            # 自算基于今日盘中实时行情, 时间用当前; latest_gztime 仅是净值基准日(昨日)
            prefix = "自算"
            ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        elif has_api:
            prefix = "估算"
            ts = latest_gztime
        else:
            prefix = "净值"
            ts = latest_gztime
        when = f"{prefix} @ {ts}"
    else:
        when = datetime.now().strftime("更新 @ %H:%M")

    items = []
    if out_of_range_msg:
        items.append(item_error("分组序号越界", out_of_range_msg))
    if parsed:
        items.append(item_total(parsed, when, group_label))
    items.extend(item_for_fund(f) for f in parsed)

    for code in missing:
        items.append(item_error(f"{code} 未找到", "请检查基金代码是否正确"))

    out = {"items": items}
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(
            json.dumps(
                {"items": [item_error("脚本异常", str(e))]}, ensure_ascii=False
            )
        )
