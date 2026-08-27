#!/usr/bin/env bash
# ============================================================
#  Pan stopper (POSIX: mac/linux) — 平替 scripts/stop_pan.bat
#
#  只杀 start.sh 记录的 PID（及其进程组）与 main.py 记录的 QQ
#  bridge PID，绝不 pkill 全部 python 进程。
#
#  用法: bash scripts/stop.sh
# ============================================================
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PID_FILE="$ROOT/data/process.pid"
QQ_PID_FILE="$ROOT/data/qq_bot.pid"

MAIN_PID="$(sed -n 's/^MAIN=//p' "$PID_FILE" 2>/dev/null)"
IN_GROUP="$(sed -n 's/^GROUP=//p' "$PID_FILE" 2>/dev/null)"
QQ_PID="$(cat "$QQ_PID_FILE" 2>/dev/null)"

stopped=0

# ---- 1. QQ bridge 先杀（可能比 main.py 活得更久）----
if [ -n "$QQ_PID" ] && kill -0 "$QQ_PID" 2>/dev/null; then
    kill -TERM "$QQ_PID" 2>/dev/null && { echo "[OK] QQ bridge killed, PID=$QQ_PID"; stopped=1; }
fi

# ---- 2. Pan Core（setsid 启动的整组杀，覆盖 main.py 全部子进程）----
if [ -n "$MAIN_PID" ] && kill -0 "$MAIN_PID" 2>/dev/null; then
    if [ "$IN_GROUP" = "1" ]; then
        kill -TERM -- "-$MAIN_PID" 2>/dev/null || kill -TERM "$MAIN_PID" 2>/dev/null
    else
        # 非进程组启动：先 TERM 子进程再 TERM 主进程
        pkill -TERM -P "$MAIN_PID" 2>/dev/null
        kill -TERM "$MAIN_PID" 2>/dev/null
    fi
    echo "[OK] Pan Core stopping, PID=$MAIN_PID"
    stopped=1

    # 宽限 5s（uvicorn 收 SIGTERM 后优雅关停 + atexit 收割 QQ），仍活则 KILL
    for _ in 1 2 3 4 5; do
        kill -0 "$MAIN_PID" 2>/dev/null || break
        sleep 1
    done
    if kill -0 "$MAIN_PID" 2>/dev/null; then
        if [ "$IN_GROUP" = "1" ]; then
            kill -KILL -- "-$MAIN_PID" 2>/dev/null
        fi
        pkill -KILL -P "$MAIN_PID" 2>/dev/null
        kill -KILL "$MAIN_PID" 2>/dev/null
        echo "[WARN] Pan Core force-killed, PID=$MAIN_PID"
    fi
fi

if [ "$stopped" -eq 0 ]; then
    echo "[INFO] No running Pan process found (pid file: $PID_FILE)"
fi

# ---- 3. 清理 pid 文件 ----
rm -f "$PID_FILE" "$QQ_PID_FILE"
echo "[OK] Pan stopped."
