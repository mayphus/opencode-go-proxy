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

    def test_configures_one_combined_provider_with_prefixed_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(os.environ, {"CODEX_HOME": directory}):
            config_path, catalog_path = configure_codex("combined")
            config = tomllib.loads(config_path.read_text(encoding="utf-8"))
            catalog = json.loads(catalog_path.read_text(encoding="utf-8"))

        self.assertEqual(config["model_providers"]["opencode"]["wire_api"], "responses")
        self.assertEqual(config["profiles"]["luna-go"]["model"], "go/gpt-5.6-luna")
        self.assertEqual(config["profiles"]["luna-zen"]["model"], "zen/gpt-5.6-luna")
        self.assertEqual(config["profiles"]["deepseek-zen"]["model"], "zen/deepseek-v4-flash-free")
        slugs = {model["slug"] for model in catalog["models"]}
        self.assertIn("go/gpt-5.6-luna", slugs)
        self.assertIn("zen/gpt-5.6-luna", slugs)
        self.assertIn("zen/deepseek-v4-flash-free", slugs)
        self.assertNotIn("gpt-5.6-luna", slugs)

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
