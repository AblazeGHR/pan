# LLM 信任与安全搁置清单

> **搁置声明（2026-08-23 用户指示）**：以下所有 LLM 信任/安全相关项**统一搁置，不再主动提及或推进**，直到用户主动提起。
> 状态：⚠️ 搁置（存档备查，不做不催）

## 搁置项

| # | 项 | 说明 | 原状态 |
|---|----|------|--------|
| S1 | **workdir 放行策略** | `_ALLOWED_WORKDIR_ROOTS=None` 对绝对路径直接放行，结合 `/api/fs/*` 可读写删任意目录——鉴权推迟后唯一实际安全边界 | D1（优先决策） |
| S2 | **manifest 信任模型** | `_parse_mcp_server` 不校验 command/args/env/cwd，manifest 可声明任意可执行文件被 cbc 子进程执行 | D2 |
| S3 | **API key 落库改 hash** | `embedder.py` 用 `api_key[-8:]` 作 provider_key（明文截断），应改 sha256（旧缓存一次性失效） | D12 |
| S4 | **鉴权设计** | API 无鉴权 + 绑 loopback 是既定姿态（0 鉴权推迟） | D0（已办/既定姿态） |
| S5 | **MCP 隔离细节** | 隔离的权限边界、claim 释放时机、与报告订阅的交互 | L5 |
| S6 | **冷启动 profile 幻觉调用** | `coc-keeper-coldstart` profile 断言"已连接 RuleWhisper MCP"，依赖 config.json 的 `plugin_manifests` 配置；未配置时会幻觉调用 MCP 工具 | 阶段计划注记 |

## 搁置原因
用户指示：这些问题"统一收集后另外存文档，标志搁置不再提起，直到我主动提及"。

## 恢复条件
用户主动提及安全/信任相关话题时，从此文档取用。
