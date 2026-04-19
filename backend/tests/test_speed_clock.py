import threading

import pytest

from application.services.speed_clock import ClockStopped, SpeedClock


def test_speed_clock_wait_advances_fake_time():
    t = [0.0]
    sleeps: list[float] = []

    def mono():
        return t[0]

    def sleep(dt):
        sleeps.append(dt)
        t[0] += dt

    c = SpeedClock(bps=10.0, monotonic=mono, sleeper=sleep)
    c.wait_next()
    assert t[0] == pytest.approx(0.1)
    assert sum(sleeps) == pytest.approx(0.1)


def test_speed_clock_stop_raises():
    c = SpeedClock(bps=1000.0, monotonic=lambda: 0.0, sleeper=lambda _: None)
    c.stop()
    with pytest.raises(ClockStopped):
        c.wait_next()


def test_speed_clock_pause_blocks_until_resume():
    t = [0.0]

    def mono():
        return t[0]

    def sleep(dt):
        t[0] += dt

    c = SpeedClock(bps=2.0, monotonic=mono, sleeper=sleep)
    c.pause()
    done = threading.Event()

    def worker():
        try:
            c.wait_next()
        finally:
            done.set()

    th = threading.Thread(target=worker)
    th.start()
    assert not done.wait(timeout=0.15)
    c.resume()
    assert done.wait(timeout=1.0)
    th.join(timeout=1.0)


def test_speed_clock_change_speed():
    c = SpeedClock(bps=1.0, monotonic=lambda: 0.0, sleeper=lambda _: None)
    c.change_speed(2.0)
    assert c.interval_seconds == pytest.approx(0.5)
