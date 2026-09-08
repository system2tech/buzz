"""Portable fixture tests; never load LaunchAgents or contact a relay/model."""
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parent.parent)]
import local_manager as local
import install


class EndLoop(Exception):
    pass


class LocalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'manager with spaces'
        self.root.mkdir()
        for name in ('workers', 'plists', 'logs', 'tasks'):
            (self.root / name).mkdir()
        self.config = {'root': str(self.root), 'name': 'Person', 'channel_name': 'person-local',
                       'manager_pubkey': 'b'*64, 'human_pubkey': 'a'*64,
                       'relay': 'wss://example.test',
                       'worker_idle_minutes': 3, 'worker_max_awake': 2}

    def test_loaded_foreign_job_is_neither_accepted_nor_stopped(self):
        response = SimpleNamespace(returncode=0, stdout='program = /other/runtime\npid = 123\n', stderr='')
        with patch.object(local.subprocess, 'run', return_value=response) as run:
            for operation in (local.start_job, local.stop_job):
                with self.assertRaises(ValueError):
                    operation(self.config, 'manager')
            self.assertTrue(all(call.args[0][:2] == ['launchctl', 'print'] for call in run.call_args_list))

    def test_own_loaded_program_with_spaces_is_recognized(self):
        response = SimpleNamespace(returncode=0, stdout='program = '+str(self.root/'bin/buzz-local')+'\narguments = {\n'+str(self.root/'bin/buzz-local')+'\nrun\nmanager\n}\npid = 123\n', stderr='')
        with patch.object(local.subprocess, 'run', return_value=response):
            self.assertEqual(local.inspect_job(self.config, 'manager'), {'state': 'running', 'pid': 123})

    def test_same_program_with_wrong_component_is_a_conflict(self):
        program = str(self.root/'bin/buzz-local')
        response = SimpleNamespace(returncode=0, stderr='', stdout=(
            'program = '+program+'\narguments = {\n'+program+'\nrun\nworker\nsample\n}\npid = 123\n'))
        with patch.object(local.subprocess,'run',return_value=response):
            self.assertEqual(local.inspect_job(self.config,'manager')['state'],'conflict')

    def test_stale_busy_snapshot_never_wakes_a_retired_worker(self):
        busy=self.root/'workers/sample.busy'
        busy.write_text('1')
        local.save_json(self.root/'workers/sample.json',{'slug':'sample','state':'retired'})
        with patch.object(local,'start_job') as start:
            self.assertFalse(local.guarded_wake(self.config,'sample'))
            busy.unlink()
            self.assertFalse(local.guarded_wake(self.config,'sample'))
            start.assert_not_called()

    def test_plist_arguments_preserve_spaces_without_shell_interpolation(self):
        data = local.plist(self.config, 'worker', 'research')
        decoded = plistlib.loads(plistlib.dumps(data))
        self.assertEqual(decoded['ProgramArguments'], [str(self.root/'bin/buzz-local'), 'run', 'worker', 'research'])
        self.assertEqual(decoded['Label'], 'com.mrfix.worker.research')
        self.assertNotIn('BUZZ_PRIVATE_KEY', decoded['EnvironmentVariables'])

    def test_cpu_parser_handles_days_and_ignores_unrelated_bad_times(self):
        response = SimpleNamespace(stdout='10 1 1-02:03:04\n11 10 00:02.50\n90 1 ???\n')
        with patch.object(local.subprocess, 'run', return_value=response):
            self.assertEqual(local.tree_cpu(10), 93786.5)

    def test_cpu_unknown_descendant_prevents_sleep(self):
        response = SimpleNamespace(stdout='10 1 00:01.00\n11 10 ???\n')
        with patch.object(local.subprocess, 'run', return_value=response):
            self.assertIsNone(local.tree_cpu(10))

    def test_first_time_channel_membership_failure_is_not_ready_and_reuses_invite(self):
        local.save_json(self.root/'local.json', self.config)
        (self.root/'.buzz-key').write_text('manager-key')
        key = SimpleNamespace(secret=b'1'*32)
        replies = [None, {'channel_id': 'retained-channel'}, RuntimeError('add failed')]
        with patch.object(local, 'secret_key', return_value=key), \
             patch.object(local, 'pubkey', return_value='b'*64), \
             patch.object(local, 'claim_relay_invite') as claim, \
             patch.object(local, 'buzz', side_effect=replies):
            with self.assertRaises(RuntimeError):
                local.configure(self.config, SimpleNamespace(
                    human_pubkey='a'*64, invite='invite-code', invite_file=None))
        claim.assert_called_once_with(key, self.config['relay'], 'invite-code')
        saved = local.load_json(self.root/'local.json')
        self.assertEqual(saved['channel'], 'retained-channel')
        self.assertEqual(saved['relay_membership'], 'invite')
        self.assertFalse(saved.get('configured', False))
        with patch.object(local, 'secret_key', return_value=key), \
             patch.object(local, 'pubkey', return_value='b'*64), \
             patch.object(local, 'claim_relay_invite') as claim, \
             patch.object(local, 'buzz', side_effect=[
                 {}, {}, [{'channel_id': 'coordination', 'name': 'agent-managers',
                           'visibility': 'public'}], {}
             ]) as buzz:
            local.configure(saved, SimpleNamespace(
                human_pubkey='a'*64, invite=None, invite_file=None))
        claim.assert_not_called()
        self.assertFalse(any(c.args[1][:2] == ['channels','create'] for c in buzz.call_args_list))
        configured = local.load_json(self.root/'local.json')
        self.assertTrue(configured['configured'])
        self.assertEqual(configured['coordination_channel'], 'coordination')

    def test_start_requires_personal_readiness_before_loading_jobs(self):
        with patch.object(local, 'status', return_value={'missing':['claude_login']}), \
             patch.object(local, 'start_job') as start:
            with self.assertRaises(ValueError):
                local.operate(self.config, 'start')
            start.assert_not_called()

    def test_sleeping_same_second_event_wakes(self):
        (self.root/'workers/sample.busy').write_text('1')
        local.save_json(self.root/'workers/sample.json', {'slug':'sample','channel':'channel','state':'active'})
        local.save_json(self.root/'workers/sample.slept', {'created_at':1000, 'ids':['old']})
        with patch.object(local, 'inspect_job', return_value={'state':'absent', 'pid':None}), \
             patch.object(local, 'messages', return_value={'created_at':1000,'ids':['old','new']}), \
             patch.object(local, 'mark'), patch.object(local, 'start_job') as start, \
             patch.object(local.time, 'sleep', side_effect=EndLoop):
            with self.assertRaises(EndLoop):
                local.supervise(self.config)
            start.assert_called_once_with(self.config, 'worker', 'sample')

    def test_worker_env_keeps_personal_keychain_and_saved_session_scope(self):
        (self.root/'.buzz-key').write_text('not-a-real-key')
        self.config.update(tools={'buzz':'/tools/buzz','claude':'/tools/claude'},relay='wss://example.test')
        with patch.dict(local.os.environ, {'CLAUDE_CONFIG_DIR':'/wrong/config','BUZZ_AUTH_TAG':'wrong'}):
            env = local.environment(self.config)
        self.assertNotIn('CLAUDE_CONFIG_DIR',env)
        self.assertEqual(env['HOME'], str(Path.home()))
        self.assertEqual(env['MRFIX_ROOT'],str(self.root))
        self.assertNotIn('BUZZ_AUTH_TAG', env)

    def test_local_restart_stops_only_the_saved_manager_session(self):
        session_id = '12345678-1234-4234-8234-123456789abc'
        (self.root / '.session-id').write_text(session_id + '\n')
        (self.root / '.buzz-key').write_text('not-a-real-key')
        self.config.update(tools={'claude': '/tools/claude', 'buzz': '/tools/buzz'},
                           relay='wss://example.test')
        response = SimpleNamespace(returncode=0, stdout='', stderr='')
        with patch.object(local.subprocess, 'run', return_value=response) as run:
            local.restart_manager(self.config)
        self.assertEqual(run.call_args.args[0], ['/tools/claude', 'stop', session_id])

    def test_installer_refuses_unmanaged_existing_runtime(self):
        args=SimpleNamespace(root=self.root,brain=self.root/'brain')
        with self.assertRaises(ValueError):
            install.install(args)

    def test_install_generates_valid_files_without_loading_any_job(self):
        root=Path(self.tmp.name)/'fresh runtime'
        args=SimpleNamespace(root=root,brain=self.root/'brain',name='Person',channel_name='person-local',
                             relay='https://example.test', **{k:sys.executable for k in (
                                 'python','buzz','bridge','watcher','claude','adapter','node')})
        shared=Path(os.environ.get('BUZZ_TEST_SHARED_SOURCE',str(Path(__file__).resolve().parent.parent)))
        key=SimpleNamespace(secret=b'2'*32)
        with patch.object(install, 'mint_pair',return_value=key), \
             patch.object(install,'pubkey',return_value='b'*64), \
             patch.object(install.subprocess,'run',return_value=SimpleNamespace(
                 returncode=0,stdout='room --session-id --json conversation is kept')) as run:
            install.install(args,shared)
            original=(root/'local.json').read_bytes()
            install.install(args,shared)
            self.assertEqual((root/'local.json').read_bytes(),original)
            self.assertFalse(any(c.args[0][0]=='launchctl' for c in run.call_args_list))
            self.assertFalse(any('--bg' in c.args[0] for c in run.call_args_list))
            self.assertTrue(any(c.args[0][-2:]==['--help','--verbose'] for c in run.call_args_list))
        for p in (root/'plists').glob('*.plist'):
            self.assertIn('Label',plistlib.loads(p.read_bytes()))
        self.assertEqual(len(list((root/'plists').glob('*.plist'))),4)
        self.assertEqual((root/'bin/buzz-local').stat().st_mode & 0o777,0o700)
        self.assertIn('#agent-managers', (root/'manager/CLAUDE.md').read_text())
        self.assertIn('buzz-local restart', (root/'manager/CLAUDE.md').read_text())
        result=subprocess.run(['sh','-n',str(root/'bin/buzz-local')],capture_output=True)
        self.assertEqual(result.returncode,0)


if __name__=='__main__':
    unittest.main()
