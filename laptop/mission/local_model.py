"""Start and supervise the bundled loopback-only model server."""
import json
import os
import re
from pathlib import Path
import subprocess
import threading
import time
import urllib.request
from urllib.parse import urlparse


class LocalModelServer:
    def __init__(self,root,endpoint='http://127.0.0.1:8091',auto_start=True):
        self.root=Path(root);self.endpoint=endpoint.rstrip('/');self.auto_start=auto_start
        self.http=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.process=None;self.log=None;self.closed=threading.Event();self.lock=threading.RLock()
        self.backend='existing server';self.force_cpu=False

    def healthy(self):
        try:
            with self.http.open(self.endpoint+'/health',timeout=.7) as response:
                return response.status==200
        except (OSError,ValueError):return False

    def command(self):
        endpoint=urlparse(self.endpoint)
        if endpoint.scheme!='http' or endpoint.hostname not in ('127.0.0.1','localhost'):
            raise ValueError('Automatic startup is only supported for a local HTTP endpoint.')
        exe=self.root/'runtime/llama/llama-server.exe'
        gpu=self.root/'runtime/llama-vulkan/llama-server.exe'
        device=None
        if gpu.is_file() and not self.force_cpu:
            try:
                detected=subprocess.run([str(gpu),'--list-devices'],capture_output=True,text=True,
                    encoding='utf-8',errors='replace',timeout=10,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
                match=re.search(r'^\s*(Vulkan\d+):\s+NVIDIA',detected.stdout+'\n'+detected.stderr,re.MULTILINE)
                if match:exe=gpu;device=match.group(1)
            except (OSError,subprocess.TimeoutExpired):pass
        self.backend='NVIDIA GPU / Vulkan' if device else 'CPU'
        model=self.root/'models/Qwen3VL-2B-Instruct-Q4_K_M.gguf'
        projector=self.root/'models/mmproj-Qwen3VL-2B-Instruct-Q8_0.gguf'
        for path in (exe,model,projector):
            if not path.is_file():raise FileNotFoundError(f'Missing local AI asset: {path.name}. Run setup_environment.ps1.')
        command=[str(exe),'-m',str(model),'--mmproj',str(projector),'--alias','local-qwen3-vl-2b',
                '--host','127.0.0.1','--port',str(endpoint.port or 80),'-c','4096','-np','1',
                '-t','6','-ngl','99' if device else '0','--no-mmproj-offload',
                '--image-min-tokens','128','--image-max-tokens','512']
        if device:command+=['--device',device]
        return command

    def ensure_ready(self,timeout=45):
        with self.lock:
            if self.closed.is_set():raise RuntimeError('AI supervisor is closed.')
            if self.healthy():return {'status':'ready','started_here':False,'endpoint':self.endpoint,'backend':self.backend}
            if not self.auto_start:raise RuntimeError('Local AI is not running. Automatic startup was disabled.')
            if self.process is None or self.process.poll() is not None:
                command=self.command()
                if self.log:self.log.close()
                path=self.root/'runtime/local-model.log';path.parent.mkdir(parents=True,exist_ok=True)
                self.log=path.open('ab',buffering=0)
                self.process=subprocess.Popen(command,cwd=str(self.root),stdin=subprocess.DEVNULL,
                    stdout=self.log,stderr=subprocess.STDOUT,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        deadline=time.monotonic()+timeout
        while not self.closed.is_set() and time.monotonic()<deadline:
            if self.healthy():return {'status':'ready','started_here':True,'endpoint':self.endpoint,'backend':self.backend}
            if self.process and self.process.poll() is not None:
                if self.backend.startswith('NVIDIA') and not self.force_cpu:
                    self.force_cpu=True
                    return self.ensure_ready(timeout=timeout)
                path=self.root/'runtime/local-model.log'
                tail=path.read_bytes()[-1400:].decode('utf-8',errors='replace')
                raise RuntimeError('Local AI server exited. '+tail)
            self.closed.wait(.25)
        if self.closed.is_set():raise RuntimeError('AI startup cancelled.')
        raise TimeoutError('Local AI startup timed out. See runtime/local-model.log.')

    def close(self):
        self.closed.set()
        with self.lock:
            if self.process and self.process.poll() is None:
                self.process.terminate()
                try:self.process.wait(timeout=4)
                except subprocess.TimeoutExpired:self.process.kill();self.process.wait(timeout=2)
            if self.log:self.log.close();self.log=None
