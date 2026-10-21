"""Container-local client; administrative secret never appears in command arguments."""
import os
import httpx


def command(action, *, directory=None, release_id=None):
    body = {'directory':directory} if action=='prepare' else {'release_id':release_id} if action=='activate' else {}
    with httpx.Client(base_url='http://127.0.0.1:8000',trust_env=False,timeout=300) as client:
        response=client.post('/admin/releases/'+action,json=body,
            headers={'Authorization':'Bearer '+os.environ['HM_APP_PASSWORD']})
        if response.status_code!=200:
            raise ValueError('Administrative command failed (HTTP '+str(response.status_code)+')')
        return response.json()
