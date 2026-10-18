# NetSecOps Agent

第一阶段建立双路由器 OSPF 实验基线；第二阶段加入可控的 OSPF 区域不一致故障实验与数据采集；第三阶段加入重启恢复验收和离线规则诊断。当前不包含大模型、Agent 框架或生产网络接入。

## 工作区与环境基线

- Windows 工作区：`C:\Users\Administrator\Documents\Codex\2026-10-12\windows-agent-1-windows-wsl-linux`
- 项目目录：`C:\Users\Administrator\Documents\Codex\2026-10-12\windows-agent-1-windows-wsl-linux\netsecops-agent`
- 当前 Codex 命令环境：Windows PowerShell 7.6.5（原生 Windows 沙盒）
- Git 状态：当前工作区不是 Git 仓库；本阶段未执行 `git init`
- Windows Git：2.53.0.windows.3
- Windows Python：3.12.10
- WSL：Ubuntu 24.04 LTS，WSL2
- WSL Git：2.43.0
- WSL Python：3.12.3
- Docker Engine：29.9.0（仅安装在 WSL 内）
- Containerlab：0.81.0（仅安装在 WSL 内）
- FRRouting 镜像：`quay.io/frrouting/frr:containerlab-10.7.1`

Windows 侧没有安装 Docker Desktop。实验文件保留在当前 Windows 工作区，通过 WSL 的 `/mnt/c/...` 路径使用。

## 实验目标

```text
r1 eth1 (10.0.12.1/30) -------- (10.0.12.2/30) eth1 r2
   lo 10.255.0.1/32              lo 10.255.0.2/32
```

- 两台 FRRouting 路由器仅启用 OSPFv2。
- 两端链路属于 OSPF area `0.0.0.0`，网络类型为 point-to-point。
- Loopback 接口被动加入 OSPF，用于验证动态路由学习和端到端可达性。

## 文件

- `labs/ospf-baseline/topology.clab.yml`：Containerlab 拓扑。
- `labs/ospf-baseline/configs/r1/frr.conf`：r1 启动配置。
- `labs/ospf-baseline/configs/r2/frr.conf`：r2 启动配置。
- `labs/ospf-baseline/deploy-and-verify.sh`：部署并验证实验，不执行销毁。
- `labs/ospf-baseline/collect_evidence.py`：固定命令白名单的数据采集器。
- `labs/ospf-baseline/run_area_mismatch.py`：单侧 OSPF 区域故障实验与自动恢复入口。
- `labs/ospf-baseline/verify_restart.py`：仅重启本项目两台路由器并检查持久恢复。
- `labs/ospf-baseline/diagnose_offline.py`：仅读取观测 JSON 的离线规则诊断入口。
- `labs/ospf-baseline/run_diagnosis_acceptance.py`：三组证据与保守判断输入的验收。
- `labs/ospf-baseline/tests/test_phase2.py`：配置解析、命令限制和恢复流程检查。
- `labs/ospf-baseline/tests/test_diagnose_offline.py`：离线诊断回归与缺证据测试。
- `labs/ospf-baseline/verification-results/`：运行后保存原始验证输出。
- `labs/ospf-baseline/experiments/`：第二阶段每次运行的三组证据、实验记录和对比报告。
- `labs/ospf-baseline/restart-verification/`：第三阶段路由器重启检查原始证据。
- `labs/ospf-baseline/diagnostic-acceptance/`：第三阶段离线诊断验收结果。

## 部署与验证

从 Ubuntu/WSL2 执行：

```bash
bash /mnt/c/Users/Administrator/Documents/Codex/2026-10-12/windows-agent-1-windows-wsl-linux/netsecops-agent/labs/ospf-baseline/deploy-and-verify.sh
```

脚本会：

1. 检查 Git、Python、Docker、Containerlab，不安装或升级任何软件。
2. 如果发现同名但不完整或未运行的容器，立即停止，不删除任何资源。
3. 部署两台 FRR 路由器，或复用已正常运行的同名实验。
4. 等待两端 OSPF 邻居达到 `Full`。
5. 检查两端 OSPF 路由，并互相 ping 对端 Loopback 地址。
6. 将完整输出写入 `verification-results/`。

脚本不会销毁实验。需要查看状态时可手动执行：

```bash
sudo containerlab inspect -t /mnt/c/Users/Administrator/Documents/Codex/2026-10-12/windows-agent-1-windows-wsl-linux/netsecops-agent/labs/ospf-baseline/topology.clab.yml
```

只有在明确决定销毁时，才手动执行以下命令（本阶段不会自动执行）：

```bash
sudo containerlab destroy -t /mnt/c/Users/Administrator/Documents/Codex/2026-10-12/windows-agent-1-windows-wsl-linux/netsecops-agent/labs/ospf-baseline/topology.clab.yml
```

## 实际验证结果

最终状态：**PASS**（UTC `2026-10-13T01:47:31Z`）。

- 运行环境：Ubuntu 24.04 LTS / WSL2，内核 `6.18.40.1-microsoft-standard-WSL2`。
- 工具版本：Git `2.43.0`、Python `3.12.3`、Docker Engine 客户端/服务端 `29.9.0`、Containerlab `0.81.0`。
- 容器：`clab-netsecops-ospf-r1`、`clab-netsecops-ospf-r2` 均为 `running`，镜像均为 `quay.io/frrouting/frr:containerlab-10.7.1`。
- OSPF 邻居：r1 到 Router ID `10.255.0.2` 为 `Full/-`；r2 到 Router ID `10.255.0.1` 为 `Full/-`。
- OSPF 路由：r1 学到 `O>* 10.255.0.2/32 via 10.0.12.2`；r2 学到 `O>* 10.255.0.1/32 via 10.0.12.1`。
- r1 到 `10.255.0.2`：发送 3、接收 3，丢包率 `0%`，平均时延 `0.113 ms`。
- r2 到 `10.255.0.1`：发送 3、接收 3，丢包率 `0%`，平均时延 `0.120 ms`。
- 完整原始记录：`labs/ospf-baseline/verification-results/verification-20261013T014731Z.txt`。
- 实验仍保持部署和运行；没有自动执行销毁。

### 修正记录

第一次运行（UTC `2026-10-13T01:39:07Z`）的实际结果：

- `clab-netsecops-ospf-r1` 和 `clab-netsecops-ospf-r2` 均为 `running`，镜像均为固定版本 `quay.io/frrouting/frr:containerlab-10.7.1`。
- r1 看到 r2（Router ID `10.255.0.2`）为 `Full/-`；r2 看到 r1（Router ID `10.255.0.1`）为 `Full/-`。
- 两端 `show ip route ospf` 没有出现远端 Loopback 路由，r1 到 `10.255.0.2` 的 ping 返回 `Network unreachable`。
- Containerlab 在 Windows 挂载目录上报告 `unable to adjust Labdir file ACLs: operation not supported`；这是 `/mnt/c` 文件系统 ACL 语义差异导致的非致命提示，未影响容器、链路或最终验证。
- 实验保留运行，没有执行销毁。原始记录：`labs/ospf-baseline/verification-results/verification-20261013T013907Z.txt`。

据此在 OSPF 进程下补充了明确的 `network` 通告，并让验证脚本在复用现有容器时将同样的幂等配置应用到运行态。第三次运行确认远端 `/32` 路由和双向 ping 均恢复，原始输出见上文最终验证记录。

第二次运行（UTC `2026-10-13T01:45:13Z`）在配置修正阶段停止。FRR 返回 `Please remove all ip ospf area x.x.x.x commands first.`，说明不能把接口级 `ip ospf area` 与进程级 `network` 配置混用。此次运行没有销毁或重建容器。配置随后统一为进程级 `network` 方式；验证脚本会先从两台实验路由器移除旧的接口级 area 配置，再重新等待邻居与路由收敛。

## 第二阶段：单侧互联区域不一致

### 运行入口

在已有 Ubuntu/WSL2 中运行以下命令。脚本只对现有两台运行中的实验容器操作；如果容器不存在、镜像不符或正常基线未通过，会在注入前停止。

```bash
python3 /mnt/c/Users/Administrator/Documents/Codex/2026-10-12/windows-agent-1-windows-wsl-linux/netsecops-agent/labs/ospf-baseline/run_area_mismatch.py
```

脚本在开始时调用 `sudo -v`，可能要求输入 Ubuntu 用户密码。一次完整运行会先采集正常证据，从拓扑和两端的实际运行配置识别互联接口、地址、OSPF 区域、配置模式与远端 Loopback；随后仅把第一台路由器的互联区域临时改到另一不同区域。它兼容已发现的进程级 `network … area …` 和接口级 `ip ospf area …` 两种模式，不混用两种语法。故障与恢复轮询各有 90 秒上限。脚本在 `finally` 中按原模式恢复区域，再核对运行配置、启动配置文件哈希、OSPF 邻居、路由和双向 ping。它不执行 `write memory`、`containerlab save`、重建或销毁。

也可以单独采集一份只读快照，`--output` 必须是尚不存在的目录：

```bash
python3 /mnt/c/Users/Administrator/Documents/Codex/2026-10-12/windows-agent-1-windows-wsl-linux/netsecops-agent/labs/ospf-baseline/collect_evidence.py --output /mnt/c/Users/Administrator/Documents/Codex/2026-10-12/windows-agent-1-windows-wsl-linux/netsecops-agent/labs/ospf-baseline/experiments/manual-check-001
```

采集器仅执行固定的只读命令白名单：`show running-config`、接口地址/链路状态、互联接口的 FRR/OSPF 状态、OSPF 邻居、路由表，以及互联地址和远端 Loopback ping。命令使用参数数组调用，不通过 shell 拼接。每条命令在 JSON 中记录节点、实际参数、采集时间、退出码、标准输出、错误输出和执行错误；同目录另存原始 `.stdout.txt`、`.stderr.txt`。

### 输出结构与验收

每次运行在 `labs/ospf-baseline/experiments/area-mismatch-<UTC时间>-<标识>/` 创建独立目录：

```text
comparison.md                         前后对比与实际验收结果
experiment-record/experiment-record.json
experiment-record/original-*          注入前的运行与启动配置快照
observations/normal/                 正常基线数据
observations/fault/snapshot/          故障时完整数据
observations/fault/poll.json          有上限的故障轮询原始数据
observations/recovered/snapshot/      恢复后完整数据
observations/recovered/poll.json      有上限的恢复轮询原始数据
```

故障名称、注入命令、原始与错误区域、预期根因记录在独立的 `experiment-record/`；面向人的 `comparison.md` 也位于观测目录之外。未来给诊断 Agent 的输入只选 `observations/`。完整通过需要三阶段各自符合预期，且原始运行配置与两份启动配置文件均恢复/保持不变。任何恢复或核对失败都会标记 `FAIL`，保留现场证据与手动恢复步骤。

### 实际运行结果

本阶段已在现有 Ubuntu/WSL2 实验中完整运行一次，结果为 **PASS**。实验目录：`labs/ospf-baseline/experiments/area-mismatch-20261013T072913Z-a2e17eda/`；运行时间为 UTC `2026-10-13T07:29:13Z` 至 `07:30:29Z`。完整前后对比见该目录的 `comparison.md`。

| 阶段 | 预期 | 实际观察 |
|---|---|---|
| 正常 | 两端同区域、邻居 FULL、远端 /32 路由与两类 ping 均正常 | r1/r2 的互联接口均为 `eth1`，区域均为 `0.0.0.0`；两端全部指标正常 |
| 故障 | 单侧区域不一致，OSPF 邻居与远端路由消失；互联地址仍通、远端 Loopback 不通 | r1 互联子网区域为 `0.0.0.1`，r2 仍为 `0.0.0.0`；两端均无 FULL 邻居和远端 `/32` 路由，互联 ping 成功，Loopback ping 失败 |
| 恢复 | 原区域恢复、邻居和路由重新建立、双向可达 | r1/r2 区域均回到 `0.0.0.0`；两端邻居 FULL、远端 `/32` 路由和两类 ping 均恢复 |

三阶段预期与实际没有偏差。故障时和恢复后的原始输出均保留在对应 `observations/` 子目录。实验记录确认修改前后的完整运行配置一致（忽略显示顺序），项目源配置和 Containerlab 已生成的启动配置文件哈希也分别保持不变；故障配置没有写入启动文件。最终网络在恢复快照时正常，两个容器没有重建或销毁。

### 恢复方法与限制

脚本会在异常或 Ctrl+C 后尝试恢复。若进程被强制杀死或电脑断电，自动恢复无法保证。此时先看最新实验目录中的 `comparison.md` 和 `experiment-record/experiment-record.json`，其中有按实际发现的接口/区域生成的手动步骤。对当前配置，恢复命令为：

```bash
sudo docker exec clab-netsecops-ospf-r1 vtysh -c 'configure terminal' -c 'router ospf' -c 'no network 10.0.12.0/30 area 0.0.0.1' -c 'network 10.0.12.0/30 area 0.0.0.0' -c 'end'
```

以上命令只适用于第二阶段那次采用进程级 `network` 配置的实验。第三阶段节点重启后运行态采用接口级写法；若在该模式下需要手动恢复同一故障，应使用该次实验记录中的接口名和原始区域，例如：

```bash
sudo docker exec clab-netsecops-ospf-r1 vtysh -c 'configure terminal' -c 'interface eth1' -c 'no ip ospf area 0.0.0.1' -c 'ip ospf area 0.0.0.0' -c 'end'
```

执行后检查两端 `show ip ospf neighbor`、`show ip route ospf` 与远端 Loopback ping。若实验前配置后来变更，两个示例命令都不应直接套用，应使用该次实验记录中的原始区域、接口和配置模式。脚本仅覆盖当前单链路、双路由器实验；运行期间不要同时改动这两台路由器配置。

已发现 Containerlab 在第一次部署时生成的 `clab-netsecops-ospf/*/config/frr.conf` 仍是早期接口级 OSPF 配置；第一阶段最终通过时的运行态是后来修正过的进程级 `network` 配置，项目 `configs/*/frr.conf` 也采用该方式。第三阶段实际重启验证发现：旧生成配置虽然与项目源配置不同，但两节点重启后依然收敛成功，远端路由和 Loopback 双向 ping 都正常。因此先前“重启可能使基线失败”只是风险推测，并非已证实故障。重启后运行态回到接口级写法；故障脚本现在按实际模式注入和恢复，不改写启动文件。

## 第三阶段：重启恢复与离线诊断

### 重启恢复检查

在 Ubuntu/WSL2 执行：

```bash
python3 /mnt/c/Users/Administrator/Documents/Codex/2026-10-12/windows-agent-1-windows-wsl-linux/netsecops-agent/labs/ospf-baseline/verify_restart.py
```

脚本先检查已有实验是否健康，使用 `docker inspect` 核实两台 FRR 容器的 `/etc/frr` 实际挂载来源，并保存配置原件。随后只用 Containerlab 的节点级 `restart --node r1 --node r2` 重启这两台路由器，不重启 Docker、WSL、Windows，也不销毁或重建实验。若首次重启失败，它只在严格确认“旧生成配置导致的特定路由回归”且配置文件未被并发更改时，才将本项目正确源配置复制到两份生成 `frr.conf`，再重启相同节点；否则停止并保留证据。每次只读轮询上限 90 秒。

本次实际运行结果：`restart-verification/restart-20261013T080354Z-af42089e/` 为 **PASS**。`docker inspect` 证实 r1/r2 的 `/etc/frr` 分别绑定到 `clab-netsecops-ospf/r1/config`、`r2/config`；当前容器实际从其中的 `frr.conf` 启动，而不是直接读取拓扑里 `startup-config` 指向的项目源文件。源文件使用进程级 `network`，生成文件使用接口级 `ip ospf area`。第一次重启后两端运行态是接口级写法，但 OSPF 邻居 FULL、远端 /32 路由、互联地址和远端 Loopback 双向 ping 均通过，未触发修复，未修改两份启动文件。原始挂载、启动文件快照、重启前后观测和轮询见该目录；详细结果见 `report.md`。

### 离线诊断入口

仅提供一个采集器保存的 `observations.json`，另指定一个尚不存在的输出目录，例如：

```bash
python3 /mnt/c/Users/Administrator/Documents/Codex/2026-10-12/windows-agent-1-windows-wsl-linux/netsecops-agent/labs/ospf-baseline/diagnose_offline.py \
  --input /mnt/c/Users/Administrator/Documents/Codex/2026-10-12/windows-agent-1-windows-wsl-linux/netsecops-agent/labs/ospf-baseline/experiments/area-mismatch-20261013T072913Z-a2e17eda/observations/fault/snapshot/observations.json \
  --output-dir /mnt/c/Users/Administrator/Documents/Codex/2026-10-12/windows-agent-1-windows-wsl-linux/netsecops-agent/labs/ospf-baseline/diagnostic-acceptance/manual-example-001
```

输出为 `diagnosis.json` 与中文 `report.zh-CN.md`。示例结论为 `ospf_area_mismatch`；报告同时引用 r1 `eth1` 的 `Area 0.0.0.1` 和 r2 `eth1` 的 `Area 0.0.0.0`，并附邻居、路由、ping 症状。诊断代码只接收观测 JSON，不读取 `experiment-record/`、预期根因或输入目录名；目录路径仅用于打开指定观测文件和保存报告。邻居消失、路由缺失或 ping 失败不能单独证明区域不一致。命令执行失败会得到 `collection_failure`，区域字段缺失或证据矛盾会得到 `insufficient_evidence`。修复建议只写在报告中，不执行配置变更。

### 回归与验收

本地脚本检查：

```bash
python3 -B -m unittest discover -s /mnt/c/Users/Administrator/Documents/Codex/2026-10-12/windows-agent-1-windows-wsl-linux/netsecops-agent/labs/ospf-baseline/tests -q
```

本次在 Windows Python 运行共 **15 项通过**。旧实验的离线诊断验收记录在 `diagnostic-acceptance/acceptance-20261013T080150Z-99df4320/`，共六组：正常→`normal`、区域故障→`ospf_area_mismatch`、恢复→`normal`、缺少一端区域→`insufficient_evidence`、关键采集命令失败→`collection_failure`、仅有邻居/路由/ping 症状→`insufficient_evidence`；**六组均通过**。各组的输入、副本诊断 JSON、中文报告与总表均保留。这是当前单一拓扑和故障规则的验收，不是通用诊断准确率。

对任意新完成的同结构实验，可只用其三组观测再次验收：

```bash
python3 /mnt/c/Users/Administrator/Documents/Codex/2026-10-12/windows-agent-1-windows-wsl-linux/netsecops-agent/labs/ospf-baseline/run_diagnosis_acceptance.py \
  --experiment-dir /mnt/c/Users/Administrator/Documents/Codex/2026-10-12/windows-agent-1-windows-wsl-linux/netsecops-agent/labs/ospf-baseline/experiments/area-mismatch-20261013T081041Z-3fbfc8c4
```

### 新一轮故障实验与最终网络状态

在重启检查后，按实际接口级 OSPF 模式再次完整运行 `run_area_mismatch.py`。结果为 **PASS**，目录：`experiments/area-mismatch-20261013T081041Z-3fbfc8c4/`。实际观测如下：

| 阶段 | r1/r2 互联 Area | 双向 FULL | 双向远端 /32 路由 | 双向互联 ping | 双向远端 Loopback ping |
|---|---|---|---|---|---|
| 正常 | `0.0.0.0` / `0.0.0.0` | 是 | 是 | 是 | 是 |
| 故障 | `0.0.0.1` / `0.0.0.0` | 否 | 否 | 是 | 否 |
| 恢复 | `0.0.0.0` / `0.0.0.0` | 是 | 是 | 是 | 是 |

这是实验脚本对网络的实际检查，完整原始记录见本次实验的 `comparison.md` 与三组 `observations.json`。实验记录还确认 r1 原运行配置已恢复、两份启动配置哈希均未改变、错误列表为空；没有把故障配置保存为启动配置。最终恢复快照显示两台路由器的邻居、远端路由与双向 ping 全部正常。

**离线诊断验收与网络恢复检查分开：** 用上述新实验的三组观测再次执行 `run_diagnosis_acceptance.py`，结果见 `diagnostic-acceptance/acceptance-20261013T081618Z-c77eb02e/acceptance.md`，六组均通过。正常和恢复观测被判为 `normal`，故障观测被判为 `ospf_area_mismatch`，两种关键缺证据/失败输入分别得到 `insufficient_evidence` 和 `collection_failure`；只有症状的输入为 `insufficient_evidence`。这些分类来自离线 JSON，不代表重新访问了现场网络。现场恢复以故障实验的最终只读采集与轮询为依据。

当前能力只覆盖两台 FRR 路由器、一条 OSPF 互联链路，以及观测正常/区域不一致/证据不足/采集失败四类判断。不处理其他 OSPF 故障的根因归类；没有概率、模型或通用网络故障识别能力。

## 第四阶段：DeepSeek 只读工具调用 Agent

项目只使用一个诊断 Agent，模型为 `deepseek-flash`，端点为 `https://api.deepseek.com`。实现采用 DeepSeek Chat Completions 的 `tool_calls` → `role: tool` 多轮流程，并通过 `thinking: {"type": "disabled"}` 关闭思考模式；没有把 OpenAI Responses API 仅替换 URL 后套用。模型只收到任务和必要拓扑，后续由它自行选择工具。规则诊断程序 `diagnose_offline.py` 保持独立，不进入 Agent 提示词。

六个只读工具为 `query_topology`、`query_interface`、`query_ospf_interface`、`query_neighbors`、`query_routes`、`ping_address`。节点、互联接口、路由范围与 ping 目标都限制在本实验拓扑；底层只有 `collect_evidence.py` 的固定命令模板，无任意 shell 或配置写入入口。每次工具返回分配 `E001` 等 evidence_id，原始命令、输出、退出码和错误保存在 `trace.json`。回放只打开指定的单份观测 JSON，不把实验目录名、故障记录或规则诊断答案传给模型。最终 OSPF 区域不一致结论必须引用两端互联接口不同 Area 的原始证据；正常结论还要求双向 FULL 邻居、远端 OSPF 路由和 Loopback ping。证据缺失、命令失败或到达上限不能伪报成功。

本地配置文件是 `C:\Users\Administrator\Documents\Codex\2026-10-12\windows-agent-1-windows-wsl-linux\netsecops-agent\.env`，字段为 `DEEPSEEK_API_KEY=`；也可使用当前进程同名环境变量，环境变量优先。程序实际加载此文件，绝不输出密钥。`.env.example` 只有空占位符，`.gitignore` 排除 `.env` 和其他本地环境文件。当前项目尚不是 Git 仓库，因此当前没有 Git 跟踪记录；将来若初始化仓库，该忽略规则会生效。上述配置只作用于本项目，不改变 Codex 应用模型或执行环境。`agent-settings.json` 设定模型和调用限制：最多 8 轮、12 次工具调用、每次输出最多 700 tokens、单次请求 30 秒、单条命令 12 秒、整个诊断 180 秒、最多 2 次重试。只有网络/服务端错误重试；认证、余额、限流和其他 4xx 错误立即停止。

回放可在 Windows PowerShell 运行；现场 Docker 工具只在 Ubuntu/WSL2 可用。以下示例使用 Windows PowerShell，路径保持在当前 Windows 工作区：

```powershell
python -B .\netsecops-agent\labs\ospf-baseline\agent_cli.py --mode replay --observations .\netsecops-agent\labs\ospf-baseline\experiments\area-mismatch-20261013T081041Z-3fbfc8c4\observations\normal\observations.json --run-label normal --task "检查 r1 到 r2 Loopback 的连通性并解释异常原因。请自行选择必要的只读工具，在调用上限内优先核对 OSPF 区域、邻居、远端路由和双向 Loopback ping；不必检查与结论无关的接口。"
```

将 `normal/observations.json` 换成 `fault/snapshot/observations.json` 即可回放故障。现场模式入口为在 Ubuntu/WSL2 运行 `python3 /mnt/c/Users/Administrator/Documents/Codex/2026-10-12/windows-agent-1-windows-wsl-linux/netsecops-agent/labs/ospf-baseline/agent_cli.py --mode live`；它只查询现有 r1/r2 容器。输出自动保存到 `labs/ospf-baseline/agent-runs/<标签>-<时间>-<随机号>/trace.json` 和 `report.zh-CN.md`。`trace.json` 包含模型逐轮消息、所选工具、每次工具返回及原始证据、最终结论、耗时、每次请求与累计 token 用量；不含 API Key。

### 验收记录

本地自动测试（模拟模型、无 API 调用）当前共 **34 项通过**，覆盖工具非法参数、`eth1@if...` 输出解析、区域引用限制、缺少一端区域、关键命令失败、模型多轮工具结果、token 累加、调用上限、认证和余额错误不重试、持续服务端错误按上限停止、故障期间 Agent 异常后仍执行恢复，以及独立验收脚本对伪造工具调用或无效引用的拒绝。这些测试不计入真实模型验收。

真实密钥只检查能否加载，结果为“已配置”；没有记录或显示其内容。真实调用使用 `deepseek-flash`：

| 测试 | 实际状态 | 工具调用 | 耗时 | API 累计 tokens | 轨迹目录 |
|---|---|---:|---:|---:|---|
| 小规模拓扑工具调用 | 通过；模型选择 `query_topology`，证据 `E001`，仅有拓扑证据故结论为 `insufficient_evidence` | 1 | 3.188 秒 | 3209 | `agent-runs/smoke-20261013T085456Z-d522f80f/` |
| 正常快照回放 | 通过；`normal`，引用 `E001`–`E009` | 9 | 4.359 秒 | 10941 | `agent-runs/normal-20261013T085804Z-7f07762f/` |
| 区域不一致快照回放 | 通过；`ospf_area_mismatch`，引用 `E001`–`E009` | 9 | 5.218 秒 | 10490 | `agent-runs/mismatch-20261013T085844Z-e52d75aa/` |
| 缺少一端 Area 回放 | 通过；`insufficient_evidence`，不以邻居消失等症状证明区域不一致 | 9 | 7.047 秒 | 10262 | `agent-runs/missing-area-20261013T142045Z-8adf2bed/` |
| 关键命令失败回放 | Agent 最终判为 `collection_failure`；模型原始 JSON 判为 `insufficient_evidence`，由引用失败命令的证据保护层纠正 | 8 | 7.625 秒 | 9032 | `agent-runs/failed-command-20261013T142100Z-e4241eeb/` |

两类回放由模型实际选择相同的一组工具：拓扑、两端互联接口 OSPF、两端邻居、两端 OSPF 路由、双向远端 Loopback ping。正常回放中两端均为 Area `0.0.0.0`、邻居 Full、路由及 ping 正常；故障回放中 r1 为 `0.0.0.1`、r2 为 `0.0.0.0`，邻居与远端路由消失、双向 Loopback ping 失败。区域结论以 `E002` 和 `E003` 的接口原始输出为依据，而非只凭症状。各组完整模型消息、工具证据和中文诊断见上述目录。

对两份既有正常/故障真实轨迹逐轮比对，实际工具调用均来自 DeepSeek API 回复中的 `tool_calls`；调用循环只解析回复中的名称与 JSON 参数，再交给白名单校验，不预置采集队列。两次的模型选择恰好相同，参数也相同，差别在工具返回值及结论：

| 顺序 | 工具与参数 | 正常观测 / 故障观测 |
|---:|---|---|
| 1 | `query_topology {}` | 地址相同 |
| 2–3 | `query_ospf_interface {node:r1,interface:eth1}`、`{node:r2,interface:eth1}` | `0.0.0.0/0.0.0.0` / `0.0.0.1/0.0.0.0` |
| 4–5 | `query_neighbors {node:r1}`、`{node:r2}` | 双向 Full / 双向无邻居 |
| 6–7 | `query_routes {node:r1,scope:ospf}`、`{node:r2,scope:ospf}` | 双向远端 /32 / 双向无远端 OSPF 路由 |
| 8–9 | `ping_address {node:r1,target:10.255.0.2}`、`{node:r2,target:10.255.0.1}` | 双向成功 / 双向失败 |

首次正常回放达到上限时，模型先额外调用了 r1/r2 的 `query_interface` 检查 `eth1` 和 `lo` 共四次；当时接口地址输出中的 `eth1@if...` 未被解析成 `eth1`，使两次 `eth1` 查询误报不可用。到第 12 次调用只完成单向 ping，第 13 次请求的反向 ping 被固定的 12 次上限拒绝。修复是识别 Linux 虚拟接口名的 `@` 后缀，并在任务文字中优先强调与结论直接相关的证据、跳过无关接口；上限仍为 12 次，代码中没有固定上述九项的执行顺序。

失败尝试也保留：首次请求因本机网络权限返回 `URLError`（0 次模型响应、0 tokens）；权限开放后，首次小测试虽执行 1 次工具，但模型最终回复夹带正文，未形成可解析最终 JSON，保存在 `smoke-20261013T085341Z-da069c84/`；首次正常回放调用满 12 次工具后仍缺一侧 ping，按 `incomplete` 处理，保存在 `normal-20261013T085549Z-01fa065a/`。随后修正了 JSON 代码块解析、Linux 虚拟接口名称解析，并在不提高限制的情况下聚焦诊断任务。新增两种异常输入的首次真实回放保存在 `missing-area-20261013T141730Z-2a79a678/` 与 `failed-command-20261013T141746Z-8c2a9c7a/`：前者输出了未经确认的具体区域改配建议；后者未明确归类采集失败，均由独立验收判为 FAIL。之后修正证据保护层，失败命令的原始输出仍保存在轨迹，但不再作为有效观测传给模型；对缺证据的确定性改配建议予以中和；引用关键采集失败时最终状态统一为 `collection_failure`。保留失败记录，未修改验收条件。Windows 执行环境直接启动 WSL 返回 `E_ACCESSDENIED`，现场脚本由用户在 Ubuntu/WSL2 终端运行。单一拓扑、单一故障场景的结果不代表通用诊断准确率，也不代表其他故障可被正确识别。

### 独立验收与有界现场入口

`judge_agent_trace.py` 与 Agent 分离：验收期望由调用者在 Agent 完成后传入，Agent 不读取参考答案。它逐项比对 API 返回的 `tool_calls` 与实际执行工具及参数、证据 ID 与原始输出、结论所需的两端 Area / 邻居 / 路由 / ping、请求用量汇总；异常输入也检查失败命令引用与建议是否越过证据。

在 Ubuntu/WSL2 运行以下入口，先做当前正常现场 Agent 诊断；若模型 API 没有认证、余额或持续接口错误，再由现有实验脚本检查基线、注入同一故障，故障稳定后运行只读现场 Agent，最后无论 Agent 成败都尝试恢复原配置，并轮询核验邻居、远端路由、互联及 Loopback 双向 ping：

```bash
python3 /mnt/c/Users/Administrator/Documents/Codex/2026-10-12/windows-agent-1-windows-wsl-linux/netsecops-agent/labs/ospf-baseline/run_live_agent_acceptance.py
```

脚本不销毁容器、不保存故障为启动配置；如恢复检查失败，以输出的 `Experiment` 路径查看 `comparison.md` 与 `experiment-record/experiment-record.json` 内按实际接口和原区域生成的手动恢复步骤。不得将 `acceptance.json` 的总状态当作网络状态；必须单独查看 `Final network: RESTORED`。两次现场 Agent 只使用六个只读工具，不接触实验注入记录或预期根因。

本次现场运行见 `agent-runs/live-acceptance-20261013T143221Z-63fd3402/acceptance.json`，状态 **PASS**：正常现场 Agent 在 4 次模型响应中执行 11 次工具，9.596 秒，输入 18,833 / 输出 970 tokens，结论 `normal`；故障稳定期间的现场 Agent 在 3 次响应中执行 9 次工具，5.923 秒，输入 9,457 / 输出 867 tokens，结论 `ospf_area_mismatch`。故障现场的 `E002`/`E003` 直接显示 r1/r2 的 Area 分别为 `0.0.0.1` / `0.0.0.0`。正常现场还由模型额外选择了 r1/r2 的互联接口状态查询，故实际调用数不同于两类旧回放；这也是调用顺序未在代码中写死的旁证。两份完整轨迹及中文报告分别在 `agent-runs/live-normal-20261013T143231Z-343f3366/`、`agent-runs/live-fault-20261013T143329Z-7994fa2a/`，相邻 `judge.json` 为独立验收判定。

实验目录 `experiments/area-mismatch-20261013T143231Z-da809951/` 的记录为 **PASS**，`config_restored=true`、`startup_unchanged=true`、错误列表为空；恢复轮询与最终原始快照均显示两端 Area `0.0.0.0`、邻居 FULL、远端 /32 OSPF 路由及互联地址和 Loopback 双向 ping 全部正常。脚本结束时的最终网络为 **RESTORED**。这属于实际现场检查，与离线回放验收分开。

统一验收表可在 Windows 或 WSL 运行 `build_agent_acceptance.py` 生成；带上 `--live-acceptance <上述现场 acceptance.json 路径>` 纳入现场结果。本次最终表在 `agent-runs/unified-acceptance-20261013T143609Z-c0141d35/acceptance.md`，总状态 **PASS**；它分别列出模拟测试、四类真实模型回放和两类真实模型现场诊断，包含模型请求、工具调用、耗时、输入/输出 tokens、模型原始状态、Agent 最终状态和网络恢复状态。关键命令失败回放的模型原始状态为 `insufficient_evidence`，最终状态由证据保护层纠正为 `collection_failure`，表中明确展示这一差异。

DeepSeek 官方依据：[Tool Calls](https://api-docs.deepseek.com/guides/tool_calls/)、[Thinking Mode](https://api-docs.deepseek.com/guides/thinking_mode/)、[Chat Completion API](https://api-docs.deepseek.com/api/create-chat-completion/)。

## 官方依据

- [Containerlab FRRouting kind](https://containerlab.dev/manual/kinds/frr/)
- [Containerlab FRR OSPF 示例](https://containerlab.dev/lab-examples/frr01/)
- [FRRouting OSPFv2 文档](https://docs.frrouting.org/en/latest/ospfd.html)
- [Containerlab 节点级 restart](https://containerlab.dev/cmd/restart/)

## 第五阶段：三类 OSPF 故障的鉴别诊断

本阶段仍使用原来的两台 FRR 路由器和单条互联，不安装软件、不重建容器、不修改启动配置。只读预检 `labs/ospf-baseline/inspect_frr_stage5.py` 在现有容器中实际查到 r1/r2 均为 `FRRouting 10.7.1_git`，`show interface eth1`、`show ip ospf interface eth1` 与 `show running-config` 均成功。互联接口名由 `topology.clab.yml` 解析，不在故障脚本中假定。依据 [FRR 10.7 Zebra 接口配置](https://docs.frrouting.org/en/stable-10.7/zebra.html) 与 [FRR 10.7 OSPFv2 接口配置](https://docs.frrouting.org/en/stable-10.7/ospfd.html)，开发验证实际使用接口视图的 `shutdown` / `no shutdown` 和 `ip ospf passive` / `no ip ospf passive`，并以 `show interface`、`show ip ospf interface` 和运行配置确认生效。r1 管理关闭时 `show interface eth1` 显示 `Interface eth1 is down`、`Shutdown config is Enabled by CLI`；passive 时 `show ip ospf interface eth1` 显示 `No Hellos (Passive interface)`，运行配置包含 `ip ospf passive`。

新的受控入口为 `labs/ospf-baseline/run_fault_scenario.py`：`--fault` 只能选 `area_mismatch`、`admin_down`、`ospf_passive`，`--target` 只能选 `r1` 或 `r2`。例如在 Ubuntu/WSL2 运行：

```bash
python3 /mnt/c/Users/Administrator/Documents/Codex/2026-10-12/windows-agent-1-windows-wsl-linux/netsecops-agent/labs/ospf-baseline/run_fault_scenario.py --fault ospf_passive --target r1
```

每次脚本先检查原网络健康、容器身份和 FRR 版本，保存目标路由器的运行配置和原有启动文件哈希；只注入一种运行时故障。故障稳定后采集独立快照。配置命令部分失败、轮询或采集异常也会进入 `finally` 恢复；恢复后比较完整运行配置与原快照、启动文件哈希，并检查两端邻居 FULL、远端 /32 OSPF 路由、互联地址和 Loopback 双向 ping。`experiment-record/` 保存故障名称、操作和手工恢复命令，诊断工具仅读取 `observations/` 中的原始观测。若恢复失败，脚本返回 FAIL 和 `Final network: NOT VERIFIED`，应先阅读该次 `comparison.md` 和独立实验记录，不要继续注入新故障。

`diagnose_offline.py` 保持为独立规则基线。它对区域不一致要求两端互联接口的不同 Area；对管理关闭要求故障接口的 `shutdown` 运行配置及 FRR 有效管理状态；对 passive 要求该互联接口作用域内的 `ip ospf passive` 或 `router ospf` 下针对该接口的 `passive-interface`。邻居、路由与 ping 异常只算症状，不能单独证明三种根因。Agent 新增只读 `query_interface_config`，`query_interface` 同时返回 Linux 链路及 FRR 管理状态；所有节点、接口和目标地址仍受白名单约束，不提供配置写入工具。模型原始 JSON 存于 `model_raw_final`，证据保护后的结论存于 `final`，修改字段与原因存于 `guardrail_changes`；工具原始输出、evidence_id、耗时及每次/累计 token 用量仍在同一轨迹。预算仍为 8 轮、12 次工具调用、700 输出 tokens、请求 30 秒、总计 180 秒与最多 2 次有限重试；超限不能算完成。

r1 开发验证的三次新实验分别在 `experiments/admin_down-r1-20261013T155540Z-c1baf540/`、`experiments/ospf_passive-r1-20261013T155945Z-466708a8/` 和 `experiments/area_mismatch-r1-20261013T161220Z-5b5092b8/`，三者均 PASS，运行配置恢复、启动文件不变、最终网络恢复。规则基线在三份故障观测上分别输出 `interface_admin_down`、`ospf_passive`、`ospf_area_mismatch`。真实模型开发回放和独立评判也分别通过；正常快照回放为 `normal`。首次管理关闭回放达到 12 次工具上限，另两次开发回放曾因模型在 JSON 前输出说明、以及取得 passive 直接证据后继续调用工具而判 `incomplete`；这些原始失败轨迹保留在 `agent-runs/diagnosis-20261013T155911Z-1731f90b/`、`agent-runs/diagnosis-20261013T160234Z-0c595274/`、`agent-runs/diagnosis-20261013T160245Z-fec8f24d/`。冻结前修复为：允许解析尾部完整 JSON 对象，工具预算用尽时不再提供工具、要求模型基于现有证据结束；同时说明拓扑工具已提供 Loopback 地址、无需重复查 `lo`，已获直接根因证据时可停止。未提高调用上限，也未在代码中写死采集顺序。

冻结清单在 `labs/ospf-baseline/stage5-freeze.json`，记录提示词、规则、工具、模型流程、验收代码与调用设置的 SHA256；在 r2 测试前生成，后续脚本会核对。独立 r2 测试的 WSL 入口为 `run_stage5_r2.py`：按区域不一致、管理关闭、passive 三种故障依次只针对 r2 运行，任一实验或恢复失败即停止后续注入，末尾再做一次独立只读网络健康采集。其 `batch.json` 路径是真实模型验收 `run_stage5_acceptance.py --batch <batch.json>` 的输入；本次因批次内路径使用 `/mnt/c/...`，在 Ubuntu/WSL2 执行。验收器对每个场景复制一份中性命名的观测快照；规则和 Agent 读取同一份冻结 JSON。Agent 只接收统一任务描述和必要拓扑，既不读实验标签或预期根因，也不读规则答案。独立评判器在模型完成后核验 API `tool_calls` 与实际工具轨迹、证据 ID、直接根因内容和保护层变更记录；输出模型原始与保护后结论、调用数、耗时及 token 用量。此有界验收不构成通用准确率。

本次冻结时间为 `2026-10-13T16:16:38+00:00`；最终再次核对全部 10 个冻结文件哈希一致。本地模拟/静态测试 **43 项通过**，不计入真实模型验收。r2 新批次在 `labs/ospf-baseline/stage5-r2-tests/batch-20261013T161857Z-72f15a49/batch.json`：三种实验各自 PASS、各自配置恢复、启动文件未改变。批次末尾额外只读采集显示 r1/r2 均为 Area `0.0.0.0`、邻居 FULL、远端 /32 OSPF 路由存在、互联地址及远端 Loopback 双向 ping 成功；`Final network: RESTORED`。原始正常/故障/恢复证据分别在 `experiments/area_mismatch-r2-20261013T161857Z-595dc1c7/`、`experiments/admin_down-r2-20261013T162006Z-431a42ae/`、`experiments/ospf_passive-r2-20261013T162031Z-8fb41311/` 下，旧实验数据未删除。

真实 DeepSeek 验收在 Ubuntu/WSL2 执行（批次文件中的实验路径采用 `/mnt/c/...`）：

```bash
python3 /mnt/c/Users/Administrator/Documents/Codex/2026-10-12/windows-agent-1-windows-wsl-linux/netsecops-agent/labs/ospf-baseline/run_stage5_acceptance.py \
  --batch /mnt/c/Users/Administrator/Documents/Codex/2026-10-12/windows-agent-1-windows-wsl-linux/netsecops-agent/labs/ospf-baseline/stage5-r2-tests/batch-20261013T161857Z-72f15a49/batch.json
```

统一验收记录在 `labs/ospf-baseline/agent-runs/stage5-acceptance-20261013T162832Z-db38a0f7/acceptance.md` 与相邻 JSON，状态 **PASS**。三组均用同一任务描述，规则和模型读取每个 `case-0N/observations.json` 的同一冻结哈希；独立评判逐轮验证实际工具名称与参数确实来自 DeepSeek 返回的 `tool_calls`，所有最终引用的 evidence_id 均存在且支持结论。实际数据如下：

| r2 场景 | 规则基线 | 模型原始 / 保护后 | 独立验收 | 请求 / 工具 | 耗时 | 输入 / 输出 tokens | 关键直接证据 |
|---|---|---|---|---:|---:|---:|---|
| Area 不一致 | `ospf_area_mismatch` | 同左 / 同左 | PASS | 4 / 7 | 5.928 秒 | 11,721 / 786 | `E002` r1 Area `0.0.0.0`，`E003` r2 Area `0.0.0.1` |
| 管理关闭 | `interface_admin_down` | 同左 / 同左 | PASS | 5 / 8 | 7.927 秒 | 21,283 / 1,076 | r2 `show interface eth1` 的 `Shutdown config is Enabled by CLI` |
| OSPF passive | `ospf_passive` | 同左 / 同左 | PASS | 6 / 10 | 8.250 秒 | 25,447 / 1,050 | r2 互联接口的 `ip ospf passive` 运行配置 |

逐场景轨迹、原始工具输出、规则 JSON、中文报告与独立评判分别在该验收目录的 `case-01/`、`case-02/`、`case-03/`。保护层没有改变三次根因状态。`case-01` 的模型把 `missing_information` 写成字符串，保护层按约定数组格式清空该非根因字段，`guardrail_changes` 留有原值、最终值及校验原因；缺少路由/ping 是模型自述未采集的症状性信息，不是确认 Area 差异所必需的直接证据。另两场景没有保护层修改。首次 r1 开发回放触及上限或 JSON 格式失败的原始轨迹仍保留；修复都发生在冻结及 r2 独立测试之前。未提高调用限制，r2 无超限或未执行项。三次通过只说明当前双路由器拓扑、当前三种受控故障的有界验收结果，不是通用网络诊断准确率。

## v0.2 可靠性修复（离线验证）

依据外部源码审查反馈，v0.2 修复了实验入口的 SIGTERM 恢复、根因位置与修复对象绑定、回放观测身份校验、`missing_information` 字符串保留、测试样本便携性以及工具/命令统一 deadline。具体代码位置、复现反例和局限见 [RELIABILITY_CHANGELOG.md](RELIABILITY_CHANGELOG.md)。此节是**v0.2 历史修复记录**；上面的 v0.1 第五阶段冻结与真实 DeepSeek 验收属于更早的历史数据，没有被重写，也不能自动推定 v0.2 已通过真实模型或现场故障验收。`labs/ospf-baseline/reliability-v0.2.json` 是当时的独立清单，旧 `stage5-freeze.json` 保持原样。v0.2 时的 r2 入口会校验该清单；当前入口已转为下述 v0.3 清单，旧批次仍按原版本解读。

离线回归入口在项目根目录：

```bash
python3 run_clean_offline_tests.py
```

它只把白名单源码、拓扑/源配置和 `tests/fixtures/` 复制到临时目录，明确排除 `.env`、历史实验、Agent 轨迹、Containerlab 生成目录与 `.git`，使用当前 Python 执行完整 `unittest`。Ubuntu/WSL2 实跑结果：**54 项通过、0 跳过**，见 `review-results/v0.2-offline-20261014T015728Z/{result.json,unittest-output.txt}`；其中 SIGTERM 测试只终止自身创建的 mock 子进程，没有访问 Docker 或真实路由器。Windows 补充运行同一测试为 54 项、1 项 Linux 信号测试跳过。此次没有调用 DeepSeek、没有注入真实故障，也没有重新检查现场网络。

规则、保护层与独立评判器现从原始观测重新校验节点、容器命令、接口和地址；结构化根因包含故障类型、节点、接口及直接证据 ID。Area 不一致只确定两端 Area 不同，不擅自指定哪一端应改；管理关闭和 passive 的修复对象由已验证的故障接口生成。证据不足或采集失败不会被解释成网络故障。`missing_information` 若由模型给出非空字符串，会保存为单元素数组；报告另列实测事实与推断影响。统一总时限到达时不再启动后续工具命令。`SIGKILL`、主机断电等不可捕获的中断仍不能保证自动恢复，遇到恢复失败应以实验记录中的手动步骤核对现场。

## v0.3 复审 P2 修复（仅离线）

针对 `review-feedback-v0.2.zip` 的三项复审反例，v0.3 区分了合法的不可达 ping 症状与命令采集失败；将 OSPF `Internet Address`/掩码/唯一 Area 与同次运行配置绑定；独立评判器改为核对权威修复建议的完整确定性模板，不能靠“提到正确节点”掩盖在另一节点执行配置命令。反例测试保存在 `labs/ospf-baseline/tests/test_review_regressions_v02.py`。修复和版本关系见 [REVIEW_REPAIR_v0.3.md](REVIEW_REPAIR_v0.3.md)。

在项目根目录运行 `python3 run_clean_offline_tests_v03.py`，会从不含密钥、历史实验目录和 Containerlab 生成目录的临时白名单副本执行完整测试。Ubuntu/WSL2 Python 3.12.3 的实际记录为 **57 项通过、0 跳过**，见 `review-results/v0.3-offline-20261014T031239Z/{result.json,unittest-output.txt}`；Windows 补充测试为 57 项、1 项 Linux 专用信号测试跳过。所附原始三个复审测试也全部通过。v0.3 独立清单为 `labs/ospf-baseline/reliability-v0.3.json`；v0.1/v0.2 清单和原始验收未覆盖。当前 r2 批次/验收入口核对 v0.3，不能拿旧批次当作新版本验收。

本次没有调用模型 API、没有运行真实故障实验或重新核对当前网络。**v0.3 只有离线回归通过，真实模型与现场验收仍未执行。**

## v0.3 后续有界真实验收

上述“仅离线”描述的是 v0.3 修复发布时的状态。2026-10-14 又按冻结版本完成一次有界真实验收：七项真实模型诊断 **5 PASS、2 FAIL**，最终网络 **RESTORED**；历史清单和验收记录未覆盖。正常现场漏引拓扑证据，关键采集失败回放漏引失败命令证据，均保留为 FAIL；三种新 r2 故障回放和缺失一端 Area 回放通过。完整统一表、调用与 token 数据、失败原因及原始记录路径见 [V03_REAL_ACCEPTANCE_20261014.md](V03_REAL_ACCEPTANCE_20261014.md)。本结果不能描述为通用诊断准确率。

## v0.4 针对性离线修复

v0.4 针对上述两个真实 FAIL 做证据归属与当前采集失败的离线修复，原 v0.3 模型响应、评判与冻结记录保持原样。保护层与独立评判现在都要求正常结论具备经核实的拓扑、两端 Area、邻居、路由及双向 Loopback ping；只能补入本轮实际取得并通过节点/命令/地址/输出验证的必要引用，逐项记录来源与原因。关键命令失败不依赖模型是否主动引用，但缺失记录和数据矛盾不能冒充执行失败；后续有效成功重采取代当前失败，旧失败仍保留。修复说明见 [REVIEW_REPAIR_v0.4.md](REVIEW_REPAIR_v0.4.md)，源码审查入口见 [REVIEW_GUIDE_v0.4.md](REVIEW_GUIDE_v0.4.md)。本阶段仍仅做离线验证，不调用 DeepSeek 或真实故障实验；v0.4 不继承 v0.3 的真实验收结论。

Ubuntu/WSL2 的 v0.4 干净临时副本测试已通过 **66 项、0 跳过**，记录在 `review-results/v0.4-offline-20261014T093031Z/result.json`。独立版本清单为 `labs/ospf-baseline/reliability-v0.4.json`，新审查包为项目根目录的 `review-bundle-v0.4.zip`；两者不更改旧版本清单或失败轨迹。

## v0.5 成功重采边界（仅离线）

定向复审发现：FRR 在接口 down 时成功返回 `OSPF not enabled on this interface`，虽然没有 Area，却应取代同查询先前的执行失败。v0.5 将固定命令执行成功与 Area 根因证据分开；只接受节点、接口、命令及完整 FRR 状态输出均匹配的重采，不把它当成区域正常或区域不一致的证明。附件两项复现测试在旧实现上均失败，修复后均通过；项目另加入四项身份、地址、残缺输出和无 Area 边界测试。运行入口为 `python3 run_clean_offline_tests_v05.py`，修复细节及版本关系见 [REVIEW_REPAIR_v0.5.md](REVIEW_REPAIR_v0.5.md)，审查导航见 [REVIEW_GUIDE_v0.5.md](REVIEW_GUIDE_v0.5.md)。本轮不调用模型 API 或运行真实故障实验。

Ubuntu/WSL2 的 v0.5 干净临时副本测试已通过 **72 项、0 跳过**，记录在 `review-results/v0.5-offline-20261014T105947Z/result.json`。新清单为 `labs/ospf-baseline/reliability-v0.5.json`，审查包为 `review-bundle-v0.5.zip`；旧版本清单、验收轨迹及原 v0.3 的两个真实 FAIL 均保留。
