"""Local bounded IO for operational T5. Trace labels never contain URLs or bodies."""
import http.client
import json
import socket
import subprocess
import threading
from project import compose
from events_clock import require


class OperationalIO:
    def __init__(self,root,values,monitor):self.root=root;self.values=values;self.monitor=monitor

    def call(self,*args,label='compose',limit=1800):
        return self.monitor.perform(label,limit,lambda left:compose(self.root,self.values,*args,capture=True,timeout=left()).strip())

    def output(self,command,*,label='cgroup',timeout=30,**kwargs):
        return self.monitor.perform(label,timeout,lambda left:subprocess.check_output(command,timeout=left(),stderr=subprocess.DEVNULL,**kwargs))

    def inspect(self,cid):
        template='{"Id":{{json .Id}},"Image":{{json .Image}},"RestartCount":{{.RestartCount}},"State":{"Running":{{.State.Running}},"StartedAt":{{json .State.StartedAt}},"OOMKilled":{{.State.OOMKilled}}},"HostConfig":{"Memory":{{.HostConfig.Memory}},"MemorySwap":{{.HostConfig.MemorySwap}},"NanoCpus":{{.HostConfig.NanoCpus}}},"NetworkSettings":{"Ports":{{json .NetworkSettings.Ports}}}}'
        return json.loads(self.output(['docker','inspect','--format',template,cid],text=True,label='inspect'))

    def request(self,path,body=None,expected=200,*,limit=600):
        labels={'/health/live':'health_live','/health/ready':'health_ready','/customers':'http_customers',
                '/catalog?limit=2':'http_catalog','/recommend':'http_recommend','/events':'http_events',
                '/admin/releases/prepare':'http_prepare','/admin/releases/activate':'http_activate'}
        label=labels.get(path)
        if label is None and path.startswith('/streams/') and path.endswith('/recommend'):label='http_profile'
        if label is None and path.startswith('/admin/streams/') and path.endswith('/refresh'):label='http_refresh'
        require(label is not None)
        return self._http(label,int(self.values['API_PORT']),path,body,expected,True,limit)

    def ui_health(self):
        return self._http('ui_health',int(self.values['UI_PORT']),'/_stcore/health',None,200,False,10)

    def _http(self,label,port,path,body,expected,decode,limit):
        require(1024<=port<=65535)
        def request(left):
            connection=http.client.HTTPConnection('127.0.0.1',port,timeout=left())
            timer=None;response=None;expired=threading.Event()
            try:
                # Socket timeouts alone only bound inactivity. A fixed deadline
                # must also interrupt http.client's internal header/chunk loops.
                connection.connect();sock=connection.sock
                def cancel_read():
                    expired.set()
                    try:sock.shutdown(socket.SHUT_RDWR)
                    except OSError:pass
                timer=threading.Timer(left(),cancel_read);timer.daemon=True;timer.start()
                headers={'Content-Type':'application/json'}
                if label!='ui_health':headers['Authorization']='Bearer '+self.values['HM_APP_PASSWORD']
                connection.request('GET' if body is None else 'POST',path,
                    body=None if body is None else json.dumps(body).encode(),headers=headers)
                connection.sock.settimeout(left());response=connection.getresponse()
                require(response.status==expected) # No redirects or proxies.
                chunks=[];size=0
                while True:
                    # HTTPResponse may detach a close-delimited socket from the connection.
                    response.fp.raw._sock.settimeout(left())
                    chunk=response.read1(min(65536,2*1024**2+1-size))
                    if not chunk:break
                    chunks.append(chunk);size+=len(chunk)
                    require(size<=2*1024**2)
                    if response.isclosed():break
                raw=b''.join(chunks)
                return json.loads(raw) if decode else raw.decode()
            except Exception as error:
                if expired.is_set() or isinstance(error,socket.timeout):
                    raise TimeoutError('HTTP deadline') from None
                raise
            finally:
                if timer is not None:
                    timer.cancel();timer.join(timeout=1)
                if response is not None:response.close()
                connection.close()
                if timer is not None:require(not timer.is_alive())
        return self.monitor.perform(label,min(600,limit),request)
