"""Exercise sleep/wake boundaries without network calls or real services."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import remote_workers as workers


class StopLoop(Exception):
    pass


class SleepTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.registry = self.root / 'workers'
        self.registry.mkdir()
        (self.registry / 'sample.busy').write_text('1')
        (self.registry / 'sample.channel').write_text('channel')
        (self.registry / 'sample.json').write_text(json.dumps(
            {'slug': 'sample', 'channel': 'channel', 'state': 'active'}))
        self.slept = self.registry / 'sample.slept'
        self.config = {'root': str(self.root), 'user': 'harri',
                       'worker_memory_floor_mb': 1, 'worker_max_awake': 3,
                       'worker_idle_minutes': 3}
        self.old = {'created_at': 600, 'ids': ['old']}
        self.new = {'created_at': 1000, 'ids': ['old', 'new']}

    def run_cycles(self, snapshots, cycles=60, initial='active', stop_error=False):
        active = initial
        actions = []
        sleeps = 0
        original_read = Path.read_text

        def read(path, *args, **kwargs):
            if str(path) == '/proc/meminfo':
                return 'MemAvailable: 999999999 kB\n'
            return original_read(path, *args, **kwargs)

        def service(action, unit):
            nonlocal active
            actions.append(action)
            if action == 'stop' and stop_error:
                raise subprocess.CalledProcessError(1, ['systemctl', 'stop'])
            active = 'active' if action == 'start' else 'inactive'

        def sleep(_):
            nonlocal sleeps
            sleeps += 1
            if sleeps >= cycles:
                raise StopLoop()

        with patch.object(workers, 'newest', side_effect=snapshots), \
             patch.object(workers, 'fields', side_effect=lambda _: {
                 'ActiveState': active, 'CPUUsageNSec': '100'}), \
             patch.object(workers, 'service', side_effect=service), \
             patch.object(workers, 'mark') as mark, \
             patch.object(workers.time, 'sleep', side_effect=sleep), \
             patch.object(workers.time, 'time', return_value=1000.8), \
             patch.object(Path, 'read_text', new=read):
            with self.assertRaises(StopLoop):
                workers.supervise(self.config)
        return actions, mark.call_args_list

    def test_snapshot_retains_ids_and_timestamp_independent_of_order(self):
        rows = [{'id': 'b', 'created_at': 10}, {'id': 'a', 'created_at': 10}]
        with patch.object(workers, 'buzz', return_value=rows):
            self.assertEqual(workers.newest(self.config, 'channel'),
                             {'created_at': 10, 'ids': ['a', 'b']})

    def test_invalid_activity_response_cannot_look_like_quiet(self):
        for value in ({'error': 'unavailable'}, [{'id': 'a'}], [{'created_at': 10}], None):
            with self.subTest(value=value), patch.object(workers, 'buzz', return_value=value):
                with self.assertRaises(ValueError):
                    workers.newest(self.config, 'channel')

    def test_sleeping_worker_wakes_for_new_id_in_same_second(self):
        self.slept.write_text(json.dumps({'created_at': 1000, 'ids': ['old']}))
        actions, _ = self.run_cycles([self.new], cycles=1, initial='inactive')
        self.assertEqual(actions, ['start'])

    def test_retired_record_with_stale_busy_marker_is_never_woken(self):
        (self.registry / 'sample.json').write_text(json.dumps(
            {'slug': 'sample', 'channel': 'channel', 'state': 'retired'}))
        actions, _ = self.run_cycles([self.new], cycles=1, initial='inactive')
        self.assertEqual(actions, [])

    def test_retirement_completing_before_registry_lock_prevents_wake(self):
        def finish_retirement(_fd, operation):
            self.assertEqual(operation, workers.fcntl.LOCK_EX)
            (self.registry / 'sample.busy').unlink()
            (self.registry / 'sample.json').write_text(json.dumps(
                {'slug': 'sample', 'channel': 'channel', 'state': 'retired'}))
        with patch.object(workers.fcntl, 'flock', side_effect=finish_retirement) as lock:
            actions, _ = self.run_cycles([self.new], cycles=1, initial='inactive')
        lock.assert_called_once()
        self.assertEqual(actions, [])

    def test_unchanged_sleep_watermark_survives_supervisor_restart(self):
        self.slept.write_text(json.dumps(self.old))
        actions, _ = self.run_cycles([self.old], cycles=1, initial='inactive')
        self.assertEqual(actions, [])

    def test_legacy_fractional_sleep_time_includes_boundary_second(self):
        self.slept.write_text('1000.2\n')
        actions, _ = self.run_cycles([self.new], cycles=1, initial='inactive')
        self.assertEqual(actions, ['start'])

    def test_message_between_idle_check_and_stop_keeps_worker_running(self):
        actions, marks = self.run_cycles([self.old, self.new])
        self.assertEqual(actions, [])
        self.assertEqual(marks, [])
        self.assertFalse(self.slept.exists())

    def test_message_during_stop_immediately_wakes_worker(self):
        actions, _ = self.run_cycles([self.old, self.old, self.new])
        self.assertEqual(actions, ['stop', 'start'])
        self.assertEqual(json.loads(self.slept.read_text()), self.old)

    def test_same_second_message_after_post_stop_check_wakes_next_poll(self):
        # Event creation time is the same second as stop (1000.8), which the old
        # wall-clock .slept record would incorrectly classify as already consumed.
        actions, _ = self.run_cycles([self.old, self.old, self.old, self.new], cycles=61)
        self.assertEqual(actions, ['stop', 'start'])
        self.assertEqual(json.loads(self.slept.read_text()), self.old)

    def test_failed_stop_restores_previous_sleep_record(self):
        self.slept.write_text('500.2\n')
        actions, marks = self.run_cycles([self.old, self.old], stop_error=True)
        self.assertEqual(actions, ['stop'])
        self.assertEqual(marks, [])
        self.assertEqual(self.slept.read_text(), '500.2\n')

    def test_failed_first_stop_does_not_leave_sleep_record(self):
        actions, _ = self.run_cycles([self.old, self.old], stop_error=True)
        self.assertEqual(actions, ['stop'])
        self.assertFalse(self.slept.exists())

    def test_post_stop_query_failure_preserves_watermark_for_next_poll(self):
        actions, _ = self.run_cycles([self.old, self.old, RuntimeError('relay unavailable'),
                                     self.new], cycles=61)
        self.assertEqual(actions, ['stop', 'start'])
        self.assertEqual(json.loads(self.slept.read_text()), self.old)


if __name__ == '__main__':
    unittest.main()
