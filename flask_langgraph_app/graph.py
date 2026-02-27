from __future__ import annotations

import json
import time
import uuid
from typing import Any, Callable, Literal

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from planner import Planner
from storage import save_snapshot, save_trace
from tools import ESTools, ToolRegistry, ToolSpec
from tools.es_tools import (
    DeltaIn,
    DeltaOut,
    EvidenceIn,
    EvidenceOut,
    ExecuteIn,
    ExecuteOut,
    FieldCapsIn,
    FieldCapsOut,
    GenerateDSLOut,
    GenerateESQLOut,
    GenerateQueryIn,
    ListIndicesIn,
    ListIndicesOut,
    TermsIn,
    TermsOut,
    ValidateIn,
    ValidateOut,
)
from validators import Constraints, circuit_breaker


class Preferences(BaseModel):
    mode: Literal["auto", "esql", "dsl"] = "auto"
    max_rows: int
    time_range_hours: int
    baseline_first: bool = False
    baseline_window_hours: int | None = None


class ValidationReport(BaseModel):
    pass_value: bool = False
    reasons: list[str] = Field(default_factory=list)
    suggested_fixes: list[dict[str, Any]] = Field(default_factory=list)


class GraphState(BaseModel):
    trace_id: str
    conversation_id: str
    user_message: str
    preferences: Preferences
    intent: str = ""
    planner_confidence: float = 0.0
    chosen_mode: Literal["esql", "dsl", "unknown"] = "unknown"
    candidate_indices: list[str] = Field(default_factory=list)
    selected_index: str | None = None
    schema_snapshot: dict[str, Any] = Field(default_factory=dict)
    generated_query_v1: dict[str, Any] | str | None = None
    generated_query_v2: dict[str, Any] | str | None = None
    validation_report: ValidationReport = Field(default_factory=ValidationReport)
    execution_result_summary: dict[str, Any] = Field(default_factory=dict)
    baseline_result_summary: dict[str, Any] | None = None
    evidence_cards: list[dict[str, Any]] = Field(default_factory=list)
    final_answer: str = ""
    tool_trace: list[dict[str, Any]] = Field(default_factory=list)
    node_path: list[str] = Field(default_factory=list)
    node_timings: dict[str, float] = Field(default_factory=dict)
    blocked: bool = False
    blocked_reason: str | None = None
    should_clarify: bool = False
    dry_run: bool = False


class ChatGraph:
    def __init__(self, planner: Planner, tools: ESTools, constraints: Constraints, summary_model: str, openai_api_key: str):
        self.planner = planner
        self.tools = tools
        self.constraints = constraints
        self.summary_model = summary_model
        self.openai_api_key = openai_api_key
        self.registry = self._build_registry()
        self.graph = self._build_graph().compile()

    def _build_registry(self) -> ToolRegistry:
        reg = ToolRegistry()
        reg.register(ToolSpec("list_indices", "List read-only indices", ["schema"], ListIndicesIn, ListIndicesOut, {"limit": 50}, self.tools.list_indices))
        reg.register(ToolSpec("get_field_caps", "Fetch field capabilities", ["schema"], FieldCapsIn, FieldCapsOut, {}, self.tools.get_field_caps))
        reg.register(ToolSpec("schema_terms", "Lookup bounded terms", ["schema"], TermsIn, TermsOut, {"max_terms": self.constraints.max_terms}, self.tools.schema_terms))
        reg.register(ToolSpec("generate_esql", "Generate ES|QL only", ["generation"], GenerateQueryIn, GenerateESQLOut, {}, self.tools.generate_esql))
        reg.register(ToolSpec("generate_dsl", "Generate DSL only", ["generation"], GenerateQueryIn, GenerateDSLOut, {}, self.tools.generate_dsl))
        reg.register(ToolSpec("validate_query", "Validate safety constraints", ["validation"], ValidateIn, ValidateOut, {}, self.tools.validate))
        reg.register(ToolSpec("execute_esql", "Execute ES|QL safely", ["execution"], ExecuteIn, ExecuteOut, {}, self.tools.execute_esql))
        reg.register(ToolSpec("execute_dsl", "Execute DSL safely", ["execution"], ExecuteIn, ExecuteOut, {}, self.tools.execute_dsl))
        reg.register(ToolSpec("build_evidence_cards", "Build evidence cards", ["evidence"], EvidenceIn, EvidenceOut, {}, self.tools.build_evidence_cards))
        reg.register(ToolSpec("compute_baseline_delta", "Compute deltas", ["baseline"], DeltaIn, DeltaOut, {}, self.tools.compute_baseline_delta))
        return reg

    def _emit(self, emitter: Callable[[dict[str, Any]], None] | None, event: dict[str, Any]) -> None:
        if emitter:
            emitter(event)

    def _record_tool(self, state: GraphState, tool: str, args: dict[str, Any], start: float, status: str = "done") -> None:
        state.tool_trace.append(
            {
                "tool": tool,
                "args_redacted": args,
                "status": status,
                "duration_ms": round((time.time() - start) * 1000, 2),
            }
        )

    def _wrap_node(self, name: str, fn: Callable[[GraphState, Callable[[dict[str, Any]], None] | None], GraphState]):
        def _inner(state_dict: dict[str, Any]) -> dict[str, Any]:
            state = GraphState.model_validate(state_dict)
            emitter = state_dict.get("_emitter")
            t0 = time.time()
            state.node_path.append(name)
            self._emit(emitter, {"type": "node_start", "node": name, "ts": time.time()})
            state = fn(state, emitter)
            elapsed = round((time.time() - t0) * 1000, 2)
            state.node_timings[name] = elapsed
            save_snapshot(state.trace_id, name, state.model_dump())
            self._emit(emitter, {"type": "node_end", "node": name, "duration_ms": elapsed})
            return state.model_dump()

        return _inner

    def _build_graph(self) -> StateGraph:
        g = StateGraph(dict)
        g.add_node("planner", self._wrap_node("planner", self.planner_node))
        g.add_node("index_select", self._wrap_node("index_select", self.index_select_node))
        g.add_node("schema_fetch", self._wrap_node("schema_fetch", self.schema_fetch_node))
        g.add_node("mode_router", self._wrap_node("mode_router", self.mode_router_node))
        g.add_node("query_generate", self._wrap_node("query_generate", self.query_generate_node))
        g.add_node("query_validate", self._wrap_node("query_validate", self.query_validate_node))
        g.add_node("execute", self._wrap_node("execute", self.execute_node))
        g.add_node("baseline", self._wrap_node("baseline", self.baseline_node))
        g.add_node("evidence", self._wrap_node("evidence", self.evidence_node))
        g.add_node("summarizer", self._wrap_node("summarizer", self.summarizer_node))
        g.add_node("blocked", self._wrap_node("blocked", self.blocked_node))
        g.add_node("clarify", self._wrap_node("clarify", self.clarify_node))

        g.add_edge(START, "planner")
        g.add_edge("planner", "index_select")
        g.add_edge("index_select", "schema_fetch")
        g.add_edge("schema_fetch", "mode_router")
        g.add_edge("mode_router", "query_generate")
        g.add_edge("query_generate", "query_validate")
        g.add_conditional_edges(
            "query_validate",
            lambda s: "blocked" if s.get("blocked") else ("clarify" if s.get("should_clarify") else ("evidence" if s.get("dry_run") else "execute")),
            {"blocked": "blocked", "clarify": "clarify", "execute": "execute", "evidence": "evidence"},
        )
        g.add_conditional_edges(
            "execute",
            lambda s: "baseline" if s.get("preferences", {}).get("baseline_first") else "evidence",
            {"baseline": "baseline", "evidence": "evidence"},
        )
        g.add_edge("baseline", "evidence")
        g.add_edge("evidence", "summarizer")
        g.add_edge("blocked", END)
        g.add_edge("clarify", END)
        g.add_edge("summarizer", END)
        return g

    def planner_node(self, state: GraphState, emitter=None) -> GraphState:
        blocked, reasons, fixes = circuit_breaker(state.preferences.model_dump(), self.constraints)
        if blocked:
            state.blocked = True
            state.blocked_reason = "; ".join(reasons)
            state.validation_report = ValidationReport(pass_value=False, reasons=reasons, suggested_fixes=fixes)
            self._emit(emitter, {"type": "blocked", "reason": state.blocked_reason, "suggested_fixes": fixes})
            return state

        start = time.time()
        plan = self.planner.plan(state.user_message, state.preferences.model_dump(), self.registry.manifest())
        self._record_tool(state, "planner", {"message": state.user_message}, start)
        self._emit(emitter, {"type": "tool_call", "tool": "planner", "args": {"message": state.user_message}})
        state.intent = plan.intent
        state.planner_confidence = plan.confidence
        if plan.index_hint and not state.selected_index:
            state.selected_index = plan.index_hint
        return state

    def index_select_node(self, state: GraphState, emitter=None) -> GraphState:
        start = time.time()
        out = self.tools.list_indices(prefix=state.selected_index or None)
        self._record_tool(state, "list_indices", {"prefix": state.selected_index}, start)
        self._emit(emitter, {"type": "tool_call", "tool": "list_indices", "args": {"prefix": state.selected_index}})
        state.candidate_indices = out["indices"]
        if not state.selected_index:
            state.selected_index = state.candidate_indices[0] if state.candidate_indices else None
        if not state.selected_index:
            state.should_clarify = True
        return state

    def schema_fetch_node(self, state: GraphState, emitter=None) -> GraphState:
        if not state.selected_index:
            state.should_clarify = True
            return state
        start = time.time()
        schema = self.tools.get_field_caps(state.selected_index)
        self._record_tool(state, "get_field_caps", {"index": state.selected_index}, start)
        self._emit(emitter, {"type": "tool_call", "tool": "get_field_caps", "args": {"index": state.selected_index}})
        schema["time_range_hours"] = state.preferences.time_range_hours
        state.schema_snapshot = schema
        return state

    def mode_router_node(self, state: GraphState, emitter=None) -> GraphState:
        pref = state.preferences.mode
        if pref in ("esql", "dsl"):
            state.chosen_mode = pref
        else:
            state.chosen_mode = "dsl" if "aggregate" in state.intent.lower() else "esql"
        return state

    def query_generate_node(self, state: GraphState, emitter=None) -> GraphState:
        if not state.selected_index:
            state.should_clarify = True
            return state
        args = dict(intent=state.intent or state.user_message, index=state.selected_index, time_range_hours=state.preferences.time_range_hours, limit=state.preferences.max_rows)
        start = time.time()
        if state.chosen_mode == "esql":
            q = self.tools.generate_esql(**args)["esql"]
            tool = "generate_esql"
        else:
            q = self.tools.generate_dsl(**args, size=args["limit"])["dsl"]
            tool = "generate_dsl"
        self._record_tool(state, tool, args, start)
        self._emit(emitter, {"type": "tool_call", "tool": tool, "args": args})
        state.generated_query_v1 = q
        return state

    def query_validate_node(self, state: GraphState, emitter=None) -> GraphState:
        start = time.time()
        report = self.tools.validate(state.chosen_mode, state.generated_query_v1, state.schema_snapshot)
        self._record_tool(state, "validate_query", {"mode": state.chosen_mode}, start)
        self._emit(emitter, {"type": "tool_call", "tool": "validate_query", "args": {"mode": state.chosen_mode}})
        state.validation_report = ValidationReport(
            pass_value=report["pass_value"], reasons=report["reasons"], suggested_fixes=report["suggested_fixes"]
        )
        state.generated_query_v2 = report.get("rewritten_query") or state.generated_query_v1
        if not report["pass_value"]:
            state.blocked = True
            state.blocked_reason = "; ".join(report["reasons"])
            self._emit(emitter, {"type": "blocked", "reason": state.blocked_reason, "suggested_fixes": report["suggested_fixes"]})
        return state

    def execute_node(self, state: GraphState, emitter=None) -> GraphState:
        if state.blocked or not state.validation_report.pass_value:
            state.blocked = True
            state.blocked_reason = state.blocked_reason or "Validation failed."
            return state
        start = time.time()
        if state.chosen_mode == "esql":
            res = self.tools.execute_esql(state.generated_query_v2, state.selected_index)
            tool = "execute_esql"
        else:
            res = self.tools.execute_dsl(state.generated_query_v2, state.selected_index)
            tool = "execute_dsl"
        self._record_tool(state, tool, {"index": state.selected_index}, start)
        self._emit(emitter, {"type": "tool_call", "tool": tool, "args": {"index": state.selected_index}})
        state.execution_result_summary = res
        return state

    def baseline_node(self, state: GraphState, emitter=None) -> GraphState:
        bw = min(state.preferences.baseline_window_hours or self.constraints.max_time_range_hours, self.constraints.max_time_range_hours)
        args = dict(intent=state.intent, index=state.selected_index, time_range_hours=bw, size=state.preferences.max_rows)
        start = time.time()
        base_q = self.tools.generate_dsl(**args)["dsl"]
        base_res = self.tools.execute_dsl(base_q, state.selected_index)
        delta = self.tools.compute_baseline_delta(state.execution_result_summary, base_res)["delta"]
        self._record_tool(state, "baseline_compute", {"hours": bw}, start)
        self._emit(emitter, {"type": "tool_call", "tool": "compute_baseline_delta", "args": {"hours": bw}})
        state.baseline_result_summary = {**base_res, "delta": delta}
        return state

    def evidence_node(self, state: GraphState, emitter=None) -> GraphState:
        start = time.time()
        ev = self.tools.build_evidence_cards(state.intent, state.generated_query_v2, state.execution_result_summary, state.baseline_result_summary)
        self._record_tool(state, "build_evidence_cards", {"intent": state.intent}, start)
        self._emit(emitter, {"type": "tool_call", "tool": "build_evidence_cards", "args": {"intent": state.intent}})
        state.evidence_cards = ev["cards"]
        return state

    def summarizer_node(self, state: GraphState, emitter=None) -> GraphState:
        summary = f"Intent: {state.intent}. Rows: {state.execution_result_summary.get('row_count', 0)}."
        if state.baseline_result_summary:
            delta = state.baseline_result_summary.get("delta", {})
            summary += f" Baseline delta abs={delta.get('abs')}, pct={delta.get('pct')}%."
        state.final_answer = summary
        self._emit(emitter, {"type": "final", "answer": state.final_answer, "trace_id": state.trace_id, "debug": self._debug(state)})
        return state

    def blocked_node(self, state: GraphState, emitter=None) -> GraphState:
        state.final_answer = f"Query blocked: {state.blocked_reason}"
        return state

    def clarify_node(self, state: GraphState, emitter=None) -> GraphState:
        state.final_answer = "Please specify an index; none could be determined safely."
        return state

    def _debug(self, state: GraphState) -> dict[str, Any]:
        return {
            "intent": state.intent,
            "chosen_mode": state.chosen_mode,
            "selected_index": state.selected_index,
            "query_v1": state.generated_query_v1,
            "query_v2": state.generated_query_v2,
            "validation": state.validation_report.model_dump(),
        }

    def run(self, *, user_message: str, conversation_id: str, preferences: dict[str, Any], stream: bool = False, dry_run: bool = False, emitter: Callable[[dict[str, Any]], None] | None = None):
        trace_id = str(uuid.uuid4())
        events: list[dict[str, Any]] = []

        def _default_emitter(e: dict[str, Any]):
            events.append(e)

        actual_emitter = emitter or _default_emitter

        state = GraphState(
            trace_id=trace_id,
            conversation_id=conversation_id,
            user_message=user_message,
            selected_index=preferences.get("selected_index"),
            preferences=Preferences(
                mode=preferences.get("mode", "auto"),
                max_rows=int(preferences.get("max_rows", self.constraints.default_max_rows)),
                time_range_hours=int(preferences.get("time_range_hours", self.constraints.default_time_range_hours)),
                baseline_first=bool(preferences.get("baseline_first", False)),
                baseline_window_hours=preferences.get("baseline_window_hours"),
            ),
            dry_run=dry_run,
        )
        initial = state.model_dump()
        initial["_emitter"] = actual_emitter
        out = self.graph.invoke(initial)
        result = GraphState.model_validate(out)

        trace = {
            "trace_id": trace_id,
            "conversation_id": conversation_id,
            "node_path": result.node_path,
            "node_timings": result.node_timings,
            "tool_trace": result.tool_trace,
            "plan": {"intent": result.intent, "confidence": result.planner_confidence},
            "queries": {"v1": result.generated_query_v1, "v2": result.generated_query_v2},
            "validation_report": result.validation_report.model_dump(),
            "evidence_cards": result.evidence_cards,
            "final_answer": result.final_answer,
            "blocked": result.blocked,
            "blocked_reason": result.blocked_reason,
        }
        save_trace(trace)

        payload = {
            "trace_id": trace_id,
            "answer": result.final_answer,
            "blocked": result.blocked,
            "blocked_reason": result.blocked_reason,
            "suggested_fixes": result.validation_report.suggested_fixes,
            "debug": self._debug(result),
            "evidence_cards": result.evidence_cards,
        }
        if stream:
            return events, payload
        return payload
