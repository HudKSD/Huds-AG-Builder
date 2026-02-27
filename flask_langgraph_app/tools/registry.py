from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from pydantic import BaseModel


@dataclass
class ToolSpec:
    name: str
    description: str
    tags: list[str]
    input_schema: type[BaseModel]
    output_schema: type[BaseModel]
    constraints: dict[str, Any]
    handler: Callable[..., Any]


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec:
        return self._tools[name]

    def manifest(self) -> list[dict[str, Any]]:
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "tags": tool.tags,
                "input_schema": tool.input_schema.model_json_schema(),
                "output_schema": tool.output_schema.model_json_schema(),
            }
            for tool in self._tools.values()
        ]
