# NetSecOps Agent v0.4 离线源码审查指南

先阅读 `REVIEW_REPAIR_v0.4.md`、`review-results/v0.4-red-20261014/result.md` 和 `labs/ospf-baseline/reliability-v0.4.json`，再运行 `python3 run_clean_offline_tests_v04.py`。它从白名单复制源码、测试与 `tests/fixtures/` 到临时目录，不复制 `.env`、历史 `agent-runs/`、`experiments/`、Containerlab 生成目录或 `.git`；不调用模型或 Docker。Linux SIGTERM 回归只作用于测试自己启动的 mock 子进程。

## 代码路径

- `labs/ospf-baseline/topology.clab.yml`、`configs/r1/frr.conf`、`configs/r2/frr.conf`：双路由器固定拓扑与源配置。
- `collect_evidence.py`：固定命令、原始输出和命令/地址/接口一致性校验。`agent_tools.py`：限定参数的只读现场/单份快照回放工具，按工具调用产生 evidence_id。
- `agent_loop.py`：单 Agent 的 DeepSeek Chat Completions 工具调用循环、轮数/次数/总时限、原始模型结果和保护层结果。`evidence_checks.py`：只从本次证据中验证正常网络必需事实，并区分有效执行失败、缺失/矛盾记录及后来成功重采。`judge_agent_trace.py`：独立校验实际 API `tool_calls`、工具结果、原始/最终引用与根因/修复建议；不读取预期答案作为模型输入。
- `diagnose_offline.py`：独立规则基线；`run_fault_scenario.py`：已有现场注入与恢复。两者不进入 Agent 提示词。本次 v0.4 修复**没有**运行现场注入。
- `run_stage5_r2.py`、`run_stage5_acceptance.py`：后续现场/真实模型验收入口，现核对 v0.4 清单；本包只验证其离线代码，不包含新的 v0.4 真实运行结果。

## 模型能看到什么

初始输入只有用户的中性任务和必要拓扑；模型每轮只能获得它通过 API `tool_calls` 实际选择的只读工具结果。它不会收到故障标签、注入记录、规则基线或独立判定。密钥仅由项目本机环境或 `.env` 加载，不写入提示词、轨迹或本包。配置示例 `.env.example` 只含空占位；调用限制仍由 `agent-settings.json` 设置（8 轮、12 次工具、每响应 700 tokens、请求 30 秒、命令 12 秒、总计 180 秒、最多 2 次重试）。

保护层可以从**本轮实际返回并严格验证**的必要证据补入最终引用；`model_raw_final` 保持模型原值。每个补入项的 ID、工具、参数和原因在 `evidence_additions` 中；不能凭模型文字猜 ID、不能写死 E001/E003，也不合并所有证据。关键命令失败基于真实固定命令执行记录判断；后续有效成功重采取代“当前失败”，但旧失败保留在 trace。

## 可复核样本与范围

`tests/fixtures/v04-failures/` 中的两份完整 trace 是 v0.3 原始 FAIL 的不变副本，含 API 工具调用、返回的证据、模型原始/保护后结论和旧评判；相邻 README 记录原路径与 SHA256。审查者可从 trace 的每个最终 `evidence_id` 定位至同文件的 `evidence`，再定位 `rounds[*].tool_results[*]` 与 API `model_message.tool_calls`。原始 FAIL 不被“修复后离线重算”改写。

新反例测试验证：有效但漏引的拓扑或失败命令可逐项补证；必要证据未采集、拓扑/地址矛盾、合成的缺失命令记录不会被补成事实；失败后成功重采不再作为当前失败。独立判定条件未放宽。v0.4 的完整离线结果见清单引用的 `review-results/v0.4-offline-*/result.json`；v0.3 的真实验收保持 5/7 PASS、最终网络当时 RESTORED。两者版本和验收范围不同，不应合并为通用准确率。
