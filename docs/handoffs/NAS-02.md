# NAS-02：修复群晖一次性容器输出丢失

基线 `7fa511eb1f773096790258ac6a1d30cf09c529f8`，独立工作树/分支 `codex/nas-oneoff-output`。用户返回 b 轮失败后直接排障、修复，不修改正在运行的代码，不重放 b 原任务。

b 轮四镜像 build 全部退出 0，平台依赖一次性容器也退出 0，清理后解析捕获 stdout 时失败；主服务尚未创建。错误为通用 `linux_runtime_failed`，`stop_confirmed=true` 对应空容器集合，不是四服务正常停写通过。NAS 独立复现：直接 Docker 创建的 probe 附着输出 489 字节，按 Compose 同构配置创建的 probe 附着输出 0 字节，而它自身的 docker logs 有完整 489 字节 JSON。未发现产品依赖失败。

修复限定 linux_lifecycle.py、linux_runtime.py、生命周期测试/模拟器及本交接：capture 输出为空时，在正常退出和身份检查后读取同一个固定容器的 stdout 日志（tail 100），不重新启动/重放操作。读取失败、空结果、超长或非 JSON 对象时拒绝并保留容器；解析通过才进入原稳定退出/持久化证据/删除固定容器流程。报告只记录输出来源，不记录凭据/ref 原文。增加静态步骤名的开始/结果提示，不打印命令参数或应用原始日志。

验证：packaging 完整 84 项、0 skip、32.710 秒通过；Ruff 与 diff 检查通过。初轮模拟测试有一项失败：旧模拟器对 publish 返回空 stdout；按公开 CLI 的 JSON 输出契约补齐模拟器后全套通过。新增覆盖 attach 丢失时不重放、无有效日志时不删除/不误判。

真实 NAS 修复验证：在无产品挂载的隔离探针中调用改后的 Lifecycle.oneoff，成功经 container_stdout 恢复 JSON、读取 16 个发行包；正常退出/稳定期通过、证据持久化后删除探针容器，剩余 0。另两只首次定位探针经固定 ID/归属/退出 0/无 OOM/无重启/无产品挂载复核后删除；无卷删除。证据在协调根 `.runtime/nas-wave1/nas02-real-lifecycle.json`，原 b 失败报告 `linux-executed-b.json` 保留。

下一步：新工具快照、新 c 标签/scope、只读计划和管理员完整四核心验证。此局部实机成功不等于四核心、日志、恢复或正式发布通过。
