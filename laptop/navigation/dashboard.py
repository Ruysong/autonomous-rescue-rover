"""One English OpenCV dashboard for mapping, video, AI and operator controls."""
import cv2
import numpy as np
import textwrap

class Dashboard:
    def __init__(self):
        self.action=None
        self.buttons=[('pause','PAUSE'),('resume','RESUME'),('estop','E-STOP')]
    def mouse(self,event,x,y,flags,param):
        if event==cv2.EVENT_LBUTTONDOWN and 10<=y<=55:
            for i,(action,_) in enumerate(self.buttons):
                if 12+i*160<=x<162+i*160:self.action=action
    def pop_action(self):
        value,self.action=self.action,None
        return value
    def draw(self,map_image,frame,explorer,selector,guard,llm,vision):
        canvas=np.full((760,1180,3),24,np.uint8)
        for i,(action,label) in enumerate(self.buttons):
            x=12+i*160
            cv2.rectangle(canvas,(x,10),(x+150,55),(40,40,180) if action=='estop' else (70,65,55),-1)
            cv2.putText(canvas,label,(x+12,40),cv2.FONT_HERSHEY_SIMPLEX,.65,(255,255,255),2)
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
        sensors=t.get('sensors',{})
        pose=t.get('map_pose') or t.get('odom_pose') or {}
        def fmt_pose(p):
            try:return 'x %.2f  y %.2f  yaw %.0f deg'%(p['x'],p['y'],np.degrees(p['yaw']))
            except (KeyError,TypeError):return '-'
        lines=['',
            'ROBOT: '+str(t.get('safety_state','not connected'))+'  |  MODE: '+str(t.get('control_mode','-'))
                +'  |  FOLLOW: '+str(t.get('follow',{}).get('state','idle')),
            'SENSORS: '+(', '.join(f'{k} {"OK" if v else "--"}' for k,v in sensors.items()) or '-')
                +'  |  POSE (map): '+fmt_pose(pose),
            'VISION: '+(guard.level.upper()+' - '+guard.reason if vision else 'Not enabled'),
            'AI: '+(selector.status if selector else 'Rule baseline (LLM off)'),
            'EXPLORER: '+str(explorer.status.note),
            'Space: pause/resume | E: emergency stop | M: manual | Q: quit   (image zones are visual cues, not distance)']
        y=550
        for line in lines[1:]:
            for wrapped in textwrap.wrap(line,110) or ['']:
                if y>748:break
                cv2.putText(canvas,wrapped,(14,y),cv2.FONT_HERSHEY_SIMPLEX,.48,(230,230,230),1)
                y+=20
        return canvas
