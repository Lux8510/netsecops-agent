"""DeepSeek Chat Completions transport; no Responses API or extra packages."""

from __future__ import annotations

import json
import re
import socket
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from agent_config import AgentSettings


class DeepSeekAPIError(RuntimeError):
    def __init__(self, category: str, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.category = category
        self.status_code = status_code


class DeepSeekChatClient:
    def __init__(self, settings: AgentSettings, api_key: str) -> None:
        if not api_key:
            raise ValueError("API key is not configured")
        self.settings = settings
        self._api_key = api_key
        self.endpoint = settings.base_url.rstrip("/") + "/chat/completions"

    def _safe_message(self, value: str) -> str:
        return re.sub(r"sk-[A-Za-z0-9_-]{6,}", "[REDACTED]", value.replace(self._api_key, "[REDACTED]"))[:500]

    def _send_once(self, body: dict[str, Any], timeout_seconds: float) -> dict[str, Any]:
        encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = Request(self.endpoint, data=encoded, method="POST",
                          headers={"Authorization": f"Bearer {self._api_key}",
                                   "Content-Type": "application/json", "Accept": "application/json"})
        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                data = response.read()
        except HTTPError as exc:
            try:
                parsed = json.loads(exc.read().decode("utf-8", "replace"))
                detail = parsed.get("error", {}).get("message", "")
            except (ValueError, AttributeError):
                detail = ""
            category = ("authentication" if exc.code in (401, 403) else
                        "balance" if exc.code == 402 else
                        "rate_limit" if exc.code == 429 else
                        "request" if 400 <= exc.code < 500 else "server")
            message = self._safe_message(str(detail)) if detail else f"HTTP {exc.code}"
            raise DeepSeekAPIError(category, message, exc.code) from None
        except (URLError, TimeoutError, socket.timeout) as exc:
            raise DeepSeekAPIError("network", f"Network request failed: {type(exc).__name__}") from None
        try:
            value = json.loads(data.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            raise DeepSeekAPIError("protocol", "Invalid JSON in API response") from None
        if not isinstance(value, dict):
            raise DeepSeekAPIError("protocol", "API response is not an object")
        return value

    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]],
                 *, deadline: float) -> dict[str, Any]:
        # DeepSeek's non-thinking Chat Completions parameter is `thinking`, not Responses `reasoning`.
        body = {"model": self.settings.model, "messages": messages,
                "thinking": {"type": "disabled"},
                "max_tokens": self.settings.max_tokens, "stream": False}
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        for attempt in range(self.settings.max_retries + 1):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise DeepSeekAPIError("timeout", "Total diagnosis timeout exceeded")
            try:
                response = self._send_once(body, min(self.settings.request_timeout_seconds, remaining))
                choices = response.get("choices")
                if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
                    raise DeepSeekAPIError("protocol", "API response has no single choice")
                message = choices[0].get("message")
                if not isinstance(message, dict) or message.get("role") != "assistant":
                    raise DeepSeekAPIError("protocol", "API response has no assistant message")
                return {"message": message, "finish_reason": choices[0].get("finish_reason"),
                        "usage": response.get("usage"), "model": response.get("model"),
                        "request_id": response.get("id")}
            except DeepSeekAPIError as exc:
                if exc.category not in ("network", "server") or attempt >= self.settings.max_retries:
                    raise
                pause = min(2 ** attempt, max(0, deadline - time.monotonic()))
                if pause:
                    time.sleep(pause)
        raise DeepSeekAPIError("retry", "Retry limit reached")
