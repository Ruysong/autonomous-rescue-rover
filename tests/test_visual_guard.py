import unittest
from laptop.mission.visual_guard import VisualGuard, GuardConfig
from laptop.communication.messages import make_protection, encode_message, decode_message, ProtocolError

class GuardTests(unittest.TestCase):
    def setUp(self):self.now=10.;self.g=VisualGuard(clock=lambda:self.now)
    def observe(self,box):return self.g.update([{'box':box}],self.now)
    def test_small_side_detection_does_not_stop(self):
        for _ in range(10):
            self.assertEqual(self.observe([.02,.7,.10,.95]),'observing');self.now+=.1
    def test_caution_then_hold_requires_repeat_time(self):
        self.assertEqual(self.observe([.35,.5,.65,1.]),'caution')
        self.assertEqual(self.observe([.3,.4,.7,1.]),'caution')
        self.now+=.31
        self.assertEqual(self.observe([.3,.4,.7,1.]),'hold')
    def test_large_side_detection_keeps_driving(self):
        for _ in range(5):
            self.assertEqual(self.observe([.02,.35,.33,1.]),'observing')
            self.now+=.12
    def test_one_frozen_frame_cannot_confirm_hold(self):
        box=[{'box':[.3,.4,.7,1.]}]
        self.g.update(box,10.);self.now+=.4
        self.assertNotEqual(self.g.update(box,10.),'hold')
    def test_stale_video_holds_and_explicit_clear_resume(self):
        self.assertEqual(self.g.update([],None),'hold')
        self.g.update([],self.now);self.assertFalse(self.g.resume())
        self.now+=.5;self.g.update([],self.now)
        self.now+=.6;self.g.update([],self.now)
        self.assertTrue(self.g.resume());self.assertFalse(self.g.latched)
    def test_frozen_clear_frame_does_not_unlock_hold(self):
        self.g.update([],None)
        self.g.update([],self.now)
        self.now += 1.0
        self.g.update([],self.now-1.0)
        self.assertFalse(self.g.resume())
    def test_reversing_does_not_use_forward_roi(self):
        self.g.update([{'box':[.3,.4,.7,1]}],self.now,False)
        self.now+=.4
        self.assertEqual(self.g.update([{'box':[.3,.4,.7,1]}],self.now,False),'observing')
    def test_config_rejects_bad_zone(self):
        with self.assertRaises(ValueError):GuardConfig(roi=[.7,0,.3,1])
    def test_protection_protocol(self):
        self.assertEqual(decode_message(encode_message(make_protection(True,.05)))['hold'],True)
        for hold,speed in [(1,.1),(False,-1),(True,float('nan'))]:
            with self.assertRaises(ProtocolError):make_protection(hold,speed)
