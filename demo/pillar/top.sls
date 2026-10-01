base:
  '*':
    - common
  'roles:web':
    - match: grain
    - web
  'db*':
    - db
