#!/usr/bin/env bash
# ============================================================
#  Pan launcher (POSIX: mac/linux) — 平替 scripts/start_pan.bat
#
#  - 后台启动 Pan Core (main.py)，PID 记入 data/process.pid
#  - QQ bridge 由 main.py 按 config qq.enabled 自行拉起，无需单独启动
#  - cloudflared 远程隧道暂未平替（start_cf.ps1 的临时配置改写逻辑），
#    需要远程通道时手工启动
#
#  用法: bash scripts/start.sh
# ============================================================
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
DATA_DIR="$ROOT/data"
PID_FILE="$DATA_DIR/process.pid"
OUT_LOG="$DATA_DIR/pan.out.log"

mkdir -p "$DATA_DIR"

# ---- 1. 清理 Python 缓存（对齐 start_pan.bat 第 1 步）----
find "$ROOT" -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null
find "$ROOT" -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete 2>/dev/null

# ---- 2. 定位 venv 解释器（setup.sh 建在仓库根 .venv）----
PYTHON="$ROOT/.venv/bin/python"
if [ ! -x "$PYTHON" ]; then
    echo "[ERROR] Virtual env python not found: $PYTHON"
    echo "        请先运行: bash scripts/setup.sh"
    exit 1
fi

# ---- 3. 防重复启动 ----
OLD_MAIN="$(sed -n 's/^MAIN=//p' "$PID_FILE" 2>/dev/null)"
if [ -n "$OLD_MAIN" ] && kill -0 "$OLD_MAIN" 2>/dev/null; then
    echo "[WARN] Pan Core 已在运行 (PID=$OLD_MAIN)，先执行 scripts/stop.sh"
    exit 1
fi

# ---- 4. 后台启动 main.py ----
# setsid（Linux 有、macOS 默认无）让 main.py 成为独立进程组组长，
# stop.sh 可 kill -- -PID 整组收割子进程；无 setsid 时退化为普通后台
# 进程，靠 main.py 自身 atexit/SIGTERM 收割 QQ 子进程。
USE_SETSID=0
if command -v setsid >/dev/null 2>&1; then
    USE_SETSID=1
    setsid "$PYTHON" main.py >>"$OUT_LOG" 2>&1 &
else
    "$PYTHON" main.py >>"$OUT_LOG" 2>&1 &
fi
MAIN_PID=$!

{
    echo "MAIN=$MAIN_PID"
    [ "$USE_SETSID" -eq 1 ] && echo "GROUP=1"
} > "$PID_FILE"

# ---- 5. 确认进程存活（启动即退出则报日志）----
sleep 1
if ! kill -0 "$MAIN_PID" 2>/dev/null; then
    echo "[ERROR] main.py 启动即退出，最近日志:"
    tail -n 20 "$OUT_LOG"
    rm -f "$PID_FILE"
    exit 1
fi

echo "[OK] Pan Core started, PID=$MAIN_PID (log: $OUT_LOG)"
echo "[OK] Pan started. Stop with scripts/stop.sh"
