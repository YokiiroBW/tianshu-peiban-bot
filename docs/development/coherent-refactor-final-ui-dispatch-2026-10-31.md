# 前端收口与保留功能核对分派

本卡延续用户已授权的全队列实施。所有实现仍由现有 gpt-6.1-sol / xhigh 子智能体完成；主协调者串行集成。所有源码、装配与部署材料到齐后才开始 CR19，不提前运行测试、typecheck、build 或浏览器。

## 正式交接基线

前端共享基础为 `7660945`（集成 `14ab3f2`）。它包含会话/CSRF、明确读写客户端、原命令身份与 pending 查询、真实对话与 canonical 上传引用。后续 selector 增量仍由 frontend 唯一发布，其他写者消费正式提交，不复制自己的 fetch/session。

## 写入范围

| 写者 | 顺序与独占范围 |
|---|---|
| frontend | 继续 B 日记/写作取消、表达恢复；前端 `features/life`、`multimodal`、`room`、`companion`、`home`、`workbench`、`flow-graph`，以及 Channels/Weather 等仍归它的设置页面。唯一拥有 `web/src/api/**`、`app/**`（含实际模块注册 `app/modules.ts`）、公共组件、路由和前端 manifest/lock。 |
| capabilities | 完成 channels、QWeather、AssetLibrary 与 Chat Audit；`web/src/features/plugins/**`、`media/**`、`infrastructure/**`、`resources/**`。2026-10-31 21:38 起另接 foundation 已确认未修改并冻结的 `people/**`、`memory/**`；设置页仅 `ProviderManager.tsx`、`ProviderEditors.tsx`、`ProviderModelsPanel.tsx`、`ModelFunctionsPanel.tsx`、`providerApi.ts`、`providerTypes.ts`、`providerPresets.ts`、`models.css`。2026-10-31 22:03 起再接已确认未开始的 `capabilities/projects/**`、其业务测试、`web/src/features/knowledge/**` 与 `projects/**`。不操作独立产品私有库。 |
| foundation_models | root composition、CR20 和旧实现退出；Models/Memory/Source 公共 API；设置 `AccessPanel.tsx`、`access.css`。其余原 UI 白名单及尚未开始的 Knowledge/Projects 后端与 UI 已按上述两次明确交接转 capabilities；总迁移和装配仍由 foundation 唯一维护。 |

项目/知识实现现在统一由 capabilities 所有者维护 `src/tianshu/capabilities/projects/**` 及上述两个页面目录，A 已确认没有该范围未提交修改并停止写入。C 发布正式模块/迁移/CLI-MCP 入口后，A 接到唯一根装配与迁移注册；Source/Memory/Models 公共口继续找 A。B 唯一维护共享前端客户端、路由与锁，消费者不复制第二套会话或 fetch。

## 保留清单的实际核对

CR01 的“新归属”是施工责任，不能当实现证据。U17–18、U43–47、U69–74、U89–92 应逐项给出冻结有效入口、当前真实消费者及仍未实现的历史状态。原 Knowledge 的目录导入、资料/章节阅读、研究笔记、续作与经验，以及 AssetLibrary 资源页等，按实际有效代码迁入并接通前端；不以 Source/Actions 已有通用端口代替业务实现。原本未实现或未挂载的能力如实保留其状态，不新增占位成功。

新实现共用 SourceAuthority、唯一 PG、Actions/Receipt、Procrastinate、ContextAssembler 与 FileStore。原独立 Core、旧内部 HTTP 信封、旧数据库兼容层不重新进入产品。

## 联合接线约束

- `actor_id/person_id/conversation_id` 由共享客户端统一放入 query，经 Identity 派生可信场景；请求体同名字段必须一致，不授予范围。C 从业务参数中剥离选择器，具体执行权限仍由 handler 定义检查，普通能力调用不额外要求配置管理权限。
- 未知命令保留原 `client_request_id/action_id/attempt_id`；页面查询原回执，不因超时重复提交。受理、处理、成功、失败和未知分别展示。
- 新读写操作名由页面 owner 提交给 frontend 注册；实际 HTTP 方法、字段、revision 与分页以当前已集成模块为准。
- 页面的有效布局、主题、资源继续复用；删除被替代的旧 DTO、分值/阶段配置、跨产品包装和无消费者分支。不得只把旧页面套到新应用壳中。

本卡仅规定已授权工作边界；没有运行验收或生产操作。

技能页使用唯一 Catalog 的角色策略与清单来源，不再保留第二个 SkillsRegistry。已在运行中的原生模型任务只有显式取消才取消；关闭能力阻止新调用和未开始的 Action，不把该开关描述成已取消供应商在途工作。

## 用户最新角色绑定约定

2026-10-31 用户明确：一个 QQ 账号对应一个角色，不按群选择角色。连接的 `actor_id` 是该账号实际唯一角色；前端 FlowGraph 与 Channels 不提供逐群角色切换。各群保留独立的回复方式、关键词、主动参与和来源范围配置。本轮为保留历史功能提出的逐群角色补丁已撤销，未进入集成树；不将该功能列为待办或验收缺口。
