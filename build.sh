#!/usr/bin/env bash
# 把 workflow/ 目录打包成可双击安装的 .alfredworkflow
set -euo pipefail

cd "$(dirname "$0")"

# 1. 重新生成 info.plist (保证与脚本一致)
/usr/bin/python3 gen_plist.py

# 2. 清掉 macOS 元数据 / Python 缓存, 避免 zip 里塞垃圾
find workflow -name '.DS_Store' -delete
find workflow -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true

# 3. 打包
OUT="funds-alfred.alfredworkflow"
rm -f "$OUT"
( cd workflow && zip -qr "../$OUT" . -x '*.DS_Store' '__pycache__/*' )

echo "✅ Built: $OUT ($(du -h "$OUT" | cut -f1))"
echo ""
echo "安装: 双击 $OUT, 或运行: open $OUT"
