# Salt Conductor demo lab

A complete, disposable SaltStack 3008 environment to try Salt Conductor.

```sh
cd demo
docker compose up -d --build
# open http://localhost:8080
```

| Service | Role |
|---|---|
| `salt-master` | Salt 3008 master + salt-api (`127.0.0.1:8000`), gitfs, git_pillar, `conductor_db` and vault ext_pillars, PAM and LDAP eauth |
| `web01`, `web02`, `db01` | Minions, auto-accepted |
| `new01` | Minion whose key stays pending (to try the Keys page) |
| `postgres` | Job cache, event history, database pillar, Salt Conductor data |
| `openbao` | OpenBao in dev mode (root token `root`), seeded with demo secrets |
| `openldap` | Directory for the LDAP login (`127.0.0.1:3389`) |
| `conductor` | Salt Conductor, built from the repository root (`127.0.0.1:8080`) |

Every container has a `mem_limit`; the whole lab needs about 2 GB of RAM.

## Accounts

| Backend | User / password | Rights |
|---|---|---|
| pam | `saltadmin` / `saltadmin` | everything |
| pam | `viewer` / `viewer` | read-only functions |
| ldap | `alice` / `alice` | in `salt-users` + `salt-admins`: everything |
| ldap | `bob` / `bob` | in `salt-users` + `salt-operators`: states and read-only commands |
| ldap | `carol` / `carol` | valid account outside `salt-users`: login refused |

## What's inside

* `salt/`: the Salt image (Debian + Salt 3008 packages, `psycopg2`, `GitPython`,
  `saltext-vault`, `python-ldap`) and the master configuration (`master.d/`).
* `states/`, `pillar/`: initial content of the git repositories. On first start the master
  turns them into bare repositories in `data/git/` (branches `main` and `dev`), which gitfs,
  git_pillar and Salt Conductor all use.
* `postgres/90-seed.sql`: a few database pillar entries using different target types.
* `openbao/seed.sh`: secrets under `secret/salt/...` and the `saltstack/*` policies the vault
  ext_pillar needs.
* `openldap/50-demo.ldif`: users and groups for the LDAP login.

To start from scratch: `docker compose down -v && sudo rm -rf data` (the git repositories are created by the master as root).
