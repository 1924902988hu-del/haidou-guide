#!/bin/bash
# 一键全量更新:静态数据 → hexdata → op.gg → 合成站点数据
# 用法:./update.sh [--force](--force 强制重抓 op.gg)
set -euo pipefail
cd "$(dirname "$0")"

echo "== [1/4] 官方静态数据 =="
python3 fetch_static.py

echo "== [2/4] hexdata(buildId 未变则自动跳过)=="
python3 fetch_hexdata.py

echo "== [3/4] op.gg(断点续跑)=="
python3 fetch_opgg.py "$@"

echo "== [4/5] 合成站点数据 =="
python3 build_site_data.py

echo "== [5/5] 图片本地化 =="
python3 fetch_images.py

echo "完成:site/ 已更新"
