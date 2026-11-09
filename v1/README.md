# v1 第一阶段：多节点拓扑与 HTTP 业务实验基础

本目录是与现有 `labs/ospf-baseline/` **并列的新版草案**。本阶段仅交付可校验的拓扑意图、Containerlab/FRR/HTTP 配置与 mock 离线样本；没有部署六节点实验，没有现场路由或 HTTP 验收，也没有将旧 Agent 接到新版拓扑。v0.5 历史证据、冻结清单与 v0.7 工作台保持原样。

## 离线入口

从仓库根目录运行（仅需 Python 3.12 标准库）：

```bash
python3 -m v1.topology
python3 -B v1/run_offline_tests.py
```

测试入口将 `v1/` 单独复制到临时目录，不读取 `.env`、旧实验运行记录或 Docker 状态。`topology.py` 校验节点/角色、接口及唯一地址、链路端点/子网、业务目标，并从**验证后的**清单生成 `ToolScope`。`ToolScope` 为以后接入只读工具提供精确的节点、接口、ping 地址与 HTTP 目标授权；目前不执行工具或命令。OSPF/路由查询仅授权给 router，client/server 不被当作 FRR 路由器。

`candidate_paths()` 枚举 router 间的简单结构路径，按**配置意图中的** OSPF cost 排序。它不解析实时路由表，也不能证明邻居 FULL、转发正常或 HTTP 成功。

## 计划拓扑与地址

```text
client -- r1 -- r2 -- r4 -- server
            \        /
             r3 -----
```

实际链路清单以 [`lab/topology.json`](lab/topology.json) 为准；图仅用于说明结构。所有数据链路为 `/30`，router loopback 为 `/32`，OSPF area 为 `0.0.0.0`。

| 链路 | 端点 A | 端点 B | 双端预设 OSPF cost |
| --- | --- | --- | ---: |
| client–r1 | client eth1 `10.101.0.2/30` | r1 eth1 `10.101.0.1/30` | 接入网段，被动通告 |
| r1–r2 | r1 eth2 `10.12.0.1/30` | r2 eth1 `10.12.0.2/30` | 10 |
| r2–r4 | r2 eth2 `10.24.0.1/30` | r4 eth1 `10.24.0.2/30` | 10 |
| r1–r3 | r1 eth3 `10.13.0.1/30` | r3 eth1 `10.13.0.2/30` | 50 |
| r3–r4 | r3 eth2 `10.34.0.1/30` | r4 eth2 `10.34.0.2/30` | 50 |
| r4–server | r4 eth3 `10.104.0.1/30` | server eth1 `10.104.0.2/30` | 接入网段，被动通告 |

| 路由器 | Router ID / Loopback |
| --- | --- |
| r1 | `10.255.1.1/32` |
| r2 | `10.255.1.2/32` |
| r3 | `10.255.1.3/32` |
| r4 | `10.255.1.4/32` |

计划主路 `r1→r2→r4` 的路由器链路 cost 合计 20；备路 `r1→r3→r4` 合计 100。这是静态配置意图，部署后必须查询实际 OSPF 邻居、LSDB 与路由表，不能仅凭图上的两条线宣称主备转发已生效。r1/r4 的客户/服务端接口为 OSPF passive，仍宣告各自接入 `/30`；`client` 到服务网段有经 r1 的静态路由，`server` 到客户网段有经 r4 的回程静态路由。

`server` 使用官方 `python:3.12.15-alpine3.24` 镜像运行标准库 `http.server`；只读挂载 [`lab/services/health`](lab/services/health)，计划端点 `http://10.104.0.2:8080/health` 返回 `200` 和 `ok`。这只是待部署配置，**尚无实际 HTTP 响应记录**。

独立实验名 `netsecops-v1-http`，计划容器名为 `clab-netsecops-v1-http-{client,r1,r2,r3,r4,server}`；独立管理网名 `netsecops-v1-mgmt`，计划子网 `172.31.87.0/24`，六节点管理地址为 `.11`–`.16`。这些名称与旧双节点实验分开，但并不保证现场没有既有资源或地址冲突。

## 配置文件

- [`lab/topology.json`](lab/topology.json)：机器可校验的角色、接口、地址、链路、业务目标及成本意图。
- [`lab/topology.clab.yml`](lab/topology.clab.yml)：Containerlab 声明。FRR 镜像沿用旧版 `containerlab-10.7.1`；主机镜像为明确版本。四个 FRR 节点设置 `enforce-startup-config: true`，避免已有 lab 目录中的保存配置覆盖本目录源配置。**部署前需检查是否已有同名 lab 目录，不做清理或覆盖。**
- `lab/configs/r{1,2,3,4}/frr.conf`：OSPFv2 启动配置；接口 IP、Router ID、区域、cost、passive 声明均由离线测试与 JSON 清单对照。
- [`lab/services/health`](lab/services/health)：HTTP 健康检查正文。
- [`tests/fixtures/`](tests/fixtures/README.md)：四组明确标为 `synthetic/mock` 的未来诊断样本；标签放在独立 `expected.json`，不能作为 Agent 观测输入，也不能充当现场证据。

## 下一阶段部署前检查（本阶段未执行）

1. 在 Ubuntu/WSL2 核对 `clab`、Docker Engine 与 `docker info`，确认所需 FRR 与 Python 镜像可取得；六节点的 CPU/内存足够。离线 Python 测试**没有**验证镜像、FRR 启动、Alpine `ip` 小程序或容器权限。
2. 检查现有 Docker 网络、容器、WSL 路由与主机接口，确认 `netsecops-v1-mgmt`、`172.31.87.0/24`、全部数据网段、`clab-netsecops-v1-http-*` 及同名 lab 目录均不冲突。若冲突，应先修订本 v1 配置并复测，不能自动删除其他资源。
3. 确认六节点能按 [`topology.clab.yml`](lab/topology.clab.yml) 创建，FRR 10.7.1 接受配置，`client/server` 的 `exec` 地址与静态路由成功，服务文件挂载正确。配置使用 Containerlab 的 `frr` kind、`startup-config`、`daemons`、Linux `exec` 与 `binds` 语法；本阶段只做静态检查，**未执行 Containerlab 解析或部署**。
4. 部署后先逐项验证四个路由器的邻居 FULL、服务 `/30` 的实际 OSPF 路由与下一跳、client/server 双向 ICMP、client 对 `/health` 的 HTTP 200/`ok`。观察到主路的下一跳后，才讨论切换到备路的故障实验。此阶段没有任何故障注入计划被执行。

## 文档依据与边界

配置语法参考 [Containerlab FRRouting kind](https://containerlab.dev/manual/kinds/frr/)、[Containerlab 节点 `exec`、`binds` 与启动配置](https://containerlab.dev/manual/nodes/)、[Containerlab 管理网](https://containerlab.dev/manual/network/)、[FRRouting OSPFv2](https://docs.frrouting.org/en/latest/ospfd.html) 与 [Docker 官方 Python 镜像](https://hub.docker.com/_/python)。镜像标签明确写在拓扑中，但镜像未拉取验证。

本版本不提供多节点 Agent、实时采集器、HTTP 诊断保护层或网页展示。旧版双节点 Agent 与 v0.7 页面继续按原路径运行，不会自动理解这个 v1 拓扑。
