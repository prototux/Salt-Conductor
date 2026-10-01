from flask import Blueprint, g, jsonify, render_template, request

from .. import db
from ..core import api_errors, audit, cached, json_body, login_required, salt
from ..salt_utils import TARGET_TYPES, keys, minion_status, parse_args

bp = Blueprint("run", __name__)

CLIENTS = [
    ("local", "Execution (sync)"),
    ("local_async", "Execution (async)"),
    ("local_batch", "Execution (batch)"),
    ("runner", "Runner"),
    ("runner_async", "Runner (async)"),
    ("wheel", "Wheel"),
]


@bp.route("/run")
@login_required
def index():
    saved = db.query(
        "SELECT * FROM conductor_saved_commands WHERE owner = %s OR shared ORDER BY name", (g.user["username"],)
    )
    return render_template("run/index.html", page_title="Run command", target_types=TARGET_TYPES,
                           clients=CLIENTS, saved=saved, minions=keys().get("minions", []))


def _pick_minion():
    up = minion_status().get("up", [])
    if up:
        return up[0]
    accepted = keys().get("minions", [])
    return accepted[0] if accepted else None


@bp.route("/api/functions")
@login_required
@api_errors
def api_functions():
    client = request.args.get("client", "local")

    def load_exec():
        mid = _pick_minion()
        if not mid:
            return []
        ret = salt().local(mid, "sys.list_functions", tgt_type="list", timeout=15)
        return sorted(ret.get(mid) or [])

    def load_runner():
        ret = salt().runner("doc.runner")
        return sorted((ret or {}).keys()) if isinstance(ret, dict) else []

    def load_wheel():
        ret = salt().runner("doc.wheel")
        return sorted((ret or {}).keys()) if isinstance(ret, dict) else []

    if client.startswith("runner"):
        return jsonify(cached("funcs:runner", 600, load_runner))
    if client.startswith("wheel"):
        return jsonify(cached("funcs:wheel", 600, load_wheel))
    return jsonify(cached("funcs:exec", 600, load_exec))


@bp.route("/api/doc")
@login_required
@api_errors
def api_doc():
    fun = request.args.get("fun", "").strip()
    client = request.args.get("client", "local")
    if not fun:
        return jsonify({})
    if client.startswith("runner"):
        docs = cached("doc:runner", 600, lambda: salt().runner("doc.runner") or {})
        return jsonify({k: v for k, v in docs.items() if k == fun or k.startswith(fun + ".")})
    if client.startswith("wheel"):
        docs = cached("doc:wheel", 600, lambda: salt().runner("doc.wheel") or {})
        return jsonify({k: v for k, v in docs.items() if k == fun or k.startswith(fun + ".")})
    mid = _pick_minion()
    if not mid:
        return jsonify({})
    ret = salt().local(mid, "sys.doc", arg=[fun], tgt_type="list", timeout=15)
    return jsonify(ret.get(mid) or {})


@bp.route("/api/run", methods=["POST"])
@login_required
@api_errors
def api_run():
    body = json_body()
    client = body.get("client", "local")
    fun = (body.get("fun") or "").strip()
    if not fun:
        raise ValueError("Function is required")
    tgt = (body.get("tgt") or "").strip()
    tgt_type = body.get("tgt_type", "glob")
    arg, kwarg = parse_args(body.get("args", ""))
    if body.get("test"):
        kwarg["test"] = True
    timeout = int(body.get("timeout") or 0) or None
    details = {"client": client, "fun": fun, "tgt": tgt, "tgt_type": tgt_type, "arg": arg, "kwarg": kwarg}
    s = salt()
    try:
        if client == "local":
            if not tgt:
                raise ValueError("Target is required")
            result = s.local(tgt, fun, arg=arg, kwarg=kwarg, tgt_type=tgt_type, timeout=timeout, full_return=True)
            out = {"result": result, "full_return": True}
        elif client == "local_async":
            ret = s.local_async(tgt, fun, arg=arg, kwarg=kwarg, tgt_type=tgt_type)
            out = {"jid": ret.get("jid"), "minions": ret.get("minions", [])}
            details["jid"] = out["jid"]
        elif client == "local_batch":
            batch = body.get("batch") or "25%"
            out = {"result": s.local_batch(tgt, fun, batch, arg=arg, kwarg=kwarg, tgt_type=tgt_type, timeout=timeout)}
            details["batch"] = batch
        elif client == "runner":
            out = {"result": s.runner(fun, arg=arg, _timeout=max(timeout or 0, 300), **kwarg), "single": True}
        elif client == "runner_async":
            ret = s.runner_async(fun, arg=arg, **kwarg)
            out = {"jid": ret.get("jid"), "tag": ret.get("tag")}
            details["jid"] = out["jid"]
        elif client == "wheel":
            out = {"result": s.wheel(fun, arg=arg, **kwarg), "single": True}
        else:
            raise ValueError("Unsupported client")
    except Exception as exc:
        audit("run", tgt or fun, dict(details, error=str(exc)), success=False)
        raise
    audit("run", tgt or fun, details)
    return jsonify(out)


@bp.route("/api/preview-target", methods=["POST"])
@login_required
@api_errors
def api_preview():
    body = json_body()
    ret = salt().local(body.get("tgt", "*"), "test.true", tgt_type=body.get("tgt_type", "glob"), timeout=5)
    return jsonify(sorted(ret.keys()))


# -- saved commands -----------------------------------------------------------
@bp.route("/api/saved", methods=["POST"])
@login_required
@api_errors
def api_save():
    b = json_body()
    if not b.get("name") or not b.get("fun"):
        raise ValueError("Name and function are required")
    arg, kwarg = parse_args(b.get("args", ""))
    row = db.query_one(
        """INSERT INTO conductor_saved_commands (name, description, client, tgt, tgt_type, fun, arg, kwarg, owner, shared)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
        (b["name"], b.get("description", ""), b.get("client", "local"), b.get("tgt", "*"), b.get("tgt_type", "glob"),
         b["fun"], db.Jsonb(arg), db.Jsonb(dict(kwarg, __raw__=b.get("args", ""))), g.user["username"],
         bool(b.get("shared", True))),
    )
    audit("saved_command.create", b["name"])
    return jsonify(id=row["id"])


@bp.route("/api/saved/<int:cid>", methods=["DELETE"])
@login_required
@api_errors
def api_saved_delete(cid):
    row = db.query_one("DELETE FROM conductor_saved_commands WHERE id = %s AND (owner = %s OR %s) RETURNING name",
                       (cid, g.user["username"], g.user["is_admin"]))
    if not row:
        raise ValueError("Not found or not yours")
    audit("saved_command.delete", row["name"])
    return jsonify(ok=True)


# -- remote shell -------------------------------------------------------------
@bp.route("/shell")
@login_required
def shell():
    return render_template("run/shell.html", page_title="Remote shell", target_types=TARGET_TYPES,
                           minions=keys().get("minions", []))


@bp.route("/api/shell", methods=["POST"])
@login_required
@api_errors
def api_shell():
    b = json_body()
    cmd = (b.get("cmd") or "").strip()
    if not cmd:
        raise ValueError("Empty command")
    kwarg = {"python_shell": True, "cwd": b.get("cwd") or "/root", "timeout": int(b.get("timeout") or 60)}
    if b.get("runas"):
        kwarg["runas"] = b["runas"]
    ret = salt().local(b.get("tgt"), "cmd.run_all", arg=[cmd], kwarg=kwarg, tgt_type=b.get("tgt_type", "glob"),
                       timeout=kwarg["timeout"] + 5)
    audit("shell", b.get("tgt"), {"cmd": cmd, "tgt_type": b.get("tgt_type", "glob")})
    return jsonify(ret)


# -- documentation ------------------------------------------------------------
@bp.route("/docs")
@login_required
def docs():
    return render_template("run/docs.html", page_title="Documentation")


@bp.route("/api/docs/modules")
@login_required
@api_errors
def api_modules():
    kind = request.args.get("kind", "exec")

    def load(k):
        if k == "runner":
            return sorted((salt().runner("doc.runner") or {}).keys())
        if k == "wheel":
            return sorted((salt().runner("doc.wheel") or {}).keys())
        if k == "state":
            mid = _pick_minion()
            ret = salt().local(mid, "sys.list_state_functions", tgt_type="list", timeout=15)
            return sorted(ret.get(mid) or [])
        mid = _pick_minion()
        ret = salt().local(mid, "sys.list_functions", tgt_type="list", timeout=15)
        return sorted(ret.get(mid) or [])

    return jsonify(cached(f"docs:list:{kind}", 600, lambda: load(kind)))


@bp.route("/api/docs/doc")
@login_required
@api_errors
def api_docs_doc():
    kind = request.args.get("kind", "exec")
    name = request.args.get("name", "")
    if kind in ("runner", "wheel"):
        docs = cached(f"doc:{kind}", 600, lambda: salt().runner(f"doc.{kind}") or {})
        return jsonify({k: v for k, v in docs.items() if k == name or k.startswith(name + ".")})
    mid = _pick_minion()
    fun = "sys.state_doc" if kind == "state" else "sys.doc"
    ret = salt().local(mid, fun, arg=[name], tgt_type="list", timeout=15)
    return jsonify(ret.get(mid) or {})
