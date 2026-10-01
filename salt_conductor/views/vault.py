"""OpenBao (Vault-compatible) secrets used by the Salt vault ext_pillar."""

from flask import Blueprint, current_app, jsonify, render_template, request

from ..core import admin_required, api_errors, audit, bao, json_body
from ..openbao import OpenBaoError
from ..salt_utils import keys

# Salt Conductor talks to OpenBao with its own (privileged) token: Salt administrators only.
bp = Blueprint("vault", __name__)

MINION_POLICY = """# Managed by Salt Conductor: pillar secrets readable by minion {minion}
path "{mount}/data/{prefix}/minions/{minion}"     {{ capabilities = ["read"] }}
path "{mount}/metadata/{prefix}/minions/{minion}" {{ capabilities = ["read", "list"] }}
"""


def _cfg():
    c = current_app.config
    return c["OPENBAO_SALT_MOUNT"], c["OPENBAO_SALT_PREFIX"]


@bp.route("/vault")
@admin_required
def index():
    client = bao()
    if not client.configured:
        return render_template("vault/unconfigured.html", page_title="Secrets (OpenBao)")
    mount, prefix = _cfg()
    health = client.health()
    ctx = {"health": health, "mount": mount, "prefix": prefix, "error": None}
    try:
        ctx["token"] = client.token_info()
        ctx["mounts"] = client.mounts()
        ctx["policies"] = client.policies()
        minion_paths = set(k.rstrip("/") for k in client.list(mount, f"{prefix}/minions"))
        ctx["common"] = client.read(mount, f"{prefix}/common")
        ctx["coverage"] = [
            {"id": m, "secret": m in minion_paths, "policy": f"saltstack/{m}" in ctx["policies"]}
            for m in keys().get("minions", [])
        ]
    except OpenBaoError as exc:
        ctx["error"] = str(exc)
    return render_template("vault/index.html", page_title="Secrets (OpenBao)", **ctx)


@bp.route("/vault/browse")
@admin_required
def browse():
    if not bao().configured:
        return render_template("vault/unconfigured.html", page_title="Secrets (OpenBao)")
    mount = request.args.get("mount") or _cfg()[0]
    path = request.args.get("path", "").lstrip("/")
    return render_template("vault/browse.html", page_title="Secrets browser", mount=mount, path=path,
                           mounts=list(bao().kv_mounts().values()))


# -- API ---------------------------------------------------------------------
@bp.route("/api/vault/list")
@admin_required
@api_errors
def api_list():
    mount = request.args.get("mount") or _cfg()[0]
    path = request.args.get("path", "")
    return jsonify(bao().list(mount, path))


@bp.route("/api/vault/secret")
@admin_required
@api_errors
def api_read():
    mount = request.args.get("mount") or _cfg()[0]
    path = request.args.get("path", "")
    version = request.args.get("version")
    reveal = request.args.get("reveal") == "1"
    sec = bao().read(mount, path, version=version)
    if sec is None:
        return jsonify(error="Secret not found"), 404
    if reveal:
        audit("vault.reveal", f"{mount}/{path}", {"version": version})
    else:
        sec["data"] = {k: None for k in sec["data"]}
    sec["versions"] = (bao().metadata(mount, path) or {}).get("versions")
    return jsonify(sec)


@bp.route("/api/vault/secret", methods=["POST"])
@admin_required
@api_errors
def api_write():
    b = json_body()
    mount = b.get("mount") or _cfg()[0]
    path = (b.get("path") or "").strip("/")
    data = b.get("data") or {}
    if not path:
        raise ValueError("Path is required")
    if not isinstance(data, dict) or not data:
        raise ValueError("At least one key/value is required")
    if b.get("merge_existing"):
        # keys submitted without a value keep their current value
        current = (bao().read(mount, path) or {}).get("data", {})
        data = {k: (current.get(k) if v is None else v) for k, v in data.items()}
    bao().write(mount, path, data)
    audit("vault.write", f"{mount}/{path}", {"keys": sorted(data)})
    return jsonify(ok=True)


@bp.route("/api/vault/secret", methods=["DELETE"])
@admin_required
@api_errors
def api_delete():
    mount = request.args.get("mount") or _cfg()[0]
    path = request.args.get("path", "")
    bao().delete(mount, path)
    audit("vault.delete", f"{mount}/{path}")
    return jsonify(ok=True)


@bp.route("/api/vault/rollback", methods=["POST"])
@admin_required
@api_errors
def api_rollback():
    b = json_body()
    mount = b.get("mount") or _cfg()[0]
    bao().rollback(mount, b.get("path", ""), b.get("version"))
    audit("vault.rollback", f"{mount}/{b.get('path')}", {"version": b.get("version")})
    return jsonify(ok=True)


@bp.route("/api/vault/policy/<path:name>")
@admin_required
@api_errors
def api_policy(name):
    return jsonify(name=name, rules=bao().policy(name))


@bp.route("/api/vault/policy", methods=["POST"])
@admin_required
@api_errors
def api_policy_write():
    b = json_body()
    name = (b.get("name") or "").strip()
    if not name or name in ("root", "default"):
        raise ValueError("Invalid policy name")
    bao().write_policy(name, b.get("rules", ""))
    audit("vault.policy.write", name)
    return jsonify(ok=True)


@bp.route("/api/vault/policy/<path:name>", methods=["DELETE"])
@admin_required
@api_errors
def api_policy_delete(name):
    if name in ("root", "default"):
        raise ValueError("Built-in policies cannot be deleted")
    bao().delete_policy(name)
    audit("vault.policy.delete", name)
    return jsonify(ok=True)


@bp.route("/api/vault/onboard", methods=["POST"])
@admin_required
@api_errors
def api_onboard():
    """Create the per-minion policy (and an empty secret path) used by the vault ext_pillar."""
    mount, prefix = _cfg()
    minions = json_body().get("minions") or []
    done = []
    for m in minions:
        bao().write_policy(f"saltstack/{m}", MINION_POLICY.format(mount=mount, prefix=prefix, minion=m))
        if bao().read(mount, f"{prefix}/minions/{m}") is None:
            bao().write(mount, f"{prefix}/minions/{m}", {"managed_by": "salt-conductor"})
        done.append(m)
    audit("vault.onboard", ",".join(done))
    return jsonify(done=done)
