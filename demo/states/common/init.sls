# Baseline for every minion
common-packages:
  pkg.installed:
    - pkgs:
      - curl
      - jq
      - tree
      - htop

/etc/motd:
  file.managed:
    - contents: |
        {{ salt['pillar.get']('motd:banner', 'Managed by SaltStack') }}
        Host: {{ grains['id'] }} | Roles: {{ grains.get('roles', []) | join(', ') }} | DC: {{ grains.get('datacenter', '?') }}

/etc/salt-inventory.json:
  file.serialize:
    - serializer: json
    - dataset:
        id: {{ grains['id'] }}
        os: {{ grains['osfinger'] }}
        roles: {{ grains.get('roles', []) | json }}
        ntp_servers: {{ salt['pillar.get']('ntp:servers', []) | json }}
