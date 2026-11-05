import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from chatflows_checkin import run_checkin

OK_LOGIN = {'code': 0, 'data': {'access_token': 'fixture-token'}}
OK_POST = {'code': 0, 'data': {'amount': 50}}

def status(can_checkin, period='2026-11-04'):
    return {'code': 0, 'data': {'can_checkin': can_checkin, 'period_key': period}}

class FakeClient:
    def __init__(self, results):
        self.results = iter(results)
        self.calls = []
    def request(self, method, path, token=None):
        self.calls.append((method, path, token))
        return next(self.results)

class CheckinTest(unittest.TestCase):
    def test_already_checked_skips_post(self):
        c = FakeClient([OK_LOGIN, status(False)])
        self.assertEqual(run_checkin(c)['result'], 'already_checked_in')
        self.assertEqual([x[:2] for x in c.calls], [('POST','/api/v1/auth/login'),('GET','/api/checkin/status')])
    def test_checkin_then_verify(self):
        c = FakeClient([OK_LOGIN,status(True),OK_POST,status(False)])
        self.assertEqual(run_checkin(c)['result'], 'checked_in')
        self.assertEqual([x[:2] for x in c.calls], [('POST','/api/v1/auth/login'),('GET','/api/checkin/status'),('POST','/api/checkin'),('GET','/api/checkin/status')])
        self.assertTrue(all(call[2]=='fixture-token' for call in c.calls[1:]))
    def test_login_or_status_errors_fail_closed(self):
        for seq in [[{'code':401}], [OK_LOGIN, {'code':500}], [OK_LOGIN, {'code':0,'data':{}}]]:
            with self.subTest(seq=seq):
                with self.assertRaises(RuntimeError): run_checkin(FakeClient(seq))
    def test_verify_fails_if_still_unchecked(self):
        with self.assertRaises(RuntimeError): run_checkin(FakeClient([OK_LOGIN,status(True),OK_POST,status(True)]))
    def test_different_period_is_not_misreported(self):
        with self.assertRaises(RuntimeError): run_checkin(FakeClient([OK_LOGIN,status(True),OK_POST,status(False,'2026-11-05')]))
    def test_conflict_is_verified_and_treated_as_already_checked(self):
        c=FakeClient([OK_LOGIN,status(True),{'code':409},status(False)])
        self.assertEqual(run_checkin(c)['result'],'already_checked_in')

if __name__ == '__main__': unittest.main()
