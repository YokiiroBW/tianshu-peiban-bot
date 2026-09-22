# DEP-E 发布清单卷接口 1.1.0

本文件和同提交的 `release-manifest.schema.json`、`release-manifest.example.json`
是 DEP-F 的固定输入。唯一发布清单仍在 deploy/tianshu。旧 1.0.0 继续可读，
`release-manifest.v1.example.json` 保留旧样例；旧消费者遇 1.1 应明确拒绝，不能忽略新增卷。

四产品 products/source、应用卷、合同字段原形不变。新增 observability 绑定根仓库固定
668d69e2c52ea55d9b1f102426196a5286c57bc5 的 deploy/observability，输出固定到部署根的
observability。服务 product=observability，五个单 owner 服务 obs-vector/loki/grafana/prometheus/guard。

| id | host_path（相对唯一部署根） | container_path | owner_service / backup_group |
| --- | --- | --- | --- |
| obs-vector-state | observability/data/vector | /var/lib/vector | obs-vector |
| obs-loki-state | observability/data/loki | /var/lib/loki | obs-loki |
| obs-grafana-state | observability/data/grafana | /var/lib/grafana | obs-grafana |
| obs-prometheus-state | observability/data/prometheus | /var/lib/prometheus | obs-prometheus |
| obs-guard-state | observability/data/guard | /var/lib/guard | obs-guard |

所有新增卷 category=observability_state、mount=true、kind=directory，整目录备份，
不枚举内部 SQLite/WAL/索引文件。Vector 缓冲/位点、Loki WAL/chunks/compactor 标记必须一起保留。
所有应用 writer 与观测 owner 均确认停止后才能取一致快照；guard 对 Vector/Loki 的容量挂载及
guard/vector 对四应用日志的挂载仅只读。日志查询成功、Loki204、对账成功均不授权应用删段。

两个 Compose 项目保持独立网络。核心项目取 deployment.json project_name；日志项目必须显式
`-p <project_name>-obs`，目录为 `<deployment>/observability`，文件为 compose.yaml。
不得依赖日志生成器默认的共享项目名。恢复后两项目均保持停止，协调授权检查完成再放行。

DEP-B configure.py 只读 1.0，因此 DEP-E 使用明确的旧形状投影（剔除新增项、版本为1.0）调用公开
入口，随后校验生成的五个实际写卷与本清单严格一致。投影只作兼容输入，不能交备份消费者代替
1.1 原清单；保留原清单和投影各自 hash。配置和代码也需备份版本引用，秘密仅在受保护部署目录。

示例仍是 candidate：所有应用镜像 digest 空、网页关闭、长期记忆和 Chat Audit 未启用。
这不是可部署或运行通过声明。依赖新产品提交需协调确认集成后重绑，不改本卷接口。
