# TS-023 有界查询传输恢复验收

2026-09-14，实现c7c0a869，交接/集成4f22c031deb2d193ca66c73ef7c5a41da3872fc6。协调审查唯一生产改动JsonService及所有调用者/白名单说明与新回归。

仅5个正式POST查询及当前生成回执精确GET允许响应头前RemoteProtocolError/ReadError/WriteError恢复一次；相同序列化请求和共享客户端，整体15秒含读体。HTTP拒绝、坏JSON/schema、证书/连接/超时、响应体错误不重试，取消传播。input授权、生成/发送/写操作明确排除。未全局禁keepalive，不关闭共享客户端干扰并发轮次。

协调显式TLS与固定Memory联合后全套108 passed、95 subtests passed，24.03秒，无skip；ruff与完整diff通过。真实TLS默认5秒idle受控断连后新连接成功、T2不被中断，写正文已接收后丢响应不重放，sender维持unknown/closed_unknown。

快进合入，允许TS-050固定新Core与Memory575941e做新联合复验。原协调W5异常类型未记录，保留高置信推断与独立确定性复现的区别；本组件通过不自动覆盖原失败或宣称完整L0。
