# NetSecOps Agent v0.3 源码审查指南

本包是白名单离线审查副本，不是运行中的网络实验。请先读 `REVIEW_REPAIR_v0.3.md` 和 `labs/ospf-baseline/reliability-v0.3.json`，再看 `labs/ospf-baseline/tests/test_review_regressions_v02.py` 的三个新增反例。旧 `stage5-freeze.json`、`reliability-v0.2.json` 及相应历史索引/测试结果只用于版本关系核对；它们的 PASS 不能代替 v0.3 真实模型或现场验收。

## 代码与入口

- 固定拓扑、FRR 源配置：`labs/ospf-baseline/topology.clab.yml`、`configs/r1/frr.conf`、`configs/r2/frr.conf`。
- `collect_evidence.py` 定义只读命令白名单、地址发现、原始观测身份核对；`agent_tools.py` 把实时或单份回放观测封装成参数受限工具。不可达 ping 可以是有效症状，但不能单独证明故障根因。
- `agent_loop.py` 只依据模型实际选择的工具结果，积累 evidence_id，并从经验证的两端 Area 或管理关闭/passive 直接证据生成权威结论与修复建议。`deepseek_client.py` 是 DeepSeek Chat Completions 工具调用适配器；本包无可用密钥。
- `diagnose_offline.py` 是独立规则基线，不进入 Agent 提示词；`judge_agent_trace.py` 在 Agent 完成后独立评判工具调用、原始证据、节点/地址/接口及权威修复文本。`run_fault_scenario.py` 是现场故障入口，此次未运行。
- `run_clean_offline_tests_v03.py` 是本次唯一需要执行的验证入口。它复制白名单源码、测试和 `tests/fixtures/` 到临时目录运行 `unittest`，不复制 `.env`、历史实验、容器生成目录或 `.git`。

## 模型输入与答案隔离

模型初始只收到任务、必要拓扑和固定只读工具定义；每轮后只收到它通过 API `tool_calls` 指定的工具对应结果。不能读取实验注入记录、预期根因、规则答案或独立评判。保护层可将模型输出降级，并分别保存模型原始诊断与最终诊断；它不执行改配。

确认 Area 不一致须有两端互联接口的有效 Area，且 OSPF 输出的 `Internet Address`/掩码与同次运行配置一致。管理关闭/passive 须有对应故障接口的直接状态/配置。独立评判从已引用拓扑工具的原始配置重建地址，不能只相信工具的摘要字段；管理关闭/passive 的权威修复文本必须完整匹配证据导出的节点、接口和动作，出现另一节点的配置动作即 FAIL。邻居、路由、ping 异常只是症状；命令失败与数据矛盾不能当作网络故障事实。

## 可复核的离线结果

Ubuntu/WSL2 Python 3.12.3 的完整干净副本记录在 `review-results/v0.3-offline-20261014T031239Z/result.json` 和 `unittest-output.txt`：57 项、0 跳过、PASS。`tests/test_review_regressions_v02.py` 是附件三个反例的项目内独立实现，不依赖附件脚本。`tests/fixtures/` 是必要历史样本的固定副本，不是 v0.3 新现场观测。审查者可在包根目录运行 `python3 run_clean_offline_tests_v03.py` 复核；会生成一份新的离线结果，不会调用模型 API 或真实实验。

当前调用限制仍在 `agent-settings.json`（8 轮、12 次工具、单响应 700 tokens、请求 30 秒、单命令 12 秒、总计 180 秒、最多 2 次重试）。本包不含真实 `.env`、密钥、认证请求头、完整环境变量、虚拟环境、`.git`、历史实验目录、Containerlab 生成目录或镜像。v0.3 的真实 DeepSeek 与现场故障验收尚未执行；现有实验的最终网络状态未在此次离线工作中重新检查。
