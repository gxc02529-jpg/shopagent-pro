from shopagent.memory.redis_store import RedisMemoryStore


def test_redis_keys_cannot_collide_on_separator_characters():
    store = object.__new__(RedisMemoryStore)
    store._prefix = "shopagent-test"
    assert store._key("tenant:a", "session") != store._key("tenant_a", "session")
    assert store._key("tenant", "a:session") != store._key("tenant:a", "session")
