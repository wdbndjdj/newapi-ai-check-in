import pathlib, sys, unittest
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
import apicasa_checkin as a
OK={"code":0,"data":{"access_token":"fixture-token"}}
def status(checked,day="2026-11-17",reward=5):return {"code":0,"data":{"checked_in":checked,"date":day,"reward":reward}}
class Fake:
 def __init__(self,rows):self.rows=iter(rows);self.calls=[]
 def request(self,method,path,token=None):
  self.calls.append((method,path,token)); x=next(self.rows)
  if isinstance(x,Exception):raise x
  return x
class Tests(unittest.TestCase):
 def test_already(self):
  c=Fake([OK,status(True)]);self.assertEqual(a.checkin(c,"2026-11-17")["result"],"already_checked_in");self.assertEqual(len(c.calls),2)
 def test_new(self):
  c=Fake([OK,status(False),{"code":0},status(True)]);self.assertEqual(a.checkin(c,"2026-11-17")["result"],"checked_in");self.assertEqual(sum(x[0]=="POST" and x[1].endswith("checkin") for x in c.calls),1)
 def test_uncertain_post_verify_no_retry(self):
  c=Fake([OK,status(False),a.Transient(),status(True)]);self.assertEqual(a.checkin(c,"2026-11-17")["result"],"checked_in");self.assertEqual(len(c.calls),4)
 def test_bad_date_stops(self):
  c=Fake([OK,status(False,"2026-11-16")]);
  with self.assertRaises(a.Failure):a.checkin(c,"2026-11-17")
  self.assertEqual(len(c.calls),2)
 def test_reward_disabled_stops(self):
  c=Fake([OK,status(False,reward=0)]);
  with self.assertRaises(a.Failure):a.checkin(c,"2026-11-17")
  self.assertEqual(len(c.calls),2)
 def test_boolean_code_is_not_success(self):
  with self.assertRaises(a.Failure):a.checkin(Fake([{"code":False,"data":{"access_token":"fixture"}}]),"2026-11-17")
 def test_unbounded_reward_rejected(self):
  with self.assertRaises(a.Failure):a.checkin(Fake([OK,status(False,reward=10**309)]),"2026-11-17")
 def test_bad_login_stops(self):
  with self.assertRaises(a.Failure):a.checkin(Fake([{"code":1}]),"2026-11-17")
 def test_malformed_stops(self):
  with self.assertRaises(a.Failure):a.checkin(Fake([OK,{"code":0,"data":{}}]),"2026-11-17")
if __name__=="__main__":unittest.main()
