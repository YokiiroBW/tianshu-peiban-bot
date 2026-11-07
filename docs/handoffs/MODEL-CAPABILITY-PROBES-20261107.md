# 模型能力测试与 DSF 工具续接修复

用户在本聊天查证缺口后明确要求“修复功能缺口”。基线 deb0585f（线上产品 f9a98a8a），
独立工作树 `C:/Users/Administrator/.codex/worktrees/model-capability-probes/tianshu-peiban-bot`，
分支 `codex/model-capability-probes-20261107`。本文是实现交接，实际发布由既定唯一生产写者执行。

## 实现

- 复用原 `/models/routes/{id}/probe` 与 Models 端口，新增工具、图片、音频、视频、JSON、嵌入和重排序测试。
  后端按当前原生协议发布可测项，前端统一“测试与登记能力”面板；不发送聊天附件。
- 小型图片/视频/音频样本由已安装 Pillow / PyAV / 标准库生成。图片读数字、视频核对先后数字、音频核对三组提示音；
  工具必须先产生正确调用，再消费本地回声工具结果并完整回复。文本成功不能代替多模态或工具成功。
  嵌入按现有1024维合同核对两条有限非零向量，重排序核对完整排名和有效分数；不调整既有嵌入空间。
- 每项只在实际通过后登记，保留逐能力原 attempt IDs 与时间，同时合并原上下文窗口、note 和其他能力证据。
  保存前按原 route revision 再做 CAS；重复命令沿用既有 CommandStore，未知/活动/失败模型只观察原对象，不自动新建尝试。
- OpenAI Chat 的 `reasoning_content` 在流式分片和非流式响应中保留为私有模型材料；
  Models 共用 `assistant_message` 提供续接 metadata，Conversation 的实际工具消费路径保存它。
  所有既有历史轮次仍在，续接只携带一份原思考内容，公开回复不包含该材料。
- OpenAI Chat 编译器新增 `video_url: {url: ...}` 兼容扩展，同时识别原生视频请求的能力需求。
  模型支持程度由真实测试判定，不按供应商/模型名直接授予 video。
- HTTP 错误最多读取16KiB/1秒，仅保留有限错误 code/type/param 和分类，不保存任意 message、正文或凭据。
  可区分明确限速和明确额度错误；泛化 `quota_exceeded_error` 仍显示“限速或额度”，不编造具体结论。

未更改模型路由、绑定、供应商凭据、预算/超时配置或原账本。无新依赖、迁移、业务重放或真实模型/QQ调用。
本批不包含此前另行发现的普通聊天准入/超时和日记材料对账问题。

## 验证

- 78 个不同 Python 定向用例通过：能力样本/工具/错误/证据31，原生协议9，Conversation实际续接1，既有供应商传输37。
  结果按受影响范围复用；修订后只补跑变化覆盖。Windows 使用 SelectorEventLoop 运行。
- 新增页面用例桌面/手机共4项通过，覆盖入口、逐项登记、CAS跟随新版本、测试期间禁重入、失败原因与嵌入原生测试入口。
- TypeScript检查、Vite生产构建、相关Ruff与diff检查通过。后续文案与格式按直接检查验证。
- **`tests/models/test_probe_completion.py` 六项 PG 用例尚未执行**：本窗口没有可用的隔离PG DSN，因此明确跳过。
  其中原流式三项、工具两轮落账/证据保留与幂等一项、失败/unknown不登记两项，建议由协调者只补这六项。
  不把未执行用例、模拟供应商回包或页面拦截测试称为真实供应商验收。

## 商汤依据与限制

TokenPlan DSF 沿用 OpenAI Chat、`https://token.sensenova.cn/v1`、`deepseek-flash`，不改成旧版商汤 `/llm/chat-completions`。
官方要求带 tools 请求回传历史 reasoning_content；原四条429仍是历史事实，本批不重放、改写或宣称已解除上游额度限制。

- 商汤 DSF：https://platform.sensenova.cn/docs#model-deepseek-flash
- 兼容接口 video_url 对象形态的官方示例：https://help.aliyun.com/zh/model-studio/qwen3-livetranslate-flash

商汤 TokenPlan 文档确认视频仅可走 Chat Completions，但未给完整视频字段示例。
本批扩展通用兼容传输并提供真实视频测试入口，**尚未验证商汤 DSF 服务端是否接受这一扩展或当前账号能否使用视频**。
不通过测试的能力不会登记；不将旧版 SenseCore 字符串字段/响应包装套在 TokenPlan 接口上。
