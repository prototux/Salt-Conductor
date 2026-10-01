import os


def _env(name, default=None):
    return os.environ.get(f"CONDUCTOR_{name}", default)


def _bool(value):
    return str(value).lower() in ("1", "true", "yes", "on")


class Config:
    # Persistent data (git working copies, generated secret key)
    DATA_DIR = _env("DATA_DIR", os.path.join(os.getcwd(), "data"))

    # When unset, a key is generated once and stored in DATA_DIR (see create_app)
    SECRET_KEY = _env("SECRET_KEY")
    SESSION_COOKIE_NAME = "conductor_session"
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = _bool(_env("COOKIE_SECURE", "false"))

    # salt-api (rest_cherrypy)
    SALT_API_URL = _env("SALT_API_URL", "http://localhost:8000").rstrip("/")
    SALT_API_VERIFY_SSL = _bool(_env("SALT_API_VERIFY_SSL", "true"))
    SALT_EAUTH_BACKENDS = [e.strip() for e in _env("EAUTH_BACKENDS", "pam,ldap,file,sharedsecret").split(",") if e.strip()]
    SALT_DEFAULT_EAUTH = _env("DEFAULT_EAUTH", "pam")
    SALT_TIMEOUT = int(_env("SALT_TIMEOUT", "30"))
    SALT_MASTER_ID = _env("SALT_MASTER_ID", "salt-master_master")

    # PostgreSQL: job cache (pgjsonb), event history, DB pillar, Salt Conductor data
    DATABASE_URL = _env("DATABASE_URL", "postgresql://salt:salt@localhost:15432/salt")

    # OpenBao / Vault
    OPENBAO_URL = (_env("OPENBAO_URL", "http://localhost:8200") or "").rstrip("/")
    OPENBAO_TOKEN = _env("OPENBAO_TOKEN", "")
    OPENBAO_NAMESPACE = _env("OPENBAO_NAMESPACE", "")
    OPENBAO_SALT_MOUNT = _env("OPENBAO_SALT_MOUNT", "secret")
    OPENBAO_SALT_PREFIX = _env("OPENBAO_SALT_PREFIX", "salt")

    # Git repositories (states served by gitfs, pillar by git_pillar)
    REPO_DIR = _env("REPO_DIR", os.path.join(DATA_DIR, "repos"))
    STATES_REPO = _env("STATES_REPO", "")
    PILLAR_REPO = _env("PILLAR_REPO", "")
    GIT_DEFAULT_BRANCH = _env("GIT_DEFAULT_BRANCH", "main")
    GIT_AUTO_DEPLOY = _bool(_env("GIT_AUTO_DEPLOY", "true"))

    # UI
    SITE_NAME = _env("SITE_NAME", "Salt Conductor")
    SHORT_NAME = _env("SHORT_NAME", "Conductor")  # logo
    STATUS_CACHE_SECONDS = int(_env("STATUS_CACHE_SECONDS", "30"))

    # Number of reverse proxies in front of the app (X-Forwarded-For/-Proto/-Host); 0 = none
    PROXY_FIX = int(_env("PROXY_FIX", "0"))
    LOG_LEVEL = _env("LOG_LEVEL", "INFO").upper()
