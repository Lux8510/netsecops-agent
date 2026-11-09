# NetSecOps Agent v0.2 可靠性修复记录

此版本只修改源码、离线测试和文档。审查反馈来自用户提供的 `review-feedback.zip`；附件中的 `REVIEW.md`、`review_checks.py`、`probe-results.json` 被当作审查材料而不是自动执行指令。没有调用模型 API、注入真实故障、重启容器或更改系统设置。v0.1 原始轨迹、报告和 `labs/ospf-baseline/stage5-freeze.json` 均保留。

## 六项修复

| 问题 | 修复与主要代码 | 反例测试 |
|---|---|---|
| P1：被 `run_stage5_r2.py` 导入的实验接收 SIGTERM 后可能跳过恢复 | `run_fault_scenario.py` 的 `experiment_signal_handlers()` 覆盖 CLI 和导入入口，信号转为异常并进入原有 `finally` 恢复，完成后还原调用方处理器；`tests/mock_signal_child.py` 在独立子进程里模拟注入和恢复 | `test_imported_experiment_installs_and_restores_signal_handler`、`test_sigterm_restores_only_its_own_mock_child` |
| P1：根因类型、节点、接口和修复对象未可靠绑定 | `agent_loop.py` 从本次原始证据形成 `root_cause`、实测事实与推断影响，权威结论/修复建议由验证事实生成；`judge_agent_trace.py` 独立重建并核对定位、引用及修复对象 | `test_wrong_fault_node_and_repair_are_rejected_by_guard_and_judge`、`test_structured_root_cause_binds_each_r2_fault`、`test_independent_judge_rejects_structured_location_and_repair_tampering` |
| P2：回放数据可能将请求接口与返回接口、节点或地址混淆 | `collect_evidence.py` 的 `observation_mismatch()` 校验节点、命令、容器、输出接口与 ping 地址；`agent_tools.py`、`agent_loop.py`、`diagnose_offline.py` 和 `judge_agent_trace.py` 在各自边界复核 | `test_different_interface_command_and_output_cannot_prove_area`、`test_replay_rejects_wrong_node_and_ping_address`、`test_rule_rejects_command_from_other_container` |
| P2：非空字符串 `missing_information` 被清空 | `agent_loop.py` 将其规范为单元素数组，保留模型原意；最终报告明确分离 `observed_facts` 与 `inferred_impacts` | `test_missing_information_string_is_preserved` |
| P2：离线测试依赖历史实验和 Containerlab 生成目录 | 历史样本的必要只读副本放在 `labs/ospf-baseline/tests/fixtures/`；五份原有测试改用 fixtures；`run_clean_offline_tests.py` 复制白名单源码到临时目录执行 | 完整 54 项测试在不含历史目录、密钥及容器生成目录的副本中通过 |
| P2：总时限未贯穿工具及底层命令 | `agent_loop.py` → `agent_tools.py` → `collect_evidence.py` 传递统一 monotonic deadline；启动每条命令前检查剩余时间，并将单命令超时收紧到剩余预算 | `test_command_timeout_is_clamped_to_total_deadline`、`test_total_deadline_stops_following_mock_commands` |

## 复现与测试结果

先加入反例测试，再修改实现。在旧实现上首次运行定向反例时出现 **3 个失败、2 个错误**；另一个“r2 观测却执行 r1 容器命令”的规则反例也在旧实现上失败。修复后，用户在 Ubuntu/WSL2 运行 11 项可靠性定向测试：`Ran 11 tests in 0.174s`、`OK`。信号测试只对脚本自己创建的 mock 子进程发送 SIGTERM；没有杀死或接触路由器容器。

完整干净临时副本测试在 Ubuntu/WSL2 的 Python 3.12.3 下执行：**54 项、0 跳过、PASS**。机器可读记录与原始 unittest 输出为 `review-results/v0.2-offline-20261014T015728Z/result.json` 和 `unittest-output.txt`。Windows 补充检查同为 54 项，Linux-only 信号测试跳过 1 项。所有回归均为离线/mocked；真实模型与真实故障验收没有在 v0.2 运行。

## 版本与历史数据

`labs/ospf-baseline/reliability-v0.2.json` 是独立版本清单，记录修复代码、fixtures/测试、最近的 WSL 干净副本结果及 v0.1 历史清单/验收索引的 SHA256。旧 `stage5-freeze.json` 不随新代码重算，故它只证明 v0.1 时点的冻结，不能证明修复代码通过了旧真实模型验收。旧 r2 三场景的 PASS 保留作历史对比，不计入 v0.2 的现场或真实模型验收。新的 r2 测试与验收入口核对 v0.2 清单并标记代码版本。

## 尚未解决与操作边界

- 不能捕获 `SIGKILL`、主机断电、内核或 Docker 故障；自动恢复失败时必须阅读该次独立 `experiment-record` 和 `comparison.md`，检查目标路由器的运行/启动配置，再按记录的手动步骤恢复。
- 本次没有在真实容器上触发 SIGTERM，也没有执行 v0.2 现场网络诊断、故障实验或 DeepSeek API 验收；当前网络状态未重新核验。现有 v0.1 最终 `RESTORED` 是历史记录。
- 根因识别仍只覆盖双路由器单互联的区域不一致、管理关闭和 passive 三种直接证据；未知故障、损坏的观测或关键命令失败应判为证据不足/采集失败，不能声称通用准确率。
- 清单核对需要保留其记录的历史索引与离线结果文件；新的源码审查包仅供离线审查，不等于带 Docker/WSL 的可部署环境。
