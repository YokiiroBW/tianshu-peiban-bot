# 天枢业务链路前两项实现与隔离实测

两项候选实现及实测已完成，**本轮未采用到产品主线，也未部署**。重复序列化和请求内纯计算确已减少；PG整体收益有限，未达到各场景全面持平SQLite。SQLite写入CPU保护尚不能确认，因此按已批准方案保留原实现，不把候选交给后续任务自动使用。

## 实现与输入

- 基线 `4dbb8163524af55822d492cfdfda740090b569e9`；预算提交 `61304513356c4bb9293f62a2473021f95664812e`；两项完整提交 `4d33d053f0107553019e8dd5f08c80a086a9ae81`。共享存储0.1.3、依赖、SQL、合同及锁模型未变。
- Memory逐组预算改为精确累计固定外壳、数组内容和逗号；Knowledge逐块预算复用原有omissions预留。完整组、顺序、UTF8及错误边界保持。
- SourceAuthority和SourceSnapshot只复用当次键、canonical及映射；重试重新计算。SQLite不构造PG专用快照。无授权、来源或文档通过结论缓存。
- 独立语义审查未发现阻断项。六次fresh revision、三次常见owner RPC、完整合同/关系规则、锁后独立读取、CAS、负向广播、guard先于commit、PG持久提交和SQLite WAL/FULL保留。
- 实际诊断的来源fingerprint调用从240降到120；本地关键词的service canonical返回字节从约385.6KB降到26.4KB。诊断含hook，不能冒充正式延迟收益。

## 功能验证

- 完整产品：1745通过、131跳过、1失败；原生PG专项130/130通过，覆盖其中130个跳过，合计1875个不同节点通过，余1既定profile场景跳过。
- 唯一失败是account-403期望403、实际400；在精确4db基线、同一合同夹具独立复现。没有宣称全量全绿或修改该旧问题。

## 三轮原生业务对比

36组、6600次正式操作。表为每轮p50的中位数，负数表示耗时下降；完整p95/p99、所有样本、不利结果及负载见[formal.json](business-flow-performance-2026-10-22/formal.json)。不同场景不混合成一个总提速百分比。

| 场景 | SQLite旧→新 ms | 变化 | PG旧→新 ms | 变化 | 新PG/新SQLite |
| --- | ---: | ---: | ---: | ---: | ---: |
| knowledge_authorized_cohort20_query | 25.104→20.669 | -17.66% | 37.538→36.598 | -2.50% | 1.77× |
| knowledge_authorized_exact_query | 9.621→8.826 | -8.26% | 21.031→21.326 | +1.41% | 2.42× |
| knowledge_fresh_application_query | 15.662→16.333 | +4.28% | 90.519→80.799 | -10.74% | 4.95× |
| knowledge_http_cohort_query | 27.501→23.245 | -15.48% | 42.498→42.355 | -0.34% | 1.82× |
| knowledge_http_exact_query | 10.700→11.149 | +4.20% | 26.836→28.930 | +7.80% | 2.59× |
| knowledge_import_5blocks | 32.542→35.411 | +8.82% | 27.510→33.791 | +22.83% | 0.95× |
| knowledge_indivisible_group_small_budget | 10.218→9.880 | -3.30% | 21.509→23.763 | +10.48% | 2.41× |
| source_sync_authorized_field_recall | 210.611→204.510 | -2.90% | 217.206→218.158 | +0.44% | 1.07× |
| source_sync_authorized_keyword_recall | 261.140→268.824 | +2.94% | 274.844→263.795 | -4.02% | 0.98× |
| source_sync_recall_after_withdrawal | 228.908→225.397 | -1.53% | 230.927→235.024 | +1.77% | 1.04× |
| source_sync_zero_budget | 210.010→201.581 | -4.01% | 212.563→218.518 | +2.80% | 1.08× |
| memory_authorized_field_recall | 2.668→2.781 | +4.23% | 4.909→4.026 | -18.00% | 1.45× |
| memory_authorized_keyword_recall | 69.337→64.889 | -6.41% | 71.381→69.531 | -2.59% | 1.07× |
| memory_complete_group_commit | 32.776→32.029 | -2.28% | 32.033→26.636 | -16.85% | 0.83× |
| memory_recall_after_source_withdrawal | 34.336→31.852 | -7.23% | 36.433→34.595 | -5.04% | 1.09× |
| memory_source_candidate_commit | 89.378→83.278 | -6.82% | 61.666→70.041 | +13.58% | 0.84× |
| memory_zero_budget | 1.900→2.101 | +10.58% | 2.208→2.123 | -3.87% | 1.01× |

PG关键词来源召回约快4%，资料多块查询约快2.5%；来源field短查约慢0.44%，未得到稳定收益。资料短查、HTTP和写入也有不利结果，全部保留。PG普通field仍约为新版SQLite的1.45倍，资料多块约1.77倍，不能说已全面追平。

## SQLite配对保护

- Memory同文件3300对、6600次：完整内容及DB/guard字节不变；每块分两段交换启动角色。exact/keyword配对耗时约下降1.6%；零预算约增加0.58%，六段方向混合，保留该不利值。
- Knowledge同一封存配置、DB、guard、registered和.files共600对、1200次，另120对预热。多块耗时/CPU均约下降11.5%；exact近乎持平；fresh耗时约+0.07%、CPU约+1.06%；HTTP exact耗时约-0.44%。没有省略当前授权、双读hash或最终校验。
- 普通三轮里的Knowledge导入+8.82%未隐去；导入实现与SQL未改，但本轮没有独立导入配对，不能据此承诺所有SQLite写入无退步。
- 写入从相同300组模板克隆，每次真提交、关闭重开并核对ledger/增长/内容/FK/quick_check，保持WAL/FULL。初版240对仅交换物理角色，启动序位仍绑定版本，因此只保留作诊断；同版本初版A/A也保留。
- 修正版按a→b启动并交换版本、完整记录launch_position；补丁A/B和同版本A/A各240测量对、480真提交，另各24预热对，交替执行。原生业务和计时函数没有变化。

| 写入场景 | A/B配对耗时变化 | A/B CPU变化 | A/A配对耗时变化 | A/A CPU变化 |
| --- | ---: | ---: | ---: | ---: |
| local_commit | +6.21% | +6.21% | -0.62% | +0.20% |
| local_full_flow | -2.30% | +1.15% | -2.74% | -3.15% |
| source_commit | +1.05% | -0.27% | +0.56% | +0.40% |
| source_full_flow | -0.06% | -0.13% | -0.64% | -0.76% |

A/A使用完全相同的基线镜像。它暴露的波动不能归因于补丁；也不能把两个百分比直接相减当成已证明无退步。CPU原因仍未定位，置信区间跨1、部分场景更快或源代码路径未变均不单独构成采用依据。本轮到此停止，不再围绕同一状态盲目微调和重复跑分。

## 边界、资源与后续交接

- NAS十个核心容器的ID/镜像/启动及重启记录、五个服务的验证TLS就绪、配置及容量保护哈希在本号窗口保持。测试仅操作专属低权限UUID库、目录及临时镜像；不挂载生产数据/媒体，不访问Redis、真实owner、模型或QQ。
- 所有本号测试库、角色、辅助目录和镜像均按所有权清理，见[cleanup.json](business-flow-performance-2026-10-22/cleanup.json)。源码写权与NAS测试窗口已释放。
- Q18产品装配仍由原协调者处理；Memory main保持e5e1b3。本候选不合入、不推送、不部署、不迁移生产。与31bd9a7仅做过merge-tree无文本冲突检查，没有冒充联合验收。
- 当前范围为300/365组、40个常见来源、200普通资料块及单个活动客户端的合成负载。包含测试机PG真实连接，owner仍为合成响应；不是生产并发或真实跨服务TLS性能证明。正式三套复用旧wall计时；配对提供CPU，带hook诊断另列。
- 结果与原始样本：[证据索引](business-flow-performance-2026-10-22/evidence-index.json)、[功能验证](business-flow-performance-2026-10-22/verification.json)、[工作量](business-flow-performance-2026-10-22/work-counts.json)、[最终写入](business-flow-performance-2026-10-22/writes-balanced.json)、[A/A](business-flow-performance-2026-10-22/writes-aa-balanced.json)、[交付状态](business-flow-performance-2026-10-22/delivery.json)。
