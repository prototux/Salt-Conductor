"""Working-copy management for the states / pillar git repositories."""

import contextlib
import fcntl
import os
import re
import shutil
import subprocess

TEXT_EXTENSIONS = {
    ".sls", ".jinja", ".j2", ".yml", ".yaml", ".json", ".py", ".sh", ".conf", ".cfg",
    ".ini", ".txt", ".md", ".rst", ".toml", ".xml", ".html", ".css", ".js", ".pem",
    ".pub", ".service", ".tmpl", ".template", ".csv", "",
}


class GitError(Exception):
    pass


class GitRepo:
    def __init__(self, name, url, workdir, default_branch="main"):
        self.name = name
        self.url = url
        self.workdir = workdir
        self.default_branch = default_branch

    # -- plumbing ---------------------------------------------------------
    @property
    def configured(self):
        return bool(self.url)

    def _global_config(self):
        """Global git config trusting repositories owned by another uid (e.g. shared with the
        salt-master container). GIT_CONFIG_* variables would not reach the local receive-pack
        spawned by a push, GIT_CONFIG_GLOBAL does. The user's own ~/.gitconfig is included."""
        path = os.path.join(os.path.dirname(self.workdir) or ".", ".gitconfig-conductor")
        if not os.path.exists(path):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("[safe]\n\tdirectory = *\n[include]\n\tpath = ~/.gitconfig\n")
        return path

    def _git(self, *args, check=True, env=None, timeout=60, cwd=None):
        run_env = dict(
            os.environ,
            GIT_TERMINAL_PROMPT="0",
            LC_ALL="C",
            GIT_CONFIG_GLOBAL=self._global_config(),
        )
        if env:
            run_env.update(env)
        proc = subprocess.run(
            ["git", *args],
            cwd=cwd or self.workdir,
            capture_output=True,
            text=True,
            env=run_env,
            timeout=timeout,
            check=False,
        )
        if check and proc.returncode != 0:
            raise GitError((proc.stderr or proc.stdout).strip() or f"git {args[0]} failed")
        return proc.stdout

    @contextlib.contextmanager
    def lock(self):
        os.makedirs(os.path.dirname(self.workdir) or ".", exist_ok=True)
        with open(f"{self.workdir}.lock", "w", encoding="utf-8") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)

    def ensure(self):
        if not self.configured:
            raise GitError(f"No git remote configured for the {self.name} repository")
        if os.path.isdir(os.path.join(self.workdir, ".git")):
            return
        with self.lock():
            if os.path.isdir(os.path.join(self.workdir, ".git")):
                return
            os.makedirs(os.path.dirname(self.workdir), exist_ok=True)
            self._git("clone", self.url, self.workdir, cwd=os.path.dirname(self.workdir), timeout=300)
            self._git("config", "user.name", "Salt Conductor")
            self._git("config", "user.email", "conductor@localhost")

    def safe_path(self, path):
        path = (path or "").strip("/")
        full = os.path.realpath(os.path.join(self.workdir, path))
        root = os.path.realpath(self.workdir)
        if full != root and not full.startswith(root + os.sep):
            raise GitError("Invalid path")
        rel = os.path.relpath(full, root)
        if rel.split(os.sep)[0] == ".git":
            raise GitError("Access to .git is not allowed")
        return full, "" if rel == "." else rel

    # -- branches -----------------------------------------------------------
    def current_branch(self):
        return self._git("rev-parse", "--abbrev-ref", "HEAD").strip()

    def branches(self):
        out = self._git("for-each-ref", "--format=%(refname:short)", "refs/heads", "refs/remotes/origin")
        names = set()
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("origin/"):
                line = line[len("origin/"):]
            if line and line not in ("HEAD", "origin"):
                names.add(line)
        return sorted(names, key=lambda b: (b != self.default_branch, b))

    def checkout(self, branch):
        if not re.match(r"^[\w./-]+$", branch):
            raise GitError("Invalid branch name")
        with self.lock():
            local = self._git("branch", "--list", branch).strip()
            if local:
                self._git("checkout", branch)
            else:
                self._git("checkout", "-b", branch, "--track", f"origin/{branch}")

    def create_branch(self, name, start=None):
        if not re.match(r"^[A-Za-z0-9][\w.-]*$", name):
            raise GitError("Invalid branch name (letters, digits, '.', '_' and '-')")
        with self.lock():
            self._git("checkout", "-b", name, start or "HEAD")
            self._git("push", "-u", "origin", name, timeout=120)

    def fetch(self):
        with self.lock():
            self._git("fetch", "--prune", "origin", timeout=120)

    def pull(self):
        with self.lock():
            self._git("fetch", "--prune", "origin", timeout=120)
            branch = self.current_branch()
            if not self._git("ls-remote", "--heads", "origin", branch).strip():
                return "Branch has no upstream yet"
            dirty = bool(self.status())
            if dirty:
                self._git("stash", "push", "--include-untracked", "-m", "conductor-autostash")
            try:
                out = self._git("merge", "--ff-only", f"origin/{branch}")
            finally:
                if dirty:
                    self._git("stash", "pop", check=False)
            return out.strip()

    def ahead_behind(self):
        branch = self.current_branch()
        out = self._git("rev-list", "--left-right", "--count", f"{branch}...origin/{branch}", check=False).split()
        if len(out) == 2:
            return int(out[0]), int(out[1])
        return 0, 0

    # -- files ------------------------------------------------------------
    def tree(self, path=""):
        full, rel = self.safe_path(path)
        if not os.path.isdir(full):
            raise GitError("Not a directory")
        changed = {c["path"]: c["status"] for c in self.status()}
        entries = []
        for name in os.listdir(full):
            if name == ".git":
                continue
            p = os.path.join(full, name)
            relp = os.path.join(rel, name) if rel else name
            is_dir = os.path.isdir(p)
            status = changed.get(relp)
            if is_dir and not status:
                prefix = relp + "/"
                status = "M" if any(k.startswith(prefix) for k in changed) else None
            entries.append({
                "name": name,
                "path": relp,
                "type": "dir" if is_dir else "file",
                "size": 0 if is_dir else os.path.getsize(p),
                "status": status,
            })
        entries.sort(key=lambda e: (e["type"] != "dir", e["name"].lower()))
        return entries

    def walk_files(self, suffix=None):
        out = []
        for root, dirs, files in os.walk(self.workdir):
            dirs[:] = [d for d in dirs if d != ".git"]
            for f in files:
                rel = os.path.relpath(os.path.join(root, f), self.workdir)
                if suffix is None or rel.endswith(suffix):
                    out.append(rel)
        return sorted(out)

    def sls_list(self):
        """Return SLS names (dotted) present in the working copy."""
        names = []
        for rel in self.walk_files(".sls"):
            if rel.split(os.sep)[0].startswith("_"):
                continue
            name = rel[:-4].replace(os.sep, ".")
            if name.endswith(".init"):
                name = name[:-5]
            if name != "top":
                names.append(name)
        return sorted(set(names))

    def read(self, path):
        full, _ = self.safe_path(path)
        if not os.path.isfile(full):
            raise GitError("File not found")
        with open(full, "rb") as fh:
            data = fh.read()
        if b"\0" in data[:8000]:
            return None
        return data.decode("utf-8", errors="replace")

    def write(self, path, content):
        full, rel = self.safe_path(path)
        if not rel:
            raise GitError("Invalid file name")
        with self.lock():
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(content.replace("\r\n", "\n"))
        return rel

    def mkdir(self, path):
        full, rel = self.safe_path(path)
        os.makedirs(full, exist_ok=True)
        # git doesn't track empty dirs
        keep = os.path.join(full, ".gitkeep")
        if not os.listdir(full):
            open(keep, "w", encoding="utf-8").close()
        return rel

    def delete(self, path):
        full, rel = self.safe_path(path)
        if not rel:
            raise GitError("Refusing to delete the repository root")
        with self.lock():
            if os.path.isdir(full):
                shutil.rmtree(full)
            elif os.path.exists(full):
                os.remove(full)

    def rename(self, old, new):
        src, _ = self.safe_path(old)
        dst, rel = self.safe_path(new)
        if os.path.exists(dst):
            raise GitError("Destination already exists")
        with self.lock():
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            os.rename(src, dst)
        return rel

    # -- changes ----------------------------------------------------------
    def status(self):
        out = self._git("status", "--porcelain=v1", "-uall")
        changes = []
        for line in out.splitlines():
            if len(line) < 4:
                continue
            code, path = line[:2], line[3:]
            if " -> " in path:
                path = path.split(" -> ", 1)[1]
            path = path.strip('"')
            code = code.strip() or "M"
            status = {"??": "A", "A": "A", "D": "D", "R": "R"}.get(code, code[0])
            changes.append({"path": path, "status": status})
        return changes

    def diff(self, path=None):
        with self.lock():
            # intent-to-add so new files show in the diff
            self._git("add", "-N", "--", path or ".", check=False)
            args = ["diff", "--no-color", "HEAD"]
            if path:
                args += ["--", path]
            return self._git(*args, check=False)

    def discard(self, path=None):
        with self.lock():
            target = path or "."
            self._git("reset", "-q", "HEAD", "--", target, check=False)
            self._git("checkout", "--", target, check=False)
            self._git("clean", "-fdq", "--", target, check=False)

    def commit(self, message, author_name, author_email, paths=None):
        if not message.strip():
            raise GitError("A commit message is required")
        with self.lock():
            if paths:
                self._git("add", "-A", "--", *paths)
            else:
                self._git("add", "-A")
            if not self._git("diff", "--cached", "--name-only").strip():
                raise GitError("Nothing to commit")
            env = {
                "GIT_AUTHOR_NAME": author_name,
                "GIT_AUTHOR_EMAIL": author_email,
                "GIT_COMMITTER_NAME": author_name,
                "GIT_COMMITTER_EMAIL": author_email,
            }
            self._git("commit", "-q", "-m", message, env=env)
            return self._git("rev-parse", "HEAD").strip()

    def push(self):
        with self.lock():
            branch = self.current_branch()
            return self._git("push", "origin", f"{branch}:{branch}", timeout=120)

    # -- history ------------------------------------------------------------
    def log(self, path=None, limit=50, skip=0, branch=None):
        fmt = "%H%x1f%h%x1f%an%x1f%ae%x1f%at%x1f%s%x1e"
        args = ["log", f"--max-count={limit}", f"--skip={skip}", f"--format={fmt}", "--shortstat"]
        if branch:
            args.append(branch)
        if path:
            args += ["--", path]
        out = self._git(*args, check=False)
        commits = []
        for chunk in out.split("\x1e"):
            chunk = chunk.strip()
            if not chunk:
                continue
            head, _, stat = chunk.partition("\n")
            parts = head.split("\x1f")
            if len(parts) < 6:
                continue
            commits.append({
                "sha": parts[0], "short": parts[1], "author": parts[2], "email": parts[3],
                "time": int(parts[4]), "subject": parts[5], "stat": stat.strip(),
            })
        return commits

    def show(self, sha):
        if not re.match(r"^[0-9a-fA-F]{4,40}$", sha):
            raise GitError("Invalid commit id")
        meta = self._git("show", "-s", "--format=%H%x1f%an%x1f%ae%x1f%at%x1f%B", sha)
        parts = meta.split("\x1f", 4)
        diff = self._git("show", "--no-color", "--format=", "--stat", "-p", sha)
        return {
            "sha": parts[0], "author": parts[1], "email": parts[2], "time": int(parts[3]),
            "message": parts[4].strip(), "diff": diff,
        }

    def file_at(self, sha, path):
        if not re.match(r"^[0-9a-fA-F]{4,40}$", sha):
            raise GitError("Invalid commit id")
        _, rel = self.safe_path(path)
        return self._git("show", f"{sha}:{rel}")

    def revert(self, sha, author_name, author_email):
        if not re.match(r"^[0-9a-fA-F]{4,40}$", sha):
            raise GitError("Invalid commit id")
        env = {
            "GIT_AUTHOR_NAME": author_name, "GIT_AUTHOR_EMAIL": author_email,
            "GIT_COMMITTER_NAME": author_name, "GIT_COMMITTER_EMAIL": author_email,
        }
        with self.lock():
            self._git("revert", "--no-edit", sha, env=env)
            return self._git("rev-parse", "HEAD").strip()

    def grep(self, pattern, limit=200):
        out = self._git("grep", "-n", "-I", "-i", "--full-name", "-F", "-e", pattern, check=False)
        results = []
        for line in out.splitlines()[:limit]:
            path, _, rest = line.partition(":")
            lineno, _, text = rest.partition(":")
            results.append({"path": path, "line": lineno, "text": text.strip()[:200]})
        return results
