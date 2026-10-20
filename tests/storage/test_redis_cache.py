"""Cache loss, expiry and recovery use a protocol implementation, not business mocks."""

import json
import time

import fakeredis
import pytest
import redis
from tianshu_storage import RedisCache, StorageOptions, StorageRuntime


@pytest.fixture
def cache_client(monkeypatch):
    client = fakeredis.FakeRedis()
    monkeypatch.setattr(redis.Redis, "from_url", lambda *args, **kwargs: client)
    return client


def test_cache_expiry_is_not_sliding_and_identity_values_are_not_in_keys(cache_client):
    cache = RedisCache("redis://localhost/0", prefix="tianshu:test:epoch:platform")
    assert cache.enabled
    assert cache.set(
        "weather", {"private_location": "exact coordinates"}, {"celsius": 20}, ttl=0.15
    )
    assert cache.get("weather", {"private_location": "exact coordinates"}) == {
        "celsius": 20
    }
    keys = cache_client.keys()
    assert len(keys) == 1 and b"exact coordinates" not in keys[0]
    before = cache_client.pttl(keys[0])
    time.sleep(0.04)
    assert cache.get("weather", {"private_location": "exact coordinates"}) == {
        "celsius": 20
    }
    assert cache_client.pttl(keys[0]) < before
    time.sleep(0.13)
    assert cache.get("weather", {"private_location": "exact coordinates"}) is None
    cache.close()


def test_cache_failure_is_a_miss_and_recovers_without_mutating_sql_state(
    cache_client, monkeypatch, tmp_path
):
    cache = RedisCache("redis://localhost/0", prefix="tianshu:test:epoch:platform")
    original = cache_client.get

    def disconnected(*args, **kwargs):
        raise redis.ConnectionError("synthetic disconnect")

    monkeypatch.setattr(cache_client, "get", disconnected)
    assert cache.get("music", "id") is None
    assert cache.status() == "unavailable"
    assert not list(tmp_path.iterdir())
    monkeypatch.setattr(cache_client, "get", original)
    assert cache.probe()
    assert cache.set("music", "id", {"title": "恢复"}, ttl=30)
    assert cache.get("music", "id") == {"title": "恢复"}
    assert cache.delete("music", "id")
    assert cache.get("music", "id") is None
    cache.close()


def test_cache_corruption_and_oversize_never_become_facts(cache_client):
    cache = RedisCache("redis://localhost/0", prefix="tianshu:test:epoch:platform")
    cache.set("summary", "id", {"count": 1}, ttl=10)
    key = cache_client.keys()[0]
    cache_client.set(key, b"malformed JSON")
    assert cache.get("summary", "id") is None
    cache_client.set(
        key, json.dumps({"version": 1, "expires_at": time.time() - 1, "value": "old"})
    )
    assert cache.get("summary", "id") is None
    assert not cache.set("summary", "id", "x" * (cache.MAX_ENTRY_BYTES + 1), ttl=10)
    assert not cache.set("summary", "id", {"bad": float("nan")}, ttl=10)
    cache.close()


def test_sqlite_with_redis_and_without_redis_are_independent(cache_client, tmp_path):
    options = StorageOptions(redis_url="redis://localhost/0")
    with StorageRuntime(
        options, service="platform", state_directory=tmp_path
    ) as manager:
        assert manager.backend == "sqlite" and manager.cache.enabled
        database = manager.database("core", ":memory:")
        connection = database.connect()
        connection.execute("CREATE TABLE facts(id TEXT PRIMARY KEY)")
        connection.execute("INSERT INTO facts VALUES('durable')")
        connection.commit()
        manager.cache.set("summary", "facts", 1, ttl=30)
        cache_client.flushall()
        assert connection.execute("SELECT id FROM facts").fetchone() == ("durable",)
        assert manager.cache.get("summary", "facts") is None
        connection.close()


def test_generation_and_service_prefix_isolate_shared_redis(cache_client):
    old = RedisCache("redis://localhost/0", prefix="tianshu:one:old:platform")
    new = RedisCache("redis://localhost/0", prefix="tianshu:one:new:platform")
    other = RedisCache("redis://localhost/0", prefix="tianshu:one:old:memory")
    old.set("summary", "same", {"count": 2}, ttl=30)
    assert new.get("summary", "same") is None
    assert other.get("summary", "same") is None
