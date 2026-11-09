# v0.4 证据归属与采集失败离线修复

本修复针对 2026-10-14 v0.3 有界真实验收的两个 **FAIL**，不重写原始模型响应、工具轨迹、评判或 v0.3 冻结清单。原结果仍是 **5/7 PASS，最终网络 RESTORED**，见 [V03_REAL_ACCEPTANCE_20261014.md](V03_REAL_ACCEPTANCE_20261014.md)。v0.4 尚未调用 DeepSeek 或运行真实故障，不能把离线回归转称真实验收通过。

## 复现与样本

两份原始 FAIL trace、正常现场验收 JSON、关键失败案例的独立判定 JSON 已原样复制到 `labs/ospf-baseline/tests/fixtures/v04-failures/`。该目录的 README 记录来源与 SHA256；测试不读取历史 `agent-runs/`。原始历史文件与旧审查包保留。

先用 v0.3 冻结版本的 `agent_loop.py`、`judge_agent_trace.py` 运行新增 7 项测试，得到 **6 FAIL、1 PASS**：正常漏拓扑引用仍被保护层称为 `normal`，真实命令失败漏引时仍称证据不足，未取得拓扑仍可称正常，缺失记录被误当执行失败，成功重采未取代旧失败，独立评判也会放过被取代的旧失败；数据矛盾边界原本正确。旧实现的运行输出与哈希见 `review-results/v0.4-red-20261014/result.md`。

## 修复边界

| 问题 | v0.4 处理 | 代码位置 |
|---|---|---|
| 正常结论缺必要引用 | 从**本次工具返回**中选最新且有效的拓扑、两端 Area、对端邻居 FULL、远端 OSPF /32 路由、双向远端 Loopback ping；任何一项无法验证则降为 `insufficient_evidence`。仅为已验证的缺失项补引用 | `evidence_checks.py:validated_topology()` / `normal_proofs()`；`agent_loop.py:validate_final()` |
| 模型漏引真实关键命令失败 | 从实际工具执行记录核对节点、接口、固定命令、退出码/错误以及 `validation_error`；仍未被成功重采取代的失败可补入引用并归类 `collection_failure`。错误命令、缺失记录、数据矛盾不算真实命令失败 | `evidence_checks.py:critical_attempt_kind()` / `critical_history()`；`agent_loop.py:validate_final()` |
| 成功重采与失败历史 | 同节点、接口、工具的后续有效成功记录取代当前失败状态；旧失败留在证据历史，不删除。若模型仍声称采集失败，最终结论降级并引用成功重采，说明新旧关系 | `evidence_checks.py:critical_history()`；`agent_loop.py:validate_final()`；`judge_agent_trace.py:verdict()` |
| 补引的可审查性 | `model_raw_final.evidence_ids` 不变；最终结果及 trace 的 `evidence_additions` 逐项记录 `evidence_id`、必要条件、工具、节点、参数、原因。独立评判比对原始与最终引用、API 工具调用及原始观测 | `agent_loop.py:run_agent()` / `render_trace()`；`judge_agent_trace.py:verdict()` |

独立评判的正常网络必要条件未放宽：它仍从**已引用的**拓扑原始配置核对地址，核对两端 Area、邻居、路由和双向 ping；v0.4 还核查引用来自最近有效观测。采集失败判定收紧为当前真实命令失败，若已有有效成功重采则拒绝旧失败。评判器没有为两个旧 FAIL 添加例外；只有重新处理离线样本时，经验证的引用被程序补齐才可通过。原始 v0.3 trace 与原判定继续保持 FAIL。

## 离线验证与版本关系

- 新增 `tests/test_v04_evidence_reconciliation.py` 的 9 项反例，包含两份真实失败轨迹、未采集必要证据、地址矛盾、缺失记录、数据矛盾和成功重采。一个旧模拟测试原只修改工具状态而未模拟任何命令错误，v0.4 将它改为同时提供固定命令的非零退出与错误；验证目标没有弱化。
- Windows 本机完整测试为 **66 项通过、1 项 Linux 信号测试按平台跳过**；干净临时副本同为 **66 项通过、跳过 1 项**。Ubuntu/WSL2 Python 3.12.3 的干净临时副本实际运行 **66 项通过、0 跳过**，见 `review-results/v0.4-offline-20261014T093031Z/{result.json,unittest-output.txt}`。该副本有 60 个白名单源码/样本文件，不含密钥、历史实验及容器生成目录。
- 旧 v0.1/v0.2/v0.3 清单与报告不变；新清单是 `labs/ospf-baseline/reliability-v0.4.json`，绑定当前源码/测试、v0.3 失败原始记录及新 WSL 离线结果。新审查包为 `review-bundle-v0.4.zip`，按白名单收集源码、样本、测试和说明，排除真实 `.env` 与运行环境。
- 本轮不调用模型 API、不注入故障，也不声明现场网络在 v0.4 之后经过重新核对。v0.3 验收末尾的 `RESTORED` 属于当时记录，不能自动转为 v0.4 现场验收。

遗留边界：诊断仍只覆盖当前双 FRR 路由器拓扑与三种受控根因。`SIGKILL`、宿主机断电无法由 Python `finally` 保证恢复。未来 v0.4 真实模型验收应使用新冻结清单、新观测与独立判定；不能复写或重新标记 v0.3 的两个 FAIL。
