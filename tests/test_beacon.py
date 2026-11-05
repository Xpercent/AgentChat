import contextlib
import io
import unittest
from unittest.mock import patch

from agentchat import beacon
from agentchat.client import AgentChatError


class Clock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class WatchLifetimeTests(unittest.TestCase):
    def run_watch(self, clock, next_task):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            patch.object(beacon.hub, "is_alive", return_value=True),
            patch.object(beacon.client, "next_task", side_effect=next_task),
            patch.object(beacon.time, "monotonic", side_effect=clock.monotonic),
            patch.object(beacon.time, "sleep", side_effect=clock.advance),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            status = beacon.run()
        return status, stdout.getvalue(), stderr.getvalue()

    def assert_restart(self, status, stdout):
        self.assertEqual(status, 0)
        self.assertIn("python run.py watch", stdout)
        self.assertNotIn("reply --id", stdout)

    def test_idle_exits_after_nine_minutes_and_caps_network_timeout(self):
        clock = Clock()

        def idle(*, wait, timeout):
            self.assertEqual(wait, 540.0)
            self.assertEqual(timeout, 540.0)
            clock.advance(wait)
            return None

        status, stdout, stderr = self.run_watch(clock, idle)
        self.assert_restart(status, stdout)
        self.assertEqual(clock.now, 540.0)
        self.assertEqual(stderr, "")

    def test_early_empty_response_does_not_reset_deadline(self):
        clock = Clock()
        waits = []

        def idle(*, wait, timeout):
            waits.append(wait)
            self.assertEqual(timeout, wait)
            clock.advance(min(wait, 100.0))
            return None

        status, stdout, _ = self.run_watch(clock, idle)
        self.assert_restart(status, stdout)
        self.assertEqual(waits, [540.0, 440.0, 340.0, 240.0, 140.0, 40.0])
        self.assertEqual(clock.now, 540.0)

    def test_retry_backoff_cannot_extend_lifetime(self):
        clock = Clock()

        def failing(*, wait, timeout):
            clock.advance(539.5)
            raise AgentChatError("connection lost")

        status, stdout, _ = self.run_watch(clock, failing)
        self.assert_restart(status, stdout)
        self.assertEqual(clock.now, 540.0)

    def test_network_timeout_at_deadline_is_a_normal_restart(self):
        clock = Clock()

        def expired(*, wait, timeout):
            clock.advance(timeout)
            raise AgentChatError("request timed out")

        status, stdout, stderr = self.run_watch(clock, expired)
        self.assert_restart(status, stdout)
        self.assertEqual(stderr, "")

    def test_task_received_at_deadline_is_not_discarded(self):
        clock = Clock()

        def deliver(*, wait, timeout):
            clock.advance(wait)
            return {"id": 7, "text": "complete this task"}

        status, stdout, stderr = self.run_watch(clock, deliver)
        self.assertEqual(status, 0)
        self.assertIn("complete this task", stdout)
        self.assertIn("reply --id 7", stdout)
        self.assertIn("python run.py watch", stdout)
        self.assertEqual(stderr, "")

    def test_each_run_starts_a_new_lifetime(self):
        clock = Clock()

        def idle(*, wait, timeout):
            self.assertEqual(wait, 540.0)
            clock.advance(wait)
            return None

        for _ in range(2):
            status, stdout, _ = self.run_watch(clock, idle)
            self.assert_restart(status, stdout)
        self.assertEqual(clock.now, 1080.0)

    def test_repeated_network_failures_still_exit_with_error(self):
        clock = Clock()

        def failing(*, wait, timeout):
            raise AgentChatError("connection lost")

        status, stdout, stderr = self.run_watch(clock, failing)
        self.assertEqual(status, beacon.EXIT_NETWORK)
        self.assertEqual(stdout, "")
        self.assertIn("connection lost", stderr)


if __name__ == "__main__":
    unittest.main()
