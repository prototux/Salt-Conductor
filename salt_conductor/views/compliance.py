from flask import Blueprint, current_app, jsonify, render_template

from .. import db
from ..core import api_errors, audit, json_body, login_required, repo, salt
from ..salt_utils import keys, last_state_runs, minion_status

bp = Blueprint("compliance", __name__)


@bp.route("/compliance")
@login_required
def index():
    runs = last_state_runs()
    status = minion_status()
    up = set(status.get("up", []))
    rows = []
    for mid in keys().get("minions", []):
        s = runs.get(mid)
        if not s:
            state = "never"
        elif s["failed"]:
            state = "failed"
        elif s["pending"]:
            state = "drift"
        else:
            state = "compliant"
        rows.append({"id": mid, "run": s, "state": state, "up": mid in up})
    counts = {k: sum(1 for r in rows if r["state"] == k) for k in ("compliant", "drift", "failed", "never")}
    return render_template("compliance/index.html", page_title="Compliance", rows=rows, counts=counts)


@bp.route("/api/compliance/run", methods=["POST"])
@login_required
@api_errors
def api_run():
    b = json_body()
    minions = b.get("minions") or []
    test = bool(b.get("test", True))
    tgt, tgt_type = (",".join(minions), "list") if minions else ("*", "glob")
    ret = salt().local_async(tgt, "state.test" if test else "state.apply", tgt_type=tgt_type)
    audit("compliance.drift_check" if test else "compliance.remediate", tgt, {"jid": ret.get("jid")})
    return jsonify(jid=ret.get("jid"), minions=ret.get("minions", []))


# -- orchestration --------------------------------------------------------------
@bp.route("/orchestrate")
@login_required
def orchestrate():
    orchs, error, branches = [], None, []
    try:
        rp = repo("states")
        rp.ensure()
        orchs = [s for s in rp.sls_list() if s.startswith(("orch.", "orchestrate.", "orchestration."))]
        branches = rp.branches()
    except Exception as exc:  # pylint: disable=broad-except
        error = str(exc)
    recent = db.query(
        """SELECT j.jid, j.load->>'user' AS "user", j.load->'kwarg' AS kwarg, j.load->'arg' AS arg, r.ok AS success, r.alter_time
           FROM jids j LEFT JOIN conductor_returns r ON r.jid = j.jid
           WHERE j.load->>'fun' IN ('runner.state.orchestrate', 'runner.state.orch', 'runner.state.sls')
           ORDER BY j.jid DESC LIMIT 20"""
    )
    return render_template("compliance/orchestrate.html", page_title="Orchestration", orchs=orchs, error=error,
                           branches=branches, recent=recent, default_branch=current_app.config["GIT_DEFAULT_BRANCH"])


@bp.route("/api/orchestrate", methods=["POST"])
@login_required
@api_errors
def api_orchestrate():
    b = json_body()
    mods = b.get("mods")
    if not mods:
        raise ValueError("Select an orchestration")
    kwarg = {"mods": mods}
    env = b.get("saltenv")
    if env and env != current_app.config["GIT_DEFAULT_BRANCH"]:
        kwarg["saltenv"] = env
    if b.get("test"):
        kwarg["test"] = True
    if b.get("pillar"):
        import yaml  # pylint: disable=import-outside-toplevel

        kwarg["pillar"] = yaml.safe_load(b["pillar"])
    ret = salt().runner_async("state.orchestrate", **kwarg)
    audit("orchestrate", mods, dict(kwarg, jid=ret.get("jid")))
    return jsonify(jid=ret.get("jid"))
