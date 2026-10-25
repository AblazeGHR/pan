# Locker 附着提醒卡与未读定位

提醒卡仍只由经过 User authorization 的 `POST /api/reminding-cards` 创建。新增可选字段：

```json
{"parent": {"type": "locker_message", "id": "lmsg_..."}}
```

`type` 只允许 `locker_message | interaction_card | user_card`，`id` 遵循现有 Id 契约。
缺省或 `null` 表示不附着。查询与 answer 返回的卡片均包含 `parent`，旧卡返回 `null`。
原有 MCP `reminding_card(operation="list_pending")` 直接返回该字段；MCP 没有创建入口。

父项必须在同一 recipient、owner Locker 内存在且类型匹配；终态父项也允许附着。
父关系在创建后不可修改，requestKey 重试仍执行原内容幂等契约。创建沿祖先链检查自身与重复节点，拒绝环。
schema 升级仅新增可空 `parent_type`、`parent_id` 列及查询索引，不重写历史卡片。

删除任一父项，在同一 SQLite BEGIN IMMEDIATE 事务内迭代删除全部提醒卡后代、写入 tombstone、清理创建通知与过期提醒。
创建与删除串行进入事务，避免产生悬空子项。回答、拒绝、无需回答与 read 操作均不传播给子项。
已创建提醒任务的正常 Queue 语义保持原有行为；已交付的消息不会因删除而撤回。

顶层项目按 createdAt、稳定 id 排序；提醒卡只保留 createdAt 的原卡与答案，不再按 answeredAt 复制。
附着卡按同父 createdAt、id 升序插在父项后，以前序遍历展平，所有层级具有同一左边界。
锁链图标、虚线及父项 id 表达关系。遍历用显式栈和 visited 集合，无递归、无深度上限；损坏数据中的重复/环仅渲染一次。
父项意外缺失的旧异常数据保留可见父链接；新创建不存在或已删父项会明确失败。
原有提醒卡总量限制仍然生效，嵌套深度不另设限制。

进入 Locker 或切换 scope 时，按顶层实际时间排序定位第一条未读，并在它之前绘制分界线。
附着提醒卡独立计入未读，但不参与时间排序或移动分界线；若只有附着卡未读，默认到底且不画时间线未读线。
read 操作后，分界线随顶层当前未读更新；不自动改变用户正在阅读的位置。
保留现有全量 API 读取方式，展示窗口每页 50 个时间线顶层项目，附着后代不占页槽。
初始一页最多再向前展开五页，仍不可达时停在最早已加载项并显示提示，用户可手动继续加载。
离底部超过 48px 显示与 Chat 一致的悬浮按钮，点击平滑到底。

此功能不新增平台专用能力、平台导入或进程/服务操作。
