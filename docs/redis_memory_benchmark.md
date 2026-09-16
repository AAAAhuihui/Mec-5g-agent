# Redis 会话记忆基准：VMware Linux 使用与测评说明

本基准比较同一组会话数据和读取请求在绕过、启用 Redis 缓存时的耗时、MySQL 访问次数及返回结果。A 组不发送 Redis 请求，并不是停止 Redis 服务。运行入口是 `scripts/benchmark_memory_cache.py`。默认仅生成计划；只有显式传入 `--run` 才连接服务并写入测试数据。

本文不包含真实实跑成绩。性能结论必须以目标虚拟机生成的 JSON 和 Markdown 报告为依据，不能将计划输出或示例命令当作实验结果。

## 测试边界与隔离

每次实跑创建一个全新的独立 MySQL 数据库，名称为 `memory_bench_<12hex>`。账号必须拥有 `CREATE DATABASE` 权限。如果同名数据库已经存在，运行器拒绝继续，避免覆盖已有数据。测试结束后保留数据库，不自动删除；报告中的数据库名称可用于事后复查。是否清理以及清理哪个数据库，应在核对报告后另行决定。

Redis 测试键统一使用 `memory-bench:<runid>:` 前缀。运行器仅清理属于本次测试组的键，不执行 `FLUSHDB` 或 `FLUSHALL`。独立数据库和键前缀提供数据隔离，但 MySQL、Redis、虚拟机 CPU 与磁盘仍可能与其他任务共享资源；应记录同机负载，避免将资源争用归因为缓存实现。

运行器不调用 LLM，并设置阻止 LLM 调用的保护。它也不加载嵌入或重排模型。此次测试不修改生产记忆代码。

## 在 VMware Linux 准备文件和环境

在 Linux 虚拟机中使用项目目录。至少同步本项目的 `app/`、`scripts/benchmark_memory_cache.py` 和本文所在的 `docs/`；运行器依赖 `app/` 中的记忆仓库和基准负载模块。同步时保留虚拟机已有 `.env`，不要用 Windows 的 `.env` 覆盖它。

Windows 的 `.venv` 不能复制给 Linux 使用。在虚拟机项目根目录创建独立的测评环境：

```bash
python3 -m venv .venv-bench
source .venv-bench/bin/activate
python -m pip install 'pymysql[rsa]>=1.1.1' 'redis>=5.0' 'pydantic>=2.7' 'python-dotenv>=1.0'
```

若系统提示缺少 `venv`，先通过该 Linux 发行版的包管理器安装相应的 Python venv 支持。测评环境无需安装 `torch`、`sentence-transformers` 或下载任何模型，也无需安装整个应用的所有依赖。

为便于复现，可记录本次实际环境：

```bash
python --version
python -m pip freeze
```

## 确认服务，再建立本机端口转发

以下示例假设 MySQL 和 Redis 位于虚拟机可访问的 Kubernetes 集群。先确认服务名称、命名空间和服务端口：

```bash
sudo k3s kubectl get svc -A
```

不要直接假定服务一定叫 `mysql` 或 `redis`。下面两个命令仅适用于实际服务名称和端口与示例一致的情况；其他情况按上一步输出调整。

在虚拟机的两个独立终端中分别执行端口转发，并在测试期间保持终端运行。命名空间也要替换为实际值。

终端一：

```bash
sudo k3s kubectl -n agent-system port-forward service/mysql 13306:3306 --address 127.0.0.1
```

终端二：

```bash
sudo k3s kubectl -n agent-system port-forward service/redis 16379:6379 --address 127.0.0.1
```

这里按之前的 k3s 和 `agent-system` 部署举例。这里的 `127.0.0.1` 是运行基准脚本的 Linux 虚拟机自身。默认仅绑定回环地址即可，不需要开放公网端口，也不需要改为 `0.0.0.0`。如果端口转发建在 Windows 而脚本运行在虚拟机，两个环境的 `127.0.0.1` 并不相同；应将端口转发也放到虚拟机中。[Kubernetes 官方端口转发说明](https://kubernetes.io/docs/reference/kubectl/generated/kubectl_port-forward/)

端口转发经过的本机代理和 Kubernetes 网络会进入实际读取耗时。因此报告反映的是这条访问路径下的表现，不能直接代表生产 Pod 内访问 Service 的延迟。

## 配置连接，不把密码写入命令历史

在第三个虚拟机终端中进入项目目录、激活 `.venv-bench`。下面使用 Bash 的隐藏输入读取密码：

```bash
source .venv-bench/bin/activate
export MYSQL_HOST=127.0.0.1
export MYSQL_PORT=13306
export MYSQL_USER=root
read -rsp 'MySQL password: ' MYSQL_PASSWORD
printf '\n'
export MYSQL_PASSWORD

export REDIS_HOST=127.0.0.1
export REDIS_PORT=16379
```

`MYSQL_USER=root` 是示例账号；也可改为拥有创建测试数据库权限的专用账号。不要在命令中直接填写密码，不要把包含凭据的环境输出附在报告中。数据库名称由运行器生成，不需要指向业务数据库。

仅在实际 Redis 配置了密码时输入并导出密码：

```bash
read -rsp 'Redis password: ' REDIS_PASSWORD
printf '\n'
export REDIS_PASSWORD
```

若实际 Redis 无密码，可显式设置空值，避免项目 `.env` 中已有的 Redis 密码影响此次连接：

```bash
export REDIS_PASSWORD=''
```

这些变量只用于当前终端环境，不需要修改或覆盖服务器 `.env`。完成测试后可关闭该终端，或执行 `unset MYSQL_PASSWORD REDIS_PASSWORD`。

## 先查看计划，再小规模实跑

默认参数为 50 个会话、每个非 cold 场景 1000 次读取、3 轮、并发度 `1,5,10`，场景为 `cold,warm,mixed`，操作为 `semantic,task,events,memory`。

不传 `--run` 时只生成计划，不连接 MySQL 或 Redis：

```bash
python scripts/benchmark_memory_cache.py --plan
```

先检查小规模计划：

```bash
python scripts/benchmark_memory_cache.py --plan \
  --sessions 5 --requests 30 --rounds 1 --concurrency 1
```

再执行小规模实跑：

```bash
python scripts/benchmark_memory_cache.py --run \
  --sessions 5 --requests 30 --rounds 1 --concurrency 1 \
  --output reports/memory_benchmark_vm_smoke.json
```

默认仍包含更新一致性、TTL 和故障检查；小规模试跑并非只测速度。先确认连接、数据库隔离、返回值比较及检查结果，再增加负载。

完整参数可显式写为：

```bash
python scripts/benchmark_memory_cache.py --run \
  --sessions 50 --requests 1000 --rounds 3 \
  --concurrency 1,5,10 \
  --scenarios cold,warm,mixed \
  --operations semantic,task,events,memory
```

默认报告输出为 `reports/memory_benchmark_<runid>.json`，同时生成同名 `.md` 文件。`--output` 可指定 JSON 路径，其旁仍生成 Markdown 报告。如果 JSON 或同名 Markdown 已经存在，运行器拒绝覆盖；再次执行小规模命令时，请省略 `--output` 使用新的默认名称，或填写新的输出路径。

完整默认组合的主要读取数为：

```text
(cold 50 + warm 1000 + mixed 1000)
× 3 轮 × 3 个并发度 × 4 类操作 × 2 种缓存模式
= 147600 次读取
```

这还不包含准备数据、更新写入、直接 MySQL 对照读取及额外正确性检查。完整运行可能较重，尤其是在资源有限的虚拟机中，应先完成小规模试跑。

`--skip-checks` 可以跳过附加检查，但不是默认选项。使用它得到的性能报告不能视为已通过 TTL、故障及恢复一致性验证；正式比较应保留默认检查。

## 负载与计时口径

每个合成会话默认包含语义摘要、任务摘要、12 条消息和 20 条事件。两个摘要都携带会话标识和版本号，正文约为 KB 量级；消息和事件也包含会话标识，用来检查串会话。事件覆盖 `mysql`、`redis` 和 `pod`，重要性分值随序号变化。

| 场景 | 读取分布 | 查询与更新 |
| --- | --- | --- |
| `cold` | 每个会话首次读取一次；50 会话就是 50 次，忽略 `--requests 1000` 的读取数量 | 固定查询 `pod redis` |
| `warm` | 轮流访问前 20% 热点会话，至少保留 1 个热点会话 | 固定查询 `pod redis`，重复访问形成缓存热点 |
| `mixed` | 约 80% 读取热点会话，约 20% 读取其余会话；只有一个会话时全部访问它 | 约 70% 固定查询，其余使用带唯一后缀的查询；每 20 次读取前安排更新 |

热点池具体取前 `max(1, sessions // 5)` 个会话。混合分布使用固定种子和局部随机数生成器生成，默认 `--seed 20260908`；报告中的负载摘要可用于确认对照输入一致。小请求量下比例会因整数取整略有偏差。

混合更新发生在从零开始的请求索引 `20,40,60...` 之前，目标就是该条请求的会话。运行器同步更新两个摘要并新增一条事件。写操作在读取批次之间以 barrier 方式执行，避免与该批读取重叠；写入耗时不计入读取延迟，另记 `update_wall_seconds`。这可以检查更新后读的一致性，但不属于并发读写竞争压力测试。并发组使用相同的请求提交序列，但实际完成顺序由线程调度决定。

所有组先预热数据库。cold 只清理本测试组的 Redis 键，因此它测的是 Redis 冷缓存，不是 MySQL 冷磁盘。mixed 的固定查询在所有会话上预热；两个摘要是写后更新缓存，命中率可能接近 100%，不等于 80% 热点比例。warm 在生成对照值后再次预热，仍应核对实测命中率：如果测量过程超过 TTL，不能把结果当作全命中的理论上限。

| 操作 | 读取内容 | 解读重点 |
| --- | --- | --- |
| `semantic` | 语义摘要 | 摘要缓存命中及版本一致性 |
| `task` | 当前任务摘要 | 任务状态更新后是否读取到新版本 |
| `events` | 与查询相关的重要事件 | 查询变化、事件版本变化及缓存失效 |
| `memory` | 实际调用 `get_or_create_memory()`，包含两个摘要和最近 6 条消息等 | 仅两个摘要走缓存，其他读取及装配成本仍保留 |

完整 `memory` 操作当前根本不读取重要事件；重要事件由 `events` 操作独立测量。`memory` 仍直接读取会话、消息、操作和 artifacts，并执行产物检查；不能把剩余 MySQL 查询直接视为 Redis 未生效。本负载的操作记录和 artifacts 为空，也不能据此推断大量历史操作或文件产物下的性能。

现有 `ensure_schema` 调用仍在原路径执行，相关耗时保留在读取计时中，没有为基准临时删去。`mysql_selects` 包括对 `information_schema` 的查询；应结合 `mysql_schema_selects` 和 DDL 统计区分业务数据查询、模式检查及建表语句。看到读取期间存在 DDL 或模式查询时，应先定位来源，不能将全部查询数解释为业务记录读取。

## 正确性与故障检查如何解释

运行器比较完整读取结果，而非仅比较返回条数。对照值直接从 MySQL 读取，且对照读取不计入被测读操作耗时。事件排序涉及的时钟在对照过程中冻结，以减少时间流逝引入的分值差异。

这类 oracle 检查用于验证缓存开关两种路径及更新恢复后的结果一致性，不是对事件排名算法本身的独立质量评估。如果两条路径共享相同排序逻辑，结果一致并不能证明该算法对业务问题的相关性最佳。

默认附加检查覆盖摘要与事件更新、TTL 到期和 Redis 故障及恢复。故障由测试客户端注入约 20 ms 的超时实现，不停止真实 Redis 服务，也不修改其他使用者的连接。它检查应用如何处理缓存异常；不能把该延迟结果当作真实网络丢包、连接黑洞或实际 1 秒超时设置下的耗时。

需要重点检查“数据库写入成功，但缓存更新失败，随后 Redis 恢复”的路径。当前实现可能在恢复后读到旧摘要或旧事件。若报告发现这种情况，应如实保留失败结果，并查看对应操作、会话和版本差异；不能只展示较好的延迟数字，也不能把恢复后第一次读到旧值标为正确。

写后缓存一致性可以作为后续优化课题，例如评估持久化版本校验、可靠失效重试或恢复后的缓存淘汰。此次基准不修改生产实现，也不将这些设计设想当作已经完成的修复。

## 阅读结果表与形成结论

先检查正确性与附加检查结果，再比较同一场景、操作、并发度和轮次下的缓存关闭与启用结果。不要跨越不同操作或负载组合直接比较单个耗时值。

| 报告信息 | 用于回答的问题 | 常见误读 |
| --- | --- | --- |
| 正确性失败、更新及故障恢复结果 | 是否返回了与直接 MySQL 对照一致的完整内容？ | 返回更快就代表更好 |
| Mean、P50、P95 | 普通读取与慢读取是否同时改善？ | 只用平均值掩盖少数长延迟 |
| 各类业务命中率 | 摘要和重要事件各复用了多少读取结果？ | 用包含版本键的 Redis 全局命中率代替业务命中率 |
| 吞吐与组总耗时 | 在指定并发下完成读取的速度如何？ | 将并发加速全部归因于 Redis |
| `mysql_selects` 与模式查询、DDL 细分 | 缓存节省了哪些数据库访问？剩余成本在哪里？ | 将模式检查计为摘要查询 |
| `update_wall_seconds` | 更新及失效操作付出了多少额外时间？ | 将排除写入的读延迟解释为总业务耗时 |
| 运行环境、负载摘要、参数与数据库名称 | 两次运行是否具有可比条件并可追查？ | 将不同数据规模或访问路径混为一组 |

若启用缓存后 MySQL 访问减少但延迟没有下降，应结合端口转发、连接建立、模式检查、Redis 往返、序列化和虚拟机资源争用分析。数据集较小或纯内存环境下，缓存收益也可能不明显；这些都应由实际数据支持，不能预先承诺加速比例。

建议保留 JSON 原始结果和 Markdown 汇总，记录虚拟机 CPU、内存、Python 与依赖版本、MySQL/Redis 部署方式，以及运行时其他负载。重复运行可以观察波动，但只应比较正确性通过且测量条件一致的组。

该结果仅说明记忆读取组件在指定负载和部署路径下的表现。完整 Agent 还包含检索、重排、LLM 推理与工具调用，因此记忆读取加速不能直接等同于端到端 Agent 的同等倍数加速。

统计细节：延迟包括失败尝试；P50/P95 使用 `(n-1)×q` 位置的线性插值。业务命中率 = hits/(hits+misses)，旁路不算 miss，零分母显示 N/A。SQL 和连接数分别统计执行尝试和 `_connect` 尝试；SQL 数不是服务器内部执行次数。吞吐 = 读取尝试数/读取批次墙钟时间，包含线程派发、结果校验和客户端关闭，不包含种数、oracle 和写入。单次延迟计时包含原生产风格的客户端创建，但不包含返回后的结果校验及显式关闭。测的是应用记忆读取，不是 Redis 服务器极限性能。[Redis 基准测试注意事项](https://redis.io/docs/latest/operate/oss_and_stack/management/optimization/benchmarks/)

退出码：0 表示所执行的检查通过；1 表示运行被连接、权限或其他异常中断；2 表示测量报告已保存，但发现错误或一致性检查失败。尤其 `recovery_after_failed_cache_write` 失败时，应保留报告分析旧缓存问题，不要误以为没有生成结果。报告只记录失败类型和 mismatch，不输出密码、API Key 或完整会话内容。
