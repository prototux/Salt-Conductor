-- Demo data for the database pillar (only inserted on a fresh database).
INSERT INTO conductor_pillar (target, tgt_type, key, value, priority, description, updated_by)
SELECT * FROM (VALUES
    ('*', 'glob', 'motd:banner', '"This host is managed by SaltStack - local changes will be overwritten"'::jsonb, 10, 'Login banner for every minion', 'seed'),
    ('*', 'glob', 'ntp:servers', '["0.pool.ntp.org", "1.pool.ntp.org"]'::jsonb, 10, 'Default NTP servers', 'seed'),
    ('G@roles:web', 'compound', 'nginx:worker_processes', '4'::jsonb, 50, 'Tuning for web nodes', 'seed'),
    ('webservers', 'nodegroup', 'nginx:server_name', '"www.example.com"'::jsonb, 50, 'Public vhost', 'seed'),
    ('db*', 'glob', 'postgresql:max_connections', '200'::jsonb, 50, 'DB tuning', 'seed'),
    ('db01', 'list', 'postgresql:replication:role', '"primary"'::jsonb, 100, 'Primary node', 'seed')
) AS v
WHERE NOT EXISTS (SELECT 1 FROM conductor_pillar);
