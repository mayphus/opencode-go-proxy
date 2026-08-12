import json
import os
import subprocess
import unittest
from http import HTTPStatus
from unittest import mock

from opencode_go_proxy.app import (
    ProxyConfig,
    ProxyError,
    _stream_native_response_events,
    call_upstream_responses,
    resolve_api_key,
    sanitize_websocket_payload,
)


def make_config() -> ProxyConfig:
    return ProxyConfig(
        bind="127.0.0.1",
        port=8787,
        chat_base_url="https://opencode.ai/zen/go/v1",
        api_key_env="OPENCODE_GO_API_KEY",
        client_token_env="OPENCODE_GO_PROXY_CLIENT_TOKEN",
        timeout_sec=1,
        max_body_bytes=20 * 1024 * 1024,
    )


class CredentialTests(unittest.TestCase):
    def setUp(self) -> None:
        # Reset the module-level cache between tests.
        import opencode_go_proxy.app as app_mod
        app_mod._api_key_cache = None

    def test_env_key_wins_without_keychain_lookup(self) -> None:
        with mock.patch.dict(os.environ, {"OPENCODE_GO_API_KEY": "env-key"}, clear=True), mock.patch("opencode_go_proxy.app.subprocess.run") as run:
            self.assertEqual(resolve_api_key(make_config(), "req"), "env-key")

        run.assert_not_called()

    def test_keychain_lookup_uses_first_line(self) -> None:
        completed = subprocess.CompletedProcess(
            ["security", "find-generic-password", "-a", os.environ.get("USER", ""), "-s", "opencode-go-api-key", "-w"],
            0,
            stdout="keychain-key\n",
            stderr="",
        )
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch("opencode_go_proxy.app.subprocess.run", return_value=completed):
            self.assertEqual(resolve_api_key(make_config(), "req"), "keychain-key")

    def test_missing_key_names_env_and_keychain(self) -> None:
        completed = subprocess.CompletedProcess(
            ["security", "find-generic-password", "-a", os.environ.get("USER", ""), "-s", "opencode-go-api-key", "-w"],
            1,
            stdout="",
            stderr="could not be found",
        )
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch(
            "opencode_go_proxy.app.subprocess.run", return_value=completed
        ), self.assertRaises(ProxyError) as ctx:
            resolve_api_key(make_config(), "req")

        self.assertEqual(ctx.exception.status, HTTPStatus.UNAUTHORIZED)
        self.assertIn("$OPENCODE_GO_API_KEY", ctx.exception.message)
        self.assertIn("keychain", ctx.exception.message)


class NativeResponsesTests(unittest.TestCase):
    def test_luna_payload_is_sent_to_responses_endpoint_unchanged(self) -> None:
        upstream_response = mock.MagicMock()
        upstream_response.status = 200
        upstream_response.read.return_value = json.dumps({"id": "resp_1", "output": []}).encode()
        with mock.patch.dict(os.environ, {"OPENCODE_GO_API_KEY": "test-key"}), mock.patch(
            "opencode_go_proxy.app.urllib.request.urlopen", return_value=upstream_response
        ) as urlopen:
            upstream_response.__enter__.return_value = upstream_response
            result = call_upstream_responses(
                {"model": "gpt-5.6-luna", "input": "hello", "tools": [{"type": "web_search_preview"}]},
                make_config(),
                "req",
            )

        self.assertEqual(result["id"], "resp_1")
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://opencode.ai/zen/go/v1/responses")
        self.assertEqual(json.loads(request.data), {
            "model": "gpt-5.6-luna",
            "input": "hello",
            "tools": [{"type": "web_search_preview"}],
        })


class WebSocketResponsesTests(unittest.TestCase):
    def test_sanitizer_uses_stateless_history_and_preserves_reasoning(self) -> None:
        result = sanitize_websocket_payload({
            "type": "response.create",
            "model": "gpt-5.6-luna",
            "previous_response_id": "resp_old",
            "store": True,
            "input": [
                {
                    "type": "additional_tools",
                    "tools": [{"type": "function", "name": "browser_open", "parameters": {}}],
                },
                {"type": "reasoning", "encrypted_content": "opaque"},
                {"type": "custom_tool_call_output", "call_id": "call_1", "output": "done"},
            ],
        })

        self.assertNotIn("previous_response_id", result)
        self.assertIs(result["store"], False)
        self.assertEqual(result["input"], [
            {"type": "reasoning", "encrypted_content": "opaque"},
            {"type": "function_call_output", "call_id": "call_1", "output": "done"},
        ])
        self.assertEqual(result["tools"], [
            {"type": "function", "name": "browser_open", "parameters": {}},
            {"type": "web_search"},
        ])
        self.assertEqual(result["context_management"], [
            {"type": "compaction", "compact_threshold": 800000},
        ])

    def test_sanitizer_preserves_verified_native_fields(self) -> None:
        payload = {
            "model": "gpt-5.6-luna",
            "input": "hello",
            "background": False,
            "context_management": [{"type": "compaction", "compact_threshold": 200000}],
            "max_tool_calls": 8,
            "metadata": {"source": "desktop"},
            "safety_identifier": "local-user",
            "service_tier": "auto",
            "prompt_cache_options": {"mode": "implicit"},
        }

        result = sanitize_websocket_payload(payload)

        for key in (
            "background",
            "context_management",
            "max_tool_calls",
            "metadata",
            "safety_identifier",
            "service_tier",
        ):
            self.assertEqual(result[key], payload[key])
        self.assertNotIn("prompt_cache_options", result)

    def test_sanitizer_prunes_history_before_latest_native_compaction(self) -> None:
        result = sanitize_websocket_payload({
            "model": "gpt-5.6-luna",
            "input": [
                {"role": "user", "content": "old"},
                {"type": "compaction", "encrypted_content": "canonical-state"},
                {"role": "user", "content": "new"},
            ],
        })

        self.assertEqual(result["input"], [
            {"type": "compaction", "encrypted_content": "canonical-state"},
            {"role": "user", "content": "new"},
        ])

    def test_sanitizer_deduplicates_extracted_desktop_tools(self) -> None:
        tool = {"type": "function", "name": "browser_open", "parameters": {}}
        result = sanitize_websocket_payload({
            "model": "gpt-5.6-luna",
            "tools": [tool],
            "additional_tools": [tool],
            "input": "open Google",
        })

        self.assertEqual(result["tools"], [tool, {"type": "web_search"}])

    def test_sanitizer_adds_native_search_when_desktop_omits_it(self) -> None:
        result = sanitize_websocket_payload({
            "model": "gpt-5.6-luna",
            "tools": [{"type": "custom", "name": "browser", "description": "Use the browser"}],
            "input": "search the web for today's news",
        })

        self.assertEqual(result["tools"], [
            {"type": "custom", "name": "browser", "description": "Use the browser"},
            {"type": "web_search"},
        ])

    def test_native_search_can_be_disabled(self) -> None:
        with mock.patch.dict(os.environ, {"OPENCODE_GO_NATIVE_SEARCH": "0"}):
            result = sanitize_websocket_payload({
                "model": "gpt-5.6-luna",
                "tools": [{"type": "custom", "name": "browser", "description": "Use the browser"}],
                "input": "open Google",
            })

        self.assertEqual(result["tools"], [
            {"type": "custom", "name": "browser", "description": "Use the browser"},
        ])

    def test_native_compaction_can_be_disabled(self) -> None:
        with mock.patch.dict(os.environ, {"OPENCODE_GO_COMPACT_THRESHOLD": "0"}):
            result = sanitize_websocket_payload({"model": "gpt-5.6-luna", "input": "hello"})

        self.assertNotIn("context_management", result)

    def test_sanitizer_appends_desktop_tools_to_explicit_tools(self) -> None:
        result = sanitize_websocket_payload({
            "model": "gpt-5.6-luna",
            "tools": [{"type": "web_search"}],
            "input": [
                {
                    "type": "additional_tools",
                    "tools": [{"type": "function", "name": "browser_click", "parameters": {}}],
                },
                {"role": "user", "content": "open the page"},
            ],
        })

        self.assertEqual(result["tools"], [
            {"type": "web_search"},
            {"type": "function", "name": "browser_click", "parameters": {}},
        ])
        self.assertEqual(result["input"], [{"role": "user", "content": "open the page"}])

    def test_sanitizer_extracts_top_level_desktop_tools_and_keeps_streaming(self) -> None:
        result = sanitize_websocket_payload({
            "model": "gpt-5.6-luna",
            "stream": True,
            "additional_tools": [
                {"type": "custom", "name": "browser", "description": "Use the browser"},
            ],
            "input": "open Google",
        })

        self.assertIs(result["stream"], True)
        self.assertEqual(result["tools"], [
            {"type": "custom", "name": "browser", "description": "Use the browser"},
            {"type": "web_search"},
        ])

    def test_empty_input_completes_locally_without_upstream_call(self) -> None:
        events: list[dict[str, object]] = []
        upstream = mock.MagicMock()
        with mock.patch("opencode_go_proxy.app.resolve_api_key") as resolve_key, mock.patch(
            "opencode_go_proxy.app.trace"
        ) as trace_mock:
            _stream_native_response_events(
                {"type": "response.create", "model": "gpt-5.6-luna", "input": [], "store": True},
                make_config(),
                "req-empty",
                events.append,
                upstream,
            )

        resolve_key.assert_not_called()
        upstream.request.assert_not_called()
        self.assertEqual([event["type"] for event in events], [
            "response.created",
            "response.in_progress",
            "response.completed",
        ])
        self.assertEqual(events[-1]["response"]["status"], "completed")  # type: ignore[index]
        self.assertTrue(any(call.args[0] == "websocket.empty_completed" for call in trace_mock.call_args_list))

    def test_successful_stream_emits_completion_trace(self) -> None:
        events: list[dict[str, object]] = []
        upstream = mock.MagicMock()
        upstream.request.return_value = [
            b'data: {"type":"response.completed","response":{"status":"completed"}}\n',
            b'data: [DONE]\n',
        ]
        with mock.patch("opencode_go_proxy.app.resolve_api_key", return_value="test-key"), mock.patch(
            "opencode_go_proxy.app.trace"
        ) as trace_mock:
            _stream_native_response_events(
                {"type": "response.create", "model": "gpt-5.6-luna", "input": "hello"},
                make_config(),
                "req-done",
                events.append,
                upstream,
            )

        self.assertEqual(events[0]["type"], "response.completed")
        done_calls = [call for call in trace_mock.call_args_list if call.args[0] == "upstream.websocket.done"]
        self.assertEqual(len(done_calls), 1)
        self.assertEqual(done_calls[0].kwargs["status"], "completed")


if __name__ == "__main__":
    unittest.main()
