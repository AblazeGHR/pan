# Pan 开发约定

> **前端提示：Vanilla/legacy 前端已弃用。React 是当前唯一维护和推荐的前端。**
> 进行前端开发或 bug 修复时，默认修改 React 源码；只有任务明确要求维护 legacy 时才触碰 Vanilla。

## 前端源码

- React 源码位于 `packages/web/src/`，是所有新功能和修复的实现依据。修改后在 `packages/web/` 执行 `pnpm build`。
- Vanilla legacy 源码仅作兼容后备，位于 `packages/web/ts/app.ts`；它不再作为功能设计或 API 行为的依据。若任务明确要求维护它，修改源文件后从项目根目录执行 `npx tsc`。
- `packages/web/static/js/app.js` 与 `packages/web/dist/` 都是 gitignored 编译产物，禁止直接编辑。

## 路由状态

- 默认入口使用 React（`/` 重定向到 `/react/`）。
- `/vanilla` 仍保留为 legacy fallback；`frontend=legacy` 仅用于明确的兼容场景，不是推荐配置。

后端 API/WebSocket 按 React 优先演进；不要为了保持 legacy 行为而限制新实现。只有在明确维护 legacy 时，才让 `app.ts` 跟随接口变化。

## 校验

启用仓库 hook（`git config core.hooksPath scripts`）后，暂存前端源码变更会分别校验 legacy TypeScript 与 React 构建。常规测试命令为 `python -m pytest tests/ -q`。
