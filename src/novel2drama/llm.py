"""Claude 结构化调用封装。

- 用 output_config.format(json_schema) 让模型直接产出符合 Pydantic 模型的 JSON；
- 稳定的系统提示 + 故事圣经放在前缀并打 cache_control，逐章调用时命中提示缓存；
- 开启 server-side fallback：若请求被安全分类器拒绝，服务端自动换模型重试；
- 流式请求，避免长分镜输出触发 HTTP 超时。
"""
from __future__ import annotations

import copy
import json
import os
from typing import Protocol, TypeVar

from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)

DEFAULT_MODEL = "claude-opus-5-5"


class LLM(Protocol):
    def structured(self, *, system: str, context: str, prompt: str, schema: type[T]) -> T: ...


def strict_schema(model: type[BaseModel]) -> dict:
    """把 Pydantic JSON Schema 转成结构化输出要求的严格形式：
    所有 object 都 additionalProperties=false，且所有属性都 required。"""
    schema = copy.deepcopy(model.model_json_schema())

    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "object" and "properties" in node:
                node["additionalProperties"] = False
                node["required"] = list(node["properties"].keys())
            for key in ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum"):
                node.pop(key, None)  # 数值范围交给 Pydantic 校验
            node.pop("default", None)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(schema)
    return schema


class LLMError(RuntimeError):
    pass


class ClaudeLLM:
    def __init__(self, model: str | None = None, effort: str = "high", max_tokens: int = 64000):
        import anthropic

        self.client = anthropic.Anthropic()
        self.model = model or os.environ.get("NOVEL2DRAMA_MODEL", DEFAULT_MODEL)
        self.effort = effort
        self.max_tokens = max_tokens

    def structured(self, *, system: str, context: str, prompt: str, schema: type[T]) -> T:
        content = []
        if context:
            # 故事圣经 / 前情等相对稳定的上下文：打缓存断点
            content.append({"type": "text", "text": context, "cache_control": {"type": "ephemeral"}})
        content.append({"type": "text", "text": prompt})

        last_error: Exception | None = None
        for _ in range(2):
            with self.client.beta.messages.stream(
                model=self.model,
                max_tokens=self.max_tokens,
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                thinking={"type": "adaptive"},
                output_config={
                    "effort": self.effort,
                    "format": {"type": "json_schema", "schema": strict_schema(schema)},
                },
                messages=[{"role": "user", "content": content}],
            ) as stream:
                msg = stream.get_final_message()

            if msg.stop_reason == "refusal":
                raise LLMError(f"模型拒绝了该请求: {getattr(msg, 'stop_details', None)}")
            if msg.stop_reason == "max_tokens":
                raise LLMError("输出超出 max_tokens，请减少单集镜头数或提高 max_tokens")
            text = "".join(b.text for b in msg.content if b.type == "text")
            try:
                return schema.model_validate(json.loads(text))
            except (json.JSONDecodeError, ValidationError) as e:
                last_error = e
        raise LLMError(f"结构化输出校验失败: {last_error}")
