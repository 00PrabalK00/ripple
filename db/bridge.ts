// Private stdin/stdout RPC. Python owns sequencing; Drizzle owns every DB query.
import 'dotenv/config';
import { createInterface } from 'node:readline';
import { drizzle } from 'drizzle-orm/node-postgres';
import pg from 'pg';
import { asc, eq } from 'drizzle-orm';
import { receipts } from './schema.js';

const client = new pg.Client({ connectionString: process.env.DATABASE_URL,
  connectionTimeoutMillis: 5000, statement_timeout: 5000 });
await client.connect();
const db = drizzle(client);
const input = createInterface({ input: process.stdin, crlfDelay: Infinity });
for await (const line of input) {
  try {
    const request = JSON.parse(line);
    let result: unknown;
    if (request.op === 'append') {
      // PostgreSQL acknowledges only after this INSERT commits (synchronous_commit on).
      const rows = await db.insert(receipts).values({kind: request.kind,
        time: request.time, payload: request.payload}).returning({sequence: receipts.sequence});
      result = rows[0].sequence;
    } else if (request.op === 'receipts') {
      result = (await db.select().from(receipts).orderBy(asc(receipts.sequence)))
        .map(({payload, ...row}) => ({...payload, ...row}));
    } else if (request.op === 'import_sqlite') {
      result = await db.transaction(async tx => {
        const prior = await tx.select().from(receipts).where(eq(receipts.kind, 'sqlite_import_completed'));
        if (prior.length) return 0;
        for (const row of request.rows) {
          await tx.insert(receipts).values({kind: row.kind, time: row.time,
            payload: {...row.payload, legacy_sqlite_sequence: row.sequence}});
        }
        await tx.insert(receipts).values({kind: 'sqlite_import_completed',
          payload: {count: request.rows.length}});
        return request.rows.length;
      });
    } else if (request.op === 'ping') {
      result = 'postgres-drizzle';
    } else { throw new Error('Unsupported database operation'); }
    process.stdout.write(JSON.stringify({ok: true, result}) + '\n');
  } catch (error) {
    // Do not expose driver connection strings, queries or credentials through the panel.
    process.stdout.write(JSON.stringify({ok: false, error: 'Database operation failed'}) + '\n');
  }
}
await client.end();
