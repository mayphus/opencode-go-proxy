from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import http.client
import json
import os
import signal
import struct
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

from . import __version__
from .codex_config import configure_codex
from .dashboard import DASHBOARD_CSS, DASHBOARD_HTML, DASHBOARD_JS
from .protocol import (
    CAPABILITY_MODEL_DEFAULT,
    COMBINED_MODELS,
    DEFAULT_MODEL,
    IMAGE_MODEL_DEFAULT,
    KNOWN_MODELS,
    ZEN_MODELS,
    ZEN_RESPONSES_MODELS,
    chat_completion_to_response,
    chat_message_to_response_output,
    model_capabilities,
    new_response_id,
    normalize_model_slug,
    normalize_usage,
    normalize_zen_model_slug,
    now_unix,
    required_capabilities,
    responses_payload_to_chat_payload,
    split_combined_model_slug,
)

Json = dict[str, Any]
GO_BASE_URL = "https://opencode.ai/zen/go/v1"
ZEN_BASE_URL = "https://opencode.ai/zen/v1"


class ProxyConfig:
    def __init__(
        self,
        *,
        bind: str,
        port: int,
        chat_base_url: str,
        api_key_env: str,
        client_token_env: str,
        timeout_sec: float,
        max_body_bytes: int,
        upstream: str | None = None,
        routed_from_combined: bool = False,
        model_prefix: str | None = None,
    ) -> None:
        self.bind = bind
        self.port = port
        self.chat_base_url = chat_base_url.rstrip("/")
        self.api_key_env = api_key_env
        self.client_token_env = client_token_env
        self.timeout_sec = timeout_sec
        self.max_body_bytes = max_body_bytes
        self.upstream = upstream or ("zen" if "/zen/v1" in self.chat_base_url and "/zen/go/v1" not in self.chat_base_url else "go")
        self.routed_from_combined = routed_from_combined
        self.model_prefix = model_prefix

    def for_upstream(self, upstream: str) -> ProxyConfig:
        base_url = (
            os.environ.get("OPENCODE_ZEN_BASE_URL", ZEN_BASE_URL)
            if upstream == "zen"
            else os.environ.get("OPENCODE_GO_BASE_URL", GO_BASE_URL)
        )
        return ProxyConfig(
            bind=self.bind,
            port=self.port,
            chat_base_url=base_url,
            api_key_env=self.api_key_env,
            client_token_env=self.client_token_env,
            timeout_sec=self.timeout_sec,
            max_body_bytes=self.max_body_bytes,
            upstream=upstream,
            routed_from_combined=True,
            model_prefix=upstream,
        )

    def external_model(self, model: Any) -> Any:
        if self.model_prefix and isinstance(model, str) and not model.startswith(f"{self.model_prefix}/"):
            return f"{self.model_prefix}/{model}"
        return model


def trace(event: str, **fields: Any) -> None:
    record = {"ts": time.time(), "event": event, **fields}
    print(json.dumps(record, sort_keys=True), file=sys.stderr, flush=True)


class ResponsesProxyHandler(BaseHTTPRequestHandler):
    # Codex streaming and WebSocket-compatible clients require HTTP/1.1.
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        if self.path in {"/responses", "/v1/responses"} and self.headers.get("upgrade", "").lower() == "websocket":
            config: ProxyConfig = self.server.config  # type: ignore[attr-defined]
            if not self._is_authorized(config):
                self._send_json(
                    {"error": {"message": "invalid client bearer token", "type": "authentication_error"}},
                    status=HTTPStatus.UNAUTHORIZED,
                )
                return
            handle_websocket_responses(self, config)
            return
        if self.path == "/":
            self._send_asset(DASHBOARD_HTML, "text/html; charset=utf-8")
            return
        if self.path == "/dashboard.css":
            self._send_asset(DASHBOARD_CSS, "text/css; charset=utf-8")
            return
        if self.path == "/dashboard.js":
            self._send_asset(DASHBOARD_JS, "text/javascript; charset=utf-8")
            return
        if self.path == "/dashboard.json":
            config: ProxyConfig = self.server.config  # type: ignore[attr-defined]
            self._send_json(dashboard_payload(config, self.headers.get("host", "127.0.0.1")))
            return
        if self.path in {"/health", "/v1/health"}:
            self._send_json({"status": "ok"})
            return
        if self.path in {"/models", "/v1/models"}:
            config: ProxyConfig = self.server.config  # type: ignore[attr-defined]
            models = (
                COMBINED_MODELS
                if _is_combined_upstream(config)
                else ZEN_MODELS if _is_zen_upstream(config) else KNOWN_MODELS
            )
            self._send_json({
                "object": "list",
                "data": [{"id": slug, "object": "model"} for slug in sorted(models)],
            })
            return
        self._send_json({"error": {"message": "not found"}}, status=HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        request_id = uuid.uuid4().hex[:12]
        if self.path not in {"/responses", "/v1/responses", "/responses/compact", "/v1/responses/compact"}:
            self._send_json({"error": {"message": "not found"}}, status=HTTPStatus.NOT_FOUND)
            return

        try:
            config: ProxyConfig = self.server.config  # type: ignore[attr-defined]
            if not self._is_authorized(config):
                self._send_json(
                    {"error": {"message": "invalid client bearer token", "type": "authentication_error"}},
                    status=HTTPStatus.UNAUTHORIZED,
                )
                return
            payload = self._read_json(config)
            if self.path in {"/responses/compact", "/v1/responses/compact"}:
                raise ProxyError(
                    HTTPStatus.NOT_IMPLEMENTED,
                    "OpenCode Go does not expose /responses/compact; regular Luna requests use native server-side compaction automatically",
                )
            payload, config, product = route_combined_payload(payload, config)
            trace(
                "request.received",
                request_id=request_id,
                path=self.path,
                model=payload.get("model"),
                stream=payload.get("stream", False),
                upstream=product or config.upstream,
            )
            native_model, fallback_capabilities = select_native_model(payload, config)
            if native_model is not None:
                requested_model = (
                    normalize_zen_model_slug(payload.get("model"))
                    if _is_zen_upstream(config)
                    else normalize_model_slug(payload.get("model"))
                )
                payload["model"] = native_model
                if fallback_capabilities:
                    trace(
                        "request.capability_fallback",
                        request_id=request_id,
                        requested_model=requested_model,
                        fallback_model=native_model,
                        capabilities=sorted(fallback_capabilities),
                    )
                payload = sanitize_websocket_payload(payload)
                payload["model"] = native_model
                input_value = payload.get("input")
                trace(
                    "request.native_payload",
                    request_id=request_id,
                    keys=sorted(payload),
                    input_items=len(input_value) if isinstance(input_value, list) else None,
                    input_types=_item_types(input_value),
                    tool_types=_item_types(payload.get("tools")),
                )
                if payload.get("stream") is True:
                    self.send_response(HTTPStatus.OK)
                    self.send_header("content-type", "text/event-stream")
                    self.send_header("cache-control", "no-cache")
                    self.send_header("connection", "close")
                    self.end_headers()
                    handle_native_responses_stream(payload, config, request_id, self.wfile)
                else:
                    self._send_json(externalize_response(call_upstream_responses(payload, config, request_id), config))
                return
            if payload.get("stream") is True:
                # Real streaming: send SSE headers, then stream from upstream in real-time.
                self.send_response(HTTPStatus.OK)
                self.send_header("content-type", "text/event-stream")
                self.send_header("cache-control", "no-cache")
                self.send_header("connection", "close")
                self.end_headers()
                try:
                    handle_streaming_request(payload, config, request_id, self.wfile)
                except Exception as exc:  # noqa: BLE001 - defensive crash trace
                    trace("request.crashed", request_id=request_id, message=str(exc), traceback=traceback.format_exc())
                    try:
                        err = json.dumps({"type": "response.error", "error": {"message": "proxy crashed; see stderr trace"}}, separators=(",",":")).encode("utf-8")
                        self.wfile.write(b"data: " + err + b"\n\ndata: [DONE]\n\n")
                        self.wfile.flush()
                    except BrokenPipeError:
                        pass
            else:
                response = handle_responses_request(payload, config, request_id)
                self._send_json(response)
        except ProxyError as exc:
            trace("request.failed", request_id=request_id, status=exc.status, message=exc.message)
            self._send_json({"error": {"message": exc.message, "type": "proxy_error"}}, status=exc.status)
        except BrokenPipeError:
            trace("client.disconnected", request_id=request_id, message="client closed connection during stream")
        except Exception as exc:  # pragma: no cover - defensive crash trace  # noqa: BLE001
            trace("request.crashed", request_id=request_id, message=str(exc), traceback=traceback.format_exc())
            try:
                self._send_json(
                    {"error": {"message": "proxy crashed; see stderr trace", "type": "proxy_crash"}},
                    status=HTTPStatus.INTERNAL_SERVER_ERROR,
                )
            except BrokenPipeError:
                pass

    def _read_json(self, config: ProxyConfig) -> Json:
        try:
            length = int(self.headers.get("content-length", "0"))
        except ValueError:
            raise ProxyError(HTTPStatus.BAD_REQUEST, "invalid content-length header")
        if length < 0:
            raise ProxyError(HTTPStatus.BAD_REQUEST, "negative content-length")
        if length > config.max_body_bytes:
            raise ProxyError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, f"request body exceeds {config.max_body_bytes // (1024*1024)}MB cap")
        raw = self.rfile.read(length)
        if not raw:
            return {}
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ProxyError(HTTPStatus.BAD_REQUEST, "request body must be a JSON object")
        return value

    def _send_json(self, payload: Json, status: HTTPStatus = HTTPStatus.OK) -> None:
        raw = json.dumps(payload, separators=(",",":")).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(raw)))
        self.send_header("cache-control", "no-store")
        self.send_header("x-content-type-options", "nosniff")
        self.end_headers()
        self.wfile.write(raw)

    def _send_asset(self, content: str, content_type: str) -> None:
        raw = content.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("content-type", content_type)
        self.send_header("content-length", str(len(raw)))
        self.send_header("cache-control", "no-cache")
        self.send_header("content-security-policy", "default-src 'self'; connect-src 'self'; style-src 'self'; script-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        self.send_header("x-content-type-options", "nosniff")
        self.send_header("referrer-policy", "no-referrer")
        self.end_headers()
        self.wfile.write(raw)

    def _is_authorized(self, config: ProxyConfig) -> bool:
        expected = os.environ.get(config.client_token_env)
        if not expected:
            return True
        authorization = self.headers.get("authorization", "")
        scheme, separator, supplied = authorization.partition(" ")
        return bool(
            separator
            and scheme.lower() == "bearer"
            and supplied
            and hmac.compare_digest(supplied, expected)
        )


class ProxyError(Exception):
    def __init__(self, status: HTTPStatus, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class PersistentUpstreamConnection:
    """Reuse one HTTP(S) connection for all turns on a WebSocket session."""

    def __init__(self, config: ProxyConfig) -> None:
        parsed = urlsplit(config.chat_base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ProxyError(HTTPStatus.BAD_GATEWAY, "invalid upstream base URL")
        self._connection_type = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
        self._host = parsed.hostname
        self._port = parsed.port
        self._timeout = config.timeout_sec
        self._connection: http.client.HTTPConnection | None = None
        self._path = f"{parsed.path.rstrip('/')}/responses"

    def request(self, payload: bytes, api_key: str, config: ProxyConfig) -> http.client.HTTPResponse:
        try:
            if self._connection is None:
                self._connection = self._connection_type(self._host, self._port, timeout=self._timeout)
            self._connection.request(
                "POST",
                self._path,
                body=payload,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "Accept": "text/event-stream",
                    "User-Agent": os.environ.get("OPENCODE_GO_PROXY_USER_AGENT", "codex/1.0"),
                },
            )
            response = self._connection.getresponse()
            if response.status >= 400:
                body = response.read().decode("utf-8", errors="replace")
                raise ProxyError(
                    HTTPStatus.BAD_GATEWAY,
                    f"upstream Responses HTTP {response.status}: {body[:500]}",
                )
            return response
        except (ConnectionError, TimeoutError, OSError) as exc:
            self.close()
            raise ProxyError(HTTPStatus.BAD_GATEWAY, f"upstream connection error: {exc}") from exc

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None


def handle_responses_request(payload: Json, config: ProxyConfig, request_id: str) -> Json:
    chat_payload, request_model, conversion_stats = responses_payload_to_chat_payload(
        payload, zen=_is_zen_upstream(config)
    )

    # Split-turn: if image + tools, caption images via MiMo sub-call, then route to the requested model.
    # MiMo can't drive tool loops from tool-role image messages; caption + requested model keeps the agent loop alive.
    if conversion_stats.get("has_image") and conversion_stats.get("tools_present"):
        chat_payload = caption_images_in_messages(chat_payload, request_model, config, request_id)
        conversion_stats["upstream_model"] = chat_payload.get("model")

    trace(
        "request.converted",
        request_id=request_id,
        stats=conversion_stats,
        upstream_model=chat_payload.get("model"),
    )
    chat = call_upstream_chat(chat_payload, config, request_id)
    response = chat_completion_to_response(chat, request_model=request_model)
    trace(
        "response.converted",
        request_id=request_id,
        output_items=len(response.get("output", [])),
        output_text_len=len(response.get("output_text", "")),
        usage=response.get("usage"),
    )
    return externalize_response(response, config)


def externalize_response(value: Json, config: ProxyConfig) -> Json:
    """Restore a combined model prefix in Responses objects and events."""
    if not config.model_prefix:
        return value
    result = dict(value)
    if isinstance(result.get("model"), str):
        result["model"] = config.external_model(result["model"])
    nested = result.get("response")
    if isinstance(nested, dict):
        result["response"] = externalize_response(nested, config)
    return result


def _is_zen_upstream(config: ProxyConfig) -> bool:
    return config.upstream == "zen" or (
        config.upstream != "combined"
        and "/zen/v1" in config.chat_base_url
        and "/zen/go/v1" not in config.chat_base_url
    )


def _is_combined_upstream(config: ProxyConfig) -> bool:
    return config.upstream == "combined"


def route_combined_payload(payload: Json, config: ProxyConfig) -> tuple[Json, ProxyConfig, str | None]:
    """Route a prefixed model to its product without inferring billing intent."""
    if not _is_combined_upstream(config):
        return payload, config, None
    split = split_combined_model_slug(payload.get("model"))
    if split is None:
        raise ProxyError(HTTPStatus.BAD_REQUEST, "combined mode requires a valid go/<model> or zen/<model> model id")
    product, model = split
    routed = dict(payload)
    routed["model"] = model
    return routed, config.for_upstream(product), product


def dashboard_payload(config: ProxyConfig, host: str) -> Json:
    """Return read-only service status; probes only health/model-list endpoints."""
    upstream = "Combined" if _is_combined_upstream(config) else "Zen" if _is_zen_upstream(config) else "Go"
    default_peer = {
        "name": f"OpenCode {upstream}",
        "upstream": upstream,
        "probe_url": f"http://{host}/v1",
        "public_url": f"http://{host}/v1",
    }
    raw_peers = os.environ.get("OPENCODE_DASHBOARD_PEERS", "")
    try:
        peers = json.loads(raw_peers) if raw_peers else [default_peer]
    except json.JSONDecodeError:
        peers = [default_peer]
    if not isinstance(peers, list):
        peers = [default_peer]

    services: list[Json] = []
    for candidate in peers[:8]:
        if not isinstance(candidate, dict):
            continue
        probe_url = str(candidate.get("probe_url", "")).rstrip("/")
        parsed = urlsplit(probe_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            continue
        service: Json = {
            "name": str(candidate.get("name") or "OpenCode"),
            "upstream": str(candidate.get("upstream") or "Proxy"),
            "public_url": str(candidate.get("public_url") or probe_url),
            "ok": False,
            "models": [],
            "model_count": 0,
        }
        try:
            with urllib.request.urlopen(f"{probe_url}/health", timeout=1.5) as response:
                health = json.loads(response.read())
            with urllib.request.urlopen(f"{probe_url}/models", timeout=1.5) as response:
                model_payload = json.loads(response.read())
            models = [item["id"] for item in model_payload.get("data", []) if isinstance(item, dict) and isinstance(item.get("id"), str)]
            peer_upstream = str(candidate.get("upstream") or "Proxy").lower()
            native_models: list[str] = []
            capability_models: dict[str, list[str]] = {}
            if peer_upstream == "combined":
                for model in models:
                    split = split_combined_model_slug(model)
                    if split is None:
                        continue
                    product, raw_model = split
                    native_set = ZEN_RESPONSES_MODELS if product == "zen" else {"gpt-5.6-luna"}
                    if raw_model in native_set:
                        native_models.append(model)
                    capabilities = sorted(model_capabilities(raw_model))
                    if capabilities:
                        capability_models[model] = capabilities
            else:
                native_models = sorted(
                    set(models) & (ZEN_RESPONSES_MODELS if peer_upstream == "zen" else {"gpt-5.6-luna"})
                )
                capability_models = {
                    model: sorted(model_capabilities(model))
                    for model in models
                    if model_capabilities(model)
                }
            service.update({
                "ok": health.get("status") == "ok",
                "models": models,
                "model_count": len(models),
                "routing": {
                    "native_models": native_models,
                    "capability_models": capability_models,
                    "fallback_model": os.environ.get("OPENCODE_CAPABILITY_MODEL", CAPABILITY_MODEL_DEFAULT),
                    "vision_bridge_model": IMAGE_MODEL_DEFAULT if peer_upstream == "go" else None,
                    "fallback_models": {
                        "go": f'go/{os.environ.get("OPENCODE_GO_CAPABILITY_MODEL", CAPABILITY_MODEL_DEFAULT)}',
                        "zen": f'zen/{os.environ.get("OPENCODE_ZEN_CAPABILITY_MODEL", CAPABILITY_MODEL_DEFAULT)}',
                    } if peer_upstream == "combined" else {},
                    "vision_bridge_models": {},
                },
            })
        except (OSError, ValueError, urllib.error.URLError, json.JSONDecodeError):
            pass
        services.append(service)
    return {"services": services, "token_free": True}


def select_native_model(payload: Json, config: ProxyConfig) -> tuple[str | None, set[str]]:
    """Choose native Responses passthrough, including capability fallback."""
    zen = _is_zen_upstream(config)
    requested_model = (
        normalize_zen_model_slug(payload.get("model")) if zen else normalize_model_slug(payload.get("model"))
    )
    required = required_capabilities(payload)
    available_native = ZEN_RESPONSES_MODELS if zen else {"gpt-5.6-luna"}

    if requested_model in available_native:
        missing = required - model_capabilities(requested_model)
        if not missing:
            return requested_model, set()
    else:
        missing = required

    # Go already has a low-cost MiMo caption path for image-only requests.
    # Zen can send the original image to Luna, avoiding that conversion.
    if not zen and not config.routed_from_combined and requested_model not in available_native:
        missing.discard("image")

    if not missing:
        return None, set()

    product_fallback_env = "OPENCODE_ZEN_CAPABILITY_MODEL" if zen else "OPENCODE_GO_CAPABILITY_MODEL"
    configured_fallback = os.environ.get(
        product_fallback_env,
        os.environ.get("OPENCODE_CAPABILITY_MODEL", CAPABILITY_MODEL_DEFAULT),
    )
    fallback_model = normalize_zen_model_slug(configured_fallback) if zen else normalize_model_slug(configured_fallback)
    unsupported = missing - model_capabilities(fallback_model)
    if fallback_model not in available_native or unsupported:
        details = ", ".join(sorted(unsupported or missing))
        raise ProxyError(
            HTTPStatus.BAD_GATEWAY,
            f"no native fallback model is available for requested capabilities: {details}",
        )
    return fallback_model, missing


def call_upstream_responses(payload: Json, config: ProxyConfig, request_id: str) -> Json:
    """Call a Responses-native Go model without converting its request shape."""
    api_key = resolve_api_key(config, request_id)
    url = f"{config.chat_base_url}/responses"
    raw_payload = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=raw_payload,
        headers={
            "authorization": f"Bearer {api_key}",
            "content-type": "application/json",
            "accept": "application/json",
            "user-agent": os.environ.get("OPENCODE_GO_PROXY_USER_AGENT", "codex/1.0"),
        },
        method="POST",
    )
    trace("upstream.responses.start", request_id=request_id, url=url, bytes=len(raw_payload))
    try:
        with urllib.request.urlopen(request, timeout=config.timeout_sec) as response:
            body = response.read()
            value = json.loads(body)
            if not isinstance(value, dict):
                raise ProxyError(HTTPStatus.BAD_GATEWAY, "upstream returned non-object JSON")
            trace("upstream.responses.done", request_id=request_id, status=response.status, bytes=len(body))
            return value
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        trace("upstream.responses.error", request_id=request_id, status=exc.code, body=body[:2000])
        raise ProxyError(HTTPStatus.BAD_GATEWAY, f"upstream Responses HTTP {exc.code}") from exc
    except json.JSONDecodeError as exc:
        raise ProxyError(HTTPStatus.BAD_GATEWAY, "upstream returned invalid JSON") from exc
    except urllib.error.URLError as exc:
        raise ProxyError(HTTPStatus.BAD_GATEWAY, f"upstream network error: {exc.reason}") from exc


def handle_native_responses_stream(payload: Json, config: ProxyConfig, request_id: str, wfile: Any) -> None:
    """Forward native Responses SSE events unchanged."""
    try:
        api_key = resolve_api_key(config, request_id)
    except ProxyError as exc:
        error = json.dumps({"type": "response.error", "error": {"message": exc.message}}, separators=(",", ":"))
        wfile.write(b"data: " + error.encode("utf-8") + b"\n\ndata: [DONE]\n\n")
        wfile.flush()
        return
    url = f"{config.chat_base_url}/responses"
    raw_payload = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=raw_payload,
        headers={
            "authorization": f"Bearer {api_key}",
            "content-type": "application/json",
            "accept": "text/event-stream",
            "user-agent": os.environ.get("OPENCODE_GO_PROXY_USER_AGENT", "codex/1.0"),
        },
        method="POST",
    )
    trace("upstream.responses.start", request_id=request_id, url=url, bytes=len(raw_payload), stream=True)
    try:
        with urllib.request.urlopen(request, timeout=config.timeout_sec) as response:
            for line in response:
                if config.model_prefix:
                    decoded = line.decode("utf-8", errors="replace").strip()
                    if decoded.startswith("data: ") and decoded[6:] != "[DONE]":
                        try:
                            event = json.loads(decoded[6:])
                            if isinstance(event, dict):
                                line = b"data: " + json.dumps(externalize_response(event, config), separators=(",", ":")).encode("utf-8") + b"\n\n"
                        except json.JSONDecodeError:
                            pass
                wfile.write(line)
                wfile.flush()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        trace("upstream.responses.error", request_id=request_id, status=exc.code, body=body[:2000])
        wfile.write(b'data: {"type":"response.error","error":{"message":"upstream Responses error"}}\n\ndata: [DONE]\n\n')
        wfile.flush()
    except urllib.error.URLError as exc:
        trace("upstream.responses.network_error", request_id=request_id, reason=str(exc.reason))


def _read_ws_frame(rfile: Any) -> tuple[int, bytes] | None:
    header = rfile.read(2)
    if len(header) != 2:
        return None
    first, second = header
    opcode = first & 0x0F
    masked = bool(second & 0x80)
    length = second & 0x7F
    if length == 126:
        raw_length = rfile.read(2)
        if len(raw_length) != 2:
            return None
        length = struct.unpack("!H", raw_length)[0]
    elif length == 127:
        raw_length = rfile.read(8)
        if len(raw_length) != 8:
            return None
        length = struct.unpack("!Q", raw_length)[0]
    if length > 20 * 1024 * 1024:
        raise ProxyError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "WebSocket frame exceeds 20MB cap")
    mask = rfile.read(4) if masked else b""
    payload = rfile.read(length)
    if len(payload) != length:
        return None
    if masked:
        payload = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
    return opcode, payload


def _write_ws_frame(wfile: Any, payload: bytes, opcode: int = 1) -> None:
    length = len(payload)
    if length < 126:
        header = bytes([0x80 | opcode, length])
    elif length <= 0xFFFF:
        header = bytes([0x80 | opcode, 126]) + struct.pack("!H", length)
    else:
        header = bytes([0x80 | opcode, 127]) + struct.pack("!Q", length)
    wfile.write(header + payload)
    wfile.flush()


def _write_ws_json(wfile: Any, value: Json) -> None:
    _write_ws_frame(wfile, json.dumps(value, separators=(",", ":")).encode("utf-8"))


def _stream_native_response_events(
    payload: Json,
    config: ProxyConfig,
    request_id: str,
    on_event: Any,
    upstream: PersistentUpstreamConnection | None = None,
) -> None:
    """Stream native Responses events from Go and pass decoded JSON to a transport."""
    native_model, fallback_capabilities = select_native_model(payload, config)
    if native_model is None:
        raise ProxyError(HTTPStatus.BAD_REQUEST, "this model requires HTTP Responses mode through the Chat Completions bridge")
    upstream_payload = dict(payload)
    requested_model = (
        normalize_zen_model_slug(payload.get("model"))
        if _is_zen_upstream(config)
        else normalize_model_slug(payload.get("model"))
    )
    upstream_payload["model"] = native_model
    if fallback_capabilities:
        trace(
            "request.capability_fallback",
            request_id=request_id,
            requested_model=requested_model,
            fallback_model=native_model,
            capabilities=sorted(fallback_capabilities),
        )
    upstream_payload = sanitize_websocket_payload(upstream_payload)
    upstream_payload["stream"] = True
    input_value = upstream_payload.get("input")
    input_types = []
    if isinstance(input_value, list):
        input_types = sorted({item.get("type", "<missing>") for item in input_value if isinstance(item, dict)})
    tool_types = sorted({tool.get("type", "<missing>") for tool in upstream_payload.get("tools", []) if isinstance(tool, dict)})
    trace(
        "websocket.payload",
        request_id=request_id,
        keys=sorted(upstream_payload),
        input_types=input_types,
        tool_types=tool_types,
        input_items=len(input_value) if isinstance(input_value, list) else None,
    )
    if isinstance(input_value, list) and not input_value:
        _complete_empty_websocket_response(upstream_payload, request_id, on_event, config)
        return

    api_key = resolve_api_key(config, request_id)
    raw_payload = json.dumps(upstream_payload, separators=(",", ":")).encode("utf-8")
    owns_upstream = upstream is None
    if upstream is None:
        upstream = PersistentUpstreamConnection(config)
    trace("upstream.websocket.start", request_id=request_id, url=f"{config.chat_base_url}/responses", bytes=len(raw_payload))
    started = time.time()
    event_count = 0
    final_status: str | None = None
    try:
        response = upstream.request(raw_payload, api_key, config)
        for line in response:
            decoded = line.decode("utf-8", errors="replace").strip()
            if not decoded.startswith("data: "):
                continue
            data = decoded[6:]
            if data == "[DONE]":
                trace(
                    "upstream.websocket.done",
                    request_id=request_id,
                    events=event_count,
                    status=final_status,
                    elapsed_ms=int((time.time() - started) * 1000),
                )
                return
            try:
                event = json.loads(data)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                event_count += 1
                if event.get("type") == "response.completed":
                    response_value = event.get("response")
                    if isinstance(response_value, dict) and isinstance(response_value.get("status"), str):
                        final_status = response_value["status"]
                on_event(externalize_response(event, config))
        trace(
            "upstream.websocket.eof",
            request_id=request_id,
            events=event_count,
            status=final_status,
            elapsed_ms=int((time.time() - started) * 1000),
        )
    except ProxyError as exc:
        trace("upstream.websocket.error", request_id=request_id, status=exc.status, body=exc.message[:4000])
        raise
    finally:
        if owns_upstream:
            upstream.close()


def _complete_empty_websocket_response(
    payload: Json, request_id: str, on_event: Any, config: ProxyConfig | None = None
) -> None:
    """Acknowledge Desktop warm-up creates without sending invalid empty input upstream."""
    response_id = new_response_id()
    model = normalize_model_slug(payload.get("model"))
    if config is not None:
        model = config.external_model(model)
    created_at = now_unix()
    in_progress: Json = {
        "id": response_id,
        "object": "response",
        "created_at": created_at,
        "status": "in_progress",
        "model": model,
        "output": [],
        "output_text": "",
        "usage": None,
    }
    on_event({"type": "response.created", "response": in_progress})
    on_event({"type": "response.in_progress", "response": in_progress})
    completed = dict(in_progress)
    completed["status"] = "completed"
    on_event({"type": "response.completed", "response": completed})
    trace("websocket.empty_completed", request_id=request_id, model=model)


def sanitize_websocket_payload(payload: Json) -> Json:
    """Keep portable stateless Responses fields for the HTTP Go backend."""
    allowed_keys = {
        "model",
        "input",
        "instructions",
        "tools",
        "tool_choice",
        "max_output_tokens",
        "temperature",
        "top_p",
        "store",
        "reasoning",
        "text",
        "parallel_tool_calls",
        "stream",
        # Verified against the OpenCode Go native Responses endpoint. Keep these
        # fields native instead of silently weakening Desktop requests.
        "background",
        "context_management",
        "max_tool_calls",
        "metadata",
        "safety_identifier",
        "service_tier",
    }
    upstream_payload = {key: value for key, value in payload.items() if key in allowed_keys}
    # OpenCode Go accepts stateless Responses but rejects persisted response state.
    # Desktop already sends full history, so discard previous_response_id and replay
    # its supplied items with store disabled.
    upstream_payload["store"] = False
    if "compaction" in model_capabilities(upstream_payload.get("model")) and "context_management" not in upstream_payload:
        compact_threshold = _native_compact_threshold()
        if compact_threshold:
            upstream_payload["context_management"] = [
                {"type": "compaction", "compact_threshold": compact_threshold}
            ]
    extracted_tools: list[object] = []
    top_level_additional_tools = payload.get("additional_tools")
    if isinstance(top_level_additional_tools, list):
        extracted_tools.extend(top_level_additional_tools)
    input_items = upstream_payload.get("input")
    if isinstance(input_items, list):
        sanitized_items = []
        for item in input_items:
            if not isinstance(item, dict):
                sanitized_items.append(item)
                continue
            item_type = item.get("type")
            if item_type == "additional_tools":
                item_tools = item.get("tools")
                if isinstance(item_tools, list):
                    extracted_tools.extend(item_tools)
                continue
            if item_type == "custom_tool_call_output":
                item = dict(item)
                item["type"] = "function_call_output"
            sanitized_items.append(item)
        upstream_payload["input"] = _prune_before_latest_compaction(sanitized_items)
    existing_tools = upstream_payload.get("tools")
    merged_tools = _dedupe_json_items([
        *(existing_tools if isinstance(existing_tools, list) else []),
        *extracted_tools,
    ])
    # Some Desktop request paths omit the hosted search tool even though the
    # selected Luna model supports it. Make the native capability available so
    # search questions do not have to fall back to browser automation.
    if "web_search" in model_capabilities(upstream_payload.get("model")) and _native_search_enabled() and not any(
        isinstance(tool, dict) and tool.get("type") in {"web_search", "web_search_preview"}
        for tool in merged_tools
    ):
        merged_tools.append({"type": "web_search"})
    if merged_tools:
        upstream_payload["tools"] = merged_tools
    return upstream_payload


def _native_search_enabled() -> bool:
    """Enable Luna's native hosted search unless explicitly disabled."""
    raw = os.environ.get("OPENCODE_GO_NATIVE_SEARCH", "1").strip().lower()
    return raw not in {"", "0", "off", "false", "no"}


def _native_compact_threshold() -> int | None:
    """Return the native compaction threshold, or None when explicitly disabled."""
    raw = os.environ.get("OPENCODE_GO_COMPACT_THRESHOLD", "800000").strip()
    if raw in {"", "0", "off", "false", "no"}:
        return None
    try:
        value = int(raw)
    except ValueError as exc:
        raise ProxyError(HTTPStatus.INTERNAL_SERVER_ERROR, "OPENCODE_GO_COMPACT_THRESHOLD must be an integer") from exc
    if value < 1000:
        raise ProxyError(HTTPStatus.INTERNAL_SERVER_ERROR, "OPENCODE_GO_COMPACT_THRESHOLD must be at least 1000")
    return value


def _prune_before_latest_compaction(items: list[object]) -> list[object]:
    """Keep the canonical compacted state and everything after it."""
    latest = None
    for index, item in enumerate(items):
        if isinstance(item, dict) and item.get("type") == "compaction":
            latest = index
    return items[latest:] if latest is not None else items


def _dedupe_json_items(items: list[object]) -> list[object]:
    """Remove repeated Desktop tool definitions while preserving their order."""
    result: list[object] = []
    seen: set[str] = set()
    for item in items:
        marker = json.dumps(item, sort_keys=True, separators=(",", ":"), default=str)
        if marker not in seen:
            seen.add(marker)
            result.append(item)
    return result


def _item_types(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return sorted({str(item.get("type", "<missing>")) for item in value if isinstance(item, dict)})


def handle_websocket_responses(handler: ResponsesProxyHandler, config: ProxyConfig) -> None:
    key = handler.headers.get("sec-websocket-key")
    if not key or handler.headers.get("sec-websocket-version") != "13":
        handler.send_error(HTTPStatus.BAD_REQUEST, "WebSocket version 13 and key are required")
        return
    accept = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
    handler.send_response_only(HTTPStatus.SWITCHING_PROTOCOLS)
    handler.send_header("Upgrade", "websocket")
    handler.send_header("Connection", "Upgrade")
    handler.send_header("Sec-WebSocket-Accept", accept)
    handler.end_headers()
    trace("websocket.connected", path=handler.path)
    upstream = None if _is_combined_upstream(config) else PersistentUpstreamConnection(config)

    try:
        while True:
            try:
                frame = _read_ws_frame(handler.rfile)
            except (ConnectionResetError, BrokenPipeError, OSError):
                trace("websocket.disconnected", path=handler.path)
                return
            if frame is None:
                return
            opcode, payload = frame
            if opcode == 8:  # close
                _write_ws_frame(handler.wfile, payload[:125], opcode=8)
                return
            if opcode == 9:  # ping
                _write_ws_frame(handler.wfile, payload[:125], opcode=10)
                continue
            if opcode != 1:
                _write_ws_json(handler.wfile, {"type": "error", "error": {"message": "text WebSocket frames are required"}})
                continue
            try:
                event = json.loads(payload.decode("utf-8"))
                if not isinstance(event, dict) or event.get("type") != "response.create":
                    raise ValueError("expected a response.create event")
                request_id = uuid.uuid4().hex[:12]
                event, event_config, product = route_combined_payload(event, config)
                if product:
                    trace("request.combined_route", request_id=request_id, upstream=product, model=event.get("model"))
                _stream_response_events(
                    event,
                    event_config,
                    request_id,
                    lambda response_event: _write_ws_json(handler.wfile, response_event),
                    upstream,
                )
            except (ProxyError, urllib.error.URLError, ValueError, json.JSONDecodeError) as exc:
                message = getattr(exc, "message", str(exc))
                trace("websocket.request_failed", message=message)
                _write_ws_json(handler.wfile, {"type": "error", "error": {"message": message}})
    finally:
        if upstream is not None:
            upstream.close()


def _stream_response_events(
    payload: Json,
    config: ProxyConfig,
    request_id: str,
    on_event: Any,
    upstream: PersistentUpstreamConnection | None = None,
) -> None:
    """Route one Desktop WebSocket create to native Responses or the chat bridge."""
    input_value = payload.get("input")
    if isinstance(input_value, list) and not input_value:
        _complete_empty_websocket_response(payload, request_id, on_event, config)
        return
    native_model, _fallback_capabilities = select_native_model(payload, config)
    if native_model is not None:
        _stream_native_response_events(payload, config, request_id, on_event, upstream)
        return
    handle_streaming_request(payload, config, request_id, None, on_event=on_event)


def handle_streaming_request(
    payload: Json,
    config: ProxyConfig,
    request_id: str,
    wfile: Any,
    *,
    on_event: Any | None = None,
) -> None:
    """Stream upstream response as SSE in real-time: created → text deltas → completed."""
    chat_payload, request_model, conversion_stats = responses_payload_to_chat_payload(
        payload, zen=_is_zen_upstream(config)
    )

    if conversion_stats.get("has_image") and conversion_stats.get("tools_present"):
        chat_payload = caption_images_in_messages(chat_payload, request_model, config, request_id)
        conversion_stats["upstream_model"] = chat_payload.get("model")

    chat_payload["stream"] = True
    trace("request.converted", request_id=request_id, stats=conversion_stats,
          upstream_model=chat_payload.get("model"), stream=True)

    response_id = new_response_id()
    model = config.external_model(request_model or DEFAULT_MODEL)

    client_alive = True

    def send_event(event: Json) -> None:
        nonlocal client_alive
        if not client_alive:
            return
        if on_event is not None:
            on_event(event)
            return
        try:
            wfile.write(b"data: " + json.dumps(event, separators=(",", ":")).encode("utf-8") + b"\n\n")
            wfile.flush()
        except BrokenPipeError:
            client_alive = False
            trace("client.disconnected", request_id=request_id, message="client closed connection during stream")

    def send_error(msg: str) -> None:
        send_event({"type": "response.error", "error": {"message": msg}})
        if client_alive and on_event is None:
            wfile.write(b"data: [DONE]\n\n")
            wfile.flush()

    try:
        api_key = resolve_api_key(config, request_id)
    except ProxyError as exc:
        send_error(exc.message)
        return

    send_event({"type": "response.created", "response": {
        "id": response_id, "object": "response", "created_at": now_unix(),
        "status": "in_progress", "model": model, "output": [], "output_text": "", "usage": None,
    }})

    url = f"{config.chat_base_url}/chat/completions"
    raw_payload = json.dumps(chat_payload, separators=(",",":")).encode("utf-8")
    req = urllib.request.Request(url, data=raw_payload, headers={
        "authorization": f"Bearer {api_key}", "content-type": "application/json",
        "accept": "text/event-stream",
        "user-agent": os.environ.get("OPENCODE_GO_PROXY_USER_AGENT", "codex/1.0"),
    }, method="POST")
    trace("upstream.start", request_id=request_id, url=url, bytes=len(raw_payload), stream=True)
    started = time.time()

    text = ""
    reasoning = ""
    tool_calls: list[Json] = []
    tool_call_items: dict[int, Json] = {}  # index → {id, call_id, name, namespace}
    tool_call_open: set[int] = set()  # indices already emitted as output_item.added
    usage: Json | None = None
    message_id = f"msg_{uuid.uuid4().hex}"
    reasoning_id = f"rs_{uuid.uuid4().hex}"
    item_open = False
    reasoning_open = False
    got_data = False

    # Keepalive: send SSE comments every 15s while waiting for upstream first byte.
    # Prevents Codex from timing out when the model thinks for 30+ seconds before responding.
    keepalive_stop = threading.Event()

    def keepalive() -> None:
        if on_event is not None:
            return
        while not keepalive_stop.wait(15):
            if not client_alive:
                return
            try:
                wfile.write(b": keepalive\n\n")
                wfile.flush()
            except BrokenPipeError:
                return

    ka_thread = threading.Thread(target=keepalive, daemon=True)
    ka_thread.start()

    try:
        with urllib.request.urlopen(req, timeout=config.timeout_sec) as resp:
            keepalive_stop.set()  # Stop keepalive once upstream starts responding.
            for line in resp:
                line = line.decode("utf-8", errors="replace").strip()
                if not line.startswith("data: "):
                    continue
                got_data = True
                data = line[6:]
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if chunk.get("usage"):
                    usage = chunk["usage"]
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta", {})
                # Reasoning — stream summary deltas so Codex shows thinking text in real-time.
                r = delta.get("reasoning_content")
                if isinstance(r, str) and r:
                    if not reasoning_open:
                        send_event({"type": "response.output_item.added", "output_index": 0, "item": {
                            "type": "reasoning", "id": reasoning_id, "summary": [], "status": "in_progress",
                        }})
                        reasoning_open = True
                    reasoning += r
                    send_event({"type": "response.reasoning_summary_text.delta",
                                "item_id": reasoning_id, "output_index": 0, "summary_index": 0, "delta": r})
                # Text delta — open item lazily, then stream.
                d = delta.get("content")
                if isinstance(d, str) and d:
                    if not item_open:
                        idx = 1 if reasoning_open else 0
                        send_event({"type": "response.output_item.added", "output_index": idx, "item": {
                            "type": "message", "id": message_id, "role": "assistant",
                            "status": "in_progress", "content": [],
                        }})
                        item_open = True
                    text += d
                    send_event({"type": "response.output_text.delta", "item_id": message_id, "output_index": 1 if reasoning_open else 0, "delta": d})
                tcs = delta.get("tool_calls")
                if isinstance(tcs, list) and tcs and reasoning_open:
                    # Close reasoning item before tool calls so UI shows tool calls, not "thinking".
                    rs_done = {"type": "reasoning", "id": reasoning_id,
                               "summary": [{"type": "summary_text", "text": reasoning}], "status": "completed"}
                    send_event({"type": "response.output_item.done", "output_index": 0, "item": rs_done})
                    reasoning_open = False
                if isinstance(tcs, list):
                    for tc in tcs:
                        idx = tc.get("index", 0)
                        while len(tool_calls) <= idx:
                            tool_calls.append({"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                        if tc.get("id"):
                            tool_calls[idx]["id"] = tc["id"]
                        fn = tc.get("function") or {}
                        name_delta = fn.get("name")
                        if name_delta:
                            tool_calls[idx]["function"]["name"] += name_delta
                            # Emit output_item.added as soon as we have the full tool name.
                            # DeepSeek sends the name in one chunk, so first non-empty name = complete.
                            if idx not in tool_call_open and tool_calls[idx]["function"]["name"]:
                                flat_name = tool_calls[idx]["function"]["name"]
                                ns, _, n = flat_name.rpartition("__")
                                if not ns or not n:
                                    ns, n = None, flat_name
                                fc_id = f"fc_{uuid.uuid4().hex}"
                                call_id = tool_calls[idx]["id"] or f"call_{uuid.uuid4().hex}"
                                tc_item: Json = {
                                    "type": "function_call", "id": fc_id,
                                    "call_id": call_id, "name": n,
                                    "arguments": "", "status": "in_progress",
                                }
                                if ns:
                                    tc_item["namespace"] = ns
                                tool_call_items[idx] = tc_item
                                tc_base = 1 if reasoning_open else 0
                                send_event({"type": "response.output_item.added",
                                            "output_index": tc_base + idx, "item": tc_item})
                                tool_call_open.add(idx)
                        if fn.get("arguments"):
                            tool_calls[idx]["function"]["arguments"] += fn["arguments"]
    except urllib.error.HTTPError as exc:
        keepalive_stop.set()
        body = exc.read().decode("utf-8", errors="replace")
        trace("upstream.error", request_id=request_id, status=exc.code, body=body[:2000])
        if exc.code == 429:
            retry_after = exc.headers.get("retry-after", "5")
            send_error(f"rate limited (retry after {retry_after}s)")
        elif exc.code in (500, 502, 503, 504):
            send_error(f"upstream unavailable (HTTP {exc.code})")
        else:
            send_error(f"upstream HTTP {exc.code}")
        return
    except (urllib.error.URLError, TimeoutError) as exc:
        keepalive_stop.set()
        trace("upstream.network_error", request_id=request_id, reason=str(getattr(exc, "reason", exc)))
        send_error(f"upstream network error: {getattr(exc, 'reason', exc)}")
        return

    trace("upstream.done", request_id=request_id, status=200,
          elapsed_ms=int((time.time() - started) * 1000), stream=True)

    if not got_data:
        send_error("upstream returned no SSE data")
        return

    if not client_alive:
        trace("client.gone", request_id=request_id, message="client disconnected before final events")
        return

    # Build final response from accumulated data.
    fake_msg: Json = {}
    if reasoning:
        fake_msg["reasoning_content"] = reasoning
    if tool_calls:
        fake_msg["tool_calls"] = tool_calls
    if text:
        fake_msg["content"] = text
    output = chat_message_to_response_output(fake_msg)

    # Close reasoning item if opened.
    if reasoning_open:
        rs_done = {"type": "reasoning", "id": reasoning_id,
                    "summary": [{"type": "summary_text", "text": reasoning}], "status": "completed"}
        send_event({"type": "response.output_item.done", "output_index": 0, "item": rs_done})

    # Emit output_item.done for tool calls that were opened during streaming,
    # and added+done for any that weren't (e.g. name arrived in non-stream chunk).
    tc_base = 1 if reasoning_open else 0
    tc_count = 0
    for item in output:
        if item.get("type") != "function_call":
            continue
        idx = tc_base + tc_count
        if tc_count in tool_call_open:
            # Already emitted added; update with final arguments and close.
            done_item = dict(tool_call_items[tc_count])
            done_item["arguments"] = item.get("arguments", "{}")
            done_item["status"] = "completed"
            send_event({"type": "response.output_item.done", "output_index": idx, "item": done_item})
        else:
            send_event({"type": "response.output_item.added", "output_index": idx, "item": item})
            send_event({"type": "response.output_item.done", "output_index": idx, "item": item})
        tc_count += 1

    # Close message item if opened.
    if item_open:
        msg_idx = tc_base + len(tool_calls)
        msg_done = {"type": "message", "id": message_id, "role": "assistant", "status": "completed",
                     "content": [{"type": "output_text", "text": text, "annotations": []}]}
        send_event({"type": "response.output_item.done", "output_index": msg_idx, "item": msg_done})

    final: Json = {
        "id": response_id, "object": "response", "created_at": now_unix(),
        "status": "completed", "model": model, "output": output,
        "output_text": text, "usage": normalize_usage(usage),
    }
    send_event({"type": "response.completed", "response": final})
    if on_event is None:
        wfile.write(b"data: [DONE]\n\n")
        wfile.flush()
    trace("response.converted", request_id=request_id, output_items=len(output),
          output_text_len=len(text), usage=final.get("usage"), stream=True)


def caption_images_in_messages(chat_payload: Json, target_model: str, config: ProxyConfig, request_id: str) -> Json:
    """Replace image_url parts with MiMo-generated text captions. Routes turn to target_model after."""
    image_model = os.environ.get("CODEX_IMAGE_MODEL", IMAGE_MODEL_DEFAULT) or IMAGE_MODEL_DEFAULT
    messages = chat_payload.get("messages", [])

    # Collect all image URLs across messages.
    image_jobs: list[tuple[int, int, str]] = []  # (msg_idx, part_idx, url)
    for mi, message in enumerate(messages):
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for pi, part in enumerate(content):
            if isinstance(part, dict) and part.get("type") == "image_url":
                url = (part.get("image_url") or {}).get("url", "")
                if url:
                    image_jobs.append((mi, pi, url))

    if not image_jobs:
        chat_payload["model"] = target_model
        return chat_payload

    # Only caption the latest image; stub older ones to save 25+ seconds per turn.
    # Old screenshots are stale context — the model only needs the current screen to act.
    latest = image_jobs[-1]
    caption = caption_image_via_mimo(latest[2], image_model, config, request_id)
    for mi, pi, _url in image_jobs[:-1]:
        messages[mi]["content"][pi] = {"type": "text", "text": "[prior screenshot omitted]"}
    mi, pi, _ = latest
    messages[mi]["content"][pi] = {"type": "text", "text": f"[screenshot: {caption}]"}

    # Collapse text-only lists back to strings (fast path for upstream).
    for message in messages:
        content = message.get("content")
        if isinstance(content, list) and all(
            isinstance(p, dict) and p.get("type") == "text" for p in content
        ):
            message["content"] = "\n".join(p.get("text", "") for p in content if p.get("text"))

    chat_payload["model"] = target_model
    trace("split_turn.captioned", request_id=request_id, captions=1, omitted=len(image_jobs) - 1, model=chat_payload["model"])
    return chat_payload


CAPTION_PROMPT = (
    "You are captioning a screenshot for a coding agent that cannot see images. "
    "The agent needs to click elements precisely, so spatial positions are critical. "
    "Describe in 4-6 sentences: (1) app name and what window/panel is active, "
    "(2) list every clickable element with its approximate position as (x,y) pixels "
    "from top-left — buttons, menu items, links, input fields, toolbar icons. "
    "Format: 'button \"Save\" at (120, 45)', 'input field at (300, 200)', etc. "
    "(3) any visible text content — quote exactly. "
    "(4) where the cursor/focus/selection currently is. "
    "Skip colors and styling unless they convey state (e.g. red error, green success)."
)


def caption_image_via_mimo(image_url: str, image_model: str, config: ProxyConfig, request_id: str) -> str:
    """Sub-call MiMo to caption a single image. Returns text description."""
    caption_payload: Json = {
        "model": image_model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": CAPTION_PROMPT},
                    {"type": "image_url", "image_url": {"url": image_url}},
                ],
            }
        ],
        "stream": False,
        "max_tokens": 200,
    }
    try:
        chat = call_upstream_chat(caption_payload, config, request_id, timeout_sec=15.0)
        choice = (chat.get("choices") or [{}])[0]
        text = (choice.get("message", {}) or {}).get("content", "")
        return text.strip() if isinstance(text, str) and text.strip() else "[caption unavailable]"
    except ProxyError as exc:
        trace("split_turn.caption_failed", request_id=request_id, status=exc.status, message=exc.message[:200])
        return f"[caption failed: {exc.message[:100]}]"


def call_upstream_chat(chat_payload: Json, config: ProxyConfig, request_id: str, *, timeout_sec: float | None = None) -> Json:
    api_key = resolve_api_key(config, request_id)

    url = f"{config.chat_base_url}/chat/completions"
    raw_payload = json.dumps(chat_payload, separators=(",",":")).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=raw_payload,
        headers={
            "authorization": f"Bearer {api_key}",
            "content-type": "application/json",
            "accept": "application/json",
            "user-agent": os.environ.get("OPENCODE_GO_PROXY_USER_AGENT", "codex/1.0"),
        },
        method="POST",
    )
    trace("upstream.start", request_id=request_id, url=url, bytes=len(raw_payload))
    started = time.time()
    try:
        with urllib.request.urlopen(request, timeout=timeout_sec or config.timeout_sec) as response:
            body = response.read()
            elapsed_ms = int((time.time() - started) * 1000)
            trace("upstream.done", request_id=request_id, status=response.status, bytes=len(body), elapsed_ms=elapsed_ms)
            try:
                value = json.loads(body)
            except json.JSONDecodeError:
                raise ProxyError(HTTPStatus.BAD_GATEWAY, "upstream returned invalid JSON")
            if not isinstance(value, dict):
                raise ProxyError(HTTPStatus.BAD_GATEWAY, "upstream returned non-object JSON")
            return value
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        trace("upstream.error", request_id=request_id, status=exc.code, body=body[:2000])
        if exc.code == 429:
            retry_after = exc.headers.get("retry-after", "5")
            raise ProxyError(HTTPStatus.TOO_MANY_REQUESTS, f"rate limited (retry after {retry_after}s)") from exc
        if exc.code == 503:
            raise ProxyError(HTTPStatus.SERVICE_UNAVAILABLE, "upstream unavailable") from exc
        if exc.code == 504:
            raise ProxyError(HTTPStatus.GATEWAY_TIMEOUT, "upstream timeout") from exc
        raise ProxyError(HTTPStatus.BAD_GATEWAY, f"upstream HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        trace("upstream.network_error", request_id=request_id, reason=str(exc.reason))
        raise ProxyError(HTTPStatus.BAD_GATEWAY, f"upstream network error: {exc.reason}") from exc


_api_key_cache: str | None = None
_api_key_lock = threading.Lock()


def resolve_api_key(config: ProxyConfig, request_id: str) -> str:
    global _api_key_cache
    if _api_key_cache:
        return _api_key_cache

    with _api_key_lock:
        if _api_key_cache:
            return _api_key_cache

        api_key = os.environ.get(config.api_key_env)
        if api_key:
            _api_key_cache = api_key
            trace("credential.source", request_id=request_id, source="env", env=config.api_key_env)
            return api_key

        keychain_service = os.environ.get("CODEX_KEYCHAIN_SERVICE", "opencode-go-api-key")
        trace("credential.lookup", request_id=request_id, source="keychain", service=keychain_service)
        try:
            completed = subprocess.run(
                ["security", "find-generic-password", "-a", os.environ.get("USER", ""), "-s", keychain_service, "-w"],
                check=False, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=10,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            completed = None
        if completed and completed.returncode == 0:
            first_line = completed.stdout.splitlines()[0].strip() if completed.stdout.splitlines() else ""
            if first_line:
                _api_key_cache = first_line
                trace("credential.source", request_id=request_id, source="keychain", service=keychain_service)
                return first_line

        raise ProxyError(HTTPStatus.UNAUTHORIZED, f"missing API key: set ${config.api_key_env} or keychain:{keychain_service}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Codex Responses API shim for OpenAI Chat Completions upstreams")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--bind", default=os.environ.get("OPENCODE_GO_PROXY_BIND", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("OPENCODE_GO_PROXY_PORT", "8787")))
    parser.add_argument(
        "--upstream",
        choices=("go", "zen", "combined"),
        default=os.environ.get("OPENCODE_UPSTREAM", "go"),
        help="OpenCode upstream product (default: go)",
    )
    parser.add_argument(
        "--chat-base-url",
        dest="chat_base_url",
        default=os.environ.get("CHAT_COMPLETIONS_BASE_URL"),
        help="override the upstream base URL selected by --upstream",
    )
    parser.add_argument("--api-key-env", default=os.environ.get("OPENCODE_GO_PROXY_API_KEY_ENV", "OPENCODE_GO_API_KEY"))
    parser.add_argument(
        "--client-token-env",
        default=os.environ.get("OPENCODE_GO_PROXY_CLIENT_TOKEN_ENV", "OPENCODE_GO_PROXY_CLIENT_TOKEN"),
        help="environment variable containing an optional bearer token required from clients",
    )
    parser.add_argument("--timeout-sec", type=float, default=float(os.environ.get("OPENCODE_GO_PROXY_TIMEOUT_SEC", "180")))
    parser.add_argument("--max-body-mb", type=int, default=int(os.environ.get("OPENCODE_GO_PROXY_MAX_BODY_MB", "20")))
    parser.add_argument(
        "--configure-codex",
        action="store_true",
        help="create the OpenCode Go Luna provider, profile, and model catalog, then exit",
    )
    parser.add_argument(
        "--configure-codex-zen",
        action="store_true",
        help="create the OpenCode Zen provider, profile, and compatible model catalog, then exit",
    )
    parser.add_argument(
        "--configure-codex-combined",
        action="store_true",
        help="create one prefixed Go + Zen provider, profiles, and combined model catalog, then exit",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.configure_codex:
        config_path, catalog_path = configure_codex()
        print(f"Codex configured: {config_path}")
        print(f"Model catalog: {catalog_path}")
        return
    if args.configure_codex_zen:
        config_path, catalog_path = configure_codex("zen")
        print(f"Codex configured for Zen: {config_path}")
        print(f"Model catalog: {catalog_path}")
        return
    if args.configure_codex_combined:
        config_path, catalog_path = configure_codex("combined")
        print(f"Codex configured for combined Go + Zen: {config_path}")
        print(f"Model catalog: {catalog_path}")
        return
    config = ProxyConfig(
        bind=args.bind,
        port=args.port,
        chat_base_url=args.chat_base_url or (ZEN_BASE_URL if args.upstream == "zen" else GO_BASE_URL),
        api_key_env=args.api_key_env,
        client_token_env=args.client_token_env,
        timeout_sec=args.timeout_sec,
        max_body_bytes=args.max_body_mb * 1024 * 1024,
        upstream=args.upstream,
    )
    if config.bind not in {"127.0.0.1", "localhost", "::1"}:
        trace("security.warning", bind=config.bind,
              message="binding to non-localhost address — proxy exposes upstream API key to network")
    server = ThreadingHTTPServer((config.bind, config.port), ResponsesProxyHandler)
    server.config = config  # type: ignore[attr-defined]
    trace(
        "server.start",
        bind=config.bind,
        port=config.port,
        chat_base_url=config.chat_base_url,
        api_key_env=config.api_key_env,
        upstream=config.upstream,
    )
    # serve_forever in a background thread: shutdown() from a signal handler
    # running on the main thread would otherwise deadlock (both need the main
    # thread), leaving the process unkillable via SIGTERM.
    serve_thread = threading.Thread(target=server.serve_forever, daemon=True)
    signal.signal(signal.SIGTERM, lambda *_: server.shutdown())
    try:
        serve_thread.start()
        serve_thread.join()
    except KeyboardInterrupt:
        trace("server.stop", reason="keyboard_interrupt")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
