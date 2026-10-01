"""Client for the salt-api rest_cherrypy netapi."""

import json
import logging
from http.cookiejar import DefaultCookiePolicy

import requests

log = logging.getLogger(__name__)


class SaltAPIError(Exception):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class SaltAuthError(SaltAPIError):
    pass


class SaltAPI:
    def __init__(self, url, verify=True, timeout=30):
        self.url = url.rstrip("/")
        self.verify = verify
        self.timeout = timeout
        self.http = requests.Session()
        self.http.headers.update({"Accept": "application/json"})
        # This session is shared by every user. salt-api sets a session cookie on /login and
        # prefers it over X-Auth-Token, so a stored cookie would make all users act with the
        # token of whoever logged in last: never keep cookies, always send the user's token.
        self.http.cookies.set_policy(DefaultCookiePolicy(allowed_domains=[]))

    # -- plumbing ---------------------------------------------------------
    def _post(self, path, payload, token=None, timeout=None):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["X-Auth-Token"] = token
        try:
            resp = self.http.post(
                f"{self.url}{path}",
                data=json.dumps(payload),
                headers=headers,
                verify=self.verify,
                timeout=timeout or self.timeout,
            )
        except requests.RequestException as exc:
            raise SaltAPIError(f"salt-api unreachable: {exc}") from exc
        if resp.status_code == 401:
            raise SaltAuthError("Authentication failed or session expired", 401)
        if resp.status_code == 403:
            raise SaltAPIError("Permission denied by Salt external_auth", 403)
        if resp.status_code >= 400:
            raise SaltAPIError(f"salt-api error {resp.status_code}: {resp.text[:500]}", resp.status_code)
        try:
            return resp.json()
        except ValueError as exc:
            raise SaltAPIError(f"invalid salt-api response: {resp.text[:200]}") from exc

    def ping(self):
        try:
            resp = self.http.get(self.url + "/", verify=self.verify, timeout=5)
            return resp.status_code == 200
        except requests.RequestException:
            return False

    # -- auth -------------------------------------------------------------
    def login(self, username, password, eauth):
        data = self._post("/login", {"username": username, "password": password, "eauth": eauth})
        ret = data.get("return") or [{}]
        if not ret or "token" not in ret[0]:
            raise SaltAuthError("Invalid credentials")
        return ret[0]

    def logout(self, token):
        try:
            self._post("/logout", {}, token=token, timeout=5)
        except SaltAPIError:
            pass

    # -- lowstate ---------------------------------------------------------
    def lowstate(self, token, chunks, timeout=None):
        data = self._post("/", chunks, token=token, timeout=timeout)
        return data.get("return", [])

    def local(self, token, tgt, fun, arg=None, kwarg=None, tgt_type="glob",
              timeout=None, full_return=False, **extra):
        low = {"client": "local", "tgt": tgt, "fun": fun, "tgt_type": tgt_type}
        if arg:
            low["arg"] = arg
        if kwarg:
            low["kwarg"] = kwarg
        if timeout:
            low["timeout"] = timeout
        if full_return:
            low["full_return"] = True
        low.update(extra)
        http_timeout = (timeout or self.timeout) + 30
        ret = self.lowstate(token, [low], timeout=http_timeout)
        return ret[0] if ret else {}

    def local_async(self, token, tgt, fun, arg=None, kwarg=None, tgt_type="glob", **extra):
        low = {"client": "local_async", "tgt": tgt, "fun": fun, "tgt_type": tgt_type}
        if arg:
            low["arg"] = arg
        if kwarg:
            low["kwarg"] = kwarg
        low.update(extra)
        ret = self.lowstate(token, [low])
        return ret[0] if ret else {}

    def local_batch(self, token, tgt, fun, batch, arg=None, kwarg=None, tgt_type="glob", timeout=None):
        low = {"client": "local_batch", "tgt": tgt, "fun": fun, "tgt_type": tgt_type, "batch": str(batch)}
        if arg:
            low["arg"] = arg
        if kwarg:
            low["kwarg"] = kwarg
        ret = self.lowstate(token, [low], timeout=(timeout or self.timeout) * 4)
        merged = {}
        for chunk in ret:
            if isinstance(chunk, dict):
                merged.update(chunk)
        return merged

    def runner(self, token, fun, arg=None, _timeout=None, **kwarg):
        low = {"client": "runner", "fun": fun}
        if arg:
            low["arg"] = arg
        if kwarg:
            low["kwarg"] = kwarg
        ret = self.lowstate(token, [low], timeout=_timeout)
        return ret[0] if ret else None

    def runner_async(self, token, fun, arg=None, **kwarg):
        low = {"client": "runner_async", "fun": fun}
        if arg:
            low["arg"] = arg
        if kwarg:
            low["kwarg"] = kwarg
        ret = self.lowstate(token, [low])
        return ret[0] if ret else {}

    def wheel(self, token, fun, arg=None, **kwarg):
        low = {"client": "wheel", "fun": fun}
        if arg:
            low["arg"] = arg
        # the wheel client takes its keyword arguments at the top level of the lowstate
        low.update(kwarg)
        ret = self.lowstate(token, [low])
        data = (ret[0] if ret else {}).get("data", {})
        if not data.get("success", True):
            raise SaltAPIError(str(data.get("return")))
        return data.get("return")

    # -- event bus ---------------------------------------------------------
    def events(self, token):
        """Yield decoded events ({"tag":..., "data":...}) from /events."""
        resp = self.http.get(
            f"{self.url}/events",
            headers={"X-Auth-Token": token, "Accept": "text/event-stream"},
            stream=True,
            verify=self.verify,
            timeout=(5, 60),
        )
        if resp.status_code == 401:
            raise SaltAuthError("session expired", 401)
        resp.raise_for_status()
        try:
            for line in resp.iter_lines(decode_unicode=True):
                if line and line.startswith("data:"):
                    try:
                        yield json.loads(line[5:].strip())
                    except ValueError:
                        continue
        finally:
            resp.close()
