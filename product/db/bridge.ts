// Fixed database RPC, internal to the edge. No caller-provided SQL.
import { createInterface } from 'node:readline';
import { drizzle } from 'drizzle-orm/node-postgres';
import { sql, and, eq } from 'drizzle-orm';
import pg from 'pg';
import { actions } from './schema.js';
const client = new pg.Client({connectionString:process.env.DATABASE_URL,
  connectionTimeoutMillis:5000,statement_timeout:5000});
await client.connect();
const db=drizzle(client);
await db.execute(sql`CREATE TABLE IF NOT EXISTS ripple_edge_actions (
 robot text NOT NULL, request text NOT NULL, incident text NOT NULL, action text NOT NULL,
 fingerprint text NOT NULL, status text NOT NULL, result jsonb, created_at timestamptz DEFAULT now(),
 UNIQUE(robot,request))`);
const input=createInterface({input:process.stdin,crlfDelay:Infinity});
for await (const line of input) {
 try {
  const r=JSON.parse(line);let result;
  if(r.op==='ping') result='postgres-drizzle';
  else if(r.op==='claim') result=await db.transaction(async tx=>{
   // Serialize across edge processes: callers cannot reset a budget through races.
   await tx.execute(sql`SELECT pg_advisory_xact_lock(hashtext(${r.robot}))`);
   const prior=await tx.select().from(actions).where(and(eq(actions.robot,r.robot),eq(actions.request,r.request)));
   if(prior.length) return {claimed:false,reason:prior[0].fingerprint===r.fingerprint?'duplicate_request':'request_id_conflict',prior:prior[0]};
   const used=await tx.select().from(actions).where(and(eq(actions.robot,r.robot),eq(actions.incident,r.incident),eq(actions.action,r.action)));
   if(used.length>=r.limit)return {claimed:false,reason:'incident_budget_exhausted'};
   await tx.insert(actions).values({robot:r.robot,request:r.request,incident:r.incident,action:r.action,
    fingerprint:r.fingerprint,status:'claimed'});
   return {claimed:true};
  });
  else if(r.op==='finish') {
   const rows=await db.update(actions).set({status:r.status,result:r.result}).where(and(eq(actions.robot,r.robot),eq(actions.request,r.request),eq(actions.status,'claimed'))).returning();
   result={updated:rows.length===1};
  } else if(r.op==='interrupt') {
   const rows=await db.update(actions).set({status:'interrupted',result:{reason:'Edge restarted; no automatic replay'}})
    .where(and(eq(actions.robot,r.robot),eq(actions.status,'claimed'))).returning();
   result={interrupted:rows.length};
  } else throw Error('Unsupported operation');
  process.stdout.write(JSON.stringify({ok:true,result})+'\n');
 } catch {process.stdout.write(JSON.stringify({ok:false,error:'Database operation failed'})+'\n');}
}
await client.end();
