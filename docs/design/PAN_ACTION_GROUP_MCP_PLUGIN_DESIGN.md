# Action Group MCP Plugin Design

## Status

设计已冻结。本文记录产品边界；后续只在用户明确提醒后继续实现或修改。

## 核心模型

### Action

Action 是存储在 Pan data 中的独立操作定义。它描述一个可调用操作的名称、输入字段、执行方式、效果等级和输出格式。Action 本身不是独立 MCP 插件，也不单独进入 Session 的 MCP 配置。

同一个 Action 可以被多个 Group 引用。Action 的定义只保存一份，Group 只保存引用关系。

### Group

Group 是用户选择若干 Action 后形成的打包单位。Group 的 Action 集合决定它构建后暴露的 MCP 工具集合。

例如：

```text
Group A = [action-a, action-b, action-c]
Group B = [action-b, action-c, action-d]
```

Action 可以同时属于多个 Group。

### Build

用户对 Group 执行 build 后，Pan 根据 Group 当前引用的 Action 定义生成并注册一个 MCP 类插件：

- 插件名使用 Group 名称或其稳定插件 ID；
- 生成对应的 MCP 配置文件和注册信息；
- Group 作为一个整体出现在 Session 的 MCP 管理中；
- Agent 只看到当前 Session 启用的 Group 所暴露的 Action。

Action 的数据是源定义，Group 的 MCP 插件是构建产物。两者不混为同一层。

### Session 启用

不同 Session 可以启用不同 Group：

```text
MA → Group A
TA → Group B
```

Group 的 MCP 注册、Session 绑定、Worker 生命周期、三种 MCP 模式和延迟生效复用通用插件管理逻辑。Action 不自行实现 MCP 注入、Worker 重启或 Session 生命周期。

## Panel 职责

Action Panel 属于 Pan 核心，负责：

- 创建、查看、修改和禁用 Action 数据；
- 创建和修改 Group；
- 在 Group 中选择 Action；
- 执行 build 和 unbuild；
- 查看 Group 当前暴露的 Action；
- 管理 Session 对 Group 的启用关系；
- 显示插件版本、注册状态和运行阻塞原因。

具体 Action 的业务定义仍保存在 Pan data 中，不要求为每个 Action 安装独立插件。

## Unbuild 约束

unbuild 会取消 Group 的 MCP 注册并清理对应 MCP 文件。执行前必须检查所有启用该 Group 的 Session：

- 如果存在 running Session，拒绝 unbuild；
- 错误中列出阻塞的 Session 名称和 ID；
- 只有没有 running Session 启用该 Group 时才允许 unbuild；
- idle Session 即使仍启用该 Group，也不构成阻塞；
- 注销和配置引用更新必须保持一致，不能留下指向不存在插件的有效配置。

running 判断使用 Worker 的真实运行状态，不能只依据 Session 是否启用、历史状态或最近一次任务结果。

示例错误：

```text
Cannot unbuild action group "Group A":
it is enabled by running sessions:
- MA: ses_xxx
- TA: ses_yyy
Stop or disable the group for these sessions first.
```

## 权限与安全边界

Action 的效果等级、输入校验、资源范围和执行权限由 Pan 的 Action 抽象与插件管理层统一校验。Group 只是 Action 的打包和 MCP 暴露边界，不因为收纳 Action 而自动提升权限。

Agent 可以创建或修改 Action 草稿，但 Action 的验证、启用和高风险执行仍遵守现有权限与审批策略。正在运行的 Session 使用已锁定的插件/Action 版本；修改或重新 build 不改变当前执行中的版本。

## 生命周期

```text
创建 Action 数据
  → 创建 Group 并选择 Action
  → build Group
  → 注册 Group MCP 插件
  → 在 Session 中启用 Group
  → Worker 按插件管理策略加载
  → Agent 使用 Group 暴露的 Action
  → 无 running Session 阻塞时 unbuild
  → 注销 Group MCP 并清理配置文件
```

本文冻结的是产品模型和生命周期边界。文件格式、注册 API、构建目录和具体错误码属于后续实现细节，除非用户重新打开设计，不改变上述模型。
