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
        paste(map_image,12,90,560,490)
        if frame is not None:
            img=frame.copy();h,w=img.shape[:2];a,b,c,d=guard.config.roi
            color=(20,20,240) if guard.level=='hold' else (0,200,240)
            cv2.rectangle(img,(int(a*w),int(b*h)),(int(c*w),int(d*h)),color,2)
            paste(img,590,90,575,360)
        lines=['MAP / FRONTIER IDS','',explorer.status.note,
            'AI: '+(selector.status if selector else 'Rule baseline (LLM off)'),
            'VISION: '+(guard.level.upper()+' - '+guard.reason if vision else 'Not enabled'),
            'ROBOT: '+str(explorer.telemetry.get('safety_state','not connected')),
            'FOLLOW: '+str(explorer.telemetry.get('follow',{}).get('state','idle')),
            'Space: pause/resume | E: emergency stop | Q: quit',
            'Image zones are visual cues, not measured distance.']
        y=590
        for line in lines[2:]:
            for wrapped in textwrap.wrap(line,110) or ['']:
                if y>748:break
                cv2.putText(canvas,wrapped,(14,y),cv2.FONT_HERSHEY_SIMPLEX,.48,(230,230,230),1)
                y+=20
        return canvas
