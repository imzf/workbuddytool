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
            "supports_tool_call": True,
            "supports_images": True,
            "supports_reasoning": True,
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

    def test_names_are_prefixed(self) -> None:
        self.assertEqual(self.provider["models"]["cn:fast-model"]["name"], "WB 快速")
        self.assertEqual(
            self.provider["models"]["global:gpt-6-astra"]["name"],
            "WB 国际 · GPT-6-Astra",
        )

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
    status = 200
    body = b"{}"

    def do_GET(self) -> None:  # noqa: N802
        _Handler.seen_auth = self.headers.get("Authorization", "")
        self.send_response(_Handler.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(_Handler.body)))
        self.end_headers()
        self.wfile.write(_Handler.body)

    def log_message(self, *_args) -> None:
        pass


class FetchTest(unittest.TestCase):
    def setUp(self) -> None:
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def test_reads_models_and_sends_key(self) -> None:
        _Handler.status, _Handler.body = 200, json.dumps(SAMPLE).encode()
        models = opencode.fetch_models(f"http://127.0.0.1:{self.port}/v1", "sk-abc")
        self.assertEqual(len(models), 3)  # 出图模型被过滤
        self.assertEqual(_Handler.seen_auth, "Bearer sk-abc")

    def test_bad_key_is_reported(self) -> None:
        _Handler.status, _Handler.body = 401, b'{"error":"unauthorized"}'
        with self.assertRaises(opencode.OpenCodeError) as ctx:
            opencode.fetch_models(f"http://127.0.0.1:{self.port}/v1", "nope")
        self.assertIn("401", str(ctx.exception))

    def test_missing_data_is_reported(self) -> None:
        _Handler.status, _Handler.body = 200, b'{"models":[]}'
        with self.assertRaises(opencode.OpenCodeError):
            opencode.fetch_models(f"http://127.0.0.1:{self.port}/v1", "sk-abc")


class CliTest(unittest.TestCase):
    def run_cli(self, tmp: Path, argv: list[str]) -> int:
        from wb_launcher import cli

        panel = tmp / "panel"
        panel.mkdir(exist_ok=True)
        paths = mock.Mock(installed=True)
        paths.config = panel / "config.json"
        with (
            mock.patch.object(cli.LauncherConfig, "load", return_value=mock.Mock(app_path=panel)),
            mock.patch.object(cli.payload, "PanelPaths", return_value=paths),
            mock.patch.object(cli.payload, "get_listen", return_value=("127.0.0.1", 7863)),
            mock.patch.object(cli.payload, "get_api_key", return_value="sk-test"),
            mock.patch.object(opencode, "fetch_models", return_value=[
                m for m in SAMPLE["data"] if m.get("context_length")
            ]),
        ):
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                return cli.main(argv)

    def test_dry_run_does_not_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "opencode.jsonc"
            code = self.run_cli(Path(tmp), ["opencode-models", "--dry-run", "--config", str(target)])
            self.assertEqual(code, 0)
            self.assertFalse(target.exists())

    def test_write_creates_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "opencode.jsonc"
            code = self.run_cli(Path(tmp), ["opencode-models", "--config", str(target)])
            self.assertEqual(code, 0)
            data = json.loads(opencode.strip_jsonc(target.read_text(encoding="utf-8")))
            self.assertEqual(data["providers"]["wb"]["models"]["cn:fast-model"]["limit"]["context"], 300000)
            self.assertEqual(os.stat(target).st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
