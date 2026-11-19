#!/usr/bin/env python3
"""Fault-injection matrix and evidence metrics for the read-only OSPF agent.

Offline mode evaluates saved traces against a manifest; it never starts Docker,
Containerlab or a model. Live mode is opt-in (NETSECOPS_LIVE_MATRIX=1), runs on
Linux/WSL2 with the lab up, and is intentionally not part of the offline suite.

Metrics are deliberately conservative:

* root_cause_accuracy  - final status equals the scenario's expected status
* evidence_coverage    - cited evidence covers every required proof for the
                         expected status (missing proof lowers coverage)
* false_confidence     - a definite answer (not abstention) with coverage < 1
* abstention_rate      - final status is insufficient_evidence / collection_failure
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Callable, Iterable

from evidence_semantics import semantic_support
from stats import rate as wilson_rate

LAB_DIR = Path(__file__).resolve().parent
DEFINITE = ("normal", "ospf_area_mismatch", "interface_admin_down", "ospf_passive")
ABSTAIN = ("insufficient_evidence", "collection_failure", "incomplete", "api_error")
EXPECTED = DEFINITE + ABSTAIN


@dataclass(frozen=True)
class Scenario:
    scenario_id: str
    fault: str | None
    target: str | None
    expected_status: str
    injectable: bool
    description: str


SCENARIOS: dict[str, Scenario] = {
    "normal": Scenario("normal", None, None, "normal", False,
                       "健康基线：两端 Area 一致、邻居 FULL、双向 Loopback 可达"),
    "area_mismatch": Scenario("area_mismatch", "area_mismatch", "r2", "ospf_area_mismatch", True,
                              "把 r2 互联接口 OSPF Area 改错"),
    "admin_down": Scenario("admin_down", "admin_down", "r1", "interface_admin_down", True,
                           "管理关闭 r1 互联接口"),
    "ospf_passive": Scenario("ospf_passive", "ospf_passive", "r2", "ospf_passive", True,
                             "在 r2 互联接口启用 OSPF passive"),
}

# Each requirement is one proof the final answer must cite. "node" is optional;
# ping requirements also require a non-empty target.
REQUIREMENTS: dict[str, tuple[dict[str, Any], ...]] = {
    "normal": (
        {"tool": "query_ospf_interface", "node": "r1"},
        {"tool": "query_ospf_interface", "node": "r2"},
        {"tool": "query_neighbors", "node": "r1"},
        {"tool": "query_neighbors", "node": "r2"},
        {"tool": "query_routes", "node": "r1"},
        {"tool": "ping_address", "node": "r1", "target": True},
        {"tool": "ping_address", "node": "r2", "target": True},
    ),
    "ospf_area_mismatch": (
        {"tool": "query_ospf_interface", "node": "r1"},
        {"tool": "query_ospf_interface", "node": "r2"},
        {"tool": "query_neighbors", "node": "r1"},
    ),
    "interface_admin_down": (
        {"tool": "query_interface", "node": "r1"},
        {"tool": "query_neighbors", "node": "r2"},
    ),
    "ospf_passive": (
        {"tool": "query_interface_config", "node": "r2"},
        {"tool": "query_neighbors", "node": "r1"},
    ),
    "insufficient_evidence": (),
    "collection_failure": (),
    "incomplete": (),
    "api_error": (),
}


class MatrixError(ValueError):
    pass


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MatrixError(f"Cannot read {path}: {exc}") from exc


def extract_trace(document: Any) -> dict[str, Any]:
    """Accept a case wrapper, a run record with trace, or a raw trace."""
    if not isinstance(document, dict):
        raise MatrixError("Trace document must be an object")
    for key in ("trace", "agent_trace"):
        if isinstance(document.get(key), dict):
            return document[key]
    if "final" in document and "evidence" in document:
        return document
    raise MatrixError("Document does not contain an agent trace")


def cited_ids(trace: dict[str, Any]) -> list[str]:
    final = trace.get("final") or {}
    ids = final.get("evidence_ids")
    return [item for item in ids if isinstance(item, str)] if isinstance(ids, list) else []


def _matches(requirement: dict[str, Any], evidence: dict[str, Any]) -> bool:
    if evidence.get("tool") != requirement["tool"]:
        return False
    arguments = evidence.get("arguments") or {}
    node = requirement.get("node")
    if node and arguments.get("node") != node:
        return False
    if requirement.get("target") and not arguments.get("target"):
        return False
    return True


def coverage(trace: dict[str, Any], expected: str) -> tuple[float, list[str]]:
    required = REQUIREMENTS.get(expected, ())
    if not required:
        return 1.0, []
    evidence = trace.get("evidence") or {}
    cited = [evidence[item] for item in cited_ids(trace) if item in evidence]
    missing: list[str] = []
    for requirement in required:
        if not any(_matches(requirement, item) for item in cited):
            label = requirement["tool"] + ("@" + requirement["node"] if requirement.get("node") else "")
            missing.append(label)
    return (len(required) - len(missing)) / len(required), missing


def trace_metrics(trace: dict[str, Any], expected: str) -> dict[str, Any]:
    if expected not in EXPECTED:
        raise MatrixError(f"Unsupported expected status: {expected}")
    final = trace.get("final") or {}
    status = final.get("status")
    ratio, missing = coverage(trace, expected)
    definite = status in DEFINITE
    evidence = trace.get("evidence") or {}
    unknown = [item for item in cited_ids(trace) if item not in evidence]
    semantic = semantic_support(trace, expected)
    return {
        "status": status,
        "expected": expected,
        "root_cause_match": status == expected,
        "evidence_coverage": round(ratio, 4),
        "missing_proofs": missing,
        "definite": definite,
        "semantic_supported": semantic["supported"],
        "semantic_contradictions": semantic["contradictions"],
        "ambiguous_detected": semantic["ambiguous_faults"],
        "false_confidence": bool(definite and (ratio < 1.0 or not semantic["supported"])),
        "wrong_confident": bool(definite and status != expected),
        "abstained": status in ABSTAIN,
        "unknown_citations": unknown,
        "guardrail_intervened": bool(trace.get("guardrail_changes")),
        "tool_calls": trace.get("tool_calls_executed"),
        "elapsed_seconds": trace.get("elapsed_seconds"),
        "error": (trace.get("error") or {}).get("category") if isinstance(trace.get("error"), dict)
        else trace.get("error"),
    }


def aggregate(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    items = list(rows)
    total = len(items)
    if not total:
        raise MatrixError("No runs to aggregate")

    def rate(key: str) -> float:
        return round(sum(1 for item in items if item.get(key)) / total, 4)

    def mean(key: str) -> float | None:
        values = [item[key] for item in items if isinstance(item.get(key), (int, float))]
        return round(sum(values) / len(values), 2) if values else None

    by_scenario: dict[str, dict[str, Any]] = {}
    for scenario in sorted({item["scenario"] for item in items}):
        subset = [item for item in items if item["scenario"] == scenario]
        hits = sum(1 for item in subset if item["root_cause_match"])
        by_scenario[scenario] = {
            "runs": len(subset),
            "root_cause_accuracy": round(hits / len(subset), 4),
            "root_cause_accuracy_ci95": wilson_rate(hits, len(subset)),
            "evidence_coverage": round(sum(item["evidence_coverage"] for item in subset) / len(subset), 4),
            "semantic_support_rate": round(sum(1 for item in subset if item["semantic_supported"]) / len(subset), 4),
            "false_confidence_rate": round(sum(1 for item in subset if item["false_confidence"]) / len(subset), 4),
            "abstention_rate": round(sum(1 for item in subset if item["abstained"]) / len(subset), 4),
        }
    def count(key: str) -> int:
        return sum(1 for item in items if item.get(key))

    failures = [
        {"scenario": item["scenario"], "trace": item.get("trace"), "status": item.get("status"),
         "expected": item.get("expected"), "missing_proofs": item.get("missing_proofs"),
         "semantic_contradictions": item.get("semantic_contradictions")}
        for item in items
        if not item.get("root_cause_match") or item.get("false_confidence")
    ]
    return {
        "schema_version": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "runs": total,
        "root_cause_accuracy": round(count("root_cause_match") / total, 4),
        "root_cause_accuracy_ci95": wilson_rate(count("root_cause_match"), total),
        "mean_evidence_coverage": round(sum(item["evidence_coverage"] for item in items) / total, 4),
        "semantic_support_rate": round(count("semantic_supported") / total, 4),
        "false_confidence_rate": round(count("false_confidence") / total, 4),
        "false_confidence_ci95": wilson_rate(count("false_confidence"), total),
        "wrong_confident_rate": round(count("wrong_confident") / total, 4),
        "abstention_rate": round(count("abstained") / total, 4),
        "ambiguous_rate": round(count("ambiguous_detected") / total, 4),
        "guardrail_intervention_rate": round(count("guardrail_intervened") / total, 4),
        "unknown_citation_rate": round(sum(1 for item in items if item["unknown_citations"]) / total, 4),
        "mean_tool_calls": mean("tool_calls"),
        "mean_elapsed_seconds": mean("elapsed_seconds"),
        "underpowered": total < 5,
        "failures": failures,
        "by_scenario": by_scenario,
        "items": items,
    }


def load_manifest(path: Path) -> list[dict[str, str]]:
    document = load_json(path)
    runs = document.get("runs") if isinstance(document, dict) else document
    if not isinstance(runs, list) or not runs:
        raise MatrixError("Manifest must contain a non-empty runs list")
    parsed = []
    for entry in runs:
        if not isinstance(entry, dict) or not isinstance(entry.get("trace"), str):
            raise MatrixError("Each manifest entry needs a trace path")
        scenario = entry.get("scenario")
        expected = entry.get("expected")
        if scenario not in SCENARIOS:
            raise MatrixError(f"Unknown scenario: {scenario!r}")
        expected = expected or SCENARIOS[scenario].expected_status
        if expected not in EXPECTED:
            raise MatrixError(f"Unknown expected status: {expected!r}")
        parsed.append({"trace": entry["trace"], "scenario": scenario, "expected": expected})
    return parsed


def evaluate_manifest(manifest_path: Path, base_dir: Path | None = None) -> dict[str, Any]:
    base = base_dir or manifest_path.parent
    rows = []
    for entry in load_manifest(manifest_path):
        trace_path = (base / entry["trace"]).resolve()
        trace = extract_trace(load_json(trace_path))
        metrics = trace_metrics(trace, entry["expected"])
        metrics.update({"scenario": entry["scenario"], "trace": str(trace_path)})
        rows.append(metrics)
    return aggregate(rows)


def render_markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# 故障注入评测矩阵", "",
        f"生成时间：{summary['generated_at_utc']}；运行次数：{summary['runs']}", "",
        "| 指标 | 值 |", "| --- | --- |",
        f"| 根因准确率 | {summary['root_cause_accuracy']:.1%} "
        f"（95% CI {summary['root_cause_accuracy_ci95']['ci95_low']:.1%}–"
        f"{summary['root_cause_accuracy_ci95']['ci95_high']:.1%}） |",
        f"| 平均证据覆盖率 | {summary['mean_evidence_coverage']:.1%} |",
        f"| 语义支持率 | {summary['semantic_support_rate']:.1%} |",
        f"| 虚假置信率 | {summary['false_confidence_rate']:.1%} |",
        f"| 错误但自信 | {summary['wrong_confident_rate']:.1%} |",
        f"| 弃权率 | {summary['abstention_rate']:.1%} |",
        f"| 护栏介入率 | {summary['guardrail_intervention_rate']:.1%} |",
        "", "## 分场景", "",
        "| 场景 | 次数 | 根因准确率 | 证据覆盖率 | 虚假置信率 | 弃权率 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for name, value in summary["by_scenario"].items():
        lines.append(f"| {name} | {value['runs']} | {value['root_cause_accuracy']:.1%} | "
                     f"{value['evidence_coverage']:.1%} | {value['false_confidence_rate']:.1%} | "
                     f"{value['abstention_rate']:.1%} |")
    if summary.get("underpowered"):
        lines += ["", "> 样本量不足 5 次/场景：区间很宽，只能作为方向性参考。"]
    if summary.get("failures"):
        lines += ["", "## 失败样例", ""]
        for item in summary["failures"][:10]:
            lines.append(f"- {item['scenario']}: {item['status']}（期望 {item['expected']}）；"
                         f"缺证据 {item['missing_proofs']}；语义矛盾 {item['semantic_contradictions']}")
    lines += ["", "> 指标只针对受控单故障实验；不代表通用准确率。", ""]
    return "\n".join(lines)


# ---------- live orchestration (opt-in, not covered by offline tests) ----------

def _run_replay_diagnosis(observations: Path, destination: Path) -> Path:
    completed = subprocess.run(
        [sys.executable, "-B", str(LAB_DIR / "agent_cli.py"), "--mode", "replay",
         "--observations", str(observations), "--run-label", "live-fault"],
        cwd=LAB_DIR, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
    )
    print(completed.stdout, end="")
    if completed.returncode != 0:
        raise MatrixError(f"Replay diagnosis failed: {completed.stderr.strip()}")
    trace_line = next((line for line in completed.stdout.splitlines() if line.startswith("Trace: ")), None)
    if not trace_line:
        raise MatrixError("Replay diagnosis did not report a trace path")
    source = Path(trace_line.split("Trace: ", 1)[1].strip())
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(source.read_bytes())
    return destination


def run_live_matrix(runs: int, out: Path, scenarios: list[str] | None = None,
                    experiment: Callable[..., tuple[Path, dict[str, Any]]] | None = None,
                    diagnose: Callable[[Path, Path], Path] | None = None) -> dict[str, Any]:
    if os.environ.get("NETSECOPS_LIVE_MATRIX") != "1":
        raise MatrixError("Live matrix is disabled; set NETSECOPS_LIVE_MATRIX=1 and confirm the lab")
    if sys.platform != "linux":
        raise MatrixError("Live matrix requires Linux/WSL2 with Docker and Containerlab")
    if runs < 1:
        raise MatrixError("--runs must be >= 1")
    selected = scenarios or [key for key, value in SCENARIOS.items() if value.injectable]
    for name in selected:
        if name not in SCENARIOS or not SCENARIOS[name].injectable:
            raise MatrixError(f"Scenario is not injectable: {name}")
    if experiment is None:
        from run_fault_scenario import run_experiment as experiment  # type: ignore
    if diagnose is None:
        diagnose = _run_replay_diagnosis
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for name in selected:
        scenario = SCENARIOS[name]
        for index in range(runs):
            run_dir = out / f"{name}-{index + 1:02d}"
            run_dir.mkdir(parents=True, exist_ok=True)
            try:
                experiment_dir, record = experiment(scenario.fault, scenario.target, run_dir)
                snapshot = experiment_dir / "observations" / "fault" / "snapshot" / "observations.json"
                trace_path = diagnose(snapshot, run_dir / "trace.json")
                trace = extract_trace(load_json(trace_path))
                metrics = trace_metrics(trace, scenario.expected_status)
                metrics.update({"scenario": name, "trace": str(trace_path),
                                "experiment_status": record.get("status")})
            except Exception as exc:  # keep going; a failed run is data, not a crash
                metrics = {"scenario": name, "trace": None, "status": None,
                           "expected": scenario.expected_status, "root_cause_match": False,
                           "evidence_coverage": 0.0, "missing_proofs": ["run_failed"],
                           "definite": False, "false_confidence": False, "wrong_confident": False,
                           "abstained": False, "unknown_citations": [], "guardrail_intervened": False,
                           "tool_calls": None, "elapsed_seconds": None, "error": f"{type(exc).__name__}: {exc}"}
            rows.append(metrics)
            (run_dir / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2) + "\n",
                                                  encoding="utf-8")
    summary = aggregate(rows)
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out / "summary.md").write_text(render_markdown(summary), encoding="utf-8")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, help="JSON manifest with trace/scenario/expected entries")
    parser.add_argument("--out", type=Path, default=Path("matrix-report"), help="Output directory")
    parser.add_argument("--live", action="store_true", help="Inject faults and run replay diagnosis (Linux lab)")
    parser.add_argument("--runs", type=int, default=3, help="Runs per scenario in live mode")
    parser.add_argument("--scenario", action="append", choices=sorted(SCENARIOS), help="Limit live scenarios")
    args = parser.parse_args(argv)
    try:
        if args.live:
            summary = run_live_matrix(args.runs, args.out, args.scenario)
        else:
            if not args.manifest:
                parser.error("--manifest is required unless --live is used")
            summary = evaluate_manifest(args.manifest)
            args.out.mkdir(parents=True, exist_ok=True)
            (args.out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
                                                   encoding="utf-8")
            (args.out / "summary.md").write_text(render_markdown(summary), encoding="utf-8")
    except MatrixError as exc:
        print(f"Matrix error: {exc}", file=sys.stderr)
        return 2
    print(render_markdown(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
