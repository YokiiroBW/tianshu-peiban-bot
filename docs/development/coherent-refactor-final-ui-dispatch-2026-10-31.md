# 前端收口与保留功能核对分派

本卡延续用户已授权的全队列实施。所有实现仍由现有 gpt-6.1-sol / xhigh 子智能体完成；主协调者串行集成。所有源码、装配与部署材料到齐后才开始 CR19，不提前运行测试、typecheck、build 或浏览器。

## 正式交接基线

前端共享基础为 `7660945`（集成 `14ab3f2`）。它包含会话/CSRF、明确读写客户端、原命令身份与 pending 查询、真实对话与 canonical 上传引用。后续 selector 增量仍由 frontend 唯一发布，其他写者消费正式提交，不复制自己的 fetch/session。

## 写入范围

| 写者 | 顺序与独占范围 |
|---|---|
| frontend | 继续 B 日记/写作取消、表达恢复；前端 `features/life`、`multimodal`、`room`、`companion`、`home`、`workbench`，以及 Channels/Weather 等仍归它的设置页面。唯一拥有 `web/src/api/**`、`app/**`、`modules/**`、公共组件、路由和前端 manifest/lock。 |
| capabilities | 先交完整 channels、QWeather 与当前实际能力补丁；然后接 `web/src/features/plugins/**`、`media/**`、`infrastructure/**`、`resources/**`。这些目录自 `7660945` 起由 frontend 冻结。核对外部 AssetLibrary 有效只读适配及原 Chat Audit 接口实际去向；不操作或复制独立产品私有库。 |
| foundation_models | 继续 root composition、CR20 和旧实现退出；核对旧 Knowledge/Project 的真实已实现能力。然后接 `web/src/features/people/**`、`memory/**`、`knowledge/**`、`projects/**`；设置页仅 `ProviderManager.tsx`、`ProviderEditors.tsx`、`ProviderModelsPanel.tsx`、`ModelFunctionsPanel.tsx`、`providerApi.ts`、`providerTypes.ts`、`providerPresets.ts`、`models.css`、`AccessPanel.tsx`、`access.css`。这些路径尚未开始最终业务重写，frontend 不再修改。 |

foundation 的项目/知识代码如需能力归属，独占 `src/tianshu/capabilities/projects/**`，从 capabilities 的大目录范围明确划除。跨域注册由所属 owner 提交小补丁或在根装配；不让两个写者同时改 capabilities/service.py、共享客户端或主路由。

## 保留清单的实际核对

CR01 的“新归属”是施工责任，不能当实现证据。U17–18、U43–47、U69–74、U89–92 应逐项给出冻结有效入口、当前真实消费者及仍未实现的历史状态。原 Knowledge 的目录导入、资料/章节阅读、研究笔记、续作与经验，以及 AssetLibrary 资源页等，按实际有效代码迁入并接通前端；不以 Source/Actions 已有通用端口代替业务实现。原本未实现或未挂载的能力如实保留其状态，不新增占位成功。

新实现共用 SourceAuthority、唯一 PG、Actions/Receipt、Procrastinate、ContextAssembler 与 FileStore。原独立 Core、旧内部 HTTP 信封、旧数据库兼容层不重新进入产品。

## 联合接线约束

- `actor_id/person_id/conversation_id` 由共享客户端统一放入 query，经 Identity 派生可信场景；请求体同名字段必须一致，不授予范围。C 从业务参数中剥离选择器，具体执行权限仍由 handler 定义检查，普通能力调用不额外要求配置管理权限。
- 未知命令保留原 `client_request_id/action_id/attempt_id`；页面查询原回执，不因超时重复提交。受理、处理、成功、失败和未知分别展示。
- 新读写操作名由页面 owner 提交给 frontend 注册；实际 HTTP 方法、字段、revision 与分页以当前已集成模块为准。
- 页面的有效布局、主题、资源继续复用；删除被替代的旧 DTO、分值/阶段配置、跨产品包装和无消费者分支。不得只把旧页面套到新应用壳中。

本卡仅规定已授权工作边界；没有运行验收或生产操作。
