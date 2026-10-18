# v0.3 有界真实验收（2026-10-14）

结论：**总状态 FAIL；7 项真实模型诊断中 5 项 PASS、2 项 FAIL；最终网络 RESTORED。**
这是一次受控验收，不代表通用诊断准确率。原始失败记录未被覆盖，未调整模型/工具调用上限、提示词、保护层、独立判定条件或 v0.3 冻结清单以追求通过。没有认证、余额或接口错误。

## 版本、输入和隔离

- `reliability-v0.3.json` 校验通过；代码版本 `v0.3-review-repair`。本次额外验收编排器 `labs/ospf-baseline/run_v03_real_acceptance.py` 不在原冻结源码列表内，它及既有三个入口的运行时 SHA256 保存在统一 `acceptance.json` 的 `harness_sha256` 中。
- 模型为 `deepseek-flash`，使用项目现有设置：最多 8 轮、12 次工具调用、每次输出最多 700 tokens、单请求 30 秒、单命令 12 秒、总诊断 180 秒、最多 2 次重试。密钥只从本机环境或项目 `.env` 加载，记录中只有“已配置”，没有密钥。
- 七项均使用同一中性任务：“检查这个双路由器实验的 OSPF 和双向 Loopback 连通性；若异常，仅凭只读观测鉴别原因，证据不足时明确说明。”故障标签与预期结果不进入模型提示词或回放工具。
- 现场初次运行的正常诊断独立判定失败后，编排器停止了后续场景。续跑只复用已恢复的两份现场轨迹，不重复现场区域故障；随后新建 r2 三故障批次并完成五项回放。续跑重新计算现场独立判定，原 FAIL 保持不变。

## 统一结果

| 类别 | 场景 | 模型原始 / 保护后状态 | 独立判定 | 请求 / 工具 | 秒 | 输入 / 输出 tokens |
|---|---|---|---|---:|---:|---:|
| 真实现场 | 正常 | `normal` / `normal` | **FAIL** | 5 / 12 | 11.629 | 27,166 / 939 |
| 真实现场 | r1 区域不一致 | `ospf_area_mismatch` / 同左 | PASS | 3 / 3 | 5.222 | 5,977 / 589 |
| 真实模型＋新观测回放 | r2 区域不一致 | `ospf_area_mismatch` / 同左 | PASS | 4 / 5 | 6.161 | 9,957 / 730 |
| 真实模型＋新观测回放 | r2 管理关闭 | `interface_admin_down` / 同左 | PASS | 5 / 7 | 7.811 | 20,376 / 831 |
| 真实模型＋新观测回放 | r2 OSPF passive | `ospf_passive` / 同左 | PASS | 4 / 6 | 6.436 | 16,012 / 840 |
| 真实模型＋回放 | 缺少一端 Area 信息 | `ospf_area_mismatch` / `insufficient_evidence` | PASS | 5 / 9 | 10.572 | 24,737 / 1,182 |
| 真实模型＋回放 | 关键 OSPF 接口命令失败 | `ospf_area_mismatch` / `insufficient_evidence` | **FAIL** | 6 / 10 | 9.802 | 23,432 / 1,306 |

累计 32 次模型请求、52 次工具调用、57.633 秒诊断耗时、127,657 输入 / 6,417 输出 / 134,074 总 tokens。各项模型原始 JSON、保护后 JSON、保护层差异、证据 ID、API 返回的 `tool_calls`、工具参数及原始输出都保存在对应 `trace.json`；独立判定保存在相应 `judge.json` 或现场 `acceptance.json`。

### 两项失败的具体原因

1. 正常现场：模型查询了拓扑 `E001`、两端 Area `E002/E003`、两端邻居、路由和双向 ping，但最终 `evidence_ids` 漏了 `E001`。独立评判因此无法用**被引用的**运行配置核对目标地址，报告 `E010/E011` 地址不可核、Area 等同性不可核及“未引用拓扑地址”。模型与保护后状态虽同为 `normal`，不能算验收通过。该失败不是现场网络故障，也不是工具上限：模型用满 12 次工具后返回了结论。
2. 关键命令失败回放：`query_ospf_interface(r2, eth1)` 的 `E003` 实际返回 `collection_failed`。模型在文字的 `missing_information` 中提到 E003，却没有将它纳入 `evidence_ids`；同时错误地原始判定 `ospf_area_mismatch`。保护层拒绝未获两端有效 Area 直接证据的结论，降为 `insufficient_evidence`，但由于失败记录未被正式引用，不能把最终结论提升为 `collection_failure`。独立判定因此 FAIL。此处反映证据引用与采集失败分类的能力缺口；不能用邻居或路由症状替代失败命令证据。

两项失败都保留在原始轨迹中；本轮没有重试模型、补写 evidence_id 或放宽独立判定。未来若修复，应另建版本、先加反例回归，再用新的有界验收验证，不得追改本次结果。

## 网络与离线检查

- 初始预检健康：两端 Area `0.0.0.0`、邻居 FULL、远端 OSPF /32 路由存在、互联地址及 Loopback 双向 ping 成功。
- r1 区域实验及 r2 三个独立故障实验均报告 PASS 与恢复。r2 新批次的末尾额外只读网络采集为 RESTORED。
- 续跑结束后的独立复查：两端 Area `0.0.0.0`、邻居 FULL、远端路由存在、互联地址和 Loopback 双向 ping 成功；运行配置与开始前逐行比对一致，源及已部署启动配置的 SHA256 均未改变。最终网络状态 `RESTORED`。
- v0.3 的历史干净副本离线测试为 Ubuntu/WSL2 **57 项通过、0 跳过**。这与本次真实模型 5/7 结果分开报告，不能相互替代。

## 原始记录入口

- 首次只读预检：`labs/ospf-baseline/agent-runs/v03-real-acceptance-20261014T073015Z-04c36a3e/acceptance.json`
- 初次现场验收（保留 FAIL）：`labs/ospf-baseline/agent-runs/v03-real-acceptance-20261014T074425Z-6d9425cf/acceptance.json`
- 现场原始验收和 r1 实验：`labs/ospf-baseline/agent-runs/live-acceptance-20261014T074430Z-ab8485ed/acceptance.json`，`labs/ospf-baseline/experiments/area-mismatch-20261014T074442Z-9f0fc172/`
- 新 r2 三故障批次：`labs/ospf-baseline/stage5-r2-tests/batch-20261014T080028Z-65d46a35/batch.json`
- 三场景真实模型回放：`labs/ospf-baseline/agent-runs/stage5-acceptance-20261014T080323Z-48c7f1e8/acceptance.json`
- 七项汇总、缺失证据与采集失败回放、最终网络复查：`labs/ospf-baseline/agent-runs/v03-real-acceptance-20261014T080022Z-aac7786c/acceptance.json`，同目录 `acceptance.md`、`case-06/`、`case-07/`、`network-after/`。
- v0.3 离线测试：`review-results/v0.3-offline-20261014T031239Z/result.json`。
