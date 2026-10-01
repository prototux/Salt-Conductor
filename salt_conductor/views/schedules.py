from flask import Blueprint, jsonify, render_template, request

from ..core import api_errors, audit, json_body, login_required, salt
from ..salt_utils import TARGET_TYPES, keys, parse_args

bp = Blueprint("schedules", __name__)


@bp.route("/schedules")
@login_required
def index():
    return render_template("schedules/index.html", page_title="Schedules", target_types=TARGET_TYPES,
                           minions=keys().get("minions", []))


@bp.route("/api/schedules")
@login_required
@api_errors
def api_list():
    tgt = request.args.get("tgt", "*")
    tgt_type = request.args.get("tgt_type", "glob")
    ret = salt().local(tgt, "schedule.list", kwarg={"return_yaml": False}, tgt_type=tgt_type, timeout=15)
    return jsonify(ret)


@bp.route("/api/schedules", methods=["POST"])
@login_required
@api_errors
def api_add():
    b = json_body()
    name = (b.get("name") or "").strip()
    fun = (b.get("function") or "").strip()
    if not name or not fun:
        raise ValueError("Name and function are required")
    arg, kwarg = parse_args(b.get("args", ""))
    job = {"function": fun, "persist": bool(b.get("persist", True))}
    if arg:
        job["job_args"] = arg
    if kwarg:
        job["job_kwargs"] = kwarg
    if b.get("cron"):
        job["cron"] = b["cron"]
    else:
        unit = b.get("unit", "minutes")
        if unit not in ("seconds", "minutes", "hours", "days"):
            raise ValueError("Invalid interval unit")
        job[unit] = int(b.get("every") or 0)
        if job[unit] <= 0:
            raise ValueError("Interval must be positive")
    if b.get("splay"):
        job["splay"] = int(b["splay"])
    if b.get("return_job") is False:
        job["return_job"] = False
    ret = salt().local(b.get("tgt", "*"), "schedule.add", arg=[name], kwarg=job, tgt_type=b.get("tgt_type", "glob"),
                       timeout=15)
    audit("schedule.add", b.get("tgt", "*"), dict(job, name=name))
    return jsonify(result=ret)


@bp.route("/api/schedules/<op>", methods=["POST"])
@login_required
@api_errors
def api_op(op):
    funcs = {"delete": "schedule.delete", "enable": "schedule.enable_job", "disable": "schedule.disable_job",
             "run": "schedule.run_job"}
    if op not in funcs:
        raise ValueError("Unknown operation")
    b = json_body()
    kwarg = {"persist": True} if op in ("delete", "enable", "disable") else {}
    ret = salt().local(b.get("minions"), funcs[op], arg=[b.get("name")], kwarg=kwarg, tgt_type="list", timeout=15)
    audit(f"schedule.{op}", b.get("minions"), {"name": b.get("name")})
    return jsonify(result=ret)
