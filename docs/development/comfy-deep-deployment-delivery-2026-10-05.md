# ComfyUI 深度接入部署交付（2026-10-05）

ComfyUI 深度接入已部署到 NAS。两款固定镜像、Companion 出站网络、澄汐工作流绑定及真实 Gateway 中文模型辅助编译均已核验；最终五个核心服务 HTTPS live/ready 均为 200、capacity guard ready，56 个网页资产经 HTTP 核验。真实 GPU 在隔离验收上下文完成一张图，生产中文翻译验收只编译，未额外提交 GPU 或发送 QQ。

## 交付版本与范围

| 项目 | 固定提交 / 镜像 |
| --- | --- |
| Companion | `41393d9f559e6ca98a082bc9aa39b22fc4bb1bae`；`sha256:17cee6e9208e35a6ccfc50700500755e54bf2e83cd604886ba39d51837a96c02` |
| Platform | `e385df791dfc78fb258ced1066f502903918aff4`；`sha256:c399e358c788197bb6f755fcbe09d5d58a8476c622f321e49cb891eecc904877` |
| 本轮合同闭包 | `725113bcc5676972226d0c73ae2543cdc8495085`；生产只读目录 `/volume2/tianshu-v2-resident/contracts-comfy-725113bcc567` |
| 部署生成源网络修复 | `afa6e7a7145d78e78f80b1029d5136651f5ec046`；`deploy/tianshu/compose.py` 为 Companion 添加现有 frontend，并沿用 core 内网 |

仅 Platform/Companion 使用本轮新合同目录；其他核心服务沿用原合同闭包。

ComfyUI 已实现连接与工作流目录发现、逐角色绑定、节点自动识别及下拉修正、模型辅助选择/适配、结构化服装/姿态/背景/镜头提示词、横竖尺寸预设、编译预览、原有生图任务与相册交付。图片请求可关联生活活动，后台拍照消费角色生活上下文。人物标签留空继承原工作流；没有衣橱条目也可沿用工作流默认穿搭。

管理链复用 Platform 登录/CSRF、Companion 原有管理授权及统一加密凭据目录，生图继续使用原有 image_jobs、未知结果查询和原图读取。未增加第二套凭据或任务账本。NAI/在线图像 API 仅保留 provider 扩展边界，本轮没有实现或显示为可用供应商。

## 验证证据

- Companion：138 项影响范围回归对应最终恢复修复前的代码；恢复修复后通过 51 项定向回归，最终画幅修订通过 1 项测试。Linux 最终候选镜像按固定 Git archive 校验已安装源码，59 个运行模块导入、CLI help 与 pip check 通过。
- Platform：`test_web_image_backend.py` 11 项、`test_web_console.py` 8 项、既有凭据回归 3 项通过；同一未变状态下复用适用结果。TypeScript 无输出检查、Vite 构建及改动范围格式/静态检查通过；Vite 仍有既有大 chunk 提示。
- 合成浏览器：桌面/小屏共 10 项通过、0 跳过，覆盖工作流发现、节点映射、编译预览、角色切换和原图读取。截图保存在 Platform 候选检出的 `apps/web/test-results/comfy-backend-ComfyUI-work-91ab3-efaults-and-compile-preview-{desktop,mobile}/`，四个候选截图为 `comfy-connection.png`、`comfy-bindings.png`、`comfy-preview.png`、`comfy-management.png`。
- 部署生成源：既有 `PackagingTests.test_valid_inputs_initialize_movable_candidate_and_keep_database_absent` 通过，验证 Companion frontend、core 原 IP/别名、无新增发布端口，以及 Memory/Gateway 网络边界。部署与网络修复脚本渲染、嵌入 Python 编译及 diff 检查通过。
- NAS 首次部署：十个受管理 owner 停机后冷备，33 个 SQLite 数据库验证通过；五个核心服务 TLS 验证通过并 ready，guard ready；用户状态和 settings 哈希保持，没有数据库迁移或自动旧账本还原。详细安全回执：`.runtime/comfy-deep-20261005/resident-poll-result.json`。
- HTTP 网页：56 个资产与最终 Platform 镜像内容一致，`Cache-Control: no-store`；回执未使用真实已登录浏览器会话，不能称真实网页登录验收完成。证据：`.runtime/comfy-deep-20261005/http-assets.json`。网络修复后的 Dockge Compose 同步已成功，OBS 定义保持原哈希；证据为 `dockge-sync-after-egress.json`。

真实 ComfyUI GPU 在隔离验收上下文完成一张 PNG，输入为显式英文 intent，未调用模型翻译。协调者已查看成品；本记录只保留其提供的安全摘要，不复制工作流图或原图内容：

| 字段 | 值 |
| --- | --- |
| prompt_id | `7277d72e-f0d2-42c1-8efd-18aafa9c35b6` |
| 尺寸 | `1024 × 1536` |
| 文件大小 | `2,023,498 bytes` |
| PNG SHA256 | `c8e801c61406ac6b7430a80a9b23e03e6b290d34507c4a12373d75b3d408f4af` |

GPU 验收与下述生产角色配置、Gateway 中文翻译验收分别记录。`real-generation/acceptance.json` 内含完整 `compile.graph`，不作为可公开的整体回执复制。

## 出站网络与生产管理链

首次生产 configure 返回 `connection_not_ready`：NAS 主机可达 8188，而 Companion 只有 `Internal=true` 的 core 网络，容器连接超时。连接已保存为 version 2，协调者保留失败 configure 证据，沿用原 request IDs 显式恢复，避免重写已匹配连接或重复调用模型。

独立修复 stage 为 `/volume2/tianshu-v2-resident-updates/comfy-deep-20261005-companion-egress`。它复用现有冷备/启动/guard helpers：停十个 owner、备份当前账本、只给 Companion 增加已有 frontend、同步 platform-first 当前 Companion 定义、建立 fresh guard generation、保留其他九个容器 ID 启动并核验。core 保持 internal、原 IP 与别名，镜像/合同/挂载不变，不发布端口，不改其他核心服务网络。实际 guard/CAP 不记录网络规则，无需扩展规则体系。

最终 `network-fix-poll-result.json` 为 `network_fix_verified`：Companion 容器只读 GET 8188 `/system_stats` 返回 200，五核心 TLS live/ready 200、guard ready，33 个数据库冷备验证通过。core 仍为 internal、IP `10.205.200.11` 和 `companion.internal` 别名保留；frontend 为 non-internal，Companion 未发布端口。其他九个 owner 的 ID/网络、全部镜像/合同/挂载和用户状态保持。

`resident-live-poll-result.json` 的 configure 为 `configured_and_read_back`：连接 version 2、无附加凭据，澄汐 actor version 2、11 个语义绑定，真实目录 `角色/澄汐/澄汐-分类测试版.json` 已绑定，默认 `1024 × 1536`，人物提示词留空继承工作流。

同一安全回执的 compile 为 `model_compile_verified`：真实 Gateway 调用 1 次，将中文服装、动作、场景和镜头转译为英文，按 Router 节点 35 的语义分段写入。LoRA 节点 27/28 及未绑定节点保持；输出尺寸 `1024 × 1536`，模型回执存在，编译图 SHA256 为 `9dc18a8de7a51c760cda405324c4dcee5fbbcddbc6f27b885552685e250bf267`。零模型 baseline 用于比较冻结图，此阶段未提交 GPU、未发 QQ。未知网络结果保留原 request ID，不自动重试模型。

## 使用限制

- 本轮未发送真实 QQ 消息；后台拍照与日程分享的实际 QQ 投递未验收。
- 本次 fullbody 请求的成品实际为半身，尺寸和提示词编译正确不保证模型严格遵循全身构图；尚不能称全身效果验收通过。
- 真实已登录网页操作未验收，当前网页交互证据来自隔离合成会话，真实 HTTP 证据覆盖静态资产。
- 已支持本轮实际 Anima、AstrBot Prompt Router、ResolutionMaster 节点转换。未知复杂自定义节点的 UI 工作流需补对应转换器或提供可验证的 API 格式；自动适配范围以已实现转换器和实际 object_info 验证为准。
- NAI 和其他在线图像 API 未实现；未来可接入不代表本轮可用。
