"""
Small custom execution module synced with ``saltutil.sync_modules``.
"""


def summary():
    """
    Return a short inventory summary of the minion.

    CLI Example:

    .. code-block:: bash

        salt '*' inventory.summary
    """
    return {
        "id": __grains__["id"],
        "os": __grains__.get("osfinger"),
        "cpus": __grains__.get("num_cpus"),
        "mem_mb": __grains__.get("mem_total"),
        "roles": __grains__.get("roles", []),
        "ipv4": [ip for ip in __grains__.get("ipv4", []) if ip != "127.0.0.1"],
    }
