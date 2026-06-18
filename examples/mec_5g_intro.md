# MEC/5G 组件关系简介

MEC/5G 分流场景通常涉及 AF、NEF、SMF、UPF、MEP、MEPM 和 MEC APP。

NEF 是 5GC 对外暴露网络能力的入口，AF 可以通过 NEF 或 NEF_NBI 提交 Traffic Influence 请求。NEF 接收请求后，会把业务影响信息传递给核心网内部策略或会话相关组件。

SMF 负责会话管理和用户面路径控制。SMF 根据策略结果选择或更新 UPF，并向 UPF 下发用户面规则。

UPF 执行用户面转发。PDR 用于匹配流量，FAR 用于描述转发行为。N3 通常连接 RAN 与 UPF，N6 通常连接 UPF 与数据网络或 MEC 本地网络。

MEP 提供 MEC 平台能力，MEPM 负责 MEC 平台管理。MEC APP 是部署在边缘侧的业务应用，流量最终需要被正确分流并转发到 MEC APP 对应服务。

