"""Small synchronous client for the public Daiso MCP Streamable HTTP endpoint."""
from __future__ import annotations

import json
import re
import time
import uuid
from typing import Any

import requests
from core.config import LIVE_MCP_ENDPOINT


class McpRequestError(RuntimeError):
    pass


class McpClient:
    def __init__(self, endpoint: str = LIVE_MCP_ENDPOINT, session: requests.Session | None = None,
                 timeout: float = 20.0, retries: int = 1, min_interval: float = 0.25):
        self.endpoint = endpoint
        self.session = session or requests.Session()
        self.session.headers.update({"Accept": "application/json, text/event-stream", "Content-Type": "application/json",
                                     "User-Agent": "ConvenienceStoreCompare/1.0 (read-only)"})
        self.timeout, self.retries, self.min_interval = timeout, retries, min_interval
        self._last_request = 0.0

    def call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        body = {"jsonrpc": "2.0", "id": str(uuid.uuid4()), "method": "tools/call",
                "params": {"name": tool, "arguments": arguments}}
        for attempt in range(self.retries + 1):
            pause = self.min_interval - (time.monotonic() - self._last_request)
            if pause > 0:
                time.sleep(pause)
            try:
                response = self.session.post(self.endpoint, json=body, timeout=self.timeout)
                self._last_request = time.monotonic()
                if response.status_code == 429:
                    raise McpRequestError("MCP 호출 제한(HTTP 429)")
                if response.status_code >= 500 and attempt < self.retries:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                response.raise_for_status()
                try:
                    message = self._decode_response(response)
                except McpRequestError:
                    if attempt < self.retries:
                        time.sleep(0.5 * (attempt + 1))
                        continue
                    raise
                if message.get("error"):
                    raise McpRequestError(str(message["error"].get("message", message["error"])))
                result = message.get("result")
                if not isinstance(result, dict):
                    raise McpRequestError("MCP 응답에 result 객체가 없습니다.")
                if result.get("isError"):
                    details = " ".join(str(x.get("text", "")) for x in result.get("content", []) if isinstance(x, dict))
                    raise McpRequestError(details or "MCP 도구 실행 실패")
                structured = result.get("structuredContent")
                if isinstance(structured, dict):
                    structured = self._repair_text(structured)
                    if structured.get("error"):
                        error = structured["error"]
                        diagnostics = structured.get("diagnostics") or {}
                        if diagnostics.get("retryable") is True and attempt < self.retries:
                            time.sleep(0.5 * (attempt + 1))
                            continue
                        status = diagnostics.get("upstreamStatus") or diagnostics.get("status")
                        message = error.get("message", error) if isinstance(error, dict) else error
                        suffix = f" (upstream HTTP {status})" if status else ""
                        raise McpRequestError(f"{message}{suffix}")
                    return structured
                for block in result.get("content", []):
                    if isinstance(block, dict) and block.get("type") == "text":
                        try:
                            parsed = json.loads(block.get("text", ""))
                            if isinstance(parsed, dict):
                                return self._repair_text(parsed)
                        except json.JSONDecodeError:
                            continue
                raise McpRequestError("MCP 응답에서 구조화된 결과를 읽지 못했습니다.")
            except McpRequestError:
                raise
            except (requests.Timeout, requests.ConnectionError) as exc:
                if attempt == self.retries:
                    raise McpRequestError(f"네트워크/시간 초과: {exc}") from exc
                time.sleep(0.5 * (attempt + 1))
            except requests.RequestException as exc:
                raise McpRequestError(f"HTTP 요청 실패: {exc}") from exc
        raise McpRequestError("MCP 요청 실패")

    @staticmethod
    def _decode_response(response: requests.Response) -> dict[str, Any]:
        content_type = response.headers.get("content-type", "")
        if "json" in content_type and "event-stream" not in content_type or response.text.lstrip().startswith("{"):
            payload = response.json()
            if isinstance(payload, dict):
                return payload
            raise McpRequestError("JSON-RPC 응답 형식이 올바르지 않습니다.")
        matches = re.findall(r"(?:^|\s)data:\s*(\{.*?\})(?=\s*(?:event:|$))", response.text, re.DOTALL)
        for candidate in matches:
            try:
                payload = json.loads(candidate.strip())
                if isinstance(payload, dict):
                    return payload
            except json.JSONDecodeError:
                continue
        for line in response.text.splitlines():
            if "data:" in line:
                candidate = line.split("data:", 1)[1].strip()
                try:
                    payload = json.loads(candidate)
                    if isinstance(payload, dict):
                        return payload
                except json.JSONDecodeError:
                    continue
        excerpt = response.text[:240].replace("\n", " ")
        if response.status_code >= 400:
            raise McpRequestError(f"HTTP {response.status_code}: {excerpt or '응답 본문이 비어 있습니다.'}")
        raise McpRequestError(f"SSE 응답 파싱 실패(content-type={content_type or '미지정'}): {excerpt or '응답 본문이 비어 있습니다.'}")

    @classmethod
    def _repair_text(cls, value: Any) -> Any:
        """Repair UTF-8 bytes accidentally decoded as Latin-1 by upstream text serialization."""
        if isinstance(value, dict):
            return {key: cls._repair_text(item) for key, item in value.items()}
        if isinstance(value, list):
            return [cls._repair_text(item) for item in value]
        if isinstance(value, str) and any(marker in value for marker in ("ì", "ë", "ê", "í", "ã")):
            try:
                return value.encode("latin-1").decode("utf-8")
            except (UnicodeEncodeError, UnicodeDecodeError):
                return value
        return value
