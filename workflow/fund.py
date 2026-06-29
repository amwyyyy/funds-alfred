#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Alfred Script Filter: 查看自选基金当前情况
数据源: 天天基金实时估算端点 fundgz.1234567.com.cn (与原 x2rr/funds v1.x 一致)

为什么不用 fundmobapi.eastmoney.com/FundMNewApi/FundMNFInfo?
  实测该端点在交易时段也会返回 GSZ=null (拒绝给非 App 客户端实时估值)。
  反而 fundgz 老端点公开、稳定、无鉴权、CORS *, 一直能拿到实时估值。
"""

import json
import os
import re
import ssl
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

# ---------- 常量 ----------
API_TPL = "https://fundgz.1234567.com.cn/js/{code}.js"
TIMEOUT = 6  # seconds per request
CONCURRENCY = 8  # 最多并发数

# 中式红涨绿跌
EMOJI_UP = "📈"
EMOJI_DOWN = "📉"
EMOJI_FLAT = "➖"

# 匹配 jsonpgz({...}) 包装
JSONP_RE = re.compile(r"^\s*jsonpgz\((.*)\)\s*;?\s*$", re.S)


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
_SSL_CTX = ssl.create_default_context()


def fetch_one(code):
    """调用 fundgz 拉单只基金。返回解析后的字典，未找到返回 None。"""
    url = API_TPL.format(code=code)
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Alfred funds-alfred)",
            "Accept": "*/*",
        },
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT, context=_SSL_CTX) as resp:
        body = resp.read().decode("utf-8", errors="replace")
    m = JSONP_RE.match(body)
    if not m:
        return None
    inner = m.group(1).strip()
    if not inner:  # jsonpgz(); 表示基金不存在
        return None
    return json.loads(inner)


def fetch_funds(codes):
    """并发拉取多只基金。返回 {code: parsed_dict_or_None}。"""
    out = {}
    if not codes:
        return out
    with ThreadPoolExecutor(max_workers=min(CONCURRENCY, len(codes))) as ex:
        future_map = {ex.submit(fetch_one, c): c for c in codes}
        for fut in as_completed(future_map):
            code = future_map[fut]
            try:
                out[code] = fut.result()
            except Exception as e:
                out[code] = {"__error__": str(e)}
    return out


# ---------- 计算 ----------
def to_float(x, default=None):
    if x is None or x == "" or x == "--":
        return default
    try:
        return float(x)
    except (ValueError, TypeError):
        return default


def parse_fund(api_row: dict, holding: dict) -> dict:
    """将 fundgz 返回 + 用户持仓信息合成一行展示用数据。

    fundgz 返回字段:
      fundcode, name, jzrq (净值日期), dwjz (单位净值),
      gsz (估算净值), gszzl (估算涨跌幅%), gztime (估算时间 'YYYY-mm-dd HH:MM')
    """
    code = api_row.get("fundcode")
    name = api_row.get("name", code)
    nav = to_float(api_row.get("dwjz"))
    gsz = to_float(api_row.get("gsz"))
    gszzl = to_float(api_row.get("gszzl"))
    gztime = api_row.get("gztime")
    pdate = api_row.get("jzrq")

    num = float(holding.get("num") or 0)
    cost = to_float(holding.get("cost"))

    # 判断是否已结算: 净值日期 == 估算时间的日期
    # 已结算后 fundgz 的 gsz 通常等于 dwjz, gszzl 就是当日真实涨跌幅
    settled = False
    if pdate and pdate != "--" and gztime and pdate == gztime[:10]:
        settled = True

    rate = gszzl  # 直接用估算涨跌幅 (已结算后它就是真实涨跌幅)

    # 今日估算收益
    if gsz is not None and nav is not None:
        gains = (gsz - nav) * num
    else:
        gains = None

    # 持有额: 优先用 gsz (实时), 退化到 dwjz
    base_price = gsz if gsz is not None else nav
    amount = (base_price * num) if (base_price is not None) else None

    # 持仓总收益 (cost 必填); 用 gsz 计算更贴近实时
    cost_gains = None
    cost_rate = None
    if cost is not None and cost != 0 and base_price is not None:
        cost_gains = (base_price - cost) * num
        cost_rate = (base_price - cost) / cost * 100

    return {
        "code": code,
        "name": name,
        "nav": nav,
        "gsz": gsz,
        "rate": rate,
        "settled": settled,
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
    flag = " ✓" if f["settled"] else (" [估算]" if f["rate"] is not None else "")
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
        "uid": f"fund-{f['code']}",
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

    label = f"[{group_label}] " if group_label else ""
    title = (
        f"{rate_emoji(rate)} {label}持有 {fmt_money(total_amount)}  ·  "
        f"今日 {fmt_signed(total_gains)} ({fmt_rate(rate)})"
    )
    parts = [f"{len(funds)} 只基金"]
    if have_cost:
        parts.append(f"持仓累计 {fmt_signed(total_cost_gains)}")
    parts.append(when_text)
    subtitle = "  ·  ".join(parts)

    arg = (
        f"{label}持有 {fmt_money(total_amount)}  "
        f"今日 {fmt_signed(total_gains)} ({fmt_rate(rate)})"
    )
    return {
        "uid": "fund-total",
        "title": title,
        "subtitle": subtitle,
        "arg": arg,
        "text": {"copy": arg, "largetype": arg},
        "valid": True,
    }


def item_open_config(reason: str = "") -> dict:
    path = get_config_path()
    subtitle = reason or "回车在默认文本编辑器中打开"
    return {
        "uid": "fund-config",
        "title": "⚙️  编辑 funds.json",
        "subtitle": f"{subtitle}  ·  {path}",
        "arg": path,
        "variables": {"action": "open_config"},
        "valid": True,
    }


def item_error(title: str, subtitle: str) -> dict:
    return {
        "uid": "fund-error",
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
                "在配置文件中给该组的 funds 数组添加 {code, num, cost?}",
            ),
        ]
        items.append(item_open_config())
        print(json.dumps({"items": items}, ensure_ascii=False))
        return

    codes = [str(h.get("code")).zfill(6) for h in holdings if h.get("code")]
    holdings_by_code = {
        str(h["code"]).zfill(6): h for h in holdings if h.get("code")
    }

    # 2. 并发拉取
    try:
        results = fetch_funds(codes)
    except Exception as e:
        out = {"items": [item_error("网络请求失败", str(e))]}
        print(json.dumps(out, ensure_ascii=False))
        return

    # 3. 整理 + 排序: 涨跌幅降序 (涨得多的在前)
    parsed = []
    missing = []
    errors = []  # [(code, msg)]
    for code in codes:
        r = results.get(code)
        if r is None:
            missing.append(code)
            continue
        if isinstance(r, dict) and r.get("__error__"):
            errors.append((code, r["__error__"]))
            continue
        parsed.append(parse_fund(r, holdings_by_code[code]))
    parsed.sort(key=lambda f: (f["rate"] if f["rate"] is not None else -999), reverse=True)

    # 4. 拼参考时间 (用最新的 gztime)
    when = ""
    latest_gztime = None
    for f in parsed:
        gt = f.get("gztime")
        if gt and (latest_gztime is None or gt > latest_gztime):
            latest_gztime = gt
    if latest_gztime:
        # 看看是不是已结算
        any_settled = any(f["settled"] for f in parsed)
        prefix = "净值" if any_settled and all(f["settled"] for f in parsed) else "估算"
        when = f"{prefix} @ {latest_gztime}"
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
    for code, msg in errors:
        items.append(item_error(f"{code} 请求失败", msg))

    items.append(item_open_config())

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
