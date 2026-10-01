{% for name, user in salt['pillar.get']('users', {}).items() %}
user-{{ name }}:
  user.present:
    - name: {{ name }}
    - fullname: {{ user.get('fullname', name) }}
    - shell: {{ user.get('shell', '/bin/bash') }}
    - home: /home/{{ name }}
    - createhome: True
{% if user.get('ssh_key') %}
ssh-key-{{ name }}:
  file.managed:
    - name: /home/{{ name }}/.ssh/authorized_keys
    - contents: {{ user['ssh_key'] | yaml_encode }}
    - user: {{ name }}
    - mode: '0600'
    - makedirs: True
    - require:
      - user: user-{{ name }}
{% endif %}
{% endfor %}
