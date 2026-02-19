from typing import Any, Dict

class ToolContext:
    def __init__(self, es, settings, schema_cache):
        self.es = es
        self.settings = settings
        self.schema_cache = schema_cache

class Tool:
    name: str
    description: str
    parameters: Dict[str, Any]  # JSON Schema

    async def run(self, args: Dict[str, Any], ctx: ToolContext) -> Any:
        raise NotImplementedError

    def as_openai_tool(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            }
        }

