#!/usr/bin/env python3
"""生成 info.plist (Alfred 5 workflow definition)."""

import os
import plistlib
import uuid
from datetime import datetime

BUNDLE = "com.denis.funds-alfred"
WORKDIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "workflow")

# 固定 UID (保持稳定，重新生成时不变)
UID_SCRIPTFILTER = "A1F1F0A0-0000-0000-0000-000000000001"
UID_RUNSCRIPT = "A1F1F0A0-0000-0000-0000-000000000003"

# 回车动作: 普通行复制到剪贴板; __CONFIG__: 前缀的行打开配置文件
RUN_SCRIPT = (
    'q="$1"; '
    'if [[ "$q" == __CONFIG__:* ]]; then '
    'open "${q#__CONFIG__:}"; '
    'else printf "%s" "$q" | pbcopy; fi'
)

plist = {
    "bundleid": BUNDLE,
    "category": "Productivity",
    "connections": {
        UID_SCRIPTFILTER: [
            {
                "destinationuid": UID_RUNSCRIPT,
                "modifiers": 0,
                "modifiersubtext": "",
                "vitoclose": False,
            }
        ],
    },
    "createdby": "denis",
    "description": "查看自选基金当前情况 (持有额 / 涨跌幅 / 估算收益 / 持仓收益)。数据源: 东方财富",
    "disabled": False,
    "name": "Funds Alfred",
    "objects": [
        # 1. Script Filter
        {
            "config": {
                "alfredfiltersresults": False,
                "alfredfiltersresultsmatchmode": 0,
                "argumenttreatemptyqueryasnil": False,
                "argumenttrimspaces": True,
                "argumenttype": 1,  # optional argument
                "escaping": 102,
                "keyword": "fund",
                "queuedelaycustom": 3,
                "queuedelayimmediatelyinitially": True,
                "queuedelaymode": 0,
                "queuemode": 1,
                "runningsubtext": "正在拉取基金数据…",
                "script": '"/usr/bin/python3" fund.py "$1"',
                "scriptargtype": 1,  # pass {query} as argv
                "scriptfile": "",
                "subtext": "回车: 复制基金代码 · ⌘C: 复制概要 · fund N: 切组 · fund sum: 跨组合计 · fund config: 编辑",
                "title": "自选基金",
                "type": 0,  # /bin/bash
                "withspace": True,
            },
            "type": "alfred.workflow.input.scriptfilter",
            "uid": UID_SCRIPTFILTER,
            "version": 3,
        },
        # 2. Run Script: 回车分派 (复制 or 打开配置)
        {
            "config": {
                "concurrently": False,
                "escaping": 104,
                "keyword": "",
                "queuedelaycustom": 3,
                "queuedelayimmediatelyinitially": True,
                "queuedelaymode": 0,
                "queuemode": 1,
                "runtime": "/bin/zsh",
                "script": RUN_SCRIPT,
                "scriptargtype": 1,
                "scriptfile": "",
                "type": 0,
            },
            "type": "alfred.workflow.action.script",
            "uid": UID_RUNSCRIPT,
            "version": 2,
        },
    ],
    "readme": (
        "# Funds Alfred\n\n"
        "查看自选基金当前情况。数据源: 东方财富 (天天基金)。\n\n"
        "## 用法\n\n"
        "- `fund`         查看自选基金 (默认第 1 组)\n"
        "- `fund N`       切换到第 N 个分组 (1-based)\n"
        "- `fund sum`     跨所有分组合计 (只显示合计行, 不展示单只基金)\n"
        "- `fund config`  打开配置文件 funds.json 增删基金/分组\n"
        "  - 基金行/合计行回车: 复制基金代码(合计行复制概要)\n"
        "  - ⌘C: 复制完整概要\n\n"
        "首次运行会在 Alfred Workflow Data 目录下生成示例配置 (含分组示例)。\n"
    ),
    "uidata": {
        UID_SCRIPTFILTER: {"xpos": 30, "ypos": 110},
        UID_RUNSCRIPT: {"xpos": 560, "ypos": 110},
    },
    "variablesdontexport": [],
    "version": "1.0.0",
    "webaddress": "",
}


out = os.path.join(WORKDIR, "info.plist")
with open(out, "wb") as f:
    plistlib.dump(plist, f, fmt=plistlib.FMT_XML)
print(f"wrote {out}")
