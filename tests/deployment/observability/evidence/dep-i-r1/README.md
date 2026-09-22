# DEP-I R1（仅本地协议替身与CLI证据）

本目录新建，原dep-i-local及DEP-B证据未修改。
`summary.json`记录真实执行OwnedContainers、由显式FakeDocker协议替身返回观测的结果：
0正常；143、2、137均abnormal_container_exit；超时stop_unconfirmed且保留running。
各case目录保存初始固定ID、SIGTERM尝试/响应、退出事实及实际构造的Docker命令。
这些不是Docker/Linux执行证据；各report的18个Linux维度全部not_run。

`plan/`、`windows-refusal/`为R1源码实际CLI运行，分别返回0及1/linux_host_required。
报告绑定本次工作字节hash和G固定planned例子原字节hash；没有Linux/NAS声明。

本轮测试工具输出：test_container_lifecycle.py最终11项0skip通过0.154s；
test_linux_acceptance.py相关13项12过/真实flock 1skip，0.069s。未重复旧TLS/期限成功检查。
