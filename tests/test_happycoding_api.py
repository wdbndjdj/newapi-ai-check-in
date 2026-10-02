import importlib.util
import pathlib
import unittest
from unittest.mock import patch


ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('happycoding_checkin', ROOT / 'happycoding_checkin.py')
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)


class HappyCodingTest(unittest.TestCase):
    def test_already_checked_in_does_not_post(self):
        replies = [
            {'success': True, 'data': {'id': 2806}},
            {'success': True, 'data': {'stats': {'checked_in_today': True}}},
        ]
        with patch.object(app, 'request_json', side_effect=replies) as api:
            result = app.checkin('fixture-token')
        self.assertEqual(result['result'], 'already_checked_in')
        self.assertEqual(api.call_count, 2)
        self.assertTrue(all(c.kwargs.get('method', 'GET') == 'GET' for c in api.call_args_list))

    def test_new_checkin_is_verified(self):
        replies = [
            {'success': True, 'data': {'id': 2806}},
            {'success': True, 'data': {'stats': {'checked_in_today': False}}},
            {'success': True, 'data': {'quota_awarded': 2500000}},
            {'success': True, 'data': {'stats': {'checked_in_today': True}}},
        ]
        with patch.object(app, 'request_json', side_effect=replies) as api:
            result = app.checkin('fixture-token')
        self.assertEqual(result['result'], 'checked_in')
        self.assertEqual(result['quota_awarded'], 2500000)
        self.assertEqual(api.call_args_list[2].kwargs['method'], 'POST')
        self.assertEqual(api.call_args_list[2].kwargs['user_id'], 2806)

    def test_failed_verification_is_not_reported_success(self):
        replies = [
            {'success': True, 'data': {'id': 2806}},
            {'success': True, 'data': {'stats': {'checked_in_today': False}}},
            {'success': True},
            {'success': True, 'data': {'stats': {'checked_in_today': False}}},
        ]
        with patch.object(app, 'request_json', side_effect=replies):
            with self.assertRaises(RuntimeError):
                app.checkin('fixture-token')

    def test_invalid_status_is_not_treated_as_unchecked(self):
        replies = [{'success': True, 'data': {'id': 2806}}, {'success': False}]
        with patch.object(app, 'request_json', side_effect=replies) as api:
            with self.assertRaises(RuntimeError):
                app.checkin('fixture-token')
        self.assertEqual(api.call_count, 2)

    def test_missing_token_fails_before_network(self):
        with patch.object(app, 'request_json') as api:
            with self.assertRaises(ValueError):
                app.checkin('')
        api.assert_not_called()


if __name__ == '__main__':
    unittest.main()
