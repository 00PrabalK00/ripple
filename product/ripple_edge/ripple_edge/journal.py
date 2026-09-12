"""PostgreSQL/Drizzle action claims. An uncertain acknowledgement fails closed."""
import json
import os
from pathlib import Path
import select
import subprocess
import threading

class ActionJournal:
    def __init__(self, database_url, root):
        self.lock=threading.Lock()
        self.failed=False
        root=Path(root)
        self.process=subprocess.Popen([str(root/'node_modules/.bin/tsx'),str(root/'product/db/bridge.ts')],
            cwd=root,env=dict(os.environ,DATABASE_URL=database_url),stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,bufsize=1)
        try:self.request(op='ping')
        except Exception:
            self.close()
            raise

    def request(self, **data):
        with self.lock:
            if self.failed:raise RuntimeError('Journal acknowledgement uncertain; hold and reconcile')
            try:
                self.process.stdin.write(json.dumps(data,allow_nan=False)+'\n')
                self.process.stdin.flush()
                ready,_,_=select.select([self.process.stdout],[],[],8)
                if not ready:raise TimeoutError()
                reply=json.loads(self.process.stdout.readline())
                if not reply.get('ok'):raise RuntimeError()
                return reply['result']
            except Exception as exc:
                self.failed=True
                raise RuntimeError('Journal unavailable; execution denied') from exc

    def close(self):
        if self.process.poll() is None:
            self.process.stdin.close()
            try:self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill();self.process.wait()
        self.process.stdout.close()
