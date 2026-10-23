"""Regression coverage for Hive's public login configuration formats."""

import importlib.util
import pathlib
import unittest
from unittest.mock import Mock, patch


ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('hive_setup', ROOT / 'hive-setup.py')
hive = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(hive)


class HiveConfigTests(unittest.TestCase):
    def test_config_formats(self):
        for page in (
            'window.HiveSSOPoolId="eu-west-1_test";'
            'window.HiveSSOPublicCognitoClientId="public-client";',
            "window.HiveSSOPoolId = 'eu-west-1_test'\n"
            "window.HiveSSOCognitoClientId = 'private-client'\n"
            "window.HiveSSOPublicCognitoClientId = 'public-client'",
        ):
            with self.subTest(page=page), patch.object(
                hive.requests, 'get', return_value=Mock(text=page)
            ):
                auth = hive.HiveSRPAuth('unused', 'unused')
                auth.fetch_config()
                self.assertEqual(auth.pool_id, 'eu-west-1_test')
                self.assertEqual(auth.client_id, 'public-client')
                self.assertEqual(auth.region, 'eu-west-1')

    def test_missing_config_fails_before_authentication(self):
        with patch.object(hive.requests, 'get', return_value=Mock(text='<html/>')):
            with self.assertRaisesRegex(RuntimeError, 'Cannot extract Cognito config'):
                hive.HiveSRPAuth('unused', 'unused').fetch_config()


if __name__ == '__main__':
    unittest.main()
