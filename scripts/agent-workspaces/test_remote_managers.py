"""Isolated contract tests: no network, real accounts or services are changed."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import manager_common as common
import remote_manager as setup
import remote_workers as workers
import install_reporter
import install_remote_managers
import workspace_reporter


class MultiUserTests(unittest.TestCase):
    def test_required_coordination_channel_must_resolve_exactly_once(self):
        self.assertEqual(common.exact_channel_id([
            {'channel_id': 'coordination', 'name': 'agent-managers', 'visibility': 'public'},
            {'channel_id': 'private', 'name': 'agent-managers', 'visibility': 'private'},
            {'channel_id': 'other', 'name': 'agent-managers-archive', 'visibility': 'open'},
        ]), 'coordination')
        for rows in ([], [
            {'channel_id': 'one', 'name': 'agent-managers', 'visibility': 'open'},
            {'channel_id': 'two', 'name': 'AGENT-MANAGERS', 'visibility': 'open'},
        ]):
            with self.assertRaisesRegex(ValueError, 'must exist exactly once'):
                common.exact_channel_id(rows)

    def test_configuration_joins_required_coordination_channel(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = {'root': directory, 'name': 'Test', 'manager_pubkey': 'b'*64}
            key = SimpleNamespace(secret=b'x'*32)
            args = SimpleNamespace(owner_pubkey='a'*64, owner_key_file=None)
            replies = [{}, {'id': 'personal'}, {},
                       [{'channel_id': 'coordination', 'name': 'agent-managers',
                         'visibility': 'public'}], {}]
            with patch.object(setup.os, 'geteuid', return_value=1000), \
                 patch.object(setup.pwd, 'getpwuid', return_value=SimpleNamespace(pw_name='harri')), \
                 patch.object(setup, 'config_for', return_value=dict(config)), \
                 patch.object(setup.getpass, 'getpass', return_value='hidden'), \
                 patch.object(setup, 'secret_key', return_value=key), \
                 patch.object(setup, 'pubkey', return_value='a'*64), \
                 patch.object(setup, 'auth_tag', return_value=[]), \
                 patch.object(setup, 'buzz', side_effect=replies) as buzz:
                setup.configure_locked(args)
            saved = common.load_json(root / 'manager.json')
            self.assertEqual(saved['channel'], 'personal')
            self.assertEqual(saved['coordination_channel'], 'coordination')
            self.assertTrue(saved['configured'])
            self.assertIn(
                ['channels', 'join', '--channel', 'coordination'],
                [call.args[1] for call in buzz.call_args_list],
            )

    def test_shared_installer_includes_discoverable_setup_guides(self):
        source = Path(__file__).resolve().parent
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / 'installed'
            install_remote_managers.install_guides(source, destination)
            for name in ('s2-personal-agents.md', 's2-personal-manager-setup.md',
                         's2-local-manager.md', 's2-server-recovery.md'):
                self.assertEqual((destination / 'docs' / name).read_bytes(),
                                 (source.parent.parent / 'docs' / name).read_bytes())

    def test_identical_task_slugs_have_distinct_account_units(self):
        self.assertEqual(common.worker_unit('harri', 'research'), 'buzz-worker-harri@research.service')
        self.assertNotEqual(common.worker_unit('harri', 'research'),
                            common.worker_unit('alex', 'research'))

    def test_unit_files_keep_account_home_and_process_owner_together(self):
        with tempfile.TemporaryDirectory() as directory:
            for user in ('harri', 'alex'):
                names = setup.install_units(user, f'/srv/users/{user}', '/opt/buzz-manager',
                                             Path(directory))
                self.assertEqual(len(names), 5)
                for name in names:
                    text = (Path(directory) / name).read_text()
                    self.assertIn(f'User={user}\n', text)
                    self.assertIn(f'ConditionPathExists=/srv/users/{user}/mrfix/.ready', text)
                    self.assertIn('KillMode=control-group', text)
                    self.assertNotIn('User=root', text)
            self.assertEqual(len(list(Path(directory).iterdir())), 10)

    def test_invalid_names_cannot_escape_units_or_task_directories(self):
        for slug in ('../alex', '/tmp/file', 'a/b', 'a@b', '--help', 'A', 'two words'):
            with self.subTest(slug=slug), self.assertRaises(ValueError):
                common.worker_unit('harri', slug)
        for user in ('root\nExecStart=x', '../root', '-bad'):
            with self.subTest(user=user), self.assertRaises(ValueError):
                common.services(user)

    def test_systemd_specifiers_and_whitespace_in_paths_are_rejected(self):
        for home in ('/home/%i', '/home/two words', '/home/a\nUser=root'):
            with self.subTest(home=home), self.assertRaises(ValueError):
                setup.unit_text('harri', home, '/opt/buzz-manager', 'manager')

    def test_relay_origin_is_validated_and_normalized(self):
        self.assertEqual(common.relay_url('https://example.test/'), 'wss://example.test')
        for value in ('https://user:secret@example.test', 'file:///tmp/a',
                      'https://example.test/path', 'https://example.test?key=x'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                common.relay_url(value)

    def test_reporter_reads_only_its_accounts_worker_unit(self):
        seen = []
        def run(argv):
            seen.append(argv)
            return SimpleNamespace(returncode=0, stdout='ActiveState=active\nMainPID=42\nLoadState=loaded', stderr='')
        for user in ('harri', 'alex'):
            state = workspace_reporter.process_state(
                'research', 'linux', run, f'buzz-worker-{user}@{{slug}}.service')
            self.assertEqual(state, ('running', 42))
            self.assertEqual(seen[-1][2], common.worker_unit(user, 'research'))

    def test_reporter_rejects_unknown_unit_template(self):
        with self.assertRaises(ValueError):
            workspace_reporter.process_state('research', 'linux', unit_template='ssh.service')

    def test_reporter_installer_defaults_are_distinct_and_legacy_is_explicit(self):
        self.assertNotEqual(install_reporter.linux_service_names('harri'),
                            install_reporter.linux_service_names('alex'))
        self.assertEqual(install_reporter.linux_service_names(
            'khoi', 'buzz-workspace-reporter.service', 'buzz-worker-supervisor.service'),
            ('buzz-workspace-reporter.service', 'buzz-worker-supervisor.service'))

    def test_secret_output_is_not_in_cli_failure(self):
        with patch.object(common, 'runtime_env', return_value={}), \
             patch.object(common.subprocess, 'run', return_value=SimpleNamespace(
                 returncode=1, stdout='', stderr='secret must never be printed')):
            with self.assertRaises(RuntimeError) as caught:
                common.buzz({'tools': {'buzz': '/bin/buzz'}}, ['users', 'set-profile'])
        self.assertNotIn('secret', str(caught.exception))

    def test_failed_sleep_does_not_retire_worker(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'workers').mkdir()
            common.save_json(root / 'workers/research.json', {'slug': 'research', 'state': 'active'})
            (root / 'workers/research.busy').write_text('1')
            with patch.object(workers, 'service', side_effect=subprocess.CalledProcessError(1, ['systemctl'])):
                with self.assertRaises(subprocess.CalledProcessError):
                    workers.retire({'root': directory, 'user': 'harri'}, 'research')
            self.assertTrue((root / 'workers/research.busy').exists())
            self.assertEqual(common.load_json(root / 'workers/research.json')['state'], 'active')

    def test_retirement_keeps_channel_session_and_key(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            w = root / 'workers'
            w.mkdir()
            common.save_json(w / 'research.json', {'slug': 'research', 'state': 'active'})
            for suffix in ('busy', 'key', 'channel', 'sessions.json'):
                (w / f'research.{suffix}').write_text('retained')
            with patch.object(workers, 'service') as service, patch.object(workers, 'mark'):
                workers.retire({'root': directory, 'user': 'harri'}, 'research')
                service.assert_called_once_with('stop', 'buzz-worker-harri@research.service')
            self.assertFalse((w / 'research.busy').exists())
            for suffix in ('key', 'channel', 'sessions.json'):
                self.assertEqual((w / f'research.{suffix}').read_text(), 'retained')

    def test_spawn_refuses_before_personal_setup_is_ready(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(workers, 'buzz') as buzz:
            with self.assertRaises(ValueError):
                workers.spawn({'root': directory}, 'research', 'test')
            buzz.assert_not_called()

    def test_start_skips_unconfigured_accounts_without_stopping_other_accounts(self):
        blocked = {'user': 'harri', 'layout': 'personal', 'root': '/home/harri/mrfix',
                   'missing': ['claude_login', 'buzz_identity']}
        with patch.object(setup, 'require_root'), \
             patch.object(setup, 'selected_users', return_value=['harri', 'alex']), \
             patch.object(setup, 'status_one', side_effect=[blocked, {**blocked, 'user': 'alex'}]), \
             patch.object(setup.subprocess, 'run') as run:
            self.assertEqual(setup.operate(SimpleNamespace(command='start', all=True)), 1)
            run.assert_not_called()

    def test_bad_account_does_not_block_other_accounts(self):
        blocked = {'user': 'alex', 'layout': 'personal', 'root': '/home/alex/mrfix',
                   'missing': ['claude_login']}
        with patch.object(setup, 'require_root'), \
             patch.object(setup, 'selected_users', return_value=['harri', 'alex']), \
             patch.object(setup, 'status_one', side_effect=[ValueError('bad config'), blocked]) as status, \
             patch.object(setup.subprocess, 'run') as run:
            self.assertEqual(setup.operate(SimpleNamespace(command='start', all=True)), 1)
            self.assertEqual(status.call_count, 2)
            run.assert_not_called()

    def test_service_failure_does_not_block_other_accounts(self):
        state = {'user': 'harri', 'layout': 'personal', 'root': '/home/harri/mrfix', 'missing': []}
        with patch.object(setup, 'require_root'), \
             patch.object(setup, 'selected_users', return_value=['harri', 'alex']), \
             patch.object(setup, 'status_one', return_value=state), \
             patch.object(setup.subprocess, 'run', side_effect=[
                 subprocess.CalledProcessError(1, ['systemctl']), None]) as run:
            self.assertEqual(setup.operate(SimpleNamespace(command='stop', all=True)), 1)
            self.assertEqual(run.call_count, 2)
            self.assertIn('buzz-manager@alex.service', run.call_args.args[0])

    def test_partial_spawn_retries_profile_with_same_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'mrfix'
            w = root / 'workers'
            w.mkdir(parents=True)
            (root / '.ready').touch()
            (root / '.buzz-key').write_text('owner')
            (w / 'research.key').write_text('worker')
            common.save_json(w / 'research.json', {'slug': 'research', 'task': 'test',
                            'state': 'preparing', 'pubkey': 'worker-pub', 'channel': ''})
            config = {'root': str(root), 'owner_pubkey': 'human-pub', 'manager_pubkey': 'owner-pub', 'name': 'Test'}
            with patch.dict(sys.modules, {'coincurve': SimpleNamespace()}), \
                 patch.object(workers, 'secret_key', side_effect=lambda value: value), \
                 patch.object(workers, 'pubkey', side_effect=lambda value: value + '-pub'), \
                 patch.object(workers, 'buzz', side_effect=RuntimeError('relay unavailable')) as buzz:
                for attempt in range(2):
                    with self.assertRaisesRegex(RuntimeError, 'relay unavailable'):
                        workers.spawn(config, 'research', 'test')
                self.assertEqual(buzz.call_count, 2)
                self.assertEqual((w / 'research.key').read_text(), 'worker')

    def test_new_worker_is_signed_by_manager_without_human_secret(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'workers').mkdir()
            (root / '.ready').touch()
            (root / '.buzz-key').write_text('manager')
            key = SimpleNamespace(secret=b'x'*32)
            config = {'root': directory, 'manager_pubkey': 'manager-pub',
                      'owner_pubkey': 'human-pub', 'name': 'Test'}
            with patch.dict(sys.modules, {'coincurve': SimpleNamespace(PrivateKey=lambda: key)}), \
                 patch.object(workers, 'secret_key', side_effect=lambda value: value), \
                 patch.object(workers, 'pubkey', side_effect=lambda value: 'manager-pub' if value == 'manager' else 'worker-pub'), \
                 patch.object(workers, 'auth_tag', return_value=['signed-by-manager']) as sign, \
                 patch.object(workers, 'buzz', side_effect=RuntimeError('offline')):
                with self.assertRaisesRegex(RuntimeError, 'offline'):
                    workers.spawn(config, 'sample', 'test')
            sign.assert_called_once_with('manager', 'worker-pub')
            self.assertFalse((root / '.owner-key').exists())
            self.assertEqual(common.load_json(root / 'workers/sample.json')['auth_tag'],
                             ['signed-by-manager'])

    def test_ambiguous_channel_creation_cannot_create_duplicate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'mrfix'
            w = root / 'workers'
            w.mkdir(parents=True)
            (root / '.ready').touch()
            (root / '.buzz-key').write_text('owner')
            (w / 'research.key').write_text('worker')
            common.save_json(w / 'research.json', {'slug': 'research', 'task': 'test',
                'state': 'preparing', 'pubkey': 'worker-pub', 'channel': '', 'channel_pending': True})
            config = {'root': str(root), 'owner_pubkey': 'human-pub', 'manager_pubkey': 'owner-pub', 'name': 'Test'}
            with patch.dict(sys.modules, {'coincurve': SimpleNamespace()}), \
                 patch.object(workers, 'secret_key', side_effect=lambda value: value), \
                 patch.object(workers, 'pubkey', side_effect=lambda value: value + '-pub'), \
                 patch.object(workers, 'buzz') as buzz:
                with self.assertRaisesRegex(ValueError, 'outcome unknown'):
                    workers.spawn(config, 'research', 'test')
                buzz.assert_not_called()

    def test_failed_owner_membership_stays_pending_and_retries_same_channel(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = {'root': directory, 'name': 'Test', 'manager_pubkey': 'b'*64}
            key = SimpleNamespace(secret=b'x'*32)
            args = SimpleNamespace(owner_pubkey='a'*64, owner_key_file=None)
            with patch.object(setup.os, 'geteuid', return_value=1000), \
                 patch.object(setup.pwd, 'getpwuid', return_value=SimpleNamespace(pw_name='harri')), \
                 patch.object(setup, 'config_for', side_effect=lambda user: dict(config)), \
                 patch.object(setup.getpass, 'getpass', return_value='hidden'), \
                 patch.object(setup, 'secret_key', return_value=key), \
                 patch.object(setup, 'pubkey', return_value='a'*64), \
                 patch.object(setup, 'auth_tag', return_value=[]), \
                 patch.object(setup, 'buzz', side_effect=[{}, {'id': 'saved-channel'}, RuntimeError('membership failed')]):
                with self.assertRaisesRegex(RuntimeError, 'membership failed'):
                    setup.configure_locked(args)
            saved = common.load_json(root / 'manager.json')
            self.assertFalse((root / '.owner-key').exists())
            self.assertEqual(saved['channel'], 'saved-channel')
            self.assertFalse(saved.get('configured', False))
            self.assertFalse(saved.get('channel_pending', False))

    def test_atomic_private_file_rejects_temporary_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            victim = root / 'keep'
            victim.write_text('unchanged')
            (root / 'key.new').symlink_to(victim)
            with self.assertRaises(OSError):
                common.atomic(root / 'key', 'secret')
            self.assertEqual(victim.read_text(), 'unchanged')

    def test_worker_runtime_keeps_personal_credentials_and_session_map(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'mrfix'
            w = root / 'workers'
            w.mkdir(parents=True)
            common.save_json(w / 'research.json', {'slug': 'research', 'state': 'active',
                                                  'channel': 'channel-harri'})
            (w / 'research.busy').write_text('1')
            config = {'root': str(root), 'user': 'harri', 'owner_pubkey': 'a'*64,
                      'prefix': '/opt/buzz-manager',
                      'tools': {'adapter': '/opt/adapter', 'bridge': '/opt/bridge'}}
            with patch.object(workers, 'runtime_env', return_value={'HOME': '/home/harri'}), \
                 patch.object(workers.os, 'chdir'), patch.object(workers.os, 'dup2'), \
                 patch.object(workers.os, 'execve') as execute:
                workers.run_component(config, 'worker', 'research')
            env = execute.call_args.args[2]
            self.assertEqual(env['HOME'], '/home/harri')
            self.assertEqual(env['BUZZ_ACP_SESSION_MAP'], str(w / 'research.sessions.json'))
            self.assertEqual(env['BUZZ_ACP_MULTIPLE_EVENT_HANDLING'], 'steer')
            self.assertEqual(env['BUZZ_ACP_OBSERVER_CHANNEL_MEMBERS'], 'true')
            self.assertNotIn('CLAUDE_CONFIG_DIR', env)


if __name__ == '__main__':
    unittest.main()
