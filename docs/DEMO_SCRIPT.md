# 三分钟演示：从工具调用到可核实结论

准备：在项目根目录 `cd web && npm ci && npm run dev`，打开终端显示的本地地址。默认案例库无 API Key、无 Docker、无实时采集；展示的是 [`examples/`](../examples/README.md) 选择性导出的五份历史轨迹。`E00x` 只在当前案例中有效。不要把保存轨迹回放说成新一轮真实模型验收。

| 时间 | 页面操作 | 讲述重点 |
|---|---|---|
| 0:00–0:30 | 打开“正常基线”，未播放先看网络拓扑。 | “现在 Area、地址与邻居显示未知；虽然案例有历史最终结果，工作台不提前泄漏未回放观测。” |
| 0:30–1:05 | 点击播放或单步到结束；点 `E002`、`E003` 和一项 ping 证据。 | “这是 v0.5 **当时现场只读诊断**的[保存副本](../examples/cases/normal.json)。工具返回两端 Area、邻居 FULL、远端路由与双向 Loopback ping 后才显示 `normal`。点击证据可看到原始命令、时间、退出码和输出。” |
| 1:05–1:35 | 切“OSPF 区域不一致”，单步到两端 Area 出现；再切“互联接口管理关闭”。 | “这两例是较早 Stage 5 的**冻结观测模型回放**，不是现在的现场。[区域案例](../examples/cases/area-mismatch.json)只能证明两端不同，没有设计基准不能指定错误端；[管理关闭案例](../examples/cases/admin-down.json)有故障接口管理状态的直接证据。邻居消失只是症状。” |
| 1:35–2:20 | 切“关键命令采集失败”，播放到底，点模型原始状态、校验后状态和补引 `E003`。 | “[保存轨迹](../examples/cases/collection-failure.json)中模型原始误判为区域不一致；关键 OSPF 查询真正执行失败，系统改为 `collection_failure`，并把本轮实际取得的失败证据补入最终引用。旧模型文字保留但不作为已验证事实。” |
| 2:20–2:45 | 切“缺少一端 Area 证据”，播放到底。 | “[保存轨迹](../examples/cases/missing-area.json)缺一端有效 Area；即使邻居或路由异常，保护后仍是 `insufficient_evidence`，不输出确定的区域修复对象。” |
| 2:45–3:00 | 看运行概览与[验证记录](VALIDATION_v0.6.md)。 | “网页回放、Python 离线测试、历史真实模型验收是三种不同验证。v0.5 前三项真实定向验收 PASS，但动态成功重采 `NOT_EXERCISED`、总体 `INCOMPLETE`。这不是通用准确率或自动修复系统。” |

如面试官追问，打开 [`agent_loop.py`](../labs/ospf-baseline/agent_loop.py) 的 `run_agent()`/`validate_final()`、[`agent_tools.py`](../labs/ospf-baseline/agent_tools.py) 的白名单和后端、[`judge_agent_trace.py`](../labs/ospf-baseline/judge_agent_trace.py) 的独立评判、[`trace.ts`](../web/src/trace.ts) 的网页 schema/证据校验。页面显示的单次工具耗时若无原始字段就是“未记录”；不展示模型内部思维链。

**可选的 v0.7 追加演示（不计入上面三分钟）：**按 [README](../README.md) 启动本机事件服务后展开“本次诊断事件流”，运行 mock 演示。指出 `run_agent()` 的回调在实际执行步骤后发送 SSE，网页逐步收到模型调用记录和 E001–E009，再显示保护层补引与最终结论。这里的工具顺序来自脚本化 mock，观测来自固定测试样本，**不是 DeepSeek 自主选工具的真实验收**。现场按钮本轮未测试，不作为面试现场的默认演示步骤。
