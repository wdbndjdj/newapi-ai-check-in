import sys, pathlib, unittest, hashlib
from unittest.mock import patch
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
import runanytime_checkin as m
class Tests(unittest.TestCase):
 def test_pow_valid(self):
  nonce=m.solve_pow("fixture",12,timeout=4)
  self.assertEqual(int.from_bytes(hashlib.sha256(("fixture"+nonce).encode()).digest(),"big") >> (256-12),0)
  self.assertEqual(len(nonce),8)
 def test_pow_rejects_invalid(self):
  for n in [-1,27,True]:
   with self.assertRaises(ValueError): m.solve_pow("x",n)
 def test_checked_skips_pow_and_post(self):
  with patch.object(m,"request",side_effect=[{"success":True,"data":{"id":3}},{"success":True,"data":{"stats":{"checked_in_today":True,"records":[{"checkin_date":"2026-11-11","quota_awarded":1}]}}}]) as r, patch.object(m,"day_string",return_value="2026-11-11"):
   self.assertEqual(m.checkin("fixture")["result"],"already_checked_in"); self.assertEqual(r.call_count,2)
 def test_checkin_with_pow_and_verify(self):
  absent={"success":True,"data":{"stats":{"checked_in_today":False,"records":[]}}}; checked={"success":True,"data":{"stats":{"checked_in_today":True,"records":[{"checkin_date":"2026-11-11","quota_awarded":1}]}}}; status={"success":True,"data":{"checkin_enabled":True,"pow_enabled":True,"pow_mode":"replace"}}; challenge={"success":True,"data":{"challenge_id":"abc","prefix":"fix","difficulty":0}}
  with patch.object(m,"request",side_effect=[{"success":True,"data":{"id":3}},absent,status,challenge,{"success":True},checked]) as r, patch.object(m,"day_string",return_value="2026-11-11"):
   self.assertEqual(m.checkin("fixture")["result"],"checked_in"); self.assertEqual(r.call_count,6); self.assertIn("pow_challenge=abc",r.call_args_list[4].args[0])
 def test_uncertain_post_queries_once_without_retry(self):
  absent={"success":True,"data":{"stats":{"checked_in_today":False,"records":[]}}}; checked={"success":True,"data":{"stats":{"checked_in_today":True,"records":[{"checkin_date":"2026-11-11","quota_awarded":1}]}}}; status={"success":True,"data":{"checkin_enabled":True,"pow_enabled":True,"pow_mode":"replace"}}; challenge={"success":True,"data":{"challenge_id":"abc","prefix":"fix","difficulty":0}}
  with patch.object(m,"request",side_effect=[{"success":True,"data":{"id":3}},absent,status,challenge,m.Failure("network or response failure"),checked]) as r,patch.object(m,"day_string",return_value="2026-11-11"):
   self.assertEqual(m.checkin("fixture")["result"],"checked_in"); self.assertEqual(sum(c.kwargs.get("method")=="POST" for c in r.call_args_list),1)
 def test_reject_supplement_mode(self):
  absent={"success":True,"data":{"stats":{"checked_in_today":False,"records":[]}}}
  with patch.object(m,"request",side_effect=[{"success":True,"data":{"id":3}},absent,{"success":True,"data":{"checkin_enabled":True,"pow_enabled":True,"pow_mode":"supplement"}}]),patch.object(m,"day_string",return_value="2026-11-11"):
   with self.assertRaises(m.Failure):m.checkin("fixture")
 def test_challenge_stops(self):
  absent={"success":True,"data":{"stats":{"checked_in_today":False,"records":[]}}}
  with patch.object(m,"request",side_effect=[{"success":True,"data":{"id":3}},absent,{"success":True,"data":{"checkin_enabled":True,"pow_enabled":False,"turnstile_check":True}}]),patch.object(m,"day_string",return_value="2026-11-11"):
   with self.assertRaises(m.Failure):m.checkin("fixture")
if __name__=="__main__":unittest.main()
