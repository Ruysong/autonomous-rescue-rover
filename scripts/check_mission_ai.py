"""Real local model smoke test with SYNTHETIC candidates. Never opens robot ports."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from laptop.mission.local_model import LocalModelServer
from laptop.mission.local_reasoner import LocalReasoner

def main():
    p=argparse.ArgumentParser();p.add_argument('--assets',type=Path);p.add_argument('--endpoint',default='http://127.0.0.1:8091');a=p.parse_args()
    server=LocalModelServer(a.assets,a.endpoint) if a.assets else None
    snapshot={'snapshot_id':1,'mission':'Find people. Prefer a repeated recent visual cue when relevant.',
        'pose':[0,0,0], 'candidates':[
            {'id':1,'x':2,'y':-1,'weighted_cost':2.0,'bearing_rad':-.5,'boundary_cells':12,'failed_attempts':0},
            {'id':2,'x':1,'y':2,'weighted_cost':2.5,'bearing_rad':1.1,'boundary_cells':24,'failed_attempts':0}],
        'recent_observations':[{'age_s':.2,'people':[{'side':'left','confidence':.9}]}]*3,
        'previous_choices':[]}
    try:
        if server: print(json.dumps(server.ensure_ready()))
        answer=LocalReasoner(a.endpoint)(snapshot)
        if type(answer.get('candidate_id')) is not int or answer['candidate_id'] not in (1,2):
            raise RuntimeError('Model held / returned an invalid candidate: '+str(answer))
        print(json.dumps({'synthetic_input':True,'robot_output':False,'answer':answer}))
    finally:
        if server:server.close()

if __name__=='__main__':main()
