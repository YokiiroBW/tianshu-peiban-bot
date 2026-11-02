# NR-05C：转发读取局部容错

基线 `c11e99af`；实现提交 `8e8266fcf9a449657575710ce3f761f9a37f319a`；分支 `codex/nr-05c-forward-read-20261102`。唯一写入 worktree 为 `C:\Users\Administrator\.codex\worktrees\tianshu-forward-read-20261102\tianshu-peiban-bot`，未使用已归档的旧 worktree。实现按本轮 gpt-6.1-sol/max、全部收齐后由根集中验证的分工交付。

## 已确认原因与边界

根提供的去敏实机拒绝为 `status=failed, retcode=100, wording='download forward message payload is empty'`。最新核对中，失败请求和五条成功请求均直接携带 `{'id': <非空64字符字符串>}`，没有错误参数嵌套、空 ID 或数值类型根因证据。这是 QQ 网关正文下载为空；本补丁不能使外部不可用的正文变为可读，不将这次局部修复宣称为已根治该网关问题。

源码调用链为 OneBot normalize/input_parts → ingress.call_api → 原 channels.read Action → Operation.execute/registry.validate_call → channels.perform_call → 原 transport。forward 的 `data.id` 保持原值，注册表要求标准 `id`，没有把它当数值 message_id。Transport 的实际 rejected/retcode/error/raw、原 Action/Receipt 和封存响应保留。入站按原 event/name/params 稳定 key 找回原读取回执，已失败或未知请求不重新 invoke；本次未新增重试、换 ID、供应商兼容参数或外部 effect 重发。

已定位的产品问题在转发展开：原单一大 try 覆盖全部节点，后续坏节点或异常会丢弃此前已经展开的 quote；失败回退又丢掉原 segment、转发 ID 和节点位置。顶层其他可用文本原本仍能入站，本次保持该行为，不把局部读取失败当整条消息失败。

## 改动与复用

- 把原 `expand_forward` 集中到 `src/tianshu/channels/forward.py`，ingress 保留同一调用路径。继续复用 OneBot 的 segments/input_parts、Media.receive、Sources.capture 及入站保存前的真实来源授权复核。
- 逐节点解析；单个坏节点以 reference 标出原因，保留此前和后续实际 quote。媒体失败和嵌套转发失败也局部标记，header 的 missing 含节点/子项位置，原 forward segment/ID、来源引用和引用作者声明保留。
- 对确认响应中的空或畸形正文明确标为 unavailable/missing，只保存确实收到的原件，不生成正文 quote，不宣称分析完成。真实读取拒绝没有响应正文时不造来源或成功。
- 原两层递归、4 MiB、128 节点及 caller 的 part 预算保留；超过预算明确 omitted/missing。取消继续抛出，不转换成已完成的降级输入。引用仍为 quote/未核实的外部作者声明，不授予 verified person 身份。

没有 Memory/Models、Sources/Identity、数据库迁移、配置或前端变更；无需额外 Capabilities 接线。get_msg 本轮保留原有作者/消息 ID 读取与来源范围校验，没有扩展引用正文功能。

## 验证与最小待跑范围

本代理只读代码、核对调用方与静态 diff；`git diff --cached --check` 无输出。没有执行测试、typecheck、build、真实 QQ、发送消息、生产访问或部署。下述均为待根执行，不能称已通过。

新增 `test_forward_expansion.py` 六个定向用例，含媒体返回/抛错、畸形响应和取消阶段的 subtests：坏中间节点保留两侧 quote 与真实原件；单个媒体失败保留前后文本；嵌套拒绝保留原 ID 与其他正文；空/畸形响应不造 quote；原 part/node/depth 上限显式保留；取消不伪装完成。

扩展既有 `test_ingress_reads.py`，同时锁住原 unknown 和 failed 回执不重发、失败标记保留原 forward ID/缺项、可用顶层文本仍入站、原回执不变。新增一个 OneBot 协议用例保留本次真实错误形状的 rejected/100/wording，不将其变成成功或异步接受。

```text
python -m pytest -q tests/channels/test_forward_expansion.py tests/channels/test_ingress_reads.py tests/channels/test_onebot_protocol.py::OneBotProtocol::test_forward_download_empty_rejection_keeps_its_actual_response
```

根负责与另两组补丁一起收齐后集中运行必要检查、串行集成及后续受影响入口核实。原 QQ 网关 payload empty 的外部可用性仍需真实服务自身恢复，原拒绝/未知请求与历史回执不重写、不自动补发。本卡到此收口。
