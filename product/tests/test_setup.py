import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from ripple_edge.contracts import Profile

HERE = Path(__file__).resolve().parents[1]


class SetupTests(unittest.TestCase):
    def test_non_interactive_setup_writes_a_valid_site_config_and_private_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, ws = Path(tmp, 'ripple'), Path(tmp, 'robot_ws')
            root.mkdir()
            (ws / 'src/bringup/config').mkdir(parents=True)
            (root / '.env').write_text('KEEP_ME=1\n')
            (ws / 'src/bringup/package.xml').write_text('<package><name>bringup</name></package>')
            (ws / 'src/bringup/config/twist_mux.yaml').write_text(
                'twist_mux:\n  ros__parameters:\n    topics:\n      navigation: {topic: cmd_vel, timeout: 0.5, priority: 10}\n')
            (ws / 'src/bringup/config/places.yaml').write_text('dock: {x: 0.0, y: 0.0, yaw: 0.0}\nbay: {x: 4.0, y: 1.0, yaw: 1.57}\n')
            env = {**os.environ, 'PYTHONPATH': f"{HERE / 'agent'}:{HERE / 'ripple_edge'}:{os.environ.get('PYTHONPATH', '')}",
                   'PATH': '/usr/bin:/bin'}  # no npx or docker: Ambiguous and the database start are skipped cleanly
            out = subprocess.run([sys.executable, '-m', 'ripple_agent.setup', '--root', str(root), '--workspace', str(ws),
                                  '--non-interactive', '--no-live', '--no-verify', '--no-doctor', '--skip-rosscope',
                                  '--openrouter-key', 'sk-test', '--robot-name', 'test_bot'],
                                 env=env, capture_output=True, text=True, timeout=120)
            self.assertEqual(out.returncode, 0, out.stderr[-800:])
            site = json.loads((ws / 'ripple.json').read_text())
            profile = Profile.model_validate(site['profile'])
            self.assertEqual(profile.robot, 'test_bot')
            self.assertEqual(set(profile.stations), {'dock', 'bay'})
            self.assertFalse(profile.safety.required)
            self.assertEqual(profile.recovery.escape.autonomy, 'off')
            self.assertEqual(site['agent']['operators'], [{'id': 'local-dashboard', 'name': 'Local operator', 'via': 'dashboard'}])
            env_text = (root / '.env').read_text()
            self.assertIn('KEEP_ME=1', env_text)
            self.assertIn('OPENROUTER_API_KEY=sk-test', env_text)
            self.assertIn('DATABASE_URL=postgresql://ripple:', env_text)
            self.assertEqual(stat.S_IMODE((root / '.env').stat().st_mode), 0o600)


if __name__ == '__main__':
    unittest.main()
