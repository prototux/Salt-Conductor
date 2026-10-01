from flask import Blueprint, abort, jsonify, render_template, request

from .. import db
from ..core import api_errors, audit, json_body, login_required, salt
from ..salt_utils import (TARGET_TYPES, all_grains, inventory, keys, last_state_runs, minion_status,
                          minion_summary, summarize_state)

bp = Blueprint("minions", __name__)

# Quick actions available from the minion list / detail page
ACTIONS = {
    "ping": ("test.ping", [], {}),
    # state.apply without SLS runs the top file ("highstate"); state.test = state.apply test=True
    "state_test": ("state.test", [], {}),
    "state_apply": ("state.apply", [], {}),
    "refresh_pillar": ("saltutil.refresh_pillar", [], {}),
    "sync_all": ("saltutil.sync_all", [], {}),
    "refresh_grains": ("saltutil.refresh_grains", [], {}),
    "update_mine": ("mine.update", [], {}),
}

# Lazy-loaded tabs on the minion page: section -> (function, args, kwargs)
SECTIONS = {
    "packages": ("pkg.list_pkgs", [], {}),
    "upgrades": ("pkg.list_upgrades", [], {"refresh": False}),
    "services": ("service.get_all", [], {}),
    "running": ("service.get_running", [], {}),
    "disks": ("disk.usage", [], {}),
    "network": ("network.interfaces", [], {}),
    "uptime": ("status.uptime", [], {}),
    "loadavg": ("status.loadavg", [], {}),
    "meminfo": ("status.meminfo", [], {}),
    "top": ("state.show_top", [], {}),
    "schedule": ("schedule.list", [], {"return_yaml": False}),
    "beacons": ("beacons.list", [], {"return_yaml": False}),
    "users": ("user.list_users", [], {}),
    "cron": ("cron.raw_cron", ["root"], {}),
    "versions": ("test.versions_report", [], {}),
    "procs": ("cmd.run", ["ps aux --sort=-%cpu | head -n 25"], {"python_shell": True}),
}


@bp.route("/minions")
@login_required
def index():
    return render_template("minions/index.html", page_title="Minions", target_types=TARGET_TYPES)


@bp.route("/api/minions")
@login_required
@api_errors
def api_list():
    refresh = request.args.get("refresh") == "1"
    rows = inventory(refresh)
    for row in rows:
        row.pop("grains", None)
        if row.get("last_state") and row["last_state"].get("time"):
            row["last_state"] = dict(row["last_state"], time=row["last_state"]["time"].isoformat())
    k = keys()
    return jsonify(minions=rows, pending=k.get("minions_pre", []),
                   nodegroups=_nodegroups())


def _nodegroups():
    from ..core import cached  # pylint: disable=import-outside-toplevel
    from ..salt_utils import user_key  # pylint: disable=import-outside-toplevel

    def load():
        try:
            ret = salt().runner("salt.cmd", arg=["config.get", "nodegroups"])
            return ret if isinstance(ret, dict) else {}
        except Exception:  # pylint: disable=broad-except
            return {}

    return cached(user_key("nodegroups"), 300, load)


@bp.route("/api/minions/action", methods=["POST"])
@login_required
@api_errors
def api_action():
    body = json_body()
    action = body.get("action")
    if action not in ACTIONS:
        raise ValueError("Unknown action")
    minions = body.get("minions") or []
    tgt = body.get("tgt")
    tgt_type = body.get("tgt_type", "list")
    if minions:
        tgt, tgt_type = ",".join(minions), "list"
    if not tgt:
        raise ValueError("No target selected")
    fun, arg, kwarg = ACTIONS[action]
    long_running = fun.startswith("state.") or action == "sync_all"
    if long_running or body.get("async"):
        ret = salt().local_async(tgt, fun, arg=arg, kwarg=kwarg, tgt_type=tgt_type)
        audit(f"minion.{action}", tgt, {"jid": ret.get("jid"), "tgt_type": tgt_type})
        return jsonify(jid=ret.get("jid"), minions=ret.get("minions", []), fun=fun)
    ret = salt().local(tgt, fun, arg=arg, kwarg=kwarg, tgt_type=tgt_type, timeout=15)
    audit(f"minion.{action}", tgt, {"tgt_type": tgt_type})
    return jsonify(result=ret, fun=fun)


@bp.route("/minions/<mid>")
@login_required
def detail(mid):
    k = keys()
    state = "accepted" if mid in k.get("minions", []) else "pending" if mid in k.get("minions_pre", []) else None
    if state is None:
        abort(404)
    grains = all_grains().get(mid, {})
    status = minion_status()
    online = "up" if mid in status.get("up", []) else "down" if mid in status.get("down", []) else "unknown"
    last = last_state_runs().get(mid)
    jobs = db.query(
        """
        SELECT r.jid, r.fun, CASE WHEN r.ok THEN 'true' ELSE 'false' END AS success, r.alter_time,
               j.load->>'user' AS "user"
        FROM conductor_returns r LEFT JOIN jids j ON j.jid = r.jid
        WHERE r.id = %s ORDER BY r.alter_time DESC LIMIT 25
        """,
        (mid,),
    )
    db_pillar = db.query("SELECT id, target, tgt_type, key, priority, enabled FROM conductor_pillar ORDER BY priority, id")
    return render_template("minions/detail.html", page_title=mid, mid=mid, key_state=state, grains=grains,
                           summary=minion_summary(grains), online=online, last=last, jobs=jobs,
                           db_pillar=db_pillar, sections=list(SECTIONS))


@bp.route("/api/minions/<mid>/ping")
@login_required
@api_errors
def api_ping(mid):
    ret = salt().local(mid, "test.ping", tgt_type="list", timeout=5)
    return jsonify(up=bool(ret.get(mid)))


@bp.route("/api/minions/<mid>/grains")
@login_required
@api_errors
def api_grains(mid):
    if request.args.get("live") == "1":
        ret = salt().local(mid, "grains.items", tgt_type="list", timeout=15)
        return jsonify(ret.get(mid, {}))
    return jsonify(all_grains(refresh=request.args.get("refresh") == "1").get(mid, {}))


@bp.route("/api/minions/<mid>/pillar")
@login_required
@api_errors
def api_pillar(mid):
    unmask = request.args.get("unmask") == "1"
    kwarg = {"unmask": True} if unmask else {}
    if unmask:
        audit("pillar.reveal", mid)
    ret = salt().local(mid, "pillar.items", kwarg=kwarg, tgt_type="list", timeout=20)
    return jsonify(ret.get(mid))


@bp.route("/api/minions/<mid>/section/<section>")
@login_required
@api_errors
def api_section(mid, section):
    if section not in SECTIONS:
        abort(404)
    fun, arg, kwarg = SECTIONS[section]
    ret = salt().local(mid, fun, arg=arg, kwarg=kwarg, tgt_type="list", timeout=30)
    if mid not in ret:
        return jsonify(error="Minion did not return (offline or timed out)"), 504
    return jsonify(ret[mid])


@bp.route("/api/minions/<mid>/grains/set", methods=["POST"])
@login_required
@api_errors
def api_grain_set(mid):
    body = json_body()
    key = (body.get("key") or "").strip()
    if not key:
        raise ValueError("Grain name required")
    if body.get("delete"):
        ret = salt().local(mid, "grains.delkey", arg=[key], tgt_type="list", kwarg={"force": True})
        audit("grain.delete", mid, {"key": key})
    else:
        ret = salt().local(mid, "grains.setval", arg=[key, body.get("value")], tgt_type="list")
        audit("grain.set", mid, {"key": key, "value": body.get("value")})
    from ..core import cache_clear  # pylint: disable=import-outside-toplevel

    cache_clear("grains:")
    return jsonify(result=ret.get(mid))


@bp.route("/api/minions/<mid>/service", methods=["POST"])
@login_required
@api_errors
def api_service(mid):
    body = json_body()
    name, op = body.get("name"), body.get("op")
    if op not in ("start", "stop", "restart", "enable", "disable"):
        raise ValueError("Invalid operation")
    ret = salt().local(mid, f"service.{op}", arg=[name], tgt_type="list", timeout=30)
    audit(f"service.{op}", mid, {"service": name})
    return jsonify(result=ret.get(mid))


@bp.route("/api/minions/<mid>/pkg", methods=["POST"])
@login_required
@api_errors
def api_pkg(mid):
    body = json_body()
    name, op = body.get("name"), body.get("op")
    if op not in ("install", "remove", "upgrade"):
        raise ValueError("Invalid operation")
    if op == "upgrade":
        ret = salt().local_async(mid, "pkg.upgrade", tgt_type="list")
        audit("pkg.upgrade", mid, {"jid": ret.get("jid")})
        return jsonify(jid=ret.get("jid"))
    ret = salt().local(mid, f"pkg.{op}", arg=[name], tgt_type="list", timeout=120)
    audit(f"pkg.{op}", mid, {"pkg": name})
    return jsonify(result=ret.get(mid))


@bp.route("/api/minions/<mid>/states")
@login_required
@api_errors
def api_last_state(mid):
    row = db.query_one(
        """SELECT jid, fun, return, alter_time, full_ret->'fun_args' AS fun_args FROM salt_returns
           WHERE id = %s AND fun IN ('state.apply', 'state.test', 'state.highstate', 'state.sls')
           ORDER BY alter_time DESC LIMIT 1""",
        (mid,),
    )
    if not row:
        return jsonify(None)
    return jsonify(jid=row["jid"], fun=row["fun"], time=row["alter_time"].isoformat(),
                   fun_args=row["fun_args"], ret=row["return"], summary=summarize_state(row["return"]))
