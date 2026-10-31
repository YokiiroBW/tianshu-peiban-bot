# CR18 前端交接

状态：`implemented_not_tested`，首轮可集成代码；CR18B/C/D 与 CR18 最终接线继续中，当前不是整卡完成。基线 `e034276dfb757b7dd64cd2f56e952d81193a5248`，分支 `codex/cr-frontend`。唯一写入范围 `web/**` 与本文件。

## 冻结来源与保留

只读确认 Platform HEAD `21bdb7b9e11e18d47111e656e829edd331a77b14` 包含部署前端 `911a1dc60318abad76287013a05afacd1e63f9f3`；两者 `apps/web` Git tree 均为 `940b85b8bbaf909e52af3dabaabf181f1c4257be`，目录 diff 为空。迁入使用协调者冻结的 platform/apps/web，保留当前认可页面及 8 个 public 文件。没有从较旧根协调副本覆盖页面。

完整迁入应用壳、角色/Web聊天/人格/设置、生活/日记/创作书架/衣柜/阅读/经历、小屋（Three.js及插画）、用户/群画像/本人记忆/人物详情、视频/音乐/书籍/漫画/下载/账号/存储、知识/资产/项目经验、设备/容器/Dockge/节点、插件全管理页、模型/连接/技能/天气/检索/任务/链路图，以及原有视觉和用例。没有新增依赖。

## 首轮实现

- `web/` 自有 manifest/lock、Vite/TypeScript/Playwright 入口，移除旧 apps/web 路径依赖。
- 数字段地址替换为语义路由；新增角色成长/约定、语音、图包、图像引擎入口。账号关联入口进入现有人物空间；原未实现身体与活动仍准确显示不可用。
- `src/api/client.ts` 为唯一 fetch：同源Cookie/CSRF、标准错误、取消、CommandResult、原action/attempt/receipt、未知结果保留；统一读取/命令/二进制媒体。旧 sessionTransport 退出运行树，不存在 `/api/web` 自动回退。
- 正式 SessionView 平面字段由 `src/api/session.ts` 转为 UI 显示数据；各领域 HTTP 包装复用同一客户端。
- 作用域读取支持会话变化/角色变化后的取消及旧结果隔离；命令在结果未知时保留原请求标识，统一反馈展示执行与投递差异。
- 按能力所有者 `067d1dc` 冻结投影实现 Fish连接/Plus OAuth/独立余额/实际功能、音色查询/创建/角色绑定/试听/词典/文件转写；图包导入导出/动图/标签与用途纠错/标注索引/语义检索；ComfyUI/NAI/GPT Image配置、工作流导入、形象参考、场景提示预览、实际生成/取消/unknown核对、相册原件。均调用正式域API，不内置生产mock或成功替身。

## 依赖及继续工作

基础契约原提交 `ec2fc7c`、能力契约原提交 `067d1dc` 已在本分支 cherry-pick，仅作为只读正式输入；后端文件未由本实现者修改。

下一轮需核心所有者冻结并实现 identity/roles/personas/conversation、sources/memory/people、companion/life/growth/commitments、channels、runtime/settings 的具体操作与投影。现有保留页当前已统一传输路径，但还包含旧业务 DTO 和跨产品信封（特别 life runtime、角色legacy、关系score/stage、记忆/观察范围），这部分不以首轮迁移冒称新核接通。成长与约定新页使用已提交给协调者的待定字段，服务未注册时显示真实请求失败，不假成功。CR18B 需要随核心定稿删除旧积分/阶段/legacy分支并增加认识纠正；CR18C 需完成消费覆盖与完整ComfyUI参数绑定体验；CR18D 需接模型Broker/OneBot完整能力矩阵/真实任务资源等待。旧ComfyUI专用组件当前仍作为待替换源码保留，主生活相册入口已经改接三引擎页面。

语音/图像/图包界面按正式端点与DTO编码，能力服务实现尚在另一泳道进行，当前不能宣称实际音频/图片/插件或付费API验收。旧前端合成fixture需在新核心接口齐备后更新；新写统一传输与语义路由用例，尚未执行。

## 实际检查与限制

按用户“全部代码完成后统一测试”要求：未安装依赖、未运行测试、typecheck、build、浏览器、真实账号/服务、生产或收费调用。只阅读源码/正式契约与diff；静态相对源码import检查未发现缺失路径，源运行树搜索未发现旧 `/api/web` 或旧产品源码引用。此检查不替代编译或行为验证。

协调者可集成首轮前端提交；前端唯一写入归属保留，后续完成全卡接线后再集中验证。
