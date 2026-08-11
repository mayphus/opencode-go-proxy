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

    def test_repeated_configuration_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(os.environ, {"CODEX_HOME": directory}):
            config_path, _ = configure_codex()
            first = config_path.read_text(encoding="utf-8")
            configure_codex()
            second = config_path.read_text(encoding="utf-8")

        self.assertEqual(first, second)
        self.assertEqual(first.count("[model_providers.opencode-go]"), 1)
        self.assertEqual(first.count(f'[profiles."{MODEL_SLUG}"]'), 1)

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
