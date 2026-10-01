"""Request-scoped helpers: authentication, CSRF, auditing, caching, service clients."""

import functools
import hmac
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone

from flask import abort, current_app, g, jsonify, redirect, request, session, url_for

from . import db
from .gitrepo import GitRepo
from .openbao import OpenBao
from .saltapi import SaltAPI, SaltAPIError, SaltAuthError


# ---------------------------------------------------------------------------
# service clients
# ---------------------------------------------------------------------------
def salt_client():
    if "salt_client" not in current_app.extensions:
        cfg = current_app.config
        current_app.extensions["salt_client"] = SaltAPI(
            cfg["SALT_API_URL"], verify=cfg["SALT_API_VERIFY_SSL"], timeout=cfg["SALT_TIMEOUT"]
        )
    return current_app.extensions["salt_client"]


def bao():
    if "openbao" not in current_app.extensions:
        cfg = current_app.config
        current_app.extensions["openbao"] = OpenBao(
            cfg["OPENBAO_URL"], cfg["OPENBAO_TOKEN"], cfg["OPENBAO_NAMESPACE"]
        )
    return current_app.extensions["openbao"]


def repo(name):
    key = f"repo_{name}"
    if key not in current_app.extensions:
        cfg = current_app.config
        url = cfg["STATES_REPO"] if name == "states" else cfg["PILLAR_REPO"]
        current_app.extensions[key] = GitRepo(
            name, url, f"{cfg['REPO_DIR'].rstrip('/')}/{name}", cfg["GIT_DEFAULT_BRANCH"]
        )
    return current_app.extensions[key]


class BoundSalt:
    """SaltAPI bound to the logged-in user's eauth token."""

    def __init__(self, client, token):
        self._client = client
        self._token = token

    def __getattr__(self, name):
        func = getattr(self._client, name)
        if name in ("ping",):
            return func
        return functools.partial(func, self._token)


def salt():
    return BoundSalt(salt_client(), g.user["salt_token"])


# ---------------------------------------------------------------------------
# tiny TTL cache (per process)
# ---------------------------------------------------------------------------
_cache = {}
_cache_lock = threading.Lock()


def cached(key, ttl, producer):
    now = time.time()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and hit[0] > now:
            return hit[1]
    value = producer()
    with _cache_lock:
        _cache[key] = (now + ttl, value)
    return value


def cache_clear(prefix=""):
    with _cache_lock:
        for k in [k for k in _cache if str(k).startswith(prefix)]:
            _cache.pop(k, None)


# ---------------------------------------------------------------------------
# sessions / auth
# ---------------------------------------------------------------------------
def create_session(username, eauth, login_ret):
    sid = secrets.token_urlsafe(32)
    expires = datetime.fromtimestamp(float(login_ret.get("expire", time.time() + 43200)), tz=timezone.utc)
    db.execute(
        "INSERT INTO conductor_sessions (id, username, eauth, salt_token, perms, expires_at, ip) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (sid, username, eauth, login_ret["token"], db.Jsonb(login_ret.get("perms", [])), expires,
         request.remote_addr),
    )
    db.execute("DELETE FROM conductor_sessions WHERE expires_at < now()")
    session.clear()
    session["sid"] = sid
    session["csrf"] = secrets.token_hex(16)
    session.permanent = True
    return sid


def destroy_session():
    sid = session.get("sid")
    if sid:
        row = db.query_one("DELETE FROM conductor_sessions WHERE id = %s RETURNING salt_token", (sid,))
        if row:
            salt_client().logout(row["salt_token"])
    session.clear()


def load_user():
    g.user = None
    sid = session.get("sid")
    if not sid:
        return
    try:
        row = db.query_one(
            "UPDATE conductor_sessions SET last_seen = now() WHERE id = %s AND expires_at > now() "
            "RETURNING id, username, eauth, salt_token, perms, expires_at, created_at",
            (sid,),
        )
    except Exception:  # pylint: disable=broad-except
        row = None
    if row:
        g.user = row
        g.user["is_admin"] = _is_admin(row["perms"])


def _is_admin(perms):
    flat = [p for p in perms if isinstance(p, str)]
    return ".*" in flat and "@wheel" in flat and "@runner" in flat


def has_perm(kind):
    """kind: '@wheel', '@runner', '@jobs' or an execution function name."""
    if not g.get("user"):
        return False
    perms = g.user["perms"]
    for p in perms:
        if isinstance(p, str) and (p == kind or p == ".*" and not kind.startswith("@")):
            return True
        if isinstance(p, dict) and kind in p:
            return True
    return False


def wants_json():
    return "/api/" in request.path or request.accept_mimetypes.best == "application/json"


def login_required(view):
    @functools.wraps(view)
    def wrapper(*args, **kwargs):
        if not g.get("user"):
            if wants_json():
                return jsonify(error="Not authenticated"), 401
            return redirect(url_for("auth.login", next=request.full_path))
        return view(*args, **kwargs)

    return wrapper


def admin_required(view):
    @functools.wraps(view)
    @login_required
    def wrapper(*args, **kwargs):
        if not g.user["is_admin"]:
            if wants_json():
                return jsonify(error="Administrator permissions required"), 403
            abort(403)
        return view(*args, **kwargs)

    return wrapper


# ---------------------------------------------------------------------------
# CSRF
# ---------------------------------------------------------------------------
def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return session["csrf"]


def check_csrf():
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    if request.endpoint == "static":
        return
    sent = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token", "")
    if not sent or not hmac.compare_digest(sent, session.get("csrf", "")):
        if wants_json():
            return jsonify(error="CSRF token missing or invalid"), 400
        abort(400, "CSRF token missing or invalid")
    return None


# ---------------------------------------------------------------------------
# audit
# ---------------------------------------------------------------------------
def audit(action, target="", details=None, success=True):
    try:
        db.execute(
            "INSERT INTO conductor_audit (username, action, target, details, success, ip) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (g.user["username"] if g.get("user") else "anonymous", action, str(target)[:500],
             db.Jsonb(details or {}), success, request.remote_addr),
        )
    except Exception:  # pylint: disable=broad-except
        current_app.logger.exception("audit write failed")


# ---------------------------------------------------------------------------
# JSON API helpers
# ---------------------------------------------------------------------------
def api_errors(view):
    """Convert backend exceptions into JSON errors for /api views."""

    @functools.wraps(view)
    def wrapper(*args, **kwargs):
        from .gitrepo import GitError  # pylint: disable=import-outside-toplevel
        from .openbao import OpenBaoError  # pylint: disable=import-outside-toplevel

        try:
            return view(*args, **kwargs)
        except SaltAuthError as exc:
            if g.get("user"):
                # the Salt Conductor session is still valid: salt-api answers 401 when eauth denies a function
                return jsonify(error="Salt external_auth denied this operation for your account"), 403
            return jsonify(error=str(exc), relogin=True), 401
        except (SaltAPIError, GitError, OpenBaoError) as exc:
            status = getattr(exc, "status", None) or 502
            if isinstance(exc, GitError):
                status = 400
            return jsonify(error=str(exc)), status if 400 <= status < 600 else 502
        except ValueError as exc:
            return jsonify(error=str(exc)), 400

    return wrapper


def json_body():
    return request.get_json(silent=True) or {}
