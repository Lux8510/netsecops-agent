# v0.6 演示案例

`index.json` 与 `cases/*.json` 是五个**已保存运行的选择性导出副本**，不是新模型调用，也不是合成现场数据。导出脚本为 [`tools/export_demo_examples.py`](../tools/export_demo_examples.py)。每份案例记录原始来源标签和源文件 SHA-256；本地历史文件仍在被 Git 忽略的 `agent-runs/` 下，未被修改或整体发布。

| 案例 | 数据性质 | 原始运行模式 | 结论边界 |
|---|---|---|---|
| normal | v0.5 正常现场只读轨迹 | live | 当次判定 `normal`；不是现在的实时网络状态 |
| area-mismatch | Stage 5 冻结观测的模型回放 | replay | 两端 Area 不同；无设计依据时不判哪端配置错误 |
| admin-down | Stage 5 冻结观测的模型回放 | replay | r2 互联接口管理关闭的直接证据 |
| collection-failure | v0.5 冻结观测的模型回放 | replay | 模型原始误判，保护后为 `collection_failure` |
| missing-area | v0.5 冻结观测的模型回放 | replay | 模型原始误判，保护后为 `insufficient_evidence` |

网页只是逐步展示保存的 `rounds[*].tool_results`、`evidence`、模型原始结果和保护后结果。不会重新调用模型、执行命令或按案例名生成观测。`E001` 等 ID 仅在所属案例内有效。导出时去除了 API 请求 ID 和本机路径，保留实际工具参数、原始命令输出、采集状态和校验结果。`judge.verdict` 是当时独立验收的保存结果，不是这个网页重新评判出的新结论。

离线回归样本另见 [`tests/fixtures/README.md`](../labs/ospf-baseline/tests/fixtures/README.md)。其中 v0.3 的原始 `normal-acceptance.json` 含个人工作站路径，继续留在本地忽略目录；可提交的 `normal-acceptance-public.json` 只保留回归测试所需的原判定摘要，不能冒充完整历史记录。
