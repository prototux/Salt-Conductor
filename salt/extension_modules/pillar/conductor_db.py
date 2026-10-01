"""
Salt Conductor database pillar
==============================

Serves pillar data stored in the ``conductor_pillar`` PostgreSQL table, which is
managed from Salt Conductor, the Salt web console ("Pillar > Database" page).

Each row has a target expression (with the same target types as top files:
glob, list, pcre, grain, grain_pcre, pillar, compound, nodegroup), a
colon-delimited pillar key (``app:db:password``), a JSON value and a priority.
Matching rows are applied in ascending priority, so higher priorities win.

.. code-block:: yaml

    extension_modules: /srv/salt-ext
    ext_pillar:
      - conductor_db:
          host: postgres
          port: 5432
          dbname: salt
          user: salt
          password: salt
"""

import copy
import logging

import salt.loader
import salt.utils.dictupdate

try:
    import psycopg2
    import psycopg2.extras

    HAS_PSYCOPG2 = True
except ImportError:
    HAS_PSYCOPG2 = False

log = logging.getLogger(__name__)

__virtualname__ = "conductor_db"

_MATCHERS = {
    "glob": "glob_match.match",
    "list": "list_match.match",
    "pcre": "pcre_match.match",
    "grain": "grain_match.match",
    "grain_pcre": "grain_pcre_match.match",
    "pillar": "pillar_match.match",
    "compound": "compound_match.match",
    "ipcidr": "ipcidr_match.match",
}


def __virtual__():
    if not HAS_PSYCOPG2:
        return False, "conductor_db ext_pillar requires psycopg2"
    return __virtualname__


def _matches(matchers, opts, minion_id, tgt, tgt_type):
    if tgt_type == "nodegroup":
        nodegroups = opts.get("nodegroups", {})
        return matchers["nodegroup_match.match"](
            tgt, nodegroups=nodegroups, opts=opts, minion_id=minion_id
        )
    func = _MATCHERS.get(tgt_type)
    if func is None:
        log.warning("conductor_db: unsupported target type %s", tgt_type)
        return False
    return matchers[func](tgt, opts=opts, minion_id=minion_id)


def _nest(path, value):
    ret = value
    for part in reversed([p for p in path.split(":") if p]):
        ret = {part: ret}
    return ret


def ext_pillar(minion_id, pillar, **kwargs):  # pylint: disable=unused-argument
    conn_args = {
        "host": kwargs.get("host", "localhost"),
        "port": int(kwargs.get("port", 5432)),
        "dbname": kwargs.get("dbname", "salt"),
        "user": kwargs.get("user", "salt"),
        "password": kwargs.get("password", ""),
        "connect_timeout": int(kwargs.get("connect_timeout", 5)),
    }
    table = kwargs.get("table", "conductor_pillar")
    try:
        conn = psycopg2.connect(**conn_args)
    except psycopg2.Error as exc:
        log.error("conductor_db: cannot connect to PostgreSQL: %s", exc)
        return {}

    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                f"SELECT target, tgt_type, key, value FROM {table} "  # nosec
                "WHERE enabled ORDER BY priority ASC, id ASC"
            )
            rows = cur.fetchall()
    except psycopg2.Error as exc:
        log.error("conductor_db: query failed: %s", exc)
        return {}
    finally:
        conn.close()

    opts = copy.copy(__opts__)
    opts["id"] = minion_id
    opts["grains"] = __grains__
    opts["pillar"] = pillar
    matchers = salt.loader.matchers(opts)

    ret = {}
    for row in rows:
        try:
            if not _matches(matchers, opts, minion_id, row["target"], row["tgt_type"]):
                continue
        except Exception as exc:  # pylint: disable=broad-except
            log.warning("conductor_db: bad target %r: %s", row["target"], exc)
            continue
        ret = salt.utils.dictupdate.merge(
            ret, _nest(row["key"], row["value"]), strategy="smart"
        )
    return ret
