# 取舍记录（v1 收敛）

日期：2026-11-09。范围：仓库结构、可复现性、可展示性。**不改诊断逻辑、不重写证据规则、不删除历史材料。**

判断标准（按优先级）：

1. 别人能否在 5 分钟内跑起来
2. 面试官能否在 10 分钟内看懂这个项目在解决什么
3. 历史结论是否仍然可追溯
4. 改动风险是否可控

## 结论概览

| 处置 | 内容 |
| --- | --- |
| 保留在主线 | `labs/ospf-baseline/`（Agent 核心）、`v1/`（新拓扑）、`web/`、`examples/`、`docs/` 的概览与验证记录 |
| 归档到 `history/` | v0.1–v0.5 审查指南、修复说明、打包脚本、旧测试入口、旧版 README 日志 |
| 新增 | `Makefile`、`run_offline_tests.py`、`DECISIONS.md`、重写 `README.md` |
| 修正 | `requirements-progress.txt` 固定了不存在的版本；CI 入口 |
| 暂不处理 | `labs/` 内部的历史验收脚本（见"待决"） |

## 逐项说明

### 新增 `run_offline_tests.py`

原来根目录有 6 个 `run_clean_offline_tests*.py`，每个对应一个历史版本。面试者不知道该跑哪个，CI 也只能选一个。

现在：

- `run_offline_tests.py` = 唯一入口，跑核心套件（77）+ v1 套件（26）= **103 项**
- `run_clean_offline_tests.py` 保留为干净的复制工具模块（新入口依赖它）
- 旧入口 `v03–v07` 移到 `history/`

风险：历史版本的入口不再在根目录，但 `history/` 内脚本仍在，且旧结论以文档形式保留。
如果审查认为需要保留可执行的历史入口，可再补一个 `make test-v06`。

### `requirements-progress.txt` 修正

原内容：

```text
fastapi==0.162.0
uvicorn==0.54.0
```

这两个版本在公开 PyPI 上不存在（截至核对：fastapi 最新 0.128.8，uvicorn 最新 0.39.0）。
后果：GitHub Actions 的 `pip install` 必然失败，任何人新装环境也装不上。

现改为实测通过的固定版本：

```text
fastapi==0.128.8
uvicorn==0.39.0
httpx==0.28.1
```

风险：如果原版本来自某个私有镜像，需要确认该镜像是否也要同步。

### `history/` 归档

移动的文件**全部是历史审查材料**，不被 `labs/`、`v1/`、`web/` 的代码 import：

- `REVIEW_GUIDE*.md`（5 份）、`REVIEW_REPAIR_v0.3–v0.5.md`（3 份）
- `V03_REAL_ACCEPTANCE_20261014.md`、`RELIABILITY_CHANGELOG.md`
- `build_review_bundle*.py`（6 个打包脚本）、`review-input-v0.4/`
- `run_clean_offline_tests_v03–v07.py`
- `tools/create_v06_manifest.py`、`create_v07_manifest.py`
- 原 `README.md` → `history/README-v0.7-log.md`（保留完整版本日志）

风险：`docs/` 内原先指向根目录这些文件的相对链接会失效，已同步修正。归档脚本不再保证能直接运行（它们依赖当时的目录结构），这是刻意的：**归档是快照，不是维护目标**。

### 重写 `README.md`

原 README 是 400+ 行的按时间追加日志，第一屏讲的是 v0.6 的解码细节。对第一次打开仓库的人不友好。

新 README 的结构：一句话定位 → 能/不能做什么 → 3 条命令跑起来 → 四个设计要点 → 架构图 → 验证现状 → 目录 → 延伸阅读。

历史日志完整保留在 `history/README-v0.7-log.md`，没有删除任何结论。

风险：重写可能弱化某些历史限定语。处理方式：新 README 只陈述当前可复现的事实，历史口径留在验证文档里。

### 新增 `Makefile`

`make test` / `make web` / `make demo` / `make publish-check` / `make clean`。
动机：面试者不会读文档找命令，只会看有没有 `make`。

### CI 修正

`.github/workflows/offline.yml`：

- 改为安装修正后的依赖
- 改用 `python -B run_offline_tests.py`（同时覆盖核心与 v1）
- 保留 `check_publication.py` 与前端 `npm run check`

注意：该工作流此前从未在远端真正运行过（历史文档已注明），本次推送后是第一次。

## 待决（需要审查意见）

| 项 | 选项 | 我的倾向 |
| --- | --- | --- |
| `labs/` 内的历史脚本（`reliability_freeze_v03/04/05.py`、`run_stage5_*`、`run_v03_real_acceptance.py`、`run_v05_targeted_acceptance.py`、`stage5_freeze.py`、`inspect_frr_stage5.py`、`build_agent_acceptance.py`） | 留在原位 / 移入 `labs/ospf-baseline/history/` | **移入**，但需先确认测试是否 import |
| `v1/` 与 `labs/` 的关系 | 并列 / 合并 | 暂并列；合并是产品化阶段的事，不应在本轮做 |
| 是否公开仓库 | 私有 / 公开脱敏版 | **公开脱敏版**才有展示价值，但需先跑一遍 `publish-check` |
| 评测指标（根因准确率、护栏干预率、token/延迟） | 本轮做 / 下轮做 | **下轮**，需要先扩场景集 |
| 持久化运行历史（SQLite） | 做 / 不做 | **暂不做**，当前 JSON 轨迹已足够演示 |

## 未改变的部分

- 诊断规则、工具白名单、证据校验、判分器：一行未改
- 示例轨迹与验证记录：未改
- `.gitignore`：未改
- 历史结论的口径：未改
