#!/usr/bin/env bash
# ============================================================
# Pan — 新环境一键 setup 脚本 (POSIX: mac/linux)
#
# 覆盖《可移植性调查报告》(docs/reports/portability-research-2026-08-27.md)
# §2.1「新环境最简启动清单」第 1-5 步：
#   1. 仓库根建 .venv + minimal-requirements.txt (start_pan.bat:27 依赖此位置)
#   2. QQ 模块依赖 (packages/qq/requirements.txt，可用 PAN_QQ_PYTHON 覆盖)
#   3. PATH 检查: node + cbc/kimi/codex/opencode 等 CLI
#   4. config.json 复制提示 + packages/qq/.env 重建提醒
#   5. 前端构建: React (pnpm) + legacy (根目录 npx tsc)
#
# 单步失败不中断后续步骤，最后汇总。
# 用法: bash scripts/setup.sh
# ============================================================

# 不用 set -e：单步失败要继续走完后续步骤。
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

FAIL=0   # 致命失败计数 (阻断启动)
WARN=0   # 警告计数 (可降级)

ok()   { echo "✅ $*"; }
warn() { echo "⚠️  $*"; WARN=$((WARN+1)); }
bad()  { echo "❌ $*"; FAIL=$((FAIL+1)); }
step() { echo ""; echo "========== [$1] $2 =========="; }

echo "Pan setup — 仓库根: $ROOT"

# ------------------------------------------------------------
# [1/5] .venv + minimal-requirements.txt
#   start_pan.bat:27 硬编码 .venv 在仓库根；POSIX 将来由 start_pan.sh 沿用。
# ------------------------------------------------------------
step "1/5" "创建 .venv 并安装 minimal-requirements.txt"

VENV="$ROOT/.venv"
if [ -x "$VENV/bin/python" ]; then
    ok ".venv 已存在，复用: $VENV"
    VPY="$VENV/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    echo "用 python3 ($(command -v python3)) 创建 .venv ..."
    if python3 -m venv "$VENV"; then
        ok ".venv 创建成功"
        VPY="$VENV/bin/python"
    else
        bad ".venv 创建失败 — 请检查 python3 版本 (需 >= 3.10，代码用到 X | None 语法)"
        VPY=""
    fi
else
    bad "未找到 python3 — 请先安装 Python 3.10+"
    VPY=""
fi

if [ -n "$VPY" ]; then
    echo "安装 minimal-requirements.txt ..."
    if "$VPY" -m pip install -r "$ROOT/minimal-requirements.txt"; then
        ok "核心依赖安装完成 (fastapi/uvicorn/websockets/psutil/mcp/pytest)"
    else
        bad "核心依赖安装失败 — Pan Core 无法启动，请检查网络/源后重跑本脚本"
    fi
fi

# ------------------------------------------------------------
# [2/5] QQ 模块依赖
#   main.py:22-24 默认解释器硬编码为 E:\software\miniforge\python.exe (Windows)，
#   可用 PAN_QQ_PYTHON 环境变量覆盖。POSIX 上必须设置该变量。
#   不用 QQ 可跳过 (config.json 里 qq.enabled=false)。
# ------------------------------------------------------------
step "2/5" "安装 QQ 模块依赖 (packages/qq/requirements.txt)"

QQ_REQ="$ROOT/packages/qq/requirements.txt"
if [ ! -f "$QQ_REQ" ]; then
    bad "未找到 $QQ_REQ — 仓库不完整？"
else
    QQ_PY="${PAN_QQ_PYTHON:-}"
    if [ -n "$QQ_PY" ]; then
        echo "使用 PAN_QQ_PYTHON: $QQ_PY"
    else
        # POSIX 上没有 Windows 的 miniforge 路径，退而求其次用 python3，
        # 并明确提醒 main.py 的默认值在 POSIX 上无效，必须设 PAN_QQ_PYTHON。
        if command -v python3 >/dev/null 2>&1; then
            QQ_PY="$(command -v python3)"
            warn "PAN_QQ_PYTHON 未设置，临时用 python3 ($QQ_PY) 安装"
        else
            QQ_PY=""
            bad "未找到可用于 QQ 依赖的 python3"
        fi
    fi

    if [ -n "$QQ_PY" ]; then
        if "$QQ_PY" -m pip install -r "$QQ_REQ"; then
            ok "QQ 依赖已装入: $QQ_PY"
            warn "记得 export PAN_QQ_PYTHON=\"$QQ_PY\" 再启动 — main.py 默认解释器是 Windows 硬编码路径 (E:\\software\\miniforge\\python.exe)，本机无效"
        else
            warn "QQ 依赖安装失败 — 不影响 Pan Core，QQ 功能降级 (可在 config.json 设 qq.enabled=false)"
        fi
        echo "   (不用 QQ 的话可完全跳过本步，仅设 qq.enabled=false)"
    fi
fi

# ------------------------------------------------------------
# [3/5] PATH 检查: node + 模型 CLI
#   adapter 用 shutil.which 从 PATH 找 CLI，缺失可砍对应 adapter。
# ------------------------------------------------------------
step "3/5" "检查 PATH 中的 node 与模型 CLI"

MISSING_REQUIRED=0
for cli in node; do
    if command -v "$cli" >/dev/null 2>&1; then
        ok "$cli: $(command -v "$cli")"
    else
        bad "$cli 未找到 — 前端构建 (第 5 步) 会失败，请先安装 Node.js"
        MISSING_REQUIRED=1
    fi
done

MISSING_ADAPT=""
for cli in cbc kimi codex opencode; do
    if command -v "$cli" >/dev/null 2>&1; then
        ok "$cli: $(command -v "$cli")"
    else
        warn "$cli 未找到 — 对应 adapter 不可用"
        MISSING_ADAPT="$MISSING_ADAPT $cli"
    fi
done
if [ -n "$MISSING_ADAPT" ]; then
    echo "   缺失:$MISSING_ADAPT"
    echo "   → 不需要的可在 config.json 里禁用对应 adapter；需要的话安装后重跑本脚本核对。"
    echo "   → 各 CLI 均有 PAN_*_PATH 环境变量兜底 (非 PATH 安装时可用)。"
fi

# ------------------------------------------------------------
# [4/5] config.json + packages/qq/.env
#   config.json 缺失时 load_config() 回落 DEFAULT_CONFIG (不报错)，但仍建议复制。
#   packages/qq/.env 被 gitignore，ONEBOT_WS_URLS 是 QQ 连接唯一来源，新机器必须重建。
# ------------------------------------------------------------
step "4/5" "配置文件检查 (config.json / packages/qq/.env)"

if [ -f "$ROOT/config.json" ]; then
    ok "config.json 已存在，跳过"
else
    if cp "$ROOT/config.example.json" "$ROOT/config.json"; then
        ok "已从 config.example.json 生成 config.json — 请按需修改端口等字段"
    else
        warn "config.json 复制失败 — 可手动执行: cp config.example.json config.json (缺失时程序会用内置默认配置，不报错)"
    fi
fi

echo ""
if [ -f "$ROOT/packages/qq/.env" ]; then
    ok "packages/qq/.env 已存在"
else
    bad "packages/qq/.env 不存在 — 该文件被 gitignore，新机器必须手工重建！"
    echo "   它是 QQ 连接的唯一来源 (main.py 从中读 ONEBOT_WS_URLS；当前 channel=llonebot"
    echo "   时 config.json 的 ws_urls 不生效)。模板 (JSON 数组格式):"
    echo ''
    echo '     ONEBOT_WS_URLS=["ws://127.0.0.1:3002"]'
    echo ''
    echo "   → 指向你的 LLOneBot (默认 3002) / NapCat (默认 3001) WS 地址。"
    echo "   → 不用 QQ 可忽略，config.json 设 qq.enabled=false 即可。"
fi

# ------------------------------------------------------------
# [5/5] 前端构建
#   React SPA: packages/web/ 内 pnpm install && pnpm build (产物 dist/，gitignored)
#   Legacy:    根目录 npx tsc (产物 static/js/app.js，gitignored)
# ------------------------------------------------------------
step "5/5" "构建前端 (React + legacy)"

if command -v pnpm >/dev/null 2>&1; then
    echo "pnpm install && pnpm build (packages/web/) ..."
    if (cd "$ROOT/packages/web" && pnpm install && pnpm build); then
        ok "React SPA 构建完成 (packages/web/dist/)"
    else
        warn "React SPA 构建失败 — /react/ 不可用；legacy 前端仍可作为备用"
    fi
else
    warn "未找到 pnpm — 跳过 React 构建。安装: corepack enable  或  npm install -g pnpm"
fi

if command -v node >/dev/null 2>&1 && [ "$MISSING_REQUIRED" -eq 0 ]; then
    echo "npx tsc (根目录, legacy 前端) ..."
    if npx tsc; then
        ok "Legacy 前端编译完成 (packages/web/static/js/app.js)"
    else
        warn "Legacy 前端编译失败 — /vanilla 不可用；React 前端仍可作为主用"
    fi
else
    warn "node 不可用 — 跳过 legacy 编译"
fi

# ------------------------------------------------------------
# 汇总
# ------------------------------------------------------------
step "汇总" "setup 完成"

echo "致命失败: $FAIL   警告: $WARN"
echo ""
if [ "$FAIL" -eq 0 ]; then
    echo "✅ 核心步骤完成。下一步:"
    echo "   1. 检查上面 ⚠️ 项 (缺失的 CLI / PAN_QQ_PYTHON / .env)"
    echo "   2. 启动: Windows 用 scripts\\start_pan.bat；POSIX 等待 start_pan.sh (迁移第一批)"
    echo "   3. 浏览器访问 http://localhost:<config.json 里的 port>/"
else
    echo "❌ 存在致命失败 ($FAIL 项)，请按上面 ❌ 提示修复后重跑: bash scripts/setup.sh"
fi
exit 0
