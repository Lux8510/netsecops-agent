import { useEffect, useRef, useState } from "react";
import { statusText, type Diagnosis, type Evidence, type Trace } from "./trace";

interface Capabilities {
  schema_version: 1;
  demo_available: boolean;
  live_available: boolean;
  session_token: string;
}

interface ProgressEvent {
  schema_version: 1;
  kind: string;
  round?: number;
  name?: string;
  arguments?: Record<string, unknown>;
  result?: Evidence;
  tool_calls?: Array<{
    id: string;
    function: { name: string; arguments: string };
  }>;
  trace?: Trace;
  message?: string;
}

function evidenceText(evidence: Evidence): string {
  const lines = [
    `工具：${evidence.tool}  ·  证据：${evidence.evidence_id}`,
    `状态：${statusText[evidence.status] ?? evidence.status}`,
    `参数：${JSON.stringify(evidence.arguments)}`,
    `工具错误：${evidence.error ?? "无"}`,
  ];
  for (const item of evidence.observations ?? []) {
    lines.push(
      `节点：${item.node ?? "未知"}  ·  退出码：${item.exit_code ?? "未记录"}`,
      `命令：${item.command?.join(" ") ?? "未记录"}`,
      `STDOUT：\n${item.stdout ?? ""}`,
      `STDERR：\n${item.stderr ?? ""}`,
      `采集错误：${item.error ?? "无"}`,
    );
  }
  return lines.join("\n");
}

function diagnosisSummary(value: Diagnosis | null | undefined): string {
  if (!value) return "未记录";
  return `${statusText[value.status] ?? value.status} · ${value.conclusion ?? "无结论文本"}`;
}

export default function LiveProgress() {
  const [expanded, setExpanded] = useState(false);
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null);
  const [loading, setLoading] = useState(false);
  const [connectionError, setConnectionError] = useState<string | null>(null);
  const [events, setEvents] = useState<ProgressEvent[]>([]);
  const [running, setRunning] = useState(false);
  const [trace, setTrace] = useState<Trace | null>(null);
  const [selectedEvidence, setSelectedEvidence] = useState<string | null>(null);
  const stream = useRef<EventSource | null>(null);

  useEffect(() => {
    if (!expanded) return;
    const controller = new AbortController();
    setLoading(true);
    fetch("/api/capabilities", { signal: controller.signal })
      .then((response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return response.json();
      })
      .then((value: Capabilities) => {
        if (value.schema_version !== 1 || !value.session_token)
          throw new Error("事件服务格式不兼容");
        setCapabilities(value);
        setConnectionError(null);
      })
      .catch((error) => {
        if (!controller.signal.aborted)
          setConnectionError(`事件服务未就绪：${String(error)}`);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [expanded]);

  useEffect(() => () => stream.current?.close(), []);

  async function start(mode: "demo" | "live") {
    if (!capabilities || running) return;
    stream.current?.close();
    setEvents([]);
    setTrace(null);
    setSelectedEvidence(null);
    setConnectionError(null);
    setRunning(true);
    try {
      const response = await fetch("/api/runs", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-NetSecOps-Session": capabilities.session_token,
        },
        body: JSON.stringify({ mode }),
      });
      if (!response.ok) throw new Error(`无法启动（HTTP ${response.status}）`);
      const started = await response.json();
      if (started.schema_version !== 1 || typeof started.run_id !== "string")
        throw new Error("运行响应格式不兼容");
      const source = new EventSource(`/api/runs/${started.run_id}/events`);
      stream.current = source;
      for (const kind of [
        "started",
        "model_response",
        "tool_result",
        "completed",
        "run_error",
      ]) {
        source.addEventListener(kind, (message) => {
          try {
            const event = JSON.parse((message as MessageEvent).data) as ProgressEvent;
            if (event.schema_version !== 1 || event.kind !== kind)
              throw new Error("事件 schema 不兼容");
            setEvents((current) => [...current, event]);
            if (kind === "completed") {
              if (event.trace) setTrace(event.trace);
              setRunning(false);
              source.close();
            } else if (kind === "run_error") {
              setConnectionError(event.message ?? "运行未完成");
              setRunning(false);
              source.close();
            }
          } catch (error) {
            setConnectionError(`事件数据不可用：${String(error)}`);
            setRunning(false);
            source.close();
          }
        });
      }
      source.onerror = () => {
        setConnectionError("事件流中断；本次诊断结果不能据此确认。");
        setRunning(false);
        source.close();
      };
    } catch (error) {
      setConnectionError(String(error));
      setRunning(false);
    }
  }

  function downloadTrace() {
    if (!trace) return;
    const objectUrl = URL.createObjectURL(
      new Blob([JSON.stringify(trace, null, 2)], { type: "application/json" }),
    );
    const link = document.createElement("a");
    link.href = objectUrl;
    link.download = "netsecops-current-run-trace.json";
    link.click();
    URL.revokeObjectURL(objectUrl);
  }

  const resultEvents = events.filter(
    (event): event is ProgressEvent & { result: Evidence } =>
      event.kind === "tool_result" && Boolean(event.result),
  );
  const selected = resultEvents.find(
    (event) => event.result.evidence_id === selectedEvidence,
  )?.result;

  return (
    <details
      className="progress-panel"
      onToggle={(event) => setExpanded(event.currentTarget.open)}
    >
      <summary>可选：查看本次诊断事件流（需另启本机服务）</summary>
      <div className="progress-content">
        <p>
          默认演示使用 mock 模型与固定观测，只验证事件链路，不代表真实模型自主决策。现场按钮仅在
          WSL 服务明确启用后出现，会调用 DeepSeek 并使用现有只读工具和原调用限制。
        </p>
        {loading && <p>正在连接本机事件服务…</p>}
        {connectionError && (
          <p role="alert" className="alert alert-error">
            {connectionError}。保存轨迹回放仍可独立使用。
          </p>
        )}
        {capabilities && (
          <div className="progress-actions">
            <button disabled={running} onClick={() => start("demo")}>
              {running ? "诊断进行中…" : "运行 mock 事件演示"}
            </button>
            {capabilities.live_available && (
              <button disabled={running} onClick={() => start("live")}>
                发起一次现场只读诊断（调用 DeepSeek）
              </button>
            )}
            {trace && <button onClick={downloadTrace}>下载本次轨迹 JSON</button>}
          </div>
        )}
        {events.length > 0 && (
          <div className="progress-grid">
            <div>
              <h3>本次事件</h3>
              <ol className="progress-events">
                {events.map((event, index) => (
                  <li key={`${event.kind}-${index}`}>
                    {event.kind === "started" && "诊断开始"}
                    {event.kind === "model_response" && (
                      <>
                        第 {event.round} 轮模型响应：
                        {event.tool_calls?.length
                          ? event.tool_calls.map((call) => call.function.name).join("、")
                          : "最终文本"}
                      </>
                    )}
                    {event.kind === "tool_result" && event.result && (
                      <button
                        onClick={() =>
                          setSelectedEvidence(event.result?.evidence_id ?? null)
                        }
                      >
                        {event.name} {JSON.stringify(event.arguments)} →{" "}
                        {event.result.evidence_id} /{" "}
                        {statusText[event.result.status] ?? event.result.status}
                      </button>
                    )}
                    {event.kind === "completed" && "保护层已完成最终校验"}
                    {event.kind === "run_error" && "运行未完成"}
                  </li>
                ))}
              </ol>
            </div>
            <div>
              <h3>本次证据</h3>
              {selected ? (
                <pre className="progress-evidence">{evidenceText(selected)}</pre>
              ) : (
                <p>点击本次工具返回的证据 ID 查看原始输出。</p>
              )}
            </div>
          </div>
        )}
        {trace && (
          <div className="progress-final">
            <h3>原始结论与证据校验</h3>
            <p>模型原始：{diagnosisSummary(trace.model_raw_final)}</p>
            <p>校验后：{diagnosisSummary(trace.final)}</p>
            <p>
              引用：{trace.final?.evidence_ids?.join("、") || "无"}；保护层补引：
              {trace.evidence_additions?.map((item) => item.evidence_id).join("、") ||
                "无"}
            </p>
            <p>
              实际模型：{trace.model_reported ?? "未记录"}；输入 / 输出 Token：
              {trace.usage_total?.requests_with_usage
                ? `${trace.usage_total.prompt_tokens} / ${trace.usage_total.completion_tokens}`
                : trace.model_reported === "mock-offline"
                  ? "未记录（mock 模型）"
                  : "未记录"}
            </p>
            <p>本次最终记录可下载；此区不读取任何历史故障标签。</p>
          </div>
        )}
      </div>
    </details>
  );
}
