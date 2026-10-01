from flask import Blueprint, jsonify, render_template

from ..core import api_errors, audit, cache_clear, json_body, login_required, salt
from ..salt_utils import keys

bp = Blueprint("keys", __name__)

OPS = {"accept": "key.accept", "reject": "key.reject", "delete": "key.delete"}


@bp.route("/keys")
@login_required
def index():
    return render_template("keys/index.html", page_title="Minion keys")


@bp.route("/api/keys")
@login_required
@api_errors
def api_list():
    k = keys(refresh=True)
    try:
        fingers = salt().wheel("key.finger", match="*") or {}
    except Exception:  # pylint: disable=broad-except
        fingers = {}
    flat = {}
    for group in fingers.values():
        if isinstance(group, dict):
            flat.update(group)
    return jsonify(keys=k, fingers=flat)


@bp.route("/api/keys/<op>", methods=["POST"])
@login_required
@api_errors
def api_op(op):
    if op not in OPS:
        raise ValueError("Unknown key operation")
    names = json_body().get("minions") or []
    if not names:
        raise ValueError("No minion selected")
    kwargs = {"match": names}
    if op == "accept":
        kwargs["include_rejected"] = True
        kwargs["include_denied"] = True
    if op == "reject":
        kwargs["include_accepted"] = True
        kwargs["include_denied"] = True
    ret = salt().wheel(OPS[op], **kwargs)
    audit(f"key.{op}", ",".join(names))
    cache_clear()
    return jsonify(result=ret)
