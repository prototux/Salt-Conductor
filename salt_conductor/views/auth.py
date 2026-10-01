from flask import Blueprint, current_app, flash, g, redirect, render_template, request, url_for

from ..core import audit, create_session, destroy_session, salt_client
from ..saltapi import SaltAPIError, SaltAuthError

bp = Blueprint("auth", __name__)


@bp.route("/login", methods=["GET", "POST"])
def login():
    if g.get("user"):
        return redirect(url_for("dashboard.index"))
    cfg = current_app.config
    error = None
    username = request.form.get("username", "")
    eauth = request.form.get("eauth", cfg["SALT_DEFAULT_EAUTH"])
    if request.method == "POST":
        password = request.form.get("password", "")
        try:
            ret = salt_client().login(username, password, eauth)
            create_session(username, eauth, ret)
            g.user = {"username": username}
            audit("auth.login", username, {"eauth": eauth})
            nxt = request.args.get("next") or ""
            if not nxt.startswith("/") or nxt.startswith("//"):
                nxt = url_for("dashboard.index")
            return redirect(nxt)
        except SaltAuthError:
            error = ("Login refused: wrong username or password for this backend, or your account is not "
                     "allowed to use Salt (e.g. not in the required LDAP group).")
        except SaltAPIError as exc:
            error = f"Cannot reach salt-api: {exc}"
    return render_template("login.html", error=error, username=username, eauth=eauth,
                           backends=cfg["SALT_EAUTH_BACKENDS"])


@bp.route("/logout", methods=["POST"])
def logout():
    if g.get("user"):
        audit("auth.logout", g.user["username"])
    destroy_session()
    flash("You have been logged out.", "info")
    return redirect(url_for("auth.login"))
