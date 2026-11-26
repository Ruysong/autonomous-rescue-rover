"""One in-flight request. Snapshot IDs never escape into a different plan."""
from collections import deque
import copy
import math
import queue
import threading
import time

class AISelector:
    strict = True
    def __init__(self, reasoner, mission='Find people and report observations', ttl=20,
                 clock=time.monotonic, event=None):
        self.reasoner, self.mission, self.ttl, self.clock = reasoner, mission, ttl, clock
        self.event = event or (lambda *args: None)
        self.results = queue.Queue()
        self.generation = 0
        self.busy = False
        self.closed = False
        self.status = 'AI ready'
        self.retry_at = 0.0
        self.observations = deque(maxlen=120)
        self.history = deque(maxlen=20)
        self.serial = 0
        self.last_selection = None

    def observe(self, people, now=None):
        now = self.clock() if now is None else now
        self.observations.append((now, copy.deepcopy(people)))

    def invalidate(self):
        self.generation += 1
        self.status = 'AI cancelled / holding'
        self.last_selection = None

    def close(self):
        self.closed = True
        self.invalidate()

    def choose(self, candidates, context):
        self.last_selection = None
        now = self.clock()
        while not self.results.empty():
            generation, started, snapshot, answer, error = self.results.get_nowait()
            self.busy = False
            if self.closed or generation != self.generation:
                continue
            if now-started > self.ttl:
                self.status='AI expired; reviewing again'
                continue
            pose=context['pose']; old=snapshot['pose']
            yaw=math.atan2(math.sin(pose[2]-old[2]),math.cos(pose[2]-old[2]))
            if math.hypot(pose[0]-old[0],pose[1]-old[1]) > .3 or abs(yaw) > .35:
                self.status='Pose changed; review discarded'
                continue
            if error:
                self.status='AI error: '+error[:100]; self.retry_at=now+3
                self.event('ai_error', {'error':error}); return None
            selected=answer.get('candidate_id') if isinstance(answer,dict) else None
            reason=answer.get('reason') if isinstance(answer,dict) else None
            row=next((c for c in snapshot['candidates'] if type(selected) is int and c['id']==selected),None)
            if not isinstance(reason,str) or not reason.strip() or len(reason)>400 or row is None:
                self.status='AI hold / rejected output'; self.retry_at=now+3
                self.event('ai_rejected',{'snapshot':snapshot['snapshot_id'],'answer':answer}); return None
            # Match the selected LOCATION, not a renumbered frontier ID.
            current=min(candidates,key=lambda f:math.hypot(f.x-row['x'],f.y-row['y']),default=None)
            if current is None or math.hypot(current.x-row['x'],current.y-row['y']) > .25:
                self.status='Selected frontier vanished; reviewing again'; continue
            self.status=f'AI chose F{current.id}: {reason}'
            self.history.append({'x':current.x,'y':current.y,'reason':reason})
            self.last_selection = {
                'snapshot': snapshot['snapshot_id'], 'candidate_id': current.id,
                'model_candidate_id': selected, 'candidates_reviewed': len(snapshot['candidates']),
                'x': current.x, 'y': current.y, 'reason': reason, 'seconds': now - started,
            }
            self.event('ai_selected', self.last_selection)
            return current.id
        if self.closed or self.busy or now < self.retry_at or not candidates:
            return None
        self.serial += 1
        recent=[{'age_s':round(now-t,2),'people':p} for t,p in self.observations if now-t<=5]
        snapshot={'snapshot_id':self.serial,'mission':self.mission,'pose':list(context['pose']),
            'candidates':[{'id':f.id,'x':f.x,'y':f.y,'weighted_cost':f.distance,
                           'bearing_rad':f.bearing,'relative_direction':'left' if f.bearing>.25 else 'right' if f.bearing<-.25 else 'ahead','boundary_cells':f.size,
                           'failed_attempts':context.get('attempts',{}).get(f.id,0)} for f in candidates],
            'recent_observations':recent[-10:], 'previous_choices':list(self.history),'goal_results':copy.deepcopy(context.get('goal_results',[]))}
        self.busy=True; self.status=f'Qwen reviewing {len(candidates)} candidates...'
        self.event('ai_request',snapshot)
        threading.Thread(target=self._run,args=(self.generation,now,snapshot),daemon=True).start()
        return None

    def _run(self,generation,started,snapshot):
        try: answer,error=self.reasoner(snapshot),None
        except Exception as exc: answer,error=None,str(exc)
        self.results.put((generation,started,snapshot,answer,error))
