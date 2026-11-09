# NetSecOps Agent v0.1 源码审查指南

本包是项目目录的**白名单审查副本**，不是完整运行环境。路径从压缩包根目录算起。未附带真实 `.env`、API Key、认证请求头、进程环境变量、虚拟环境、`.git`、Containerlab 生成目录或容器镜像；原始实验记录未被编辑。本次打包不调用模型，也不执行故障实验。

## 入口与模块关系

- `labs/ospf-baseline/topology.clab.yml` 和 `labs/ospf-baseline/configs/{r1,r2}/frr.conf` 是两路由器拓扑及源配置；`README.md` 是完整运行说明。实验在 Ubuntu/WSL2 中运行，Python 代码仅使用标准库；本项目没有外部 Python 依赖清单。`agent-settings.json` 是模型名称和调用限制声明，`.env.example` 只有空占位。
- `collect_evidence.py` 通过固定命令采集原始输出及 JSON；`agent_tools.py` 将实时采集或单份离线观测封装成有参数校验的只读工具。
- `agent_cli.py` 是单 Agent 命令行入口；`agent_config.py` 只从进程环境或项目本地 `.env` 读取密钥；`deepseek_client.py` 实现 DeepSeek Chat Completions 工具调用；`agent_loop.py` 驱动模型选择工具、记录证据并校验最终引用。
- `diagnose_offline.py` 是独立规则基线，不参与 Agent 提示或工具返回。`run_fault_scenario.py` 负责有界注入、恢复与观测；`run_stage5_r2.py` 生成 r2 独立场景；`run_stage5_acceptance.py` 在冻结观测上运行规则和 Agent；`judge_agent_trace.py` 独立评判。`tests/` 中为模拟与单元测试。

只读审查可运行 `python3 -m unittest discover -s labs/ospf-baseline/tests`。真实运行入口、WSL/Docker/Containerlab 前置条件和参数见 `README.md`；不要将本包直接当作已部署的实验。单次离线回放入口示例（真实模型调用，须由审查者自行配置密钥）：

```sh
python3 labs/ospf-baseline/agent_cli.py --mode replay --observations labs/ospf-baseline/agent-runs/stage5-acceptance-20261013T162832Z-db38a0f7/case-01/observations.json --task "检查双路由器 OSPF 与 Loopback 连通性"
```

## 模型输入与答案隔离

初始输入只有任务、必要拓扑和工具定义。模型通过 API 返回的 `tool_calls` 选择工具和参数，后续仅收到该次工具的观测及错误；回放工具不能列出整份实验记录。工具没有任意 shell、配置修改或故障注入能力。注入记录、场景标签、预期根因、规则诊断与 `judge.json` 都不传给 Agent；它们只在独立验收进程中使用。三组 r2 案例的共同任务见 `acceptance.json` 的 `task`。

## 第五阶段 r2 冻结案例与证据定位

统一索引：`labs/ospf-baseline/agent-runs/stage5-acceptance-20261013T162832Z-db38a0f7/acceptance.json` 和 `acceptance.md`。三例同一冻结版本、同一任务：

| 案例 | 观测 | 模型/证据 | 规则 | 独立评判 |
| --- | --- | --- | --- | --- |
| `case-01` 区域不一致 | `case-01/observations.json` | `case-01/agent-trace.json`, `agent-report.zh-CN.md` | `case-01/rule-diagnosis.json`, `rule-report.zh-CN.md` | `case-01/judge.json` |
| `case-02` 管理关闭 | `case-02/observations.json` | `case-02/agent-trace.json`, `agent-report.zh-CN.md` | `case-02/rule-diagnosis.json`, `rule-report.zh-CN.md` | `case-02/judge.json` |
| `case-03` OSPF passive | `case-03/observations.json` | `case-03/agent-trace.json`, `agent-report.zh-CN.md` | `case-03/rule-diagnosis.json`, `rule-report.zh-CN.md` | `case-03/judge.json` |

上述表内文件均以统一索引所在目录为基准。每例 `agent-trace.json` 的 `rounds[].model_message` 是模型 API 原始消息，含实际返回的工具名称/参数；`rounds[].tool_results` 与 `evidence.E###` 是工具返回及原始命令输出；`model_raw_final` 是模型原始诊断；`final` 是保护层处理后的诊断；`guardrail_changes` 记录修改字段及原因；`usage_per_request`/`usage_total` 和 `elapsed_seconds` 记录消耗。`agent-report.zh-CN.md` 是可读调用轨迹。`judge.json` 校验模型调用与执行一致、引用 ID 存在且直接证据支持根因。

每例 `observations.json` 同目录还附有其 `stdout_file`、`stderr_file` 指向的原始文本副本；JSON 自身也保留 `stdout`、`stderr`。对应原始冻结快照另位于 `labs/ospf-baseline/experiments/{area_mismatch-r2-20261013T161857Z-595dc1c7,admin_down-r2-20261013T162006Z-431a42ae,ospf_passive-r2-20261013T162031Z-8fb41311}/observations/fault/snapshot/`。包中路径已改为相对压缩包根目录的 `./...`；观测内容和原始实验文件不变。注入记录不在模型输入中，也不包含在本包。

保护层只校验本次 `evidence_id`、工具错误状态和特定结论所需直接证据：区域不一致要两端互联 Area；管理关闭要接口管理状态；passive 要接口有效运行配置。证据不足时可将模型结论降为 `insufficient_evidence` 或 `collection_failed`；还会规范化报告字段。它不执行修复，也不能补造证据。审查原始与最终结论时请同时看 `model_raw_final`、`final` 和 `guardrail_changes`，不要只看验收 PASS。

## 补充案例

- 正常现场：`labs/ospf-baseline/agent-runs/live-normal-20261013T143231Z-343f3366/`，含 `trace.json`、`report.zh-CN.md`、`judge.json`。现场工具的原始输出在 `trace.json` 的 `evidence` 中。
- 采集失败：`labs/ospf-baseline/agent-runs/failed-command-20261013T142100Z-e4241eeb/`，含相同三类文件；对应回放输入为 `labs/ospf-baseline/diagnostic-acceptance/acceptance-20261013T081618Z-c77eb02e/failed-key-command/input-observations.json`。关键命令失败不得被解读为网络故障。
- 早期调用上限 incomplete：`labs/ospf-baseline/agent-runs/normal-20261013T085549Z-01fa065a/`，含 `trace.json` 与报告；其正常输入为 `labs/ospf-baseline/experiments/area-mismatch-20261013T081041Z-3fbfc8c4/observations/normal/observations.json`，同目录附原始输出。此案例保留失败现场，不按当前提示词重新评判。

证据 ID 仅在各自 trace 内有意义，不跨案例复用。`trace.json#/evidence/E001` 这类 JSON 路径是定位方式；先在同一 trace 的 `final.evidence_ids` 找到 ID，再核对 `evidence` 下的节点、命令、退出码和原始输出。观测 JSON 的文件名引用相对于该观测文件所在目录。

## 限制与审查边界

`agent-settings.json` 当前声明 `deepseek-flash`、最多 8 轮/12 次工具、每次最多 700 输出 token、请求 30 秒、命令 12 秒、全程 180 秒、最多 2 次重试。达到上限属于 `incomplete`，不能计作诊断成功。第五阶段三例 PASS 只说明这个双路由器实验中的受控故障验收，不代表一般网络环境准确率。Agent 仅支持固定拓扑、固定只读命令及三类直接故障证据；未实现任意拓扑发现、自动修复或大规模泛化评测。

若要复现实验，请使用原项目工作区和自己的本地密钥配置；`.env.example` 的 `DEEPSEEK_API_KEY=` 是空占位，真实 `.env` 不在压缩包中。本包不包含容器镜像或运行中的网络状态。
