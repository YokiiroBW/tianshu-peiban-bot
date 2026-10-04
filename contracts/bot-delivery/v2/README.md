# bot-delivery v2 实现合同

主协调审核发布；生产者/消费者联合与实机验收待完成。


POST Platform /internal/v2/bot-delivery/send、/query、/cancel。主动不借turn_sequence；expression_id和各reply_id稳定。Platform由真实绑定验证Actor/接收者/渠道；同一队列发放实际出站序号。分段文字及原件分别结算，partial/unknown不等于完整送达。query仅查原request，cancel不等于远端已撤销；无可靠未产生效果证明不得重发。普通/Direct保持既有v1身份，同一内部Delivery/平台发送队列处理。
