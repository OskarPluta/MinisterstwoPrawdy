from rag.ratelimit import RateLimiter


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_per_client_minute_and_hour_windows():
    clock = Clock()
    rl = RateLimiter(per_minute=2, per_hour=3, per_day_total=0, clock=clock)
    assert rl.hit("a") is None and rl.hit("a") is None
    refused = rl.hit("a")
    assert refused and refused.scope == "client" and 1 <= refused.retry_after <= 61
    assert "zbyt wiele" in refused.message
    assert rl.hit("b") is None, "other clients are counted separately"
    clock.t += 61
    assert rl.hit("a") is None, "minute window slid"
    clock.t += 1
    refused = rl.hit("a")
    assert refused and refused.retry_after > 60, "hour limit (3) reached"
    clock.t += 3600
    assert rl.hit("a") is None


def test_refused_requests_are_not_counted():
    clock = Clock()
    rl = RateLimiter(per_minute=1, per_hour=0, per_day_total=0, clock=clock)
    assert rl.hit("a") is None
    for _ in range(5):
        assert rl.hit("a") is not None
    clock.t += 60
    assert rl.hit("a") is None, "waiting one window is enough"


def test_global_daily_cap():
    clock = Clock()
    rl = RateLimiter(per_minute=0, per_hour=0, per_day_total=3, clock=clock)
    assert all(rl.hit(f"c{i}") is None for i in range(3))
    refused = rl.hit("new")
    assert refused and refused.scope == "global" and "Dzienny limit" in refused.message
    clock.t += 86400
    assert rl.hit("new") is None


def test_from_env_and_zero_disables(monkeypatch):
    monkeypatch.setenv("PRAWDOWID_RATE_PER_MINUTE", "0")
    monkeypatch.setenv("PRAWDOWID_RATE_PER_HOUR", "0")
    monkeypatch.setenv("PRAWDOWID_DAILY_CHECKS", "zle")
    rl = RateLimiter.from_env()
    assert rl.windows == [] and rl.per_day_total == 1000
    assert all(rl.hit("a") is None for _ in range(50))
