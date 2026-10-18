# 三分钟演示脚本：用保存记录展示证据边界

全程只打开项目内已保存文件；不执行诊断命令、不调用模型 API、不触碰容器。`E00x` 仅在所属轨迹内有效，不能跨案例互引。先说明“现场”是该次运行时的真实只读网络观测，“回放”是模型逐项查询冻结观测，“离线测试”是不用真实模型的代码回归。

| 时间 | 屏幕上打开的记录 | 建议讲述 |
|---|---|---|
| 0:00–0:25 | [拓扑](../labs/ospf-baseline/topology.clab.yml) | “这是 r1、r2 两台 FRR 路由器的 OSPF 小实验。目标不是让模型自由执行命令，而是让它在限定工具内选择检查，再由证据规则约束结论。” |
| 0:25–1:05 | v0.5 正常**现场** [轨迹](../labs/ospf-baseline/agent-runs/v05-targeted-20261014T113312Z-a98f2b1e/case-01/agent-trace.json)、[独立评判](../labs/ospf-baseline/agent-runs/v05-targeted-20261014T113312Z-a98f2b1e/case-01/judge.json) | “模型实际选择了 9 次只读工具，引用 `E001` 拓扑、`E002/E003` 两端 Area、`E004/E005` 邻居、`E006/E007` 路由、`E008/E009` 双向 Loopback ping。原始和保护后均为 `normal`，独立判定 PASS；模型已经引全，未发生补引。” |
| 1:05–1:45 | 较早版本 Stage 5 的 r2 区域不一致**冻结观测回放**：[轨迹](../labs/ospf-baseline/agent-runs/stage5-acceptance-20261013T162832Z-db38a0f7/case-01/agent-trace.json)、[规则结果](../labs/ospf-baseline/agent-runs/stage5-acceptance-20261013T162832Z-db38a0f7/case-01/rule-diagnosis.json)、[评判](../labs/ospf-baseline/agent-runs/stage5-acceptance-20261013T162832Z-db38a0f7/case-01/judge.json) | “这里不是当下现场。回放的 r1 `eth1` 原始输出 `E002` 为 Area `0.0.0.0`，r2 `eth1` 的 `E003` 为 `0.0.0.1`。模型和独立规则均判区域不一致，评判 PASS；邻居/路由/ping 的异常是影响，不是 Area 根因的单独证明。” |
| 1:45–2:25 | v0.5 两种保守处理**回放**：[缺一端 Area 轨迹](../labs/ospf-baseline/agent-runs/v05-targeted-20261014T113312Z-a98f2b1e/case-03/agent-trace.json)、[关键命令失败轨迹](../labs/ospf-baseline/agent-runs/v05-targeted-20261014T113312Z-a98f2b1e/case-02/agent-trace.json) | “这两例模型原始结果都误判区域不一致。缺少一端有效 Area 时，保护后为 `insufficient_evidence`；关键 OSPF 查询真实失败时，保护后为 `collection_failure`，补引的是本轮失败记录 `E003`。两例独立评判 PASS，但通过的是拒绝武断结论，不是定位到某个网络故障。” |
| 2:25–3:00 | v0.5 [验收总表](../labs/ospf-baseline/agent-runs/v05-targeted-20261014T113312Z-a98f2b1e/acceptance.md)、[动态例轨迹](../labs/ospf-baseline/agent-runs/v05-targeted-20261014T113312Z-a98f2b1e/case-04/agent-trace.json)、[离线测试结果](../review-results/v0.5-offline-20261014T105947Z/result.json) | “v0.5 前三例 PASS；动态例没有按同查询重采，标为 `NOT_EXERCISED`，总状态 `INCOMPLETE`。72 项离线测试通过不能替代这条真实模型路径。整次未注入新故障，最终网络 `HEALTHY_UNCHANGED`，不能说‘故障恢复成功’或宣称通用准确率。” |

演示前可把每个 JSON 定位到 `model_raw_final`、`final`、`evidence`、`rounds[*].model_message.tool_calls`、`evidence_additions` 与 `guardrail_changes`；评判文件定位到 `verdict` 和 `problems`。不要展示本地 `.env` 或认证信息。若现场实验此后状态变化，本脚本仍只陈述这些已保存记录的当时结果。
