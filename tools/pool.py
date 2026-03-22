#tools/pool.py
"""Singleton DB pool shared across all tool modules."""
_pool = None


def init_tool_db(pool):
    global _pool
    _pool = pool


def get_pool():
    return _pool
