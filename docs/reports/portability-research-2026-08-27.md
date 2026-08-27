# Pan 可移植性调查报告（克隆移植 / mac-linux 迁移 / 泄露扫描）

- **日期**：2026-08-27
- **分支**：main（只读调查，全程未改代码、未动运行中服务）
- **方法**：3 个并行 Explore 子代理分别负责 2.1 / 2.2 / 2.3，主线对关键结论做了抽查核实（main.py:24 硬编码、config.example.json 存在性、QQ 号泄露点、codex `_norm_path`），全部吻合
- **远端**：`github.com/AblazeGHR/pan`，追踪文件 276 个，历史扫描覆盖 `--all` 全分支

---

## 2.1 克隆可移植性：半开箱即用，核心可跑，周边降级

### 结论

**好消息**：`load_config()`（packages/core/config.py:118-124）在 config.json 缺失时返回内置 `DEFAULT_CONFIG`，不报错；`config.example.json` 存在且字段齐全；所有数据落 `data/`（server.py:121 相对推导）；模型 CLI 用 `shutil.which` 从 PATH 找（cbc/adapter.py:144-155 等），均有 `PAN_*_PATH` 环境变量兜底。

### 新环境最简启动清单

1. 仓库根建 `.venv`，装 `minimal-requirements.txt`（start_pan.bat:27 硬编码此位置）
2. QQ 模块：`pip install -r packages/qq/requirements.txt` 到任一解释器——main.py:24 默认 `E:\software\miniforge\python.exe`，可 `PAN_QQ_PYTHON` 覆盖
3. PATH 里装 node + cbc/kimi/codex/opencode（缺哪个砍哪个 adapter）
4. 可选：`cp config.example.json config.json`；QQ 连接来源是 **packages/qq/.env 的 `ONEBOT_WS_URLS`**（gitignored，新机器必须重建）
5. 前端：`packages/web/` 内 `pnpm install && pnpm build`；legacy 走根目录 `npx tsc`
6. 远程通道才需要：cloudflared + named tunnel yml + `../ai_coc/pan_plugin/manifest.json`（仓库外兄弟项目）

### 阻塞项清单（没有就跑不起来）

| 阻塞项 | 藏身处 |
|---|---|
| 仓库根 `.venv` | scripts/start_pan.bat:12,27-32（硬编码相对路径，缺失即退出） |
| QQ 独立解释器 | main.py:22-24（`E:\software\miniforge\python.exe`）、stop_pan.bat:56 |
| `packages/qq/.env` | gitignored；当前 channel=llonebot 且 config.json 无 ws_urls，.env 的 `ONEBOT_WS_URLS` 是唯一连接来源（main.py:40-55,90-101） |
| PATH 里的模型 CLI | cbc/kimi/codex/opencode adapter 的 `_resolve_*`（shutil.which 兜底链） |
| `C:\Users\<user>\.cloudflared\config.yml` | config.json:33 remote.config_path（仅远程通道需要） |
| `../ai_coc/pan_plugin/manifest.json` | config.json:47-51 plugin_manifests 引用仓库外兄弟项目，缺失则该 manifest 加载失败 |
| QQ 网关（LLOneBot/NapCat） | 仓库外外部进程，Pan 仅通过 WS URL 连接（llonebot.py:32 默认 3002 / napcat.py:24 默认 3001） |

**重要修正**：main 分支上不存在 `bin/llbot/`——LLOneBot 是仓库外的外部进程，Pan 只连 WS 地址。

### 硬编码路径分诊

- **致命**：main.py:24、stop_pan.bat:56（E 盘）、config.json:33（C 盘用户名，仅 remote）
- **可选降级**：embedder.py:403-416（`D:/cache/huggingface` 仅 C 盘缓存且 D 盘存在时，`HF_HOME` 可覆盖）
- **文档/测试/脚本（不影响运行）**：character.py:8、manifest_loader.py:11,215、cbc/claude sessions.py 注释、tests/test_kimi_adapter.py:19、tests/test_claude_adapter.py:35-37、scripts/repro_mcp_override.py:113-119、docs/*
- **运行时产物（可再生）**：opencode.json:6,11、data/mcp-configs/ses_*.mcp.json 含 `D:\project\Pan\.venv` 绝对路径，运行时自动再生

---

## 2.2 mac/linux 迁移：小改偏中，约 2-4 人日

**代码本体几乎为可移植而写**：全仓 0 处 pywin32/winreg/msvcrt；约 120 处读写显式 `encoding="utf-8"`；子进程 stdout 走字节流 + `decode("utf-8", errors="replace")`；进程树终止统一用 psutil（worker.py:207-220、tunnel.py:129-145）；原子写用 tmp+`os.replace`（config.py:166-171、session.py:462-472、server.py:2750-2752）；server.py:689-717 开终端已有 darwin/linux 分支。

### A 级（改一行/加平台分支）

| 位置 | 说明 |
|---|---|
| main.py:24 | QQ 解释器盘符硬编码；已有 `PAN_QQ_PYTHON` 覆盖，按平台给默认值即可 |
| packages/core/memory/embedder.py:413-414 | `startswith("C:")` → `D:/cache` 盘符假设；POSIX 上条件不触发，顺手清理 |
| packages/core/adapters/cbc/adapter.py:40 | `subprocess.run(..., text=True)` 缺 `encoding="utf-8"` |
| packages/core/adapters/opencode/adapter.py:543-546 | 同上 |
| packages/core/adapters/kimi/sessions.py:104-109 | `_same_path` 用 `.lower()` + 反斜杠替换比较路径；改 `os.path.normpath` |
| tests/test_kimi_adapter.py:19、tests/test_claude_adapter.py:35 | 夹具写死 `C:/Users/14709/...`，改 `tempfile.mkdtemp`/`tmp_path` |
| tests/test_session_import.py | 约 20 处 `D:/tmp/...`，批量改 tmp_path |
| scripts/repro_mcp_override.py:113-119 | 调试脚本写死 `D:\project\Pan`，非运行路径 |

### B 级（小重设计）

| 位置 | 说明 |
|---|---|
| packages/core/adapters/codex/sessions.py:65-72 | ⚠️ `_norm_path`（`casefold()` + 强制反斜杠）——POSIX 下**静默找不到会话**，全仓最隐蔽的破坏点（已亲自核实） |
| packages/core/adapters/cbc/sessions.py:169-261 | `browse_cbc_tree` 整套"盘符为根、`\` 分隔"的会话树浏览器，需按 `os.sep` 重写分支 |
| packages/core/adapters/cbc/sessions.py:264-307 | `_parse_project_label`/`_project_dir_to_path` 盘符剥离逻辑；有真值兜底，仅 fallback 受影响 |
| packages/core/adapters/claude/sessions.py:51-57 | `_decode_cwd` 固定产出 `C:\...` 形态；仅展示用途，加平台分支即可 |
| packages/core/adapters/cbc/sessions.py:30-43 | `sanitize_project_dir_name` 镜像 cbc CLI 自身规则，需实测 mac 上规则是否一致 |

### C 级（需重写但平替简单）

| 位置 | 说明 |
|---|---|
| scripts/start_pan.bat | 核心启动链入口，重写为 start_pan.sh，逻辑平替简单 |
| scripts/stop_pan.bat:32-59 | `taskkill /T /F` + Win32_Process 按命令行匹配杀进程；POSIX 重写反而更简单（进程组 kill / pkill -f） |
| scripts/start_main.ps1 | 12 行，`$!` 天然替代，并入 start_pan.sh |
| scripts/start_cf.ps1 | tunnel 启动：sed 平替 `-replace 'http://localhost:\d+'`，注意编码与 `$env:TEMP`/`$env:USERPROFILE` 兜底改写 |
| packages/web/server.py:689-703 | win32 分支已有 darwin/linux 平替（704-717），**无需迁移工作** |

### 建议迁移路线

1. **第一批（先做，1 天内）**：写 `start_pan.sh` + `stop_pan.sh`；修 codex `_norm_path` 与 kimi `_same_path`（唯一静默破坏点）；补 2 处 `encoding="utf-8"`；改 QQ 解释器默认值
2. **第二批（顺手，半天）**：cbc 树浏览器 POSIX 分支；测试夹具去盘符化；embedder 清理
3. **可暂时绕过/降级**：QQ 模块 `qq.enabled=false`（LLOneBot/NapCat 绑 Windows QQ NT 生态）；claude `_decode_cwd` 展示错误可暂受；历史会话跨 OS 必失联，接受或手工迁目录

### 三大风险

1. **CLI 生态 POSIX 形态未实测**：cbc 在 mac 上的项目目录名 sanitize 规则是否与 Windows 一致（直接决定 resume/fork 寻址），唯一可能 B 膨胀为 C 的点
2. **跨 OS 历史会话全失联**：`~/.codebuddy/projects`、`~/.kimi-code` 的脱敏目录名绑定绝对路径
3. **QQ 链路整体不可移植**：NoneBot + LLOneBot/NapCat 依赖 Windows QQ NT 客户端生态，mac 无平替

---

## 2.3 本机绑定信息泄露扫描：无凭据泄露，但个人信息已上 GitHub

### 高严重度（真实凭据）：未发现

- 追踪文件与全量 git 历史均无 token/sk-/password **实值**（命中皆为字段名/占位符/UI 高亮关键词）
- 历史上 3 个 config.json 版本（`2908e0b`、`fe14009`、`965c583^`）内容仅为 port/model/plugin_manifests，无凭据字段
- `packages/qq/.env`（含 LLOneBot 令牌）从未入库且 ignore 生效；`bin/llbot/`、`auth_token.txt` 从未进历史（`git log --all -- *llbot*` 为空）
- `git status --porcelain` 干净，无未追踪敏感文件

### 中严重度（个人信息已推送 GitHub）

| # | 位置 | 类型 | 脱敏值 |
|---|------|------|--------|
| 1 | packages/qq/test_qq_api.py:486-581（约 20 处） | QQ 号 + 昵称 | `1470****3983`（昵称明文） |
| 2 | packages/qq/channels/llonebot.py:13 | QQ 号（注释） | `1470****3983` |
| 3 | packages/web/src/components/session/PostboxModal.tsx:23 | QQ 号（注释示例） | `1470****3983` |
| 4 | tests/test_claude_adapter.py:9,35,37、tests/test_kimi_adapter.py:19 | 用户名 + 用户目录路径 | `C:\Users\1470****` |
| 5 | packages/core/adapters/claude/sessions.py:41 | 用户名（docstring） | `C--Users-1470****probe` |
| 6 | scripts/kimi-mcp-probe/02_acp_mcp.log:1 | 用户名（KIMI_CODE_HOME 路径） | `C:\Users\1470****\.kimi-code` |
| 7 | 历史提交 f51cddc 的父版本 scripts/start_pan.bat | 用户名（cloudflared 路径） | `C:\Users\1470****config-test.yml`（f51cddc 已改 `%USERPROFILE%`，旧值仍在历史） |
| 8 | docs/跨设备移植报告-2026-08-19.md:14,37,45,82 | 用户名 + 机器路径 | `C:\Users\1470****\.cloudflared\config.yml` |

### 低严重度（仅绝对路径，可接受）

- packages/core/character.py:8、manifest_loader.py:11,215 — docstring 示例 `D:/project/RuleWhisper/pan_plugin`
- docs/archive/cbc-mcp-experiments/*、scripts/kimi-mcp-probe/* — `D:/project/Pan/.venv`、`E:/software/miniforge/python.exe`

### .gitignore 覆盖评估（扫描时点）

| 目标 | 状态 | 依据 |
|------|------|------|
| config.json / .mcp.json / opencode.json | ✅ | .gitignore:40-43 |
| bin/llbot/data/ | ✅（隐式） | 第 26 行 `data/` 无前导斜杠匹配任意层级；非 data 目录则不覆盖 |
| packages/qq/.env | ✅ | 第 47 行，仅此单条；无通用 `*.env` |
| auth_token* | ❌ | 无规则（预防缺口） |
| 日志 | ⚠️ | 仅 3 个具体文件名，无通用 `*.log` |
| node_modules / .venv / dist / app.js | ✅ | 第 11、2、14、45 行 |

### 处置建议

1. 凭据无需撤销换新（从未入库）
2. QQ 号/昵称改占位符；历史清理（git filter-repo）属可选
3. 测试夹具改 `Path.home()`/`tmp_path`（顺带修换机必挂的测试）
4. .gitignore 补 `auth_token*`、`*.env`、`*.log` 三行
5. `data/` 隐式覆盖建议加注释说明

---

## 行动优先级清单

| # | 行动 | 状态（2026-08-27） |
|---|------|------|
| ① | .gitignore 补三行（`auth_token*`、`*.env`、`*.log`）+ QQ 号/昵称占位符化（test_qq_api.py、llonebot.py、PostboxModal.tsx） | ✅ 已执行，pytest 36 passed、pnpm build 通过、`git grep` 零残留 |
| ② | 新环境 bootstrap 脚本（覆盖启动清单第 1-5 步） | 待做 |
| ③ | 迁移前置实测：cbc/kimi CLI 在目标 OS 的安装形态与项目目录命名规则 | 待做 |
| ④ | `git rm --cached scripts/kimi-mcp-probe/*.log` 下架已追踪日志（含用户名路径），需 commit | 待做 |
| ⑤ | 迁移第一批：start/stop.sh + codex/kimi 路径比较修复 + encoding 补齐 | 待做 |
