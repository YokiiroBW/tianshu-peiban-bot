# 机器人接入第一轮总控验收

## 固定候选

- Platform：`c5f4d3acc3a906376c5dfe36279ef26b91769b25`（初版 `0a2e6d9242308eb3cb785815aae8ca37b190ec62`）。
- NoneBot：`361585c952a45e825cdc4a438fba372f294a82bb`。
- AstrBot：`910ce8c63756b03463db63bd8e0c0de156da2e7b`。
- 两适配器及 Companion Core 隔离组合：`484240a9244dcd62dec22c174d5281a715aaae10`。

主线和 NAS 均未随本次验收更新；所有测试使用本地隔离数据。真实测试目标仍未指定。

## 已发现并修复

独立初审 `docs/reviews/BOT-R-2026-09-28.md` 复现两处阻断：同一真实机器人/会话/角色可通过不同内部 binding 启用两个回复者；事件入口同步 SQLite 写锁会阻塞 aiohttp 事件循环。Platform 后续固定补丁将唯一性改为 namespace、实际 self_id、外部会话、thread，内部 binding/runtime 不再绕过；既有冲突台账拒绝新事件、claim/send。入站意图与结果结算移入已有有界 LocalWork，事务关闭后才进行异步 dispatch。

AstrBot 首版缺私有 CA 配置，由总控审查退回后在最终固定 SHA 补齐；SSL 证书及主机名验证保留，不能使用 NAS 18446 网页 HTTP 地址代替内部 HTTPS API。

## 总控实际复验

隔离组合工作区中使用 BOT-N Python 3.12 环境，设置 `PYTHONPATH=src;integrations/nonebot`、`TIANSHU_CONTRACTS`/`TS012_CONTRACT_DIR` 指向协调仓库已发布 text-dialogue/v1、`TIANSHU_PLATFORM_REPO` 指向上述固定 P、`TIANSHU_TLS_PYTHON` 指向已有证书生成环境：

```text
python -m pytest -q tests/test_bot_sender.py tests/test_nonebot_sdk.py tests/test_nonebot_platform_joint.py tests/test_nonebot_core_joint.py tests/test_astrbot_connector.py tests/test_astrbot_http_joint.py
21 passed, 0 skipped
```

其中包括实际 Platform Sources→实际 Core ingest/tick→平台回复队列→SDK 替身→ACK→实际 Core reconcile，以及重复输入、私有 CA 与错误证书拒绝。Memory、模型网关和机器人原生发送器为显式测试替身，不是实机模型或 QQ 收发证据。

Platform 固定修复版使用自身 `.runtime/venv`，配置 `PYTHONPATH` 包含项目根与 `tests/backend`，显式合同路径：

```text
python -m unittest tests.backend.test_bots tests.backend.test_web_sender -q
20 passed
```

修复前的原候选另补齐 TLS 环境，执行 `test_runtime_health.ReadinessTests` 与 `ProbeUnitTests` 共 19 项通过，替代这些范围此前因环境缺失而跳过的状态；不把余下未运行用例算通过。修复未改该健康检查模块。

## 实机阶段边界

- NoneBot 本地 SDK 组合 2.5.0 / OneBot adapter 2.4.0 已加载，真实宿主与 NapCat 仍待部署核查。
- AstrBot 目标 NAS 已确认 4.27.3，本轮插件尚未安装/启用于该宿主。
- 首版仅 QQ 纯文本，媒体、撤回/编辑、TG 及其他 AstrBot 适配器不能宣称支持。
- 管理页面管理预登记槽位；实际机器人 ID、作者、会话、Core binding、内部 HTTPS/CA 与插件凭据需统一部署。不能把空槽位或心跳在线当真实收发通过。
- 用户未指定真实测试对象前，连接保持停用，不发送群/私聊测试消息。
- 新 `.bots.sqlite` 与平台权威库应一同备份，插件 journal 必须持久化；跨产品 HTTP 候选文档仍需在部署集成时完成正式归档。现有 NAS 日志预算 5000ms、十服务容量监督和原账号必须保留。

## 最终结论

**固定候选通过本地开发与联合验收，可进入主线集成准备。** 独立 Luna 窄复核报告 `docs/reviews/BOT-R-2026-09-28-followup-c5f4d3a.md`（原报告提交 d77a31a）确认两项阻断均关闭：P 专项 12/12，另两项隔离复现通过，包括并发重复事件 unknown 不二次调度、历史双启用冲突拒绝新增 event/send/claim。总控上述联合 21/21、平台与旧 sender 20/20 均通过。

本结论不是主线已合入、已部署或真实机器人收发通过。下一阶段需完成正式接口归档与主线串行集成、指定测试对象和统一部署，再做真实账号收发验收。不得直接开启所有群或默认监听未知作者。
