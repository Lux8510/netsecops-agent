# 故障注入矩阵与模拟修复

这两个能力把项目从“5 份演示轨迹”推进到“可复现的受控实验”，但都不改变原有边界：

- 仍然只读诊断，不碰生产设备。
- 故障注入与模拟修复**只允许**在 Containerlab 实验网里执行。
- 所有变更都有固定命令白名单，并且**一定回滚**。

---

## 1. 故障注入矩阵

### 两种模式

| 模式 | 命令 | 需要什么 | 用途 |
| --- | --- | --- | --- |
| 离线评估 | `make matrix` | 无（读取已保存轨迹） | 回归指标、CI 可跑 |
| 现场矩阵 | `NETSECOPS_LIVE_MATRIX=1 python experiment_matrix.py --live --runs 3` | Linux/WSL2 + Docker + Containerlab + DeepSeek Key | 真实注入、真实模型 |

离线模式只验证“指标计算与轨迹解析”，不产生新的实验结论。  
现场模式会对每个场景重复注入、回放诊断、恢复，耗时较长。

### 指标定义

| 指标 | 定义 | 为什么需要 |
| --- | --- | --- |
| 根因准确率 | 最终状态等于场景期望状态 | 最直观，但容易被小样本高估 |
| 证据覆盖率 | 最终引用的证据覆盖该结论所需的全部证明 | 防止“结论对、证据空” |
| 虚假置信率 | 给出确定结论，但证据覆盖率 < 100% | 比“答错”更危险 |
| 错误但自信 | 确定结论且与期望不符 | 单独统计，避免和“证据不足”混淆 |
| 弃权率 | 最终为 `insufficient_evidence` / `collection_failure` | 衡量系统是否敢说“不知道” |
| 护栏介入率 | `guardrail_changes` 非空 | 观察校验层改了多少次模型输出 |

**注意**：证据覆盖率是“引用完整性”，不是“语义正确性”。它只能证明模型指对了证据，不能证明推理一定正确。

### 场景目录

| 场景 | 注入方式 | 期望状态 |
| --- | --- | --- |
| `normal` | 不注入 | `normal` |
| `area_mismatch` | 改一侧互联接口 Area | `ospf_area_mismatch` |
| `admin_down` | 管理关闭一侧互联接口 | `interface_admin_down` |
| `ospf_passive` | 一侧互联接口启用 passive | `ospf_passive` |

`collection_failure` 与 `insufficient_evidence` 不通过配置注入，而是由采集失败/缺证据自然产生，可在离线清单里评估。

### 离线清单格式

```json
{
  "runs": [
    {"trace": "labs/ospf-baseline/tests/fixtures/traces/stage5-case-01.json",
     "scenario": "area_mismatch", "expected": "ospf_area_mismatch"}
  ]
}
```

不写 `expected` 时使用场景默认值。运行：

```bash
python labs/ospf-baseline/experiment_matrix.py \
  --manifest experiments/manifest.example.json --out matrix-report
```

输出 `summary.json` / `summary.md`，含总体指标、分场景指标和每次运行的明细。

### 关于 `make matrix` 的 100%

仓库自带的 4 条样例轨迹是**已发布的历史回归集**，全部通过并不代表泛化准确率。  
真实的实验数字必须来自 `--live` 模式，并且要注明运行次数、模型版本和日期。  
如果没跑过现场矩阵，就写“离线回归 4/4”，不要写“准确率 100%”。

---

## 2. 模拟修复（lab-only）

流程：

```text
采集 → 诊断（必须是三种可修复根因之一）
     → 固定命令修复
     → 重新采集并诊断（期望 normal）
     → 无论成败都回滚
     → 再次诊断（期望回到原故障）
```

### 能修什么

| 诊断状态 | 修复动作 | 回滚动作 |
| --- | --- | --- |
| `ospf_area_mismatch` | 把 Area 改成对端 Area | 改回原 Area |
| `interface_admin_down` | `no shutdown` | `shutdown` |
| `ospf_passive` | `no ip ospf passive` | `ip ospf passive` |

`normal`、`insufficient_evidence`、`collection_failure` 一律拒绝，不做“顺手修一下”。

### 安全约束

- 必须同时满足：`NETSECOPS_LAB_SIMULATION=1` 与 `--confirm-lab`。
- 节点必须在拓扑里，接口必须是该节点的互联接口。
- 命令必须匹配固定白名单正则；不接受任意字符串。
- 容器名必须以 `clab-<lab>-` 开头。
- 预诊断状态必须与计划一致，否则拒绝执行。
- 回滚失败会写入报告并返回失败，不会静默忽略。

运行：

```bash
NETSECOPS_LAB_SIMULATION=1 python labs/ospf-baseline/simulate_fix.py \
  --target r2 --confirm-lab
```

报告在 `simulation-runs/<时间戳>/report.json`，包含修复前后状态、命令、回滚结果。

---

## 3. 验证现状

| 项目 | 状态 |
| --- | --- |
| 指标计算、清单解析、聚合 | 12 项离线测试 |
| 修复计划、白名单、回滚流程 | 12 项离线测试 |
| 真实故障注入 + 模型矩阵 | **未在 CI 执行**（需要 Docker/密钥） |
| 真实模拟修复 | **未在本机执行**（无 Docker） |

离线测试证明的是“流程与安全约束正确”，不是“现场实验成功”。  
如果要写进简历或文档，请分别写清楚“离线 24 项测试”和“现场矩阵未执行”。
