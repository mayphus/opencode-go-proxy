import json
import os
import subprocess
import unittest
from http import HTTPStatus
from unittest import mock

from opencode_go_proxy.app import (
    ProxyConfig,
    ProxyError,
    call_upstream_responses,
    resolve_api_key,
)


def make_config() -> ProxyConfig:
    return ProxyConfig(
        bind="127.0.0.1",
        port=8787,
        chat_base_url="https://opencode.ai/zen/go/v1",
        api_key_env="OPENCODE_GO_API_KEY",
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


if __name__ == "__main__":
    unittest.main()
