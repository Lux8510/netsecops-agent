# v1 第一阶段独立复核指南

本包只评审**多节点拓扑模型与 HTTP 业务实验配置**。没有部署新实验、采集现场证据、调用模型或运行故障注入。`synthetic/mock` 示例不能被解释为真实网络结果。

## 文件入口与依赖

| 入口 | 用途 |
| --- | --- |
| `v1/topology.py` | 标准库拓扑模型；校验角色、接口、地址、链路、业务目标；由有效清单生成只读工具目标范围；枚举结构候选路径 |
| `v1/lab/topology.json` | 唯一的 v1 拓扑意图清单，含六节点、六链路、OSPF cost 与 HTTP 目标 |
| `v1/lab/topology.clab.yml` | 待部署的 Containerlab 定义，独立实验/管理网/镜像标签/主机静态路由 |
| `v1/lab/configs/r1/frr.conf` 至 `r4/frr.conf` | 四台 FRR 10.7.1 路由器的启动配置 |
| `v1/lab/services/health` | HTTP `/health` 文件内容 |
| `v1/tests/test_topology.py`、`test_lab_assets.py` | 模型、授权、路径与静态资产一致性测试 |
| `v1/tests/fixtures/observations/sample-01..04.json` | 四组手工设计的 `synthetic/mock` 观测；不是现场采集 |
| `v1/tests/fixtures/expected.json` | 与观测分离的测试标签；不能作为未来 Agent 输入 |
| `v1/run_offline_tests.py` | 将 `v1/` 复制进临时目录运行本阶段测试 |
| `v1/README.md`、`v1/VALIDATION.md` | 实验规划、限制与当时的测试汇总 |
| `test-results/` | 从现存旧版 `review-results/` **选择性复制**的原始测试日志和结果 JSON，源目录未修改 |
| `MANIFEST.json` | 包内文件路径、来源、大小及逐文件 SHA-256；清单不计算自身的哈希 |

**外部项目依赖：无。** v1 测试只导入 Python 3.12 标准库及包内 `v1` 文件。Windows 上曾用已安装的 PyYAML 独立解析 Containerlab YAML；它不是 v1 测试依赖，也未收入包。旧版 v0.7/WSL 回归源码不在本包中，包内仅提供本轮现存的执行记录；若要重跑旧版测试，必须取得完整旧版项目及其 `requirements-progress.txt`，不应由此包推断旧版代码可独立重跑。

## 离线复核方法

解压到任意空目录，在解压根目录执行：

```bash
python3 -m v1.topology
python3 -B v1/run_offline_tests.py
```

第二条命令使用临时副本，不读取 `.env`、不连接 Docker、Containerlab 或模型 API。成功条件为 23 项测试通过、0 跳过。`candidate_paths()` 的输出是**拓扑结构和预设 cost**，不是当前路由表。若独立环境缺 Python 3.12，应报告依赖缺失，不把未运行当作通过。

## 拓扑、地址与主备意图

四台路由器 r1、r2、r3、r4，客户端 `client` 连接 r1，HTTP 主机 `server` 连接 r4。所有路由器的 OSPF area 为 `0.0.0.0`，Router ID/loopback 分别为 `10.255.1.1/32`、`.2/32`、`.3/32`、`.4/32`。

| 链路 | 数据网段 | 端点 | 双端预设 cost |
| --- | --- | --- | ---: |
| client–r1 | `10.101.0.0/30` | client `.2` / r1 `.1` | 接入，r1 passive |
| r1–r2 | `10.12.0.0/30` | r1 `.1` / r2 `.2` | 10 |
| r2–r4 | `10.24.0.0/30` | r2 `.1` / r4 `.2` | 10 |
| r1–r3 | `10.13.0.0/30` | r1 `.1` / r3 `.2` | 50 |
| r3–r4 | `10.34.0.0/30` | r3 `.1` / r4 `.2` | 50 |
| r4–server | `10.104.0.0/30` | r4 `.1` / server `.2` | 接入，r4 passive |

主路意图 r1–r2–r4 的路由器段 cost 20，备路意图 r1–r3–r4 cost 100。HTTP 目标为 `http://10.104.0.2:8080/health`；client 对服务网段经 `10.101.0.1`，server 对客户端网段经 `10.104.0.1` 设置静态回程路由。独立管理网计划为 `netsecops-v1-mgmt` / `172.31.87.0/24`，六节点计划使用 `.11`–`.16`；尚未检查现场地址冲突。

## 测试结果与原始记录边界

| 项目 | 环境 | 本轮实际结果 | 原始日志 |
| --- | --- | --- | --- |
| v1 干净副本离线测试 | Windows Python 3.12.10 | PASS：23 项，0 跳过 | **未提供**：当时只保留终端输出与 `v1/VALIDATION.md` 汇总 |
| v1 干净副本离线测试 | Ubuntu 24.04/WSL2 Python 3.12.3 | PASS：23 项，0 跳过 | **未提供**：当时只保留终端输出与汇总 |
| v0.7 旧版干净副本回归 | Windows 现有虚拟环境，Python 3.12.10 | PASS：77 项，1 跳过；跳过 POSIX SIGTERM 测试 | `test-results/v0.7-windows/result.json` 与 `unittest-output.txt` |
| 旧版可靠性测试 | Ubuntu/WSL2 Python 3.12.3 | PASS：11 项，0 跳过，含 mock 子进程 SIGTERM 测试 | **未提供**：当时只保留终端输出与汇总 |
| v0.7 旧版全量入口 | Ubuntu/WSL2 系统 Python | 缺少 `fastapi`，依赖检查阶段退出；**未运行全量测试** | **未提供**：当时只保留终端提示 |
| v0.6 旧版全量入口 | Ubuntu/WSL2 系统 Python | **FAIL**：75 项中 1 项导入错误，`fastapi` 缺失 | `test-results/v0.6-wsl-failed/result.json` 与 `unittest-output.txt` |
| Containerlab YAML 语法解析 | Windows 已有 PyYAML | PASS：6 节点、6 链路可解析 | **未提供**：当时只保留终端输出与汇总 |

因此，**不能称旧版 WSL 全量回归已完成或全部通过**。v0.7 Windows 的 1 项跳过由 WSL 的 11 项可靠性测试覆盖，但这不等于 WSL 全量 77 项通过。日志是旧版测试的有限执行记录，不是 v1 多节点现场证据，也没有通过重跑筛选结果。

审查包内的 `test-results/v0.7-windows/unittest-output.txt` 是原始日志的**路径脱敏副本**：仅将个人用户目录前缀替换为 `<LOCAL_USER_HOME>`；原始日志未修改。`MANIFEST.json` 同时记录原文件与包内副本的 SHA-256 及替换次数。其余测试日志按原字节收录。缺失的原始日志没有补造。

## 尚未进行的现场验证

没有执行 Containerlab 自身解析/部署，也没有检查现有 Docker 网络、容器名、WSL 路由与计划地址是否冲突；FRR 配置在 10.7.1 中的实际加载、OSPF FULL、主备真实下一跳、client/server 双向可达及 HTTP 200/`ok` 均**未验证**。Python Alpine 镜像和其中的 `ip` 小程序也尚未拉取或运行。图上两条路径只证明结构存在，不证明路由或业务正常。

下一阶段应先做只读资源冲突与镜像/权限检查，确认后才可部署新实验并建立业务基线。本包没有旧 Agent 的多节点诊断实现，不应称为已部署的多节点诊断 Agent。
