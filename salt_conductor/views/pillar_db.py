"""Database pillar (served by the conductor_db ext_pillar) and the per-minion pillar viewer."""

import yaml
from flask import Blueprint, Response, current_app, g, jsonify, render_template, request

from .. import db
from ..core import admin_required, api_errors, audit, bao, json_body, login_required, repo, salt
from ..salt_utils import TARGET_TYPES, keys

bp = Blueprint("pillar_db", __name__)

PILLAR_TGT_TYPES = [t for t in TARGET_TYPES if t[0] != "ipcidr"] + [("ipcidr", "IP / CIDR")]
FIELDS = ("target", "tgt_type", "key", "value", "priority", "enabled", "sensitive", "description")


def _row_out(row, reveal=False):
    row = dict(row)
    if row.get("sensitive") and not reveal:
        row["value"] = "**********"
    for k in ("created_at", "updated_at", "changed_at"):
        if row.get(k):
            row[k] = row[k].isoformat()
    return row


def _parse_value(raw):
    if isinstance(raw, (dict, list, int, float, bool)) or raw is None:
        return raw
    try:
        return yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise ValueError(f"Value is not valid YAML/JSON: {exc}") from exc


def _validate(b, parse=True):
    key = (b.get("key") or "").strip().strip(":")
    if not key or any(not part.strip() for part in key.split(":")):
        raise ValueError("Pillar key is required (use ':' to nest, e.g. app:db:host)")
    tgt_type = b.get("tgt_type", "glob")
    if tgt_type not in dict(PILLAR_TGT_TYPES):
        raise ValueError("Unsupported target type")
    return {
        "target": (b.get("target") or "*").strip(),
        "tgt_type": tgt_type,
        "key": key,
        "value": db.Jsonb(_parse_value(b.get("value")) if parse else b.get("value")),
        "priority": int(b.get("priority") or 100),
        "enabled": bool(b.get("enabled", True)),
        "sensitive": bool(b.get("sensitive", False)),
        "description": (b.get("description") or "").strip(),
    }


def _history(pid, action, before, after):
    db.execute(
        "INSERT INTO conductor_pillar_history (pillar_id, action, before, after, changed_by) VALUES (%s,%s,%s,%s,%s)",
        (pid, action, db.Jsonb(before) if before else None, db.Jsonb(after) if after else None, g.user["username"]),
    )


def _snapshot(row):
    return {k: row[k] for k in FIELDS if k in row}


# -- pages -------------------------------------------------------------------
@bp.route("/pillar/db")
@login_required
def index():
    return render_template("pillar/db.html", page_title="Database pillar", target_types=PILLAR_TGT_TYPES,
                           minions=keys().get("minions", []))


@bp.route("/pillar/viewer")
@login_required
def viewer():
    return render_template("pillar/viewer.html", page_title="Pillar viewer", minions=keys().get("minions", []),
                           minion=request.args.get("minion", ""))


# -- API ---------------------------------------------------------------------
@bp.route("/api/pillar/db")
@login_required
@api_errors
def api_list():
    rows = db.query("SELECT * FROM conductor_pillar ORDER BY key, priority, id")
    return jsonify([_row_out(r) for r in rows])


@bp.route("/api/pillar/db/<int:pid>")
@login_required
@api_errors
def api_get(pid):
    row = db.query_one("SELECT * FROM conductor_pillar WHERE id = %s", (pid,))
    if not row:
        return jsonify(error="Not found"), 404
    reveal = request.args.get("reveal") == "1" and g.user["is_admin"]
    if reveal and row["sensitive"]:
        audit("pillar_db.reveal", row["key"], {"id": pid})
    return jsonify(_row_out(row, reveal=reveal))


@bp.route("/api/pillar/db", methods=["POST"])
@admin_required
@api_errors
def api_create():
    v = _validate(json_body())
    row = db.query_one(
        """INSERT INTO conductor_pillar (target, tgt_type, key, value, priority, enabled, sensitive, description, updated_by)
           VALUES (%(target)s, %(tgt_type)s, %(key)s, %(value)s, %(priority)s, %(enabled)s, %(sensitive)s,
                   %(description)s, %(user)s) RETURNING *""",
        dict(v, user=g.user["username"]),
    )
    _history(row["id"], "create", None, _snapshot(row))
    audit("pillar_db.create", row["key"], {"id": row["id"], "target": row["target"]})
    return jsonify(_row_out(row))


@bp.route("/api/pillar/db/<int:pid>", methods=["PUT"])
@admin_required
@api_errors
def api_update(pid):
    before = db.query_one("SELECT * FROM conductor_pillar WHERE id = %s", (pid,))
    if not before:
        return jsonify(error="Not found"), 404
    b = json_body()
    keep = before["sensitive"] and b.get("value") == "**********"
    if keep:
        b["value"] = before["value"]  # masked value left untouched
    v = _validate(b, parse=not keep)
    row = db.query_one(
        """UPDATE conductor_pillar SET target=%(target)s, tgt_type=%(tgt_type)s, key=%(key)s, value=%(value)s,
               priority=%(priority)s, enabled=%(enabled)s, sensitive=%(sensitive)s, description=%(description)s,
               updated_by=%(user)s, updated_at=now()
           WHERE id=%(id)s RETURNING *""",
        dict(v, user=g.user["username"], id=pid),
    )
    _history(pid, "update", _snapshot(before), _snapshot(row))
    audit("pillar_db.update", row["key"], {"id": pid})
    return jsonify(_row_out(row))


@bp.route("/api/pillar/db/<int:pid>/toggle", methods=["POST"])
@admin_required
@api_errors
def api_toggle(pid):
    row = db.query_one("UPDATE conductor_pillar SET enabled = NOT enabled, updated_at = now(), updated_by = %s "
                       "WHERE id = %s RETURNING *", (g.user["username"], pid))
    _history(pid, "enable" if row["enabled"] else "disable", None, None)
    audit("pillar_db.toggle", row["key"], {"id": pid, "enabled": row["enabled"]})
    return jsonify(_row_out(row))


@bp.route("/api/pillar/db/<int:pid>", methods=["DELETE"])
@admin_required
@api_errors
def api_delete(pid):
    row = db.query_one("DELETE FROM conductor_pillar WHERE id = %s RETURNING *", (pid,))
    if not row:
        return jsonify(error="Not found"), 404
    _history(pid, "delete", _snapshot(row), None)
    audit("pillar_db.delete", row["key"], {"id": pid})
    return jsonify(ok=True)


@bp.route("/api/pillar/db/<int:pid>/history")
@login_required
@api_errors
def api_history(pid):
    rows = db.query("SELECT * FROM conductor_pillar_history WHERE pillar_id = %s ORDER BY changed_at DESC LIMIT 50", (pid,))
    current = db.query_one("SELECT sensitive FROM conductor_pillar WHERE id = %s", (pid,))
    out = []
    for r in rows:
        r = _row_out(r)
        if current and current["sensitive"]:
            for side in ("before", "after"):
                if r.get(side):
                    r[side]["value"] = "**********"
        out.append(r)
    return jsonify(out)


@bp.route("/api/pillar/db/<int:pid>/restore/<int:hid>", methods=["POST"])
@admin_required
@api_errors
def api_restore(pid, hid):
    h = db.query_one("SELECT * FROM conductor_pillar_history WHERE id = %s AND pillar_id = %s", (hid, pid))
    if not h or not h["before"]:
        raise ValueError("Nothing to restore for this history entry")
    snap = h["before"]
    exists = db.query_one("SELECT * FROM conductor_pillar WHERE id = %s", (pid,))
    v = _validate(snap, parse=False)
    if exists:
        db.execute(
            """UPDATE conductor_pillar SET target=%(target)s, tgt_type=%(tgt_type)s, key=%(key)s, value=%(value)s,
                   priority=%(priority)s, enabled=%(enabled)s, sensitive=%(sensitive)s, description=%(description)s,
                   updated_by=%(user)s, updated_at=now() WHERE id=%(id)s""",
            dict(v, user=g.user["username"], id=pid),
        )
    else:
        db.execute(
            """INSERT INTO conductor_pillar (id, target, tgt_type, key, value, priority, enabled, sensitive, description, updated_by)
               VALUES (%(id)s, %(target)s, %(tgt_type)s, %(key)s, %(value)s, %(priority)s, %(enabled)s, %(sensitive)s,
                       %(description)s, %(user)s)""",
            dict(v, user=g.user["username"], id=pid),
        )
    _history(pid, "restore", _snapshot(exists) if exists else None, snap)
    audit("pillar_db.restore", snap.get("key"), {"id": pid, "history": hid})
    return jsonify(ok=True)


@bp.route("/api/pillar/db/matches", methods=["POST"])
@login_required
@api_errors
def api_matches():
    """Which minions a target expression matches (asks the minions themselves)."""
    b = json_body()
    tgt_type = b.get("tgt_type", "glob")
    ret = salt().local(b.get("target", "*"), "test.true", tgt_type=tgt_type, timeout=5)
    return jsonify(sorted(ret))


@bp.route("/api/pillar/db/refresh", methods=["POST"])
@login_required
@api_errors
def api_refresh():
    b = json_body()
    ret = salt().local_async(b.get("target", "*"), "saltutil.refresh_pillar", tgt_type=b.get("tgt_type", "glob"))
    audit("pillar.refresh", b.get("target", "*"))
    return jsonify(jid=ret.get("jid"), minions=ret.get("minions", []))


@bp.route("/api/pillar/db/export")
@admin_required
@api_errors
def api_export():
    rows = db.query("SELECT target, tgt_type, key, value, priority, enabled, sensitive, description "
                    "FROM conductor_pillar ORDER BY key, priority, id")
    body = yaml.safe_dump([dict(r) for r in rows], sort_keys=False, allow_unicode=True)
    audit("pillar_db.export", f"{len(rows)} entries")
    return Response(body, mimetype="application/x-yaml",
                    headers={"Content-Disposition": "attachment; filename=conductor-db-pillar.yaml"})


@bp.route("/api/pillar/db/import", methods=["POST"])
@admin_required
@api_errors
def api_import():
    b = json_body()
    try:
        entries = yaml.safe_load(b.get("yaml", "")) or []
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML: {exc}") from exc
    if not isinstance(entries, list):
        raise ValueError("Expected a YAML list of entries")
    if b.get("replace"):
        db.execute("DELETE FROM conductor_pillar")
    count = 0
    for e in entries:
        v = _validate(e, parse=False)
        row = db.query_one(
            """INSERT INTO conductor_pillar (target, tgt_type, key, value, priority, enabled, sensitive, description, updated_by)
               VALUES (%(target)s, %(tgt_type)s, %(key)s, %(value)s, %(priority)s, %(enabled)s, %(sensitive)s,
                       %(description)s, %(user)s) RETURNING *""",
            dict(v, user=g.user["username"]),
        )
        _history(row["id"], "import", None, _snapshot(row))
        count += 1
    audit("pillar_db.import", f"{count} entries", {"replace": bool(b.get("replace"))})
    return jsonify(imported=count)


# -- pillar viewer -------------------------------------------------------------
@bp.route("/api/pillar/sources/<mid>")
@login_required
@api_errors
def api_sources(mid):
    """Explain where a minion's pillar comes from."""
    unmask = request.args.get("unmask") == "1"
    kwarg = {"unmask": True} if unmask else {}
    if unmask:
        audit("pillar.reveal", mid)
    merged = salt().local(mid, "pillar.items", kwarg=kwarg, tgt_type="list", timeout=20).get(mid)

    # database entries matching this minion (asked to the minion via match.* functions)
    entries = db.query("SELECT * FROM conductor_pillar ORDER BY priority, id")
    nodegroups = None
    seen = {}
    db_matches = []
    for e in entries:
        func = {"glob": "match.glob", "list": "match.list", "pcre": "match.pcre", "grain": "match.grain",
                "grain_pcre": "match.grain_pcre", "pillar": "match.pillar", "compound": "match.compound",
                "nodegroup": "match.compound", "ipcidr": "match.ipcidr"}.get(e["tgt_type"], "match.glob")
        expr = e["target"]
        if e["tgt_type"] == "nodegroup":
            if nodegroups is None:
                try:
                    nodegroups = salt().runner("salt.cmd", arg=["config.get", "nodegroups"]) or {}
                except Exception:  # pylint: disable=broad-except
                    nodegroups = {}
            expr = nodegroups.get(expr)
            if isinstance(expr, list):
                expr = " or ".join(expr)
        if not expr:
            ok = False
        elif (func, expr) in seen:
            ok = seen[(func, expr)]
        else:
            try:
                ok = salt().local(mid, func, arg=[expr], tgt_type="list", timeout=10).get(mid)
            except Exception:  # pylint: disable=broad-except
                ok = None
            seen[(func, expr)] = ok
        db_matches.append(dict(_row_out(e), matched=ok))

    # git pillar top assignment (rendered by the master for this minion)
    git_top = None
    try:
        rp = repo("pillar")
        rp.ensure()
        git_top = rp.read("top.sls")
    except Exception:  # pylint: disable=broad-except
        pass

    # OpenBao paths consumed by the vault ext_pillar
    cfg = current_app.config
    vault_paths = []
    if bao().configured and g.user["is_admin"]:
        for path in (f"{cfg['OPENBAO_SALT_PREFIX']}/common", f"{cfg['OPENBAO_SALT_PREFIX']}/minions/{mid}"):
            try:
                sec = bao().read(cfg["OPENBAO_SALT_MOUNT"], path)
                vault_paths.append({"path": f"{cfg['OPENBAO_SALT_MOUNT']}/{path}", "exists": sec is not None,
                                    "keys": sorted((sec or {}).get("data", {}).keys())})
            except Exception as exc:  # pylint: disable=broad-except
                vault_paths.append({"path": f"{cfg['OPENBAO_SALT_MOUNT']}/{path}", "exists": False, "error": str(exc)})
    return jsonify(merged=merged, db=db_matches, git_top=git_top, vault=vault_paths)
