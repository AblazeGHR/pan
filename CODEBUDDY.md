# Pan 开发约定

> **前端提示：Vanilla 前端已退役并归档。React 是唯一前端。**
> 进行前端开发或 bug 修复时，只修改 React 源码。

## 前端源码

- React 源码位于 `packages/web/src/`，是所有新功能和修复的实现依据。修改后在 `packages/web/` 执行 `pnpm build`。
- React 源码位于 `packages/web/src/`，构建产物为 gitignored 的 `packages/web/dist/`，禁止直接编辑产物。

## 路由状态

- 默认入口使用 React（`/` 重定向到 `/react/`）。
- 根路径稳定跳转到 `/react/`；若 React 构建缺失，服务返回清晰的 503，不回退到其他前端。

后端 API/WebSocket 按 React 前端演进。

## 校验

启用仓库 hook（`git config core.hooksPath scripts`）后，暂存 React 源码变更会校验 React TypeScript。常规测试命令为 `python -m pytest tests/ -q`。
