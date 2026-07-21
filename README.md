# Funds Alfred

一个 [Alfred 5](https://www.alfredapp.com/) 工作流，用于在 Alfred 中快速查看自选基金的当前情况：**持有额、涨跌幅、估算收益、持仓总收益**。

灵感来源于 Chrome 插件 [`x2rr/funds`](https://github.com/x2rr/funds)（自选基金助手），数据源同样为东方财富（天天基金）公开 API，无需登录。

![preview](./preview.jpg)

## 特性

- ✅ 单文件 Python 脚本，**零依赖**（仅用 stdlib），系统 Python 3 即可运行
- ✅ 一次请求获取全部基金数据（东方财富批量接口）
- ✅ **基金分组**：把自选基金拆成多个组（如「核心持仓 / 卫星仓 / 打新仓」），`fund N` 快速切换
- ✅ 列表头部显示**合计**（持有总额、今日估算总收益、总涨跌幅）并标注当前分组名
- ✅ 每只基金显示：涨跌幅、持有额、今日估算收益、持仓总收益（需配置成本价）
- ✅ 红涨绿跌（中国市场习惯，emoji 📈 📉 ➖）
- ✅ 闭市/未估算时优雅降级显示「—」
- ✅ 当日净值已结算时自动用真实净值替换估算值，并标注 ✓
- ✅ 错误处理：网络失败、JSON 损坏、基金代码不存在等都有友好提示

## 安装

### 方式 1：双击安装

下载 `funds-alfred.alfredworkflow`，双击即可。

### 方式 2：从源码构建

```bash
git clone <this-repo>
cd funds-alfred
./build.sh         # 生成 funds-alfred.alfredworkflow
open funds-alfred.alfredworkflow
```

## 使用

| 关键词 | 行为 |
|---|---|
| `fund` | 查看自选基金列表（默认第 1 个分组） |
| `fund 2` | 切换到第 2 个分组（数字 = 分组序号，从 1 开始） |
| `fund sum` | 跨所有分组合计（仅显示总合计 + 各分组合计，不展示单只基金） |
| `fund config` | 打开配置文件 `funds.json`（默认列表中不会展示编辑入口） |

在列表中：
- **回车 ⏎**：在普通基金行/合计行上把概要复制到剪贴板
- **⌘+L**：以 large type 大字号显示当前行

## 分组

把基金按用途/账户/风格分成多组，一组一组地看；切换轻量，零额外网络开销（每次只拉取当前组里的基金）。

### 切换分组

- `fund`：默认显示**第 1 个**分组
- `fund 2`：切到**第 2 个**分组
- `fund N`：切到第 N 个分组（N 从 1 开始计数）
- 越界（如只有 2 组却输入 `fund 9`）会显示提示并回落到第 1 组
- 当前是哪一组，看合计行最前面的 `[分组名]` 即可

合计行示例：

```
📈 [核心持仓] 持有 ¥12,345.67  ·  今日 +56.78 (+0.46%)
📈 招商中证白酒指数(LOF)A · +1.23% [估算]
...
```

### 配置示例

在 `funds.json` 顶层使用 `groups` 数组，每个组有 `name` 与该组的 `funds` 列表：

```json
{
  "groups": [
    {
      "name": "核心持仓",
      "funds": [
        { "code": "161725", "num": 10000, "cost": 0.5162 },
        { "code": "110022", "num": 200,   "cost": 2.50 }
      ]
    },
    {
      "name": "卫星仓",
      "funds": [
        { "code": "001618", "num": 500 }
      ]
    }
  ]
}
```

### 向后兼容

如果你已经在用旧版的「顶层 `funds`」配置（不含 `groups`），无需修改 —— 脚本会自动把它当成一个名为「默认」的分组使用：

```json
{ "funds": [ { "code": "161725", "num": 10000, "cost": 0.5162 } ] }
```

### 边界情况

| 情况 | 行为 |
|---|---|
| `fund N` 序号越界 | 提示「分组序号 N 越界」，回落到第 1 组继续显示 |
| 该组 `funds: []` 为空 | 提示「分组「xxx」尚未配置任何基金」（运行 `fund config` 去补充） |
| 同时存在 `groups` 与 `funds` | 优先使用 `groups`，顶层 `funds` 被忽略 |
| 分组项缺 `name` | 自动命名为「分组 N」 |

### 跨分组合计

`fund sum` 把所有分组的持有/今日收益/持仓总收益**合并求和**，并按组列出每个分组的小计，**不展示单只基金明细**。一次网络请求（跨组同代码自动去重），适合快速看全局：

```
📈 [全部分组] 持有 ¥123,456.78  ·  今日 +678.90 (+0.55%)
📈 [核心持仓] 持有 ¥98,765.43  ·  今日 +543.21 (+0.55%)
📈 [卫星仓]   持有 ¥24,691.35  ·  今日 +135.69 (+0.55%)
➖ [打新仓]   无基金可估算
```

别名：`fund sum` / `fund 汇总` / `fund 总计` / `fund 合计` / `fund all`。

## 配置文件

首次运行 `fund` 会在以下位置生成示例配置：

```
~/Library/Application Support/Alfred/Workflow Data/com.denis.funds-alfred/funds.json
```

新格式（推荐，支持分组）：

```json
{
  "groups": [
    {
      "name": "核心持仓",
      "funds": [
        { "code": "161725", "num": 10000, "cost": 0.5162 },
        { "code": "110022", "num": 200, "cost": 2.50 }
      ]
    },
    {
      "name": "卫星仓",
      "funds": [
        { "code": "001618", "num": 500 }
      ]
    }
  ]
}
```

旧格式（兼容，会被当作单个名为「默认」的分组）：

```json
{
  "funds": [
    { "code": "161725", "num": 10000, "cost": 0.5162 },
    { "code": "001618", "num": 500 },
    { "code": "110022", "num": 200, "cost": 2.50 }
  ]
}
```

字段说明：

| 字段 | 必填 | 说明 |
|---|---|---|
| `groups[*].name` | ✅ | 分组名（显示在合计行最前面的 `[分组名]` 里；缺省则自动命名为「分组 N」） |
| `groups[*].funds` | ✅ | 该分组下的基金列表 |
| `code` | ✅ | 6 位基金代码（字符串；如 `"001618"`，前导零不会丢） |
| `num` | ✅ | 持有份额 |
| `cost` | ❌ | 成本价；填写后才会显示「持仓总收益 / 持仓收益率」 |

## 数据接口

调用天天基金 `FundValuationLast` 批量估值接口（主域失败自动回退备用域）：

```
GET https://fundcomapi.tiantianfunds.com/mm/newCore/FundValuationLast
GET https://fundcomapi.eastmoney.com/mm/newCore/FundValuationLast   # 备用
    ?FCODES=<基金代码1>,<基金代码2>,...
    &FIELDS=FCODE,SHORTNAME,GSZZL,GZTIME,GSZ,NAV,PDATE
```

返回示例（纯 JSON，非 JSONP）：

```json
{"data":[
  {"FCODE":"161725","SHORTNAME":"招商中证白酒指数(LOF)A",
   "PDATE":"2026-07-20","NAV":0.5582,
   "GSZ":0.5478,"GSZZL":-1.86,"GZTIME":"2026-07-21 14:26"},
  {"FCODE":"000001","SHORTNAME":"华夏成长混合",
   "PDATE":"2026-07-20","NAV":1.306,
   "GSZ":null,"GSZZL":null,"GZTIME":null}
]}
```

- 无需 Cookie / Token / 鉴权
- 单次请求批量拉取整个分组（`FCODES` 逗号分隔），比逐只并发更快
- 字段映射：`FCODE→fundcode`、`SHORTNAME→name`、`GSZ→gsz`、`GSZZL→gszzl`、`GZTIME→gztime`、`NAV→dwjz`、`PDATE→jzrq`
- 不存在的基金代码不会出现在 `data` 中，脚本识别为「未找到」
- **部分主动管理型基金** `GSZ/GSZZL/GZTIME` 为 `null`（数据侧不再提供盘中估值），脚本保留名称与正式净值，标注「无盘中估值」，今日估算显示 `-`

> 历史：2026-07-21 前使用老版 JSONP 端点 `fundgz.1234567.com.cn/js/<code>.js`，该端点当日 301 下线（跳转 notfound 页），故切换至上述接口。

## 计算逻辑

与参考项目（`x2rr/funds`）保持一致：

| 指标 | 公式 |
|---|---|
| 持有额 | `gsz × num`（实时；闭市或无盘中估值时退化为 `dwjz × num`） |
| 涨跌幅 | `gszzl`（已结算 ✓ 时即为当日真实涨跌幅） |
| 今日估算收益 | `(gsz − dwjz) × num` |
| 持仓总收益 | `(gsz − cost) × num`（仅当 `cost` 存在时；闭市退化为 dwjz） |
| 持仓收益率 | `(gsz − cost) / cost × 100%` |

## 常见问题

**Q: 闭市时所有「今日估算」都显示 —？**
A: 分情况。本 workflow 调用的 `FundValuationLast` 接口对**有盘中估值的基金**（大多数指数型 / ETF 联接），即便闭市也返回当日最后一次估值（`gsz`/`gszzl`/`gztime`），全天可见；但对**部分主动管理型基金**，数据侧已不再提供盘中估值（`GSZ=null`），这类基金会标注「无盘中估值」，今日估算显示 `-`，仍保留名称与正式净值。开盘前凌晨或基金当天暂停估值时同样显示 `-`。

> 2026-07-21 前使用的 `fundgz.1234567.com.cn` 端点几乎对所有基金都返回估值；切换到新接口后主动管理型基金不再有盘中估值，这是数据源差异，并非 bug。

**Q: 基金代码以 0 开头怎么办？**
A: JSON 中**必须用字符串**，例如 `"code": "001618"`，否则 JSON 解析后会变成数字 `1618` 导致请求失败。脚本会自动补零到 6 位，但仍建议你直接写成字符串。

**Q: 如何卸载？**
A: 在 Alfred Preferences → Workflows 中右键 → Remove。配置文件在 `~/Library/Application Support/Alfred/Workflow Data/com.denis.funds-alfred/` 下，可手动删除。

## 致谢

- 数据源：[东方财富网 / 天天基金](https://fund.eastmoney.com/)
- 灵感来源：[x2rr/funds](https://github.com/x2rr/funds)（GPL-3.0）

## License

MIT
