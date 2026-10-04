# life-runtime v2 实现合同

主协调审核发布；生产者/消费者联合与实机验收待完成。


POST /internal/v2/life/manage、/internal/v2/life/read、/internal/v2/life/media/read。管理只接受可信 Platform 服务；operator由认证服务决定，value不能授予权限。读沿 life_readers/life_access并核scope可信会话；scope=null仅访问明确公共虚构生活，绝不是自动公开。原件必须核Actor、相册scope和当前源权限。跨person私有记录404。state缺角色404；未配置能力503；空列表仅表示已存在Actor该资源确无记录。expected_version用于目标CAS（创建0），request_id与摘要同事务记账。outfit覆盖只影响本次image，不更新当前穿搭。认识片段期望版本分别核对；来源无效不采用。manage仅返回闭合id/version/state/operation_ref，read按resource投影闭合字段；总量超限返回429不截断正文。
