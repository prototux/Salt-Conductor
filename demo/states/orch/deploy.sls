# salt-run state.orchestrate orch.deploy
databases:
  salt.state:
    - tgt: 'roles:db'
    - tgt_type: grain
    - sls: [common, db]

webservers:
  salt.state:
    - tgt: 'roles:web'
    - tgt_type: grain
    - sls: [common, nginx]
    - require:
      - salt: databases
