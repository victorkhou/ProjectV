"""
Unit tests for the MetricsCollector module.

Locks in the fix for the broken ``log_summary`` (it previously called the
non-existent ``logger.log_info`` and so never reset its per-interval buffer)
and the hardened ``set_gauge`` (must not overwrite control attributes).

Validates: Requirements 30.1, 30.2, 30.3, 30.4
"""

import unittest

from mygame.world.metrics import MetricsCollector


class TestMetricsCollector(unittest.TestCase):
    def test_log_summary_does_not_raise_and_resets_buffer(self):
        """log_summary must emit via the stdlib logger and clear durations."""
        m = MetricsCollector(enabled=True, log_interval=60)
        m.record_tick(5.0)
        m.record_tick(7.0)
        self.assertEqual(len(m._tick_durations), 2)

        # Previously raised AttributeError (logger.log_info); must not now.
        m.log_summary()

        # Per-interval buffer is always reset, even though logging happened.
        self.assertEqual(m._tick_durations, [])

    def test_log_summary_resets_buffer_even_if_logging_fails(self):
        """The reset runs in a finally block, so a logging error cannot leak."""
        m = MetricsCollector(enabled=True, log_interval=60)
        m.record_tick(5.0)

        # Force the log call to blow up and confirm the buffer still clears.
        import mygame.world.metrics as metrics_mod

        original = metrics_mod.logger.info

        def _boom(*_a, **_kw):
            raise RuntimeError("logging backend down")

        metrics_mod.logger.info = _boom
        try:
            with self.assertRaises(RuntimeError):
                m.log_summary()
        finally:
            metrics_mod.logger.info = original

        self.assertEqual(m._tick_durations, [])

    def test_record_tick_no_unbounded_growth_when_interval_elapses(self):
        """With a zero interval, every record_tick flushes the buffer."""
        m = MetricsCollector(enabled=True, log_interval=0)
        for i in range(10):
            m.record_tick(float(i))
        # Each record_tick triggers _maybe_log_summary -> log_summary, which
        # clears the buffer, so it never accumulates.
        self.assertEqual(m._tick_durations, [])

    def test_set_gauge_sets_known_gauge(self):
        m = MetricsCollector(enabled=True)
        m.set_gauge("connected_players", 7)
        self.assertEqual(m.connected_players, 7)

    def test_set_gauge_ignores_unknown_and_control_attributes(self):
        """set_gauge must not be able to flip control attributes."""
        m = MetricsCollector(enabled=True, log_interval=60)
        m.set_gauge("enabled", 0)
        m.set_gauge("log_interval", 1)
        m.set_gauge("errors", 999)  # a counter, not a gauge
        self.assertIs(m.enabled, True)
        self.assertEqual(m.log_interval, 60)
        self.assertEqual(m.errors, 0)

    def test_disabled_collector_records_nothing(self):
        m = MetricsCollector(enabled=False)
        m.record_tick(5.0)
        m.increment("commands_processed")
        m.set_gauge("connected_players", 3)
        self.assertEqual(m._tick_durations, [])
        self.assertEqual(m.commands_processed, 0)
        self.assertEqual(m.connected_players, 0)


if __name__ == "__main__":
    unittest.main()
