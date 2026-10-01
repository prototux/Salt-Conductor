# Salt Conductor

Salt Conductor ("Conductor" for short) is a web console for [SaltStack](https://saltproject.io)
3008. It is a Flask application with a modern admin interface (dark side menu, light/dark
mode), built for an infrastructure where:

* **states** live in git and are served to the master by **gitfs** (each branch is a saltenv),
* **pillars** come from git (**git_pillar**), from **PostgreSQL** (the `conductor_db`
  ext_pillar shipped in this repository, with Salt targeting) and from **OpenBao/Vault**
  (`saltext-vault`),
* the job cache and the event bus are stored in **PostgreSQL** (`pgjsonb` returner),
* users log in with Salt **external_auth** (PAM, LDAP with group restriction, ...).

Want to try it? The [`demo/`](demo/) directory starts a complete lab (master, minions,
PostgreSQL, OpenBao, OpenLDAP) with one `docker compose` command.

## Features

| Area | What you can do |
|---|---|
| Dashboard | Minions up/down, pending keys, job activity, state compliance, OS and Salt versions, recent jobs and activity |
| Minions | Inventory from the grains cache, bulk actions (`state.apply`, `state.test`, refresh pillar/grains, `sync_all`...), nodegroups |
| Minion page | Grains (edit), pillar (masked, audited reveal), top file, last state run, packages, services, disks, network, processes, schedule, beacons, jobs |
| Keys | Accept / reject / delete with fingerprints |
| Run command | Every netapi client (local sync/async/batch, runner, wheel), all target types with match preview, autocomplete and docs, saved commands |
| Remote shell | Terminal-like `cmd.run_all` on any target, audited |
| Jobs | History with filters, live progress, state results with diffs, re-run, kill, running jobs |
| Schedules, Orchestration | Manage minion schedules; run `orch/*.sls` with `state.orchestrate` |
| States & pillar in git | Browser, editor (YAML + Jinja), validation, render on a minion, commit/push, diff, history, revert, branches, top-file matrix, deploy (`fileserver.update`, `git_pillar.update`) |
| Database pillar | Key/values with Salt targeting and priorities, history with restore, import/export |
| Secrets | OpenBao/Vault KV browser with versions, ACL policies, per-minion onboarding for the vault ext_pillar |
| Compliance | Last top-file run per minion, drift checks with `state.test`, remediation with `state.apply` |
| Event bus, Grains, Audit, System | Live and stored events, grain distribution, audit trail of every action, health and maintenance |

## Production deployment

The image is published to `ghcr.io/prototux/salt-conductor` by GitHub Actions (`latest` for
the `main` branch, `X.Y.Z` / `X.Y` for `vX.Y.Z` tags, plus the short commit sha).

```sh
cp .env.example .env      # configure salt-api, PostgreSQL, OpenBao and the git remotes
docker compose up -d
```

The container listens on port 8080 and runs as an unprivileged user (uid 10001) with a
read-only root filesystem. Persistent data (git working copies, the generated session key)
lives in the `/data` volume. Put a TLS reverse proxy in front of it and set
`CONDUCTOR_PROXY_FIX=1` and `CONDUCTOR_COOKIE_SECURE=true`. `/healthz` is an
unauthenticated health endpoint used by the image's `HEALTHCHECK`.

For SSH git remotes, mount a deploy key and `known_hosts` into `/home/conductor/.ssh`
(see `docker-compose.yml`).

### Salt master requirements

[`salt/master.d/salt-conductor.conf.example`](salt/master.d/salt-conductor.conf.example) lists
the master settings Salt Conductor relies on:

* `salt-api` (rest_cherrypy) with `netapi_enable_clients`,
* `master_job_cache: pgjsonb` and `event_return: pgjsonb` pointing at the same PostgreSQL
  database as Salt Conductor (`salt-pip install psycopg2-binary`); the tables are created by
  [`salt_conductor/schema.sql`](salt_conductor/schema.sql), applied at startup,
* the database pillar: copy
  [`salt/extension_modules/pillar/conductor_db.py`](salt/extension_modules/pillar/conductor_db.py)
  into `<extension_modules>/pillar/` and add the `conductor_db` ext_pillar,
* optionally `saltext-vault` for OpenBao, with the `saltstack/minions` and
  `saltstack/<minion>` policies (Salt Conductor can create them).

### LDAP login restricted to a group

Salt's `ldap` eauth does the work (see `demo/salt/master.d/50-ldap.conf`):

* `auth.ldap.filter` is the user search filter; adding
  `(memberOf=cn=salt-users,ou=groups,dc=example,dc=org)` refuses everybody outside that
  group, even with a valid password (the directory needs the `memberOf` overlay; Active
  Directory has it natively),
* rights come from group ACLs in `external_auth: ldap:` (`salt-admins%`, `salt-operators%`),
  resolved with `auth.ldap.group_basedn` / `auth.ldap.group_filter`,
* the master needs `python-ldap` in the Salt onedir (`salt-pip install python-ldap`, which
  compiles against `libldap` / `libsasl2` headers).

### Security model

* Salt Conductor keeps the user's Salt eauth token server-side (PostgreSQL session table);
  the browser only gets a signed session cookie. Everything that goes through salt-api is
  limited by the user's `external_auth` ACL.
* Git pushes, the database pillar and OpenBao use Salt Conductor's own credentials, so writing
  to them (and revealing secrets) requires a **Salt administrator**: an ACL containing `.*`,
  `@wheel` and `@runner`. Other users get read-only views and only see their own audit entries.
* CSRF protection on every state-changing request, audit trail in PostgreSQL.

### Terminology

Salt Conductor uses the current Salt entry points: `state.apply` (without SLS it applies the
top file, which the Salt documentation still calls a *highstate*) and `state.test` (Salt 3001+,
`state.apply test=True`). Runs made with `state.highstate` are still recognised.

## Configuration

All settings are environment variables (see [`.env.example`](.env.example)).

| Variable | Default | Purpose |
|---|---|---|
| `CONDUCTOR_SECRET_KEY` | generated in `/data` | Session signing key |
| `CONDUCTOR_DATA_DIR` | `/data` (image) | Persistent data |
| `CONDUCTOR_SALT_API_URL` | `http://localhost:8000` | salt-api (rest_cherrypy) |
| `CONDUCTOR_SALT_API_VERIFY_SSL` | `true` | TLS verification |
| `CONDUCTOR_EAUTH_BACKENDS` | `pam,ldap,file,sharedsecret` | Backends offered on the login page |
| `CONDUCTOR_DEFAULT_EAUTH` | `pam` | Preselected backend |
| `CONDUCTOR_DATABASE_URL` | `postgresql://salt:salt@localhost:15432/salt` | PostgreSQL shared with the master |
| `CONDUCTOR_OPENBAO_URL`, `CONDUCTOR_OPENBAO_TOKEN` | | OpenBao/Vault (no token = Secrets pages disabled) |
| `CONDUCTOR_OPENBAO_SALT_MOUNT`, `CONDUCTOR_OPENBAO_SALT_PREFIX` | `secret`, `salt` | KV path read by the vault ext_pillar |
| `CONDUCTOR_STATES_REPO`, `CONDUCTOR_PILLAR_REPO` | | Git remotes |
| `CONDUCTOR_REPO_DIR` | `$CONDUCTOR_DATA_DIR/repos` | Git working copies |
| `CONDUCTOR_GIT_DEFAULT_BRANCH` | `main` | Branch mapped to the `base` saltenv |
| `CONDUCTOR_GIT_AUTO_DEPLOY` | `true` | Update gitfs / git_pillar right after a push |
| `CONDUCTOR_PROXY_FIX` | `0` | Number of trusted reverse proxies |
| `CONDUCTOR_COOKIE_SECURE` | `false` | Send the session cookie over HTTPS only |
| `CONDUCTOR_SITE_NAME`, `CONDUCTOR_SHORT_NAME` | `Salt Conductor`, `Conductor` | Branding |
| `CONDUCTOR_WORKERS`, `CONDUCTOR_THREADS` | `2`, `16` | Gunicorn workers / threads per worker |
| `CONDUCTOR_LOG_LEVEL`, `CONDUCTOR_ACCESS_LOG` | `info`, `true` | Logging |

## Development

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
(cd demo && docker compose up -d postgres openbao openldap salt-master web01 web02 db01 new01)
CONDUCTOR_STATES_REPO=file://$PWD/demo/data/git/states.git \
CONDUCTOR_PILLAR_REPO=file://$PWD/demo/data/git/pillar.git \
CONDUCTOR_OPENBAO_TOKEN=root \
  .venv/bin/flask --app 'salt_conductor:create_app()' run --debug
```

Layout: `salt_conductor/` is the application (`views/` blueprints, `templates/`, `static/`),
`salt/` holds the master-side integration, `demo/` the lab.

## License

Apache License 2.0, see [LICENSE](LICENSE).
