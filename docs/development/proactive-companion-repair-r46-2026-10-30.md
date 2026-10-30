# 主动陪伴 R45 / R46：完整基线与空轮观测

R46 `sha256:f71edb94b2b7383c2c796fba9406a3b538b2d86c46fab9bc70c500a97de5c8f3` 已部署健康。Companion `042c537f906ea6f39cd09e3ff220787f00736c9d`，宿主 `f834763c967afb3baae40cc34e970064428db643`。六业务、五观测及既有鉴权 TLS ready200/9项通过；37挂载、1167配置文件、14其他容器和20GiB容量守护保持，仅原 Gateway 启动租约续期。没有数据迁移、权限、模型、超时或业务调度改动。

PC-01 当前为 **verified_production**：本版连续100条已有出站中 98 条归因完整，没有生成验收请求。镜像内原生 CLI 一条命令完成全部基线字段读取，数据库 repeatable-read/read-only=on，日志和宿主观测也在该事务生命周期内读取；文件系统与PG仍不是全局原子快照。失败原因读取当前 last_failure 并兼容旧字段。

R45 新观测暴露了继承计数误判：全库累计写入被当成单个任务进度，且 direct.work 未装配。R46 修正为观察本轮写入并接回既有包装；窗口扫描使用明确 bool。空轮不写日志、不增加业务进度，诊断计数仅在内存保留最多61个秒桶，不新增队列或调度器。

本次8个循环的实际观测区间约 59.94 秒，空轮合计约 1018.98/分钟，失败比例 0.0000。口径是循环进度信号，包含高频 core.tick；不能与旧文档未分范围的约110/分钟直接比较，也不代表业务完成率或达到后续性能目标。

定向验证：R45 Companion 22项、宿主18项及29子断言，1项可选 Gateway 本地模块缺失而跳过；R46新增根因及相关调用方8项通过。静态、格式、diff和镜像字节/运行身份核对通过，复用输入未变结果，没有全量套件或额外模型/QQ测试。

固定 R38 新决策 cohort 当前 14/22（63.64%），24小时完成=False；所有失败、unknown和interrupted保留。真实日记草稿 0 行，三个连续真实日期仍待验。PC-07原真实主动消息、幂等和未回应抑制证据保持。W1仍未完成，W3禁止。

基线：[完整快照](proactive-companion-baseline-r46-2026-10-30.json)。精确来源、检查和限制：[发布收据](proactive-companion-repair-r46-2026-10-30.json)。原R45原始观测和初始部署截面保留在收据中；未把其错误计数当成验收通过。

未验证项：历史缺失归因不能回填；本卡不证明真实日记、长期生活质量或24小时成功率。回退为代码镜像回退，原业务事实和未知回执保持。

实际执行的一条只读采集命令（在现有部署宿主执行）：

```sh
docker exec --user 10001:10001 tianshu-unified-tianshu-1 /opt/tianshu-venv/bin/python -B /opt/tianshu/distribution/proactive_baseline.py --settings /config/modules/companion/settings.json --runtime-state /run/tianshu/host/business-host.json --companion-logs /data/logs/companion --platform-logs /data/logs/platform --log-minutes 5
```
