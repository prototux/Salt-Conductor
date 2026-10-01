from flask import Blueprint, abort, jsonify, render_template, request

from ..core import api_errors, audit, json_body, login_required, salt
from ..salt_utils import jid_to_datetime, job_detail, job_list, summarize_state

bp = Blueprint("jobs", __name__)

PER_PAGE = 40


@bp.route("/jobs")
@login_required
def index():
    filters = {k: request.args.get(k, "").strip() for k in ("fun", "user", "target", "minion", "status", "jid")}
    filters["show_internal"] = request.args.get("internal") == "1"
    page = max(int(request.args.get("page", 1) or 1), 1)
    rows, total = job_list(filters, limit=PER_PAGE, offset=(page - 1) * PER_PAGE)
    return render_template("jobs/index.html", page_title="Job history", rows=rows, total=total, page=page,
                           per_page=PER_PAGE, filters=filters)


def _job_payload(jid):
    load, returns = job_detail(jid)
    if load is None and not returns:
        return None
    targeted = load.get("minions") if isinstance(load, dict) else None
    out_returns = {}
    for r in returns:
        full = r["full_ret"] or {}
        ret = r["return"]
        retcode = full.get("retcode")
        # runner jobs are stored with their event envelope: {fun, jid, return, success, ...}
        if str(r["fun"]).startswith("runner.") and isinstance(ret, dict) and "return" in ret and "fun" in ret:
            ret = ret["return"]
            if isinstance(ret, dict) and isinstance(ret.get("data"), dict) and ret.get("outputter") == "highstate":
                # orchestration: one highstate-like result per orchestrating master
                retcode = ret.get("retcode", retcode)
                for host, data in ret["data"].items():
                    out_returns[host] = {
                        "ret": data, "retcode": retcode, "success": not retcode,
                        "time": r["alter_time"].isoformat() if r["alter_time"] else None,
                        "summary": summarize_state(data),
                    }
                continue
        out_returns[r["id"]] = {
            "ret": ret,
            "retcode": retcode,
            "success": r["success"] == "true",
            "time": r["alter_time"].isoformat() if r["alter_time"] else None,
            "summary": summarize_state(ret) if isinstance(ret, (dict, list)) else None,
        }
    missing = sorted(set(targeted or []) - set(out_returns)) if isinstance(targeted, list) else []
    started = jid_to_datetime(jid)
    return {
        "jid": jid,
        "load": load or {},
        "returns": out_returns,
        "targeted": targeted,
        "missing": missing,
        "started": started.isoformat() + "Z" if started else None,
    }


@bp.route("/jobs/<jid>")
@login_required
def detail(jid):
    payload = _job_payload(jid)
    if payload is None:
        abort(404)
    return render_template("jobs/detail.html", page_title=f"Job {jid}", job=payload, jid=jid)


@bp.route("/api/jobs/<jid>")
@login_required
@api_errors
def api_detail(jid):
    payload = _job_payload(jid)
    if payload is None:
        return jsonify(error="Job not found"), 404
    return jsonify(payload)


@bp.route("/jobs/active")
@login_required
def active():
    return render_template("jobs/active.html", page_title="Running jobs")


@bp.route("/api/jobs/active")
@login_required
@api_errors
def api_active():
    ret = salt().runner("jobs.active", _timeout=60) or {}
    return jsonify(ret if isinstance(ret, dict) else {})


@bp.route("/api/jobs/<jid>/kill", methods=["POST"])
@login_required
@api_errors
def api_kill(jid):
    body = json_body()
    tgt = body.get("tgt") or "*"
    tgt_type = body.get("tgt_type", "glob")
    ret = salt().local(tgt, "saltutil.kill_job", arg=[jid], tgt_type=tgt_type, timeout=10)
    audit("job.kill", jid, {"tgt": tgt})
    return jsonify(result=ret)
