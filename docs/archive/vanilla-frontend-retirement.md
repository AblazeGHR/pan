# Vanilla 前端退役记录

- **日期**：2026-09-11
- **状态**：已归档，不再执行维护

## 原因

React 已承担唯一持续开发的 Dashboard 实现。保留一个独立 Vanilla 入口、旧构建链和
前端模式开关会使路由、配置、测试和文档继续分叉，也会让 React 构建缺失时静默回退到
无法维护的代码。此次变更将 Web 前端收敛为 React-only，并让构建缺失显式失败。

## 删除文件

- `packages/web/ts/app.ts`
- `packages/web/index.html`
- `packages/web/mobile.html`
- `packages/web/static/css/styles.css`
- 根目录 `tsconfig.json`
- `packages/web/static/js/app.js` 和 `packages/web/static/js/app.js.map`（若本地生成）

## 路由与配置变化

- 根路径 `/` 固定以 307 跳转到 `/react/`。
- `/react/` 继续挂载 `packages/web/dist/`；dist 缺失时根路径和 `/react/` 返回清晰的 503，
  不再回退到旧 HTML。
- `/vanilla` 路由已移除。
- `frontend` 配置及 `coexist`、`react`、`legacy` 模式已移除；配置 reload、React 设置 UI、
  setup 脚本、pre-commit 和相关测试不再处理该字段。
- 普通数据/协议迁移中名为 legacy 的兼容逻辑不属于本次退役范围，继续保留。

## 迁移方式

1. 删除 `config.json` 中的 `frontend` 字段（未知字段不会再影响前端路由）。
2. 在 `packages/web/` 执行 `pnpm build`。
3. 访问 `http://127.0.0.1:{port}` 或 `http://127.0.0.1:{port}/react/`。

历史设计、调查和实现文档中的旧前端描述仅作为当时事实保留；当前开发以
`packages/web/src/` 和 React Vite 配置为准。
