# 计划：funds-alfred Workflow

> 这是项目立项时的原始实现计划，记录于 2026-06-26。
> 实际实现以本仓库代码为准；如代码与本文档不一致，以代码为准。

## 一、技术选型

| 项 | 选择 | 理由 |
|---|---|---|
| 运行时 | **Python 3** 系统自带 (`/usr/bin/python3`) | 无需任何依赖（`urllib` + `json` 即可），换台 mac 直接能跑 |
| Alfred 版本 | Alfred 5 (info.plist v5 格式) | 你装的是 5.7.3 |
| HTTP 库 | `urllib.request` (stdlib) | 不引入 requests/axios |
| 配置位置 | `${alfred_workflow_data}/funds.json` | Alfred 官方推荐路径，workflow 自带的环境变量 |
| 项目位置 | `~/IdeaProjects/demo/funds-alfred/` | 与参考项目并列 |

## 二、目录结构

```
~/IdeaProjects/demo/funds-alfred/
├── workflow/                  # ← Alfred workflow 源（这个目录可以直接拖进 Alfred）
│   ├── info.plist             # workflow 定义：1 个 Script Filter + 1 个 Open File action
│   ├── fund.py                # 主脚本：拉数据 → 渲染列表
│   ├── fund_config.py         # 打开 funds.json 让你编辑
│   └── icon.png               # 简单图标（占位）
├── build.sh                   # 把 workflow/ 打包成 funds-alfred.alfredworkflow
└── README.md                  # 配置 JSON 示例 + 使用说明
```

## 三、配置文件格式 `funds.json`

```json
{
  "funds": [
    { "code": "161725", "num": 10000, "cost": 0.5162 },
    { "code": "001618", "num": 500,   "cost": 3.10 },
    { "code": "110022", "num": 200 }
  ]
}
```

- `code` 必填（基金代码）
- `num` 必填（持有份额）
- `cost` 可选（成本价，没填就不显示持仓总收益那一栏）
- 首次运行若文件不存在，脚本会自动创建一个示例文件并提示

## 四、Alfred 工作流节点

| Keyword | 类型 | 行为 |
|---|---|---|
| `fund` | Script Filter | 拉所有基金 → 显示列表；第一行是合计 |
| `fund config` | （`fund` 列表中的特殊项）Open File | `open -t funds.json` 打开默认文本编辑器 |

## 五、列表展示（每只基金一行）

```
标题:    招商中证白酒指数(LOF)A · -1.23% [估算]      ← name · 涨跌幅 + 标记是否已结算
副标题:  持有 ¥5,162.00  ·  今日 -50.12  ·  持仓 +320.45 (+6.62%)
                 ↑持有额          ↑估算收益          ↑持仓总收益（有 cost 才显示）
按 ⏎:    复制 "招商中证白酒 -1.23%  今日-50.12  持仓+320.45" 到剪贴板
```

合计行（置顶）：

```
标题:    合计 持有 ¥18,432.50  ·  今日 -180.30 (-0.98%)
副标题:  4 只基金 · 更新于 2026-06-26 14:30
```

颜色/中式涨跌习惯（红涨绿跌）通过 emoji 体现：📈 红色上涨 / 📉 绿色下跌。

## 六、API 调用细节

> ⚠️ **实施过程中调整**：原计划使用东方财富批量端点 `fundmobapi.eastmoney.com/FundMNewApi/FundMNFInfo`
> （与 `x2rr/funds` v2.0+ 一致），但实测在交易时段也会返回 `GSZ: null`，
> 无法获取实时估值。最终改用老版 JSONP 端点 `fundgz.1234567.com.cn/js/<code>.js`
> （即 `x2rr/funds` v1.x 时期使用的端点），稳定返回实时估值。
> 多只基金用 `ThreadPoolExecutor` 并发拉取。

```
GET https://fundgz.1234567.com.cn/js/<基金代码>.js
```

返回示例：

```js
jsonpgz({"fundcode":"161725","name":"招商中证白酒指数(LOF)A",
        "jzrq":"2026-06-25","dwjz":"0.5162",
        "gsz":"0.5077","gszzl":"-1.65","gztime":"2026-06-26 11:21"});
```

> 📌 **2026-07-21 更新**：上述 `fundgz` JSONP 端点当日 301 下线（跳转 `fund.eastmoney.com/notfound.html`），导致全部基金「暂无数据」。已切换至天天基金 H5 `FundComApi.getValuationLast` 对应接口 `fundcomapi.tiantianfunds.com/mm/newCore/FundValuationLast`（备用域 `fundcomapi.eastmoney.com`），单次 `FCODES` 批量请求替代 `ThreadPoolExecutor` 并发。新接口对部分主动管理型基金返回 `GSZ=null`（不再提供盘中估值），对此类基金保留名称与正式净值并标注「无盘中估值」。详见 `workflow/fund.py`。

> 📌 **2026-07-22 二次调整**：`FundValuationLast` 实测有两个问题——结算后仍返回残留的盘中估算（不准），且对主动管理型基金 `GSZZL` 也为 null 只能显示 `-`。参考 choose-funds（LiuRabt）扩展改用 `fundmobapi.eastmoney.com/FundMNewApi/FundMNFInfo`（`plat=Android&appType=ttjj&product=EFund&Version=1`，`Fcodes` 批量），引入 `NAVCHGRT`（净值涨跌幅）字段兜底：`GSZ` 缺失即视为已结算，涨跌幅与收益改用 `NAVCHGRT`（收益公式 `(dwjz − dwjz/(1+NAVCHGRT/100)) × num`）；`GSZ` 有值时仍走盘中估算。

> 📌 **2026-07-29 三次调整**：`FundMNFInfo` 对多数基金也不再返回 `GSZ`（盘中实时估值彻底无接口来源）。新增「持仓自算估值」：盘中未结算时，用基金最新季报前十大重仓股 + 占净值比 + 重仓股实时行情加权估算净值（口径 B：按重仓覆盖率缩放到满仓），标注 `[自算]`。重仓覆盖率不足时（如 ETF 联接基金仅 0.3%）回退用 `FundMNDetailInformation.INDEXCODE` 跟踪指数实时涨跌估算。持仓缓存 7 天，`fund refresh` 强制刷新。降级链：持仓自算 -> 跟踪指数自算 -> 接口 GSZ -> `NAVCHGRT` -> `[无估值]`。详见 `docs/superpowers/specs/2026-07-29-holdings-based-fund-valuation-design.md`。

字段映射：
- `dwjz` → 单位净值（昨日结算）
- `gsz` / `gszzl` → 估算净值 / 估算涨跌幅
- `gztime` → 估算时间
- `jzrq` → 净值日期；当 `jzrq == gztime[:10]` 时认为已结算
- 持有额 = `gsz × num`（实时；闭市退化为 `dwjz × num`）
- 估算收益 = `(gsz − dwjz) × num`
- 持仓收益 = `(gsz − cost) × num`（仅当 `cost` 存在时）

## 七、错误处理

- 网络失败 → Alfred 列表显示一条红色错误项，副标题给出具体原因
- JSON 解析失败 → 给出 `funds.json` 路径并提示打开
- 单只基金 `Datas` 缺失 → 该行显示「未找到」但其他基金正常展示

## 八、打包

`build.sh` 用 `zip -r funds-alfred.alfredworkflow workflow/` 生成可直接双击安装的 `.alfredworkflow` 文件。

## 九、交付物

1. 完整的源码目录 `~/IdeaProjects/demo/funds-alfred/`
2. 打包好的 `funds-alfred.alfredworkflow`（双击即可安装）
3. README 包含：安装步骤、配置示例、关键词说明、常见问题（市场闭市时 GSZ 为 null 等）
