# Pan 插件面板与数据目录协议 v1

本协议扩展 `pan-plugin/v1`，复用 [能力协议](Pan-Plugin-Capability-v1.md)。现有前端术语是 View Entry（Chat / Editor / Jobs / Terminal）；插件声明使用 `panels`，宿主把它转换为同一导航中的 View Entry。

## 注册与归属

插件仍在 manifest 的 `plugins` 注册。`id` 为永久身份；改显示名称不改数据目录。插件 ID 不得占用 `pan`、`pan-tools`，不得通过别名冒充其他插件。入口路径固定为 `/plugins/<plugin-id>/<panel-id>`，生产 SPA 自带 `/react` 前缀。一个插件内 panel ID 唯一，重名注册报错。

```json
{
  "protocol": "pan-plugin/v1",
  "id": "cc-switch-bridge",
  "display_name": "CC Switch Bridge",
  "source": {"kind": "pan_api", "capabilities_file": "capabilities.json"},
  "data": {"version": 1},
  "agent_capabilities": ["cc-switch-bridge.list", "cc-switch-bridge.switch",
                         "cc-switch-bridge.quota", "cc-switch-bridge.auto_switch"],
  "panels": [{
    "id": "accounts",
    "title": "CC Switch",
    "renderer": "account-switcher/v1",
    "actions": {
      "list": {"capability": "cc-switch-bridge.panel_list", "effect": "read"},
      "quota": {"capability": "cc-switch-bridge.panel_quota", "effect": "read"},
      "switch": {"capability": "cc-switch-bridge.panel_switch", "effect": "write_external"},
      "config": {"capability": "cc-switch-bridge.panel_config", "effect": "write_external"},
      "auto_switch": {"capability": "cc-switch-bridge.auto_switch", "effect": "write_external"},
      "default_account": {"capability": "cc-switch-bridge.default_account", "effect": "write_external"}
    }
  }]
}
```

v1 提供宿主维护的 `account-switcher/v1` renderer。插件提供声明和数据，不向页面注入任意 JS、HTML、iframe 或远程组件；增加其他界面类型时新增带版本 renderer，并沿用同一注册流程。`web-app/v1` 是本协议的后续版本化扩展：插件声明本地构建命令、输出目录和入口，宿主构建、启停并从 Pan 自有构建副本加载静态资源，界面在 iframe 中运行；详见[本地自由 Panel](../plugins/PANELS.md)。插件行为由本机可信的既有 capability provider 执行；这是扩展接口，不是 OS 沙箱或新鉴权系统。

面板是全局入口，不依赖当前 Session，不为它创建持久 Session、Worker、任务或 MA 身份。宿主为面板持有内部调用上下文和独立 provider，退出时关闭。Session MCP 勾选状态仅影响 Agent 工具，不能因为取消 Session 的插件工具而关闭全局面板。插件移出 manifest 后入口消失，直达旧 URL 显示已卸载，数据保留；旧页面动作由服务端重新校验，不继续调用已删除的声明。

## 面板能力与 Agent 工具

`panels.actions` 只能引用本插件的能力，API 来源还必须引用实际声明的 ID。面板不能提交任意能力名、命令、URL、数据库路径或数据目录。动作输入继续按 capability JSON Schema 校验。

`agent_capabilities` 是可选白名单，v1 适用于 API 来源，在独立 API MCP shim、Pan 原生投影和 gateway 中一致过滤。省略时保持已有全部暴露行为。标准 MCP 的独立模式是直连，不能承诺宿主替它过滤，因此不接受此字段；希望过滤时应提供 API 适配。面板通过自身声明可使用白名单之外的内部能力，但 Agent 不能绕过发现直接调用这些能力。

CC Switch Bridge 区分两条切换路径：**手动切换**只属于面板（用户显式点击），**非手动切换**属于所有 Agent/自动化路径。存储上只有一个按配置的标记 `nonManualDisabled`，不区分 MCP 与自动换号两种禁用。

对 Agent 暴露四个工具：

- `cc-switch-bridge.list`：`app` 必填。返回配置与托管账号；标记了 `nonManualDisabled` 的配置整行不出现，不返回其名称、origin、quota、notes、category 等任何字段，也不用占位行代替。
- `cc-switch-bridge.quota`：`app` 必填，`id` 选填；省略 id 返回可用配置/账号 ID、当前选择与已有缓存，不自动查询所有远程额度；指定 id 查询该配置额度。被标记配置同样不出现，按 id 查询按“配置已不存在”拒绝。
- `cc-switch-bridge.switch`：非手动切换。`app`、`id`、`expectedCurrentId` 必填，`kind` 为 `provider`（默认）或 `default_account`。被标记配置拒绝切换，不回退到其它配置，未确认结果不重放。
- `cc-switch-bridge.auto_switch`：`app`、`id`、`expectedCurrentId` 必填。未来自动换号机制只允许走这个 API；它与 `switch` 共用同一非手动门禁，不静默改用其它配置，也不重放不确定结果。Pan 原生工具名分别为 `pan_cc_switch_bridge_list`、`pan_cc_switch_bridge_quota`、`pan_cc_switch_bridge_switch`、`pan_cc_switch_bridge_auto_switch`。

面板通过白名单之外的内部能力读取并管理全部配置：`panel_list`（含每个配置的 `nonManualDisabled` 与应用级 `revision`）、`panel_quota`、`panel_switch`（手动切换，允许目标为已标记配置）、`panel_config`（保存标记）、`default_account`。这些面板能力不暴露给 Agent；Agent 也不能绕过发现直接调用。

## 数据目录规则

宿主分配 `<Pan data>/plugins/<plugin-id>/`，用 `PAN_PLUGIN_DATA_DIR` 传给托管 stdio provider。插件不能通过声明指定绝对路径、`..` 或其他插件目录。宿主检查路径及 junction/symlink 解析后的归属。保留的 `.pan-plugin.json` 记录 owner 和 dataVersion，版本或 owner 不符时拒绝执行；本批不做隐式迁移或删除。

插件数据与外部产品数据分开管理。插件可以保存设置、展示缓存、运行状态；不得把“允许有 Pan data 目录”解释为应复制原产品数据库或凭据。卸载不自动删数据。清理、迁移和格式升级以后单独提供明确流程。该路径约束不限制可信本机程序的 OS 权限，也不声称完整隔离任意第三方代码。

CC Switch Bridge 的目录包含可选 `settings.json`、`provider-flags.json`、`quota-<app>.json` 和宿主持有的 `panel-requests.json`。`settings.json` 可设 `{"executable":"D:/path/to/cc-switch.exe"}`；否则优先 `PAN_CC_SWITCH_CLI`，再查 PATH 的 `cc-switch`。配置值只能是可执行文件，不是 shell 命令片段。`provider-flags.json` 只由本插件读写，按 `app` 分应用保存 `revision/updatedAt` 与各配置的 `nonManualDisabled`，原子替换写入，并复用 `.bridge.lock` 串行化读改写；它不影响 cc-switch CLI 的数据库或行为。不复制 cc-switch 数据库、auth 文件或 token；展示使用字段白名单，CLI 的原始错误输出不回传页面。

## 操作接口与结果不确定

- `GET /api/plugins/panels` 返回声明及入口，不执行 CLI 或查询额度。
- `POST /api/plugins/{plugin}/panels/{panel}/actions/{action}` 接受 `{"arguments": {...}, "requestId": "..."}`。面板状态只在该显式操作中读取；宿主使用既有 API/stdio provider 执行业务，不新增本地 HTTP 执行服务。这里的 HTTP 是浏览器访问已有 Pan Web API。
- `write_external` 要求 16–80 位稳定 requestId。宿主先落盘 pending，再执行；同 ID 同参数返回已有结果，不重放；同 ID 不同参数拒绝。未确认的 pending 或失败记录保留。最多 128 条记录，仅淘汰已确认成功的旧记录；全部不确定时拒绝接纳新切换。此为单 Pan 实例的有界重放防护，不承诺跨实例并发或永久 exactly-once。
- HTTP 断开不会撤销已接收的切换。结果不明时界面禁用后续切换，先刷新实际状态；不因为超时重发。重新进入页面也先读取状态。MCP 调用不经过面板 requestId 台账，调用者同样不得自动重放未知结果。
- CC Switch Bridge 的各 Panel/MCP helper 通过插件目录内的 `.bridge.lock` 串行访问 CLI，等待锁和一组前后查询共用总预算，避免多个 bridge 请求交叉切换。切换前重读当前 ID，与 `expectedCurrentId` 比较；切换后再次查询，未确认则返回错误。不按列表序号切换。CLI 会把纯数字账号 ID 解析为序号，因此 bridge 拒绝这类默认账号 ID。原始 cc-switch 不使用此 bridge 锁，其外部同时更改仍可能落在检查与切换之间，本批不声称跨产品事务锁。

## account-switcher/v1 数据契约

`list({app})` 返回 `AccountSnapshot`：`app/currentProviderId/observedAt/providers/accounts/accountError`。providers 有 `id/name/origin/isCurrent/quota`，accounts 有 `id/login/isDefault`。支持 Codex、Claude、Gemini；仅 Codex 展示 ChatGPT 托管账号。面板读取的 `panel_list` 额外返回应用级 `revision/updatedAt`，每个 provider 额外返回 `nonManualDisabled`，并包含被隐藏的配置。

`quota({app,id})` 返回 `{app,id,quota}`。额度使用 `status/available/queriedAt/message/tiers/balances`；tiers 包含 `name/usedPercent/resetsAt`，余额包含 `planName/remaining/used/total/unit`。百分比表示已用，未知字段不伪装成零；所有数据标明缓存时间，不后台轮询。`switch` 和 `default_account` 返回重新读取的完整快照。

`config({app,id,nonManualDisabled,expectedRevision})` 保存单个配置的非手动门禁，返回重新读取的面板快照。`expectedRevision` 来自最近一次面板读取；版本已变化时返回 `settings_conflict`，界面显式提示并重新读取，不覆盖其它窗口的写入。`nonManualDisabled` 只影响 Agent 的 list/quota 可见性与非手动切换；面板 `panel_switch` 仍是手动路径，允许用户显式切换到已标记配置。

初期面板支持应用切换、搜索配置/账号、当前状态、额度手动查询、缓存与重置时间、配置启用确认、非手动门禁开关、设置默认账号、错误和未知结果处理。新增/编辑/删除账号、登录、兑换重置额度、启动/停止 cc-switch 代理及重启 Pan Worker 不在此桥接实现内。
