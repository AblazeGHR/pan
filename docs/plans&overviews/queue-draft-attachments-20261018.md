# 队列可视编辑与草稿附件修复（2026-10-18）

- 工作树：D:/project/pan-worktrees/queue-draft-attachments-20261018；分支 fix/queue-draft-attachments-20261018；基线 7e8f318d3f9b47a297b4e7b285ea7958dbca793f。main 已推进至 09a3a753c96135c66c3f9d733af28de1118ffff5，未追合，交 MA 整合。
- 根因：队列 lease 返回 parts JSON，InputRow 把 JSON 当 initialText，附件列表为空；草稿 codec 主动丢弃无服务器身份的 File，切 Session 取消上传后仍保留 uploading 状态。
- 修复：完整 lease body 逆向还原 RichTextComposer 文本、附件 occurrence 和 URL 节点；PATCH composerParts 在原 ID/revision/lease 下经服务器 registry 归一化。旧 text/parts/attachments 可恢复；已有 occurrence 元数据保留，客户端路径及文件名不作为资源授权。普通草稿和编辑事务隔离，取消无修改。
- 本地未上传文件用 IndexedDB structured clone 保存真实 File，PUT 前等待字节事务；服务器只保存 Session 范围 localFileId。恢复为可重试错误态，缺字节明确提示；切 Session 中断状态可重试，迟到回执受 epoch 门禁。只在确认草稿 clear 后清理本 Session 的本地字节；服务器附件不随草稿清理删除。
- 未改变发送/Steer 回执时间、unknown 防重、FIFO、readonly、队列锁和编辑 lease 门禁。JSON 报告仍沿用原编辑规则，没有新增可编辑权限。
- 基线真实 Chromium：test-results/queue-draft-attachments/run-5xbhuojk，上传失败后仅附件草稿丢失，刷新为空。
- 最终真实 HTTP+Chromium：packages/web/test-results/queue-draft-attachments/run-4hr5xb67；PID 38848，动态端口 60507，独立 runtime。覆盖仅附件失败/切 Session/reload/重试、上传+服务器文件草稿 PUT/GET、CAS 冲突、清理 IndexedDB、入队、可视节点、原生拖动/删除/取消、保存重读、URL ID、普通草稿隔离。identity.json 记录 serverStopped=true，复查 PID/端口均不存在。
- fakeCLI provider-inputs.jsonl 实读两个文件：client SHA256 fd5072054d0b18635b6bd0adf9f66c8dddf36af4a35d04c7b81e2d8c7b96a8ab；server SHA256 bfc469128cbe5c59bdda98a6fcbca863950873d04ba47259cc373db943f0f394。真实 Worker/adapter/stdin，模型与网络是 deterministic fakeCLI，未验证真实模型理解和实际 provider。
- 最终全量 Vitest 163 文件/1930 项通过，无 skip；.validation/results/vitest-final.log；JUnit .validation/web/results/vitest-final.xml。相关 Python 184 项通过，无 skip，.validation/results/pytest.log 与 pytest.xml；后加前端上传切 Session 测试已包含最终 1930 项。compileall、diff --check 通过。
- owned frozen install Job job_b050e9cf9873d7bd4d2338e9 exit 0。package.json/lock 与工作树逐字节一致，src exact mirror 验证。原 canonical 只读 junction 保留不变；缺 xterm 未写穿修补。owned .validation/web 的 tsc/build 通过，70 compressed assets 校验通过；构建现有 chunk/动态导入警告。
- 改动文件 lint exit 0；全量 lint exit 1，101 errors/18 warnings，与 HEAD 基线诊断逐条相同（.validation/results/lint-comparison.json）。全量回归 Job job_5fc6e23fb425e1900547a502 因上述基线 lint exit 1，Vitest/Python 子步骤 exit 0；终态已核验。未删除或弱化测试。
- 正常 scripts/pre-commit 保留执行；本地 npx shim 仅接受 hook 的 tsc -b --noEmit，并先逐文件核对源码镜像，再使用 owned frozen dependencies 执行同一 tsc 检查，避免触碰缺 xterm 的 canonical junction。
- 待 MA 独立验收；没有合 main/practical、push、重启受保护服务或 release。未验证移动端、跨浏览器 pending File 搬迁及浏览器存储被用户清除后的字节恢复；缺本地字节会明确报错，不能伪装恢复。

## 续作：跨 panel 路由卸载恢复（2026-10-19）

- 工作树/分支同上；MA 已 clean 快进到 main `b085314e`（本报告基线）。用户复现：同 Session 从 Chat 切到 Editor/Jobs/Terminal/插件 Panel 再回 Chat，附件消失。旧交付的切 Session 与 reload 恢复不覆盖真实路由卸载。
- 根因：路由切换会卸载整个 Chat 路由，`InputRow` 的 `draftsBySessionRef`/`recoveryBySessionRef` 随组件销毁；重新挂载时只用 `useSessionStore.inputDrafts` 的纯文本投影，附件/URL 结构与 occurrence 全部丢失；且只要有文本投影就跳过服务器草稿冷读。待上传 File 字节虽按 `localFileId` 存在 IndexedDB，但恢复入口从未把 `localFileId` 重新接回附件列表。
- 修复：`composerDraftStore` 暴露本 tab 已记录的草稿快照（`restorableLocalDraft`，仅 `touchedLocally` 时生效）与最后错误（`localDraftError`）。`InputRow` 在 Session/挂载恢复时按 recovery → 组件内存 → store 快照 → 纯文本 的优先级同步恢复文本、parts 顺序、URL id、附件 occurrence 与 `localFileId`（`fromDraft` 只在该 key 真有 File 时附回字节，绝不造空 File）。卸载清理现在记录后立即 `flushDraft`（coalesced，不阻塞导航），使路由离开也走持久化生命周期的同一边界。
- 迟到的保存/上传仍受既有 epoch/CAS 门禁：卸载后的迟到上传回执被丢弃，恢复为可重试错误态；卸载期间发生的 CAS 冲突保留本地内容并在重新挂载时提示一次。另修正：冲突中相同内容的 `recordDraft` 不再自动重试（避免用已采纳的新 revision 静默覆盖另一 tab 的草稿），只有真正的编辑才清除冲突。
- 新增单测：`InputRow.routeDraft.test.tsx` 用真实 `react-router` Link/Route 卸载-重挂载覆盖 8 项（文本+URL+待上传 File 真实字节、已上传内联节点顺序与 occurrence、Attachment-only、四类 panel 路由往返、切 panel 期间切 Session、迟到上传、迟到 CAS 冲突、发送清空后不回魂且 ack 后才清字节）；`composerDraftStore.test.ts` 覆盖快照/tombstone/冷读不覆盖/CAS 不自动重试/仅 tombstone ack 清理。旧代码基线：这 8 项中 7 项在行为修复前失败（第 8 项为后补的四 panel 用例，同一缺陷路径）。
- 基线真实 Chromium（HEAD 源码构建的 dist）：`packages/web/test-results/queue-draft-attachments/route-pwrpfzdt` 失败于 `inline order/identity must round-trip`，实际 `[]`、期望 `[url:…, att:…]`；identity 记录 `serverStopped=true`。
- 修复后真实 HTTP+Chromium：`packages/web/test-results/queue-draft-attachments/route-rm4d9qch`。真实页面入口导航：桌面 Editor、Jobs 各往返一次，390×844 移动端 Editor 往返一次；每次返回均校验文本、URL id、内联附件 occurrence 顺序、待上传附件可重试、已上传附件不重复成 chip。待上传字节在返回后重试上传，服务端下载 SHA256 `bcf5a03096acb363e6470322bc0365cd5a252ff78d02e1f107509a5064fa2715`；发送后 draft 为 null、本 Session IndexedDB key 清零。fakeCLI `provider-inputs.jsonl` 实读同一条消息 2 个资源：`86ce517026be07fe24f7e1238ddc22ec277799b9487442710406f25073cf9252`（已上传）与 `bcf5a030…`（跨路由后重试的待上传文件）；文本为 URL Markdown + `@"path"` 资源引用，不含 `/api/attachments`。`identity.json` 记录 `serverStopped=true`，端口 `51675` 可重新 bind、PID 37912 不存在。
- 旧队列可视编辑/送达链路回归仍通过：`packages/web/test-results/queue-draft-attachments/run-3yejrhv4`（队列编辑原 ID/revision、原生拖动删除取消、普通草稿隔离、reload/重试、CAS、IndexedDB 清理、入队与双文件 hash）；`serverStopped=true`，端口 `59627` 可重新 bind。
- 最终全量 Vitest（owned frozen deps，`--no-cache`）：168 文件 / 2016 项通过，0 失败 0 skip；`.validation/results/vitest-final.log` 与 `vitest-final.xml`。`tsc -b`、vite build、72 compressed assets 校验通过；`.validation/web` 为 `packages/web` 的 exact source mirror（`diff -r` 一致），重建的 `packages/web/dist` 由该 mirror 产出（gitignored）。
- Lint：改动文件（含新 e2e 脚本）exit 0；全量 lint 仍为仓库既有 124 errors/19 warnings，按路径归一化后与 HEAD `b085314e` 基线逐条相同（`.validation/results/lint-baseline.json` vs `lint-route-final.json`，new/gone 均为空）。未新增、未弱化、未删除测试。
- 未验证：真实 Terminal/插件 Panel 的 Chromium 往返（单测已按同一条卸载路径覆盖四类路由，Chromium 覆盖 Editor/Jobs 与移动端 Editor）；跨浏览器与其他移动尺寸；多客户端并发冲突的真实双 tab 操作；用户手动清除浏览器存储后的字节恢复（沿用明确报错，不伪装恢复）。无 Python 源码改动，未跑完整 pytest；相关服务端协议由上述真实 HTTP e2e 覆盖。未合 main/practical、未 push、未重启 8767/8768。
