# 角色设置中选择账户音色并绑定角色

日期：2026-11-09。状态：代码已提交，交付统一发布；本窗口未推送或部署。

## 交付身份与范围

- 分支：`codex/role-speech-binding-20261109`。
- 工作树：`C:/Users/Administrator/.codex/worktrees/role-speech-binding-20261109/tianshu-peiban-bot`。
- 基线：`d3071fe095e609067903c94bcef062837c5218d0`，由联合发布协调聊天指定。
- 产品及必要用例提交：`a52a9aac43a6ff2e762197e83a64bfe3079a1e00`。
- 本文单独作为交接文档提交；最终分支 HEAD 见本窗口向协调聊天的交付回执。
- 唯一串行生产发布者：聊天 `01a1b700-8dc7-7283-91e8-a53b7070b835`。

已只读核验协调聊天的直接用户指令“与另外窗口的一起推送部署吧”，以及随后明确选择“角色设置里选择 FishAudio 音色”。本批联合发布范围为该语音功能与插件生命周期，不含仍开发中的主动联系、日记或 Memory 修复。

## 最终行为

角色设置增加“声音与语音”页签：使用已连接的语音账户，自动读取“我的音色”，显示音色名称和 ID，支持名称搜索、公开音色库、供应商分页和手动填写 ID。选择音色后可试听，再绑定当前角色。浏览及选择不会自动保存角色配置，也不会自动发起试听合成。

新绑定默认“文字与语音”；已有角色继续使用原输出方式、模型、语言、读音词典与发声默认值，保留原 profile ID、revision 和额外 vocal_defaults 字段。切换账户清空待绑定选择；切换角色重新读取该角色配置，避免把上一角色的选择保存给新角色。

角色设置与原“任务与设置 → 声音与语音”共用同一个 VoiceProfileEditor、VoicePicker 和原领域端口，没有另建绑定表或语音状态机。原连接管理、OAuth 授权、发音词典、私有音色创建和文件转写入口保留。

后端补齐原 MCP 查询未传入 owned 的缺口：根据已连接工具声明的 schema 传递 self 布尔值或包含 self 的枚举选择器，按声明传递 cursor / offset / 页码；不支持“我的音色”筛选时明确报错，不将公开结果冒充自有音色。音色页统一保留供应商 ID 和下一页游标，支持原生 API 的 total 分页；格式错误不会伪装成空音色库。

## 接口、装配与迁移

复用原接口：

- `speech/view`：读取连接及当前角色声线。
- `speech/voices.search`：复用原查询 Action，传 connection_id、query、owned 和 cursor。
- `speech/profiles.save`：复用原 revision/CAS 保存；新增可选 `voice_name`，仅作界面名称展示，实际绑定仍以 voice_id 为准。
- `speech/preview`：复用原试听 Action 与产物读取，已有 profile 的试听保留 profile_id/profile_revision。

没有新路由、数据库迁移、模块注册、依赖、环境变量或部署配置需求。`voice_name` 存在原 voice_profile JSON 配置中，旧配置没有该字段时显示 voice_id。未修改账号授权凭据、模型绑定、渠道权限、既有请求/回执/unknown、来源/CAS、代际或恢复预算。

## 精确文件归属

产品及用例提交只含以下 13 个文件：

1. `src/tianshu/capabilities/speech/mcp.py`
2. `src/tianshu/capabilities/speech/service.py`
3. `src/tianshu/capabilities/speech/voices.py`
4. `tests/capabilities/test_speech_voices.py`
5. `web/src/features/companion/RoleSettings.tsx`
6. `web/src/features/multimodal/RoleSpeechSettings.tsx`
7. `web/src/features/multimodal/VoicePicker.tsx`
8. `web/src/features/multimodal/VoiceProfileEditor.tsx`
9. `web/src/features/multimodal/SpeechPage.tsx`
10. `web/src/features/multimodal/SpeechTools.tsx`
11. `web/src/features/multimodal/types.ts`
12. `web/src/features/multimodal/multimodal.css`
13. `web/tests/role-speech.spec.ts`

从 `C:/YOKI/Codex/tianshu-peiban-bot` 的混合修改中按上述白名单复制。13 个文件均逐字节 SHA256 一致；原目录及其中其他窗口的修改全部保留，未 reset、clean、stash 或回写清理。迁出前检查，原验证基线 `37d4cb50dd3456a2b73dee61946dab71d105ef61` 到交付基线 d3071fe0 之间，上述已跟踪语音路径和原 speech-protocol / unbound-Fish 受影响用例没有改动。

本地迁出清单：`C:/YOKI/Codex/tianshu-peiban-bot/.runtime/role-speech-handoff-20261109/source-files.json`，记录源/目标、基线、13 份原始字节 SHA256 与大小。协调者应合入独立提交，不从混合根目录批量暂存。

## 可复用的实际验证

以下结果来自本聊天先前在原协调目录完成的验证，本次仅整理工作树和提交，没有改动产品/用例内容，也没有重复运行测试、类型检查或构建：

- Python：`.venv/Scripts/python.exe -m pytest tests/capabilities/test_speech_voices.py tests/capabilities/test_speech_protocol.py -q`，**11 passed，2 subtests passed**。覆盖 MCP 自有筛选、游标/页码、原生分页、供应商 ID、无效响应及既有语音协议/OAuth 回归。
- 新界面：`node node_modules/@playwright/test/cli.js test role-speech.spec.ts --workers=1 --reporter=line`，最终 **10 个不同桌面/手机用例通过**。其中 9 项在最后整批通过；手机角色切换夹具改为先打开原有导航后单独通过该 1 项。覆盖读取、绑定、刷新读回、试听参数、公开库/分页、手填、原配置保留、账户/角色切换和未连接状态。
- 原界面：`node node_modules/@playwright/test/cli.js test memory-closure.spec.ts --grep 'unbound Fish voice' --workers=1 --reporter=line`，**2 passed**，确认未绑定音色不自动写入配置。合计 **12 个不同桌面/手机用例**。
- `node node_modules/typescript/bin/tsc -p tsconfig.json --noEmit` 通过；Vite 正式前端构建通过。
- 本批前端文件 Prettier 检查、相关 diff 空白检查通过；新 voices.py 与 Python 用例 Ruff 检查通过。
- 本次交付仅新增静态证据：13 文件字节一致性、暂存白名单完全匹配、提交 diff 空白检查及工作树状态检查。

原协调目录另有其他窗口修改，因此以上不是对独立交付 HEAD 或正式 Linux 镜像的重新整体验收；协调者在最新联合候选上完成必要集成核对和正式构建，复用仍适用结果。

## 未验证项与发布边界

- 本窗口未使用真实 Fish 账号执行 tools/list、音色查询或合成；实际账户音色清单、余额、供应商当前 schema 和试听音频质量待正常接入验收。界面用例使用模拟账户与响应，不是实际供应商证明。
- 未验证真实 QQ 语音发送、模型回复或生产登录浏览器，未发消息、消耗 Fish 合成额度或重放外部 unknown。
- 未在正式容器内验证本功能；服务健康、安装文件/前端产物 SHA、现存配置/数据及实际业务效果由唯一发布者分别核验。
- 本窗口不推送、不部署、不修改生产或其他工作树；联合发布进度以协调聊天回执为准。
