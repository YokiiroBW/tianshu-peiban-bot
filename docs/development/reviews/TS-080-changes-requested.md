# TS-080 资料I/O持锁需修复

2026-09-14，交付2de7632aaa6a3bfe8d131e952573e3a366c29153暂未集成。代码核对execute的Store.transaction使用BEGIN IMMEDIATE；_import在其内fetch_url/read_file/decode，query/recover/check通过_current读取文件hash。网络/DNS/慢文件因此占用共享聊天Memory写锁，不能只列小库性能限制。

要求完整块改为短事务快照→事务外I/O→重新核验配置权限/项目/目标版本→原子写或读结果验证。幂等、删除/替换、guard及恢复包签名不得退化。项目相关版本不应被无关聊天写入无端冲突。集中验收慢来源期间聊天不阻塞、间隙撤权/替换及文件变更；不重跑未改的成功检查来掩盖结构问题。

其余客户端/MCP/版本安全有限审查并行；暂不重复417项全套，先解决实际调用链阻断。
