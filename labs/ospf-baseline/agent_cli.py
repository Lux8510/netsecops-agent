#!/usr/bin/env python3
"""Run one real DeepSeek read-only diagnosis against live tools or one replay snapshot."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import uuid

from agent_config import AgentConfigError, get_deepseek_api_key, load_settings
from agent_loop import render_trace, run_agent
from agent_tools import AgentTools, LiveBackend, ReplayBackend
from deepseek_client import DeepSeekChatClient


LAB_DIR = Path(__file__).resolve().parent
DEFAULT_TASK = "检查 r1 到 r2 Loopback 的连通性并解释异常原因；证据不足时如实说明。"


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("replay", "live"), required=True)
    parser.add_argument("--observations", type=Path, help="回放模式仅传一个 observations.json 文件")
    parser.add_argument("--task", default=DEFAULT_TASK)
    parser.add_argument("--run-label", default="diagnosis", choices=("smoke", "normal", "mismatch", "diagnosis",
                                                                    "missing-area", "failed-command", "live-normal", "live-fault"))
    return parser


def save_trace(trace: dict, label: str) -> tuple[Path, Path]:
    if label not in ("smoke", "normal", "mismatch", "diagnosis", "missing-area", "failed-command",
                     "live-normal", "live-fault"):
        raise ValueError("Unsupported run label")
    name = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    directory = LAB_DIR / "agent-runs" / f"{label}-{name}"
    directory.mkdir(parents=True, exist_ok=False)
    trace_path = directory / "trace.json"
    report_path = directory / "report.zh-CN.md"
    trace_path.write_text(json.dumps(trace, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report_path.write_text(render_trace(trace), encoding="utf-8")
    return trace_path, report_path


def main(argv: list[str] | None = None) -> int:
    args = make_parser().parse_args(argv)
    if args.mode == "replay" and (args.observations is None or args.observations.name not in
                                  ("observations.json", "input-observations.json")):
        raise SystemExit("Replay mode requires one collector observations JSON file")
    if args.mode == "live" and args.observations is not None:
        raise SystemExit("Live mode does not accept --observations")
    try:
        settings = load_settings()
        key = get_deepseek_api_key(required=True)
        backend = (ReplayBackend(args.observations) if args.mode == "replay"
                   else LiveBackend(settings.command_timeout_seconds))
        tools = AgentTools(backend)
        model = DeepSeekChatClient(settings, key)
    except (AgentConfigError, ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"Preflight failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    # The snapshot path and its phase label are never passed to the model.
    trace = run_agent(args.task, model, tools, settings, mode=args.mode)
    trace_path, report_path = save_trace(trace, args.run_label)
    print(f"Status: {trace['final']['status']}")
    print(f"Tool calls: {trace['tool_calls_executed']}")
    print(f"Elapsed seconds: {trace['elapsed_seconds']}")
    print(f"Token usage: {json.dumps(trace['usage_total'], ensure_ascii=False)}")
    print(f"Trace: {trace_path}")
    print(f"Report: {report_path}")
    if trace["error"]:
        print(f"Error category: {trace['error']['category']}")
        print(f"HTTP status: {trace['error'].get('status_code')}")
        print(f"Error detail: {trace['error']['message']}")
        return 1
    return 0 if trace["final"]["status"] not in ("incomplete", "api_error") else 1


if __name__ == "__main__":
    raise SystemExit(main())
