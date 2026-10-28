/** A strict read/convert boundary for saved Agent traces. No diagnosis runs here. */

export type JsonRecord = Record<string, unknown>;

export interface CaseSummary {
  id: string;
  title: string;
  provenance: string;
  run_mode: "live" | "replay";
  started_at_utc: string | null;
  path: string;
  source_label: string;
}

export interface CaseIndex {
  schema_version: 1;
  cases: CaseSummary[];
}

export interface Observation {
  node?: string;
  id?: string;
  command?: string[];
  started_at_utc?: string | null;
  ended_at_utc?: string | null;
  exit_code?: number | null;
  stdout?: string;
  stderr?: string;
  error?: string | null;
}

export interface Evidence {
  evidence_id: string;
  collected_at_utc?: string | null;
  tool: string;
  arguments: JsonRecord;
  status: string;
  data?: JsonRecord | null;
  observations?: Observation[];
  error?: string | null;
}

export interface ToolResult {
  tool_call_id: string;
  name: string;
  arguments: JsonRecord;
  result: Evidence;
}

export interface Round {
  round: number;
  model_message: {
    content?: string | null;
    tool_calls?: Array<{
      id: string;
      function: { name: string; arguments: string };
    }>;
  };
  tool_results: ToolResult[];
  usage?: JsonRecord | null;
}

export interface Diagnosis {
  status: string;
  conclusion?: string;
  evidence_ids?: string[];
  missing_information?: string[];
  repair_suggestion?: string;
  root_cause?: JsonRecord | null;
  observed_facts?: string[];
  inferred_impacts?: string[];
  evidence_additions?: Array<{
    evidence_id: string;
    requirement?: string;
    reason?: string;
    source_tool?: string;
    arguments?: JsonRecord;
  }>;
}

export interface Trace {
  schema_version: 1 | 2;
  mode: "live" | "replay";
  task?: string;
  started_at_utc?: string;
  finished_at_utc?: string;
  model_requested?: string;
  model_reported?: string | null;
  initial_topology: {
    lab?: string;
    nodes: string[];
    link?: Array<{ node: string; interface: string }>;
  };
  rounds: Round[];
  evidence: Record<string, Evidence>;
  model_raw_final?: Diagnosis | null;
  final?: Diagnosis | null;
  guardrail_changes?: Array<{
    field: string;
    model_value?: unknown;
    final_value?: unknown;
    reason?: string;
  }>;
  evidence_additions?: Diagnosis["evidence_additions"];
  tool_calls_executed?: number;
  elapsed_seconds?: number;
  usage_total?: {
    prompt_tokens?: number;
    completion_tokens?: number;
    total_tokens?: number;
    requests_with_usage?: number;
  };
}

export interface DemoCase {
  schema_version: 1;
  case_id: string;
  title: string;
  provenance: string;
  source_label: string;
  source_trace_sha256: string;
  redactions?: string[];
  judge?: { verdict: string; problems?: string[] };
  trace: Trace;
}

export type ReplayStep =
  | {
      kind: "tool";
      key: string;
      round: number;
      item: ToolResult;
      evidenceId: string | null;
    }
  | { kind: "final"; key: "final"; round: number };

export interface RouterView {
  node: string;
  interface: string;
  linkAddress: string | null;
  loopbackAddress: string | null;
  admin: "unknown" | "up" | "down";
  ospfArea: string | null;
  ospfState: "unknown" | "enabled" | "not-enabled";
  neighbor: "unknown" | "full" | "not-full";
  evidenceIds: string[];
}

function record(value: unknown): value is JsonRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function fail(message: string): never {
  throw new Error(`轨迹格式不兼容：${message}`);
}

function stable(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(stable);
  if (record(value))
    return Object.fromEntries(
      Object.keys(value)
        .sort()
        .map((key) => [key, stable(value[key])]),
    );
  return value;
}

function sameJson(left: unknown, right: unknown): boolean {
  return JSON.stringify(stable(left)) === JSON.stringify(stable(right));
}

function escapeRegex(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

export function parseIndex(input: unknown): CaseIndex {
  if (
    !record(input) ||
    input.schema_version !== 1 ||
    !Array.isArray(input.cases)
  )
    fail("案例索引 schema_version 或 cases 无效");
  const ids = new Set<string>();
  for (const item of input.cases) {
    if (
      !record(item) ||
      typeof item.id !== "string" ||
      !/^[a-z0-9-]+$/.test(item.id) ||
      typeof item.title !== "string" ||
      typeof item.provenance !== "string" ||
      !["live", "replay"].includes(String(item.run_mode)) ||
      typeof item.path !== "string" ||
      item.path !== `cases/${item.id}.json` ||
      ids.has(item.id)
    )
      fail("案例条目无效或路径不在演示白名单内");
    ids.add(item.id);
  }
  return input as unknown as CaseIndex;
}

export function parseCase(input: unknown, expectedId: string): DemoCase {
  if (
    !record(input) ||
    input.schema_version !== 1 ||
    input.case_id !== expectedId ||
    !record(input.trace)
  )
    fail("案例封装或案例 ID 不匹配");
  const trace = input.trace;
  if (trace.schema_version !== 1 && trace.schema_version !== 2)
    fail(`未知 trace schema_version ${String(trace.schema_version)}`);
  if (
    !["live", "replay"].includes(String(trace.mode)) ||
    !record(trace.initial_topology) ||
    !Array.isArray(trace.initial_topology.nodes) ||
    !Array.isArray(trace.rounds) ||
    !record(trace.evidence)
  )
    fail("轨迹主体缺少必需字段");
  const evidence = trace.evidence;
  const seen = new Set<string>();
  for (const round of trace.rounds) {
    if (
      !record(round) ||
      !Number.isInteger(round.round) ||
      !Array.isArray(round.tool_results) ||
      !record(round.model_message)
    )
      fail("轮次记录无效");
    const calls = Array.isArray(round.model_message.tool_calls)
      ? round.model_message.tool_calls
      : [];
    for (const item of round.tool_results) {
      if (
        !record(item) ||
        typeof item.tool_call_id !== "string" ||
        typeof item.name !== "string" ||
        !record(item.arguments) ||
        !record(item.result)
      )
        fail("工具调用记录无效");
      const call = calls.find(
        (candidate) => record(candidate) && candidate.id === item.tool_call_id,
      );
      if (
        !record(call) ||
        !record(call.function) ||
        call.function.name !== item.name
      )
        fail("工具结果与模型 tool_calls 不匹配");
      let modelArgs: unknown;
      try {
        modelArgs = JSON.parse(String(call.function.arguments));
      } catch {
        fail("模型工具参数不是 JSON");
      }
      if (!sameJson(modelArgs, item.arguments))
        fail("工具结果参数与模型请求不匹配");
      const result = item.result;
      if (
        result.evidence_id === undefined &&
        result.status === "limit_exceeded"
      )
        continue;
      if (
        typeof result.evidence_id !== "string" ||
        !record(evidence[result.evidence_id]) ||
        result.tool !== item.name ||
        !sameJson(result.arguments, item.arguments) ||
        !sameJson(evidence[result.evidence_id], result)
      )
        fail("证据 ID、工具或原始结果不属于该运行");
      if (seen.has(result.evidence_id)) fail("同一运行的证据 ID 被重复使用");
      seen.add(result.evidence_id);
    }
  }
  for (const [id, value] of Object.entries(evidence)) {
    if (!record(value) || value.evidence_id !== id || !seen.has(id))
      fail("证据表含孤立或不匹配的条目");
  }
  for (const diagnosis of [trace.model_raw_final, trace.final]) {
    if (diagnosis === null || diagnosis === undefined) continue;
    if (!record(diagnosis) || typeof diagnosis.status !== "string")
      fail("结论字段无效");
    if (
      diagnosis === trace.final &&
      Array.isArray(diagnosis.evidence_ids) &&
      diagnosis.evidence_ids.some(
        (id) => typeof id !== "string" || !record(evidence[id]),
      )
    )
      fail("最终结论引用了本运行不存在的证据");
  }
  return input as unknown as DemoCase;
}

export function stepsOf(demo: DemoCase): ReplayStep[] {
  const steps: ReplayStep[] = [];
  for (const round of demo.trace.rounds) {
    for (const item of round.tool_results) {
      steps.push({
        kind: "tool",
        key: `${round.round}-${item.tool_call_id}`,
        round: round.round,
        item,
        evidenceId: item.result.evidence_id ?? null,
      });
    }
  }
  steps.push({
    kind: "final",
    key: "final",
    round: demo.trace.rounds.at(-1)?.round ?? 0,
  });
  return steps;
}

export function visibleEvidence(
  demo: DemoCase,
  cursor: number,
): Record<string, Evidence> {
  const visible: Record<string, Evidence> = {};
  for (const step of stepsOf(demo).slice(0, cursor)) {
    if (step.kind === "tool" && step.evidenceId)
      visible[step.evidenceId] = step.item.result;
  }
  return visible;
}

export function topologyAt(demo: DemoCase, cursor: number): RouterView[] {
  const initial = demo.trace.initial_topology;
  const nodes = initial.nodes.map(
    (node) =>
      ({
        node,
        interface:
          initial.link?.find((link) => link.node === node)?.interface ?? "未知",
        linkAddress: null,
        loopbackAddress: null,
        admin: "unknown",
        ospfArea: null,
        ospfState: "unknown",
        neighbor: "unknown",
        evidenceIds: [],
      }) as RouterView,
  );
  const byNode = new Map(nodes.map((node) => [node.node, node]));
  for (const step of stepsOf(demo).slice(0, cursor)) {
    if (step.kind !== "tool") continue;
    const result = step.item.result;
    if (result.status !== "ok") continue; // A failed collection is never a device fault.
    const id = result.evidence_id;
    if (
      step.item.name === "query_topology" &&
      record(result.data) &&
      record(result.data.nodes)
    ) {
      for (const [name, info] of Object.entries(result.data.nodes)) {
        const router = byNode.get(name);
        if (!router || !record(info)) continue;
        router.linkAddress =
          typeof info.interconnect_address === "string"
            ? info.interconnect_address
            : null;
        router.loopbackAddress =
          typeof info.loopback_address === "string"
            ? info.loopback_address
            : null;
        router.interface =
          typeof info.interconnect_interface === "string"
            ? info.interconnect_interface
            : router.interface;
        router.evidenceIds.push(id);
      }
    }
    const node = result.arguments?.node;
    const router = typeof node === "string" ? byNode.get(node) : undefined;
    if (!router) continue;
    if (step.item.name === "query_interface") {
      const output = String(result.data?.frr_output ?? "");
      if (
        new RegExp(
          `Interface\\s+${escapeRegex(router.interface)}\\s+is\\s+down\\b`,
          "i",
        ).test(output) &&
        /Shutdown config is\s+(?!Not specified by CLI|off|disabled)\S+/i.test(
          output,
        )
      )
        router.admin = "down";
      else if (
        new RegExp(
          `Interface\\s+${escapeRegex(router.interface)}\\s+is\\s+up\\b`,
          "i",
        ).test(output)
      )
        router.admin = "up";
    }
    if (step.item.name === "query_ospf_interface") {
      const output = String(
        result.data?.output ?? result.observations?.[0]?.stdout ?? "",
      );
      const area = output.match(/\bArea\s+(\d+\.\d+\.\d+\.\d+)\b/);
      if (area) {
        router.ospfArea = area[1];
        router.ospfState = "enabled";
      } else if (/OSPF (?:not enabled|is not enabled)/i.test(output))
        router.ospfState = "not-enabled";
    }
    if (step.item.name === "query_neighbors") {
      const output = String(
        result.data?.output ?? result.observations?.[0]?.stdout ?? "",
      );
      router.neighbor = /\bFull(?:\/|\b)/i.test(output) ? "full" : "not-full";
    }
    router.evidenceIds.push(id);
  }
  return nodes;
}

export function formatTime(value: string | null | undefined): string {
  if (!value) return "未记录";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? "未记录"
    : date.toLocaleString("zh-CN", { hour12: false });
}

export const statusText: Record<string, string> = {
  normal: "观测正常",
  ospf_area_mismatch: "OSPF 区域不一致",
  interface_admin_down: "接口管理关闭",
  ospf_passive: "OSPF passive",
  insufficient_evidence: "证据不足",
  collection_failure: "采集失败",
  incomplete: "诊断未完成",
  api_error: "模型接口错误",
  ok: "成功",
  unavailable: "数据不可用",
  invalid_argument: "参数无效",
  limit_exceeded: "达到上限",
  collection_failed: "采集失败",
};
