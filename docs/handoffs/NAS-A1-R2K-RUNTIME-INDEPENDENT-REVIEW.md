# NAS-A1 r2k 运行终态独立验收

## 目标与结论

独立审查 r2k 单次隔离合成验证的实际 NAS 回执、功能断言、停机和资源终态，并对历史数据作有基线的只读比较。**结论：PASS，范围为本次 r2k 隔离作用域。** `release_ready=false`，本结论不改变生产发布状态。

## 基线与身份

- A1 固定代码提交 `195b7a5fa838b4e294205eb33421c0a82aec2053`；首次写前静态审查见 `NAS-A1-R2K-STATIC-PREFLIGHT-INDEPENDENT-REVIEW.md`（A4 提交 `d4eab0d`）。
- 分配 SHA-256 `1fc828704d3099f16d5ed2fec7b0af4955f600c9fc9ee1cad80e7a3c33f40293`；准备配置 SHA-256 `0aafd6aa48dbfb5d1f1da097a58e00443c6e6d19d704c4d243cbe2b457ab8ce4`；作用域 ID `36617739-0fc6-498b-9752-872a6c788742`。
- 克隆输入公开回执返回码 0，`clone_placeholders_prepared`，6 项断言、184 个输入文件，驱动配置 SHA-256 `07501cd1fbc7010e58b5d3952298c5c5909d16d9334d7fcc9fcf9150f80c5a9d`。NAS `attempt.json` 的这些身份与摘要完全一致，单次尝试 ID `77399777-7524-45d1-ace3-7a10d108934d`。

## 实际验证

1. A1 公开入口回执 `a1-once-execute.json` 返回码 0，列出 `seal → linux-rehearse → host-preflight → permit → drill-plan → drill-execute` 六阶段，`execute_calls=1`。A4 另经 NAS 只读查询每阶段原始 JSON 和 stdout：六项 `exit_code=0`、`validated=true`、`child_process_exited=true`，stdout 原始哈希与阶段回执一致。密封完成标记存在，发布版本 5；恢复阶段停止 9 个来源 owner，状态 `disabled_restore_complete`；许可签发时来源余时 208 秒；最终持久化 `drill-result.json` SHA-256 `98b8956c917fb279a74638207fa89883e9b919b901a29e6f88f0ac1e1b0fb904`，与执行子进程输出一致，状态 `drill_passed`。
2. 独立读取实际密封输入索引 SHA-256 `5368bb6a25bb6c9045778622d369c5eb299748391e6c586b212e224f4dbd9c3c`：六项请求都使用 HTTPS，五项 POST、一项 GET；撤销模型断言期望 HTTP 410，其余期望 200。六项结果 `data_readback`、`forgotten`、`source_revoked`、`model_revoked`、`unknown_no_resend`、`gateway_usage_readback` 均 `passed`；缺失语义与产品均为空。九个 owner 在功能回读期间同时运行；四个核心健康检查为健康，五个观测 owner 运行。
3. `unknown_no_resend` 要求 10 秒观察，实际 12.765 秒；记录 2 个 unknown turn 与 2 个 API 回复，API 回复快照未变，Gateway 上游 started/finished 均保持 4/4，相关性计数及两组成功对照未变。整个执行于 2026-09-25 20:32:32 UTC 结束，最早来源凭据于 20:33:37 UTC 到期，提前 65.36 秒。
4. 独立 Docker 只读检查四项目容器数为来源 4+5、克隆 4+5，共 18 个，全部已停止且退出码 0；每个容器 `CpusetCpus=6,7`，来源与克隆各自的内存上限合计 7.5 GiB，未设置 CPU quota，PID 限制保持平台报告的不支持状态。12 个 Docker 网络的 IPAM 子网逐一对应 `10.205.51.0/24` 内获分配的 12 个 `/28`，未见额外块网络。恢复结果 `activation=disabled`、`original_restore_activation=false`。
5. 使用 r2j 事前固定基线 SHA-256 `cb9c5f11bfeeabc8627e9d80a894c2453f59fb62f04c3c1845c4c83b4acd2ddc`，独立只读重算历史 r2j 来源的 Gateway 和 Companion 两个数据目录，5 个文件的清单、内容 SHA-256、inode、`mtime_ns`、大小全部一致；历史 r2j 来源两项目运行容器数为 0。旧固定发布清单仍为 SHA-256 `ce68ed58f4dbbaac6b9a09fc3632fd2522afc97501a63b790e1447b0af9fc4e2`。

## 证据与限制

A4 独立只读查询的本地回执在 `.runtime/nas-a1-r2k-review/`：`readback.json` SHA-256 `0a9ce67216bbe0a8d7cc6dd90400fe345f11c9ec2ec5aa2b437d48091ebd02b4`、`final-proof.json` `ec9ad1f87cc544ea39b829252d41fc51ad06fb9e0f3d76feac5b4bec9332636f`、`resource-readback.json` `33493d71b104a5ac08a40940067ca62562d512d0f0372d366eed5e8fd47c4839`、`old-source-readback.json` `615df63b3b1ce4a0010f3f0bc3a87202c0a77395462aafde0ab1f6b201bac2f9`。A1 的独立汇总 `final-readonly.json` SHA-256 `38690de5c1d52d19f24f110df5921d94b8d9cddffd6f5650639ef499db7ac70c` 与关键事实一致。

历史数据不变的逐文件证据仅覆盖上述两个旧 r2j 数据目录共 5 个文件；没有全 NAS 的前后哈希基线。r2k 的原始合成来源恢复事实由恢复流程验证，且克隆最终停机。A4 的查询只读，未运行任何 NAS 写入、清理、重放或发布命令。

## 下一步

协调者可将本次 r2k 隔离验证标为已独立验收，并按既定集成流程审查 A1 最终交接及固定提交。生产发布或真实数据迁移需另行范围和证据。
