# NAS 容器枚举竞态窄修

基线 `e274733`。实际部署在临时构建容器消失时，guard 的 `ps -aq` 与全量 `inspect` 之间产生 `docker_command_failed`，导致现役十 owner 被 fail-close。修改前只读核对 NAS 已安装 guard 与本地源码逐字相同，SHA256 为 `f4f6560e6314bc85001229c3b2d1ed003bde72af5bb0be65b94d06a27ca86bfc`；未写 NAS。

`Docker.snapshot` 仅对 `docker_command_failed`、`docker_inventory_changed` 完整重新枚举并 inspect 一次。两轮共用原 25 秒采集预算，保留现役 30 秒 watchdog 和 fail-close 总预算；不可用、非法库存及非法元数据直接拒绝。重试不缓存、不筛掉消失 owner、不放宽 `assess` 的完整 owner、锁定 ID、镜像或挂载检查。

实际验证使用现有 unittest 及隔离 subprocess/Docker 元数据夹具，不连接 Docker daemon。新增临时容器消失用例在修改前复现相同 `docker_command_failed`；修复后 `python -m unittest discover -s tests/resident_capacity -p test_guard.py -v` 的 **29 tests 通过**，覆盖完整重枚举、缺项/错 ID 库存、最多一次重试、持续失败后锁存并停止十 owner、owner 丢失/挂载变化/身份替换拒绝、无关错误不重试及预算耗尽。

`ruff check ops/resident_capacity/guard.py tests/resident_capacity/test_guard.py` 与 `git diff --check` 通过。先 fetch 核实 GitHub main 为 `e2747339658e8568ebd51e10e7ddd462710479ce`，本地提交后以非强推更新 main。NAS 安装、原文件备份、已锁存停机的冷备接续及启动验收由主协调者执行；本提交本身不表示 NAS 修正版已安装。
