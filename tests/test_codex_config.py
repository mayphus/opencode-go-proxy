import json
import os
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

from opencode_go_proxy.codex_config import MODEL_SLUG, configure_codex


class CodexConfigTests(unittest.TestCase):
    def test_configures_luna_provider_profile_and_selector_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(os.environ, {"CODEX_HOME": directory}):
            config_path, catalog_path = configure_codex()

            config = tomllib.loads(config_path.read_text(encoding="utf-8"))
            catalog = json.loads(catalog_path.read_text(encoding="utf-8"))

        self.assertEqual(config["model_providers"]["opencode-go"]["wire_api"], "responses")
        self.assertEqual(config["profiles"][MODEL_SLUG]["model"], MODEL_SLUG)
        self.assertEqual(config["model_catalog_json"], str(catalog_path))
        self.assertEqual([model["slug"] for model in catalog["models"]], [MODEL_SLUG])
        self.assertTrue(catalog["models"][0]["supports_search_tool"])
        self.assertTrue(catalog["models"][0]["support_verbosity"])
        self.assertTrue(catalog["models"][0]["include_skills_usage_instructions"])
        self.assertEqual(catalog["models"][0]["input_modalities"], ["text", "image"])
        self.assertEqual(catalog["models"][0]["context_window"], 1_050_000)
        self.assertEqual(
            [level["effort"] for level in catalog["models"][0]["supported_reasoning_levels"]],
            ["none", "low", "medium", "high", "xhigh", "max"],
        )

    def test_repeated_configuration_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(os.environ, {"CODEX_HOME": directory}):
            config_path, _ = configure_codex()
            first = config_path.read_text(encoding="utf-8")
            configure_codex()
            second = config_path.read_text(encoding="utf-8")

        self.assertEqual(first, second)
        self.assertEqual(first.count("[model_providers.opencode-go]"), 1)
        self.assertEqual(first.count(f'[profiles."{MODEL_SLUG}"]'), 1)

    def test_configures_zen_provider_and_compatible_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(os.environ, {"CODEX_HOME": directory}):
            config_path, catalog_path = configure_codex("zen")
            config = tomllib.loads(config_path.read_text(encoding="utf-8"))
            catalog = json.loads(catalog_path.read_text(encoding="utf-8"))

        self.assertEqual(config["model_providers"]["opencode-zen"]["wire_api"], "responses")
        self.assertEqual(config["profiles"][f"{MODEL_SLUG}-zen"]["model"], MODEL_SLUG)
        slugs = {model["slug"] for model in catalog["models"]}
        self.assertIn("gpt-5.6-sol", slugs)
        self.assertIn("deepseek-v4-flash-free", slugs)
        self.assertNotIn("claude-sonnet-5", slugs)
        deepseek = next(model for model in catalog["models"] if model["slug"] == "deepseek-v4-flash-free")
        self.assertFalse(deepseek["use_responses_lite"])
        self.assertTrue(deepseek["supports_search_tool"])

    def test_configures_endpoint_routed_go_and_zen_providers(self) -> None:
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(os.environ, {"CODEX_HOME": directory}):
            config_path, catalog_path = configure_codex("combined")
            config = tomllib.loads(config_path.read_text(encoding="utf-8"))
            catalog = json.loads(catalog_path.read_text(encoding="utf-8"))

        self.assertEqual(config["model_providers"]["opencode-go"]["base_url"], "http://127.0.0.1:8787/zen/go/v1")
        self.assertEqual(config["model_providers"]["opencode-zen"]["base_url"], "http://127.0.0.1:8787/zen/v1")
        self.assertEqual(config["profiles"]["luna-go"]["model_provider"], "opencode-go")
        self.assertEqual(config["profiles"]["luna-go"]["model"], "gpt-5.6-luna")
        self.assertEqual(config["profiles"]["luna-zen"]["model_provider"], "opencode-zen")
        self.assertEqual(config["profiles"]["luna-zen"]["model"], "gpt-5.6-luna")
        self.assertEqual(config["profiles"]["deepseek-zen"]["model"], "deepseek-v4-flash-free")
        self.assertEqual(config["model_catalog_json"], str(catalog_path))
        self.assertEqual(set(catalog), {"fetched_at", "etag", "client_version", "models"})
        slugs = {model["slug"] for model in catalog["models"]}
        self.assertEqual(len(slugs), len(catalog["models"]))
        self.assertIn("gpt-5.6-luna", slugs)
        self.assertIn("deepseek-v4-flash-free", slugs)
        self.assertFalse(any("/" in slug for slug in slugs))

    def test_combined_setup_migrates_prefixed_profiles(self) -> None:
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(os.environ, {"CODEX_HOME": directory}):
            config_path = Path(directory) / "config.toml"
            config_path.write_text(
                '[profiles."luna-go"]\nmodel_provider = "opencode"\nmodel = "go/gpt-5.6-luna"\n',
                encoding="utf-8",
            )

            configure_codex("combined")
            config = tomllib.loads(config_path.read_text(encoding="utf-8"))

        self.assertEqual(config["profiles"]["luna-go"]["model_provider"], "opencode-go")
        self.assertEqual(config["profiles"]["luna-go"]["model"], "gpt-5.6-luna")

    def test_existing_config_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(os.environ, {"CODEX_HOME": directory}):
            codex_home = Path(directory)
            codex_home.mkdir(parents=True, exist_ok=True)
            config_path = codex_home / "config.toml"
            config_path.write_text('[profiles.existing]\nmodel = "other"\n', encoding="utf-8")

            configure_codex()
            config = tomllib.loads(config_path.read_text(encoding="utf-8"))

        self.assertEqual(config["profiles"]["existing"]["model"], "other")
        self.assertEqual(config["profiles"][MODEL_SLUG]["model"], MODEL_SLUG)


if __name__ == "__main__":
    unittest.main()
