from pathlib import Path
import subprocess
import unittest

from scripts.check_public_tree import violations
from smart_home_agent.integrations.google_sheets import valid_service_account_info


class PublicationTests(unittest.TestCase):
    def test_local_google_account_can_replace_original_account(self):
        account = {'type': 'service_account', 'client_email': 'operator@example.invalid',
                   'private_key': 'test-placeholder'}
        self.assertTrue(valid_service_account_info(account))
        self.assertTrue(valid_service_account_info(account, 'operator@example.invalid'))
        self.assertFalse(valid_service_account_info(account, 'other@example.invalid'))
        self.assertFalse(valid_service_account_info({**account, 'private_key': ''}))
        self.assertFalse(valid_service_account_info({**account, 'client_email': ''}))
        self.assertFalse(valid_service_account_info({**account, 'type': 'authorized_user'}))

    def test_private_runtime_files_rejected_and_templates_allowed(self):
        for name in ('.env', 'adapters/openclaw/.env', 'config/credentials.json',
                     'adapters/openclaw/config/sheet_id.txt', 'memory_backup.tar.gz'):
            self.assertTrue(violations(Path(name), ''), name)
        self.assertEqual(violations(Path('config/credentials.example.json'), '{}'), [])
        self.assertEqual(violations(Path('adapters/openclaw/.env.example'), ''), [])

    def test_runtime_paths_ignored_in_every_component(self):
        paths = ['.env', 'adapters/openclaw/.env',
                 'adapters/openclaw/openclaw.json', 'adapters/openclaw/agent.json',
                 'adapters/openclaw/config/devices_cache.json',
                 'adapters/openclaw/homebridge_accessories.json',
                 'packages/homebridge-knx-v3/node_modules/example.js']
        result = subprocess.run(['git', 'check-ignore', '--stdin'],
                                input=('\n'.join(paths)+'\n').encode(),
                                capture_output=True, check=True)
        self.assertEqual(set(paths), set(result.stdout.decode().splitlines()))
