# Reimport 来源冲突确认：TA 验收交付

日期：2026-10-10。worktree：`D:/project/pan-worktrees/codex-reimport-origins-20261010`；分支：`fix/codex-reimport-origins-20261010`。

已验收基线 main/practical：`6f1047127e8e90d7de3704dba2ae884472ae1dd9`；本次实现源代码 SHA：`ccfb670bce7588bea94e01c677fb7469653b4d22`。该基线为候选祖先，无需合入更新。其间保留诊断提交 `bd043087bc2dc70a073a247844c17fc5c613b2ec`。交付提交包含本报告与证据，最终 SHA 由交付消息提供。没有推进 main/practical、push、build 或重启服务。

## 根因与行为

指定 Session `ses_be105a81379e8cb1` 的旧 Pan 历史有 4861 行、7 个 queue origins，native 解析为 4843 行。最后一个 origin 未出现在 native 消息中，仅 6 个可证明匹配；普通保护拒绝丢弃它是正确行为。`sent_to_cli` 代表 wrapper stdin 写入，不证明 native rollout 已保存。此前附件投影修复不能补出真正不存在的 native 消息。

本次实现用户授权的显式有损覆盖流程：普通 reimport 保留保护并返回结构化摘要；全局 Modal（复用既有 Modal/Button）显示 Pan 历史位置、数量、原因与短预览，说明无法保留的 task/source/envelope/parts 信息会丢失。菜单关闭后弹窗仍存在。取消不请求 force，原历史保留。明确确认才按 native 顺序与内容替换历史，可证明匹配的 Pan messageId/taskId/source/envelope/parts 尽量保留。

后端 HMAC token 绑定 Session 身份、adapter/native ID/workdir、Pan epoch/revision/完整历史、readonly、native 解析历史/usage 及 Codex 完整 rollout 字节摘要。进程重启 token 失效。原生无文件快照能力时仍绑定解析历史与 usage。读取前后、同 ticket apply 前和 save 后检查；新增消息、native 非正文事件变化、直接未递增 revision 的历史编辑也会使旧确认失效。前端收到新摘要后必须再次点击确认；无摘要的过期错误先重新检查。

force 仅绕过 origin 匹配保护，不绕过 readonly、并发或保存失败。沿用同一个持久化 ticket 的 apply/save/rollback；成功落盘后的真实调用取消保留新 durable 状态，实际保存失败则恢复内存与 JSON/JSONL。没有将 agent/report/system/channel 统一改为 user，没有删除或弱化原有测试。

## 验证与证据

| 层级 | 真实结果 | 证据 |
| --- | --- | --- |
| 私有归档基线 + 新回归 | 23 failed，0 skip，退出 1 | baseline.xml / baseline.log / baseline-exit.json |
| 候选 Python、ASGI HTTP、磁盘重读组合 | 316 passed，0 failed/skip，退出 0 | backend.xml / backend.log / backend-exit.json |
| TypeScript | `pnpm exec tsc -b --pretty false` 退出 0；提交 hook 类型检查通过 | 源代码 SHA 同上 |
| 前端 Vitest | 4 文件，55 passed，退出 0 | frontend.xml |
| 实际 Chromium 交互 | cancel/confirm/stale/failure 四场景通过，0 page error、0 保护端口请求 | browser/result.json 与 summary/stale/failure.png |
| 指定真实 Session 的私有快照 HTTP | 普通冲突 1 项，位置 4861；取消文件不变；确认 HTTP 200；磁盘重读 4843 行，6 个匹配 origins 元数据保留；原始文件摘要不变 | be105a-private-http.json |

后端 23 项新回归覆盖摘要限长、缺失/编辑/歧义原因、取消、确认与磁盘重读、匹配元数据、错 Session/伪造 token、Pan append/native 正文及非正文变动、排队期间 readonly/未版本化编辑、原生保存期间变化、落盘失败精确回滚、真实调用取消的成功与失败结果。组合回归还覆盖附件、既有来源保护、导入、usage、增量历史、summary、投递、worker、steer 和 readonly。

后端精确命令参数见 backend-exit.json；基线精确命令参数见 baseline-exit.json。两者使用 `python scripts/run_tests_isolated.py -- uv run --no-project --python E:/software/miniforge/python.exe --with-requirements minimal-requirements.txt --with pytest --with pytest-timeout -- python -m pytest ... -o addopts= -q --junitxml=...`。

前端命令（工作目录 packages/web）：

```text
pnpm exec tsc -b --pretty false
pnpm exec vitest run src/components/session/ReimportConfirmationModal.test.tsx src/components/session/SessionMenu.test.tsx src/services/api.test.ts src/stores/sessionStore.race.test.ts --reporter=default --reporter=junit --outputFile=../../../audit/codex-reimport-confirmation/frontend.xml
node e2e/reimport-confirmation.mjs
```

浏览器使用真实 React 组件、store、API 调用和 Chromium 153.0.8010.12，HTTP import 响应由测试拦截；独立 Vite 随机 loopback 端口 65499，未访问 8767/8768，结束清理服务与临时入口。实际后端 HTTP 由私有 Session/rollout 的 ASGI 验证，未进行真实用户写入式 reimport。真实 Session 快照证明在最后两项保护强化前完成；正向路径未改动，最终 SHA 的完整后端组合再次通过。

依赖：package.json/lockfile 与 canonical 一致；初始共享只读 junction 缺少 xterm 类型，已 Undo，随后 worktree 私有 `pnpm install --frozen-lockfile --offline` 成功，未修改共享安装或锁文件。Python 使用隔离 uv 环境。初始浏览器入口缺 React refresh preamble 的失败已修正，最终四场景通过。证据文本仅规范化换行/行尾空白，未更改结果。

## Diff 范围与未验证边界

实现提交共 14 文件、750 insertions/21 deletions：Codex snapshot provider、Session ticket 验证 callback、HTTP 冲突/确认逻辑、全局确认 store/Modal 与 App/Menu/API/store 接线、Python/API/组件回归及浏览器 harness。HTTP 契约说明和本报告在后续证据提交中。

未验证浏览器直连真实后端的整条链路、真实 Codex 在线生成/实际用户确认覆盖、生产部署、build、MA 独立验收、断电/进程崩溃时双文件原子性、跨平台、长期性能。正在变化的 native rollout 会使确认过期，需重新检查，这是有意保护。完成通知沿已有 report 订阅回 MA；由 MA 决定独立验收及 FF/build。
