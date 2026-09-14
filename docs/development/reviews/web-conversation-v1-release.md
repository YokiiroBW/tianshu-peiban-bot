# 网页会话合同1.0.0实现基线发布

2026-09-14。基于TS014候选，TS071已确认字段可实现。候选manifest原始字节SHA为080be3c...33ba7fb，内容hash按LF；协调先发现口径差异，核对全部文件后统一发布包LF口径，未采用失败时的残缺文件。

发布contracts/web-conversation/v1，manifest SHA256 e493a1b5d0f4cec8d55995553faf84042f4c33a59365d15423e57f4dc70a6c09。原text/source/profile发布包不变。协调补实际reply.state=sending并要求不可显示正文；正文content_state与送达state分别保存，超所有数组/字段/1MiB预算均失败，不截断组。

一次稳定包验证通过4结构正例/3结构反例、3关系正例/3关联反例及固定文字依赖hash。实际跨用户/过期/来源失效/分页/重连授权仍待Core和Platform产品测试，authorization-examples是场景不是通过证据。仅固定web/self_private首版快照与复用sender/cancel接口；不代表网页已连通。

TS014负责登录/同源聚合/持久web sender与界面，Core唯一负责人接web-snapshot和namespace sender选择。严禁跨Core数据库读、服务凭据入浏览器、旧origin自然到期就清历史、将不相关记忆域变化当聊天撤回。历史展示不是模型召回授权。
