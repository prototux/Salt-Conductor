from collections import defaultdict

from flask import Blueprint, jsonify, render_template, request

from ..core import api_errors, login_required
from ..salt_utils import all_grains

bp = Blueprint("grains", __name__)


@bp.route("/grains")
@login_required
def index():
    return render_template("grains/index.html", page_title="Grains explorer", grain=request.args.get("grain", "os"))


@bp.route("/api/grains/keys")
@login_required
@api_errors
def api_keys():
    grains = all_grains(refresh=request.args.get("refresh") == "1")
    keys = set()
    for g in grains.values():
        keys.update(g.keys())
    return jsonify(sorted(keys))


@bp.route("/api/grains/distribution")
@login_required
@api_errors
def api_distribution():
    key = request.args.get("grain", "os")
    dist = defaultdict(list)
    for mid, g in all_grains().items():
        value = g
        for part in key.split(":"):
            value = value.get(part) if isinstance(value, dict) else None
        values = value if isinstance(value, list) else [value]
        for v in values or [None]:
            label = "(not set)" if v is None else v if isinstance(v, str) else str(v)
            dist[label].append(mid)
    out = sorted(({"value": k, "minions": sorted(v), "count": len(v)} for k, v in dist.items()),
                 key=lambda x: -x["count"])
    return jsonify(out)
