# QQ-IDENTITY-20260929 · 固定提交独立复审

日期：2026-09-30
范围：Platform、Companion、Memory 固定提交；只读 Git 对象复审，不修改产品仓库。

## 固定对象

| 产品 | 复审提交 | 基线 / merge-base | 工作树状态 |
|---|---|---|---|
| Platform | `32a2bdab2bc2858689cd93637d2d7391fdaf9662` | `c897e8b2351694808a1231f2ce1245425235c8e1` | 干净 |
| Companion | `879c25c35f21a8eac29e777dc40ef0517df74466` | `644929cd404b106a90abbf0ebe01a049f30a0521` | 当前检出有 3 个未提交修改；未读取，仅审固定提交对象 |
| Memory | `da76ee22c1e433ec047201982191650add0092cc` | `0ff79ed315a4d707fd1dd42152e41f28bf7ad2b4` | 按协调者确认干净；复审未读取当前工作树 |

## 阻断验收的问题

### Observation-only 消息不会进入 QQ 别名及档案链

**影响：** 通过 observation-only 连接收到的真实 QQ 昵称和群名片不会写入 Memory 的正式账号别名，也无法在 Platform 的 QQ 档案页面读回。当前别名入队只随 Companion 对话事件成功 admission 发生，因此仅观察、不触发对话的消息没有进入别名持久化链。

**固定提交证据：**

- Companion `879c25c35f21a8eac29e777dc40ef0517df74466` 的 `integrations/nonebot/tianshu_nonebot/sdk.py:120-152`：`observation_event()` 返回机器人账号、作者、会话、事件时间、文本及内容状态，没有 `nickname` 或 `group_card`。
- Platform `32a2bdab2bc2858689cd93637d2d7391fdaf9662` 的 `services/platform/bots.py:503-557`：只有 `/bots/event` 的对话事件被接受后才调用 `queue_alias()`。
- Platform 固定提交中的 `tests/backend/test_observation_http_joint.py:285-364` 覆盖 observation-only HTTPS 归档和查询，没有断言 alias 写入、profile 查询或管理 UI 回读。

协调者已确认实现者正在返修，本复审没有重复实现。返修验收应覆盖一条真实格式的 observation-only 事件从宿主、Platform 到 Memory 的授权幂等别名写入，再经已登录管理 UI 读回 QQ 档案；不能只验证 observation archive。

## 安全边界复审

在以上固定提交中，没有确认其他授权绕过或数据归属缺陷：

- Platform 初始 grant 为空；写入带有版本 CAS；检查端要求 Companion 服务身份，并将经认证的服务端来源断言引用回查出的账号、actor 和会话与请求逐项核对。grant capability 仅为 `identity.explain`。
- Companion 在 QQ admission 时拒绝非规范 QQ ID、冲突身份和不匹配的私聊收件人；准备及发送前重新检查 grant 版本，版本/状态变化会阻止未发送回复。可信身份投影由服务端构造；模型请求只传文本，不包含工具定义，工具调用响应仍会被拒绝。
- Memory alias 写入要求已注册的 QQ account，不创建账号；账号、BOT、群范围分别存储；重复事件幂等，冲突失败；档案接口与别名写接口使用不同 Platform caller 凭据。schema 3 的别名迁移是显式命令，不在正常启动时自动运行。
- `da76ee2` 增量修复验证两个受保护 Memory caller 的 token 均已配置、非空，且不能与任何其他 caller token 重复；缺少配置时身份端点 fail-closed。
- 查看了注入、成员隔离及生成期间撤销的测试断言；未发现注入能够改变 grant、获得工具调用或把一名群成员的管理员状态带给另一名成员的代码路径。

## 验证范围与限制

- Platform 固定提交的 Playwright 管理页面测试覆盖登录、创建 scoped grant、刷新后持久化和撤销；档案 HTTPS peer 是合成服务。
- Memory `tests/test_qq_identity.py` 覆盖实际 Memory HTTP 路由、凭据分隔、注册限制、幂等、分页、重启及同名异号，但采用本地合成测试配置。
- Companion HTTPS 宿主联测使用 `FakeMemory`；没有单个测试贯通 Platform→Companion→Memory 的真实 HTTPS 别名写入，再登录 UI 读回。因此跨产品真实 HTTPS 闭环尚未证实。
- 本报告复核固定 Git 对象、手工检查实现和现有测试断言；未在活动产品工作树运行测试，也未检查 Companion 未提交改动。

## 结论

固定候选的授权、撤销、数据归属及迁移边界未见其他已确认安全阻断。Observation-only 档案闭环是本次确认的验收阻断项；完成返修并提供跨产品读回证据后，再复审返修提交和干净状态。
