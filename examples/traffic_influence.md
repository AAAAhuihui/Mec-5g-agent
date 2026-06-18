# Traffic Influence 大致流程

AF 可以通过 NEF 暴露的北向接口 NEF_NBI 提交 Traffic Influence 请求。请求通常包含业务标识、目标应用、DNN/S-NSSAI、DNAI、流量匹配信息或期望的路由影响。

NEF 收到 Traffic Influence 请求后，会完成鉴权、参数校验和能力适配。如果 NEF 返回成功，只能说明北向请求被 NEF 接收或初步处理成功，不等于 UPF 已经完成用户面分流规则安装。

后续流程通常还需要 PCF/SMF 参与策略决策和会话规则更新。SMF 需要把对应的 PDR/FAR 安装到 UPF，UPF 才能基于 N3/N6 路径对流量进行本地分流。

如果 Traffic Influence 已经下发但流量没有进入 MEC APP，应继续确认 SMF 是否触发会话更新、UPF 是否安装匹配的 PDR/FAR、N6 侧是否能到达 MEC APP，以及 MEP Traffic Rule 是否与应用服务匹配。

