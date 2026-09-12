import { pgTable, bigserial, text, timestamp, jsonb, index } from 'drizzle-orm/pg-core';

// Append-only journal: fact, proposal, approval, send claim and actual outcome events.
export const receipts = pgTable('receipts', {
  sequence: bigserial('sequence', { mode: 'number' }).primaryKey(),
  time: timestamp('time', { withTimezone: true, mode: 'string' }).notNull().defaultNow(),
  kind: text('kind').notNull(),
  payload: jsonb('payload').$type<Record<string, unknown>>().notNull(),
}, table => [index('receipts_kind_idx').on(table.kind)]);
