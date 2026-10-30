# 活动恢复与休眠（2.2.0）

`life-runtime/v2.2` 是独立的次版本包，保留原 `life-runtime/v2` 2.1.0 及 bot-delivery、knowledge-content 等原有依赖的 manifest 字节和 pin。新包使用独立 schema URI；Companion 与 Platform 的活动管理/读取共同消费它。

paused 必须有恢复时间；第三次已知失败仍有未来恢复点（默认六小时，`activity_retry_seconds` 可配置为 1..21600 秒）。dormant 需要恢复条件和服务端生成的 dormant_since，不自动步进，也不增加 result_refs。save 与 transition 共用迁移表；只有显式恢复可以从 dormant 回到 running。日程只把 running 当作占用，目标材料包含休眠活动。

未知的原步骤、回执及 checkpoint 保留；移除当前活动指针不会允许另一活动绕过未决副作用。显式恢复同一活动可以重新关联它，但不能改 checkpoint、清除原步骤或重放 unknown。

结构正负例、活动恢复定向用例及合成 HTTP 生产者到 Platform schema 消费者均已验证。生产迁移、真实模型和 QQ 验收单独记录，不由合同发布推定。
