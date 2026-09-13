# 天枢架构研究资料目录

整理日期：2026-09-13。将既有架构调研的 49 组引用按来源整理为 54 条来源记录、10 个主题和 5 条项目设计笔记。保留原引用、既有访问日期、使用章节，以及现有研究文档的位置和内容版本。

这是项目私有的本地种子目录，全文尚未由本次转换归档，也没有导入在线数据库或建立语义索引。旧研究中的“访问日期”不表示本轮重新验证了来源。

[结构化资料目录](architecture-research-seed.json) · [自动整理模块设计](../architecture/research-and-knowledge-library-design.md) · [原完整蓝图](../product/tianshu-complete-product-v1.md)

## 分类

| 主题 | 来源数 |
| --- | --- |
| 聊天档案与来源 | 3 |
| 陪伴、情绪与角色生活 | 7 |
| 记忆引擎与检索评测 | 4 |
| 日记、故事与一致性 | 4 |
| 小屋界面与资料资产 | 3 |
| ComfyUI 与图像生成 | 7 |
| 游戏功能、订阅与下载 | 7 |
| 家庭设备、网络与健康 | 12 |
| 项目与开发交接 | 2 |
| 模型网关与协议 | 5 |

来源形式：网络引用 42 条、本地文件引用 7 条、Forgejo 引用 5 条。所有记录默认留在本项目范围，未据此改变来源访问权限。

## 本次研究的文献入口

- [Kim 等，FANToM，EMNLP 2023](https://aclanthology.org/2023.emnlp-main.890/)
- [Park 等，Generative Agents，UIST 2023，arXiv v2](https://arxiv.org/html/2304.03442v2)
- [Marsella、Gratch，EMA，Cognitive Systems Research，2009](https://www.sciencedirect.com/science/article/pii/S1389041708000314)
- [Wu 等，LongMemEval，ICLR 2025](https://arxiv.org/abs/2410.10813)
- [Yang 等，Re3，EMNLP 2022](https://aclanthology.org/2022.emnlp-main.296/)
- [Yang 等，DOC，ACL 2023](https://aclanthology.org/2023.acl-long.190/)
- [Wang 等，Generating Long-form Story Using Dynamic Hierarchical Outlining with Memory-Enhancement，NAACL 2025](https://aclanthology.org/2025.naacl-long.63/)
- [Li 等，Lost in Stories: Consistency Bugs in Long Story Generation by LLMs，Findings of ACL 2026](https://aclanthology.org/2026.findings-acl.410/)

## 已保留的设计笔记

以下是本项目的设计归纳，不能当作来源论文已证明这些工程效果。每条在结构化目录中关联对应来源。

- 本项目将即时对话、生活推进与后台整理分开，并区分共享世界中各角色所知。
- 以统一任务、来源范围和预算比较记忆实现，保留原文引用；项目效果仍需自己的评测。
- 日记和长篇写作按素材、提纲、生成与一致性核对编排；长故事研究不能直接保证中文日记风格。
- 本地生图按真实工作流及能力绑定参数；角色设定、衣物和输出来源需要保持可追溯。
- 模型网关优先保留原生协议，显式处理模型与思考参数映射，不把旧网关的覆盖行为当成完整透传。

## 后续怎样继续整理

后续采集器可按来源继续获取可保存的正文、提取完整语义片段、核对版本并建立索引。已有来源不重新创建一份；新研究增加自己的笔记和引用关系。只见到搜索摘要的条目保留为线索，不生成不存在的全文引用。

完整设计文档仍在原项目中；目录索引这些文件，不另外复制一套可独立修改的正文。每次运行只按任务读取相关来源和笔记，不把本目录全部放进提示词。
