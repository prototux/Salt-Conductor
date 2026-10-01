#!/bin/bash
set -euo pipefail

seed_repo() {
    local name="$1" src="$2" bare="/srv/git/$1.git"
    if [ -d "$bare" ] || [ ! -d "$src" ]; then
        return
    fi
    echo "[entrypoint] seeding git repository $bare from $src"
    git init -q --bare --shared=all -b main "$bare"
    local tmp
    tmp="$(mktemp -d)"
    cp -r "$src/." "$tmp/"
    git -C "$tmp" init -q -b main
    git -C "$tmp" add -A
    git -C "$tmp" -c user.name="Salt Conductor demo" -c user.email="demo@salt-conductor.local" \
        commit -q -m "Initial import of $name"
    git -C "$tmp" push -q "$bare" main
    # second environment branch, so gitfs exposes a "dev" saltenv
    git -C "$tmp" push -q "$bare" main:dev
    rm -rf "$tmp"
    chmod -R a+rwX "$bare"
}

case "${1:-master}" in
    master)
        mkdir -p /srv/git /etc/salt/pki/master
        seed_repo states /seed/states
        seed_repo pillar /seed/pillar
        # Minions matching these names get their key accepted automatically;
        # anything else waits in "pending" so the Keys page has something to do.
        printf 'web*\ndb*\n' > /etc/salt/autosign.conf
        salt-api -d --log-file-level=info
        exec salt-master -l "${SALT_LOG_LEVEL:-info}"
        ;;
    minion)
        mkdir -p /etc/salt/minion.d
        {
            echo "master: ${SALT_MASTER:-salt-master}"
            echo "id: ${MINION_ID:-$(hostname)}"
            # notice a restarted/recreated master (new container IP) and reconnect
            echo "master_alive_interval: 30"
            echo "master_tries: -1"
            echo "auth_tries: 10"
            echo "grains:"
            echo "  roles: [${MINION_ROLES:-base}]"
            echo "  datacenter: ${MINION_DC:-dc1}"
            echo "  environment: ${MINION_ENV:-production}"
        } > /etc/salt/minion.d/demo.conf
        service cron start >/dev/null 2>&1 || true
        exec salt-minion -l "${SALT_LOG_LEVEL:-info}"
        ;;
    *)
        exec "$@"
        ;;
esac
