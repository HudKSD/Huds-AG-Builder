from typing import Any, Dict, List
from jsonschema import validate as js_validate, ValidationError

from .base import Tool, ToolContext

class ToolRegistry:
    def __init__(self, ctx: ToolContext):
        self.ctx = ctx
        self._tools: Dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def schemas(self) -> List[Dict[str, Any]]:
        return [t.as_openai_tool() for t in self._tools.values()]

    async def run(self, name: str, args: Dict[str, Any]) -> Any:
        if name not in self._tools:
            return {"error": f"Unknown tool '{name}'"}
        tool = self._tools[name]

        try:
            js_validate(instance=args, schema=tool.parameters)
        except ValidationError as e:
            return {"error": f"Invalid args for {name}: {e.message}"}

        try:
            return await tool.run(args, self.ctx)
        except Exception as e:
            return {"error": f"{name} failed: {type(e).__name__}: {str(e)}"}

