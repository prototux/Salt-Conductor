"""Salt Conductor - a Flask web console for SaltStack (salt-api, gitfs, git_pillar, DB pillar, OpenBao)."""

import json
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone

from flask import Flask, g, jsonify, render_template, request
from markupsafe import Markup, escape
from werkzeug.middleware.proxy_fix import ProxyFix

from . import db
from .config import Config
from .core import check_csrf, csrf_token, has_perm, load_user


def create_app(config_object=Config):
    app = Flask(__name__)
    app.config.from_object(config_object)
    app.permanent_session_lifetime = timedelta(hours=12)
    logging.basicConfig(level=app.config["LOG_LEVEL"], format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not app.config["SECRET_KEY"]:
        app.config["SECRET_KEY"] = _persistent_secret_key(app.config["DATA_DIR"])
    if app.config["PROXY_FIX"]:
        hops = app.config["PROXY_FIX"]
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=hops, x_proto=hops, x_host=hops)

    db.init_db(app)

    from .views import (  # pylint: disable=import-outside-toplevel
        audit, auth, compliance, dashboard, events, gitviews, grains, jobs, keys, minions,
        pillar_db, run, schedules, system, vault,
    )

    for bp in (auth.bp, dashboard.bp, minions.bp, keys.bp, run.bp, jobs.bp, gitviews.states_bp,
               gitviews.pillar_bp, pillar_db.bp, vault.bp, events.bp, schedules.bp, grains.bp,
               compliance.bp, audit.bp, system.bp):
        app.register_blueprint(bp)

    @app.route("/healthz")
    def healthz():
        """Unauthenticated liveness/readiness probe for containers and load balancers."""
        ok = db.available()
        return jsonify(status="ok" if ok else "degraded", database=ok), 200 if ok else 503

    @app.before_request
    def _before():
        if request.endpoint in ("static", "healthz"):
            return None
        load_user()
        return check_csrf()

    @app.context_processor
    def _ctx():
        return {
            "csrf_token": csrf_token,
            "has_perm": has_perm,
            "site_name": app.config["SITE_NAME"],
            "short_name": app.config["SHORT_NAME"],
            "now": datetime.now(timezone.utc),
            "nav": _nav_counters() if g.get("user") else {},
        }

    register_filters(app)

    @app.errorhandler(403)
    def _forbidden(err):
        return render_template("error.html", code=403, title="Forbidden", message=str(err.description)), 403

    @app.errorhandler(404)
    def _notfound(err):
        return render_template("error.html", code=404, title="Page not found",
                               message="The page you are looking for does not exist."), 404

    from .saltapi import SaltAPIError, SaltAuthError  # pylint: disable=import-outside-toplevel

    @app.errorhandler(SaltAPIError)
    def _salt_error(err):
        if isinstance(err, SaltAuthError) or err.status == 403:
            return render_template("error.html", code=403, title="Not allowed",
                                   message="Salt external_auth denied an operation needed by this page for your "
                                           "account."), 403
        return render_template("error.html", code=502, title="Salt API error", message=str(err)), 502

    @app.errorhandler(400)
    def _badreq(err):
        return render_template("error.html", code=400, title="Bad request", message=str(err.description)), 400

    return app


def _persistent_secret_key(data_dir):
    """Generate the session signing key once and share it between workers and restarts."""
    path = os.path.join(data_dir, "secret_key")
    os.makedirs(data_dir, exist_ok=True)
    if not os.path.exists(path):
        tmp = f"{path}.{os.getpid()}"
        with open(tmp, "w", encoding="ascii") as fh:
            fh.write(secrets.token_hex(32))
        os.chmod(tmp, 0o600)
        try:
            os.link(tmp, path)  # atomic: the first worker wins, the others read its key
        except FileExistsError:
            pass
        finally:
            os.unlink(tmp)
    with open(path, encoding="ascii") as fh:
        return fh.read().strip()


def _nav_counters():
    """Counters shown in the topbar/side menu (cheap, cached)."""
    from .core import cached  # pylint: disable=import-outside-toplevel
    from .salt_utils import keys  # pylint: disable=import-outside-toplevel

    out = {"pending_keys": 0, "failed_jobs": 0, "failed_list": []}
    try:
        out["pending_keys"] = len(keys().get("minions_pre", []))
    except Exception:  # pylint: disable=broad-except
        pass

    def failed():
        return db.query(
            """
            SELECT r.jid, r.id, r.fun, r.alter_time FROM conductor_returns r
            WHERE NOT r.ok AND r.alter_time > now() - interval '24 hours'
              AND r.fun NOT LIKE 'runner.vault.%%'
            ORDER BY r.alter_time DESC LIMIT 50
            """
        )

    try:
        rows = cached("nav_failed", 15, failed)
        out["failed_jobs"] = len(rows)
        out["failed_list"] = rows[:6]
    except Exception:  # pylint: disable=broad-except
        pass
    return out


def register_filters(app):
    @app.template_filter("tojson_pretty")
    def tojson_pretty(value):
        return json.dumps(value, indent=2, sort_keys=True, default=str)

    @app.template_filter("timeago")
    def timeago(value):
        if not value:
            return "never"
        if isinstance(value, (int, float)):
            value = datetime.fromtimestamp(value, tz=timezone.utc)
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - value
        secs = int(delta.total_seconds())
        if secs < 0:
            return "in the future"
        for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
            if secs >= size:
                return f"{secs // size}{unit} ago"
        return f"{secs}s ago"

    @app.template_filter("dt")
    def dt(value, fmt="%Y-%m-%d %H:%M:%S"):
        if not value:
            return ""
        if isinstance(value, (int, float)):
            value = datetime.fromtimestamp(value, tz=timezone.utc)
        return value.strftime(fmt)

    @app.template_filter("filesize")
    def filesize(value):
        value = float(value or 0)
        for unit in ("B", "KB", "MB", "GB"):
            if value < 1024:
                return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
            value /= 1024
        return f"{value:.1f} TB"

    @app.template_filter("status_badge")
    def status_badge(status):
        cls = {"up": "success", "down": "danger", "unknown": "secondary", "accepted": "success",
               "pending": "warning", "rejected": "danger", "denied": "dark"}.get(status, "secondary")
        return Markup(f'<span class="badge badge-soft-{cls}">{escape(status)}</span>')

    @app.template_filter("jid_time")
    def jid_time(jid):
        from .salt_utils import jid_to_datetime  # pylint: disable=import-outside-toplevel

        value = jid_to_datetime(jid)
        return value.strftime("%Y-%m-%d %H:%M:%S") if value else ""
