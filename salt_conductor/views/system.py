import os

from flask import Blueprint, current_app, g, jsonify, render_template, request

from .. import db
from ..core import admin_required, api_errors, audit, bao, cache_clear, json_body, login_required, repo, salt, salt_client
from ..salt_utils import all_grains, keys

bp = Blueprint("system", __name__)


@bp.route("/system")
@login_required
def index():
    cfg = current_app.config
    tables = []
    try:
        tables = db.query(
            """SELECT relname AS name, n_live_tup AS rows, pg_total_relation_size(relid) AS bytes
               FROM pg_stat_user_tables ORDER BY pg_total_relation_size(relid) DESC"""
        )
    except Exception:  # pylint: disable=broad-except
        pass
    repos = []
    for name in ("states", "pillar"):
        rp = repo(name)
        info = {"name": name, "url": rp.url, "workdir": rp.workdir, "ok": False}
        try:
            rp.ensure()
            info.update(ok=True, branch=rp.current_branch(), changes=len(rp.status()),
                        head=(rp.log(limit=1) or [{}])[0])
        except Exception as exc:  # pylint: disable=broad-except
            info["error"] = str(exc)
        repos.append(info)
    sessions = db.query("SELECT id, username, eauth, created_at, last_seen, expires_at, ip FROM conductor_sessions "
                        "WHERE expires_at > now() ORDER BY last_seen DESC")
    bao_health = bao().health() if bao().configured else None
    return render_template("system/index.html", page_title="System status", tables=tables, repos=repos,
                           sessions=sessions, bao_health=bao_health, salt_up=salt_client().ping(),
                           db_up=db.available(), settings={
                               "Salt API": cfg["SALT_API_URL"], "Database": _redact(cfg["DATABASE_URL"]),
                               "OpenBao": cfg["OPENBAO_URL"] or "-", "OpenBao Salt KV path":
                               f"{cfg['OPENBAO_SALT_MOUNT']}/{cfg['OPENBAO_SALT_PREFIX']}",
                               "Repositories dir": cfg["REPO_DIR"], "Auto deploy on push": cfg["GIT_AUTO_DEPLOY"],
                               "eauth backends": ", ".join(cfg["SALT_EAUTH_BACKENDS"]),
                           })


def _redact(url):
    if "@" in url and "://" in url:
        scheme, rest = url.split("://", 1)
        creds, host = rest.rsplit("@", 1)
        return f"{scheme}://{creds.split(':')[0]}:***@{host}"
    return url


@bp.route("/api/system/versions")
@login_required
@api_errors
def api_versions():
    ret = salt().runner("manage.versions", _timeout=60)
    master = salt().runner("salt.cmd", arg=["test.version"])
    return jsonify(minions=ret, master=master)


@bp.route("/api/system/maintenance", methods=["POST"])
@admin_required
@api_errors
def api_maintenance():
    b = json_body()
    action = b.get("action")
    days = int(b.get("days") or 30)
    if action == "purge_events":
        n = db.execute("DELETE FROM salt_events WHERE alter_time < now() - make_interval(days => %s)", (days,))
    elif action == "purge_audit":
        n = db.execute("DELETE FROM conductor_audit WHERE ts < now() - make_interval(days => %s)", (days,))
    elif action == "purge_jobs":
        n = db.execute("DELETE FROM salt_returns WHERE alter_time < now() - make_interval(days => %s)", (days,))
        db.execute("DELETE FROM jids j WHERE NOT EXISTS (SELECT 1 FROM salt_returns r WHERE r.jid = j.jid) "
                   "AND j.jid < to_char(now() - make_interval(days => %s), 'YYYYMMDDHH24MISS')", (days,))
    elif action == "clear_cache":
        cache_clear()
        n = 0
    elif action == "fileserver_update":
        salt().runner("fileserver.update", _timeout=120)
        salt().runner("git_pillar.update", _timeout=120)
        n = 0
    else:
        raise ValueError("Unknown action")
    audit(f"system.{action}", str(days))
    return jsonify(affected=n)


@bp.route("/api/system/sessions/<sid>", methods=["DELETE"])
@admin_required
@api_errors
def api_revoke(sid):
    row = db.query_one("DELETE FROM conductor_sessions WHERE id = %s RETURNING username, salt_token", (sid,))
    if row:
        salt_client().logout(row["salt_token"])
        audit("session.revoke", row["username"])
    return jsonify(ok=True)


@bp.route("/search")
@login_required
def search():
    q = request.args.get("q", "").strip()
    res = {"minions": [], "jobs": [], "files": [], "pillar": [], "grains": []}
    if len(q) >= 2:
        ql = q.lower()
        grains = {}
        try:
            grains = all_grains()
        except Exception:  # pylint: disable=broad-except
            pass
        for mid in keys().get("minions", []):
            gr = grains.get(mid, {})
            hay = " ".join([mid, gr.get("fqdn", ""), " ".join(gr.get("ipv4", [])), gr.get("osfinger", ""),
                            " ".join(map(str, gr.get("roles", []) or []))]).lower()
            if ql in hay:
                res["minions"].append({"id": mid, "os": gr.get("osfinger", ""), "ip": ", ".join(gr.get("ipv4", []))})
        res["jobs"] = db.query(
            """SELECT jid, fun, tgt, "user" FROM conductor_jobs
               WHERE jid LIKE %s OR fun ILIKE %s OR tgt ILIKE %s OR "user" ILIKE %s
               ORDER BY jid DESC LIMIT 20""",
            (f"{q}%", f"%{q}%", f"%{q}%", f"%{q}%"),
        )
        for name in ("states", "pillar"):
            try:
                rp = repo(name)
                rp.ensure()
                for f in rp.walk_files():
                    if ql in f.lower():
                        res["files"].append({"repo": name, "path": f, "match": "name"})
                for hit in rp.grep(q, limit=30):
                    res["files"].append({"repo": name, "path": hit["path"], "line": hit["line"], "text": hit["text"],
                                         "match": "content"})
            except Exception:  # pylint: disable=broad-except
                pass
        res["pillar"] = db.query(
            "SELECT id, key, target, tgt_type FROM conductor_pillar WHERE key ILIKE %s OR target ILIKE %s "
            "OR description ILIKE %s ORDER BY key LIMIT 30", (f"%{q}%", f"%{q}%", f"%{q}%"))
    total = sum(len(v) for v in res.values())
    return render_template("system/search.html", page_title="Search", q=q, res=res, total=total)
