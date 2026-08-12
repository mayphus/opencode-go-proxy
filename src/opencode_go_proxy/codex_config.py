from __future__ import annotations

import json
import os
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

from .protocol import GO_MODELS, ZEN_MODELS, ZEN_RESPONSES_MODELS

MODEL_SLUG = "gpt-5.6-luna"
PROVIDER_NAME = "opencode-go"
CATALOG_FILENAME = "opencode-go.json"

CATALOG: dict[str, Any] = {
    "fetched_at": "2026-08-12T00:00:00.000000Z",
    "etag": "W/\"opencode-go-gpt-5.6-luna-v1\"",
    "client_version": "0.147.0",
    "models": [
        {
            "slug": MODEL_SLUG,
            "display_name": "GPT 5.6 Luna",
            "description": "Responses-native GPT model with image input and web search.",
            "default_reasoning_level": "high",
            "supported_reasoning_levels": [
                {"effort": effort} for effort in ("none", "low", "medium", "high", "xhigh", "max")
            ],
            "shell_type": "shell_command",
            "visibility": "list",
            "supported_in_api": True,
            "priority": 100,
            "additional_speed_tiers": [],
            "service_tiers": [],
            "availability_nux": None,
            "upgrade": None,
            "model_messages": {
                "instructions_template": "You are Codex, an agent based on GPT-5. You and the user share one workspace, and your job is to collaborate with them until their goal is genuinely handled.",
                "instructions_variables": None,
                "approvals": None,
                "collaboration_modes": None,
                "auto_review": None,
                "permissions": None,
                "token_budget": {
                    "reminder_threshold_tokens": 6144,
                    "auto_compact_token_limit": 1000000,
                },
            },
            "include_skills_usage_instructions": True,
            "include_plugin_usage_instructions": True,
            "supports_reasoning_summaries": True,
            "default_reasoning_summary": "none",
            "support_verbosity": True,
            "default_verbosity": "low",
            "apply_patch_tool_type": "freeform",
            "web_search_tool_type": "text_and_image",
            "truncation_policy": {"mode": "tokens", "limit": 10000},
            "supports_parallel_tool_calls": True,
            "supports_image_detail_original": True,
            "context_window": 1050000,
            "max_context_window": 1050000,
            "effective_context_window_percent": 95,
            "experimental_supported_tools": [],
            "input_modalities": ["text", "image"],
            "supports_search_tool": True,
            "use_responses_lite": True,
            "tool_mode": "code_mode_only",
            "multi_agent_version": "v2",
            "auto_compact_token_limit": 1000000,
        }
    ],
}


def _zen_catalog() -> dict[str, Any]:
    template = CATALOG["models"][0]
    models = []
    for priority, slug in enumerate(sorted(ZEN_MODELS, key=lambda value: (not value.startswith("gpt-5.6"), value))):
        model = deepcopy(template)
        model.update({
            "slug": slug,
            "display_name": slug.replace("-", " ").title().replace("Gpt", "GPT").replace("Glm", "GLM"),
            "description": "OpenCode Zen model with automatic native capability fallback.",
            "priority": 100 - priority,
            "use_responses_lite": slug in ZEN_RESPONSES_MODELS,
        })
        if not slug.startswith("gpt-5.6-"):
            model["context_window"] = 128000
            model["max_context_window"] = 128000
            model["auto_compact_token_limit"] = 110000
            model["model_messages"]["token_budget"]["auto_compact_token_limit"] = 110000
        models.append(model)
    return {
        "fetched_at": "2026-08-12T00:00:00.000000Z",
        "etag": 'W/"opencode-zen-compatible-v1"',
        "client_version": CATALOG["client_version"],
        "models": models,
    }


def _combined_catalog() -> dict[str, Any]:
    template = CATALOG["models"][0]
    entries: list[dict[str, Any]] = []
    for priority, slug in enumerate(sorted(GO_MODELS | ZEN_MODELS)):
        model = deepcopy(template)
        model.update({
            "slug": slug,
            "display_name": slug.replace('-', ' ').title().replace('Gpt', 'GPT').replace('Glm', 'GLM'),
            "description": "OpenCode model; the provider endpoint selects Go or Zen.",
            "priority": 200 - priority,
            "use_responses_lite": slug in ZEN_RESPONSES_MODELS,
        })
        if not slug.startswith("gpt-5.6-"):
            model["context_window"] = 128000
            model["max_context_window"] = 128000
            model["auto_compact_token_limit"] = 110000
            model["model_messages"]["token_budget"]["auto_compact_token_limit"] = 110000
        entries.append(model)
    return {
        "fetched_at": "2026-08-12T00:00:00.000000Z",
        "etag": 'W/"opencode-endpoint-routed-v1"',
        "client_version": CATALOG["client_version"],
        "models": entries,
    }


def _codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser()


def _section_bounds(lines: list[str], section: str) -> tuple[int, int] | None:
    header = f"[{section}]"
    for index, line in enumerate(lines):
        if line.strip() != header:
            continue
        end = len(lines)
        for next_index in range(index + 1, len(lines)):
            if re.match(r"^\s*\[[^]]+\]\s*$", lines[next_index]):
                end = next_index
                break
        return index, end
    return None


def _ensure_section(lines: list[str], section: str, entries: list[str]) -> None:
    bounds = _section_bounds(lines, section)
    if bounds is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(f"[{section}]")
        lines.extend(entries)
        return

    start, end = bounds
    for entry in entries:
        key = entry.split("=", 1)[0].strip()
        pattern = re.compile(rf"^\s*{re.escape(key)}\s*=")
        for index in range(start + 1, end):
            if pattern.match(lines[index]):
                lines[index] = entry
                break
        else:
            lines.insert(end, entry)
            end += 1


def _ensure_top_level_key(lines: list[str], key: str, value: str) -> None:
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*=")
    for index, line in enumerate(lines):
        if pattern.match(line):
            lines[index] = f'{key} = {json.dumps(value)}'
            return
    insertion = 0
    while insertion < len(lines) and not re.match(r"^\s*\[[^]]+\]\s*$", lines[insertion]):
        insertion += 1
    lines[insertion:insertion] = [f'{key} = {json.dumps(value)}', ""]


def configure_codex(upstream: str = "go") -> tuple[Path, Path]:
    """Create an OpenCode provider/profile and selector catalog idempotently."""
    if upstream not in {"go", "zen", "combined"}:
        raise ValueError("upstream must be 'go', 'zen', or 'combined'")
    if upstream == "combined":
        catalog_filename = "opencode.json"
        catalog = _combined_catalog()
        profiles = [
            ("luna-go", "opencode-go", MODEL_SLUG),
            ("luna-zen", "opencode-zen", MODEL_SLUG),
            ("deepseek-zen", "opencode-zen", "deepseek-v4-flash-free"),
        ]
        providers = [
            ("opencode-go", "OpenCode Go", "http://127.0.0.1:8787/zen/go/v1"),
            ("opencode-zen", "OpenCode Zen", "http://127.0.0.1:8787/zen/v1"),
        ]
    else:
        provider_name = "opencode-zen" if upstream == "zen" else PROVIDER_NAME
        provider_display_name = "OpenCode Zen" if upstream == "zen" else "OpenCode Go"
        catalog_filename = "opencode-zen.json" if upstream == "zen" else CATALOG_FILENAME
        catalog = _zen_catalog() if upstream == "zen" else CATALOG
        profiles = [(f"{MODEL_SLUG}-zen" if upstream == "zen" else MODEL_SLUG, provider_name, MODEL_SLUG)]
        providers = [(provider_name, provider_display_name, "http://127.0.0.1:8787/v1")]
    codex_home = _codex_home()
    codex_home.mkdir(parents=True, exist_ok=True)
    config_path = codex_home / "config.toml"
    catalog_path = codex_home / "model-catalogs" / catalog_filename
    catalog_path.parent.mkdir(parents=True, exist_ok=True)

    lines = config_path.read_text(encoding="utf-8").splitlines() if config_path.exists() else []
    _ensure_top_level_key(lines, "model_catalog_json", str(catalog_path))
    for configured_name, configured_display_name, base_url in providers:
        _ensure_section(
            lines,
            f"model_providers.{configured_name}",
            [
                f'name = "{configured_display_name}"',
                f'base_url = "{base_url}"',
                'experimental_bearer_token = "local-proxy"',
                'wire_api = "responses"',
            ],
        )
    for profile_name, profile_provider, profile_model in profiles:
        profile_section = f'profiles."{profile_name}"'
        legacy_profile_section = f"profiles.{profile_name}"
        legacy_bounds = _section_bounds(lines, legacy_profile_section)
        if legacy_bounds is not None and _section_bounds(lines, profile_section) is None:
            lines[legacy_bounds[0]] = f"[{profile_section}]"
        _ensure_section(
            lines,
            profile_section,
            [
                f'model_provider = "{profile_provider}"',
                f'model = "{profile_model}"',
                f'model_context_window = {1050000 if profile_model.endswith(MODEL_SLUG) else 128000}',
                'approval_policy = "untrusted"',
                'sandbox_mode = "workspace-write"',
                "features = { memories = false }",
            ],
        )
    config_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    catalog_path.write_text(json.dumps(catalog, indent=2) + "\n", encoding="utf-8")
    return config_path, catalog_path
