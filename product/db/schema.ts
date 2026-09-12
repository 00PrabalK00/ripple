import { pgTable, text, jsonb, timestamp, uniqueIndex } from 'drizzle-orm/pg-core';
export const actions = pgTable('ripple_edge_actions', {
  robot: text('robot').notNull(), request: text('request').notNull(),
  incident: text('incident').notNull(), action: text('action').notNull(),
  fingerprint: text('fingerprint').notNull(), status: text('status').notNull(),
  result: jsonb('result'), createdAt: timestamp('created_at',{withTimezone:true}).defaultNow(),
}, t => [uniqueIndex('ripple_edge_actions_request').on(t.robot,t.request)]);
