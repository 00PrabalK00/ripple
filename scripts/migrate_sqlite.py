"""One-time, transactional import; leaves the original SQLite file untouched."""
import json
from pathlib import Path
import sqlite3
import sys
import os
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
from ripple.store import Store

root = Path(__file__).resolve().parents[1]
load_dotenv(root / '.env')
with sqlite3.connect(f'file:{root / "ripple.sqlite3"}?mode=ro', uri=True) as old:
    rows = [dict(sequence=s, time=t, kind=k, payload=json.loads(p)) for s,t,k,p in
            old.execute('SELECT sequence,time,kind,payload FROM receipts ORDER BY sequence')]
store = Store(os.environ['DATABASE_URL'])
try:
    print('Imported legacy receipts:', store._request(op='import_sqlite', rows=rows))
finally:
    store.close()
