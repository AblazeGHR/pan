@echo off
setlocal EnableExtensions EnableDelayedExpansion
REM ============================================================
REM  Pan setup — Windows 简版 (setup.sh 的 Windows 对应物)
REM  覆盖启动清单 1-5 步；单步失败不中断。仅输出提示，不改系统。
REM  用法: scripts\setup.bat
REM ============================================================

pushd "%~dp0.."
set "ROOT=%CD%"
popd
cd /d "%ROOT%"

echo Pan setup - 仓库根: %ROOT%
echo.

REM ---- [1/5] .venv + minimal-requirements.txt (start_pan.bat:27 依赖此位置) ----
echo ========== [1/5] .venv + minimal-requirements.txt ==========
set "VPY=%ROOT%\.venv\Scripts\python.exe"
if exist "%VPY%" (
    echo [OK] .venv 已存在，复用
) else (
    where py >nul 2>&1
    if !ERRORLEVEL! NEQ 0 (
        echo [FAIL] 未找到 py 启动器，也未存在 .venv — 请先安装 Python 3.10+
        set "VPY="
    ) else (
        echo 用 py -3 创建 .venv ...
        py -3 -m venv "%ROOT%\.venv"
        if exist "%VPY%" (echo [OK] .venv 创建成功) else (echo [FAIL] .venv 创建失败 & set "VPY=")
    )
)
if defined VPY (
    "%VPY%" -m pip install -r "%ROOT%\minimal-requirements.txt"
    if %ERRORLEVEL% EQU 0 (echo [OK] 核心依赖安装完成) else (echo [FAIL] 核心依赖安装失败 — Pan Core 无法启动)
)
echo.

REM ---- [2/5] QQ 模块依赖 (解释器解析链: %%PAN_QQ_PYTHON%% > config.json qq.python > E盘 miniforge > PATH python) ----
echo ========== [2/5] QQ 模块依赖 ==========
set "QQ_PY=%PAN_QQ_PYTHON%"
if not defined QQ_PY (
    if exist "E:\software\miniforge\python.exe" (
        set "QQ_PY=E:\software\miniforge\python.exe"
    ) else (
        where python >nul 2>&1 && set "QQ_PY=python"
    )
)
if defined QQ_PY (
    "%QQ_PY%" -m pip install -r "%ROOT%\packages\qq\requirements.txt"
    if %ERRORLEVEL% EQU 0 (
        echo [OK] QQ 依赖已装入: %QQ_PY%
    ) else (
        echo [WARN] QQ 依赖安装失败 — 可在 config.json 设 qq.enabled=false 跳过
    )
) else (
    echo [WARN] 未找到 QQ 依赖所需解释器 — 可设 PAN_QQ_PYTHON 后重跑，或 qq.enabled=false
)
echo.

REM ---- [3/5] PATH 检查: node + 模型 CLI ----
echo ========== [3/5] PATH 检查 ==========
for %%c in (node cbc kimi codex opencode cloudflared) do (
    where %%c >nul 2>&1
    if !ERRORLEVEL! EQU 0 (
        echo [OK] %%c: 检测到
    ) else (
        echo [WARN] %%c 未找到 — 对应功能/adapter 不可用，可在 config.json 中禁用
    )
)
echo.

REM ---- [4/5] config.json + packages/qq/.env ----
echo ========== [4/5] 配置文件 ==========
if exist "%ROOT%\config.json" (
    echo [OK] config.json 已存在，跳过
) else (
    copy /y "%ROOT%\config.example.json" "%ROOT%\config.json" >nul
    echo [OK] 已生成 config.json — 请按需修改端口等字段
)

REM 把 [2/5] 探测到的 QQ 解释器固化进 config.json 的 qq.python（单一事实源），
REM main.py / stop_pan.bat 均从该字段解析。字段已有相同值时跳过，不重复覆盖。
if defined QQ_PY (
    powershell -NoProfile -Command "$f='%ROOT%\config.json'; $py='%QQ_PY%'; try { $c=Get-Content -Raw -LiteralPath $f | ConvertFrom-Json } catch { $c=$null }; if ($c) { if (-not $c.qq) { $c | Add-Member -Force -MemberType NoteProperty -Name qq -Value (New-Object PSObject) }; if ($c.qq.python -ne $py) { $c.qq | Add-Member -Force -MemberType NoteProperty -Name python -Value $py; $t=$f+'.tmp'; ConvertTo-Json -InputObject $c -Depth 32 | ForEach-Object { [IO.File]::WriteAllText($t, $_) }; Move-Item -Force $t $f; Write-Host \"[OK] qq.python -> $py\" } else { Write-Host \"[INFO] qq.python 已一致，跳过\" } } else { Write-Host \"[WARN] config.json 不可读，未写入 qq.python\" }"
) else (
    echo [INFO] 未探测到 QQ 解释器，跳过 qq.python 写入
)
if exist "%ROOT%\packages\qq\.env" (
    echo [OK] packages\qq\.env 已存在
) else (
    echo [FAIL] packages\qq\.env 不存在 — gitignored，必须手工重建！
    echo        ONEBOT_WS_URLS 是 QQ 连接唯一来源，模板:
    echo        ONEBOT_WS_URLS=["ws://127.0.0.1:3002"]
    echo        不用 QQ 可 config.json 设 qq.enabled=false
)
echo.

REM ---- [5/5] 前端构建 ----
echo ========== [5/5] 前端构建 ==========
where pnpm >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    pushd "%ROOT%\packages\web"
    call pnpm install
    if !ERRORLEVEL! EQU 0 (
        call pnpm build
        if !ERRORLEVEL! EQU 0 (echo [OK] React SPA 构建完成) else (echo [WARN] React 构建失败 — legacy 仍可用)
    ) else (
        echo [WARN] pnpm install 失败 — 跳过 React 构建
    )
    popd
) else (
    echo [WARN] 未找到 pnpm — 跳过 React 构建。安装: corepack enable 或 npm i -g pnpm
)
where npx >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    pushd "%ROOT%"
    call npx tsc
    if !ERRORLEVEL! EQU 0 (echo [OK] Legacy 前端编译完成) else (echo [WARN] Legacy 编译失败)
    popd
) else (
    echo [WARN] 未找到 npx/node — 跳过 legacy 编译
)
echo.

echo ========== 汇总 ==========
echo 按上面 [OK]/[WARN]/[FAIL] 逐项处理；核心齐备后用 scripts\start_pan.bat 启动。
endlocal
exit /b 0
