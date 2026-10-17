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
