from __future__ import annotations

from switchboard.routing.circuit_breaker import BreakerState, CircuitBreaker


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def make(threshold: int = 3) -> tuple[CircuitBreaker, FakeClock]:
    clock = FakeClock()
    return CircuitBreaker(failure_threshold=threshold, reset_timeout_s=30.0, clock=clock), clock


def test_opens_after_threshold_consecutive_failures() -> None:
    breaker, _ = make()
    for _ in range(3):
        breaker.record_failure()
    assert breaker.state is BreakerState.OPEN
    assert breaker.allow() is False


def test_success_resets_failure_count() -> None:
    breaker, _ = make()
    breaker.record_failure()
    breaker.record_failure()
    breaker.record_success()
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.state is BreakerState.CLOSED


def test_half_open_after_timeout_admits_single_probe() -> None:
    breaker, clock = make()
    for _ in range(3):
        breaker.record_failure()
    clock.now += 30
    assert breaker.state is BreakerState.HALF_OPEN
    assert breaker.allow() is True
    assert breaker.allow() is False


def test_half_open_success_closes_and_failure_reopens() -> None:
    breaker, clock = make()
    for _ in range(3):
        breaker.record_failure()
    clock.now += 30
    assert breaker.allow()
    breaker.record_success()
    assert breaker.state is BreakerState.CLOSED

    for _ in range(3):
        breaker.record_failure()
    clock.now += 30
    assert breaker.allow()
    breaker.record_failure()
    assert breaker.state is BreakerState.OPEN
    assert breaker.allow() is False
