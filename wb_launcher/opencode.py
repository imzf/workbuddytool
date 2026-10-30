"""把网关的模型清单同步进 OpenCode 的 provider 配置。

OpenCode 用 `~/.config/opencode/opencode.jsonc` 里的 `providers` 描述自定义
提供商；每个模型可以带 `limit.context` / `limit.output`，不写就按内置默认值
算，长上下文模型会被提前截断。这里直接从面板的 `/v1/models` 读真实数值，
只替换 `providers.wb` 这一段，其它提供商与注释原样保留。
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .util import USER_AGENT

PROVIDER_ID = "wb"
PROVIDER_NAME = "WorkBuddy"
PACKAGE = "aisdk:@ai-sdk/openai-compatible"


class OpenCodeError(RuntimeError):
    pass


def config_path() -> Path:
    """OpenCode 的配置文件。

    $OPENCODE_CONFIG 只在它确实是一份带 providers 的用户配置时才采用：
    OpenChamber 之类的宿主会把它指向一个临时生成的 managed.json，
    往那里写没意义，下次启动就被冲掉了。
    """
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "opencode"
    override = os.environ.get("OPENCODE_CONFIG")
    if override:
        candidate = Path(override).expanduser()
        try:
            looks_like_config = "providers" in candidate.read_text(encoding="utf-8", errors="replace")
        except OSError:
            looks_like_config = False
        if looks_like_config:
            return candidate
    for name in ("opencode.jsonc", "opencode.json"):
        candidate = base / name
        if candidate.exists():
            return candidate
    return base / "opencode.jsonc"


# ── 读取模型清单 ──────────────────────────────────────────────────────


def fetch_models(base_url: str, api_key: str, timeout: float = 10) -> list[dict[str, Any]]:
    """GET {base_url}/models，返回可对话的模型（有 context_length 的那些）。"""
    url = base_url.rstrip("/") + "/models"
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {api_key}",
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read(512).decode("utf-8", "replace") if exc.fp else ""
        exc.close()
        raise OpenCodeError(f"网关返回 HTTP {exc.code}：{detail or url}") from exc
    except Exception as exc:  # noqa: BLE001 - 网络层异常类型很多
        raise OpenCodeError(f"连不上网关 {url}：{exc}") from exc

    models = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(models, list):
        raise OpenCodeError("网关返回的模型列表格式不对（缺少 data 数组）")
    return [m for m in models if isinstance(m, dict) and m.get("context_length")]


def _label(model: dict[str, Any]) -> str:
    prefix = "WB 国际 · " if str(model.get("id", "")).startswith("global:") else "WB "
    return prefix + (model.get("name") or str(model.get("id")))


def build_provider(
    models: list[dict[str, Any]],
    base_url: str,
    api_key: str,
    provider_id: str = PROVIDER_ID,
) -> dict[str, Any]:
    """把 /v1/models 的结果转成 OpenCode 的 provider 配置。"""
    entries: dict[str, Any] = {}
    for model in sorted(models, key=lambda m: (str(m["id"]).startswith("global:"), str(m["id"]))):
        model_id = str(model["id"])
        capabilities: dict[str, Any] = {
            "tools": bool(model.get("supports_tool_call")),
            "input": ["text"] + (["image"] if model.get("supports_images") else []),
            "output": ["text"],
        }
        if model.get("supports_reasoning"):
            capabilities["reasoning"] = {"supported": True}
        entries[model_id] = {
            "modelID": model_id,
            "name": _label(model),
            "capabilities": capabilities,
            "limit": {
                "context": int(model["context_length"]),
                "output": int(model.get("max_output_tokens") or 8192),
            },
        }
    return {
        "name": PROVIDER_NAME,
        "package": PACKAGE,
        "settings": {"baseURL": base_url.rstrip("/"), "apiKey": api_key},
        "models": entries,
    }


def strip_jsonc(text: str) -> str:
    """去掉 // 与 /* */ 注释，便于用 json 解析（字符串里的 // 不动）。"""
    out: list[str] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char == '"':
            end = _skip_string(text, index)
            out.append(text[index:end])
            index = end
            continue
        if text.startswith("//", index):
            newline = text.find("\n", index)
            index = len(text) if newline < 0 else newline
            continue
        if text.startswith("/*", index):
            end = text.find("*/", index + 2)
            index = len(text) if end < 0 else end + 2
            continue
        out.append(char)
        index += 1
    return "".join(out)


# ── 在 JSONC 文本里只替换 providers.<id> 这一段 ──────────────────────


def _skip_string(text: str, index: int) -> int:
    """index 指向开引号，返回闭引号之后的位置。"""
    index += 1
    while index < len(text):
        char = text[index]
        if char == "\\":
            index += 2
            continue
        if char == '"':
            return index + 1
        index += 1
    raise OpenCodeError("配置文件里的字符串没有结束")


def _skip_blank(text: str, index: int) -> int:
    """跳过空白与注释。"""
    while index < len(text):
        if text[index].isspace():
            index += 1
        elif text.startswith("//", index):
            newline = text.find("\n", index)
            index = len(text) if newline < 0 else newline + 1
        elif text.startswith("/*", index):
            end = text.find("*/", index + 2)
            if end < 0:
                raise OpenCodeError("配置文件里的块注释没有结束")
            index = end + 2
        else:
            break
    return index


def _value_end(text: str, index: int) -> int:
    """从 index 开始的一个 JSON 值，返回它结束后的位置。"""
    index = _skip_blank(text, index)
    if index >= len(text):
        raise OpenCodeError("配置文件意外结束")
    char = text[index]
    if char == '"':
        return _skip_string(text, index)
    if char in "{[":
        depth = 0
        while index < len(text):
            char = text[index]
            if char == '"':
                index = _skip_string(text, index)
                continue
            if text.startswith("//", index) or text.startswith("/*", index):
                index = _skip_blank(text, index)
                continue
            if char in "{[":
                depth += 1
            elif char in "}]":
                depth -= 1
                if depth == 0:
                    return index + 1
            index += 1
        raise OpenCodeError("配置文件里的对象没有闭合")
    # 数字 / true / false / null
    while index < len(text) and text[index] not in ",}] \t\r\n":
        index += 1
    return index


def _find_member(text: str, obj_start: int, key: str) -> tuple[int, int, int] | None:
    """在 obj_start 指向的 `{` 里找 key。

    返回 (key 起点, 值的起点, 值的终点)；找不到返回 None。
    """
    index = _skip_blank(text, obj_start + 1)
    while index < len(text) and text[index] != "}":
        if text[index] != '"':
            raise OpenCodeError(f"配置文件格式异常（位置 {index}）")
        key_end = _skip_string(text, index)
        name = json.loads(text[index:key_end])
        index = _skip_blank(text, key_end)
        if index >= len(text) or text[index] != ":":
            raise OpenCodeError(f"配置文件格式异常（{name} 后面缺少冒号）")
        value_start = _skip_blank(text, index + 1)
        value_end = _value_end(text, value_start)
        if name == key:
            return index, value_start, value_end
        index = _skip_blank(text, value_end)
        if index < len(text) and text[index] == ",":
            index = _skip_blank(text, index + 1)
    return None


def _indent_of(text: str, index: int) -> str:
    line_start = text.rfind("\n", 0, index) + 1
    prefix = text[line_start:index]
    return prefix if prefix.strip() == "" else ""


def _render(value: Any, base_indent: str) -> str:
    raw = json.dumps(value, ensure_ascii=False, indent=2)
    lines = raw.splitlines()
    return "\n".join([lines[0]] + [base_indent + line for line in lines[1:]])


def _insert_member(
    text: str, obj_start: int, obj_end: int, key: str, value: Any, base_indent: str
) -> str:
    """在 obj_start 指向的对象末尾插入一个成员。"""
    close = obj_end - 1
    if text[close] != "}":
        raise OpenCodeError("对象没有闭合")
    indent = base_indent + "  "
    block = f"{json.dumps(key, ensure_ascii=False)}: {_render(value, indent)}"
    body = text[obj_start + 1:close].rstrip()
    if body.strip():
        new_body = f"{body},\n{indent}{block}\n{base_indent}"
    else:
        new_body = f"\n{indent}{block}\n{base_indent}"
    return text[:obj_start + 1] + new_body + text[close:]


def upsert_provider(text: str, provider: dict[str, Any], provider_id: str = PROVIDER_ID) -> str:
    """把 provider 写进配置文本；已存在就替换，不碰其它内容。"""
    root = _skip_blank(text, 0)
    if root >= len(text) or text[root] != "{":
        raise OpenCodeError("配置文件的最外层不是 JSON 对象")
    providers = _find_member(text, root, "providers")
    if providers is None:
        # 没有 providers 段就补一个
        root_end = _value_end(text, root)
        return _insert_member(
            text, root, root_end, "providers", {provider_id: provider}, _indent_of(text, root)
        )
    key_start, start, end = providers
    if text[start] != "{":
        raise OpenCodeError("providers 不是对象")

    existing = _find_member(text, start, provider_id)
    if existing is not None:
        _, value_start, value_end = existing
        indent = _indent_of(text, key_start) + "  "
        return text[:value_start] + _render(provider, indent) + text[value_end:]

    # 没有就插到 providers 的 } 前面
    return _insert_member(text, start, end, provider_id, provider, _indent_of(text, key_start))


def ensure_not_disabled(text: str, provider_id: str = PROVIDER_ID) -> str:
    """从 disabled_providers 里去掉自己（OpenCode 用它屏蔽提供商）。"""
    root = _skip_blank(text, 0)
    member = _find_member(text, root, "disabled_providers")
    if member is None:
        return text
    key_start, start, end = member
    try:
        values = json.loads(text[start:end])
    except ValueError:
        return text
    if not isinstance(values, list) or provider_id not in values:
        return text
    kept = [v for v in values if v != provider_id]
    indent = _indent_of(text, key_start) + "  "
    return text[:start] + _render(kept, indent) + text[end:]


def write_config(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def sync(
    base_url: str,
    api_key: str,
    provider_id: str = PROVIDER_ID,
    path: Path | None = None,
    dry_run: bool = False,
) -> tuple[Path, int, int, str | None]:
    """拉模型清单并写进 OpenCode 配置。

    返回 (配置路径, 模型数, 1M 模型数, 预览文本)。
    """
    models = fetch_models(base_url, api_key)
    provider = build_provider(models, base_url, api_key, provider_id=provider_id)
    target = path or config_path()
    if target.exists():
        text = target.read_text(encoding="utf-8")
    else:
        text = '{\n  "$schema": "https://opencode.ai/config.json",\n  "providers": {}\n}\n'
    text = ensure_not_disabled(text, provider_id)
    text = upsert_provider(text, provider, provider_id)
    if not dry_run:
        write_config(target, text)
    big = sum(1 for m in provider["models"].values() if m["limit"]["context"] >= 1_000_000)
    preview = _render(provider, "    ")
    return target, len(provider["models"]), big, (preview if dry_run else None)
