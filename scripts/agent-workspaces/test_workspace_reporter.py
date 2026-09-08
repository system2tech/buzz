"""Lifecycle regression tests; every process and relay call is simulated.

Run with: python3 -m unittest discover -s scripts/agent-workspaces -v
"""

import contextlib
import datetime as dt
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


SPEC = importlib.util.spec_from_file_location(
    "workspace_reporter", Path(__file__).with_name("workspace_reporter.py")
)
reporter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reporter)

MANAGER_KEY = "a" * 64
WORKER_KEY = "b" * 64
SESSION = "12345678-1234-1234-1234-123456789abc"
NOW = 1_783_333_000.0


def completed(stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


class TemporaryRoot(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "workers").mkdir()
        self.args = SimpleNamespace(
            root=str(self.root), manager_cwd=str(self.root / "manager"),
            manager_channel="manager-channel", manager_pubkey=MANAGER_KEY,
            location="local", buzz="/fixture/buzz", dry_run=False,
        )
        # A new unmocked subprocess is a test failure, never a live CLI call.
        self.spawn = patch.object(
            reporter.subprocess, "run", side_effect=AssertionError("Unexpected live subprocess")
        )
        self.spawn.start()
        self.addCleanup(self.spawn.stop)

    def worker(self, slug="research", channel="task-channel", pubkey=WORKER_KEY):
        for extension, text in (("busy", "0"), ("channel", channel), ("pub", pubkey)):
            (self.root / "workers" / f"{slug}.{extension}").write_text(text + "\n")

    def sessions(self, values, wanted=SESSION):
        if wanted is not None:
            (self.root / ".session-id").write_text(wanted + "\n")
        run = Mock(return_value=completed(json.dumps(values)))
        return reporter.manager_state(self.root, Path(self.args.manager_cwd), run=run)

    def session(self, **overrides):
        return {
            "id": SESSION, "cwd": self.args.manager_cwd,
            "kind": "background", "status": "idle", **overrides,
        }


class ProcessStateTests(unittest.TestCase):
    def test_imported_commands_force_predictable_locale(self):
        with patch.dict(reporter.os.environ, {"LC_ALL": "fi_FI.UTF-8"}), \
             patch.object(reporter.subprocess, "run", return_value=completed("ok")) as run:
            reporter.command(["ps", "-o", "lstart=", "-p", "123"])
            self.assertEqual(run.call_args.kwargs["env"]["LC_ALL"], "C")

    def test_launchd_running_requires_pid(self):
        run = Mock(return_value=completed("state = running\n pid = 314\n"))
        self.assertEqual(reporter.process_state("research", "darwin", run), ("running", 314))
        self.assertIn("com.mrfix.worker.research", run.call_args.args[0][-1])

    def test_launchd_missing_service_differs_from_unreachable_service_manager(self):
        for result, expected in (
            (completed(stderr="Could not find service", returncode=113), "stopped"),
            (completed(stderr="Not privileged", returncode=1), "unknown"),
            (None, "unknown"),
        ):
            with self.subTest(expected=expected, result=result):
                run = Mock(return_value=result)
                self.assertEqual(reporter.process_state("research", "darwin", run), (expected, 0))

    def test_launchd_loaded_job_without_pid_is_never_awake(self):
        for state in ("not running", "waiting", "running"):
            with self.subTest(state=state):
                run = Mock(return_value=completed(f"state = {state}\n"))
                self.assertNotEqual(reporter.process_state("research", "darwin", run)[0], "running")

    def test_systemd_process_lifecycle(self):
        cases = (
            ("loaded", "active", "314", ("running", 314)),
            ("loaded", "activating", "314", ("starting", 314)),
            ("loaded", "failed", "0", ("failed", 0)),
            ("loaded", "inactive", "0", ("stopped", 0)),
            ("not-found", "inactive", "0", ("stopped", 0)),
        )
        for load, active, pid, expected in cases:
            with self.subTest(active=active, load=load):
                run = Mock(return_value=completed(
                    f"LoadState={load}\nActiveState={active}\nMainPID={pid}\n"
                ))
                self.assertEqual(reporter.process_state("research", "linux", run), expected)

    def test_systemd_failed_probe_is_unknown(self):
        run = Mock(return_value=completed(stderr="Failed to connect to bus", returncode=1))
        self.assertEqual(reporter.process_state("research", "linux", run), ("unknown", 0))

    def test_systemd_malformed_pid_is_unknown(self):
        run = Mock(return_value=completed("LoadState=loaded\nActiveState=active\nMainPID=invalid\n"))
        self.assertEqual(reporter.process_state("research", "linux", run), ("unknown", 0))

    def test_process_start_uses_local_start_time_and_handles_failed_ps(self):
        stamp = "Mon Sep  7 17:18:19 2026"
        run = Mock(return_value=completed(stamp + "\n"))
        expected = dt.datetime(2026, 9, 7, 17, 18, 19).timestamp()
        self.assertEqual(reporter.process_started(314, run), expected)
        for result in (None, completed(returncode=1), completed("unparseable")):
            self.assertIsNone(reporter.process_started(314, Mock(return_value=result)))


class WorkerLifecycleTests(TemporaryRoot):
    def test_intent_updates_keep_the_newest_transition_under_a_shared_lock(self):
        newer_sleep = {"state": "sleeping", "at": NOW}
        old_start = {"state": "starting", "at": NOW - 10}
        self.assertEqual(reporter.record_intent(self.root, "research", newer_sleep), newer_sleep)
        self.assertEqual(reporter.record_intent(self.root, "research", old_start), newer_sleep)
        self.assertEqual(reporter.last_intent(self.root, "research"), newer_sleep)
        newer_start = {"state": "starting", "at": NOW + 1}
        self.assertEqual(reporter.record_intent(self.root, "research", newer_start), newer_start)
        self.assertTrue((self.root / "workers" / "research.workspace-intent.lock").exists())

    def test_nonfinite_and_boolean_intent_times_are_not_accepted(self):
        for timestamp in [True, False, float("nan"), float("inf"), -float("inf")]:
            with self.subTest(timestamp=timestamp):
                self.assertFalse(reporter.valid_intent({"state": "sleeping", "at": timestamp}))

    def test_sleep_requires_supervisor_intent_and_known_stopped_service(self):
        sleeping = {"state": "sleeping", "at": NOW - 500}
        self.assertEqual(reporter.classify_worker("stopped", False, sleeping, NOW), "sleeping")
        self.assertEqual(reporter.classify_worker("unknown", False, sleeping, NOW), "unknown")
        self.assertEqual(reporter.classify_worker("failed", False, sleeping, NOW), "failed")
        self.assertNotEqual(reporter.classify_worker("stopped", False, None, NOW), "sleeping")

    def test_ready_running_process_overrides_old_sleep_intent(self):
        sleeping = {"state": "sleeping", "at": NOW - 500}
        self.assertEqual(reporter.classify_worker("running", True, sleeping, NOW, NOW - 10), "awake")

    def test_wake_is_starting_until_ready_and_failed_after_deadline(self):
        starting = {"state": "starting", "at": NOW - 5}
        expired = {"state": "starting", "at": NOW - 121}
        for service in ("stopped", "starting", "running"):
            with self.subTest(service=service):
                self.assertEqual(reporter.classify_worker(service, False, starting, NOW, NOW - 5), "starting")
                self.assertEqual(reporter.classify_worker(service, False, expired, NOW, NOW - 121), "failed")

    def test_readiness_from_previous_process_does_not_mark_new_process_awake(self):
        logfile = self.root / "workers" / "research.out"
        old = dt.datetime.fromtimestamp(NOW - 500, dt.timezone.utc).isoformat()
        logfile.write_text(f"{old} INFO subscribed to channel task-channel\n")
        self.assertFalse(reporter.subscribed_since(logfile, "task-channel", NOW - 5))
        new = dt.datetime.fromtimestamp(NOW - 2, dt.timezone.utc).isoformat()
        with logfile.open("a") as stream:
            stream.write(f"\x1b[32m{new}\x1b[0m INFO subscribed to channel task-channel\n")
        self.assertTrue(reporter.subscribed_since(logfile, "task-channel", NOW - 5))

    def test_unrelated_channel_and_undated_readiness_do_not_count(self):
        logfile = self.root / "workers" / "research.out"
        stamp = dt.datetime.fromtimestamp(NOW - 2, dt.timezone.utc).isoformat()
        logfile.write_text(
            f"{stamp} INFO subscribed to channel other-channel\n"
            "INFO subscribed to channel task-channel\n"
        )
        self.assertFalse(reporter.subscribed_since(logfile, "task-channel", NOW - 5))

    def test_legacy_supervisor_history_uses_latest_transition_and_exact_worker(self):
        history = self.root / "workers" / "supervisor.log"
        history.write_text(
            "2026-09-07 12:00:00 slept research (idle)\n"
            "2026-09-07 12:01:00 woke research-more\n"
            "2026-09-07 12:02:00 ignored research\n"
        )
        self.assertEqual(reporter.last_intent(self.root, "research"), {
            "state": "sleeping", "at": dt.datetime(2026, 9, 7, 12, 0).timestamp(),
        })
        with history.open("a") as stream:
            stream.write("2026-09-07 12:03:00 woke research (message)\n")
            stream.write("invalid-date         slept research\n")
        self.assertEqual(reporter.last_intent(self.root, "research"), {
            "state": "starting", "at": dt.datetime(2026, 9, 7, 12, 3).timestamp(),
        })

    def test_transition_marker_overrides_legacy_history(self):
        (self.root / "workers" / "supervisor.log").write_text(
            "2026-09-07 12:00:00 slept research\n"
        )
        marker = {"state": "starting", "at": NOW}
        reporter.atomic_json(self.root / "workers" / "research.workspace-intent.json", marker)
        self.assertEqual(reporter.last_intent(self.root, "research"), marker)

    def test_bad_or_absent_transition_does_not_invent_sleep(self):
        marker = self.root / "workers" / "research.workspace-intent.json"
        for content in (
            "{broken", "[]", '{"state":"awake"}',
            '{"state":"sleeping","at":"not-a-timestamp"}',
            '{"state":"starting"}',
        ):
            with self.subTest(content=content):
                marker.write_text(content)
                self.assertIsNone(reporter.last_intent(self.root, "research"))


class ManagerStateTests(TemporaryRoot):
    def test_full_session_id_field_matches_when_display_id_is_short(self):
        self.assertEqual(self.sessions([
            self.session(id=SESSION[:8], sessionId=SESSION)
        ]), "awake")

    def test_same_folder_sibling_cannot_mask_missing_full_session_id(self):
        self.assertEqual(self.sessions([
            self.session(id="abcdef01", sessionId="abcdef01-aaaa-bbbb-cccc-123456789abc")
        ]), "failed")

    def test_saved_session_and_cwd_find_manager_among_unrelated_agents(self):
        values = [
            self.session(id="unrelated", cwd="/another-project", status="busy"),
            self.session(id="interactive-session", kind="interactive"),
            self.session(),
        ]
        self.assertEqual(self.sessions(values), "awake")

    def test_unique_saved_short_id_is_supported(self):
        self.assertEqual(self.sessions([self.session()], wanted=SESSION[:8]), "awake")

    def test_ambiguous_saved_short_id_is_not_authoritative(self):
        values = [self.session(), self.session(id="12345678-aaaa-bbbb-cccc-123456789abc")]
        self.assertEqual(self.sessions(values, wanted=SESSION[:8]), "unknown")

    def test_full_saved_id_must_match_exactly(self):
        self.assertNotEqual(self.sessions([self.session(id=SESSION + "-other")]), "awake")

    def test_matching_id_with_wrong_cwd_is_not_the_manager(self):
        self.assertNotEqual(self.sessions([self.session(cwd="/another-project")]), "awake")

    def test_without_saved_id_only_background_agent_in_manager_cwd_counts(self):
        self.assertEqual(self.sessions([
            self.session(cwd="/another-project"), self.session(kind="interactive")
        ], wanted=None), "failed")
        self.assertEqual(self.sessions([self.session()], wanted=None), "awake")

    def test_manager_reports_only_known_live_statuses_as_awake(self):
        for status in ("idle", "busy", "running"):
            with self.subTest(status=status):
                self.assertEqual(self.sessions([self.session(status=status)]), "awake")
        for status in (None, "unknown", "unexpected-status"):
            with self.subTest(status=status):
                self.assertEqual(self.sessions([self.session(status=status)]), "unknown")
        for status in ("stopped", "failed"):
            with self.subTest(status=status):
                self.assertEqual(self.sessions([self.session(status=status)]), "failed")

    def test_malformed_or_unreachable_manager_probe_is_unknown(self):
        for result in (None, completed(returncode=1), completed("invalid"), completed("{}")):
            with self.subTest(result=result):
                state = reporter.manager_state(
                    self.root, Path(self.args.manager_cwd), Mock(return_value=result)
                )
                self.assertEqual(state, "unknown")

    def test_ignores_non_session_rows(self):
        self.assertEqual(self.sessions([None, [], "invalid", self.session()]), "awake")


class ReporterWorkflowTests(TemporaryRoot):
    def test_observed_start_replaces_older_sleep_before_readiness_and_survives_restart(self):
        self.worker()
        reporter.record_intent(self.root, "research", {"state": "sleeping", "at": NOW - 500})
        instance = reporter.Reporter(self.args)
        with (
            patch.object(reporter.time, "time", return_value=NOW),
            patch.object(reporter, "manager_state", return_value="awake"),
            patch.object(reporter, "process_state", return_value=("running", 314)),
            patch.object(reporter, "process_started", return_value=NOW - 10),
            patch.object(reporter, "subscribed_since", return_value=False),
            patch.object(instance, "publish", return_value=True) as publish,
        ):
            instance.tick()
            self.assertEqual(publish.call_args.args[2], "starting")
        self.assertEqual(reporter.last_intent(self.root, "research"), {
            "state": "starting", "at": NOW - 10,
        })
        restarted = reporter.Reporter(self.args)
        with (
            patch.object(reporter.time, "time", return_value=NOW + 130),
            patch.object(reporter, "manager_state", return_value="awake"),
            patch.object(reporter, "process_state", return_value=("stopped", 0)),
            patch.object(restarted, "publish", return_value=True) as publish,
        ):
            restarted.tick()
            self.assertEqual(publish.call_args.args[2], "failed")

    def test_newer_supervisor_sleep_wins_race_with_observed_process_start(self):
        self.worker()
        reporter.record_intent(self.root, "research", {"state": "sleeping", "at": NOW - 500})
        instance = reporter.Reporter(self.args)
        original_record = reporter.record_intent
        newer_sleep = {"state": "sleeping", "at": NOW}

        def sleep_races_with_observation(root, slug, intent):
            original_record(root, slug, newer_sleep)
            return original_record(root, slug, intent)

        with (
            patch.object(reporter.time, "time", side_effect=[NOW, NOW + 2]),
            patch.object(reporter, "manager_state", return_value="awake"),
            patch.object(reporter, "process_state", side_effect=[("running", 314), ("stopped", 0)]),
            patch.object(reporter, "process_started", return_value=NOW - 10),
            patch.object(reporter, "subscribed_since", return_value=True),
            patch.object(reporter, "record_intent", side_effect=sleep_races_with_observation),
            patch.object(instance, "publish", return_value=True) as publish,
        ):
            instance.tick()
            self.assertEqual(reporter.last_intent(self.root, "research"), newer_sleep)
            instance.tick()
            self.assertEqual(publish.call_args.args[2], "sleeping")

    def test_failed_intent_persistence_is_unknown_and_retried_next_tick(self):
        self.worker()
        instance = reporter.Reporter(self.args)
        with (
            patch.object(reporter.time, "time", return_value=NOW),
            patch.object(reporter, "manager_state", return_value="awake"),
            patch.object(reporter, "process_state", return_value=("running", 314)),
            patch.object(reporter, "process_started", return_value=NOW - 10),
            patch.object(reporter, "record_intent", side_effect=PermissionError("lock temporarily unavailable")) as record,
            patch.object(instance, "publish", return_value=True) as publish,
            contextlib.redirect_stderr(io.StringIO()),
        ):
            instance.tick()
            instance.tick()
            self.assertEqual(record.call_count, 2)
            self.assertEqual(publish.call_args.args[2], "unknown")

    def test_malformed_readiness_cache_does_not_prevent_worker_reporting(self):
        self.worker()
        for content in ("{broken", "[]", '"invalid"', "null"):
            with self.subTest(content=content):
                (self.root / ".workspace-readiness.json").write_text(content)
                instance = reporter.Reporter(self.args)
                with (
                    patch.object(reporter.time, "time", return_value=NOW),
                    patch.object(reporter, "manager_state", return_value="awake"),
                    patch.object(reporter, "process_state", return_value=("running", 314)),
                    patch.object(reporter, "process_started", return_value=NOW - 5),
                    patch.object(reporter, "subscribed_since", return_value=False),
                    patch.object(instance, "publish", return_value=True) as publish,
                ):
                    instance.tick()
                    self.assertEqual(publish.call_args.args[2], "starting")

    def test_loaded_launchd_job_cannot_remain_starting_forever(self):
        self.worker()
        instance = reporter.Reporter(self.args)
        with (
            patch.object(reporter.time, "time", side_effect=[NOW, NOW + 121]),
            patch.object(reporter, "manager_state", return_value="awake"),
            patch.object(reporter, "process_state", return_value=("starting", 0)),
            patch.object(instance, "publish", return_value=True) as publish,
        ):
            instance.tick()
            self.assertEqual(publish.call_args.args[2], "starting")
            instance.tick()
            self.assertEqual(publish.call_args.args[2], "failed")

    def test_supervised_sleep_wake_crash_and_unreachable_flow(self):
        self.worker()
        marker = self.root / "workers" / "research.workspace-intent.json"
        instance = reporter.Reporter(self.args)
        with (
            patch.object(reporter.time, "time", side_effect=[NOW, NOW + 2, NOW + 4, NOW + 130, NOW + 132]),
            patch.object(reporter, "manager_state", return_value="awake"),
            patch.object(reporter, "process_state", side_effect=[
                ("stopped", 0), ("running", 314), ("running", 314),
                ("stopped", 0), ("unknown", 0),
            ]),
            patch.object(reporter, "process_started", return_value=NOW + 1),
            patch.object(reporter, "subscribed_since", side_effect=[False, True]),
            patch.object(instance, "publish", return_value=True) as publish,
        ):
            reporter.atomic_json(marker, {"state": "sleeping", "at": NOW - 30})
            instance.tick()
            self.assertEqual(publish.call_args.args[2], "sleeping")
            reporter.atomic_json(marker, {"state": "starting", "at": NOW + 1})
            for expected in ("starting", "awake", "failed", "unknown"):
                instance.tick()
                self.assertEqual(publish.call_args.args[2], expected)

    def test_publish_retries_rejected_failed_and_malformed_cli_results(self):
        for response in (
            None, completed(returncode=2), completed('{"accepted":false}'),
            completed("not json"), completed("[]"), completed('{"accepted":"true"}'),
        ):
            with self.subTest(response=response):
                instance = reporter.Reporter(self.args)
                with patch.object(reporter, "command", side_effect=[response, completed('{"accepted":true}')]) as run:
                    with contextlib.redirect_stderr(io.StringIO()):
                        self.assertFalse(instance.publish("task-channel", WORKER_KEY, "awake", NOW))
                    self.assertFalse(instance.publish("task-channel", WORKER_KEY, "awake", NOW + 2))
                    self.assertFalse(instance.publish("task-channel", WORKER_KEY, "awake", NOW + 59))
                    self.assertEqual(run.call_count, 1)
                    self.assertTrue(instance.publish("task-channel", WORKER_KEY, "awake", NOW + 60))
                    self.assertEqual(run.call_count, 2)

    def test_deleted_channel_is_quiet_during_retry_even_if_process_status_changes(self):
        instance = reporter.Reporter(self.args)
        response = completed(returncode=3, stderr=json.dumps({
            "error": "auth_error", "message": "restricted: not a channel member SECRET",
        }))
        with (patch.object(reporter, "command", return_value=response) as run,
              contextlib.redirect_stderr(io.StringIO()) as output):
            for second in range(0, 60, 2):
                state = "awake" if second % 4 else "unknown"
                self.assertFalse(instance.publish("deleted-channel", WORKER_KEY, state, NOW + second))
            self.assertEqual(run.call_count, 1)
            self.assertEqual(len(output.getvalue().splitlines()), 1)
            self.assertIn("category=permission-denied", output.getvalue())
            self.assertNotIn("SECRET", output.getvalue())
            self.assertFalse(instance.publish("deleted-channel", WORKER_KEY, "awake", NOW + 60))
            self.assertEqual(run.call_count, 2)

    def test_failed_task_does_not_delay_healthy_task_or_its_transitions(self):
        instance = reporter.Reporter(self.args)
        with (patch.object(reporter, "command", side_effect=[
                None, completed('{"accepted":true}'), completed('{"accepted":true}'),
              ]) as run, contextlib.redirect_stderr(io.StringIO())):
            self.assertFalse(instance.publish("deleted-channel", WORKER_KEY, "awake", NOW))
            self.assertTrue(instance.publish("healthy-channel", WORKER_KEY, "awake", NOW + 1))
            self.assertTrue(instance.publish("healthy-channel", WORKER_KEY, "sleeping", NOW + 2))
            self.assertFalse(instance.publish("deleted-channel", WORKER_KEY, "awake", NOW + 3))
            self.assertEqual(run.call_count, 3)

    def test_success_after_retry_restores_immediate_transitions(self):
        instance = reporter.Reporter(self.args)
        with (patch.object(reporter, "command", side_effect=[
                None, completed('{"accepted":true}'), completed('{"accepted":true}'),
              ]) as run, contextlib.redirect_stderr(io.StringIO())):
            self.assertFalse(instance.publish("task-channel", WORKER_KEY, "awake", NOW))
            self.assertTrue(instance.publish("task-channel", WORKER_KEY, "awake", NOW + 60))
            self.assertTrue(instance.publish("task-channel", WORKER_KEY, "sleeping", NOW + 61))
            self.assertEqual(run.call_count, 3)
            self.assertEqual(instance.retry_after, {})

    def test_failure_categories_accept_only_safe_structured_error_information(self):
        cases = [(None, "unavailable"), ("bad-json", "rejected"), ("[]", "rejected"),
                 ('{"message":"invalid: workspace channel not found SECRET"}', "channel-unavailable"),
                 ('{"message":"permission denied SECRET"}', "permission-denied")]
        for error, expected in cases:
            with self.subTest(error=error):
                result = None if error is None else completed(returncode=2, stderr=error)
                self.assertEqual(reporter.publish_failure_category(result), expected)

    def test_success_is_throttled_until_renewal_but_state_change_is_immediate(self):
        instance = reporter.Reporter(self.args)
        with patch.object(reporter, "command", return_value=completed('{"accepted":true}')) as run:
            instance.publish("task-channel", WORKER_KEY, "awake", NOW)
            instance.publish("task-channel", WORKER_KEY, "awake", NOW + 59)
            self.assertEqual(run.call_count, 1)
            instance.publish("task-channel", WORKER_KEY, "awake", NOW + 60)
            self.assertEqual(run.call_count, 2)
            instance.publish("task-channel", WORKER_KEY, "sleeping", NOW + 61)
            self.assertEqual(run.call_count, 3)

    def test_unknown_status_expires_lease_instead_of_publishing_sleep(self):
        instance = reporter.Reporter(self.args)
        with patch.object(reporter, "command", return_value=completed('{"accepted":true}')) as run:
            self.assertTrue(instance.publish("task-channel", WORKER_KEY, "unknown", NOW))
            args = run.call_args.args[0]
            self.assertEqual(args[args.index("--lease-seconds") + 1], "0")
            self.assertNotEqual(args[args.index("--state") + 1], "sleeping")

    def test_manager_root_is_accepted_before_task_relationship(self):
        self.worker()
        instance = reporter.Reporter(self.args)
        with (
            patch.object(reporter.time, "time", side_effect=[NOW, NOW + 2, NOW + 60]),
            patch.object(reporter, "manager_state", return_value="awake"),
            patch.object(reporter, "process_state", return_value=("stopped", 0)),
            patch.object(reporter, "command", side_effect=[
                completed('{"accepted":false}'), completed('{"accepted":true}'),
                completed('{"accepted":true}'),
            ]) as run,
            contextlib.redirect_stderr(io.StringIO()),
        ):
            instance.tick()
            self.assertEqual(run.call_count, 1, "A refused root must block child publishes")
            instance.tick()
            self.assertEqual(run.call_count, 1, "Root cooldown must also block child publishes")
            instance.tick()
            channels = [call.args[0][call.args[0].index("--channel") + 1] for call in run.call_args_list]
            self.assertEqual(channels, ["manager-channel", "manager-channel", "task-channel"])

    def test_pid_reuse_invalidates_cached_readiness(self):
        self.worker()
        reporter.atomic_json(self.root / ".workspace-readiness.json", {
            "research": f"314:{NOW - 500}",
        })
        instance = reporter.Reporter(self.args)
        with (
            patch.object(reporter.time, "time", return_value=NOW),
            patch.object(reporter, "manager_state", return_value="awake"),
            patch.object(reporter, "process_state", return_value=("running", 314)),
            patch.object(reporter, "process_started", return_value=NOW - 5),
            patch.object(reporter, "subscribed_since", return_value=False) as subscribed,
            patch.object(instance, "publish", return_value=True) as publish,
        ):
            instance.tick()
            subscribed.assert_called_once()
            self.assertEqual(publish.call_args.args[:3], ("task-channel", WORKER_KEY, "starting"))

    def test_same_process_reuses_readiness_cache_and_new_readiness_is_persisted(self):
        self.worker()
        instance = reporter.Reporter(self.args)
        with (
            patch.object(reporter.time, "time", return_value=NOW),
            patch.object(reporter, "manager_state", return_value="awake"),
            patch.object(reporter, "process_state", return_value=("running", 314)),
            patch.object(reporter, "process_started", return_value=NOW - 5),
            patch.object(reporter, "subscribed_since", return_value=True) as subscribed,
            patch.object(instance, "publish", return_value=True) as publish,
        ):
            instance.tick()
            instance.tick()
            subscribed.assert_called_once()
            self.assertEqual(publish.call_args.args[:3], ("task-channel", WORKER_KEY, "awake"))
            saved = reporter.read_json(self.root / ".workspace-readiness.json")
            self.assertEqual(saved["research"], f"314:{NOW - 5}")

    def test_unverifiable_process_start_cannot_reuse_readiness(self):
        self.worker()
        instance = reporter.Reporter(self.args)
        instance.ready["research"] = "314:None"
        with (
            patch.object(reporter.time, "time", return_value=NOW),
            patch.object(reporter, "manager_state", return_value="awake"),
            patch.object(reporter, "process_state", return_value=("running", 314)),
            patch.object(reporter, "process_started", return_value=None),
            patch.object(instance, "publish", return_value=True) as publish,
        ):
            instance.tick()
            self.assertNotEqual(publish.call_args.args[2], "awake")

    def test_invalid_worker_coordinates_are_skipped_without_probing_or_publishing(self):
        self.worker("bad key", pubkey="c" * 64)
        self.worker("missing-channel", channel="")
        self.worker("bad-pubkey", pubkey="not-a-key")
        self.worker("valid")
        instance = reporter.Reporter(self.args)
        with (
            patch.object(reporter.time, "time", return_value=NOW),
            patch.object(reporter, "manager_state", return_value="awake"),
            patch.object(reporter, "process_state", return_value=("stopped", 0)) as process,
            patch.object(instance, "publish", return_value=True) as publish,
        ):
            instance.tick()
            self.assertEqual(process.call_count, 1)
            self.assertEqual(process.call_args.args[0], "valid")
            self.assertEqual(publish.call_count, 2)

    def test_dry_run_never_calls_cli_or_persists_readiness(self):
        self.worker()
        self.args.dry_run = True
        instance = reporter.Reporter(self.args)
        with (
            patch.object(reporter.time, "time", return_value=NOW),
            patch.object(reporter, "manager_state", return_value="awake"),
            patch.object(reporter, "process_state", return_value=("running", 314)),
            patch.object(reporter, "process_started", return_value=NOW - 5),
            patch.object(reporter, "subscribed_since", return_value=True),
            patch.object(reporter, "command") as run,
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            instance.tick()
            run.assert_not_called()
            self.assertFalse((self.root / ".workspace-readiness.json").exists())
            rows = [json.loads(line) for line in output.getvalue().splitlines()]
            self.assertEqual([row["channel"] for row in rows], ["manager-channel", "task-channel"])


if __name__ == "__main__":
    unittest.main()
