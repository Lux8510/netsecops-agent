"""One read-only diagnostic agent using model-selected Chat Completions tools."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import re
import time
from typing import Any, Protocol

from agent_config import AgentSettings
from agent_tools import AgentTools, tool_schemas
from collect_evidence import observation_mismatch, ospf_address_area, sections
from deepseek_client import DeepSeekAPIError
from evidence_checks import critical_history, normal_proofs


FINAL_STATES = {"normal", "ospf_area_mismatch", "interface_admin_down", "ospf_passive",
                "insufficient_evidence", "collection_failure"}


class ChatModel(Protocol):
    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]],
                 *, deadline: float) -> dict[str, Any]: ...


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_final(content: Any) -> dict[str, Any] | None:
    if not isinstance(content, str):
        return None
    candidate = content.strip()
    fenced = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", candidate, flags=re.IGNORECASE | re.DOTALL)
    if fenced:
        candidate = fenced[-1]
    try:
        value = json.loads(candidate)
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        pass
    # Some Chat Completions responses prepend prose despite the JSON-only request.
    # Accept only a complete trailing JSON object; never infer a conclusion from prose.
    decoder = json.JSONDecoder()
    for position, character in enumerate(candidate):
        if character != "{":
            continue
        try:
            value, end = decoder.raw_decode(candidate[position:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and not candidate[position + end:].strip():
            return value
    return None


def cited_area_facts(ids: list[str], tools: AgentTools) -> dict[str, dict[str, str]]:
    facts: dict[str, dict[str, str]] = {}
    try:
        details = tools.backend.details()
    except (ValueError, KeyError, TypeError):
        return facts
    for evidence_id in ids:
        item = tools.evidence.get(evidence_id, {})
        if item.get("tool") != "query_ospf_interface" or item.get("status") != "ok":
            continue
        node = item.get("arguments", {}).get("node")
        if node not in tools.layout.nodes or item.get("arguments", {}).get("interface") != tools.layout.interfaces[node]:
            continue
        observations = item.get("observations") or []
        if (len(observations) != 1 or observations[0].get("exit_code") != 0 or observations[0].get("error") or
                observation_mismatch(tools.layout, node, "ospf_interface", observations[0], details)):
            continue
        output = str(observations[0].get("stdout") or "")
        fact = ospf_address_area(output)
        if fact:
            facts[node] = {"area": fact[1], "interface": tools.layout.interfaces[node],
                           "evidence_id": evidence_id}
    return facts


def cited_admin_facts(ids: list[str], tools: AgentTools) -> dict[str, str]:
    found: dict[str, str] = {}
    for evidence_id in ids:
        item = tools.evidence.get(evidence_id, {})
        if item.get("tool") != "query_interface" or item.get("status") != "ok":
            continue
        node = item.get("arguments", {}).get("node")
        if node not in tools.layout.nodes or item.get("arguments", {}).get("interface") != tools.layout.interfaces[node]:
            continue
        records = {obs.get("id"): obs for obs in item.get("observations") or []}
        frr = records.get("frr_interface", {})
        output = str(frr.get("stdout") or "")
        interface = tools.layout.interfaces[node]
        if (frr.get("exit_code") == 0 and not frr.get("error") and
                not observation_mismatch(tools.layout, node, "frr_interface", frr) and
                re.search(r"(?m)^Interface\s+" + re.escape(interface) + r"\s+is\s+down\b", output) and
                re.search(r"(?im)^\s*Shutdown config is\s+(?!Not specified by CLI|off|disabled).+", output)):
            found[node] = evidence_id
    return found


def cited_passive_facts(ids: list[str], tools: AgentTools) -> dict[str, str]:
    found: dict[str, str] = {}
    for evidence_id in ids:
        item = tools.evidence.get(evidence_id, {})
        if item.get("tool") != "query_interface_config" or item.get("status") != "ok":
            continue
        node = item.get("arguments", {}).get("node")
        if node not in tools.layout.nodes or item.get("arguments", {}).get("interface") != tools.layout.interfaces[node]:
            continue
        records = item.get("observations") or []
        if len(records) != 1 or records[0].get("exit_code") != 0 or records[0].get("error"):
            continue
        if observation_mismatch(tools.layout, node, "running_config", records[0]):
            continue
        parts = sections(str(records[0].get("stdout") or ""))
        interface = tools.layout.interfaces[node]
        interface_lines = parts.get(f"interface {interface}", [])
        process_lines = parts.get("router ospf", [])
        if "ip ospf passive" in interface_lines or f"passive-interface {interface}" in process_lines:
            found[node] = evidence_id
    return found


def validate_final(raw: dict[str, Any], tools: AgentTools) -> dict[str, Any]:
    status = raw.get("status")
    if status not in FINAL_STATES:
        status = "insufficient_evidence"
    stated_ids = raw.get("evidence_ids")
    ids = [item for item in stated_ids if isinstance(item, str) and item in tools.evidence] if isinstance(stated_ids, list) else []
    ids = list(dict.fromkeys(ids))
    supplied_missing = raw.get("missing_information")
    if isinstance(supplied_missing, str):
        missing = [supplied_missing] if supplied_missing.strip() else []
    elif isinstance(supplied_missing, list):
        missing = [item for item in supplied_missing if isinstance(item, str) and item.strip()]
    else:
        missing = []
    if isinstance(stated_ids, list) and len(ids) != len(stated_ids):
        missing.append("模型引用了本次运行中不存在的 evidence_id；无效引用已丢弃。")
    additions: list[dict[str, Any]] = []

    def cite_validated(identifier: str, requirement: str, reason: str) -> None:
        if identifier in ids:
            return
        item = tools.evidence[identifier]
        ids.append(identifier)
        additions.append({"evidence_id": identifier, "requirement": requirement,
                          "source_tool": item["tool"], "node": (item.get("arguments") or {}).get("node"),
                          "arguments": item.get("arguments"), "reason": reason})

    if status == "normal":
        proofs, normal_missing = normal_proofs(tools.layout, tools.evidence)
        if normal_missing:
            status = "insufficient_evidence"
            missing.extend(normal_missing)
        else:
            for requirement, identifier in proofs.items():
                cite_validated(identifier, requirement,
                               "正常结论必须引用本次已执行并验证有效的拓扑、两端 Area、邻居、路由和双向 ping。")
    facts = cited_area_facts(ids, tools)
    admin_facts = cited_admin_facts(ids, tools)
    passive_facts = cited_passive_facts(ids, tools)
    if status == "ospf_area_mismatch" and (set(facts) != set(tools.layout.nodes) or
                                            len({item["area"] for item in facts.values()}) != 2):
        status = "insufficient_evidence"
        missing.append("缺少两端互联接口的有效且不同的 OSPF Area 原始证据。")
    if status == "interface_admin_down" and len(admin_facts) != 1:
        status = "insufficient_evidence"
        missing.append("缺少唯一故障互联接口被管理关闭的有效 FRR 状态输出。")
    if status == "ospf_passive" and len(passive_facts) != 1:
        status = "insufficient_evidence"
        missing.append("缺少唯一故障互联接口的有效 OSPF passive 运行配置。")
    if status == "normal" and (set(facts) != set(tools.layout.nodes) or
                                len({item["area"] for item in facts.values()}) != 1):
        status = "insufficient_evidence"
        missing.append("未引用两端互联接口的一致 Area 信息，不能确认完整基线正常。")
    active_failures, superseded = critical_history(tools.layout, tools.evidence)
    for failure in active_failures:
        cite_validated(failure["evidence_id"], "active_critical_collection_failure",
                       "本次固定命令真实执行失败，且尚无同节点同接口同查询的有效成功重采。")
    if active_failures:
        status = "collection_failure"
        missing.append("互联接口关键查询采集失败；失败命令的输出不能用于根因判断。")
    elif status == "collection_failure":
        status = "insufficient_evidence"
        missing.append("没有尚未被有效重采取代的真实关键命令失败记录。")
    for recheck in superseded:
        if recheck["failed_evidence_id"] in ids or raw.get("status") == "collection_failure":
            cite_validated(recheck["successful_evidence_id"], "superseding_successful_recheck",
                           f"此前失败 {recheck['failed_evidence_id']} 已被同一查询的有效成功重采取代；失败历史仍保留。")
            missing.append(f"此前采集失败 {recheck['failed_evidence_id']} 已由后续有效重采 "
                           f"{recheck['successful_evidence_id']} 取代，不能继续当作当前失败。")
    root_cause: dict[str, Any] | None = None
    observed: list[str] = []
    inferred: list[str] = []
    if status == "ospf_area_mismatch":
        endpoints = [{"node": node, **facts[node]} for node in tools.layout.nodes]
        root_cause = {"fault_type": status, "node": None, "interface": None,
                      "evidence_ids": [item["evidence_id"] for item in endpoints], "endpoints": endpoints}
        observed = [f"{item['node']} {item['interface']} OSPF Area {item['area']}（{item['evidence_id']}）"
                    for item in endpoints]
        conclusion = "实测两端互联接口 OSPF Area 不一致：" + "；".join(observed) + "。无法仅凭此判定哪一端改错。"
        repair = "人工核对设计区域后，使两端互联接口 Area 一致；不要未经核实就指定某一端改配。"
        inferred = ["邻居及远端路由可能受影响；未引用的 ping 或路由检查不视为实测。"]
    elif status in ("interface_admin_down", "ospf_passive"):
        node, evidence_id = next(iter((admin_facts if status == "interface_admin_down" else passive_facts).items()))
        interface = tools.layout.interfaces[node]
        root_cause = {"fault_type": status, "node": node, "interface": interface,
                      "evidence_ids": [evidence_id], "endpoints": []}
        observed = [f"{node} {interface} 的{'管理状态为关闭' if status == 'interface_admin_down' else '有效 OSPF 配置为 passive'}（{evidence_id}）"]
        conclusion = "实测" + observed[0] + "。"
        repair = (f"人工核对原配置后，仅在 {node} {interface} " +
                  ("恢复管理启用状态（no shutdown）。" if status == "interface_admin_down"
                   else "恢复原有非 passive 状态（no ip ospf passive）。"))
        inferred = ["OSPF 邻居及远端 Loopback 连通性可能受影响；需另以只读检查确认。"]
    elif status == "normal":
        observed = ["两端互联 Area 一致，且已引用邻居 FULL、OSPF 路由和双向 Loopback ping。"]
        conclusion = "本次引用的只读观测支持实验拓扑正常。"
        repair = "无需修改配置；继续按需只读监测。"
    else:
        conclusion = "证据不足，不能确认网络根因。"
        repair = "先只读核实两端接口状态、OSPF Area/passive 配置和采集错误；根因未确认前不要修改配置。"
    if active_failures:
        conclusion = "关键接口信息采集失败，无法确定网络根因。"
        repair = "先只读重试失败的接口查询并核对采集错误；根因未确认前不要修改配置。"
    return {
        "status": status, "conclusion": conclusion,
        "evidence_ids": ids, "evidence_additions": additions, "missing_information": missing,
        "repair_suggestion": repair, "root_cause": root_cause,
        "observed_facts": observed, "inferred_impacts": inferred,
    }


def incomplete(reason: str, tools: AgentTools) -> dict[str, Any]:
    return {"status": "incomplete", "conclusion": "检查未完成，不能确认网络根因。",
            "evidence_ids": list(tools.evidence), "missing_information": [reason],
            "repair_suggestion": "先补齐只读证据，不自动修改配置。",
            "root_cause": None, "observed_facts": [], "inferred_impacts": []}


def run_agent(task: str, model: ChatModel, tools: AgentTools, settings: AgentSettings,
              *, mode: str) -> dict[str, Any]:
    started = time.monotonic()
    deadline = started + settings.total_timeout_seconds
    initial = {"lab": tools.layout.lab_name, "nodes": list(tools.layout.nodes),
               "link": [{"node": node, "interface": tools.layout.interfaces[node]}
                        for node in tools.layout.nodes]}
    system = (
        "你是单个只读 NetSecOps 诊断 Agent。只能使用列出的只读工具，自己决定下一步；"
        "没有证据时不要猜测。确认区域不一致须引用两端互联接口不同 Area 的原始输出；"
        "确认管理关闭须引用故障接口的 FRR 管理状态；确认 OSPF passive 须引用该接口的有效运行配置。"
        "邻居、路由、ping 只是症状。命令失败不是网络故障。"
        "若互联接口 OSPF 查询失败，应归类采集失败并引用该错误，绝不能把失败命令的残余输出当有效 Area；"
        "若仅一端有有效 Area，归类证据不足，不得给出确定性的区域改配建议。"
        "若判断网络正常，需要两端区域一致、邻居 FULL、远端 Loopback 的 OSPF 路由和双向 ping 证据；"
        "若仅完成部分检查，应报告证据不足。工具调用总数有限：拓扑工具已给出 Loopback 地址，"
        "不要为重复读取地址而查询 lo；发现能直接确认异常根因的接口状态或 passive 配置后，"
        "不必为了穷举症状继续查询每项路由与 ping，也不必无故查询另一端配置。"
        "自行选择最有信息量的下一项只读工具，不采用固定采集顺序。"
        "最终只输出 JSON 对象，键为 status、conclusion、evidence_ids、missing_information、repair_suggestion。"
        "status 只能是 normal、ospf_area_mismatch、interface_admin_down、ospf_passive、"
        "insufficient_evidence、collection_failure；"
        "evidence_ids 必须来自本轮工具返回。修复建议不能执行。"
    )
    messages: list[dict[str, Any]] = [{"role": "system", "content": system},
                                      {"role": "user", "content": f"任务：{task}\n初始拓扑：{json.dumps(initial, ensure_ascii=False)}"}]
    trace: dict[str, Any] = {"schema_version": 2, "mode": mode, "task": task,
                             "started_at_utc": utc_now(), "finished_at_utc": None,
                             "model_requested": settings.model, "model_reported": None,
                             "initial_topology": initial, "settings": {
                                 "max_rounds": settings.max_rounds, "max_tool_calls": settings.max_tool_calls,
                                 "max_tokens": settings.max_tokens,
                                 "request_timeout_seconds": settings.request_timeout_seconds,
                                 "command_timeout_seconds": settings.command_timeout_seconds,
                                 "total_timeout_seconds": settings.total_timeout_seconds,
                                 "max_retries": settings.max_retries,
                             }, "rounds": [], "tool_calls_executed": 0,
                             "usage_per_request": [],
                             "usage_total": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
                                             "requests_with_usage": 0},
                             "model_raw_final": None, "final": None, "guardrail_changes": [], "error": None}
    try:
        for round_number in range(1, settings.max_rounds + 1):
            if time.monotonic() >= deadline:
                trace["final"] = incomplete("诊断总超时。", tools)
                break
            remaining_calls = settings.max_tool_calls - trace["tool_calls_executed"]
            response = model.complete(messages, tool_schemas() if remaining_calls > 0 else [], deadline=deadline)
            usage = response.get("usage")
            trace["usage_per_request"].append(usage)
            if isinstance(usage, dict):
                for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                    if isinstance(usage.get(key), int):
                        trace["usage_total"][key] += usage[key]
                trace["usage_total"]["requests_with_usage"] += 1
            trace["model_reported"] = response.get("model") or trace["model_reported"]
            message = response.get("message")
            if not isinstance(message, dict):
                trace["final"] = incomplete("模型未返回有效 assistant 消息。", tools)
                break
            # DeepSeek Chat Completions requires the assistant tool_calls message before each tool result.
            messages.append(message)
            round_trace: dict[str, Any] = {"round": round_number, "request_id": response.get("request_id"),
                                           "finish_reason": response.get("finish_reason"),
                                           "model_message": message, "tool_results": [], "usage": usage}
            trace["rounds"].append(round_trace)
            calls = message.get("tool_calls") or []
            if not calls:
                if response.get("finish_reason") == "length":
                    trace["final"] = incomplete("模型输出达到 max_tokens，上下文不完整。", tools)
                else:
                    parsed = parse_final(message.get("content"))
                    trace["model_raw_final"] = parsed
                    trace["final"] = validate_final(parsed, tools) if parsed else incomplete("模型未返回有效 JSON 结论。", tools)
                    if parsed:
                        trace["guardrail_changes"] = [
                            {"field": key, "model_value": parsed.get(key), "final_value": trace["final"].get(key),
                             "reason": "证据引用和直接根因条件校验"}
                            for key in ("status", "conclusion", "evidence_ids", "missing_information",
                                        "repair_suggestion", "root_cause", "observed_facts", "inferred_impacts")
                            if parsed.get(key) != trace["final"].get(key)
                        ]
                break
            if not isinstance(calls, list):
                trace["final"] = incomplete("模型工具调用格式无效。", tools)
                break
            limit_hit = False
            for call in calls:
                if not isinstance(call, dict) or not isinstance(call.get("id"), str):
                    limit_hit = True
                    break
                function = call.get("function") or {}
                name = function.get("name")
                try:
                    arguments = json.loads(function.get("arguments", ""))
                except (TypeError, json.JSONDecodeError):
                    arguments = None
                if trace["tool_calls_executed"] >= settings.max_tool_calls or time.monotonic() >= deadline:
                    result = {"status": "limit_exceeded", "error": "Tool call or total-time limit reached"}
                    limit_hit = True
                else:
                    result = tools.call(name, arguments, deadline=deadline)
                    trace["tool_calls_executed"] += 1
                    if result.get("status") == "limit_exceeded":
                        limit_hit = True
                round_trace["tool_results"].append({"tool_call_id": call["id"], "name": name,
                                                    "arguments": arguments, "result": result})
                model_result = result
                if name == "query_topology" and result.get("status") == "ok":
                    # Keep full raw configs in trace; the model needs only the requested topology data.
                    model_result = {key: value for key, value in result.items() if key != "observations"}
                elif result.get("status") in ("collection_failed", "unavailable"):
                    # Failed-command stdout is preserved in trace but is not reliable model evidence.
                    model_result = dict(result)
                    model_result["data"] = None
                    model_result["observations"] = [
                        {key: value for key, value in observation.items() if key not in ("stdout", "stderr")}
                        for observation in result.get("observations") or []
                    ]
                messages.append({"role": "tool", "tool_call_id": call["id"],
                                 "content": json.dumps(model_result, ensure_ascii=False)})
            if limit_hit:
                trace["final"] = incomplete("达到工具调用次数或总时间上限。", tools)
                break
            if trace["tool_calls_executed"] == settings.max_tool_calls:
                messages.append({"role": "user", "content":
                                 "工具调用预算已用完。请仅依据已取得的证据给出最终 JSON；若仍缺直接证据，"
                                 "报告 insufficient_evidence，不要继续请求工具。"})
        if trace["final"] is None:
            trace["final"] = incomplete("达到模型轮数上限。", tools)
    except DeepSeekAPIError as exc:
        trace["error"] = {"category": exc.category, "status_code": exc.status_code, "message": str(exc)}
        trace["final"] = {"status": "api_error", "conclusion": "模型接口未完成诊断。",
                          "evidence_ids": list(tools.evidence),
                          "missing_information": [f"接口错误：{exc.category}；{exc}"],
                          "repair_suggestion": "先处理接口问题；不要修改网络配置。",
                          "root_cause": None, "observed_facts": [], "inferred_impacts": []}
    except Exception as exc:
        trace["error"] = {"category": "local", "message": f"{type(exc).__name__}: local agent error"}
        trace["final"] = incomplete("本地调用循环异常；检查未完成。", tools)
    finally:
        trace["finished_at_utc"] = utc_now()
        trace["elapsed_seconds"] = round(time.monotonic() - started, 3)
        trace["evidence"] = tools.evidence
        trace["evidence_additions"] = (trace.get("final") or {}).get("evidence_additions") or []
    return trace


def render_trace(trace: dict[str, Any]) -> str:
    final = trace["final"]
    lines = ["# 只读网络诊断 Agent 调用轨迹", "", f"模式：`{trace['mode']}`；模型：`{trace['model_reported'] or trace['model_requested']}`",
             f"任务：{trace['task']}", f"耗时：{trace['elapsed_seconds']} 秒", "",
             "## 模型实际选择的工具", ""]
    for turn in trace["rounds"]:
        if not turn["tool_results"]:
            lines.append(f"- 第 {turn['round']} 轮：返回最终文本。")
        for item in turn["tool_results"]:
            result = item["result"]
            lines.append(f"- 第 {turn['round']} 轮：`{item['name']}` {json.dumps(item['arguments'], ensure_ascii=False)}"
                         f" → `{result.get('evidence_id', '无')}` / `{result['status']}`")
    lines += ["", "## 工具返回证据索引", ""]
    for evidence_id, item in trace["evidence"].items():
        lines.append(f"- `{evidence_id}` `{item['tool']}`：`{item['status']}`；错误：{item['error'] or '无'}")
        for observation in item.get("observations") or []:
            command = " ".join(str(part) for part in observation.get("command") or [])
            lines.append(f"  - 节点 `{observation.get('node')}`；命令 `{command}`；退出码 `{observation.get('exit_code')}`；"
                         f"采集错误：{observation.get('error') or '无'}")
    lines += ["", "## 模型原始诊断", "", f"`{json.dumps(trace.get('model_raw_final'), ensure_ascii=False)}`", "",
              "## 保护层修改", ""]
    lines += [f"- `{item['field']}`：{item['reason']}" for item in trace.get("guardrail_changes") or []] or ["- 无。"]
    lines += ["", "保护层补入的已验证证据："]
    lines += [f"- `{item['evidence_id']}`：{item['requirement']}；来源 `{item['source_tool']}` "
              f"{json.dumps(item['arguments'], ensure_ascii=False)}；{item['reason']}"
              for item in trace.get("evidence_additions") or []] or ["- 无。"]
    lines += ["", "## 最终诊断", "", f"状态：`{final['status']}`", f"结论：{final['conclusion']}",
              f"引用：{', '.join(final['evidence_ids']) or '无'}",
              f"结构化根因：`{json.dumps(final.get('root_cause'), ensure_ascii=False)}`", "", "实测事实："]
    lines += [f"- {item}" for item in final.get("observed_facts") or []] or ["- 无。"]
    lines += ["", "推断影响："]
    lines += [f"- {item}" for item in final.get("inferred_impacts") or []] or ["- 无。"]
    lines += ["", "缺失或未执行检查："]
    lines += [f"- {item}" for item in final["missing_information"]] or ["- 无。"]
    lines += ["", f"修复建议：{final['repair_suggestion']}", "", "## Token 用量", "",
              f"每次请求：`{json.dumps(trace['usage_per_request'], ensure_ascii=False)}`",
              f"累计：`{json.dumps(trace['usage_total'], ensure_ascii=False)}`", "",
              "原始工具输出、命令错误和每次返回的 evidence_id 见同目录 `trace.json`。", ""]
    return "\n".join(lines)
