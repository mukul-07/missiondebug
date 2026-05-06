from missiondebug_agent.rate_limiter import RateLimiter


def test_divisor_one_keeps_all():
    rl = RateLimiter()
    for _ in range(100):
        assert rl.should_keep("/t", 1)


def test_divisor_n_keeps_every_nth():
    rl = RateLimiter()
    kept = [i for i in range(20) if rl.should_keep("/t", 5)]
    assert kept == [0, 5, 10, 15]


def test_topics_independent():
    rl = RateLimiter()
    # Interleave two topics with different divisors.
    a_keep = []
    b_keep = []
    for i in range(20):
        if rl.should_keep("/a", 2):
            a_keep.append(i)
        if rl.should_keep("/b", 4):
            b_keep.append(i)
    assert len(a_keep) == 10  # every 2nd
    assert len(b_keep) == 5   # every 4th


def test_invalid_divisor_treats_as_one():
    rl = RateLimiter()
    assert rl.should_keep("/t", 0) is True
    assert rl.should_keep("/t", -3) is True


def test_thread_safety():
    import threading

    rl = RateLimiter()
    counters = [0] * 8
    barrier = threading.Barrier(8)

    def worker(tid: int):
        barrier.wait()
        local_keep = 0
        for _ in range(1000):
            if rl.should_keep("/shared", 5):
                local_keep += 1
        counters[tid] = local_keep

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # 8 threads × 1000 messages, divisor 5 → exactly 1600 kept.
    assert sum(counters) == 1600
