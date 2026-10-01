"""File browser / editor / history for the git repositories behind gitfs and git_pillar."""

import os
import re

import yaml
from flask import Blueprint, current_app, flash, g, jsonify, redirect, render_template, request, url_for

from ..core import admin_required, api_errors, audit, json_body, login_required, repo, salt
from ..gitrepo import TEXT_EXTENSIONS, GitError
from ..salt_utils import TARGET_TYPES, keys

JINJA_RE = re.compile(r"{[{%#]")


def _author():
    name = g.user["username"]
    return name, f"{name}@salt-conductor.local"


def _deploy(kind):
    """Make the master pick up pushed commits right away."""
    out = {}
    if kind == "states":
        out["fileserver.update"] = salt().runner("fileserver.update", backend="gitfs", _timeout=120)
        out["sync_all"] = salt().local_async("*", "saltutil.sync_all").get("jid")
    else:
        out["git_pillar.update"] = salt().runner("git_pillar.update", _timeout=120)
        out["refresh_pillar"] = salt().local_async("*", "saltutil.refresh_pillar").get("jid")
    return out


def _push_and_deploy(rp, kind, deploy):
    """Push the current branch; a failure keeps the local commit and is reported, not raised."""
    out = {}
    try:
        rp.push()
        out["pushed"] = True
    except GitError as exc:
        out["pushed"] = False
        out["push_error"] = str(exc)
        return out
    if deploy:
        try:
            out["deploy"] = _deploy(kind)
        except Exception as exc:  # pylint: disable=broad-except
            out["deploy_error"] = str(exc)
    return out


def make_blueprint(kind):
    bp = Blueprint(kind, __name__, url_prefix=f"/git/{kind}")
    title = "States" if kind == "states" else "Git pillar"

    def r():
        rp = repo(kind)
        rp.ensure()
        return rp

    def common_ctx(rp):
        ahead, behind = rp.ahead_behind()
        return {
            "kind": kind, "title": title, "branch": rp.current_branch(), "branches": rp.branches(),
            "changes": rp.status(), "ahead": ahead, "behind": behind, "remote": rp.url,
        }

    def guard(fn):
        """Render a friendly page when the repository isn't reachable."""

        def wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except GitError as exc:
                return render_template("git/error.html", page_title=title, kind=kind, error=str(exc)), 500

        wrapper.__name__ = fn.__name__
        return wrapper

    # -- pages ---------------------------------------------------------------
    @bp.route("/")
    @login_required
    @guard
    def browse():
        rp = r()
        sls = request.args.get("sls")
        if sls:
            base = sls.replace(".", "/")
            for candidate in (f"{base}.sls", f"{base}/init.sls"):
                if os.path.isfile(rp.safe_path(candidate)[0]):
                    return redirect(url_for(f"{kind}.edit", path=candidate))
        path = request.args.get("path", "").strip("/")
        full, rel = rp.safe_path(path)
        if os.path.isfile(full):
            return redirect(url_for(f"{kind}.edit", path=rel))
        entries = rp.tree(rel)
        readme = None
        for e in entries:
            if e["type"] == "file" and e["name"].lower() in ("readme.md", "readme", "readme.rst"):
                readme = rp.read(e["path"])
        last = rp.log(path=rel or None, limit=1)
        return render_template("git/browse.html", page_title=title, path=rel, entries=entries, readme=readme,
                               last_commit=last[0] if last else None, **common_ctx(rp))

    @bp.route("/edit")
    @login_required
    @guard
    def edit():
        rp = r()
        path = request.args.get("path", "").strip("/")
        new = request.args.get("new") == "1"
        content = ""
        if not new:
            content = rp.read(path)
            if content is None:
                flash("Binary files cannot be edited in the browser.", "warning")
                return redirect(url_for(f"{kind}.browse", path=os.path.dirname(path)))
        sls = None
        if kind == "states" and path.endswith(".sls") and not path.startswith("_"):
            sls = path[:-4].replace("/", ".")
            if sls.endswith(".init"):
                sls = sls[:-5]
        history = [] if new else rp.log(path=path, limit=15)
        return render_template("git/edit.html", page_title=title, path=path, content=content, new=new, sls=sls,
                               history=history, minions=keys().get("minions", []), target_types=TARGET_TYPES,
                               **common_ctx(rp))

    @bp.route("/changes")
    @login_required
    @guard
    def changes():
        rp = r()
        return render_template("git/changes.html", page_title=f"{title} - changes", diff=rp.diff(),
                               auto_deploy=current_app.config["GIT_AUTO_DEPLOY"], **common_ctx(rp))

    @bp.route("/history")
    @login_required
    @guard
    def history():
        rp = r()
        page = max(int(request.args.get("page", 1) or 1), 1)
        path = request.args.get("path") or None
        commits = rp.log(path=path, limit=30, skip=(page - 1) * 30)
        return render_template("git/history.html", page_title=f"{title} - history", commits=commits, page=page,
                               path=path, **common_ctx(rp))

    @bp.route("/commit/<sha>")
    @login_required
    @guard
    def commit(sha):
        rp = r()
        return render_template("git/commit.html", page_title=f"Commit {sha[:8]}", c=rp.show(sha), **common_ctx(rp))

    if kind == "states":
        @bp.route("/top")
        @login_required
        @guard
        def topfile():
            rp = r()
            raw, parsed, error = None, None, None
            try:
                raw = rp.read("top.sls")
                if raw is not None:
                    if JINJA_RE.search(raw):
                        error = "top.sls contains Jinja: the matrix below is not available, see the raw file."
                    else:
                        parsed = _parse_top(yaml.safe_load(raw) or {})
            except GitError:
                error = "No top.sls at the root of the repository."
            except yaml.YAMLError as exc:
                error = f"YAML error: {exc}"
            return render_template("git/top.html", page_title="Top file", raw=raw, parsed=parsed, error=error,
                                   sls_list=rp.sls_list(), **common_ctx(rp))

    # -- API -------------------------------------------------------------------
    @bp.route("/api/save", methods=["POST"])
    @admin_required
    @api_errors
    def api_save():
        rp = r()
        b = json_body()
        path = (b.get("path") or "").strip("/")
        content = b.get("content", "")
        warnings = _validate(path, content)
        rel = rp.write(path, content)
        audit(f"git.{kind}.save", rel)
        out = {"path": rel, "warnings": warnings}
        if b.get("commit"):
            name, email = _author()
            sha = rp.commit(b.get("message") or f"Update {rel}", name, email, paths=[rel])
            out["sha"] = sha
            audit(f"git.{kind}.commit", sha, {"message": b.get("message"), "paths": [rel]})
            out.update(_push_and_deploy(rp, kind, current_app.config["GIT_AUTO_DEPLOY"]))
        return jsonify(out)

    @bp.route("/api/validate", methods=["POST"])
    @login_required
    @api_errors
    def api_validate():
        b = json_body()
        return jsonify(warnings=_validate(b.get("path", ""), b.get("content", "")))

    @bp.route("/api/mkdir", methods=["POST"])
    @admin_required
    @api_errors
    def api_mkdir():
        rel = r().mkdir(json_body().get("path", ""))
        audit(f"git.{kind}.mkdir", rel)
        return jsonify(path=rel)

    @bp.route("/api/delete", methods=["POST"])
    @admin_required
    @api_errors
    def api_delete():
        path = json_body().get("path", "")
        r().delete(path)
        audit(f"git.{kind}.delete", path)
        return jsonify(ok=True)

    @bp.route("/api/rename", methods=["POST"])
    @admin_required
    @api_errors
    def api_rename():
        b = json_body()
        rel = r().rename(b.get("old", ""), b.get("new", ""))
        audit(f"git.{kind}.rename", rel, {"from": b.get("old")})
        return jsonify(path=rel)

    @bp.route("/api/commit", methods=["POST"])
    @admin_required
    @api_errors
    def api_commit():
        rp = r()
        b = json_body()
        name, email = _author()
        sha = rp.commit(b.get("message", ""), name, email, paths=b.get("paths") or None)
        audit(f"git.{kind}.commit", sha, {"message": b.get("message"), "paths": b.get("paths")})
        out = {"sha": sha}
        if b.get("push", True):
            out.update(_push_and_deploy(rp, kind, b.get("deploy", current_app.config["GIT_AUTO_DEPLOY"])))
        return jsonify(out)

    @bp.route("/api/discard", methods=["POST"])
    @admin_required
    @api_errors
    def api_discard():
        path = json_body().get("path")
        r().discard(path)
        audit(f"git.{kind}.discard", path or "*")
        return jsonify(ok=True)

    @bp.route("/api/diff")
    @login_required
    @api_errors
    def api_diff():
        return jsonify(diff=r().diff(request.args.get("path") or None))

    @bp.route("/api/pull", methods=["POST"])
    @admin_required
    @api_errors
    def api_pull():
        out = r().pull()
        audit(f"git.{kind}.pull", "")
        return jsonify(output=out)

    @bp.route("/api/push", methods=["POST"])
    @admin_required
    @api_errors
    def api_push():
        rp = r()
        rp.push()
        audit(f"git.{kind}.push", rp.current_branch())
        out = {"pushed": True}
        if current_app.config["GIT_AUTO_DEPLOY"]:
            out["deploy"] = _deploy(kind)
        return jsonify(out)

    @bp.route("/api/deploy", methods=["POST"])
    @admin_required
    @api_errors
    def api_deploy():
        out = _deploy(kind)
        audit(f"git.{kind}.deploy", "")
        return jsonify(out)

    @bp.route("/api/checkout", methods=["POST"])
    @admin_required
    @api_errors
    def api_checkout():
        branch = json_body().get("branch", "")
        r().checkout(branch)
        audit(f"git.{kind}.checkout", branch)
        return jsonify(ok=True)

    @bp.route("/api/branch", methods=["POST"])
    @admin_required
    @api_errors
    def api_branch():
        b = json_body()
        r().create_branch(b.get("name", ""), b.get("start") or None)
        audit(f"git.{kind}.branch", b.get("name"))
        return jsonify(ok=True)

    @bp.route("/api/revert", methods=["POST"])
    @admin_required
    @api_errors
    def api_revert():
        rp = r()
        name, email = _author()
        sha = rp.revert(json_body().get("sha", ""), name, email)
        audit(f"git.{kind}.revert", sha)
        out = {"sha": sha}
        out.update(_push_and_deploy(rp, kind, current_app.config["GIT_AUTO_DEPLOY"]))
        return jsonify(out)

    @bp.route("/api/file-at")
    @login_required
    @api_errors
    def api_file_at():
        return jsonify(content=r().file_at(request.args.get("sha", ""), request.args.get("path", "")))

    @bp.route("/api/grep")
    @login_required
    @api_errors
    def api_grep():
        q = request.args.get("q", "").strip()
        return jsonify(r().grep(q) if q else [])

    @bp.route("/api/sls")
    @login_required
    @api_errors
    def api_sls():
        return jsonify(r().sls_list())

    @bp.route("/api/render", methods=["POST"])
    @login_required
    @api_errors
    def api_render():
        """Render an SLS on a minion (state.show_sls) using the deployed branch as saltenv."""
        b = json_body()
        rp = r()
        saltenv = rp.current_branch()
        if saltenv == current_app.config["GIT_DEFAULT_BRANCH"]:
            saltenv = "base"
        ret = salt().local(b.get("minion"), "state.show_sls", arg=[b.get("sls")], kwarg={"saltenv": saltenv},
                           tgt_type="list", timeout=30)
        return jsonify(result=ret.get(b.get("minion")), saltenv=saltenv)

    return bp


def _validate(path, content):
    warnings = []
    ext = os.path.splitext(path)[1].lower()
    if ext not in TEXT_EXTENSIONS:
        warnings.append(f"Unusual file extension '{ext}'")
    if ext in (".sls", ".yml", ".yaml"):
        if JINJA_RE.search(content):
            warnings.append("File contains Jinja: YAML syntax is only checked after rendering (use 'Render on minion').")
        else:
            try:
                yaml.safe_load(content)
            except yaml.YAMLError as exc:
                raise ValueError(f"YAML syntax error: {exc}") from exc
    if "\t" in content and ext in (".sls", ".yml", ".yaml"):
        raise ValueError("Tabs are not allowed in YAML files")
    return warnings


def _parse_top(data):
    """Flatten a top file into rows {env, target, match, states}."""
    rows = []
    for env, targets in (data or {}).items():
        if not isinstance(targets, dict):
            continue
        for tgt, items in targets.items():
            match, states = "glob", []
            for item in items or []:
                if isinstance(item, dict):
                    if "match" in item:
                        match = item["match"]
                else:
                    states.append(str(item))
            rows.append({"env": env, "target": tgt, "match": match, "states": states})
    return rows


states_bp = make_blueprint("states")
pillar_bp = make_blueprint("pillar")
