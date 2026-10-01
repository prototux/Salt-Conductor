{% set pg = salt['pillar.get']('postgresql', {}) %}
db-packages:
  pkg.installed:
    - pkgs:
      - sqlite3

/etc/myapp/database.conf:
  file.managed:
    - makedirs: True
    - mode: '0640'
    - contents: |
        # Managed by Salt
        role = {{ pg.get('replication', {}).get('role', 'standalone') }}
        max_connections = {{ pg.get('max_connections', 100) }}
        password = {{ salt['pillar.get']('secrets:postgres_password', 'CHANGEME') }}
