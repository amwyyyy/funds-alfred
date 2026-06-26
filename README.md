# Funds Alfred

一个 [Alfred 5](https://www.alfredapp.com/) 工作流，用于在 Alfred 中快速查看自选基金的当前情况：**持有额、涨跌幅、估算收益、持仓总收益**。

灵感来源于 Chrome 插件 [`x2rr/funds`](https://github.com/x2rr/funds)（自选基金助手），数据源同样为东方财富（天天基金）公开 API，无需登录。

![preview](./preview.jpg)

## 特性

- ✅ 单文件 Python 脚本，**零依赖**（仅用 stdlib），系统 Python 3 即可运行
- ✅ 一次请求获取全部基金数据（东方财富批量接口）
- ✅ 列表头部显示**合计**（持有总额、今日估算总收益、总涨跌幅）
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
| `fund` | 查看自选基金列表 |
| `fund config` | 打开配置文件 `funds.json`（用默认文本编辑器） |

在列表中：
- **回车 ⏎**：把当前行的概要复制到剪贴板
- **⌘+L**：以 large type 大字号显示当前行

## 配置文件

首次运行 `fund` 会在以下位置生成示例配置：

```
~/Library/Application Support/Alfred/Workflow Data/com.denis.funds-alfred/funds.json
```

格式：

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
| `code` | ✅ | 6 位基金代码（字符串；如 `"001618"`，前导零不会丢） |
| `num` | ✅ | 持有份额 |
| `cost` | ❌ | 成本价；填写后才会显示「持仓总收益 / 持仓收益率」 |

## 数据接口

只调用一个公开接口（天天基金老版实时估值端点）：

```
GET https://fundgz.1234567.com.cn/js/<基金代码>.js
```

返回示例（JSONP 包装）：

```js
jsonpgz({"fundcode":"161725","name":"招商中证白酒指数(LOF)A",
        "jzrq":"2026-06-25","dwjz":"0.5162",
        "gsz":"0.5077","gszzl":"-1.65","gztime":"2026-06-26 11:21"});
```

- 无需 Cookie、Token 或任何鉴权（CORS `*`）
- 不存在的基金代码会返回 `jsonpgz();`（空参数），脚本识别为「未找到」
- 多只基金通过 `ThreadPoolExecutor` 并发拉取，3 只 ≈ 0.2 秒

> 注：原 Chrome 插件 `x2rr/funds` v2.0+ 改用了 `fundmobapi.eastmoney.com/FundMNewApi/FundMNFInfo`
> 批量端点，但实测该端点在交易时段也会对非官方 App 客户端返回 `GSZ: null`
> （估值字段缺失），所以本 workflow 退回使用更稳定的老端点。

## 计算逻辑

与参考项目（`x2rr/funds`）保持一致：

| 指标 | 公式 |
|---|---|
| 持有额 | `gsz × num`（实时；闭市退化为 `dwjz × num`） |
| 涨跌幅 | `gszzl`（已结算 ✓ 时即为当日真实涨跌幅） |
| 今日估算收益 | `(gsz − dwjz) × num` |
| 持仓总收益 | `(gsz − cost) × num`（仅当 `cost` 存在时；闭市退化为 dwjz） |
| 持仓收益率 | `(gsz − cost) / cost × 100%` |

## 常见问题

**Q: 闭市时所有「今日估算」都显示 —？**
A: 不会。本 workflow 使用 `fundgz.1234567.com.cn` 端点，**即便在闭市期间也会返回当日最后一次的估算值**（`gsz`/`gszzl`/`gztime`），所以全天任意时段都能看到当天的估值。只有在极少数情况（如开盘前的凌晨、或基金当天暂停估值）才会返回空。

**Q: 基金代码以 0 开头怎么办？**
A: JSON 中**必须用字符串**，例如 `"code": "001618"`，否则 JSON 解析后会变成数字 `1618` 导致请求失败。脚本会自动补零到 6 位，但仍建议你直接写成字符串。

**Q: 如何卸载？**
A: 在 Alfred Preferences → Workflows 中右键 → Remove。配置文件在 `~/Library/Application Support/Alfred/Workflow Data/com.denis.funds-alfred/` 下，可手动删除。

## 致谢

- 数据源：[东方财富网 / 天天基金](https://fund.eastmoney.com/)
- 灵感来源：[x2rr/funds](https://github.com/x2rr/funds)（GPL-3.0）

## License

MIT
