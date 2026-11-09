# v1 收敛验证记录（2026-11-09）

范围：仓库结构收敛（归档 v0.1–v0.5 审查材料、统一测试入口、重写 README、修正依赖固定版本）。**未修改**诊断规则、工具白名单、证据校验、判分器与示例轨迹。

## 环境

- macOS（Apple Silicon），Python 3.12.12，Node.js 24.22.2
- 未使用 Docker、Containerlab、DeepSeek 或任何网络请求

## 实际运行

| 命令 | 结果 |
| --- | --- |
| `python -B run_offline_tests.py` | **PASS**：核心 Agent 77 项（1 跳过）+ v1 拓扑 26 项 = 103 项 |
| `python -B tools/check_publication.py` | PASS：26 个可发布数据文件、5 份演示轨迹、文档链接全部有效 |
| `cd web && npm run check` | PASS：类型检查 + 7 项单元测试 + 生产构建 |
| `make test` | 与第一行相同；`review-results/offline-all-*/result.json` 为机器可读报告 |

跳过的 1 项是 POSIX SIGTERM 测试，在 Linux 上执行；本机为 macOS。

## 记录

1. 依赖版本经 GitHub Actions 实际安装日志核对，保持原固定值不变；本机镜像索引较旧，不作为判断依据。
2. GitHub Actions 工作流改为使用统一入口 `run_offline_tests.py`，运行结果以 Actions 页面为准。
3. 归档文件移动后，`docs/INTERVIEW_QA.md` 的链接与 `v1/VALIDATION.md`、`docs/VALIDATION_v0.6.md` 中的命令路径已同步更新。

## 边界

- 本次没有运行 Containerlab 部署、FRR 现场采集、OSPF 邻居或 HTTP 端到端检查。
- 本次没有调用 DeepSeek，不能据此更新任何模型准确率结论。
- 历史版本（v0.5–v0.7）的验证结论保持原样，不因本次通过而改写。
