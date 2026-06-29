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
UID_CONDITIONAL = "A1F1F0A0-0000-0000-0000-000000000002"
UID_CLIPBOARD = "A1F1F0A0-0000-0000-0000-000000000003"
UID_OPENFILE = "A1F1F0A0-0000-0000-0000-000000000004"

# 条件分支输出端口 UID
COND_OUT_OPEN = "B0000000-0000-0000-0000-000000000010"
COND_OUT_ELSE = "B0000000-0000-0000-0000-000000000020"

plist = {
    "bundleid": BUNDLE,
    "category": "Productivity",
    "connections": {
        UID_SCRIPTFILTER: [
            {
                "destinationuid": UID_CONDITIONAL,
                "modifiers": 0,
                "modifiersubtext": "",
                "vitoclose": False,
            }
        ],
        UID_CONDITIONAL: [
            {
                "destinationuid": UID_OPENFILE,
                "modifiers": 0,
                "modifiersubtext": "",
                "sourceoutputuid": COND_OUT_OPEN,
                "vitoclose": False,
            },
            {
                "destinationuid": UID_CLIPBOARD,
                "modifiers": 0,
                "modifiersubtext": "",
                "sourceoutputuid": COND_OUT_ELSE,
                "vitoclose": False,
            },
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
                "subtext": "回车: 复制概要 · fund N: 切换到第 N 组 · fund config: 编辑",
                "title": "自选基金",
                "type": 0,  # /bin/bash
                "withspace": True,
            },
            "type": "alfred.workflow.input.scriptfilter",
            "uid": UID_SCRIPTFILTER,
            "version": 3,
        },
        # 2. Conditional (根据 {var:action} 决定走哪个分支)
        {
            "config": {
                "conditions": [
                    {
                        "inputstring": "{var:action}",
                        "matchcasesensitive": False,
                        "matchmode": 0,  # is equal to
                        "matchstring": "open_config",
                        "outputlabel": "edit",
                        "uid": COND_OUT_OPEN,
                    }
                ],
                "elselabel": "copy",
                "elseuid": COND_OUT_ELSE,
                "outputvarname": "",
                "outputvarvalue": "",
            },
            "type": "alfred.workflow.utility.conditional",
            "uid": UID_CONDITIONAL,
            "version": 1,
        },
        # 3. Copy to Clipboard
        {
            "config": {
                "autopaste": False,
                "clipboardtext": "{query}",
                "ignoredynamicplaceholders": False,
                "transient": False,
            },
            "type": "alfred.workflow.output.clipboard",
            "uid": UID_CLIPBOARD,
            "version": 3,
        },
        # 4. Open File
        {
            "config": {
                "openwith": "",
                "sourcefile": "{query}",
            },
            "type": "alfred.workflow.action.openfile",
            "uid": UID_OPENFILE,
            "version": 2,
        },
    ],
    "readme": (
        "# Funds Alfred\n\n"
        "查看自选基金当前情况。数据源: 东方财富 (天天基金)。\n\n"
        "## 用法\n\n"
        "- `fund`         查看自选基金 (默认第 1 组)\n"
        "- `fund N`       切换到第 N 个分组 (1-based)\n"
        "- `fund config`  打开配置文件 funds.json 增删基金/分组\n\n"
        "首次运行会在 Alfred Workflow Data 目录下生成示例配置 (含分组示例)。\n"
    ),
    "uidata": {
        UID_SCRIPTFILTER: {"xpos": 30, "ypos": 110},
        UID_CONDITIONAL: {"xpos": 290, "ypos": 110},
        UID_CLIPBOARD: {"xpos": 560, "ypos": 200},
        UID_OPENFILE: {"xpos": 560, "ypos": 50},
    },
    "variablesdontexport": [],
    "version": "1.0.0",
    "webaddress": "",
}


out = os.path.join(WORKDIR, "info.plist")
with open(out, "wb") as f:
    plistlib.dump(plist, f, fmt=plistlib.FMT_XML)
print(f"wrote {out}")
