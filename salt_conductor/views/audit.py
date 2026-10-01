from flask import Blueprint, g, render_template, request

from .. import db
from ..core import login_required

bp = Blueprint("audit", __name__)

PER_PAGE = 50


@bp.route("/audit")
@login_required
def index():
    f = {k: request.args.get(k, "").strip() for k in ("user", "action", "target", "since")}
    f["failed"] = request.args.get("failed") == "1"
    if not g.user["is_admin"]:
        f["user"] = g.user["username"]  # operators only see their own activity
    where, params = ["TRUE"], []
    if f["user"]:
        where.append("username = %s")
        params.append(f["user"])
    if f["action"]:
        where.append("action ILIKE %s")
        params.append(f"%{f['action']}%")
    if f["target"]:
        where.append("target ILIKE %s")
        params.append(f"%{f['target']}%")
    if f["since"]:
        where.append("ts >= %s::date")
        params.append(f["since"])
    if f["failed"]:
        where.append("NOT success")
    page = max(int(request.args.get("page", 1) or 1), 1)
    rows = db.query(
        f"SELECT *, count(*) OVER() AS total FROM conductor_audit WHERE {' AND '.join(where)} "
        f"ORDER BY ts DESC LIMIT %s OFFSET %s",
        params + [PER_PAGE, (page - 1) * PER_PAGE],
    )
    total = rows[0]["total"] if rows else 0
    users = [r["username"] for r in db.query("SELECT DISTINCT username FROM conductor_audit ORDER BY 1")]
    actions = [r["a"] for r in db.query("SELECT DISTINCT split_part(action, '.', 1) AS a FROM conductor_audit ORDER BY 1")]
    return render_template("audit/index.html", page_title="Audit log", rows=rows, total=total, page=page,
                           per_page=PER_PAGE, f=f, users=users, actions=actions)
