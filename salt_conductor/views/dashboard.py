from collections import Counter

from flask import Blueprint, jsonify, render_template

from .. import db
from ..core import api_errors, bao, login_required, salt_client
from ..salt_utils import all_grains, keys, last_state_runs, minion_status

bp = Blueprint("dashboard", __name__)


def _safe(fn, default):
    try:
        return fn()
    except Exception:  # pylint: disable=broad-except
        return default


@bp.route("/")
@login_required
def index():
    stats = _safe(lambda: db.query_one(
        """
        SELECT
          (SELECT count(*) FROM jids WHERE jid >= to_char(now() - interval '24 hours', 'YYYYMMDDHH24MISS')
              AND load->>'fun' NOT LIKE 'runner.vault.%%' AND load->>'fun' <> 'saltutil.find_job') AS jobs_24h,
          (SELECT count(*) FROM salt_returns WHERE alter_time > now() - interval '24 hours') AS returns_24h,
          (SELECT count(*) FROM conductor_returns WHERE alter_time > now() - interval '24 hours' AND NOT ok
              AND fun NOT LIKE 'runner.vault.%%') AS failed_24h,
          (SELECT count(*) FROM salt_events WHERE alter_time > now() - interval '1 hour') AS events_1h,
          (SELECT count(*) FROM conductor_pillar WHERE enabled) AS db_pillar_entries
        """
    ), {})
    recent_jobs = _safe(lambda: db.query(
        """
        SELECT j.jid, j.fun, j.tgt, j."user",
               count(r.id) AS returned, count(r.id) FILTER (WHERE NOT r.ok) AS failed
        FROM conductor_jobs j LEFT JOIN conductor_returns r ON r.jid = j.jid
        WHERE j.fun NOT IN ('saltutil.find_job') AND j.fun NOT LIKE 'runner.vault.%%'
          AND j.fun NOT IN ('runner.manage.status', 'runner.cache.grains', 'wheel.key.list_all',
                            'runner.jobs.active', 'wheel.key.finger', 'runner.salt.cmd', 'runner.doc.runner',
                            'runner.doc.wheel', 'sys.list_functions', 'test.true', 'match.glob', 'match.list',
                            'match.compound', 'match.grain', 'match.pillar', 'match.pcre')
        GROUP BY j.jid, j.fun, j.tgt, j."user" ORDER BY j.jid DESC LIMIT 8
        """
    ), [])
    audit_rows = _safe(lambda: db.query(
        "SELECT ts, username, action, target, success FROM conductor_audit ORDER BY ts DESC LIMIT 7"), [])
    top_funcs = _safe(lambda: db.query(
        """
        SELECT fun, count(*) AS n FROM salt_returns
        WHERE alter_time > now() - interval '7 days' AND fun NOT LIKE 'runner.vault.%%'
          AND fun NOT IN ('saltutil.find_job', 'runner.manage.status', 'runner.cache.grains', 'wheel.key.list_all', 'runner.jobs.active')
        GROUP BY fun ORDER BY n DESC LIMIT 8
        """
    ), [])
    return render_template("dashboard.html", page_title="Dashboard", stats=stats or {},
                           recent_jobs=recent_jobs, audit_rows=audit_rows, top_funcs=top_funcs)


@bp.route("/api/dashboard/summary")
@login_required
@api_errors
def summary():
    k = keys()
    status = minion_status()
    grains = _safe(all_grains, {})
    runs = last_state_runs()
    accepted = k.get("minions", [])

    compliance = {"compliant": 0, "drift": 0, "failed": 0, "never": 0}
    for mid in accepted:
        s = runs.get(mid)
        if not s:
            compliance["never"] += 1
        elif s["failed"]:
            compliance["failed"] += 1
        elif s["pending"]:
            compliance["drift"] += 1
        else:
            compliance["compliant"] += 1

    os_counter = Counter(g.get("osfinger") or g.get("os", "unknown") for g in grains.values())
    ver_counter = Counter(g.get("saltversion", "?") for g in grains.values())
    return jsonify(
        keys={"accepted": len(accepted), "pending": len(k.get("minions_pre", [])),
              "rejected": len(k.get("minions_rejected", [])), "denied": len(k.get("minions_denied", []))},
        status={"up": status.get("up", []), "down": status.get("down", []), "error": status.get("error", False)},
        compliance=compliance,
        os=os_counter.most_common(),
        versions=ver_counter.most_common(),
    )


@bp.route("/api/dashboard/activity")
@login_required
@api_errors
def activity():
    rows = db.query(
        """
        SELECT date_trunc('hour', alter_time) AS h,
               count(*) FILTER (WHERE ok) AS ok,
               count(*) FILTER (WHERE NOT ok) AS failed
        FROM conductor_returns
        WHERE alter_time > now() - interval '24 hours' AND fun NOT LIKE 'runner.vault.%%'
          AND fun <> 'saltutil.find_job'
        GROUP BY 1 ORDER BY 1
        """
    )
    return jsonify([{"hour": r["h"].isoformat(), "ok": r["ok"], "failed": r["failed"]} for r in rows])


@bp.route("/api/health")
@login_required
def health():
    out = {"salt_api": salt_client().ping(), "database": db.available()}
    h = bao().health() if bao().configured else {"error": "not configured"}
    out["openbao"] = not h.get("error") and not h.get("sealed", True)
    return jsonify(out)
