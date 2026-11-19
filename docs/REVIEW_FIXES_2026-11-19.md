# 2026-11-19 审查修正

审查对象：`interview-review-20261119T095500Z.zip`。以下 9 条全部确认并修复。

## P1

### 1. 导入诊断不应移动正式基线

`import_diagnosis.py` 不再调用 baseline 接口。导入只产生候选版本与草稿变更单；是否设为基线由使用者单独操作。

回归：
- 已有基线 A，导入快照 B 后基线仍是 A；
- 无 Finding 时不创建变更单，基线不变；
- 变更单创建被拒时，基线仍不变。

### 2. 快照与复算版本的身份绑定

- `handoff.py` 只选择**指定节点、命令成功、且包含 show running-config** 的观测；bundle 记录 `config_snapshot.node` 与 `evidence_id`。
- `import_diagnosis.py` 的规则检查显式传 `current_version_id`，并断言 `config_version_id` 与每条 Finding 都绑定该版本。

回归：
- 同一轨迹含 r1/r2 配置时，按 target 选择快照；
- 导入后断言规则运行与 Finding 的版本 ID；
- 快照与 bundle 哈希篡改均被拒绝。

### 3. 语义支持不再“空证据也算支持”

- 事实只来自 **已被 final 引用、status=ok、exit_code=0、接口匹配拓扑** 的观测。
- `normal` 需要两端 Area、两端邻居数与双向 ping；缺失即标记未知/不支持。
- 矩阵分别计算“按最终声明”和“按期望场景”的语义支持；虚假置信按最终声明判断。

回归：无引用 + exit_code=1 的相同 Area 不再支持 normal；缺邻居/ping 也不支持。

## P2

### 4. Ping 丢包率按数值解析

`0%` 成功，`10%`/`100%` 失败；不再用“包含 0%”做匹配。三档均有测试。

### 5. 截断接入模型输入路径

- `bound()` 把标记计入预算，输出不再超过上限。
- 新增 `bound_record()`：trace 保留原始输出，模型输入使用有界副本并标注 `truncated`。
- `agent_loop` 对所有成功工具结果应用 `bound_record`（`query_topology` 仍只给结构化数据）。

### 6. 预算遥测缺失单独报告

- 只有可判定记录进入超预算率分母；缺 token/耗时的记录计入 `missing_telemetry_rate`。
- A/B 默认要求变体的场景集合一致，不匹配直接拒绝；`--allow-unpaired` 才允许非配对比较。

### 7. 区域冲突弃权时附上区域证据

管理关闭/passive 与区域不一致并存时，弃权响应同时包含两类证据：接口状态 + 两端 `ip ospf area` 配置行。回归同时检查状态和证据文本。

## P3

### 8. 空样本统计契约

`rate(0, 0)` 返回 `rate=None`、CI 为空、`underpowered=true`，不再抛异常。

## 验证

```
NetSecOps 离线全量：170 通过，1 跳过（核心 144 + 拓扑 26）
netcfg 单元/接口：208 通过
netcfg E2E：16 通过（CI）
两个发布检查：PASS
```

---

# 第二轮复审（同日）

审查对象：`interview-review-20261119T102000Z.zip`。3 条全部确认并修复。

## P1 · 缺少目标节点快照时不得回落到对端

`handoff.py` 不再从对端找配置。目标节点没有成功快照时直接拒绝导出，并新增 `device` 必须等于快照来源节点的校验；`import_diagnosis.py` 也要求 `config_snapshot.node == device.name`。

回归：
- 删除 r2 快照后，`build_bundle(trace, "r2", target="r2")` 被拒绝；
- 只有对端快照的 bundle 无法作为目标设备导入（跨仓库测试）。

## P1 · 双向 ping 必须按方向与目的地址验证

语义层从 `query_topology` 取两端 Loopback，按 `(node, target)` 去重后要求 r1→r2 与 r2→r1 各一条成功证据。同一方向重复不再证明正常。

回归：删除 r2→r1、复制 r1→r2 后 `semantic_support(trace, "normal")` 为不支持。

## P2 · A/B 配对按样本身份而非场景标签

`sample_id` 对 `task + initial_topology + evidence` 做哈希；变体签名按 `(sample_id, expected)` 排序比较，允许行序不同，拒绝重复或缺样。`--allow-unpaired` 时才输出非配对结果。

回归：同标签但底层观测不同 → 拒绝；manifest 行序不同 → 仍配对；重复样本 → 拒绝。

## 验证

```
NetSecOps 离线全量：175 通过，1 跳过（核心 149 + 拓扑 26）
netcfg 单元/接口：209 通过
两个发布检查：PASS
```

---

# 第三轮复审（同日）

审查对象：`interview-review-20261119T103000Z.zip`。1 条 P2 确认并修复。

## P2 · 同方向矛盾 ping 结果不能由记录顺序决定

原先用字典 `(node,target) -> ok`，后写入的 True 会覆盖先写入的 False。现在按 `(node,target)` 收集全部结果：

- 缺任一端 → 不支持；
- 同方向全为失败 → 不支持；
- 同方向同时出现成功与失败 → 判为 `contradictory`，不支持；
- 只有两端各自全部成功才支持 `normal`。

回归覆盖“失败记录在前”和“成功记录在前”两种排列，均断言不支持。

## 验证

```
NetSecOps 离线全量：176 通过，1 跳过（核心 150 + 拓扑 26）
netcfg 单元/接口：209 通过
```
