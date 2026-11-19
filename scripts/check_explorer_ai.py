"""Real Qwen + real Explorer + synthetic LiDAR; no robot ports or motor output."""
import argparse
import sys
import time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tests'))
from test_navigation import cast
from laptop.navigation.slam import ScanSlam
from laptop.navigation.explorer import Explorer
from laptop.mission.ai_selector import AISelector
from laptop.mission.local_model import LocalModelServer
from laptop.mission.local_reasoner import LocalReasoner

def main():
    p=argparse.ArgumentParser();p.add_argument('--assets',type=Path,required=True);a=p.parse_args()
    server=LocalModelServer(a.assets)
    sent=[]; selector=AISelector(LocalReasoner(),event=lambda k,d:print(k,d))
    ex=Explorer(ScanSlam(),lambda m: sent.append(m) or True,selector=selector)
    try:
        server.ensure_ready()
        for _ in range(3): ex.on_scan({'odom_pose':[0,0,0],'ranges_mm':cast((0,0,0))})
        ex.start();deadline=time.monotonic()+25
        while time.monotonic()<deadline:
            ex.on_telemetry({'control_mode':'autonomous','estop':False,'follow':{'state':'idle','path_id':-1}})
            ex.tick()
            paths=[m for m in sent if m['type']=='path' and m['points']]
            if paths:
                print('PASS: real Qwen -> current frontier -> validated map path',paths[-1]);return
            time.sleep(.1)
        raise RuntimeError(selector.status)
    finally:selector.close();server.close()

if __name__=='__main__':main()
