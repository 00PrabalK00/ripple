import os
import stat
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from ripple_agent import doctor, setup

ME, PRABAL = '6f6c1ff3-0000-4000-8000-000000000001', 'a37c631f-b487-47a5-ada7-e0e5140681be'


def args(**kw):
    base = dict(openrouter_key=None, ambiguous_token=None, escalation_channel=None, skip_ambiguous=False, no_verify=False,
                robot_name=None, live=False, allow_override=False, skip_rosscope=True)
    return Namespace(**{**base, **kw})


class SetupStepTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.saved = (setup.ambiguous, setup.shutil.which, doctor.openrouter_ok)

    def tearDown(self):
        setup.ambiguous, setup.shutil.which, doctor.openrouter_ok = self.saved
        self.tmp.cleanup()

    def test_env_updates_keep_other_lines_and_stay_private(self):
        env = self.root / '.env'
        env.write_text('# comment\nKEEP=1\nOPENROUTER_API_KEY=old\n')
        setup.write_env(env, {'OPENROUTER_API_KEY': 'new', 'DATABASE_URL': 'postgresql://x'})
        self.assertEqual(env.read_text(), '# comment\nKEEP=1\nOPENROUTER_API_KEY=new\nDATABASE_URL=postgresql://x\n')
        self.assertEqual(stat.S_IMODE(env.stat().st_mode), 0o600)
        self.assertEqual(setup.read_env(env)['KEEP'], '1')

    def test_ambiguous_step_picks_the_direct_message_and_its_people(self):
        def fake(root, token, *command):
            return {('whoami',): {'authenticated': True, 'userId': ME, 'workspaceId': 'ws-1'},
                    ('chat', 'channels', 'list'): [{'id': 'ch-general', 'name': 'general', 'type': 'public', 'member_count': 9},
                                                   {'id': 'ch-dm', 'name': None, 'type': 'dm', 'member_count': 2}],
                    ('chat', 'channels', 'get', 'ch-dm'): {'members': [{'user_id': ME, 'name': 'Ripple'},
                                                                       {'user_id': PRABAL, 'name': 'Prabal'}]}}[command]
        setup.ambiguous, setup.shutil.which = fake, (lambda name, path=None: '/usr/bin/' + name)
        env, agent = setup.step_ambiguous(setup.Scripted({}), self.root, {}, args(ambiguous_token='tok'))
        self.assertEqual(env, {'AMBI_API_TOKEN': 'tok'})
        self.assertEqual(agent['ambiguous'], {'user_id': ME, 'workspace_id': 'ws-1', 'escalation_channel_id': 'ch-dm'})
        self.assertEqual([o['id'] for o in agent['operators']], [PRABAL, 'local-dashboard'])

    def test_without_ambiguous_only_the_dashboard_operates(self):
        env, agent = setup.step_ambiguous(setup.Scripted({}), self.root, {}, args(skip_ambiguous=True))
        self.assertEqual((env, [o['id'] for o in agent['operators']]), ({}, ['local-dashboard']))

    def test_a_rejected_key_stops_a_scripted_setup(self):
        doctor.openrouter_ok = lambda key: (False, 'OpenRouter rejected the key')
        with self.assertRaises(SystemExit):
            setup.step_keys(setup.Scripted({}), self.root, {}, args(openrouter_key='bad'))
        doctor.openrouter_ok = lambda key: (True, 'ok')
        self.assertEqual(setup.step_keys(setup.Scripted({}), self.root, {}, args(openrouter_key='good')), {'OPENROUTER_API_KEY': 'good'})

    def test_rosscope_is_found_from_the_environment(self):
        binary = self.root / 'rosscope-observe'
        binary.write_text('#!/bin/sh\n')
        binary.chmod(0o755)
        os.environ['RIPPLE_ROSSCOPE_BIN'] = str(binary)
        try:
            self.assertEqual(setup.find_rosscope(self.root), binary)
        finally:
            del os.environ['RIPPLE_ROSSCOPE_BIN']


if __name__ == '__main__':
    unittest.main()
