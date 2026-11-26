import threading
import time
import unittest
from laptop.mission.ai_selector import AISelector
from laptop.navigation.planner import Frontier

class SelectorTests(unittest.TestCase):
    def candidates(self): return [Frontier(1,1.,0.,10,2.,0.),Frontier(2,0.,2.,20,3.,1.57)]
    def finish(self,s):
        deadline=time.monotonic()+2
        while s.results.empty() and time.monotonic()<deadline: time.sleep(.005)
        self.assertFalse(s.results.empty())
    def test_async_result_and_renumbered_id(self):
        release=threading.Event()
        def reason(snapshot):
            release.wait(2);return {'candidate_id':2,'reason':'Inspect the larger boundary'}
        s=AISelector(reason);cs=self.candidates()
        start=time.monotonic();self.assertIsNone(s.choose(cs,{'pose':[0,0,0]}))
        self.assertLess(time.monotonic()-start,.1)
        self.assertTrue(s.busy);release.set();self.finish(s)
        cs[1].id=8
        self.assertEqual(s.choose(cs,{'pose':[0,0,0]}),8)
        self.assertEqual(s.last_selection['model_candidate_id'], 2)
        self.assertEqual(s.last_selection['candidate_id'], 8)
    def test_invalid_id_holds_without_fallback(self):
        s=AISelector(lambda _: {'candidate_id':999,'reason':'Invalid'})
        s.choose(self.candidates(),{'pose':[0,0,0]});self.finish(s)
        self.assertIsNone(s.choose(self.candidates(),{'pose':[0,0,0]}))
        self.assertIn('rejected',s.status)
        self.assertIsNone(s.last_selection)
    def test_bool_id_rejected(self):
        s=AISelector(lambda _: {'candidate_id':True,'reason':'Invalid'})
        s.choose(self.candidates(),{'pose':[0,0,0]});self.finish(s)
        self.assertIsNone(s.choose(self.candidates(),{'pose':[0,0,0]}))
    def test_cancelled_request_never_applies(self):
        s=AISelector(lambda _: {'candidate_id':1,'reason':'Old'})
        s.choose(self.candidates(),{'pose':[0,0,0]});self.finish(s);s.close()
        self.assertIsNone(s.choose(self.candidates(),{'pose':[0,0,0]}))
    def test_expired_and_moved_pose(self):
        now=[10.]
        s=AISelector(lambda _: {'candidate_id':1,'reason':'Old'},clock=lambda:now[0],ttl=2)
        s.choose(self.candidates(),{'pose':[0,0,0]});self.finish(s);now[0]=13
        self.assertIsNone(s.choose(self.candidates(),{'pose':[0,0,0]}))
    def test_observations_and_failures_in_prompt(self):
        seen=[]
        def reason(p):seen.append(p);return {'candidate_id':1,'reason':'Recent observations'}
        s=AISelector(reason);s.observe([{'side':'left','confidence':.9}])
        s.choose(self.candidates(),{'pose':[0,0,0],'attempts':{1:2}});self.finish(s)
        self.assertEqual(seen[0]['candidates'][0]['failed_attempts'],2)
        self.assertEqual(seen[0]['recent_observations'][0]['people'][0]['side'],'left')
    def test_model_error_holds(self):
        def fail(_):raise OSError('server down')
        s=AISelector(fail);s.choose(self.candidates(),{'pose':[0,0,0]});self.finish(s)
        self.assertIsNone(s.choose(self.candidates(),{'pose':[0,0,0]}))
        self.assertIn('server down',s.status)
