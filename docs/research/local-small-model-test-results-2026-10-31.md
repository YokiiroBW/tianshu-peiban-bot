# 本机六个小模型：安装与实测结果

整理日期：2026-10-31；实测日期：2026-10-30。本文只整理已经保存的本机原始记录，本次没有重新运行模型。

六个候选均已安装并实际推理。**在本轮36道中文短判断评分题上，Qwen3.5-2B Q4_K_M与MiniCPM5-2B Q4_K_M同为31/36，Qwen更快；三款分类器更快，但本轮准确性不足。** 这只是小规模合成题初筛，尚未证明“主模型＋本地辅助”比现有天枢整体更快或更好。

使用边界保持：本地小模型只能辅助；主要判断、日程规划、陪伴表达与正式记忆整理仍由主模型承担。完整日程替代与前置分流数据作为已做过的测试记录保留，不代表已经接入生产。

## 1. 实测机器与运行方式

| 项目 | 测试时记录 |
|---|---|
| 主机 | 192.168.31.230 |
| CPU / 内存 | i7-7700HQ，4核8线程、AVX2；内存15,871 MiB |
| GPU / 驱动 | GTX 1050 Ti，4 GiB显存；NVIDIA 580.178.04 |
| GGUF运行时 | llama.cpp b11960，commit 8642541b2，CUDA 12.8 |
| Python推理环境 | PyTorch 2.7.1+cu126；Transformers 5.22.1；Laya 0.4.17；GLiClass 0.1.21；GLiNER2 2.1.0 |
| 隔离安装目录 | `/home/yokiirobw/tianshu-local-eval-20261030` |
| 生成模型服务 | 回环地址127.0.0.1:18990；单并发、2个CPU线程、4096上下文、GPU offload |
| 测试结束记录 | 推理进程停止，未配置开机常驻；没有切换天枢生产模型路由 |

测试时未修改生产容器配置、系统驱动、线上模型绑定，未发送QQ测试消息、调用付费模型或重放unknown任务。结束记录不是本次重新检查的当前进程状态。

## 2. 实际测试的模型版本

以下大小只指对应的权重文件，不包含分词器、支持文件、Python环境和共享编码器。

| 文件 / 安装名称 | 来源仓库 | 固定revision前12位 | 权重大小 |
|---|---|---|---:|
| MiniCPM5-1B-Q8_0.gguf | openbmb/MiniCPM5-1B-GGUF | `3d55fac80935` | 1100.1 MiB |
| MiniCPM5-2B-Q4_K_M.gguf | openbmb/MiniCPM5-2B-GGUF | `2079a22f3bea` | 1489.0 MiB |
| Qwen3.5-2B-Q4_K_M.gguf | unsloth/Qwen3.5-2B-GGUF | `f6d5376be1ed` | 1221.5 MiB |
| laya-multilingual / model.safetensors | convaiinnovations/laya | `7b928d828b7b` | 614.0 MiB |
| gliclass / model.safetensors | knowledgator/gliclass-multilang-mini | `0bd888b6c3ef` | 541.2 MiB |
| gliner-decide / model.safetensors | fastino/GLiNER2.5-multi-Decide | `e173bca1f0c4` | 1096.2 MiB |

Qwen使用Unsloth提供的GGUF转换文件；MiniCPM使用OpenBMB仓库文件。完整revision、SHA-256与文件清单见文末安装证据。环境还保留固定revision的mmBERT-base依赖缓存，不能仅按表中单文件大小估算完整部署。整个隔离安装目录当时约13.18 GiB，包含环境、运行库、缓存与证据。

## 3. 中文短判断：六模型同题结果

48条人工构造场景，分为12条开发题、36条独立评分题；日程操作意图、下一目标动作、记忆候选分类各有12条评分题。含否定、转述、假设、归属不明、临时兴趣、明确约定和unknown回执。

生成模型使用温度0、seed42、关闭思考、短JSON与枚举Schema、最多40输出token，禁用prompt cache；有记录的cache_n均为0。分类器使用各自原生接口。均先预热两次。采用配置由开发题选择，没有用评分题反复挑配置。

| 模型与量化 | 日程意图 | 下一动作 | 记忆归类 | 合计正确率 | GPU中位耗时 | GPU p95 |
|---|---:|---:|---:|---:|---:|---:|
| Qwen3.5-2B Q4_K_M | 10/12 | 11/12 | 10/12 | 31/36（86.1%） | 513.1 ms | 649.6 ms |
| MiniCPM5-2B Q4_K_M | 10/12 | 11/12 | 10/12 | 31/36（86.1%） | 649.3 ms | 806.2 ms |
| MiniCPM5-1B Q8_0 | 1/12 | 6/12 | 3/12 | 10/36（27.8%） | 281.5 ms | 387.3 ms |
| Laya multilingual | 3/12 | 6/12 | 5/12 | 14/36（38.9%） | 47.0 ms | 53.1 ms |
| GLiClass multilang-mini | 4/12 | 6/12 | 5/12 | 15/36（41.7%） | 43.9 ms | 245.8 ms |
| GLiNER2.5-multi-Decide | 2/12 | 8/12 | 3/12 | 13/36（36.1%） | 64.6 ms | 221.3 ms |

采用的分类器配置：Laya原生choice、head budget=512；GLiClass直接分类原始场景；GLiNER使用英文任务说明和中文输入。GLiClass改输入后开发题3/12→7/12；Laya增加head或改英文说明未提高开发题总分；GLiNER英文说明3/12→4/12。

生成模型经本机HTTP调用，分类器在进程内调用，计时边界不同。这里是热态单请求时延，不含权重冷加载、正式业务RPC、检索、专业执行或主模型回复。GGUF元数据的load_s只是评测客户端准备时间，不能当作模型权重加载速度。p95按原脚本nearest-rank口径计算。

两个2B模型仍有重要错误：Qwen将“明早七点叫醒我，今晚不用提醒”判成无操作意图，将正文服务不可用、应等待的目标判成继续。31/36不能作为自动修改日程或写入正式记忆的充分依据。MiniCPM5-1B的低分只适用于本次量化、模板、非思考模式与提示组合，不能外推其全部模式。

## 4. CPU补测

下表都只有12条开发题，用于运行与适配检查，不能与上一节36条评分题的准确率混排。没有完成两个2B生成模型的同题CPU评分，不能推算其CPU时延。

| 模型 / 配置 | 开发题正确数 | CPU中位耗时 | CPU p95 |
|---|---:|---:|---:|
| MiniCPM5-1B Q8_0 | 3/12 | 1030.8 ms | 1791.9 ms |
| Laya multilingual | 5/12 | 1001.4 ms | 1193.9 ms |
| GLiClass，原始场景＋FP32 | 6/12 | 551.7 ms | 993.3 ms |
| GLiNER2.5，修复支持文件后 | 3/12 | 986.0 ms | 1366.7 ms |
| GLiClass，最初输入及自动精度 | 3/12 | 5679.9 ms | 6387.9 ms |

GLiClass最初CPU输入及自动精度较慢；后续同时改用了原始场景和FP32，不将整段提速全部归因于精度。GLiNER初次装载缺少encoder_config/config.json；补齐同一revision支持文件后才完成推理。原失败与早期配置结果均保留。

## 5. 完整日程协议兼容性

8个合成场景复用当时天枢life_goals原提示词及validate_choice。第一版把相对秒数误放进绝对时间字段，已排除，原文件保留；以下只统计修正后的第二版。unknown-wait是模型前已被程序拦截的边界诊断，因此另列实际可到达的7题结果。全部40条有效版本输出已由实际strict_json离线复核，结果一致。

| 模型 / 输出方式 | 合同合法 | 合法且动作正确 | 可到达7题合法且正确 | 中位耗时 |
|---|---:|---:|---:|---:|
| MiniCPM5-1B，原提示词 | 0/8 | 0/8 | 0/7 | 1.99 s |
| MiniCPM5-2B，原提示词 | 2/8 | 2/8 | 2/7 | 2.82 s |
| Qwen3.5-2B，原提示词 | 1/8 | 0/8 | 0/7 | 6.15 s |
| MiniCPM5-2B，增加 JSON Schema | 8/8 | 5/8 | 5/7 | 3.38 s |
| Qwen3.5-2B，增加 JSON Schema | 8/8 | 6/8 | 6/7 | 4.02 s |

直接原提示词会出现代码围栏、缺字段、delay_seconds=0和错误observation_inspired等问题。Schema在这组题中修复了结构合法性，但没有消除所有动作误判。该测试不构成替代主规划的交付，也不能用它评判尚未测过的检索线索、主题标签或候选关联效果。

## 6. 入站任务分流测试

另有60条合成分流样本：12条开发题，48条评分题；评分题分为代码12、搜索12、应交主模型24。口径为“单一明确任务可派给专业能力，混合任务、否定、引述、含糊追问等由主模型处理”。本节是独立题集，不能与短判断题合并成一个总准确率。

| 模型 | 总正确 | 代码 | 搜索 | 主模型类 | 热态中位 / p95 |
|---|---:|---:|---:|---:|---:|
| GLiClass multilang-mini | 33/48（68.8%） | 12/12 | 9/12 | 12/24 | 44.2 / 61.1 ms |
| Laya multilingual | 26/48（54.2%） | 9/12 | 5/12 | 12/24 | 49.4 / 51.0 ms |

GLiNER2.5-multi-Decide完成31条输入后因达到90°C停止；Qwen3.5-2B冷却后增加每请求1秒间隔，但完成12条开发输入时再次达到90°C停止。两组都没有完整评分集，排除排名。

提高分数阈值也未解决误派：开发题选出的Laya阈值0.97，在评分题接受12次派发，只有5次正确；GLiClass阈值0.95接受1次，0次正确。Laya曾以0.998将“写代码写到崩溃、只想有人陪”派为code；GLiClass以约0.958将“翻译write a Python function、不需要实际写代码”派为code。高分不等于已经校准的可靠概率。

本轮尚未证明可以让这些现成分类器直接接管全部入站消息。前置分流已按用户要求暂缓，没有切换线上路由，也没有测量完整“专业执行→主模型统一回复”的耗时收益。

## 7. 温度、稳定性与结果边界

连续完整目标输出时最高95°C，暂停后冷却至75°C，再补短检查。分流轮按90°C条件停止两次。记录没有定位散热根因，也不能证明每个时刻都在热降频。

第一轮136次资源采样的显存最大读数约1621 MiB；这是离散采样最大值，不是每个模型的显存峰值保证。有效生产健康采样115次：104 healthy、5 unhealthy、6 starting；期间另有R48→R49串行发布，未建立这些瞬时状态与小模型负载的因果关系，不能写成“全程零影响”。测试结束时生产healthy、容量守护active。

本轮已证明六模型可安装、可推理，并取得上述组件测量。仍未证明：自然中文聊天准确率；主模型＋辅助的整体收益；主题标签/检索线索/候选关联的效果；主模型同题对照；长期并发、吞吐、常驻散热与生产参与质量。不能把短判断成绩直接当作这些任务的排名，也没有据此选定生产辅助模型。

## 8. 原始证据与复现入口

以下为本地运行目录，原始权重与证据不随文档进入Git。完整数据和失败记录仍保留：

- [六模型结果汇总](C:/YOKI/Codex/tianshu-peiban-bot/.runtime/local-model-eval-20261030/evidence/results/summary.json)
- [短判断输入与固定标签](C:/YOKI/Codex/tianshu-peiban-bot/.runtime/local-model-eval-20261030/evidence/cases.json)
- [GGUF完整revision、大小与SHA-256](C:/YOKI/Codex/tianshu-peiban-bot/.runtime/local-model-eval-20261030/evidence/models/gguf-manifest.json)
- [分类器版本、权重与支持文件](C:/YOKI/Codex/tianshu-peiban-bot/.runtime/local-model-eval-20261030/evidence/models/classifiers-manifest.json)
- [Python依赖版本清单](C:/YOKI/Codex/tianshu-peiban-bot/.runtime/local-model-eval-20261030/evidence/requirements-installed.txt)
- [日程协议第二版输入](C:/YOKI/Codex/tianshu-peiban-bot/.runtime/local-model-eval-20261030/evidence/planner-cases-v2.json)
- [40条输出的strict_json复核](C:/YOKI/Codex/tianshu-peiban-bot/.runtime/local-model-eval-20261030/evidence/results/contract-recheck.json)
- [启动脚本真实验证](C:/YOKI/Codex/tianshu-peiban-bot/.runtime/local-model-eval-20261030/evidence/results/launcher-verification.json)
- [分流题集与标签口径](C:/YOKI/Codex/tianshu-peiban-bot/.runtime/local-model-eval-20261030/routing/cases.json)
- [分流结果与误派分析](C:/YOKI/Codex/tianshu-peiban-bot/.runtime/local-model-eval-20261030/routing/analysis.json)
- [90°C停止记录](C:/YOKI/Codex/tianshu-peiban-bot/.runtime/local-model-eval-20261030/routing/evidence/results/stopped.json)
- [Qwen第二次90°C停止记录](C:/YOKI/Codex/tianshu-peiban-bot/.runtime/local-model-eval-20261030/routing/evidence/results/stopped-qwen.json)
- [首轮原始输出、脚本和清单归档](C:/YOKI/Codex/tianshu-peiban-bot/.runtime/local-model-eval-20261030/evaluation-evidence.tar.gz)
- [分流原始输出及采样归档](C:/YOKI/Codex/tianshu-peiban-bot/.runtime/local-model-eval-20261030/routing-evidence.tar.gz)
- [已安装环境使用说明](C:/YOKI/Codex/tianshu-peiban-bot/.runtime/local-model-eval-20261030/README-installed.md)
- [完整运行、辅助定位与相关研究](C:/YOKI/Codex/tianshu-peiban-bot/docs/research/tianshu-runtime-and-local-decision-model-research-2026-10-30.md)

短判断数据集SHA-256：`ea0567537cbf6227edbaa7ccb5bd6afd9d6b062e59fc06536d2b3fcd0976e419`；分流数据集：`1e7c52bd0d3087659de6bba328c590a504a1961154e773aeac5eb7913fecc0b1`。

启动复现使用现有说明中的`./serve.py --model qwen2b`，另外两个已安装选项为minicpm2b、minicpm1b；当前文档整理未执行这些命令。原生启动验证曾通过health与模型列表，并用一次“继续阅读目标”短JSON得到正确continue，耗时747.7ms。它只证明该次启动和请求，不能代替前面整组评分。
