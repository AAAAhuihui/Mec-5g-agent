# MEC 分流失败排查路径

当 AF 通过 NEF_NBI 下发 Traffic Influence 后，流量仍然没有到 MEC APP，可以按以下顺序排查：

1. 查 NEF_NBI 日志，确认 AF 请求是否到达，字段是否完整，租户、应用标识、DNN、DNAI 等参数是否正确。
2. 查 NEF 响应，确认成功响应只代表请求被接收或处理，不代表 UPF 已经安装分流规则。
3. 查 SMF 策略处理，确认 SMF 是否收到策略影响，是否对相关 PDU Session 触发规则更新。
4. 查 UPF PDR/FAR，确认是否存在匹配业务流的 PDR，以及 FAR 是否把流量转发到正确的 N6 或本地 MEC 网络。
5. 查 N3/N6 抓包，确认上行流量是否到达 UPF，N6 侧是否发往 MEC APP 所在地址和端口。
6. 查 MEP Traffic Rule，确认平台侧流量规则是否匹配 MEC APP Service。
7. 查 MEC APP Service，确认应用服务正常监听、健康检查通过，并且网络策略允许访问。

常见原因包括：NEF_NBI 请求字段不完整、SMF 未触发策略更新、UPF 未安装 PDR/FAR、N6 路由不可达、MEP Traffic Rule 不匹配、MEC APP Service 未就绪。

