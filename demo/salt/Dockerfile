# Salt 3008 master/minion image for the Salt Conductor demo lab
FROM debian:bookworm-slim

ARG SALT_VERSION=3008

ENV DEBIAN_FRONTEND=noninteractive

# python-ldap (LDAP eauth) only ships as source: it is built with a temporary toolchain.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates curl gnupg git procps iproute2 less vim-tiny cron \
 && mkdir -p /etc/apt/keyrings \
 && curl -fsSL https://packages.broadcom.com/artifactory/api/security/keypair/SaltProjectKey/public \
      -o /etc/apt/keyrings/salt-archive-keyring.pgp \
 && curl -fsSL https://github.com/saltstack/salt-install-guide/releases/latest/download/salt.sources \
      -o /etc/apt/sources.list.d/salt.sources \
 && printf 'Package: salt-*\nPin: version %s.*\nPin-Priority: 1001\n' "${SALT_VERSION}" \
      > /etc/apt/preferences.d/salt-pin-1001 \
 && apt-get update \
 && apt-get install -y --no-install-recommends salt-master salt-minion salt-api \
 && salt-pip install psycopg2-binary GitPython saltext-vault \
 && apt-get install -y --no-install-recommends gcc libc6-dev libldap-dev libsasl2-dev \
 && apt-mark manual libldap-2.5-0 libsasl2-2 \
 && salt-pip install python-ldap \
 && apt-get purge -y --auto-remove gcc libc6-dev libldap-dev libsasl2-dev \
 && rm -rf /var/lib/apt/lists/*

# Local PAM accounts used for salt-api eauth (see master.d/10-api.conf)
RUN useradd -m -s /bin/bash saltadmin && echo 'saltadmin:saltadmin' | chpasswd \
 && useradd -m -s /bin/bash viewer && echo 'viewer:viewer' | chpasswd \
 && git config --system --add safe.directory '*'

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["master"]
