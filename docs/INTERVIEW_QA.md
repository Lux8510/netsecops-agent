# NetSecOps Agent：十个面试问答（按现存实现与记录作答）

以下回答描述项目当前状态，不代替对代码的亲自阅读，也不推断任何人的个人贡献。每题所列文件可复核具体实现或结果。

1. **为什么这里使用 Agent？** 规则基线能对已编码的三类故障做确定性判断；Agent 的额外价值是在给定任务与拓扑后，根据已返回的观测自行选择下一项只读检查，而不是预先执行固定全量采集。它并不天然比规则准确，必须受白名单、预算和证据保护层约束。依据：[Agent 调用循环](../labs/ospf-baseline/agent_loop.py) `run_agent()`、[规则基线](../labs/ospf-baseline/diagnose_offline.py) `diagnose()`、[正常案例保存轨迹](../examples/cases/normal.json)。

2. **与纯规则诊断的区别是什么？** 规则程序只读整份采集器观测 JSON，按明确条件给状态；Agent 每轮接收模型 API 的 `tool_calls`，只取得所请求工具的观测，最后的模型文本还要经确定性保护。历史 r2 回放把规则、模型原始、保护后和独立评判分开保存，不能把规则答案当模型输入。依据：[规则入口](../labs/ospf-baseline/diagnose_offline.py)、[工具与回放后端](../labs/ospf-baseline/agent_tools.py)、[r2 区域案例保存轨迹](../examples/cases/area-mismatch.json)。

3. **如何证明工具调用不是写死的采集流程？** `run_agent()`解析每次 API 回复的工具名称与 JSON 参数，再交给 `AgentTools.call()`；轨迹同时保存 `model_message.tool_calls`、`tool_results`。独立评判逐项核对两者 ID、名称、参数与证据。不同记录的调用数和顺序可直接对照，不依赖口头声明。依据：[调用循环](../labs/ospf-baseline/agent_loop.py) `run_agent()`、[评判器](../labs/ospf-baseline/judge_agent_trace.py) `verdict()`、[五份可对照轨迹](../examples/README.md)。

4. **怎样限制模型对网络的权限？** 工具层只接受拓扑中的节点/接口、限定路由范围和对端地址；底层命令来自固定参数数组，没有任意 shell 或配置工具。现场后端先检查容器身份、固定镜像和运行状态；回放后端只读取一份指定的观测文件。Agent 自身只提供修复建议，不执行它。依据：[工具白名单](../labs/ospf-baseline/agent_tools.py) `TOOL_ARGUMENTS`/`_validated_args()`、[固定命令](../labs/ospf-baseline/collect_evidence.py) `command_argv()`、[现场/回放后端](../labs/ospf-baseline/agent_tools.py)。

5. **evidence_id 为什么重要，怎样防止“引用不存在的证据”？** 每次工具调用生成独立 ID，记录节点、参数、原始命令、输出、退出码和错误。保护层剔除不存在的引用；独立评判还要求最终引用来自本次模型实际调用所返回、内容未被改写的证据，并验证补引来源和原因。ID 仅在一次轨迹内部有意义。依据：[工具证据记录](../labs/ospf-baseline/agent_tools.py) `AgentTools.call()`、[保护层](../labs/ospf-baseline/agent_loop.py) `validate_final()`、[独立评判](../labs/ospf-baseline/judge_agent_trace.py) `verdict()`。

6. **三种根因分别需要什么直接证据？** 区域不一致需两端互联接口有效且不同的 Area；管理关闭需故障接口的 FRR 管理状态；passive 需该接口作用域内有效的运行配置。邻居消失、路由缺失和 ping 失败只能说明症状。依据：[保护层事实提取](../labs/ospf-baseline/agent_loop.py) `cited_area_facts()`/`cited_admin_facts()`/`cited_passive_facts()`、[规则基线](../labs/ospf-baseline/diagnose_offline.py) `direct_interface_faults()`、[评判器](../labs/ospf-baseline/judge_agent_trace.py) `verdict()`。

7. **如何避免定位正确却建议改错设备？** 权威 `root_cause` 把故障类型、节点、接口及直接证据绑定；管理关闭/passive 的建议由已验证节点和接口生成。区域不一致只能确认两端 Area 不同，不能仅据此指定哪一端设计有误。独立评判会拒绝结构化位置或修复对象与证据不一致的报告；Agent 不执行改配。依据：[权威结果生成](../labs/ospf-baseline/agent_loop.py) `validate_final()`、[评判器](../labs/ospf-baseline/judge_agent_trace.py) `verdict()`、[可靠性反例测试](../labs/ospf-baseline/tests/test_reliability.py)。

8. **如何区分网络故障、采集失败和证据不足？** 先验证观测身份、命令、输出接口/地址；命令真实失败不能用于证明 Area。关键失败若仍有效，最终为 `collection_failure`；Area 缺一端或字段矛盾不能确认具体根因。只有同节点、同接口、同查询的有效成功重采才能取代旧失败，旧记录不删除；成功返回的 `OSPF not enabled` 没有 Area，不能自动得出区域正常。依据：[观测身份校验](../labs/ospf-baseline/collect_evidence.py) `observation_mismatch()`、[失败/重采分类](../labs/ospf-baseline/evidence_checks.py) `critical_history()`、[v0.5 修复说明](../history/REVIEW_REPAIR_v0.5.md)、[缺 Area](../examples/cases/missing-area.json)与[失败](../examples/cases/collection-failure.json)保存轨迹。

9. **怎样验证结果，如何解释“72 项通过”？** 离线 72 项是无密钥、无模型、无 Docker 的干净副本代码回归；真实模型验收另核对 API 请求、工具轨迹、证据引用、保护层差异、耗时和 token；现场网络健康检查又是第三件事。v0.5 单次真实验收前三例 PASS、动态例 NOT_EXERCISED、总体 INCOMPLETE，最终网络 `HEALTHY_UNCHANGED`，不能与早期通过数据相加成准确率。依据：[v0.6 离线与网页验证](VALIDATION_v0.6.md)、[历史验收边界](PROJECT_OVERVIEW.md)、[独立评判代码](../labs/ospf-baseline/judge_agent_trace.py)。完整历史验收文件保留本地，不在源码仓库。

10. **动态成功重采为什么仍未完成真实模型验证？** [定向验收入口](../labs/ospf-baseline/run_v05_targeted_acceptance.py) 安排 r2 首次 OSPF 查询返回执行失败、后续同查询返回有效 down/未启用输出，但不规定模型必须重试。该次保留在本地的 case-04 轨迹显示模型只执行一次 r2 OSPF 查询；虽取得管理关闭证据，却没触发第二次同查询。故保护后保持 `collection_failure`，动态路径记为 `NOT_EXERCISED`，不能把其原始 `interface_admin_down` 或离线重采测试当作真实模型通过。依据：[验收入口](../labs/ospf-baseline/run_v05_targeted_acceptance.py)、[v0.5 离线重采测试](../labs/ospf-baseline/tests/test_review_recheck_v04.py)、[范围说明](PROJECT_OVERVIEW.md)。完整失败/未执行记录只在本地保存，网页没有伪造此路径。
