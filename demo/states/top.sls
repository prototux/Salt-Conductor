base:
  '*':
    - common
    - users
  'roles:web':
    - match: grain
    - nginx
  'roles:db':
    - match: grain
    - db
