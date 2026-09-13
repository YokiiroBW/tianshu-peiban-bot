# TS-041 首次交付审查

2026-09-14。提交b6188f22e8b7e04c0757a54f4905bf4ed6e97916。暂不合入，要求修正SSE错误事件解析。

只读审查定位 routing.py StreamObserver 只识别整行`event: error`。协调者从固定Git提交加载解析器复现：相同完整choice、错误事件data及[DONE]，带空格写法error=true/complete=false，无空格`event:error`却error=false/complete=true。两种合法字段写法不能导致相反结果，带错误流不可记为succeeded。

已要求按SSE字段规则解析，并补纯组件及真实本地HTTP流回执回归；旁路观察不改原始转发字节。其他配置/绑定/取消/持久诊断边界仍须最终产品测试，当前没有据离线报告直接合入。生产平台/PG/TLS和其他原生协议未接入属于已披露范围，不当作本次缺陷。
