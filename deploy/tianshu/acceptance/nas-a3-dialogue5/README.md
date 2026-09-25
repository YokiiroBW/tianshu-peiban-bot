# NAS-A3 dialogue5 一次性合成验收归档

本目录保存 `cycle-20260925-dialogue5` 在 NAS 上**实际执行**的操作器、后续请求探针、标准证明解析器和阶段预算。`.gitattributes` 保留这些文件的原始换行字节，保证从提交取出的 SHA-256 与 NAS 文件一致。它仅供审查和追溯；绝对 NAS 路径、一次性启动标记和已执行 scope 都固定在操作器内，不能作为新验收或通用部署入口。

## 执行绑定

- 一次性 scope：`/volume2/tianshu-v2-validation-wave1/accept-20260925-a3/cycle-20260925-dialogue5`；Compose 项目 `tianshu-accept-a3-dialogue5`。已执行的入口是：`sudo -n /volume2/Dockers/tianshu-v2-validation/wave1-20260923a/tooling/venv/bin/python -B /volume2/tianshu-v2-validation-wave1/accept-20260925-a3/cycle-20260925-dialogue5/operations/complete-synthetic-cycle.py`。该 scope 的 `complete-cycle.started.json` 禁止重放。
- 操作器在同一 Python 进程中临时包裹 `linux_runtime.prepare` 和 `Runner.__call__`，于 `finally` 恢复；仅 `synthetic-dialogue` 场景捕获并严格解析标准探针的两行 stdout（版本化证明 JSON 和原有成功行）。后续请求使用容器内 `delivery_probe.py`，合成测试密码经 stdin 传递，不写入归档。`linux_runtime.Runner` 默认路径不包含此严格解析器，通用 `bundle.TOOLS` 也不包含它。
- 仅对 `provider-synthetic` 的一次性发布回执把 900 秒可用窗口延至 1800 秒；Platform pin 的默认上限为 3600 秒。Gateway origin 回执 TTL 仍为 300 秒。没有改真实模型配置或 Dockge。

## 原样文件与摘要

| 文件 | NAS 实际位置 | SHA-256 |
| --- | --- | --- |
| `complete-synthetic-cycle.py` | `operations/complete-synthetic-cycle.py` | `225fa65f93d28ae4381ffc819150e00ddb3bff5b08ee0a51d32688b225a7bb02` |
| `delivery_probe.py` | `operations/delivery_probe.py` | `b9972a41d706523c10d1824396ad81b1cf9e40d626f825303c0ede925250db32` |
| `synthetic_probe_proof.py` | `operations/tooling/synthetic_probe_proof.py` | `d9d63d957704ef89bd849cfe320e9ada424441c3c9ba2b1450b8f494aa53414a` |
| `phase-budget.json` | `operations/phase-budget.json` | `e41719c7c8fd666ca0acc568cb27483fb73c4679ad654461c7fa788fc9847167` |

阶段预算原文件将父网段写作 `10.205.36.0/20`；按 CIDR 规范化后是协调分配的 `10.205.32.0/20`。为保留原始摘要，此处未改动归档文件。

标准探针是 bundle 的 `tools/container_probe.py`（SHA-256 `1cece914b3d1d1f3d45a89a6b9c9380cd3bd35fd307c7375fb52fc9664dacfa9`）；A3 重新授权工具在执行时的 SHA-256 是 `3439ca0d95d9773b9b2eb87acfc0e8e518a8e835204158d31d57fdbc437bb378`。本地冻结源清单已重算 SHA-256 `ffa41ae611f00bdec8eae48aa2b20809d6d9665bc61fd3822425f48058ab21fa`。bundle 归档 SHA-256 `90ec410dea520c08ae250ae0b3537f781eff5ba4cad5d03dbe1173d8cd98bd1d` 来自当轮记录；当前本地没有原始归档可独立重算。每个产品的提交、源码归档摘要、唯一镜像标签、镜像 ID 和最终容器 ID 见[dialogue5 证据](../../../../docs/development/nas-a3-dialogue-evidence-2026-09-25.json)的 `successful_scope.source_to_image_chain`。

最终周期报告 SHA-256 `08f2a8ff537e0ab84a1c992ff41f14c4305abad3ae6881e12c9e23aa2d562aa7`；Linux 执行报告 SHA-256 `aad82e87842651a57a121707033733475bd19cc64a5c0881aa2f3997ae95a32a`；重新授权结果 SHA-256 `15f3afe6f16496f32814c6afa6b4af513928862a7f5b2a17e907cab58e2bd4b9`。报告摘要和保留的失败 scope 见[交接](../../../../docs/handoffs/NAS-A3.md)。

## 证据边界

- 旧 Gateway 停机 345 秒后重启为 exit 1 且非 OOM；自然过期归因依据时序与状态检查，没有单独捕获专用过期错误码。
- 最终周期报告记录 `result=passed`，但操作器最外层进程退出码没有单独归档。失败时的清理是尽力而为，若 Docker inspect 失败不能保证完整停止。
- 源码到镜像的链条由冻结清单、构建成功记录、镜像和容器 inspect 共同支持，没有签名 OCI provenance。本次仅为隔离合成验收；`release_ready=false`，真实模型、持续负载及产品默认 Runner 行为仍未验收。
