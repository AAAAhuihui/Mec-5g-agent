# 记忆读取 A/B 测评

状态：存在失败/数据不一致，先检查明细。不调用 LLM。

专用数据库（保留）：`memory_bench_0e889a845aee`

A = 真正绕过 Redis；B = 真实 Redis。以下为各轮实测，不跨场景合并分位数。

| 接口 / 场景 | 并发 | 轮次 | 组 | 读取数 | Mean ms | P50 ms | P95 ms | SQL/次 | 连接/次 | RPS | 错误/不一致 |
|---|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|---|
| semantic / cold | 1 | 1 | A | 50 | 119.253 | 118.206 | 131.542 | 15.000 | 3.000 | 8.337 | 0 / 0 |
| semantic / cold | 1 | 1 | B | 50 | 126.812 | 126.052 | 139.191 | 15.000 | 3.000 | 7.860 | 0 / 0 |
| semantic / warm | 1 | 1 | A | 50 | 116.724 | 117.642 | 126.942 | 15.000 | 3.000 | 8.519 | 0 / 0 |
| semantic / warm | 1 | 1 | B | 50 | 5.343 | 4.897 | 7.994 | 0.000 | 0.000 | 172.689 | 0 / 0 |
| semantic / mixed | 1 | 1 | A | 50 | 120.979 | 120.299 | 131.018 | 15.000 | 3.000 | 8.214 | 0 / 0 |
| semantic / mixed | 1 | 1 | B | 50 | 4.805 | 4.607 | 7.287 | 0.000 | 0.000 | 191.464 | 0 / 0 |
| task / cold | 1 | 1 | A | 50 | 123.795 | 121.115 | 134.373 | 15.000 | 3.000 | 8.028 | 0 / 0 |
| task / cold | 1 | 1 | B | 50 | 125.446 | 123.779 | 140.940 | 15.000 | 3.000 | 7.945 | 0 / 0 |
| task / warm | 1 | 1 | A | 50 | 122.606 | 122.283 | 132.530 | 15.000 | 3.000 | 8.111 | 0 / 0 |
| task / warm | 1 | 1 | B | 50 | 4.598 | 4.484 | 5.649 | 0.000 | 0.000 | 199.853 | 0 / 0 |
| task / mixed | 1 | 1 | A | 50 | 122.878 | 122.827 | 134.475 | 15.000 | 3.000 | 8.114 | 0 / 0 |
| task / mixed | 1 | 1 | B | 50 | 4.239 | 4.098 | 5.516 | 0.000 | 0.000 | 211.282 | 0 / 0 |
| events / cold | 1 | 1 | A | 50 | 122.859 | 123.331 | 134.982 | 15.000 | 3.000 | 8.116 | 0 / 0 |
| events / cold | 1 | 1 | B | 50 | 131.665 | 131.275 | 142.155 | 15.000 | 3.000 | 7.563 | 0 / 0 |
| events / warm | 1 | 1 | A | 50 | 127.316 | 127.193 | 139.516 | 15.000 | 3.000 | 7.832 | 0 / 0 |
| events / warm | 1 | 1 | B | 50 | 5.968 | 5.995 | 6.862 | 0.000 | 0.000 | 152.837 | 0 / 0 |
| events / mixed | 1 | 1 | A | 50 | 131.301 | 129.982 | 142.091 | 15.000 | 3.000 | 7.592 | 0 / 0 |
| events / mixed | 1 | 1 | B | 50 | 48.652 | 6.751 | 136.694 | 5.100 | 1.020 | 20.319 | 0 / 0 |
| memory / cold | 1 | 1 | A | 50 | 975.318 | 975.412 | 1025.767 | 120.000 | 24.000 | 1.025 | 0 / 0 |
| memory / cold | 1 | 1 | B | 50 | 974.002 | 972.991 | 1002.864 | 120.000 | 24.000 | 1.026 | 0 / 0 |
| memory / warm | 1 | 1 | A | 50 | 979.690 | 981.439 | 1010.020 | 120.000 | 24.000 | 1.020 | 0 / 0 |
| memory / warm | 1 | 1 | B | 50 | 737.220 | 736.995 | 777.813 | 90.000 | 18.000 | 1.355 | 0 / 0 |
| memory / mixed | 1 | 1 | A | 50 | 988.223 | 993.758 | 1035.073 | 120.000 | 24.000 | 1.012 | 0 / 0 |
| memory / mixed | 1 | 1 | B | 50 | 751.990 | 751.282 | 790.582 | 90.000 | 18.000 | 1.329 | 0 / 0 |
| semantic / cold | 5 | 1 | A | 50 | 213.539 | 207.998 | 273.674 | 15.000 | 3.000 | 22.622 | 0 / 0 |
| semantic / cold | 5 | 1 | B | 50 | 232.212 | 234.059 | 269.216 | 15.000 | 3.000 | 20.743 | 0 / 0 |
| semantic / warm | 5 | 1 | A | 50 | 209.312 | 210.988 | 246.981 | 15.000 | 3.000 | 23.476 | 0 / 0 |
| semantic / warm | 5 | 1 | B | 50 | 25.539 | 26.150 | 37.604 | 0.000 | 0.000 | 180.773 | 0 / 0 |
| semantic / mixed | 5 | 1 | A | 50 | 200.955 | 198.885 | 243.031 | 15.000 | 3.000 | 23.577 | 0 / 0 |
| semantic / mixed | 5 | 1 | B | 50 | 23.590 | 22.077 | 34.282 | 0.000 | 0.000 | 184.982 | 0 / 0 |
| task / cold | 5 | 1 | A | 50 | 248.996 | 240.259 | 325.571 | 15.000 | 3.000 | 19.461 | 0 / 0 |
| task / cold | 5 | 1 | B | 50 | 228.883 | 221.228 | 273.731 | 15.000 | 3.000 | 20.666 | 0 / 0 |
| task / warm | 5 | 1 | A | 50 | 228.808 | 215.097 | 330.404 | 15.000 | 3.000 | 20.986 | 0 / 0 |
| task / warm | 5 | 1 | B | 50 | 24.949 | 24.021 | 36.277 | 0.000 | 0.000 | 183.181 | 0 / 0 |
| task / mixed | 5 | 1 | A | 50 | 203.822 | 201.567 | 244.887 | 15.000 | 3.000 | 23.368 | 0 / 0 |
| task / mixed | 5 | 1 | B | 50 | 24.470 | 23.288 | 37.062 | 0.000 | 0.000 | 181.481 | 0 / 0 |
| events / cold | 5 | 1 | A | 50 | 211.261 | 214.087 | 254.529 | 15.000 | 3.000 | 22.977 | 0 / 0 |
| events / cold | 5 | 1 | B | 50 | 240.798 | 241.951 | 301.361 | 15.000 | 3.000 | 20.193 | 0 / 0 |
| events / warm | 5 | 1 | A | 50 | 217.706 | 217.999 | 260.007 | 15.000 | 3.000 | 22.598 | 0 / 0 |
| events / warm | 5 | 1 | B | 50 | 28.703 | 28.432 | 47.287 | 0.000 | 0.000 | 161.676 | 0 / 0 |
| events / mixed | 5 | 1 | A | 50 | 204.359 | 205.104 | 257.708 | 15.000 | 3.000 | 22.929 | 0 / 0 |
| events / mixed | 5 | 1 | B | 50 | 94.620 | 27.341 | 259.879 | 5.400 | 1.080 | 46.158 | 0 / 0 |
| memory / cold | 5 | 1 | A | 50 | 1702.329 | 1700.878 | 1824.198 | 120.000 | 24.000 | 2.893 | 0 / 0 |
| memory / cold | 5 | 1 | B | 50 | 1677.987 | 1665.295 | 1819.410 | 120.000 | 24.000 | 2.937 | 0 / 0 |
| memory / warm | 5 | 1 | A | 50 | 1716.694 | 1706.061 | 2001.391 | 120.000 | 24.000 | 2.892 | 0 / 0 |
| memory / warm | 5 | 1 | B | 50 | 1297.323 | 1315.489 | 1425.448 | 90.000 | 18.000 | 3.753 | 0 / 0 |
| memory / mixed | 5 | 1 | A | 50 | 1718.026 | 1718.543 | 1864.819 | 120.000 | 24.000 | 2.834 | 0 / 0 |
| memory / mixed | 5 | 1 | B | 50 | 1315.607 | 1293.874 | 1591.862 | 90.000 | 18.000 | 3.714 | 0 / 0 |

## 业务命中率与相对收益

版本键不算业务命中；旁路的命中率为 N/A。加速比 A/B > 1 表示 B 更快。

| 接口 / 场景 | 并发 / 轮次 | B semantic | B task | B events | Mean 加速比 | P95 加速比 | SQL 减少比例 |
|---|---|---:|---:|---:|---:|---:|---:|
| semantic / cold | 1 / 1 | 0.000 | N/A | N/A | 0.940 | 0.945 | 0.000 |
| semantic / warm | 1 / 1 | 1.000 | N/A | N/A | 21.845 | 15.879 | 1.000 |
| semantic / mixed | 1 / 1 | 1.000 | N/A | N/A | 25.177 | 17.979 | 1.000 |
| task / cold | 1 / 1 | N/A | 0.000 | N/A | 0.987 | 0.953 | 0.000 |
| task / warm | 1 / 1 | N/A | 1.000 | N/A | 26.668 | 23.460 | 1.000 |
| task / mixed | 1 / 1 | N/A | 1.000 | N/A | 28.988 | 24.379 | 1.000 |
| events / cold | 1 / 1 | N/A | N/A | 0.000 | 0.933 | 0.950 | 0.000 |
| events / warm | 1 / 1 | N/A | N/A | 1.000 | 21.335 | 20.333 | 1.000 |
| events / mixed | 1 / 1 | N/A | N/A | 0.660 | 2.699 | 1.039 | 0.660 |
| memory / cold | 1 / 1 | 0.000 | 0.000 | N/A | 1.001 | 1.023 | 0.000 |
| memory / warm | 1 / 1 | 1.000 | 1.000 | N/A | 1.329 | 1.299 | 0.250 |
| memory / mixed | 1 / 1 | 1.000 | 1.000 | N/A | 1.314 | 1.309 | 0.250 |
| semantic / cold | 5 / 1 | 0.000 | N/A | N/A | 0.920 | 1.017 | 0.000 |
| semantic / warm | 5 / 1 | 1.000 | N/A | N/A | 8.196 | 6.568 | 1.000 |
| semantic / mixed | 5 / 1 | 1.000 | N/A | N/A | 8.519 | 7.089 | 1.000 |
| task / cold | 5 / 1 | N/A | 0.000 | N/A | 1.088 | 1.189 | 0.000 |
| task / warm | 5 / 1 | N/A | 1.000 | N/A | 9.171 | 9.108 | 1.000 |
| task / mixed | 5 / 1 | N/A | 1.000 | N/A | 8.330 | 6.607 | 1.000 |
| events / cold | 5 / 1 | N/A | N/A | 0.000 | 0.877 | 0.845 | 0.000 |
| events / warm | 5 / 1 | N/A | N/A | 1.000 | 7.585 | 5.499 | 1.000 |
| events / mixed | 5 / 1 | N/A | N/A | 0.640 | 2.160 | 0.992 | 0.640 |
| memory / cold | 5 / 1 | 0.000 | 0.000 | N/A | 1.015 | 1.003 | 0.000 |
| memory / warm | 5 / 1 | 1.000 | 1.000 | N/A | 1.323 | 1.404 | 0.250 |
| memory / mixed | 5 / 1 | 1.000 | 1.000 | N/A | 1.306 | 1.171 | 0.250 |

## 故障与一致性检查

- PASS `initial_read` / `semantic`：132.035 ms，Redis 异常 0，错误 None，结果不一致 False。
- PASS `initial_read` / `task`：141.728 ms，Redis 异常 0，错误 None，结果不一致 False。
- PASS `initial_read` / `events`：139.150 ms，Redis 异常 0，错误 None，结果不一致 False。
- PASS `initial_read` / `memory`：744.881 ms，Redis 异常 0，错误 None，结果不一致 False。
- PASS `update_then_read` / `semantic`：4.479 ms，Redis 异常 0，错误 None，结果不一致 False。
- PASS `update_then_read` / `task`：4.039 ms，Redis 异常 0，错误 None，结果不一致 False。
- PASS `update_then_read` / `events`：124.467 ms，Redis 异常 0，错误 None，结果不一致 False。
- PASS `update_then_read` / `memory`：733.919 ms，Redis 异常 0，错误 None，结果不一致 False。
- PASS `explicit_invalidation` / `semantic`：133.064 ms，Redis 异常 0，错误 None，结果不一致 False。
- PASS `explicit_invalidation` / `task`：133.997 ms，Redis 异常 0，错误 None，结果不一致 False。
- PASS `explicit_invalidation` / `events`：136.350 ms，Redis 异常 0，错误 None，结果不一致 False。
- PASS `explicit_invalidation` / `memory`：713.471 ms，Redis 异常 0，错误 None，结果不一致 False。
- PASS `before_ttl_expiry` / `semantic`：131.101 ms，Redis 异常 0，错误 None，结果不一致 False。
- PASS `confirmed_hit_before_ttl` / `semantic`：3.947 ms，Redis 异常 0，错误 None，结果不一致 False。
- PASS `before_ttl_expiry` / `task`：128.085 ms，Redis 异常 0，错误 None，结果不一致 False。
- PASS `confirmed_hit_before_ttl` / `task`：5.541 ms，Redis 异常 0，错误 None，结果不一致 False。
- PASS `before_ttl_expiry` / `events`：130.956 ms，Redis 异常 0，错误 None，结果不一致 False。
- PASS `confirmed_hit_before_ttl` / `events`：5.244 ms，Redis 异常 0，错误 None，结果不一致 False。
- PASS `after_ttl_expiry` / `semantic`：124.063 ms，Redis 异常 0，错误 None，结果不一致 False。
- PASS `after_ttl_expiry` / `task`：127.906 ms，Redis 异常 0，错误 None，结果不一致 False。
- PASS `after_ttl_expiry` / `events`：120.896 ms，Redis 异常 0，错误 None，结果不一致 False。
- PASS `after_ttl_expiry` / `memory`：740.939 ms，Redis 异常 0，错误 None，结果不一致 False。
- PASS `injected_read_timeout` / `semantic`：160.892 ms，Redis 异常 2，错误 None，结果不一致 False。
- PASS `injected_read_timeout` / `task`：151.081 ms，Redis 异常 2，错误 None，结果不一致 False。
- PASS `injected_read_timeout` / `events`：184.976 ms，Redis 异常 3，错误 None，结果不一致 False。
- PASS `injected_read_timeout` / `memory`：1031.060 ms，Redis 异常 4，错误 None，结果不一致 False。
- PASS `recovered_read` / `semantic`：4.884 ms，Redis 异常 0，错误 None，结果不一致 False。
- PASS `confirmed_hit_after_recovery` / `semantic`：4.304 ms，Redis 异常 0，错误 None，结果不一致 False。
- PASS `recovered_read` / `task`：5.397 ms，Redis 异常 0，错误 None，结果不一致 False。
- PASS `confirmed_hit_after_recovery` / `task`：6.529 ms，Redis 异常 0，错误 None，结果不一致 False。
- PASS `recovered_read` / `events`：5.713 ms，Redis 异常 0，错误 None，结果不一致 False。
- PASS `confirmed_hit_after_recovery` / `events`：4.642 ms，Redis 异常 0，错误 None，结果不一致 False。
- PASS `recovered_read` / `memory`：689.593 ms，Redis 异常 0，错误 None，结果不一致 False。
- PASS `confirmed_hit_after_recovery` / `memory`：696.559 ms，Redis 异常 0，错误 None，结果不一致 False。
- FAIL `recovery_after_failed_cache_write` / `semantic`：5.151 ms，Redis 异常 0，错误 None，结果不一致 True。
- FAIL `recovery_after_failed_cache_write` / `task`：4.146 ms，Redis 异常 0，错误 None，结果不一致 True。
- FAIL `recovery_after_failed_cache_write` / `events`：5.841 ms，Redis 异常 0，错误 None，结果不一致 True。
- FAIL `recovery_after_failed_cache_write` / `memory`：723.446 ms，Redis 异常 0，错误 None，结果不一致 True。

## 解释限制

- 冷缓存每个会话只读一次；MySQL 已预热，不是磁盘冷启动。
- 混合负载为合成数据：80% 热点会话、70% 固定问题；每 20 次读后更新一次目标会话。
- 写入位于并发读取批次之间，读指标不含写入耗时；不是读写竞争测试。
- 原实现每次回源的表结构检查、连接建立均计入；mysql_schema_selects 和 mysql_ddl 单独记录。
- 完整 memory 读取不包含重要事件，消息/操作/artifact 仍读 MySQL；操作和 artifact 测试表为空。
- 故障为测试客户端注入超时，不代表真实网络故障等待时长。
- recovery_after_failed_cache_write 失败表示缓存维护失败后可能返回旧数据，不能只引用速度而忽略它。
- 原始逐请求数据、版本、TTL、数据库/Redis 地址和 workload digest 见配套 JSON。
- 本报告不衡量回答质量、LLM Token 或完整 Agent 响应时间。
