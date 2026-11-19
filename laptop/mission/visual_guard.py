"""Image-space warning zones, NOT metric distance or a certified collision detector."""
from dataclasses import dataclass
import math
import time

@dataclass
class GuardConfig:
    roi: tuple = (.30,.55,.70,1.0)
    caution_area: float = .04
    hold_area: float = .16
    overlap: float = .25
    persistence_s: float = .3
    max_frame_age_s: float = 1.0
    clear_s: float = 1.0
    caution_speed: float = .05

    def __post_init__(self):
        a,b,c,d=self.roi
        if not (0<=a<c<=1 and 0<=b<d<=1): raise ValueError('Invalid normalized ROI')
        if not 0<self.caution_area<self.hold_area<=1: raise ValueError('Invalid box area thresholds')
        if not 0<self.overlap<=1: raise ValueError('Invalid overlap')
        for value in (self.persistence_s,self.max_frame_age_s,self.clear_s,self.caution_speed):
            if not math.isfinite(value) or value<=0: raise ValueError('Invalid guard timing/speed')

class VisualGuard:
    def __init__(self, config=None, clock=time.monotonic):
        self.config=config or GuardConfig(); self.clock=clock
        self.level='observing'; self.reason='No person in protection zone'
        self.since=None; self.clear_since=None; self.latched=False
        self.first_frame=None
        self.hold_reason=None

    def update(self, people, frame_at, forward=True):
        now=self.clock(); c=self.config
        fresh=frame_at is not None and 0 <= now-frame_at <= c.max_frame_age_s
        risky=False; caution=False
        if fresh and forward:
            a,b,d,e=c.roi
            for person in people:
                box=person.get('box',[])
                if len(box)!=4 or any(not isinstance(v,(int,float)) or not math.isfinite(v) for v in box): continue
                x1,y1,x2,y2=box
                area=max(0,x2-x1)*max(0,y2-y1)
                intersection=max(0,min(x2,d)-max(x1,a))*max(0,min(y2,e)-max(y1,b))
                overlap=intersection/max(area,1e-9)
                caution |= area>=c.caution_area and overlap>=c.overlap
                risky |= area>=c.hold_area and overlap>=c.overlap
        if risky:
            if self.since is None:self.since=now;self.first_frame=frame_at
        else:self.since=None;self.first_frame=None
        danger=not fresh or (self.since is not None and frame_at-self.first_frame>=c.persistence_s)
        if danger:
            self.latched=True
            self.hold_reason='Video stale / unavailable' if not fresh else 'Forward visual zone occupied'
        if fresh and not caution and not risky:
            if self.clear_since is None:self.clear_since=now
        else:self.clear_since=None
        self.level='hold' if self.latched else 'caution' if caution else 'observing'
        self.reason=self.hold_reason if self.latched else ('Person entering forward zone' if caution else 'Observations only')
        return self.level

    def resume(self):
        if self.clear_since is None or self.clock()-self.clear_since<self.config.clear_s:return False
        self.latched=False;self.since=None;self.hold_reason=None
        return True
