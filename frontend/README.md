# Frontend 维护说明

## 模块结构

- `static/app.js`
  入口协调层。负责页面启动、定时刷新、跨模块流程编排，以及 DOM 事件绑定。
- `static/settings.js`
  负责登录配置、频道配置、目录配置的加载、渲染与表单辅助方法。
- `static/files.js`
  负责文件浏览区的数据加载、目录树、筛选、统计、预览与文件选择。
- `static/uploads.js`
  负责上传任务区的数据加载、状态分组、批量操作、详情弹窗与任务选择。
- `static/store.js`
  前端共享状态单例，集中存放 settings/files/uploads/ui 等页面状态。
- `static/constants.js`
  页面使用的状态标签、排序顺序、重试状态集合、防抖时间等常量。
- `static/utils.js`
  通用工具方法，包括 API 请求、Toast、反馈面板、Skeleton、格式化函数等。

## 状态归属

- `state.settings` / `state.login`
  设置与登录态，仅由 `settings.js` 主导渲染。
- `state.files` / `state.selectedFiles` / `state.currentSubdir`
  文件浏览状态，由 `files.js` 负责解释和渲染。
- `state.uploads` / `state.uploadStats` / `state.selectedUploadTaskIds`
  上传任务状态，由 `uploads.js` 负责解释和渲染。
- `state.ui.loading.*` / `state.ui.errors.*`
  页面四态入口，所有模块都应通过这组字段驱动 Loading / Error / Empty 展示。

## 当前约定

- 不改接口契约，前端只消费现有 `/api/*` 接口。
- 页面刷新入口统一走 `refreshDashboard()`，避免多个地方各自拼装刷新流程。
- 搜索框统一做防抖，避免输入时频繁重渲染。
- 空态、错误态、骨架屏都通过 `setPanelFeedback()` 和 Skeleton helper 统一输出。
- 任务与文件的筛选逻辑分别封装在各自模块内，不要在入口层重复判断。

## 后续扩展建议

- 如果继续拆分，可以优先把 `app.js` 的事件绑定抽成 `events.js`。
- 如果 DOM 查询继续增多，可以新增轻量 `dom.js` 做节点缓存，但先以可读性优先。
- 如果任务详情或预览继续增强，可以单独抽成 `dialogs.js`，保持主模块聚焦列表逻辑。

## 验证方式

- 修改前端脚本后，至少执行一次：

```powershell
node --check frontend/static/app.js
node --check frontend/static/settings.js
node --check frontend/static/files.js
node --check frontend/static/uploads.js
```

- 手工检查以下页面状态：
  - 初始加载时设置区、文件区、任务区是否正常显示骨架或加载提示
  - 空目录、空任务、错误请求时是否出现对应空态/错误态与重试入口
  - 文件筛选、目录切换、任务筛选、批量选择是否仍按原逻辑工作
  - 预览弹窗、任务详情弹窗、复制按钮是否正常
