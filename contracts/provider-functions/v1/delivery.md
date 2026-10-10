# 模型功能分工

2026-10-10，本地实现。设置 → 模型供应商 → 模型功能分工。

默认提供主对话、工具处理、代码、网页搜索、日常与日记、记忆整理六项。每项选择已添加、启用且当前版本短回复测试通过的模型配置。同一接入商的不同模型可以分别添加为模型配置。

主对话复用 ProviderCatalog 原有 default 指针，和供应商卡片的“设为默认”完全同步，不建立第二份聊天默认值。其他功能允许选择独立模型，或选择“继承默认模型”。保存本身不调用外部模型。

主对话及日常/日记已有消费者接入；工具、代码、搜索、记忆整理目前可持久配置和通过内部选择端口取用，但自动任务消费者尚未接入。界面显式区分两种状态。长篇作品仍使用已有显式静态写作配置；本功能不接入 embedding、语音、图片接口，不实现聊天自动委派及两阶段回复。

## 配置与内部选择规则

- 主对话：角色已有独立模型 → 系统默认模型。
- 其他功能：独立功能绑定 → 角色已有模型 → 系统默认模型。
- 独立绑定记录准确供应商版本。供应商被编辑、停用、删除或测试失效时，该绑定显示不可用；不会偷偷换成默认。重新测试后可重新选择当前版本，或显式恢复继承。
- 一个生成任务沿用已有固定发布版本和 grant；配置变更只影响新任务。每个辅助生成必须使用独立 turn_id，不能借用主聊天轮次的 grant。

## HTTP 增量

本增量随 2026-10-11 平台与陪伴联合发布；既有共享 v1 包字节保持。

`POST /api/web/providers/view` 在原响应上增加 `functions` 数组。每项包含：

`function_id, name, description, connected, revision, provider_id, provider_revision, effective_provider_id, effective_provider_revision, configured`。

`connected` 表示代码中已有消费者，不表示真实模型/部署运行已验收。`configured` 表示供应商配置可选择，不表示工具能力已经验证。继承项的 provider_id/provider_revision 为 null，effective 字段显示系统默认；角色覆盖可能影响具体任务的最终模型。

`POST /api/web/providers/function`：

```json
{
  "client_id": "11111111-2222-3333-4444-555555555555",
  "function_id": "code",
  "provider_id": "provider-example",
  "expected_revision": 1,
  "expected_binding_revision": 0
}
```

function_id 为 `chat/tools/code/search/writing/memory`。继承时 provider_id 与 expected_revision 同为 null；chat 必须选择模型。响应 `{function_id, revision}`。复用已有 Cookie/CSRF/Origin、管理员管理租约、CAS 和幂等回执。主对话的 revision 就是原 default_revision。

`POST /internal/v1/provider-self-service/select` 允许可选 `function_id`，缺省为 chat；其他字段及响应保持原样。身份仍为可信 companion；网关传输 workload 仍是 companion.text，网关按精确版本获取该功能选中的模型，不新增权限或改变协议。旧聊天客户端不需要修改；新版陪伴的非聊天选择请求需要先升级平台。

## 持久化与更新

ProviderCatalog 在同一个加密目录的 SQLite 中增加 `model_function_bindings` 表，继续复用 receipts。首次打开已有库，在写事务保护下通过 SQLite backup 创建 `providers.pre-functions-<随机值>.sqlite`，再创建附加表；新安装直接建表。原供应商、密钥、默认模型和发布记录不变。备份仍需连同原加密 key 和目录整体保护，后续启动不重复创建备份。

部署顺序为平台（包含网页）→ 陪伴；网关无需修改。本轮未部署，也未读取或修改真实配置。

## 验证入口

- `python -m unittest discover -s tests/backend -p test_model_functions.py -v`
- `python -m unittest discover -s tests/backend -p test_provider_catalog.py -v`
- `node node_modules/typescript/bin/tsc -p apps/web/tsconfig.json --noEmit`
- `node node_modules/vite/bin/vite.js build --config apps/web/vite.config.ts`
- 设置 TS012_CONTRACT_DIR 为匹配发布哈希的合同目录，TIANSHU_TEST_PYTHON 为测试解释器，再运行 `node node_modules/@playwright/test/cli.js test --config apps/web/playwright.model-functions.config.ts`。

浏览器测试使用临时真实平台后端、合成管理员和模型配置，覆盖桌面/手机保存、刷新、默认同步、独立选择与恢复继承，不发起收费模型请求。
