"""One English OpenCV dashboard for mapping, video, AI and operator controls."""
import cv2
import numpy as np
import textwrap

from laptop.teleop.controller import KEYMAP

class Dashboard:
    def __init__(self):
        self.action=None
        self.buttons=[('pause','PAUSE'),('resume','START / AUTO'),('estop','E-STOP'),('manual','MANUAL')]
    def mouse(self,event,x,y,flags,param):
        if event==cv2.EVENT_LBUTTONDOWN and 10<=y<=55:
            for i,(action,_) in enumerate(self.buttons):
                if 12+i*160<=x<162+i*160:self.action=action
    def pop_action(self):
        value,self.action=self.action,None
        return value
    def draw(self,map_image,frame,explorer,selector,guard,llm,vision,mission=None):
        canvas=np.full((860,1180,3),24,np.uint8)
        for i,(action,label) in enumerate(self.buttons):
            x=12+i*160
            cv2.rectangle(canvas,(x,10),(x+150,55),(40,40,180) if action=='estop' else (70,65,55),-1)
            cv2.putText(canvas,label,(x+12,40),cv2.FONT_HERSHEY_SIMPLEX,.59,(255,255,255),2)
        if mission:
            mode=mission["sensor_mode"].replace("_"," ")
            if mission["rehearsal"]: mode="OFFLINE  |  "+mode
            color=(95,215,255) if mission["sensor_mode"]!="VISION_SEARCH" else (150,235,160)
            cv2.putText(canvas,mode,(658,43),cv2.FONT_HERSHEY_SIMPLEX,.7,color,2)
            cv2.putText(canvas,mission["sensor_reason"][:112],(14,78),cv2.FONT_HERSHEY_SIMPLEX,.52,color,1)
        def paste(image,x,y,w,h):
            if image is None:return
            ih,iw=image.shape[:2];k=min(w/iw,h/ih)
            out=cv2.resize(image,(max(1,int(iw*k)),max(1,int(ih*k))))
            canvas[y:y+out.shape[0],x:x+out.shape[1]]=out
        # Layout (team 11/19): camera + YOLO top left, SLAM map top right, rover status below.
        if frame is not None:
            img=frame.copy();h,w=img.shape[:2];a,b,c,d=guard.config.roi
            color=(20,20,240) if guard.level=='hold' else (0,200,240)
            cv2.rectangle(img,(int(a*w),int(b*h)),(int(c*w),int(d*h)),color,2)
            paste(img,12,90,575,430)
        else:
            cv2.putText(canvas,'CAMERA: '+('waiting for video' if vision else 'not enabled (--person-source)'),
                        (20,300),cv2.FONT_HERSHEY_SIMPLEX,.55,(160,160,160),1)
        paste(map_image,600,90,568,430)
        t=explorer.telemetry
        rover_lines,sensor_lines=status_columns(t)
        font=cv2.FONT_HERSHEY_SIMPLEX
        for x,title,rows in ((14,'ROVER',rover_lines),(600,'SENSORS',sensor_lines)):
            cv2.putText(canvas,title,(x,548),font,.5,(150,150,150),1)
            for i,row in enumerate(rows):
                cv2.putText(canvas,row,(x,572+i*20),font,.48,(230,230,230),1)
        alert=mission.get("person_alert") if mission else None
        if alert:
            cv2.rectangle(canvas,(12,680),(587,772),(55,88,43),-1)
            cv2.putText(canvas,f"PERSON FOUND  /  TRACK {alert['track_id']}",(23,713),font,.66,(220,255,214),2)
            label=alert["posture"].replace("_"," ").upper()
            cv2.putText(canvas,f"{label}   |   {alert['image_side'].upper()}   |   {alert['confidence']:.0%}",
                        (23,745),font,.53,(220,255,214),1)
        # Gamepad help while driving by hand (11/19): one stick like a driving game.
        if t.get('control_mode')=='manual':
            cv2.rectangle(canvas,(596,660),(1168,792),(48,48,48),-1)
            cv2.putText(canvas,'MANUAL - GAMEPAD',(606,680),font,.5,(120,200,255),1)
            for i,(key,action) in enumerate(KEYMAP):
                cv2.putText(canvas,key,(606,700+i*16),font,.42,(255,255,255),1)
                cv2.putText(canvas,action,(726,700+i*16),font,.42,(210,210,210),1)
        lines=['VISION: '+(guard.level.upper()+' - '+guard.reason if vision else 'Not enabled'),
            'AI: '+(selector.status if selector else 'Rule baseline (LLM off)')+'   |   EXPLORER: '+str(explorer.status.note),
            'Space: pause/resume | E: emergency stop | M: manual | Q: quit   (image zones are visual cues, not distance)']
        y=800
        for line in lines:
            for wrapped in textwrap.wrap(line,110) or ['']:
                if y>852:break
                cv2.putText(canvas,wrapped,(14,y),font,.48,(230,230,230),1)
                y+=20
        return canvas


PICO_FLAGS=((1,'link timeout'),(2,'E-stop'),(4,'clamped'),(8,'no command yet'))


def _num(values,fmt,n):
    try:return [fmt%v for v in values][:n] if len(values)>=n else None
    except TypeError:return None


def status_columns(t):
    """Telemetry -> (rover rows, sensor rows) for the status panel. '-' when missing."""
    if not t:
        return ['not connected'],['-']
    pose=t.get('map_pose') or t.get('odom_pose')
    try:pose_text='x %.2f m  y %.2f m  yaw %.0f deg'%(pose[0],pose[1],np.degrees(pose[2]))
    except (TypeError,IndexError,KeyError):pose_text='-'
    duty=_num(t.get('duty',[]),'%+.2f',2)
    steer=_num(t.get('steer_deg',[]),'%+.0f',4)
    pico=t.get('pico',{})
    flags=int(pico.get('flags',0) or 0)
    flag_text=', '.join(name for bit,name in PICO_FLAGS if flags&bit) or 'normal'
    rover=['STATE '+str(t.get('safety_state','-'))+'   MODE '+str(t.get('control_mode','-'))
            +'   FOLLOW '+str(t.get('follow',{}).get('state','-')),
        'POSE (map) '+pose_text,
        'ODOM %.2f m'%float(t.get('odom_distance',0.0) or 0.0)
            +'   DUTY L/R '+('/'.join(duty) if duty else '-'),
        'STEER FL FR RL RR '+(' '.join(steer) if steer else '-')+' deg   ('+str(t.get('steer_mode','-'))+')',
        'PICO '+('OK' if pico.get('ok') else '--')+'  flags '+str(flags)+' ('+flag_text+')']
    sensors=t.get('sensors',{})
    sonar=t.get('ultrasonic_cm',[])
    sonar_text='  '.join(f'{name} {v if isinstance(v,(int,float)) and v>=0 else "--"}'
                         for name,v in zip(('FL','FR','RL','RR'),sonar)) or '-'
    enc=t.get('encoders',[])
    sensor=['LIDAR '+('OK' if sensors.get('lidar') else '--')+'    IMU '+('OK' if sensors.get('imu') else '--'),
        'ULTRASONIC cm  '+sonar_text,
        'ENCODER L/R  '+('%d / %d'%(enc[0],enc[1]) if len(enc)>=2 else '-'),
        'CMD AGE %.2f s'%float(t.get('last_command_age',0.0) or 0.0)]
    return rover,sensor
