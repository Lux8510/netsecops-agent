import { useEffect, useMemo, useState } from "react";
import LiveProgress from "./LiveProgress";
import {
  Background,
  Controls,
  Position,
  ReactFlow,
  type Edge,
  type Node,
} from "@xyflow/react";
import {
  formatTime,
  parseCase,
  parseIndex,
  statusText,
  stepsOf,
  topologyAt,
  visibleEvidence,
  type CaseIndex,
  type CaseSummary,
  type DemoCase,
  type Evidence,
  type ReplayStep,
} from "./trace";

function valueOrUnknown(value: string | null | undefined): string {
  return value || "未知";
}

function textOrUnrecorded(value: unknown): string {
  return typeof value === "string" && value.trim() ? value : "未记录";
}

function tone(status: string | undefined): string {
  if (!status) return "neutral";
  if (status === "normal" || status === "ok") return "good";
  if (
    status === "collection_failure" ||
    status === "collection_failed" ||
    status === "api_error"
  )
    return "error";
  if (
    status === "insufficient_evidence" ||
    status === "incomplete" ||
    status === "limit_exceeded"
  )
    return "caution";
  return "fault";
}

function Badge({
  status,
  children,
}: {
  status?: string;
  children: React.ReactNode;
}) {
  return <span className={`badge badge-${tone(status)}`}>{children}</span>;
}

function EvidenceLink({
  id,
  available,
  onClick,
}: {
  id: string;
  available: boolean;
  onClick: (id: string) => void;
}) {
  return (
    <button
      type="button"
      className="evidence-link"
      disabled={!available}
      onClick={() => onClick(id)}
    >
      {id}
    </button>
  );
}

export function EvidenceDetails({ evidence }: { evidence: Evidence | null }) {
  if (!evidence)
    return (
      <div className="empty-panel">
        <span className="empty-mark">◌</span>
        <p>选择时间线中的工具调用，查看对应的原始证据。</p>
      </div>
    );
  return (
    <div className="evidence-body">
      <div className="evidence-head">
        <strong>{evidence.evidence_id}</strong>
        <Badge status={evidence.status}>
          {statusText[evidence.status] ?? evidence.status}
        </Badge>
      </div>
      <div className="fact-grid">
        <div>
          <small>工具</small>
          <code>{evidence.tool}</code>
        </div>
        <div>
          <small>采集时间</small>
          <span>{formatTime(evidence.collected_at_utc)}</span>
        </div>
        <div className="wide">
          <small>参数</small>
          <code>{JSON.stringify(evidence.arguments, null, 2)}</code>
        </div>
        <div className="wide">
          <small>工具错误</small>
          <span>{textOrUnrecorded(evidence.error)}</span>
        </div>
      </div>
      {(evidence.observations ?? []).length === 0 && (
        <p className="muted">此工具没有原始命令记录。</p>
      )}
      {(evidence.observations ?? []).map((observation, index) => (
        <div className="observation" key={`${evidence.evidence_id}-${index}`}>
          <div className="observation-title">
            原始观测 {index + 1}{" "}
            <span>
              {observation.node ?? "节点未记录"} ·{" "}
              {observation.id ?? "命令未记录"}
            </span>
          </div>
          <div className="fact-grid">
            <div>
              <small>退出码</small>
              <span>{observation.exit_code ?? "未记录"}</span>
            </div>
            <div>
              <small>开始 / 结束</small>
              <span>
                {formatTime(observation.started_at_utc)} /{" "}
                {formatTime(observation.ended_at_utc)}
              </span>
            </div>
            <div className="wide">
              <small>实际命令</small>
              <code>{observation.command?.join(" ") || "未记录"}</code>
            </div>
            <div className="wide">
              <small>采集错误</small>
              <span>{textOrUnrecorded(observation.error)}</span>
            </div>
          </div>
          <div className="raw-label">stdout</div>
          <pre className="raw-output">{observation.stdout ?? ""}</pre>
          <div className="raw-label">stderr</div>
          <pre className="raw-output">{observation.stderr ?? ""}</pre>
        </div>
      ))}
    </div>
  );
}

function DiagnosisPanel({
  demo,
  visible,
  onEvidence,
}: {
  demo: DemoCase;
  visible: Record<string, Evidence>;
  onEvidence: (id: string) => void;
}) {
  const raw = demo.trace.model_raw_final;
  const final = demo.trace.final;
  const changes = demo.trace.guardrail_changes ?? [];
  const additions =
    demo.trace.evidence_additions ?? final?.evidence_additions ?? [];
  const rawMissing = new Set(raw?.missing_information ?? []);
  return (
    <section className="diagnosis-section">
      <div className="section-heading">
        <div>
          <span className="eyebrow">DIAGNOSIS</span>
          <h2>结论与校验</h2>
        </div>
        <Badge status={final?.status}>
          {statusText[final?.status ?? ""] ?? final?.status ?? "未完成"}
        </Badge>
      </div>
      <div className="diagnosis-pair">
        <article className="diagnosis-card raw-card">
          <span className="card-kicker">01 · 模型原始结果</span>
          <h3>
            {statusText[raw?.status ?? ""] ?? raw?.status ?? "无有效输出"}
          </h3>
          <p>{textOrUnrecorded(raw?.conclusion)}</p>
          <div className="citation-row">
            <small>模型原始引用</small>
            <div>
              {raw?.evidence_ids?.length ? (
                raw.evidence_ids.map((id) => (
                  <EvidenceLink
                    key={id}
                    id={id}
                    available={Boolean(visible[id])}
                    onClick={onEvidence}
                  />
                ))
              ) : (
                <span>无</span>
              )}
            </div>
          </div>
          <details className="raw-json">
            <summary>查看模型最终原始响应</summary>
            <pre>{textOrUnrecorded(demo.trace.rounds.at(-1)?.model_message.content)}</pre>
          </details>
        </article>
        <article className="diagnosis-card final-card">
          <span className="card-kicker">02 · 系统校验后</span>
          <h3>
            {statusText[final?.status ?? ""] ?? final?.status ?? "无有效输出"}
          </h3>
          <p>{textOrUnrecorded(final?.conclusion)}</p>
          <div className="citation-row">
            <small>最终证据引用</small>
            <div>
              {final?.evidence_ids?.length ? (
                final.evidence_ids.map((id) => (
                  <EvidenceLink
                    key={id}
                    id={id}
                    available={Boolean(visible[id])}
                    onClick={onEvidence}
                  />
                ))
              ) : (
                <span>无</span>
              )}
            </div>
          </div>
          <details className="raw-json">
            <summary>查看保护后结构化结果</summary>
            <pre>{JSON.stringify(final ?? null, null, 2)}</pre>
          </details>
        </article>
      </div>
      <div className="finding-grid">
        <div>
          <h4>实测事实</h4>
          <ul>
            {final?.observed_facts?.length ? (
              final.observed_facts.map((item, i) => <li key={i}>{item}</li>)
            ) : (
              <li className="muted">未记录可确认事实</li>
            )}
          </ul>
        </div>
        <div>
          <h4>推测影响</h4>
          <ul>
            {final?.inferred_impacts?.length ? (
              final.inferred_impacts.map((item, i) => <li key={i}>{item}</li>)
            ) : (
              <li className="muted">未记录</li>
            )}
          </ul>
        </div>
        <div>
          <h4>缺失与限制</h4>
          <ul>
            {final?.missing_information?.length ? (
              final.missing_information.map((item, i) => (
                <li key={i}>
                  {item}
                  {rawMissing.has(item) && (
                    <span className="source-note">
                      模型原文，未作为独立证据验证
                    </span>
                  )}
                </li>
              ))
            ) : (
              <li className="muted">未记录</li>
            )}
          </ul>
        </div>
      </div>
      <div className="audit-grid">
        <div>
          <h4>
            保护层改动 <span>{changes.length}</span>
          </h4>
          {changes.length ? (
            changes.map((item, i) => (
              <div className="audit-item" key={i}>
                <code>{item.field}</code>
                <p>{item.reason ?? "证据校验"}</p>
                <small>
                  原始：{JSON.stringify(item.model_value) ?? "未记录"}
                </small>
                <small>
                  校验后：{JSON.stringify(item.final_value) ?? "未记录"}
                </small>
              </div>
            ))
          ) : (
            <p className="muted">此案例未记录字段改动。</p>
          )}
        </div>
        <div>
          <h4>
            补入证据 <span>{additions.length}</span>
          </h4>
          {additions.length ? (
            additions.map((item, i) => (
              <div className="audit-item" key={i}>
                <EvidenceLink
                  id={item.evidence_id}
                  available={Boolean(visible[item.evidence_id])}
                  onClick={onEvidence}
                />
                <p>{item.reason ?? "理由未记录"}</p>
                <small>
                  来源：{item.source_tool ?? "未记录"} ·{" "}
                  {JSON.stringify(item.arguments ?? {})}
                </small>
              </div>
            ))
          ) : (
            <p className="muted">本案例无补引。</p>
          )}
        </div>
      </div>
      <div className="repair-note">
        <strong>修复建议（仅建议，不执行）</strong>
        <p>{textOrUnrecorded(final?.repair_suggestion)}</p>
      </div>
      <div className="judge-note">
        保存的独立评判：
        <Badge
          status={demo.judge?.verdict === "PASS" ? "ok" : "collection_failure"}
        >
          {demo.judge?.verdict ?? "未记录"}
        </Badge>
        <span>这是来源运行的判定，不是网页重新评判。</span>
      </div>
    </section>
  );
}

export default function App() {
  const [index, setIndex] = useState<CaseIndex | null>(null);
  const [indexError, setIndexError] = useState<string | null>(null);
  const [caseId, setCaseId] = useState<string | null>(null);
  const [demo, setDemo] = useState<DemoCase | null>(null);
  const [caseError, setCaseError] = useState<string | null>(null);
  const [loadingCase, setLoadingCase] = useState(false);
  const [cursor, setCursor] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [selectedEvidence, setSelectedEvidence] = useState<string | null>(null);
  const [nodeFilter, setNodeFilter] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    fetch("/index.json", { signal: controller.signal })
      .then((response) => {
        if (!response.ok)
          throw new Error(`案例索引加载失败（HTTP ${response.status}）`);
        return response.json();
      })
      .then((value) => {
        const parsed = parseIndex(value);
        setIndex(parsed);
        setCaseId(parsed.cases[0]?.id ?? null);
      })
      .catch((error) => {
        if (!controller.signal.aborted) setIndexError(String(error));
      });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (!index || !caseId) return;
    const selected = index.cases.find((item) => item.id === caseId);
    if (!selected) return;
    const controller = new AbortController();
    setLoadingCase(true);
    setCaseError(null);
    setDemo(null);
    setCursor(0);
    setPlaying(false);
    setSelectedEvidence(null);
    setNodeFilter(null);
    fetch(`/${selected.path}`, { signal: controller.signal })
      .then((response) => {
        if (!response.ok)
          throw new Error(`案例读取失败（HTTP ${response.status}）`);
        return response.json();
      })
      .then((value) => setDemo(parseCase(value, selected.id)))
      .catch((error) => {
        if (!controller.signal.aborted) setCaseError(String(error));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoadingCase(false);
      });
    return () => controller.abort();
  }, [index, caseId]);

  const steps = useMemo(() => (demo ? stepsOf(demo) : []), [demo]);
  const visible = useMemo(
    () => (demo ? visibleEvidence(demo, cursor) : {}),
    [demo, cursor],
  );
  const routers = useMemo(
    () => (demo ? topologyAt(demo, cursor) : []),
    [demo, cursor],
  );
  const finished = Boolean(demo && cursor === steps.length);
  const selectedCase: CaseSummary | undefined = index?.cases.find(
    (item) => item.id === caseId,
  );

  useEffect(() => {
    if (!playing || !demo) return;
    const timer = window.setTimeout(() => {
      const next = Math.min(cursor + 1, steps.length);
      moveTo(next);
      if (next === steps.length) setPlaying(false);
    }, 950);
    return () => window.clearTimeout(timer);
  }, [playing, demo, steps, cursor]);

  function moveTo(next: number) {
    if (!demo) return;
    const bounded = Math.max(0, Math.min(next, steps.length));
    setCursor(bounded);
    const step = steps[bounded - 1];
    setSelectedEvidence(step?.kind === "tool" ? step.evidenceId : null);
  }

  function jumpToEvidence(id: string) {
    if (!visible[id]) return;
    setSelectedEvidence(id);
    setNodeFilter(null);
    document
      .getElementById("evidence-panel")
      ?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  const flowNodes: Node[] = routers.map((router, indexValue) => ({
    id: router.node,
    position: { x: 40 + indexValue * 365, y: 80 },
    data: {
      label: (
        <div className="router-node">
          <div className="router-node-top">
            <span className="router-icon">▣</span>
            <strong>{router.node}</strong>
            <span>{router.interface}</span>
          </div>
          <div className="router-address">
            链路 {valueOrUnknown(router.linkAddress)}
          </div>
          <div className="router-address">
            Loopback {valueOrUnknown(router.loopbackAddress)}
          </div>
          <div className="router-metrics">
            <span>
              Area <b>{valueOrUnknown(router.ospfArea)}</b>
            </span>
            <span>
              邻居{" "}
              <b>
                {router.neighbor === "full"
                  ? "FULL"
                  : router.neighbor === "not-full"
                    ? "未见 FULL"
                    : "未知"}
              </b>
            </span>
          </div>
          <div className="router-foot">
            管理状态：
            {router.admin === "unknown"
              ? "未知"
              : router.admin === "down"
                ? "已关闭"
                : "启用"}{" "}
            · OSPF：
            {router.ospfState === "unknown"
              ? "未知"
              : router.ospfState === "not-enabled"
                ? "未启用"
                : "已启用"}
          </div>
        </div>
      ),
    },
    draggable: false,
    selectable: true,
    sourcePosition: Position.Right,
    targetPosition: Position.Left,
    style: {
      width: 288,
      padding: 0,
      borderRadius: 16,
      border:
        nodeFilter === router.node ? "2px solid #315fca" : "1px solid #d9e0e7",
      boxShadow: "0 12px 34px rgba(23, 40, 63, 0.06)",
    },
  }));
  const bothAreasKnown =
    routers.length === 2 && routers.every((router) => router.ospfArea);
  const areasDiffer =
    bothAreasKnown && routers[0].ospfArea !== routers[1].ospfArea;
  const flowEdges: Edge[] =
    routers.length === 2
      ? [
          {
            id: "link-r1-r2",
            source: routers[0].node,
            target: routers[1].node,
            label: areasDiffer
              ? "两端 Area 不同 · 错误端未知"
              : bothAreasKnown
                ? "Area 相同"
                : "链路状态未知",
            style: {
              stroke: areasDiffer
                ? "#c48b37"
                : bothAreasKnown
                  ? "#7f9db6"
                  : "#aab6c5",
              strokeWidth: 2,
            },
            labelStyle: {
              fill: areasDiffer ? "#9d6b24" : "#64748b",
              fontSize: 12,
            },
          },
        ]
      : [];
  const shownSteps = steps
    .slice(0, cursor)
    .filter(
      (step): step is Extract<ReplayStep, { kind: "tool" }> =>
        step.kind === "tool",
    )
    .filter(
      (step) =>
        !nodeFilter ||
        step.item.result.observations?.some((obs) => obs.node === nodeFilter) ||
        step.item.arguments.node === nodeFilter,
    );

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark">
            N<span>·</span>
          </span>
          <span>
            NetSecOps <em>WORKBENCH</em>
          </span>
        </div>
        <div className="topbar-right">
          <span className="offline-dot" /> 默认离线回放 · 无需密钥{" "}
          <span className="top-divider" /> v0.7 事件预览
        </div>
      </header>
      <div className="page-intro">
        <div>
          <span className="eyebrow">READ-ONLY NETWORK DIAGNOSIS</span>
          <h1>
            让每一步诊断<span>有据可查。</span>
          </h1>
          <p>
            回放已保存的工具调用，看 Agent
            如何收集证据，以及系统如何约束最终结论。保存轨迹回放不连接模型或实验网络。
          </p>
        </div>
        <div className="intro-tag">双路由器 · OSPF · 只读诊断</div>
      </div>
      <LiveProgress />
      <div className="workspace">
        <aside className="case-sidebar">
          <div className="section-heading">
            <div>
              <span className="eyebrow">CASE LIBRARY</span>
              <h2>案例库</h2>
            </div>
            <span className="count">{index?.cases.length ?? "—"}</span>
          </div>
          {indexError && (
            <div role="alert" className="alert alert-error">
              {indexError}
            </div>
          )}
          {!index && !indexError && <p className="muted">正在加载案例索引…</p>}
          {index && index.cases.length === 0 && (
            <p className="muted">没有可回放的案例。</p>
          )}
          <div className="case-list">
            {index?.cases.map((item, i) => (
              <button
                type="button"
                key={item.id}
                className={`case-card ${item.id === caseId ? "selected" : ""}`}
                onClick={() => setCaseId(item.id)}
              >
                <span className="case-number">
                  {String(i + 1).padStart(2, "0")}
                </span>
                <strong>{item.title}</strong>
                <span className="case-mode">
                  {item.run_mode === "live" ? "当时现场观测" : "冻结观测回放"}
                </span>
                <small>{item.provenance}</small>
              </button>
            ))}
          </div>
          <div className="sidebar-note">
            <strong>数据边界</strong>
            <p>
              案例库包含 1 份历史现场轨迹、4 份冻结观测回放；不含当前实时运行。切换案例会重置回放，证据 ID
              不跨案例复用。可选事件演示另行标记 mock；页面不会注入故障或自动修复。
            </p>
          </div>
        </aside>
        <main className="main-column">
          {loadingCase && (
            <div className="loading-state">正在读取保存的轨迹与原始输出…</div>
          )}
          {caseError && (
            <div className="alert alert-error" role="alert">
              {caseError}
              <p>请检查案例文件与 schema_version；不会静默猜测格式。</p>
            </div>
          )}
          {!loadingCase && !caseError && !demo && (
            <div className="loading-state">请选择一个可用案例。</div>
          )}
          {demo && (
            <>
              <section className="case-header">
                <div>
                  <span className="eyebrow">SELECTED RUN</span>
                  <h2>{demo.title}</h2>
                  <p>
                    {demo.provenance} · {demo.source_label}
                  </p>
                </div>
                <div className="case-header-meta">
                  <Badge status={demo.trace.mode === "live" ? "ok" : undefined}>
                    {demo.trace.mode === "live"
                      ? "当时现场观测"
                      : "保存轨迹回放"}
                  </Badge>
                  <span>
                    {formatTime(
                      demo.trace.started_at_utc ?? selectedCase?.started_at_utc,
                    )}
                  </span>
                </div>
              </section>
              <section className="playback-bar" aria-label="回放控制">
                <div className="playback-controls">
                  <button
                    type="button"
                    className="control-primary"
                    onClick={() => {
                      if (finished) moveTo(0);
                      setPlaying(!playing);
                    }}
                  >
                    {playing
                      ? "Ⅱ  暂停"
                      : finished
                        ? "↺  重新播放"
                        : "▶  播放"}
                  </button>
                  <button
                    type="button"
                    className="control-secondary"
                    disabled={cursor >= steps.length}
                    onClick={() => {
                      setPlaying(false);
                      moveTo(cursor + 1);
                    }}
                  >
                    下一步 →
                  </button>
                  <button
                    type="button"
                    className="control-text"
                    onClick={() => {
                      setPlaying(false);
                      moveTo(0);
                    }}
                  >
                    重新开始
                  </button>
                </div>
                <div className="progress-area">
                  <div className="progress-caption">
                    <span>回放进度</span>
                    <b>
                      {cursor} / {steps.length}
                    </b>
                  </div>
                  <input
                    aria-label="回放进度"
                    type="range"
                    min="0"
                    max={steps.length}
                    value={cursor}
                    onChange={(event) => {
                      setPlaying(false);
                      moveTo(Number(event.target.value));
                    }}
                  />
                  <small>
                    {finished
                      ? "本次记录已结束，结论可见"
                      : "结论与后续观测尚未解锁"}
                  </small>
                </div>
              </section>
              {finished && (
                <section className="decision-strip">
                  <div>
                    <span className="eyebrow">MODEL → VERIFIED</span>
                    <strong>
                      原始：
                      {statusText[demo.trace.model_raw_final?.status ?? ""] ??
                        demo.trace.model_raw_final?.status ??
                        "无结果"}
                    </strong>
                    <span className="decision-arrow">→</span>
                    <strong className="decision-final">
                      校验后：
                      {statusText[demo.trace.final?.status ?? ""] ??
                        demo.trace.final?.status ??
                        "无结果"}
                    </strong>
                  </div>
                  <button
                    type="button"
                    onClick={() =>
                      document
                        .querySelector(".diagnosis-section")
                        ?.scrollIntoView({ behavior: "smooth" })
                    }
                  >
                    查看证据校验详情 ↓
                  </button>
                </section>
              )}
              <section className="topology-section">
                <div className="section-heading">
                  <div>
                    <span className="eyebrow">NETWORK VIEW</span>
                    <h2>网络拓扑</h2>
                  </div>
                  <span className="hint">
                    点击设备筛选证据 · 状态仅来自已回放观测
                  </span>
                </div>
                <div className="topology-canvas">
                  <ReactFlow
                    nodes={flowNodes}
                    edges={flowEdges}
                    fitView
                    fitViewOptions={{ padding: 0.18 }}
                    nodesDraggable={false}
                    nodesConnectable={false}
                    elementsSelectable
                    onNodeClick={(_, node) =>
                      setNodeFilter((current) =>
                        current === node.id ? null : node.id,
                      )
                    }
                    proOptions={{ hideAttribution: false }}
                  >
                    <Background color="#e4e9ef" gap={22} size={1} />
                    <Controls showInteractive={false} />
                  </ReactFlow>
                </div>
                <div className="topology-foot">
                  <span>
                    {areasDiffer
                      ? "已观测：两端 Area 不同；无预期配置依据，错误端未知。"
                      : bothAreasKnown
                        ? "已观测：两端 Area 相同。"
                        : "Area 尚未在两端取得有效证据，链路根因未知。"}
                  </span>
                  {nodeFilter && (
                    <button type="button" onClick={() => setNodeFilter(null)}>
                      清除 {nodeFilter} 筛选 ×
                    </button>
                  )}
                </div>
              </section>
              <section className="timeline-section">
                <div className="section-heading">
                  <div>
                    <span className="eyebrow">TOOL ACTIVITY</span>
                    <h2>工具调用时间线</h2>
                  </div>
                  <span className="hint">仅展示模型 API 实际请求的工具</span>
                </div>
                {shownSteps.length === 0 ? (
                  <div className="empty-timeline">
                    {nodeFilter
                      ? "当前筛选下没有已回放的相关调用。"
                      : "还没有回放工具调用。点击「播放」或「下一步」开始。"}
                  </div>
                ) : (
                  <div className="timeline-list">
                    {shownSteps.map((step) => (
                      <button
                        type="button"
                        key={step.key}
                        className={`timeline-item ${selectedEvidence === step.evidenceId ? "active" : ""}`}
                        onClick={() => {
                          if (step.evidenceId) jumpToEvidence(step.evidenceId);
                        }}
                      >
                        <span className="timeline-point" />
                        <span className="timeline-round">
                          第 {step.round} 轮
                        </span>
                        <span className="timeline-main">
                          <strong>{step.item.name}</strong>
                          <code>{JSON.stringify(step.item.arguments)}</code>
                        </span>
                        <span className="timeline-result">
                          <Badge status={step.item.result.status}>
                            {statusText[step.item.result.status] ??
                              step.item.result.status}
                          </Badge>
                          <b>{step.evidenceId ?? "无证据 ID"}</b>
                          <small>单次耗时：未记录</small>
                        </span>
                      </button>
                    ))}
                  </div>
                )}
              </section>
              {finished ? (
                <DiagnosisPanel
                  demo={demo}
                  visible={visible}
                  onEvidence={jumpToEvidence}
                />
              ) : (
                <section className="locked-final">
                  <span>◌</span>
                  <div>
                    <strong>诊断结论尚未播放到</strong>
                    <p>
                      系统不会提前显示模型原始答案或保护层结果。完成最后一步后可核对两者差异。
                    </p>
                  </div>
                </section>
              )}
            </>
          )}
        </main>
        <aside className="detail-sidebar">
          <section className="detail-panel" id="evidence-panel">
            <div className="section-heading">
              <div>
                <span className="eyebrow">EVIDENCE INSPECTOR</span>
                <h2>证据详情</h2>
              </div>
              <span className="count">{Object.keys(visible).length}</span>
            </div>
            <EvidenceDetails
              evidence={
                selectedEvidence ? (visible[selectedEvidence] ?? null) : null
              }
            />
          </section>
          <section className="run-panel">
            <span className="eyebrow">RUN SUMMARY</span>
            <h2>运行概览</h2>
            {demo ? (
              <div className="summary-list">
                <div>
                  <span>运行模式</span>
                  <strong>
                    {demo.trace.mode === "live"
                      ? "历史现场"
                      : "模型冻结观测回放"}
                  </strong>
                </div>
                <div>
                  <span>模型</span>
                  <strong>
                    {finished
                      ? textOrUnrecorded(
                          demo.trace.model_reported ??
                            demo.trace.model_requested,
                        )
                      : "回放结束后显示"}
                  </strong>
                </div>
                <div>
                  <span>模型请求</span>
                  <strong>
                    {finished ? demo.trace.rounds.length : "回放结束后显示"}
                  </strong>
                </div>
                <div>
                  <span>工具调用</span>
                  <strong>
                    {finished
                      ? (demo.trace.tool_calls_executed ?? "未记录")
                      : "回放结束后显示"}
                  </strong>
                </div>
                <div>
                  <span>总耗时</span>
                  <strong>
                    {finished && typeof demo.trace.elapsed_seconds === "number"
                      ? `${demo.trace.elapsed_seconds} 秒`
                      : finished
                        ? "未记录"
                        : "回放结束后显示"}
                  </strong>
                </div>
                <div>
                  <span>Token · 输入 / 输出</span>
                  <strong>
                    {finished &&
                    typeof demo.trace.usage_total?.prompt_tokens === "number" &&
                    typeof demo.trace.usage_total.completion_tokens === "number"
                      ? `${demo.trace.usage_total.prompt_tokens} / ${demo.trace.usage_total.completion_tokens}`
                      : finished
                        ? "未记录"
                        : "回放结束后显示"}
                  </strong>
                </div>
              </div>
            ) : (
              <p className="muted">等待案例加载。</p>
            )}
            <p className="summary-caveat">
              未记录的指标不估算；不展示准确率或费用。
            </p>
          </section>
        </aside>
      </div>
      <footer>
        NetSecOps Agent · 保存轨迹只读展示 · 不含 API
        Key、命令执行或配置写入能力
      </footer>
    </div>
  );
}
