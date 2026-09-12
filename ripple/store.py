"""PostgreSQL journal through Drizzle, with an explicit in-memory test adapter."""
import json
import os
from pathlib import Path
import select
import subprocess
from datetime import datetime, timezone


class Store:
    def __init__(self, url=':memory:'):
        self.rows = []
        self.process = None
        self.failed = False
        if url == ':memory:':
            return
        if not url.startswith(('postgresql://', 'postgres://')):
            raise ValueError('Production persistence requires a PostgreSQL DATABASE_URL')
        root = Path(__file__).resolve().parents[1]
        env = dict(os.environ, DATABASE_URL=url, DOTENV_CONFIG_QUIET='true')
        self.process = subprocess.Popen(
            [str(root / 'node_modules/.bin/tsx'), str(root / 'db/bridge.ts')],
            cwd=root, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, bufsize=1)
        try:
            self._request(op='ping')
        except Exception:
            self.close()
            raise

    def _request(self, **request):
        if self.failed:
            raise RuntimeError('Database session is uncertain; restart and reconcile before dispatch')
        try:
            self.process.stdin.write(json.dumps(request, allow_nan=False) + '\n')
            self.process.stdin.flush()
            ready, _, _ = select.select([self.process.stdout], [], [], 8)
            if not ready:
                raise TimeoutError('PostgreSQL acknowledgement timed out; dispatch must remain held')
            line = self.process.stdout.readline()
            if not line:
                raise RuntimeError('Drizzle database process disconnected')
            response = json.loads(line)
            if not response.get('ok'):
                raise RuntimeError('PostgreSQL operation failed; dispatch must remain held')
            return response['result']
        except Exception as error:
            self.failed = True
            raise RuntimeError('Drizzle database connection failed; session requires reconciliation') from error

    def append(self, kind, **payload):
        time = datetime.now(timezone.utc).isoformat()
        if self.process:
            return self._request(op='append', kind=kind, time=time, payload=payload)
        row = json.loads(json.dumps(dict(payload, sequence=len(self.rows)+1,
                                        time=time, kind=kind), allow_nan=False))
        self.rows.append(row)
        return row['sequence']

    def receipts(self):
        if self.process:
            return self._request(op='receipts')
        return json.loads(json.dumps(self.rows))

    def close(self):
        if self.process:
            try:
                self.process.stdin.close()
            except BrokenPipeError:
                pass
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                self.process.wait(timeout=3)
            self.process.stdout.close()
            self.process = None
