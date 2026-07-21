#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Alfred Script Filter: 查看自选基金当前情况
数据源: 天天基金 FundValuationLast 批量估值接口
  主域: https://fundcomapi.tiantianfunds.com/mm/newCore/FundValuationLast
  备用: https://fundcomapi.eastmoney.com/mm/newCore/FundValuationLast

2026-07-21 起 fundgz.1234567.com.cn JSONP 端点 301 下线, 改用天天基金 H5
FundComApi.getValuationLast 对应接口, 支持 FCODES 批量请求。
部分主动管理型基金 GSZ/GSZZL/GZTIME 为 null (数据侧不再提供盘中估值),
保留名称与正式净值并标注"无盘中估值"。
"""

import json
import os
import ssl
import sys
import urllib.parse
import urllib.request
from datetime import datetime

# ---------- 常量 ----------
# 估值接口: 天天基金 FundValuationLast (批量, 主域失败回退备用域)
API_HOSTS = (
    "https://fundcomapi.tiantianfunds.com",
    "https://fundcomapi.eastmoney.com",
)
API_PATH = "/mm/newCore/FundValuationLast"
API_FIELDS = "FCODE,SHORTNAME,GSZZL,GZTIME,GSZ,NAV,PDATE"
TIMEOUT = 6  # seconds per request

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
_SSL_CTX = ssl.create_default_context()


def _normalize_row(row: dict) -> dict:
    """把 FundValuationLast 字段名归一化为旧 fundgz 字段名, 复用下游 parse_fund。"""
    return {
        "fundcode": row.get("FCODE"),
        "name": row.get("SHORTNAME"),
        "dwjz": row.get("NAV"),
        "jzrq": row.get("PDATE"),
        "gsz": row.get("GSZ"),
        "gszzl": row.get("GSZZL"),
        "gztime": row.get("GZTIME"),
    }


def fetch_funds(codes):
    """批量拉取基金估值。返回 {code: normalized_row_or_None}。

    单次请求拿回全部 codes; 不存在的 code 不会出现在响应 data 中 -> None (missing)。
    主域失败自动回退备用域; 全部失败抛异常。
    """
    out = {c: None for c in codes}
    if not codes:
        return out
    qs = urllib.parse.urlencode({"FCODES": ",".join(codes), "FIELDS": API_FIELDS})
    last_err = None
    for host in API_HOSTS:
        url = f"{host}{API_PATH}?{qs}"
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Alfred funds-alfred)",
                "Accept": "*/*",
                "Referer": "https://fund.eastmoney.com/",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT, context=_SSL_CTX) as resp:
                payload = json.loads(resp.read().decode("utf-8", errors="replace"))
        except Exception as e:
            last_err = e
            continue
        for row in payload.get("data") or []:
            c = row.get("FCODE")
            if c:
                out[c] = _normalize_row(row)
        return out
    raise last_err or RuntimeError("估值接口均不可用")


# ---------- 计算 ----------
def to_float(x, default=None):
    if x is None or x == "" or x == "--":
        return default
    try:
        return float(x)
    except (ValueError, TypeError):
        return default


def parse_fund(api_row: dict, holding: dict) -> dict:
    """将估值接口返回 (经 _normalize_row 归一化) + 用户持仓信息合成一行展示用数据。

    归一化字段: fundcode, name, jzrq (净值日期), dwjz (单位净值),
      gsz (估算净值), gszzl (估算涨跌幅%), gztime (估算时间 'YYYY-mm-dd HH:MM')
    gsz/gszzl/gztime 可能为 None (主动管理型基金无盘中估值)。
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
    if f["settled"]:
        flag = " ✓"
    elif f["rate"] is not None:
        flag = " [估算]"
    else:
        flag = " [无盘中估值]"
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


def item_total(
    funds: list, when_text: str, group_label: str = "", uid: str = "fund-total"
) -> dict:
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
        "uid": uid,
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
        results = fetch_funds(unique_codes)
    except Exception as e:
        out = {"items": [item_error("网络请求失败", str(e))]}
        print(json.dumps(out, ensure_ascii=False))
        return

    # 按分组聚合 parse_fund 结果
    group_parsed = [[] for _ in groups]
    latest_gztime = None
    any_settled = False
    all_settled = True
    for gi, code, h in holdings_ctx:
        r = results.get(code)
        if not r:
            all_settled = False  # 缺失基金视为未结算, 影响 prefix
            continue
        f = parse_fund(r, h)
        group_parsed[gi].append(f)
        gt = f.get("gztime")
        if gt and (latest_gztime is None or gt > latest_gztime):
            latest_gztime = gt
        if f["settled"]:
            any_settled = True
        else:
            all_settled = False

    if latest_gztime:
        prefix = "净值" if any_settled and all_settled else "估算"
        when = f"{prefix} @ {latest_gztime}"
    else:
        when = datetime.now().strftime("更新 @ %H:%M")

    items = []
    # 顶部: 全部分组总合计
    all_parsed = [f for fs in group_parsed for f in fs]
    if all_parsed:
        items.append(item_total(all_parsed, when, "全部分组", uid="sum-all"))

    # 每个分组的合计行 (无明细基金)
    for gi, g in enumerate(groups):
        funds = group_parsed[gi]
        configured = len(g.get("funds") or [])
        if funds:
            items.append(item_total(funds, when, g["name"], uid=f"sum-group-{gi}"))
        else:
            items.append(
                {
                    "uid": f"sum-group-{gi}",
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
        results = fetch_funds(codes)
    except Exception as e:
        out = {"items": [item_error("网络请求失败", str(e))]}
        print(json.dumps(out, ensure_ascii=False))
        return

    # 3. 整理 + 排序: 涨跌幅降序 (涨得多的在前)
    parsed = []
    missing = []
    for code in codes:
        r = results.get(code)
        if not r:
            missing.append(code)
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
