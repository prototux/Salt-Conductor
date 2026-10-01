"""Salt domain helpers shared by the views."""

import re
import shlex

import yaml
from flask import current_app, g

from . import db
from .core import cached, salt
from .saltapi import SaltAPIError

TARGET_TYPES = [
    ("glob", "Glob"),
    ("list", "List"),
    ("grain", "Grain"),
    ("grain_pcre", "Grain PCRE"),
    ("pillar", "Pillar"),
    ("pcre", "PCRE"),
    ("compound", "Compound"),
    ("nodegroup", "Nodegroup"),
    ("ipcidr", "IP / CIDR"),
]

STATE_FUNCS = ("state.apply", "state.test", "state.highstate", "state.sls", "state.sls_id", "state.single",
               "state.top", "state.orchestrate", "runner.state.orchestrate", "state.orch")


# ---------------------------------------------------------------------------
# argument parsing (CLI-like: positional args and key=value kwargs)
# ---------------------------------------------------------------------------
def _yaml_scalar(value):
    try:
        return yaml.safe_load(value) if value != "" else ""
    except yaml.YAMLError:
        return value


def parse_args(text):
    """Parse 'a b key=value key2="x y"' (one per line or space separated)."""
    args, kwargs = [], {}
    if not text or not text.strip():
        return args, kwargs
    tokens = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            tokens.extend(shlex.split(line))
        except ValueError:
            tokens.append(line)
    for tok in tokens:
        m = re.match(r"^([A-Za-z_][\w.]*)=(.*)$", tok, re.S)
        if m:
            kwargs[m.group(1)] = _yaml_scalar(m.group(2))
        else:
            args.append(_yaml_scalar(tok))
    return args, kwargs


# ---------------------------------------------------------------------------
# minions / keys / grains
# ---------------------------------------------------------------------------
def user_key(name):
    return f"{name}:{g.user['username']}"


def keys(refresh=False):
    def load():
        try:
            return salt().wheel("key.list_all") or {}
        except SaltAPIError:
            # no @wheel permission: derive the accepted minions from the grains cache
            try:
                return {"minions": sorted(all_grains()), "restricted": True}
            except SaltAPIError:
                return {"minions": [], "restricted": True}

    if refresh:
        from .core import cache_clear  # pylint: disable=import-outside-toplevel

        cache_clear(user_key("keys"))
    return cached(user_key("keys"), 15, load)


def minion_status(refresh=False):
    """{'up': [...], 'down': [...]} via manage.status (cached)."""

    def load():
        try:
            ret = salt().runner("manage.status", _timeout=30, timeout=3, gather_job_timeout=5)
        except Exception:  # pylint: disable=broad-except
            ret = None
        if not isinstance(ret, dict):
            return {"up": [], "down": [], "error": True}
        return {"up": sorted(ret.get("up", [])), "down": sorted(ret.get("down", []))}

    if refresh:
        from .core import cache_clear  # pylint: disable=import-outside-toplevel

        cache_clear(user_key("status"))
    return cached(user_key("status"), current_app.config["STATUS_CACHE_SECONDS"], load)


def all_grains(refresh=False):
    """Grains of every accepted minion from the master's cache (no minion round-trip)."""

    def load():
        ret = salt().runner("cache.grains", tgt="*")
        return ret if isinstance(ret, dict) else {}

    if refresh:
        from .core import cache_clear  # pylint: disable=import-outside-toplevel

        cache_clear(user_key("grains"))
    return cached(user_key("grains"), 60, load)


def minion_summary(grains):
    ipv4 = [ip for ip in grains.get("ipv4", []) if ip not in ("127.0.0.1",)]
    return {
        "os": grains.get("os", "?"),
        "osrelease": grains.get("osrelease", ""),
        "osfinger": grains.get("osfinger", grains.get("os", "?")),
        "kernel": grains.get("kernelrelease", ""),
        "ip": ipv4[0] if ipv4 else "",
        "ipv4": ipv4,
        "roles": grains.get("roles", []) if isinstance(grains.get("roles"), list) else [grains.get("roles")],
        "saltversion": grains.get("saltversion", ""),
        "cpus": grains.get("num_cpus", ""),
        "mem": grains.get("mem_total", ""),
        "virtual": grains.get("virtual", ""),
        "fqdn": grains.get("fqdn", ""),
        "environment": grains.get("environment", ""),
        "datacenter": grains.get("datacenter", ""),
    }


def inventory(refresh=False):
    k = keys(refresh)
    status = minion_status(refresh)
    grains = all_grains(refresh)
    last = last_state_runs()
    up = set(status.get("up", []))
    down = set(status.get("down", []))
    rows = []
    for mid in k.get("minions", []):
        st = "up" if mid in up else "down" if mid in down else "unknown"
        row = {"id": mid, "status": st, "grains": grains.get(mid, {})}
        row.update(minion_summary(row["grains"]))
        row["last_state"] = last.get(mid)
        rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# state run result parsing
# ---------------------------------------------------------------------------
def is_state_return(value):
    if not isinstance(value, dict) or not value:
        return False
    sample = next(iter(value.values()))
    first_key = next(iter(value.keys()))
    return isinstance(sample, dict) and "_|-" in str(first_key) and "result" in sample


def summarize_state(ret):
    """Count ok / changed / failed / pending in a state return dict."""
    out = {"total": 0, "ok": 0, "changed": 0, "failed": 0, "pending": 0, "duration": 0.0}
    if isinstance(ret, list):  # rendering errors
        out["failed"] = len(ret) or 1
        out["total"] = out["failed"]
        out["errors"] = ret
        return out
    if not is_state_return(ret):
        return out
    for item in ret.values():
        out["total"] += 1
        result = item.get("result")
        try:
            out["duration"] += float(item.get("duration") or 0)
        except (TypeError, ValueError):
            pass
        if result is False:
            out["failed"] += 1
        elif result is None:
            out["pending"] += 1
        elif item.get("changes"):
            out["changed"] += 1
        else:
            out["ok"] += 1
    out["duration"] = round(out["duration"] / 1000.0, 2)
    return out


def last_state_runs():
    """Latest top-file state run (state.apply / state.test / state.highstate) per minion."""

    def load():
        try:
            rows = db.query(
                """
                SELECT DISTINCT ON (r.id) r.id, r.jid, r.fun, r.return, r.alter_time,
                       r.full_ret -> 'fun_args' AS fun_args, r.success
                FROM salt_returns r
                WHERE r.fun IN ('state.apply', 'state.test', 'state.highstate')
                ORDER BY r.id, r.alter_time DESC
                """
            )
        except Exception:  # pylint: disable=broad-except
            return {}
        out = {}
        for row in rows:
            summ = summarize_state(row["return"])
            fun_args = row["fun_args"] or []
            test = row["fun"] == "state.test" or any(
                (isinstance(a, dict) and a.get("test") in (True, "True", "true"))
                or (isinstance(a, str) and a.lower() == "test=true")
                for a in fun_args
            )
            summ.update({"jid": row["jid"], "fun": row["fun"], "time": row["alter_time"], "test": test})
            out[row["id"]] = summ
        return out

    return cached("last_state_runs", 10, load)


# ---------------------------------------------------------------------------
# jobs (pgjsonb job cache)
# ---------------------------------------------------------------------------
def jid_to_datetime(jid):
    from datetime import datetime  # pylint: disable=import-outside-toplevel

    try:
        return datetime.strptime(str(jid)[:20], "%Y%m%d%H%M%S%f")
    except ValueError:
        return None


def job_list(filters, limit=50, offset=0):
    where, params = ["TRUE"], []
    if filters.get("fun"):
        where.append("j.fun ILIKE %s")
        params.append(f"%{filters['fun']}%")
    if filters.get("user"):
        where.append("j.\"user\" ILIKE %s")
        params.append(f"%{filters['user']}%")
    if filters.get("target"):
        where.append("j.tgt ILIKE %s")
        params.append(f"%{filters['target']}%")
    if filters.get("minion"):
        where.append("EXISTS (SELECT 1 FROM salt_returns x WHERE x.jid = j.jid AND x.id = %s)")
        params.append(filters["minion"])
    if filters.get("jid"):
        where.append("j.jid LIKE %s")
        params.append(f"{filters['jid']}%")
    if not filters.get("show_internal"):
        where.append(
            "coalesce(j.fun,'') NOT IN ('saltutil.find_job', 'runner.jobs.list_jobs', "
            "'runner.vault.get_config', 'runner.vault.generate_new_token', 'runner.vault.get_config_new', "
            "'runner.manage.status', 'runner.cache.grains', 'runner.jobs.active', 'wheel.key.list_all', "
            "'runner.vault.generate_secret_id', 'runner.vault.show_policies', 'wheel.key.finger', "
            "'runner.salt.cmd', 'runner.doc.runner', 'runner.doc.wheel', 'sys.list_functions', 'sys.doc', "
            "'test.true', 'match.glob', 'match.list', 'match.compound', 'match.grain', 'match.pillar', "
            "'match.pcre', 'match.grain_pcre', 'match.ipcidr', 'wheel.key.list_all')"
        )
    having = ""
    if filters.get("status") == "failed":
        having = "HAVING count(r.id) FILTER (WHERE NOT r.ok) > 0"
    elif filters.get("status") == "success":
        having = "HAVING count(r.id) FILTER (WHERE NOT r.ok) = 0 AND count(r.id) > 0"
    elif filters.get("status") == "noreturn":
        having = "HAVING count(r.id) = 0"
    sql = f"""
        SELECT j.jid, j.fun, j.tgt, j.tgt_type, j."user", j.arg, j.minions,
               count(r.id) AS returned,
               count(r.id) FILTER (WHERE r.ok) AS succeeded,
               count(r.id) FILTER (WHERE NOT r.ok) AS failed,
               max(r.alter_time) AS last_return,
               count(*) OVER() AS total_count
        FROM conductor_jobs j
        LEFT JOIN conductor_returns r ON r.jid = j.jid
        WHERE {' AND '.join(where)}
        GROUP BY j.jid, j.fun, j.tgt, j.tgt_type, j."user", j.arg, j.minions
        {having}
        ORDER BY j.jid DESC
        LIMIT %s OFFSET %s
    """
    rows = db.query(sql, params + [limit, offset])
    total = rows[0]["total_count"] if rows else 0
    for row in rows:
        row["time"] = jid_to_datetime(row["jid"])
        row["targeted"] = len(row["minions"]) if isinstance(row["minions"], list) else None
    return rows, total


def job_detail(jid):
    row = db.query_one("SELECT * FROM conductor_jobs WHERE jid = %s", (jid,))
    load = None
    if row:
        load = dict(row["load"] or {})
        load.pop("return", None)
        load.update({k: row[k] for k in ("fun", "tgt", "tgt_type", "user", "arg") if row[k] is not None})
        if row["minions"] is not None:
            load["minions"] = row["minions"]
    returns = db.query(
        "SELECT id, fun, CASE WHEN ok THEN 'true' ELSE 'false' END AS success, return, full_ret, alter_time "
        "FROM conductor_returns WHERE jid = %s ORDER BY id",
        (jid,),
    )
    return load, returns
