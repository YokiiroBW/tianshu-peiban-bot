# 2026-10-11 记忆闭环本地交付

用户要求先完成记忆完整闭环。本次基于最新发布记录定位的 Memory `097ccfa`、Companion `fbb378b`；根 CURRENT 与 projects 协调检出未包含全部后续发布状态，不能据此判断线上仍缺自然召回。

最新基线已有正常对话记忆上下文、模型主动 memory_propose、查询回执、纠正/遗忘与来源抑制；本次补齐自动候选的后台提炼与持久处理，并恢复候选受理的未知回执。没有另建记忆数据库、召回规则或用户画像体系。

## 完整路径

用户消息 → 原有来源/人物/角色核验 → 持久候选事件 → Memory 受理回执 → Companion 独立后台选取记忆整理模型 → 基于当前授权原文提炼 → Memory 验证精确引文和范围 → 事务提交/去重回执 → 后续普通对话自然召回。模型失败、断连和服务重启均保留队列；撤权/撤回来源不能落库；纠正与遗忘沿用原有机制。

实现与协议见 [Memory 说明](../../worktrees/TS-117/tianshu-memory/docs/candidate-extraction.md) 和 [Companion 说明](../../worktrees/TS-118/tianshu-companion/docs/memory-learning.md)。独立任务 TS-117/118，保留根与其他工作树所有既有修改；无 NAS、真实模型、QQ、生产数据库、推送或部署操作。

## 验证与集成

实际四产品 HTTPS 回环通过：保存合成咖啡偏好，重启 Companion 并关闭短期邻接后，普通问题的实际模型请求仍包含长期记忆。模型与渠道为合成替身，不称真实模型或 QQ 验收。

测试数字、提交和剩余限制由同名 JSON 交付清单及两个任务 handoff 记录。Memory 与 Companion 成对集成，顺序 Memory → Companion；本次无新数据库迁移。自动整理沿用已启用的候选开关和现有 memory 模型配置/默认回退。

Platform 的功能目录静态 connected 文案仍为旧标记。尝试为该文案分配 TS-119 时，既有分配器因平台协调检出有未提交改动拒绝创建；没有清理/暂存别人的改动或绕过分配器，已撤销未分配的临时任务。该文案不阻断实际 memory 模型选择，留给平台集成人随同版本更新。

协调工具最终 `workspace.py check` 在检查既有 TS-016 平台工作树时遇到 Git ownership 拒绝（原属 CodexSandboxOffline，当前 Administrator），未改全局 safe.directory。TS-117/118 自身工作树均干净，两个本地提交已保存。
