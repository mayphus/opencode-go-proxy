from __future__ import annotations

import argparse
import base64
import binascii
import json
import os
import struct
import sys
import urllib.error
import urllib.request
import zlib
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

Json = dict[str, Any]

CAPABILITY_CHECKS = {
    "text": "text",
    "structured_output": "structured_output",
    "vision": "image",
    "web_search": "web_search",
    "function_tools": "function_tools",
    "custom_tools": "custom_tools",
    "stateless_reasoning": "persisted_reasoning",
    "prompt_cache_options": "prompt_caching",
}
NATIVE_CAPABILITIES = (
    "text",
    "structured_output",
    "image",
    "web_search",
    "function_tools",
    "custom_tools",
    "file_search",
    "computer_use",
    "code_interpreter",
    "image_generation",
    "mcp",
    "tool_search",
    "hosted_shell",
    "skills",
    "programmatic_tool_calling",
    "multi_agent",
    "pro_mode",
    "prompt_caching",
    "persisted_reasoning",
    "background",
    "compaction",
)

def _png_chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", binascii.crc32(kind + data) & 0xFFFFFFFF)


def red_png() -> bytes:
    """Build a dependency-free, opaque 32x32 true-color red PNG."""
    width = height = 32
    rows = b"".join(b"\x00" + b"\xff\x00\x00" * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + _png_chunk(b"IDAT", zlib.compress(rows))
        + _png_chunk(b"IEND", b"")
    )


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str


class ResponsesClient:
    def __init__(self, base_url: str, token: str | None, timeout: float) -> None:
        self.url = f"{base_url.rstrip('/')}/responses"
        self.token = token
        self.timeout = timeout

    def create(self, payload: Json) -> Json:
        headers = {"content-type": "application/json", "accept": "application/json"}
        if self.token:
            headers["authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(
            self.url,
            data=json.dumps(payload, separators=(",", ":")).encode(),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                value = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            body = exc.read().decode(errors="replace")
            raise RuntimeError(f"HTTP {exc.code}: {body[:300]}") from exc
        if not isinstance(value, dict):
            raise TypeError("response was not a JSON object")
        return value


def output_text(response: Json) -> str:
    direct = response.get("output_text")
    if isinstance(direct, str):
        return direct
    parts: list[str] = []
    for item in response.get("output", []):
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if isinstance(content, dict) and isinstance(content.get("text"), str):
                parts.append(content["text"])
    return "".join(parts)


def output_items(response: Json, item_type: str) -> list[Json]:
    output = response.get("output")
    if not isinstance(output, list):
        return []
    return [item for item in output if isinstance(item, dict) and item.get("type") == item_type]


def has_url_citation(response: Json) -> bool:
    for item in output_items(response, "message"):
        for content in item.get("content", []):
            if not isinstance(content, dict):
                continue
            for annotation in content.get("annotations", []):
                if isinstance(annotation, dict) and annotation.get("type") == "url_citation":
                    return True
    return False


def _run(name: str, check: Callable[[], str]) -> CheckResult:
    try:
        return CheckResult(name, True, check())
    except Exception as exc:  # noqa: BLE001 - each check must report independently
        return CheckResult(name, False, str(exc))


def run_verification(
    client: ResponsesClient,
    model: str,
    selected: set[str] | None = None,
) -> list[CheckResult]:
    def text_check() -> str:
        response = client.create({"model": model, "input": "Reply with exactly: verification-ok", "store": False})
        if output_text(response).strip().lower() != "verification-ok":
            raise AssertionError(f"unexpected text: {output_text(response)!r}")
        return "native text response"

    def structured_check() -> str:
        response = client.create({
            "model": model,
            "input": "Return the integer two.",
            "store": False,
            "text": {"format": {"type": "json_schema", "name": "verification", "strict": True, "schema": {
                "type": "object", "properties": {"value": {"type": "integer"}}, "required": ["value"], "additionalProperties": False,
            }}},
        })
        if json.loads(output_text(response)) != {"value": 2}:
            raise AssertionError(f"unexpected structured output: {output_text(response)!r}")
        return "strict JSON schema"

    def vision_check() -> str:
        image = base64.b64encode(red_png()).decode()
        response = client.create({
            "model": model,
            "store": False,
            "input": [{"role": "user", "content": [
                {"type": "input_text", "text": "Name only the dominant color in this image."},
                {"type": "input_image", "image_url": f"data:image/png;base64,{image}"},
            ]}],
        })
        if "red" not in output_text(response).lower():
            raise AssertionError(f"image was not recognized as red: {output_text(response)!r}")
        return "native image input"

    def search_check() -> str:
        response = client.create({
            "model": model,
            "store": False,
            "tools": [{"type": "web_search"}],
            "input": "Search the web for the official Python homepage and cite it in one sentence.",
        })
        if not output_items(response, "web_search_call"):
            raise AssertionError("no native web_search_call output item")
        if not has_url_citation(response):
            raise AssertionError("no URL citation in the answer")
        return "native web search with citation"

    def function_check() -> str:
        first_input = "You must call get_verification_value, then report its returned value."
        tool = {"type": "function", "name": "get_verification_value", "description": "Return a verification value.", "parameters": {"type": "object", "properties": {}, "additionalProperties": False}}
        first = client.create({"model": model, "store": False, "input": first_input, "tools": [tool], "tool_choice": {"type": "function", "name": "get_verification_value"}})
        calls = output_items(first, "function_call")
        if not calls or not isinstance(calls[0].get("call_id"), str):
            raise AssertionError("no function_call output item")
        second = client.create({"model": model, "store": False, "tools": [tool], "input": [
            {"role": "user", "content": first_input},
            *first.get("output", []),
            {"type": "function_call_output", "call_id": calls[0]["call_id"], "output": "function-roundtrip-ok"},
        ]})
        if "function-roundtrip-ok" not in output_text(second):
            raise AssertionError(f"function result was not consumed: {output_text(second)!r}")
        return "function tool round trip"

    def custom_check() -> str:
        first_input = "You must use browser_open to open https://example.com. Do not infer its contents. After the tool returns, repeat the secret token found only in the tool result."
        tool = {"type": "custom", "name": "browser_open", "description": "Open a URL in the app browser."}
        first = client.create({"model": model, "store": False, "input": first_input, "tools": [tool]})
        calls = output_items(first, "custom_tool_call")
        if not calls or not isinstance(calls[0].get("call_id"), str):
            raise AssertionError("no custom_tool_call output item")
        second = client.create({"model": model, "store": False, "tools": [tool], "input": [
            {"role": "user", "content": first_input},
            *first.get("output", []),
            {"type": "custom_tool_call_output", "call_id": calls[0]["call_id"], "output": "Browser page content: the secret token is browser-roundtrip-ok. Repeat that token."},
        ]})
        if "browser-roundtrip-ok" not in output_text(second):
            raise AssertionError(f"custom tool result was not consumed: {output_text(second)!r}")
        return "custom app-tool round trip"

    def stateless_reasoning_check() -> str:
        response = client.create({
            "model": model,
            "input": "Reply with exactly: reasoning-context-ok",
            "store": False,
            "reasoning": {"effort": "low", "context": "all_turns"},
        })
        if output_text(response).strip().lower() != "reasoning-context-ok":
            raise AssertionError(f"unexpected reasoning response: {output_text(response)!r}")
        return "store=false with reasoning.context=all_turns"

    def prompt_cache_check() -> str:
        response = client.create({
            "model": model,
            "input": "Reply with exactly: prompt-cache-ok",
            "store": False,
            "prompt_cache_options": {"mode": "implicit"},
        })
        if output_text(response).strip().lower() != "prompt-cache-ok":
            raise AssertionError(f"unexpected prompt-cache response: {output_text(response)!r}")
        return "prompt_cache_options accepted"

    checks = [
        ("text", text_check),
        ("structured_output", structured_check),
        ("vision", vision_check),
        ("web_search", search_check),
        ("function_tools", function_check),
        ("custom_tools", custom_check),
        ("stateless_reasoning", stateless_reasoning_check),
        ("prompt_cache_options", prompt_cache_check),
    ]
    return [_run(name, check) for name, check in checks if selected is None or name in selected]


def verification_report(model: str, results: list[CheckResult]) -> Json:
    capabilities: Json = {
        capability: {"status": "untested", "detail": "No live evidence recorded."}
        for capability in NATIVE_CAPABILITIES
    }
    for result in results:
        capability = CAPABILITY_CHECKS[result.name]
        capabilities[capability] = {
            "status": "verified" if result.passed else "rejected",
            "detail": result.detail,
        }
    return {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "model": model,
        "passed": all(item.passed for item in results),
        "checks": [asdict(item) for item in results],
        "capabilities": capabilities,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify GPT 5.6 Luna capabilities through opencode-go-proxy")
    parser.add_argument("--base-url", default=os.environ.get("OPENCODE_GO_PROXY_URL", "http://127.0.0.1:8787/v1"))
    parser.add_argument("--client-token", default=os.environ.get("OPENCODE_GO_PROXY_CLIENT_TOKEN"))
    parser.add_argument("--model", default="gpt-5.6-luna")
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument(
        "--checks",
        help="Comma-separated checks to run; use --list-checks to see names.",
    )
    parser.add_argument("--list-checks", action="store_true")
    parser.add_argument(
        "--report",
        type=Path,
        default=Path(os.environ["OPENCODE_CAPABILITY_REPORT"]) if os.environ.get("OPENCODE_CAPABILITY_REPORT") else None,
        help="Write a dashboard-compatible JSON evidence report.",
    )
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()

    if args.list_checks:
        print("\n".join(CAPABILITY_CHECKS))
        return
    selected = {item.strip() for item in args.checks.split(",") if item.strip()} if args.checks else None
    unknown = (selected or set()) - set(CAPABILITY_CHECKS)
    if unknown:
        parser.error(f"unknown checks: {', '.join(sorted(unknown))}")
    results = run_verification(
        ResponsesClient(args.base_url, args.client_token, args.timeout),
        args.model,
        selected,
    )
    report = verification_report(args.model, results)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if args.as_json:
        print(json.dumps(report, indent=2))
    else:
        for item in results:
            print(f"{'PASS' if item.passed else 'FAIL'}  {item.name}: {item.detail}")
        print(f"\n{sum(item.passed for item in results)}/{len(results)} checks passed")
    if not all(item.passed for item in results):
        sys.exit(1)


if __name__ == "__main__":
    main()
