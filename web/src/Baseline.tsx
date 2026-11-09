import { useState } from "react";
type Evidence = { evidence_id: string; node: string; command: string[]; collected_at: string; exit_code: number | null; stdout: string; stderr: string };
type Result = { schema_version: number; mode: string; run_id: string; collected_at: string; status: string; actual_primary_path: string[] | null; checks: { name: string; status: string; evidence_ids: string[] }[]; evidence: Evidence[] };
export default function Baseline() {
  const [result, setResult] = useState<Result | null>(null);
  const [error, setError] = useState("");
  async function refresh() {
    try {
      const response = await fetch("/baseline.json", { cache: "no-store" });
      if (!response.ok) throw new Error("尚无本机基线，请先运行 python3 -m v1.baseline start");
      const value = await response.json() as Result;
      if (value.schema_version !== 1 || value.mode !== "live-read-only" || !Array.isArray(value.checks) || !Array.isArray(value.evidence)) throw new Error("基线格式不兼容");
      setResult(value); setError("");
    } catch (e) { setResult(null); setError(String(e)); }
  }
  return <section className="progress-panel" aria-label="六节点真实基线">
    <h2>六节点真实基线</h2>
    <p>本机最近一次只读采集快照；点击刷新读取文件，不执行网络命令或调用模型。页面加载后不会自动判定当前网络健康。</p>
    <button onClick={refresh}>读取最近一次基线</button>
    {error && <p role="alert">{error}</p>}
    {result && <>
      <p>采集时间：{result.collected_at} · 结果：{result.status} · 运行：{result.run_id}</p>
      <p>实际路由证据支持的主路径：{result.actual_primary_path?.join(" → ") ?? "证据不足，无法确认"}</p>
      <table><thead><tr><th>检查项</th><th>结果</th><th>证据</th></tr></thead><tbody>{result.checks.map(c => <tr key={c.name}><td>{c.name}</td><td>{c.status}</td><td>{c.evidence_ids.map(id => <a key={id} href={`#baseline-${id}`}>{id} </a>)}</td></tr>)}</tbody></table>
      {result.evidence.map(e => <details key={e.evidence_id} id={`baseline-${e.evidence_id}`}><summary>{e.evidence_id} · {e.node} · 退出码 {e.exit_code ?? "未执行成功"}</summary><pre>{JSON.stringify(e, null, 2)}</pre></details>)}
    </>}
  </section>;
}
