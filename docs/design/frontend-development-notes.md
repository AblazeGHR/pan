# Pan 前端开发笔记

## Modal 尺寸 token

删除含子 Session 的确认弹窗曾经显示成一条垂直缝。直接把调用方从 `size="sm"` 改成 `size="md"` 不能彻底修复，因为问题不在调用方选择了哪个常规尺寸，而在通用 Modal 尺寸 token 的生成结果。

React 前端使用 Tailwind v4，`packages/web/src/index.css` 通过 `@theme` 定义了项目 spacing token，其中 `--spacing-lg: 1.5rem`。当 Modal 的 `md` 映射使用 `max-w-lg` 时，Tailwind v4 会按项目主题命名空间生成 `.max-w-lg { max-width: var(--spacing-lg) }`，实际最大宽度只有 `1.5rem`，删除确认内容因此被压成垂直缝。

`ImportModal` 当时正常，是因为它使用的是 `size="lg"`，映射到 `max-w-2xl`。`2xl` 没有与项目的 `--spacing-lg` 发生同名覆盖，所以仍生成预期的宽度。

以后给 Modal 这类组件维护命名尺寸时，不要使用可能落入项目 `@theme --spacing-*` 命名空间的 `max-w-sm`、`max-w-md`、`max-w-lg`、`max-w-xl` 作为契约。优先使用明确宽度的 arbitrary value，例如 `max-w-[32rem]`，或者使用不会被项目 token 改写的专用 CSS 变量/类名。回归测试应检查：

- `sm`、`md`、`lg`、`xl` 仍保持递增尺寸层级；
- `md` 不再包含 `max-w-lg` 或依赖 `var(--spacing-lg)`；
- 关键调用方如 `SessionDeleteModal` 继续使用通用 `size="md"`，并渲染到明确的 `max-w-[32rem]`；
- 移动端 bottom-sheet/fullscreen 调用方保留自己的 `max-md:max-w-none` 覆盖。
