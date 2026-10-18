# NetSecOps Agent v0.2 源码审查指南

`review-bundle-v0.2.zip` 是本项目的**白名单源码审查副本**，不是已部署网络的快照。所有路径均相对于压缩包根目录。它只含修复后的源码、测试与固定 fixtures、拓扑/启动源配置、配置示例、离线测试结果、v0.2 版本清单和变更说明。真实 `.env`、密钥、认证请求头、完整环境变量、虚拟环境、`.git`、历史实验目录、Containerlab 生成目录及容器镜像均不包含。

## 建议审查顺序

1. 阅读 `RELIABILITY_CHANGELOG.md` 的六项改动与边界，再看 `labs/ospf-baseline/reliability-v0.2.json` 的源码/测试哈希、WSL 离线测试结果引用和历史 v0.1 哈希。
2. 查看 `labs/ospf-baseline/tests/test_reliability.py` 的反例；其中 `mock_signal_child.py` 是唯一接收 SIGTERM 的子进程，由测试自己的 `Popen` 创建，绝不访问 Docker。
3. 运行 `python3 run_clean_offline_tests.py`。它创建临时白名单副本，排除密钥和历史/生成目录；已有 WSL 记录见 `review-results/v0.2-offline-20261014T015728Z/{result.json,unittest-output.txt}`，为 54 项、0 跳过。压缩包自身不包含真实实验状态，故不运行故障注入或模型验收。

## 入口与模块关系

- `labs/ospf-baseline/topology.clab.yml`、`configs/r1/frr.conf`、`configs/r2/frr.conf` 定义现有双路由器实验。`README.md` 记录项目原有部署/验收入口及 v0.2 离线验证；`agent-settings.json` 保留模型名称、调用/超时限制；`.env.example` 只有空占位。
- `collect_evidence.py` 固定可执行采集命令及观测身份校验。`agent_tools.py` 将它封装为有参数限制的只读实时/回放工具；`agent_loop.py` 驱动模型的 `tool_calls`，积累 evidence_id，并从已验证的直接证据生成最终结论。`deepseek_client.py` 负责 DeepSeek Chat Completions 流程，`agent_config.py` 负责本地配置读取；本包无真实密钥。
- `diagnose_offline.py` 是独立规则基线，模型不可读取。`judge_agent_trace.py` 在 Agent 完成后独立核对工具轨迹、引用、结构化根因、节点/接口/修复对象。`run_fault_scenario.py` 是有界故障入口，`run_stage5_r2.py` 调用它；v0.2 对导入入口也安装和恢复 SIGINT/SIGTERM 处理器。`reliability_freeze.py` 核对新版本清单，不重写旧 `stage5-freeze.json`。
- `tests/fixtures/observations/` 与 `tests/fixtures/traces/` 是从历史记录挑选的固定**测试样本**，不是 v0.2 新现场验收。它们保留命令、节点、输出和模型/工具轨迹，以便离线反例检查；测试不依赖原历史目录。

## 模型输入、直接证据与隔离

Agent 初始只收到统一任务、必要拓扑和受限只读工具定义；下一步只收到它通过 API `tool_calls` 所选工具返回的单次观测。不能列举整套历史实验、注入记录、预期根因、规则答案或独立评判结果。工具层根据观测里的命令容器、节点、接口及 ping 目标校验其身份；不一致作为采集数据不可用处理，不能用于根因确认。

最终 `root_cause` 绑定 `fault_type`、`node`、`interface`、`evidence_ids`。区域不一致的直接事实是两端互联接口 Area 不同；因此只列两端 `endpoints`，不凭差异本身指定哪一端配置错误。管理关闭/passive 必须引用故障接口的有效状态或运行配置。`observed_facts` 只列实测，`inferred_impacts` 标明推断影响；邻居、路由或 ping 症状本身不能单独证明根因。保护层会规范化非空字符串 `missing_information` 为一元素数组，拒绝/替换与证据不符的故障位置、结论和改配对象，并在 `guardrail_changes` 记录变化；不执行修复。独立评判器自己从原始证据重新验证这些事实，而不是接受保护层给出的标签。

## 测试与版本关系

v0.2 WSL 干净副本输出在 `review-results/v0.2-offline-20261014T015728Z/`，`result.json` 有运行平台/Python/测试数/跳过数，`unittest-output.txt` 有每项原始结果。它只证实离线测试通过。早期 v0.1 真实 DeepSeek r2 三场景 PASS 的原始记录仍在用户项目目录，不在本审查包中；包内保留旧 `stage5-freeze.json` 和验收索引作为哈希关系参考。它们不构成 v0.2 的模型或现场验收。新代码的 v0.2 清单单独冻结，旧冻结不重算。

当前上限仍由 `agent-settings.json` 设定（8 轮、12 次工具、单响应 700 tokens、请求 30 秒、单命令 12 秒、诊断总计 180 秒、最多 2 次重试）。诊断总时限现在传入每个工具和底层命令，预算用尽时不再启动下一条命令。未覆盖的情况包括 `SIGKILL`/断电、其他拓扑/故障、v0.2 真实 API 与现场故障验收。历史网络恢复记录不等于本次重新检测网络状态。
