"""Regression tests for the real launcher's lock state machine; no provider calls."""
import fcntl
from unittest.mock import Mock
from scripts import launch


def test_start_retries_lock_after_previous_backend_exits(monkeypatch):
    lock = Mock()
    attempt = Mock(side_effect=[BlockingIOError(), None])
    monkeypatch.setattr(launch.fcntl, 'flock', attempt)
    monkeypatch.setattr(launch, 'health', lambda: False)
    monkeypatch.setattr(launch.time, 'sleep', lambda seconds: None)
    assert launch.acquire_or_existing(lock)
    assert attempt.call_count == 2


def test_existing_healthy_instance_is_reused(monkeypatch):
    monkeypatch.setattr(launch.fcntl, 'flock', Mock(side_effect=BlockingIOError()))
    monkeypatch.setattr(launch, 'health', lambda: True)
    assert launch.acquire_or_existing(Mock()) is False


def test_stop_waits_for_lock_release_before_reporting_completion(monkeypatch, tmp_path):
    monkeypatch.setattr(launch, 'RUNTIME', tmp_path)
    calls = Mock(side_effect=[BlockingIOError(), None, None])
    monkeypatch.setattr(launch.fcntl, 'flock', calls)
    monkeypatch.setattr(launch.time, 'sleep', lambda seconds: None)
    launch.wait_for_shutdown()
    assert calls.call_count == 3
    assert calls.call_args.args[1] == fcntl.LOCK_UN
