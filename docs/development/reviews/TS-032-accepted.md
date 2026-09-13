# TS-032 可信 HTTPS 接线验收

2026-09-14。实现5e010957，交接/集成69b29f3a6b8cd61d39d733870d136de2caeb75f0。

协调者审查完整差异：唯一生产改动为 Authenticator 的部署级 issuer_ca_file，以标准库验证非空绝对路径 CA；原默认验证、固定 HTTPS、禁环境代理和重定向保留。无关闭验证或任意请求传入信任配置。没有新增依赖或改变共享合同。

设置 TIANSHU_TEST_CERT_PYTHON 为现有 bundled Python 后，协调者执行产品全套：112 passed，11.81 秒，2 既有第三方弃用警告。新增29项验证真实TLS与合成issuer响应，含CA/主机名/用途/有效期、身份及关联拒绝；HTTPS身份成功后来源缺失仍503。没有操作系统信任库或生产数据。

快进合入记忆协调检出。允许TS-050仅更新固定Memory基线并定向验证真实Platform HTTPS resolver，减少B适配依赖；成功也不表示SourceAuthority已接通。来源事实、可信同步与读取失效屏障仍待双方最小契约和实现。Core画像消费者可按TS-021独立开发。
