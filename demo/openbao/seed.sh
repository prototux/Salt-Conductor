#!/bin/sh
# Seeds OpenBao (dev mode) with secrets consumed by the Salt vault ext_pillar.
set -e
export BAO_ADDR="${BAO_ADDR:-http://openbao:8200}"
export BAO_TOKEN="${BAO_TOKEN:-root}"

until bao status >/dev/null 2>&1; do
    echo "waiting for openbao..."; sleep 1
done

if bao kv get -mount=secret salt/common >/dev/null 2>&1; then
    echo "openbao already seeded"; exit 0
fi

bao kv put -mount=secret salt/common \
    smtp_relay_password='s3cr3t-smtp' monitoring_api_key='mon-0f3a9c2e'
bao kv put -mount=secret salt/minions/web01 tls_key_passphrase='web01-passphrase'
bao kv put -mount=secret salt/minions/web02 tls_key_passphrase='web02-passphrase'
bao kv put -mount=secret salt/minions/db01 \
    postgres_password='db01-pg-password' replication_password='repl-9912'
bao kv put -mount=secret apps/billing api_token='bill-123' db_url='postgres://billing@db01/billing'

# saltext-vault compiles pillar with per-minion tokens carrying the
# "saltstack/minions" and "saltstack/<minion_id>" policies.
bao policy write saltstack/minions - <<POLICY
path "secret/data/salt/common"     { capabilities = ["read"] }
path "secret/metadata/salt/common" { capabilities = ["read", "list"] }
POLICY
for m in web01 web02 db01 new01 salt-master_master; do
bao policy write "saltstack/$m" - <<POLICY
path "secret/data/salt/minions/$m"     { capabilities = ["read"] }
path "secret/metadata/salt/minions/$m" { capabilities = ["read", "list"] }
POLICY
done
echo "openbao seeded"
