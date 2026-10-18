# NetSecOps Agent 项目概览（截至 v0.5 定向验收）

## 要解决的问题与范围

网络排障容易把“邻居消失、路由缺失、ping 失败”误当作根因，也容易把采集命令失败误当作网络故障。本项目在固定的双 FRRouting 路由器、单条 OSPF 互联实验里，保存原始观测，让单个只读 Agent 自行选择检查，并用确定性证据规则约束最终诊断。它是受控实验基线，不是生产网络运维或自动修复系统。

拓扑为 r1/r2 的 `eth1` 互联（`10.0.12.1/30` ↔ `10.0.12.2/30`），Loopback 分别为 `10.255.0.1/32`、`10.255.0.2/32`。镜像和启动配置见 [拓扑](../labs/ospf-baseline/topology.clab.yml)及 [r1](../labs/ospf-baseline/configs/r1/frr.conf)、[r2](../labs/ospf-baseline/configs/r2/frr.conf) 源配置；现场实际配置仍以当次采集为准，不从文件名推断接口或区域。

## 架构与信任边界

1. [采集器](../labs/ospf-baseline/collect_evidence.py)从拓扑和实际运行配置识别节点、接口、地址；固定命令模板记录时间、实际命令、退出码、原始标准输出/错误和执行错误。
2. [工具层](../labs/ospf-baseline/agent_tools.py)提供现场 `LiveBackend` 与单份观测 JSON 的 `ReplayBackend`；每次调用得到独立 `evidence_id`，参数和回放记录的节点、命令、接口、地址、输出都要校验。
3. [DeepSeek 客户端](../labs/ospf-baseline/deepseek_client.py)使用 Chat Completions 的 `tool_calls`/工具结果多轮协议；[Agent 循环](../labs/ospf-baseline/agent_loop.py)只把任务、必要拓扑及模型本轮实际选择的工具结果交给模型，不预塞整套观测，也不固定采集顺序。
4. [证据校验](../labs/ospf-baseline/evidence_checks.py)与 Agent 的 `validate_final()` 构成保护层：核实直接证据、处理本轮关键采集失败和有效重采，必要时把本轮**已取得且已验证**但模型漏引的证据补入最终引用；保留 `model_raw_final`、`final`、`evidence_additions` 和 `guardrail_changes`。
5. [离线规则诊断](../labs/ospf-baseline/diagnose_offline.py)是独立比较基线，不是模型的参考答案输入。[独立评判器](../labs/ospf-baseline/judge_agent_trace.py)在诊断结束后核对 API 的工具调用与实际执行、证据 ID/原始内容、根因位置、权威修复建议、限制与 token 汇总。

故障实验的注入操作、预期根因和手动恢复步骤位于独立的 `experiment-record/`；Agent 回放只接收指定的 `observations.json`，不读取这些标签或规则结果。现场 Agent 没有配置写入工具。故障注入与恢复属于另一个受控实验脚本 [run_fault_scenario.py](../labs/ospf-baseline/run_fault_scenario.py)，不能称为 Agent 自动修复。

## 工具白名单与证据要求

工具名及参数由 [agent_tools.py](../labs/ospf-baseline/agent_tools.py) 的 `TOOL_ARGUMENTS` 定义：`query_topology()`、`query_interface(node, interface)`、`query_interface_config(node, interface)`、`query_ospf_interface(node, interface)`、`query_neighbors(node)`、`query_routes(node, scope)`、`ping_address(node, target)`。节点仅限 r1/r2；互联接口来自拓扑；路由范围仅 `all`/`ospf`；ping 目标仅对端互联或 Loopback 地址。底层只运行 [command_argv()](../labs/ospf-baseline/collect_evidence.py) 中的固定只读命令模板，不接受任意 shell 或配置命令。

根因不是由症状投票决定：

| 结论 | 必须核实的直接证据 |
|---|---|
| OSPF 区域不一致 | 两端互联接口各自有效、不同的 `Area` 原始输出；只证明两端不同，不擅自指定“错”的一端。 |
| 接口管理关闭 | 故障节点互联接口的 FRR 管理状态；修复对象绑定到该节点和接口。 |
| OSPF passive | 故障接口作用域内的有效 passive 运行配置；修复对象绑定到该节点和接口。 |
| 正常 | 可由两端原始配置核实的拓扑/地址、两端相同 Area、双向 FULL 邻居、远端 OSPF 路由和双向 Loopback ping。 |

邻居、路由和 ping 异常只是症状。关键命令真实失败时，保护层按本轮工具执行记录判定 `collection_failure`，不依赖模型主动引用失败 ID；失败输出不能证明 Area。节点/接口/地址/输出不匹配或字段缺失与执行失败分开处理。相同查询后续若返回身份匹配、完整有效的 FRR `down`/`OSPF not enabled` 输出，可取代旧执行失败，但**无 Area 仍不能确认正常或区域不一致**；旧失败历史保留。[evidence_checks.py](../labs/ospf-baseline/evidence_checks.py) 的 `critical_history()`、`normal_proofs()`及 [Agent 保护层](../labs/ospf-baseline/agent_loop.py)是对应实现。

## 已有记录：不能跨版本混算

- v0.5 离线干净副本测试：**72 项通过、0 跳过**，记录见 [result.json](../review-results/v0.5-offline-20261014T105947Z/result.json)。这是代码/样本回归，不是模型或现场验收。
- v0.5 单次 DeepSeek 定向验收：[acceptance.json](../labs/ospf-baseline/agent-runs/v05-targeted-20261014T113312Z-a98f2b1e/acceptance.json) 记录正常现场 PASS、关键命令失败回放 PASS、缺一端 Area 回放 PASS；动态成功重采回放为 **NOT_EXERCISED**，总状态 **INCOMPLETE**。正常现场的 [轨迹](../labs/ospf-baseline/agent-runs/v05-targeted-20261014T113312Z-a98f2b1e/case-01/agent-trace.json) 引用 `E001`–`E009`，已覆盖全部正常必要证据，无补引。失败回放的保护层把模型原始“区域不一致”改为“采集失败”，并补引本轮实际失败的 `E003`；缺 Area 回放把模型原始“区域不一致”改为“证据不足”。
- 动态回放中模型只查询了一次 r2 OSPF 接口，该次执行失败；虽另取得 r2 管理关闭证据，但**没有进行同查询有效重采**。因此其模型原始管理关闭结论被保护为采集失败；当前失败判定通过，但“成功重采后确认管理关闭”的真实模型路径未验收。见 [case-04 轨迹](../labs/ospf-baseline/agent-runs/v05-targeted-20261014T113312Z-a98f2b1e/case-04/agent-trace.json) 与 [总记录](../labs/ospf-baseline/agent-runs/v05-targeted-20261014T113312Z-a98f2b1e/acceptance.json)。
- 三种 r2 受控故障曾在**较早版本**的冻结观测回放中通过，见 [历史 Stage 5 报告](../labs/ospf-baseline/agent-runs/stage5-acceptance-20261013T162832Z-db38a0f7/acceptance.md)。它不能填补 v0.5 动态重采的未执行项，也不是通用准确率。

v0.5 定向验收未注入故障；前后只读检查为 `HEALTHY_UNCHANGED`：两端 Area `0.0.0.0`、邻居 FULL、远端路由和双向互联/Loopback ping 正常，运行及启动配置未变。这不是“故障恢复成功”。模型和预算见 [agent-settings.json](../agent-settings.json)：`deepseek-flash`，最多 8 轮/12 次工具调用、输出 700 tokens、请求 30 秒、单命令 12 秒、总计 180 秒、最多 2 次有限重试；密钥不应出现在演示材料中。

## 能力边界

只覆盖当前双路由器、单链路、OSPFv2 与三种受控根因；没有生产设备、多故障叠加、链路以外的网络定位或统计意义上的通用准确率验证。模型可以不选择重采，预算也可能耗尽；保护层只防止超证据结论，不能替代缺失观测。建议不执行配置变更；真实故障实验和恢复须单独、有界地运行。历史 FAIL 和冻结清单继续按原版本保留，不被后续通过记录改写。
