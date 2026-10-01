import json

import requests
from flask import Blueprint, Response, g, jsonify, render_template, request, stream_with_context

from .. import db
from ..core import api_errors, login_required, salt_client
from ..saltapi import SaltAPIError

bp = Blueprint("events", __name__)


@bp.route("/events")
@login_required
def index():
    return render_template("events/index.html", page_title="Event bus")


@bp.route("/api/events/stream")
@login_required
def stream():
    """Proxy the salt-api /events SSE stream using the user's token (never exposed to the browser)."""
    token = g.user["salt_token"]
    client = salt_client()

    def generate():
        yield "retry: 3000\n\n"
        while True:
            try:
                for event in client.events(token):
                    yield f"data: {json.dumps(event, default=str)}\n\n"
            except requests.exceptions.ReadTimeout:
                yield ": keepalive\n\n"
                continue
            except SaltAPIError as exc:
                yield f"event: error\ndata: {json.dumps({'error': str(exc)})}\n\n"
                return
            except (requests.RequestException, GeneratorExit):
                return

    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    return Response(stream_with_context(generate()), mimetype="text/event-stream", headers=headers)


@bp.route("/api/events/history")
@login_required
@api_errors
def history():
    tag = request.args.get("tag", "").strip()
    q = request.args.get("q", "").strip()
    page = max(int(request.args.get("page", 1) or 1), 1)
    where, params = ["TRUE"], []
    if tag:
        where.append("tag LIKE %s")
        params.append(tag.replace("*", "%") + ("%" if "*" not in tag else ""))
    if q:
        where.append("data::text ILIKE %s")
        params.append(f"%{q}%")
    rows = db.query(
        f"SELECT id, tag, data, alter_time, master_id, count(*) OVER() AS total FROM salt_events "
        f"WHERE {' AND '.join(where)} ORDER BY id DESC LIMIT 50 OFFSET %s",
        params + [(page - 1) * 50],
    )
    total = rows[0]["total"] if rows else 0
    for r in rows:
        r["alter_time"] = r["alter_time"].isoformat()
        r.pop("total", None)
    return jsonify(rows=rows, total=total, page=page)
