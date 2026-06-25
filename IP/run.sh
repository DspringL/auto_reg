#!/bin/zsh
# 神龙代理验证脚本启动器
# 创建继承全局依赖的虚拟环境，安装缺失依赖后运行 verify_proxy.py

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"

echo "=================================================="
echo " 神龙代理 Playwright 验证工具"
echo "=================================================="
echo "工作目录：$SCRIPT_DIR"

# 创建虚拟环境（--system-site-packages 继承全局依赖）
if [ ! -d "$VENV_DIR" ]; then
    echo "\n[1/4] 创建虚拟环境（继承全局依赖）..."
    python3 -m venv --system-site-packages "$VENV_DIR"
    echo "      虚拟环境已创建：$VENV_DIR"
else
    echo "\n[1/4] 虚拟环境已存在，跳过创建"
fi

# 激活虚拟环境
source "$VENV_DIR/bin/activate"
echo "      Python：$(python --version)"
echo "      路径：$(which python)"

# 检查并安装缺失依赖
echo "\n[2/4] 检查依赖..."
MISSING=""

python -c "import requests" 2>/dev/null || MISSING="$MISSING requests"
python -c "import playwright" 2>/dev/null || MISSING="$MISSING playwright"

if [ -n "$MISSING" ]; then
    echo "      安装缺失依赖：$MISSING"
    pip install --quiet $MISSING
else
    echo "      所有依赖已就绪（requests、playwright）"
fi

# 检查 Playwright 浏览器是否已安装
echo "\n[3/4] 检查 Playwright Chromium..."
CHROMIUM_PATH=$(python -c "
from playwright.sync_api import sync_playwright
try:
    with sync_playwright() as p:
        print(p.chromium.executable_path)
except Exception:
    print('')
" 2>/dev/null)

if [ -f "$CHROMIUM_PATH" ]; then
    echo "      Chromium 已安装：$CHROMIUM_PATH"
else
    echo "      正在安装 Chromium..."
    python -m playwright install chromium
fi

# 运行验证脚本
echo "\n[4/4] 启动验证脚本...\n"
echo "=================================================="
cd "$SCRIPT_DIR"
python verify_proxy.py

deactivate
