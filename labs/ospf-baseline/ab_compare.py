#!/usr/bin/env python3
"""Compare model/prompt variants on saved traces, with token and latency budgets."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

from experiment_matrix import (MatrixError, aggregate, extract_trace, load_json,
                               load_manifest, trace_metrics)
from stats import rate


def _usage(trace: dict[str, Any], key: str) -> int | None:
    usage = trace.get("usage_total") or {}
    value = usage.get(key)
    if isinstance(value, (int, float)):
        return int(value)
    return None


def variant_summary(name: str, manifest: Path, *, max_tokens: int | None = None,
                    max_seconds: float | None = None) -> dict[str, Any]:
    entries = load_manifest(manifest)
    rows = []
    tokens: list[int] = []
    seconds: list[float] = []
    over_budget = 0
    measurable = 0
    missing = 0
    for entry in entries:
        trace_path = (manifest.parent / entry["trace"]).resolve()
        trace = extract_trace(load_json(trace_path))
        metrics = trace_metrics(trace, entry["expected"])
        total_tokens = _usage(trace, "total_tokens")
        elapsed = trace.get("elapsed_seconds")
        if total_tokens is not None:
            tokens.append(total_tokens)
        if isinstance(elapsed, (int, float)):
            seconds.append(float(elapsed))
        needs_tokens = max_tokens is not None
        needs_seconds = max_seconds is not None
        missing_piece = (needs_tokens and total_tokens is None) or (needs_seconds and not isinstance(elapsed, (int, float)))
        exceeded = ((needs_tokens and total_tokens is not None and total_tokens > max_tokens)
                    or (needs_seconds and isinstance(elapsed, (int, float)) and elapsed > max_seconds))
        if missing_piece:
            missing += 1
        else:
            measurable += 1
            over_budget += 1 if exceeded else 0
        metrics.update({"scenario": entry["scenario"], "trace": str(trace_path),
                        "total_tokens": total_tokens, "budget_exceeded": bool(exceeded)})
        rows.append(metrics)
    summary = aggregate(rows)
    return {
        "variant": name,
        "runs": summary["runs"],
        "root_cause_accuracy": summary["root_cause_accuracy"],
        "root_cause_accuracy_ci95": summary["root_cause_accuracy_ci95"],
        "mean_evidence_coverage": summary["mean_evidence_coverage"],
        "semantic_support_rate": summary["semantic_support_rate"],
        "false_confidence_rate": summary["false_confidence_rate"],
        "mean_tokens": round(sum(tokens) / len(tokens), 1) if tokens else None,
        "mean_elapsed_seconds": round(sum(seconds) / len(seconds), 2) if seconds else None,
        "over_budget_rate": rate(over_budget, measurable)["rate"] if measurable else None,
        "budget_measurable_runs": measurable,
        "missing_telemetry_rate": round(missing / len(rows), 4),
        "budget": {"max_tokens": max_tokens, "max_seconds": max_seconds},
        "failures": summary["failures"],
    }


def sample_id(trace: dict[str, Any]) -> str:
    """Stable identity of the sampled observations, independent of model output."""
    body = {"task": trace.get("task"), "initial_topology": trace.get("initial_topology"),
            "evidence": trace.get("evidence")}
    encoded = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def _signature(manifest: Path) -> list[tuple[str, str]]:
    runs = []
    ids = []
    for entry in load_manifest(manifest):
        trace = extract_trace(load_json((manifest.parent / entry["trace"]).resolve()))
        value = sample_id(trace)
        if value in ids:
            raise MatrixError(f"Duplicate sample in variant {manifest}: {value}")
        ids.append(value)
        runs.append((value, entry["expected"]))
    return sorted(runs)


def compare(variants: dict[str, Path], *, max_tokens: int | None = None,
            max_seconds: float | None = None, allow_unpaired: bool = False) -> dict[str, Any]:
    if len(variants) < 2:
        raise MatrixError("A/B comparison needs at least two variants")
    signatures = {name: _signature(path) for name, path in variants.items()}
    base_signature = next(iter(signatures.values()))
    if not allow_unpaired and any(value != base_signature for value in signatures.values()):
        raise MatrixError("Variants do not share the same sample IDs and expected labels; "
                          "pass --allow-unpaired to compare anyway")
    results = [variant_summary(name, path, max_tokens=max_tokens, max_seconds=max_seconds)
               for name, path in variants.items()]
    return {"schema_version": 1,
            "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "paired": not allow_unpaired,
            "variants": results}


def render_markdown(summary: dict[str, Any]) -> str:
    lines = ["# 模型 / Prompt 变体对比", ""]
    for item in summary["variants"]:
        ci = item["root_cause_accuracy_ci95"]
        lines += [f"## {item['variant']}", "",
                  f"- 运行：{item['runs']}",
                  f"- 根因准确率：{item['root_cause_accuracy']:.1%} "
                  f"(95% CI {ci['ci95_low']:.1%}–{ci['ci95_high']:.1%})",
                  f"- 证据覆盖率：{item['mean_evidence_coverage']:.1%}",
                  f"- 虚假置信率：{item['false_confidence_rate']:.1%}",
                  f"- 平均 token：{item['mean_tokens']}",
                  f"- 平均耗时：{item['mean_elapsed_seconds']} 秒",
                  f"- 超预算率：{item['over_budget_rate']}（可判定 {item['budget_measurable_runs']} 次）",
                  f"- 遥测缺失率：{item['missing_telemetry_rate']:.1%}"]
        if item["budget"]["max_tokens"] or item["budget"]["max_seconds"]:
            lines.append(f"- 预算：token≤{item['budget']['max_tokens']}，"
                         f"耗时≤{item['budget']['max_seconds']} 秒")
        lines.append("")
    lines.append("> 对比只使用已保存轨迹；样本量、模型版本和日期必须一起报告。")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--variant", action="append", required=True,
                        help="name=path/to/manifest.json (repeat at least twice)")
    parser.add_argument("--max-tokens", type=int)
    parser.add_argument("--max-seconds", type=float)
    parser.add_argument("--out", type=Path, default=Path("ab-report"))
    parser.add_argument("--allow-unpaired", action="store_true")
    args = parser.parse_args(argv)
    variants: dict[str, Path] = {}
    for value in args.variant:
        if "=" not in value:
            parser.error("--variant must be name=manifest.json")
        name, path = value.split("=", 1)
        variants[name] = Path(path)
    try:
        summary = compare(variants, max_tokens=args.max_tokens, max_seconds=args.max_seconds,
                          allow_unpaired=args.allow_unpaired)
    except MatrixError as exc:
        print(f"A/B error: {exc}")
        return 2
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "comparison.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
                                              encoding="utf-8")
    (args.out / "comparison.md").write_text(render_markdown(summary), encoding="utf-8")
    print(render_markdown(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
