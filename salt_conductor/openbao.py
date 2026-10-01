"""Minimal OpenBao / HashiCorp Vault HTTP client (KV v1/v2 + ACL policies)."""

import requests


class OpenBaoError(Exception):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class OpenBao:
    def __init__(self, url, token, namespace=""):
        self.url = (url or "").rstrip("/")
        self.token = token
        self.http = requests.Session()
        self.http.headers["X-Vault-Token"] = token or ""
        if namespace:
            self.http.headers["X-Vault-Namespace"] = namespace
        self._mount_cache = None

    @property
    def configured(self):
        return bool(self.url and self.token)

    def _req(self, method, path, **kwargs):
        try:
            resp = self.http.request(method, f"{self.url}/v1/{path.lstrip('/')}", timeout=10, **kwargs)
        except requests.RequestException as exc:
            raise OpenBaoError(f"OpenBao unreachable: {exc}") from exc
        if resp.status_code == 404:
            return None
        if resp.status_code >= 400:
            try:
                errors = "; ".join(resp.json().get("errors", [])) or resp.text
            except ValueError:
                errors = resp.text
            raise OpenBaoError(errors or f"HTTP {resp.status_code}", resp.status_code)
        if resp.status_code == 204 or not resp.content:
            return {}
        return resp.json()

    # -- system -------------------------------------------------------------
    def health(self):
        try:
            resp = self.http.get(f"{self.url}/v1/sys/health", timeout=5,
                                 params={"standbyok": "true", "sealedcode": "200", "uninitcode": "200"})
            return resp.json()
        except (requests.RequestException, ValueError) as exc:
            return {"error": str(exc)}

    def token_info(self):
        ret = self._req("GET", "auth/token/lookup-self") or {}
        return ret.get("data", {})

    def mounts(self):
        ret = self._req("GET", "sys/mounts") or {}
        data = ret.get("data", ret)
        mounts = []
        for path, info in data.items():
            if not isinstance(info, dict) or "type" not in info:
                continue
            version = (info.get("options") or {}).get("version", "1")
            mounts.append({
                "path": path.rstrip("/"),
                "type": info["type"],
                "version": version,
                "description": info.get("description", ""),
            })
        return sorted(mounts, key=lambda m: m["path"])

    def kv_mounts(self):
        if self._mount_cache is None:
            self._mount_cache = {m["path"]: m for m in self.mounts() if m["type"] in ("kv", "generic")}
        return self._mount_cache

    def _is_v2(self, mount):
        return self.kv_mounts().get(mount, {}).get("version") == "2"

    # -- KV -----------------------------------------------------------------
    def list(self, mount, path=""):
        path = path.strip("/")
        api = f"{mount}/metadata/{path}" if self._is_v2(mount) else f"{mount}/{path}"
        ret = self._req("LIST", api)
        if not ret:
            return []
        return sorted(ret.get("data", {}).get("keys", []), key=lambda k: (not k.endswith("/"), k))

    def read(self, mount, path, version=None):
        path = path.strip("/")
        if self._is_v2(mount):
            params = {"version": version} if version else None
            ret = self._req("GET", f"{mount}/data/{path}", params=params)
            if not ret:
                return None
            return {"data": ret["data"].get("data") or {}, "metadata": ret["data"].get("metadata") or {}}
        ret = self._req("GET", f"{mount}/{path}")
        if not ret:
            return None
        return {"data": ret.get("data", {}), "metadata": {}}

    def metadata(self, mount, path):
        if not self._is_v2(mount):
            return None
        ret = self._req("GET", f"{mount}/metadata/{path.strip('/')}")
        return (ret or {}).get("data")

    def write(self, mount, path, data):
        path = path.strip("/")
        if self._is_v2(mount):
            return self._req("POST", f"{mount}/data/{path}", json={"data": data})
        return self._req("POST", f"{mount}/{path}", json=data)

    def delete(self, mount, path):
        path = path.strip("/")
        if self._is_v2(mount):
            return self._req("DELETE", f"{mount}/metadata/{path}")
        return self._req("DELETE", f"{mount}/{path}")

    def rollback(self, mount, path, version):
        old = self.read(mount, path, version=version)
        if old is None:
            raise OpenBaoError("Version not found")
        return self.write(mount, path, old["data"])

    # -- policies -----------------------------------------------------------
    def policies(self):
        ret = self._req("LIST", "sys/policies/acl") or {}
        return sorted(ret.get("data", {}).get("keys", []))

    def policy(self, name):
        ret = self._req("GET", f"sys/policies/acl/{name}")
        return (ret or {}).get("data", {}).get("policy")

    def write_policy(self, name, rules):
        return self._req("PUT", f"sys/policies/acl/{name}", json={"policy": rules})

    def delete_policy(self, name):
        return self._req("DELETE", f"sys/policies/acl/{name}")
