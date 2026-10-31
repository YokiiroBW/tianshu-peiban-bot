# 天枢 Web

React/Vite 前端，属于根统一产品；运行时只访问当前站点 `/api/v1/`，不依赖旧产品源码、预览 mock 或外部旧站页面。插件自定义页面继续使用现有隔离 iframe 与限定 MessageChannel bridge。

## 开发入口

在 `web/` 下使用 `npm ci`、`npm run dev`。`npm run typecheck`、`npm run build`、`npm run test:e2e` 是集中验收入口；本次开发阶段按用户要求尚未运行。

`src/app/modules.ts` 维护当前导航及语义地址；`src/api` 是唯一 HTTP、会话、命令和作用域请求基础。`src/features` 按业务保留有效页面及其视觉；新增语音、图包、三引擎在 `features/multimodal`，基础组件与素材分别在 `src/components`、`public`。

HTTP 错误、SessionView、CommandResult、Page、Receipt、ArtifactRef 与根 Python 正式定义对应。读取不补造零值，写入携带稳定 client_request_id 和对象 expected_revision；未知结果不自动重发。React 状态只保存编辑草稿或明确作用域的读取快照，关系、日程与权限仍由服务端决定。

阶段接线和尚待核心契约的范围见 `docs/handoffs/CR18-frontend.md`。历史前端用例已迁入，涉及旧后端 fixture 的用例仍需在核心 API 定稿后按实际新字段更新；不得用其存在宣称验收通过。
