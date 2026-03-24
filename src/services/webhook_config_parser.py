"""Webhook 配置解析工具。"""
from __future__ import annotations

import json
from typing import Any
from urllib.parse import parse_qsl


class WebhookConfigParseError(ValueError):
    """Webhook 配置解析错误。"""


def parse_webhook_config_value(
    raw_value: str | None,
    field_name: str,
    *,
    expect_dict: bool = False,
) -> Any | None:
    """解析 Webhook 字段，支持 JSON 与 key=value 文本格式。"""
    if raw_value is None:
        return None

    text = _normalize_structured_text(raw_value)
    if not text:
        return None

    parsed = _parse_json_first(text, field_name)
    if parsed is None:
        parsed = _parse_key_value_text(text)
    if parsed is None:
        raise WebhookConfigParseError(
            f"{field_name} 不是合法 JSON，也不是合法键值对格式"
        )
    if expect_dict and not isinstance(parsed, dict):
        raise WebhookConfigParseError(
            f"{field_name} 必须是 JSON 对象或 key=value 键值对格式"
        )
    return parsed


def normalize_webhook_config_text(raw_value: str | None) -> str | None:
    """归一化 Webhook 文本配置，统一换行并裁掉首尾空白。"""
    if raw_value is None:
        return None
    text = _normalize_structured_text(raw_value)
    return text or None


def _normalize_structured_text(raw_value: str) -> str:
    return str(raw_value).replace("\r\n", "\n").replace("\r", "\n").strip()


def _parse_json_first(text: str, field_name: str) -> Any | None:
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        if text.startswith(("{", "[", '"')):
            raise WebhookConfigParseError(
                f"{field_name} 不是合法 JSON: {exc.msg}"
            ) from exc
        return None


def _parse_key_value_text(text: str) -> dict[str, str] | None:
    line_segments = [line.strip() for line in text.split("\n") if line.strip()]
    if len(line_segments) > 1:
        return _parse_segments(line_segments)

    query_segments = _parse_query_segments(text)
    if query_segments is not None:
        return query_segments

    if ";" in text:
        semicolon_segments = [segment.strip() for segment in text.split(";") if segment.strip()]
        parsed_semicolon_segments = _parse_segments(semicolon_segments)
        if parsed_semicolon_segments is not None:
            return parsed_semicolon_segments

    single_pair = _parse_segment(text)
    if single_pair is None:
        return None
    key, value = single_pair
    return {key: value}


def _parse_query_segments(text: str) -> dict[str, str] | None:
    if "&" not in text:
        return None
    parsed = parse_qsl(text, keep_blank_values=True)
    if not parsed or any(not key for key, _ in parsed):
        return None
    return {key.strip(): value for key, value in parsed if key.strip()}


def _parse_segments(segments: list[str]) -> dict[str, str] | None:
    parsed: dict[str, str] = {}
    for segment in segments:
        pair = _parse_segment(segment)
        if pair is None:
            return None
        key, value = pair
        parsed[key] = value
    return parsed or None


def _parse_segment(segment: str) -> tuple[str, str] | None:
    separator_indexes = [
        index
        for index in (segment.find("="), segment.find(":"))
        if index > 0
    ]
    if not separator_indexes:
        return None

    separator_index = min(separator_indexes)
    key = segment[:separator_index].strip()
    value = segment[separator_index + 1 :].strip()
    if not key:
        return None
    return key, value
