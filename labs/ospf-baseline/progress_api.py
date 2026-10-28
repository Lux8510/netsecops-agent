"""Loopback-only event stream for the existing read-only Agent loop.

The browser supplies only a fixed mode. It cannot choose a task, shell command,
target address, experiment path, model key, or fault-injection operation.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import secrets
import sys
import time
from threading import Condition, Lock, Thread
from typing import Any, Literal

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict

from agent_cli import DEFAULT_TASK
from agent_config import AgentConfigError, configuration_status, get_deepseek_api_key, load_settings
from agent_loop import run_agent
from agent_tools import AgentTools, LiveBackend, ReplayBackend
from deepseek_client import DeepSeekChatClient


FIXTURE = Path(__file__).resolve().parent / "tests/fixtures/observations/normal.json"
ORIGIN = re.compile(r"https?://(?:127\.0\.0\.1|localhost):[0-9]{1,5}\Z")
MAX_RETAINED = 8


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["demo", "live"]


class OfflineEventModel:
    """Deterministic mock used only to exercise the real Agent event path."""

    def __init__(self) -> None:
        self.index = 0
        self.groups = [
            [("query_topology", {})],
            [("query_ospf_interface", {"node": node, "interface": "eth1"}) for node in ("r1", "r2")],
            [("query_neighbors", {"node": node}) for node in ("r1", "r2")],
            [("query_routes", {"node": node, "scope": "ospf"}) for node in ("r1", "r2")],
            [("ping_address", {"node": "r1", "target": "10.255.0.2"}),
             ("ping_address", {"node": "r2", "target": "10.255.0.1"})],
        ]

    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]],
                 *, deadline: float) -> dict[str, Any]:
        # Pace only the mock display; no duration is presented as a model metric.
        time.sleep(0.2)
        self.index += 1
        if self.index <= len(self.groups):
            calls = [{"id": f"demo-{self.index}-{number}", "type": "function",
                      "function": {"name": name, "arguments": json.dumps(args)}}
                     for number, (name, args) in enumerate(self.groups[self.index - 1], 1)]
            message = {"role": "assistant", "content": None, "tool_calls": calls}
            reason = "tool_calls"
        else:
            message = {"role": "assistant", "content": json.dumps({
                "status": "normal", "conclusion": "Mock candidate; independently checked by guardrail",
                "evidence_ids": [], "missing_information": [], "repair_suggestion": "Read only",
            })}
            reason = "stop"
        return {"message": message, "finish_reason": reason, "usage": None,
                "model": "mock-offline", "request_id": None}


def run_demo(emit: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    settings = load_settings()
    return run_agent(DEFAULT_TASK, OfflineEventModel(), AgentTools(ReplayBackend(FIXTURE)),
                     settings, mode="replay", on_event=emit)


def run_live(emit: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    settings = load_settings()
    key = get_deepseek_api_key(required=True)
    tools = AgentTools(LiveBackend(settings.command_timeout_seconds))
    return run_agent(DEFAULT_TASK, DeepSeekChatClient(settings, key), tools, settings,
                     mode="live", on_event=emit)


@dataclass
class RunRecord:
    run_id: str
    mode: str
    events: list[dict[str, Any]] = field(default_factory=list)
    done: bool = False
    trace: dict[str, Any] | None = None
    condition: Condition = field(default_factory=Condition)

    def publish(self, event: dict[str, Any]) -> None:
        with self.condition:
            self.events.append(event)
            self.condition.notify_all()


class ProgressService:
    def __init__(self, *, enable_live: bool = False,
                 demo_runner: Callable[[Callable[[dict[str, Any]], None]], dict[str, Any]] = run_demo,
                 live_runner: Callable[[Callable[[dict[str, Any]], None]], dict[str, Any]] = run_live) -> None:
        self.enable_live = enable_live
        self.runners = {"demo": demo_runner, "live": live_runner}
        self.records: dict[str, RunRecord] = {}
        self.lock = Lock()

    def live_available(self) -> bool:
        if not self.enable_live or sys.platform != "linux" or "microsoft-standard-WSL2" not in os.uname().release:
            return False
        try:
            return bool(configuration_status()["configured"])
        except AgentConfigError:
            return False

    def start(self, mode: str) -> RunRecord:
        if mode not in self.runners:
            raise HTTPException(422, "只支持 demo 或 live 模式")
        if mode == "live" and not self.live_available():
            raise HTTPException(403, "现场模式未显式启用，或 WSL/项目密钥未就绪")
        with self.lock:
            if any(not record.done for record in self.records.values()):
                raise HTTPException(409, "已有诊断运行中；请等待完成")
            if len(self.records) >= MAX_RETAINED:
                oldest = next(iter(self.records))
                del self.records[oldest]
            record = RunRecord(secrets.token_urlsafe(18), mode)
            self.records[record.run_id] = record
        Thread(target=self._work, args=(record,), daemon=True).start()
        return record

    def _work(self, record: RunRecord) -> None:
        try:
            record.trace = self.runners[record.mode](record.publish)
        except (AgentConfigError, ValueError, RuntimeError) as exc:
            record.publish({"schema_version": 1, "kind": "run_error",
                            "message": f"{type(exc).__name__}: {exc}"})
        except Exception as exc:
            record.publish({"schema_version": 1, "kind": "run_error",
                            "message": f"{type(exc).__name__}: 运行未完成；查看服务器本地错误"})
        finally:
            with record.condition:
                record.done = True
                record.condition.notify_all()

    def get(self, run_id: str) -> RunRecord:
        with self.lock:
            record = self.records.get(run_id)
        if record is None:
            raise HTTPException(404, "运行 ID 不存在或已过期")
        return record


def event_stream(record: RunRecord) -> Iterator[str]:
    position = 0
    while True:
        with record.condition:
            if position == len(record.events) and not record.done:
                record.condition.wait(timeout=2)
            batch = record.events[position:]
            position += len(batch)
            done = record.done
        for event in batch:
            yield f"event: {event['kind']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
        if done:
            break
        if not batch:
            yield ": keep-alive\n\n"


def create_app(service: ProgressService | None = None) -> FastAPI:
    app = FastAPI(title="NetSecOps local progress", docs_url=None, redoc_url=None,
                  openapi_url=None)
    progress = service or ProgressService()
    session_token = secrets.token_urlsafe(24)

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        host = request.url.hostname
        origin = request.headers.get("origin")
        if host not in ("127.0.0.1", "localhost") or (origin and not ORIGIN.fullmatch(origin)):
            return JSONResponse({"detail": "仅允许本机页面访问"}, status_code=403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.get("/api/capabilities")
    def capabilities():
        return {"schema_version": 1, "demo_available": True,
                "live_available": progress.live_available(), "session_token": session_token,
                "model_api_called_by_demo": False}

    @app.post("/api/runs", status_code=202)
    def start_run(payload: RunRequest, x_netsecops_session: str | None = Header(default=None)):
        if not x_netsecops_session or not secrets.compare_digest(x_netsecops_session, session_token):
            raise HTTPException(403, "本机页面会话无效")
        record = progress.start(payload.mode)
        return {"schema_version": 1, "run_id": record.run_id, "mode": record.mode}

    @app.get("/api/runs/{run_id}/events")
    def events(run_id: str):
        record = progress.get(run_id)
        return StreamingResponse(event_stream(record), media_type="text/event-stream",
                                 headers={"X-Accel-Buffering": "no"})

    @app.get("/api/runs/{run_id}/trace")
    def trace(run_id: str):
        record = progress.get(run_id)
        if not record.done or record.trace is None:
            raise HTTPException(409, "轨迹尚未完成或本次运行失败")
        return record.trace

    return app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="本机只读 Agent 事件服务")
    parser.add_argument("--enable-live", action="store_true",
                        help="显式允许 WSL 中的现场 DeepSeek 只读诊断；默认仅 mock 演示")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)
    if not 1024 <= args.port <= 65535:
        parser.error("port must be between 1024 and 65535")
    import uvicorn
    uvicorn.run(create_app(ProgressService(enable_live=args.enable_live)),
                host="127.0.0.1", port=args.port, access_log=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
