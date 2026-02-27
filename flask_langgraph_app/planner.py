from __future__ import annotations

import json
from typing import Any

try:
    from openai import OpenAI
except Exception:  # pragma: no cover
    OpenAI = None
from pydantic import BaseModel, Field


class PlannerOutput(BaseModel):
    intent: str
    confidence: float = Field(ge=0.0, le=1.0)
    suggested_mode: str = Field(pattern="^(auto|esql|dsl)$")
    index_hint: str | None = None
    why: str


class Planner:
    def __init__(self, api_key: str, model: str):
        self.client = OpenAI(api_key=api_key) if (api_key and OpenAI is not None) else None
        self.model = model

    def plan(self, message: str, context: dict[str, Any], manifest: list[dict[str, Any]]) -> PlannerOutput:
        if not self.client:
            return PlannerOutput(
                intent="investigate_logs",
                confidence=0.55,
                suggested_mode=context.get("mode", "auto") or "auto",
                index_hint=context.get("selected_index"),
                why="Fallback planner used because OPENAI_API_KEY is not configured.",
            )

        prompt = {
            "message": message,
            "context": context,
            "tools": manifest,
            "instructions": "Return strict JSON with keys: intent, confidence, suggested_mode, index_hint, why",
        }
        resp = self.client.chat.completions.create(
            model=self.model,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": "You are a read-only Elasticsearch planning assistant."},
                {"role": "user", "content": json.dumps(prompt)},
            ],
        )
        content = resp.choices[0].message.content or "{}"
        return PlannerOutput.model_validate_json(content)
