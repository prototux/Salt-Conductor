{% set nginx = salt['pillar.get']('nginx', {}) %}
nginx:
  pkg.installed:
    - name: nginx-light
  service.running:
    - name: nginx
    - enable: True
    - watch:
      - file: /etc/nginx/conf.d/salt.conf
      - file: /var/www/html/index.html

/etc/nginx/conf.d/salt.conf:
  file.managed:
    - source: salt://nginx/files/salt.conf.jinja
    - template: jinja
    - context:
        worker_processes: {{ nginx.get('worker_processes', 'auto') }}
        server_name: {{ nginx.get('server_name', grains['id']) }}
    - require:
      - pkg: nginx

/var/www/html/index.html:
  file.managed:
    - contents: |
        <h1>{{ grains['id'] }}</h1>
        <p>{{ nginx.get('welcome', 'Deployed by Salt') }}</p>
    - makedirs: True
    - require:
      - pkg: nginx
