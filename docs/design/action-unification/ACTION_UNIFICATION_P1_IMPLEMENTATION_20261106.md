# Action 统一 P1 实现边界

本候选在 P0 `302f15c7395d81102634055e55f9edb122ef9594` 上实现唯一 canonical Action 定义与版本存储，继续复用 `FinalService` 的 SQLite document store、owner fence、operation receipt 和现有 `UserAuth` Action grant。

## Canonical 存储

- `final_documents(kind='action', id)` 是逻辑 Action 头，保留稳定 `actionId`、归属、草稿/发布/退役状态、管理 revision 和启用门禁。
- `final_action_versions` 保存每个 revision 的不可变 definition、digest、发布时间和状态；草稿编辑只追加新快照，不覆盖已发布内容。
- `final_action_lifecycle` 保存版本级 enable/disable/retire 状态，内容 digest 不随生命周期变化。
- `final_action_aliases` 保存显式旧 ID → 固定 `{id, revision, digest}` 的映射、source、owner 和创建时间。

`latest` 只按 canonical 发布序列解析最新未退役版本；Job 引用通过固定 ref 读取历史版本，因此逻辑头携带新草稿时，已有旧 ref 仍可校验。版本详情允许读取 disabled/retired 历史版本，但不可作为新的可执行引用。

Action definition 的 `execution.config` 保持现有对象 schema。P1 因而可以持久保存 `process.shell`、外部脚本路径/解释器等旧 descriptor；本片不接入其执行。

## HTTP 与兼容边界

`/api/jobs/actions` 提供逻辑列表、草稿 create/update、版本与固定 ref 读取、publish/enable/disable/retire、alias 注册/读取。写请求走原 `final_operations` requestKey/CAS/owner 事务。

`packages/actions` 文件型 Action/Group 目录在本片仍作为历史兼容宿主保留，未自动迁移或删除其文件；canonical 目录不读取该 registry。显式 alias 接口为后续 dry-run/apply 映射提供稳定 ID/version/digest 基础。Group 发布、直接 invoke、Job 创建和 Trigger target 仍留在 P2–P4。

system Action 只能由受信产品注册；普通用户不能通过 draft/publish 或生命周期操作取得 `manageSystemActions` 保护能力。
