"""OpenCode provider 配置的生成与就地替换。"""

from __future__ import annotations

import contextlib
import http.server
import io
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from wb_launcher import opencode

SAMPLE = {
    "data": [
        {
            "id": "cn:fast-model",
            "name": "快速",
            "context_length": 300000,
            "max_output_tokens": 48000,
            "credits": "x0.15",
            "supports_tool_call": True,
            "supports_images": True,
            "supports_reasoning": True,
        },
        {
            "id": "cn:hy3",
            "name": "Hy3",
            "context_length": 192000,
            "max_output_tokens": 64000,
            "supports_tool_call": True,
            "supports_images": False,
        },
        {
            "id": "global:gpt-6-astra",
            "name": "GPT-6-Astra",
            "context_length": 1000000,
            "max_output_tokens": 128000,
            "credits": "x6.67",
            "supports_tool_call": True,
            "supports_images": True,
            "supports_reasoning": True,
        },
        {
            "id": "cn:hy4-preview",
            "name": "Hy4 preview",
            "context_length": 960000,
            "max_output_tokens": 64000,
            "supports_tool_call": True,
            "credits": "x0.29",
            "promo_factor": 0,
            "promo_credits": "0.00x",
            "promo_label": "夜间免费",
        },
        {
            "id": "cn:glm-5.2",
            "name": "GLM-5.2",
            "context_length": 1000000,
            "max_output_tokens": 64000,
            "supports_tool_call": True,
            "credits": "x0.79",
            "promo_factor": 0.5,
            "promo_credits": "0.50x",
            "promo_label": "夜间折扣",
        },
        {"id": "cn:hunyuan-image-alpha", "name": "Hunyuan Image Alpha"},
    ]
}

CONFIG = """{
  // 保留这条注释
  "providers": {
    "other": {
      "name": "other",
      "settings": {
        "baseURL": "https://example.com/v1"
      }
    }
  },
  "disabled_providers": [
    "other"
  ]
}
"""


class BuildTest(unittest.TestCase):
    def setUp(self) -> None:
        self.models = [m for m in SAMPLE["data"] if m.get("context_length")]
        self.provider = opencode.build_provider(self.models, "http://127.0.0.1:7863/v1", "sk-test")

    def test_limits_and_capabilities(self) -> None:
        entry = self.provider["models"]["cn:fast-model"]
        self.assertEqual(entry["limit"], {"context": 300000, "output": 48000})
        self.assertTrue(entry["capabilities"]["tools"])
        self.assertEqual(entry["capabilities"]["input"], ["text", "image"])
        self.assertEqual(entry["capabilities"]["reasoning"], {"supported": True})

    def test_text_only_model_has_no_image(self) -> None:
        entry = self.provider["models"]["cn:hy3"]
        self.assertEqual(entry["capabilities"]["input"], ["text"])
        self.assertNotIn("reasoning", entry["capabilities"])

    def test_names_show_credits(self) -> None:
        self.assertEqual(self.provider["models"]["cn:fast-model"]["name"], "WB 快速 · x0.15")
        self.assertEqual(
            self.provider["models"]["global:gpt-6-astra"]["name"],
            "WB 国际 · GPT-6-Astra · x6.67",
        )

    def test_names_show_promo(self) -> None:
        self.assertEqual(self.provider["models"]["cn:hy4-preview"]["name"], "WB Hy4 preview · 夜间免费")
        self.assertEqual(self.provider["models"]["cn:glm-5.2"]["name"], "WB GLM-5.2 · 夜间折扣 x0.50")

    def test_free_now_is_localised(self) -> None:
        self.assertEqual(opencode._promo_label("Free now"), "限时免费")

    def test_settings(self) -> None:
        self.assertEqual(self.provider["settings"]["baseURL"], "http://127.0.0.1:7863/v1")
        self.assertEqual(self.provider["settings"]["apiKey"], "sk-test")
        self.assertEqual(self.provider["package"], "aisdk:@ai-sdk/openai-compatible")


class UpsertTest(unittest.TestCase):
    def make(self) -> dict:
        return opencode.build_provider(
            [m for m in SAMPLE["data"] if m.get("context_length")],
            "http://127.0.0.1:7863/v1",
            "sk-test",
        )

    def test_insert_keeps_other_providers_and_comments(self) -> None:
        text = opencode.upsert_provider(CONFIG, self.make())
        self.assertIn("// 保留这条注释", text)
        self.assertIn('"other"', text)
        data = json.loads(opencode.strip_jsonc(text))
        self.assertEqual(set(data["providers"]), {"other", "wb"})
        self.assertEqual(data["providers"]["wb"]["models"]["cn:fast-model"]["limit"]["context"], 300000)

    def test_replace_is_idempotent(self) -> None:
        once = opencode.upsert_provider(CONFIG, self.make())
        twice = opencode.upsert_provider(once, self.make())
        self.assertEqual(opencode.strip_jsonc(once), opencode.strip_jsonc(twice))

    def test_replacement_drops_stale_models(self) -> None:
        stale = opencode.upsert_provider(CONFIG, {"models": {"old:model": {"modelID": "old:model"}}})
        updated = opencode.upsert_provider(stale, self.make())
        data = json.loads(opencode.strip_jsonc(updated))
        self.assertNotIn("old:model", data["providers"]["wb"]["models"])

    def test_missing_providers_section_is_created(self) -> None:
        out = opencode.upsert_provider('{\n  "theme": "dark"\n}\n', self.make())
        data = json.loads(opencode.strip_jsonc(out))
        self.assertEqual(data["theme"], "dark")
        self.assertIn("wb", data["providers"])

    def test_config_without_providers_gets_one(self) -> None:
        out = opencode.upsert_provider("{}", self.make())
        data = json.loads(opencode.strip_jsonc(out))
        self.assertEqual(set(data["providers"]), {"wb"})

    def test_ensure_not_disabled_removes_self(self) -> None:
        text = '{\n  "disabled_providers": ["wb", "z"]\n}\n'
        out = opencode.ensure_not_disabled(text, "wb")
        data = json.loads(opencode.strip_jsonc(out))
        self.assertEqual(data["disabled_providers"], ["z"])

    def test_ensure_not_disabled_keeps_others(self) -> None:
        text = '{\n  "disabled_providers": ["z"]\n}\n'
        self.assertEqual(opencode.ensure_not_disabled(text, "wb"), text)


class PathTest(unittest.TestCase):
    def test_opencode_config_env_wins(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            custom = Path(tmp) / "custom.jsonc"
            custom.write_text('{"providers": {}}', encoding="utf-8")
            with mock.patch.dict(os.environ, {"OPENCODE_CONFIG": str(custom)}):
                self.assertEqual(opencode.config_path(), custom)

    def test_managed_config_without_providers_is_ignored(self) -> None:
        # OpenChamber 会把 OPENCODE_CONFIG 指向临时生成的 managed.json
        with tempfile.TemporaryDirectory() as tmp:
            managed = Path(tmp) / "opencode.managed.json"
            managed.write_text('{"plugins": []}', encoding="utf-8")
            env = {"OPENCODE_CONFIG": str(managed), "XDG_CONFIG_HOME": tmp}
            with mock.patch.dict(os.environ, env):
                self.assertEqual(opencode.config_path(), Path(tmp) / "opencode" / "opencode.jsonc")

    def test_xdg_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = {"XDG_CONFIG_HOME": tmp}
            env.pop("OPENCODE_CONFIG", None)
            with mock.patch.dict(os.environ, env, clear=False):
                os.environ.pop("OPENCODE_CONFIG", None)
                self.assertEqual(opencode.config_path(), Path(tmp) / "opencode" / "opencode.jsonc")


class _Handler(http.server.BaseHTTPRequestHandler):
    seen_auth = ""
    seen_paths: list[str] = []
    routes: dict[str, tuple[int, bytes]] = {}

    def do_GET(self) -> None:  # noqa: N802
        _Handler.seen_auth = self.headers.get("Authorization", "")
        _Handler.seen_paths.append(self.path)
        status, body = _Handler.routes.get(self.path, (404, b'{"error":"not found"}'))
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args) -> None:
        pass


PANEL_BODY = json.dumps({"models": [m for m in SAMPLE["data"] if m.get("context_length")]}).encode()


class FetchTest(unittest.TestCase):
    def setUp(self) -> None:
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.port = self.server.server_address[1]
        _Handler.seen_paths = []
        _Handler.routes = {}
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.port}/v1"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def test_prefers_panel_endpoint(self) -> None:
        _Handler.routes = {"/panel/api/models": (200, PANEL_BODY)}
        models = opencode.fetch_models(self.base, "sk-abc")
        self.assertEqual(len(models), 5)  # 出图模型被过滤
        self.assertEqual(_Handler.seen_paths, ["/panel/api/models"])
        self.assertEqual(_Handler.seen_auth, "Bearer sk-abc")

    def test_falls_back_to_openai_endpoint(self) -> None:
        _Handler.routes = {"/v1/models": (200, json.dumps(SAMPLE).encode())}
        models = opencode.fetch_models(self.base, "sk-abc")
        self.assertEqual(len(models), 5)
        self.assertIn("/v1/models", _Handler.seen_paths)

    def test_bad_key_is_reported(self) -> None:
        _Handler.routes = {
            "/panel/api/models": (401, b'{"error":"unauthorized"}'),
            "/v1/models": (401, b'{"error":"unauthorized"}'),
        }
        with self.assertRaises(opencode.OpenCodeError) as ctx:
            opencode.fetch_models(self.base, "nope")
        self.assertIn("401", str(ctx.exception))

    def test_missing_data_is_reported(self) -> None:
        _Handler.routes = {"/v1/models": (200, b'{"models":[]}')}
        with self.assertRaises(opencode.OpenCodeError):
            opencode.fetch_models(self.base, "sk-abc")


if __name__ == "__main__":
    unittest.main()
