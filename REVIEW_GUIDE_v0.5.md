# NetSecOps Agent v0.5 源码审查指南

先看 `REVIEW_REPAIR_v0.5.md`、`review-results/v0.5-red-20261014/result.md`、`labs/ospf-baseline/reliability-v0.5.json`，再从项目根目录运行 `python3 run_clean_offline_tests_v05.py`。新测试在 `labs/ospf-baseline/tests/test_review_recheck_v04.py`，附件原测试在 `review-input-v0.4/test_review_recheck_v04.py`。`tests/fixtures/observations/stage5-case-02.json` 的 r2 eth1 输出和 `failed-command.json` 是离线模拟来源，无需历史实验目录。

关键代码：`agent_tools.py` 只执行固定只读工具并保存 evidence_id、命令身份、退出码和原始输出；`evidence_checks.py` 区分有效成功重采、仍活跃的执行失败与不足以证明 Area 的输出；`agent_loop.py` 保护最终结论、记录模型原始引用和补入引用；`judge_agent_trace.py` 独立核对工具调用、证据引用和直接根因。`run_fault_scenario.py` 是另一个已有现场实验入口，本次没有运行。

模型初始仅获得中性任务和必要拓扑，后续只得到它实际选择的只读工具返回；它不读取故障标签、注入记录、规则答案或独立评判。模型限制仍由 `agent-settings.json` 设置：8 轮、12 次工具、每响应 700 tokens、请求 30 秒、命令 12 秒、总计 180 秒、最多 2 次重试。本审查包没有真实 `.env` 或密钥，`.env.example` 仅为空占位。

本包为 **离线修复**。成功查询关闭接口可解除旧采集失败，但只有两端有效 Area 信息才能确认区域关系；管理关闭的确认仍要求有效的故障接口管理状态。v0.1–v0.4 原始记录保留，不能把 v0.5 测试结果回写为旧版本真实验收 PASS。
