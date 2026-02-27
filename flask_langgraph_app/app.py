from __future__ import annotations

import json
import os
import queue
import threading
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from elasticsearch import Elasticsearch
from flask import Flask, Response, jsonify, render_template, request

from graph import ChatGraph
from planner import Planner
from storage import (
    ensure_conversation,
    get_context_vars,
    get_snapshot,
    get_trace,
    init_db,
    save_message,
    upsert_context_vars,
)
from tools.es_tools import ESTools
from validators import Constraints

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def make_constraints() -> Constraints:
    return Constraints(
        default_time_range_hours=int(os.getenv("DEFAULT_TIME_RANGE_HOURS", "24")),
        max_time_range_hours=int(os.getenv("MAX_TIME_RANGE_HOURS", "168")),
        default_max_rows=int(os.getenv("DEFAULT_MAX_ROWS", "200")),
        max_rows=int(os.getenv("MAX_ROWS", "200")),
        max_terms=int(os.getenv("MAX_TERMS", "20")),
        request_timeout_seconds=int(os.getenv("REQUEST_TIMEOUT_SECONDS", "20")),
        time_field_name=os.getenv("TIME_FIELD_NAME", "@timestamp"),
    )


def make_es() -> Elasticsearch:
    url = os.getenv("ELASTICSEARCH_URL", "http://localhost:9200")
    api_key = os.getenv("ELASTICSEARCH_API_KEY")
    if api_key:
        return Elasticsearch(url, api_key=api_key)
    user = os.getenv("ELASTICSEARCH_USERNAME")
    pw = os.getenv("ELASTICSEARCH_PASSWORD")
    return Elasticsearch(url, basic_auth=(user, pw) if user and pw else None)


app = Flask(__name__)
constraints = make_constraints()
es = make_es()
planner = Planner(os.getenv("OPENAI_API_KEY", ""), os.getenv("OPENAI_MODEL_PLANNER", "gpt-4o-mini"))
chat_graph = ChatGraph(planner, ESTools(es, constraints), constraints, os.getenv("OPENAI_MODEL_SUMMARY", "gpt-4o-mini"), os.getenv("OPENAI_API_KEY", ""))
init_db()


def merged_preferences(conversation_id: str, body_preferences: dict[str, Any] | None) -> dict[str, Any]:
    stored = get_context_vars(conversation_id)
    prefs = {
        "mode": stored.get("mode") or "auto",
        "max_rows": stored.get("max_rows") or constraints.default_max_rows,
        "time_range_hours": stored.get("time_range_hours") or constraints.default_time_range_hours,
        "baseline_window_hours": stored.get("baseline_window_hours") or constraints.max_time_range_hours,
        "baseline_first": bool(stored.get("baseline_first", 0)),
        "selected_index": stored.get("selected_index"),
    }
    if body_preferences:
        prefs.update({k: v for k, v in body_preferences.items() if v is not None})
    upsert_context_vars(conversation_id, prefs)
    return prefs


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


@app.post("/chat")
def chat():
    body = request.get_json(force=True)
    cid = ensure_conversation(body.get("conversation_id"))
    prefs = merged_preferences(cid, body.get("preferences"))
    save_message(cid, "user", body["message"])
    result = chat_graph.run(user_message=body["message"], conversation_id=cid, preferences=prefs)
    save_message(cid, "assistant", result["answer"])
    result["conversation_id"] = cid
    return jsonify(result)


@app.post("/chat/stream")
def chat_stream():
    body = request.get_json(force=True)
    cid = ensure_conversation(body.get("conversation_id"))
    prefs = merged_preferences(cid, body.get("preferences"))
    save_message(cid, "user", body["message"])

    q: queue.Queue[Any] = queue.Queue()

    def emit(event: dict[str, Any]):
        q.put(event)

    def worker():
        _, final = chat_graph.run(user_message=body["message"], conversation_id=cid, preferences=prefs, stream=True, emitter=emit)
        save_message(cid, "assistant", final["answer"])
        final["conversation_id"] = cid
        q.put({"type": "final", **final})
        q.put(None)

    threading.Thread(target=worker, daemon=True).start()

    def gen():
        while True:
            item = q.get()
            if item is None:
                break
            yield json.dumps(item) + "\n"

    return Response(gen(), mimetype="application/x-ndjson")


@app.post("/dry_run")
def dry_run():
    body = request.get_json(force=True)
    cid = ensure_conversation(body.get("conversation_id"))
    prefs = merged_preferences(cid, body.get("preferences"))
    result = chat_graph.run(user_message=body["message"], conversation_id=cid, preferences=prefs, dry_run=True)
    return jsonify(result)


@app.post("/dry_run/stream")
def dry_run_stream():
    body = request.get_json(force=True)
    cid = ensure_conversation(body.get("conversation_id"))
    prefs = merged_preferences(cid, body.get("preferences"))
    events, final = chat_graph.run(user_message=body["message"], conversation_id=cid, preferences=prefs, stream=True, dry_run=True)
    return Response("".join(json.dumps(e) + "\n" for e in [*events, {"type": "final", **final}]), mimetype="application/x-ndjson")


@app.post("/replay/stream")
def replay_stream():
    body = request.get_json(force=True)
    trace_id = body["trace_id"]
    from_node = body["from_node"]
    snap = get_snapshot(trace_id, from_node)
    if not snap:
        return jsonify({"error": "snapshot_not_found"}), 404

    q: queue.Queue[Any] = queue.Queue()

    def emit(event: dict[str, Any]):
        q.put(event)

    def worker():
        prefs = snap.get("preferences", {})
        _, final = chat_graph.run(
            user_message=snap.get("user_message", "replay"),
            conversation_id=snap.get("conversation_id"),
            preferences=prefs,
            stream=True,
            emitter=emit,
            dry_run=False,
        )
        q.put({"type": "final", **final})
        q.put(None)

    threading.Thread(target=worker, daemon=True).start()

    def gen():
        while True:
            item = q.get()
            if item is None:
                break
            yield json.dumps(item) + "\n"

    return Response(gen(), mimetype="application/x-ndjson")


@app.get("/trace/<trace_id>")
def trace_view(trace_id: str):
    tr = get_trace(trace_id)
    if not tr:
        return "Not found", 404
    def parse(v):
        return json.loads(v) if v else {}
    return render_template("trace.html", trace=tr, node_path=parse(tr["node_path_json"]), node_timings=parse(tr["node_timings_json"]), tool_trace=parse(tr["tool_trace_json"]), queries=parse(tr["queries_json"]), validation=parse(tr["validation_json"]), evidence=parse(tr["evidence_json"]))


@app.get("/export/<trace_id>.json")
def export_json(trace_id: str):
    tr = get_trace(trace_id)
    if not tr:
        return jsonify({"error": "not_found"}), 404
    return Response(tr["evidence_json"] or "[]", mimetype="application/json")


@app.get("/export/<trace_id>.md")
def export_md(trace_id: str):
    tr = get_trace(trace_id)
    if not tr:
        return "Not found", 404
    cards = json.loads(tr["evidence_json"] or "[]")
    lines = ["# Evidence Cards"]
    for i, card in enumerate(cards, 1):
        lines.extend([f"## Card {i}", f"- Claim: {card.get('claim')}", f"- Confidence: {card.get('confidence')}", f"- Metrics: {card.get('metrics')}", ""])
    return Response("\n".join(lines), mimetype="text/markdown")


@app.get("/schema/fields")
def schema_fields():
    index = request.args.get("index")
    if not index:
        return jsonify({"error": "index_required"}), 400
    return jsonify(ESTools(es, constraints).get_field_caps(index))


@app.get("/schema/terms")
def schema_terms():
    index = request.args.get("index")
    field = request.args.get("field")
    if not index or not field:
        return jsonify({"error": "index_and_field_required"}), 400
    return jsonify(ESTools(es, constraints).schema_terms(index, field, constraints.default_time_range_hours, constraints.max_terms))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("APP_PORT", "8000")), debug=True)
