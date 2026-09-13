import { pgTable, text, jsonb, timestamp, uniqueIndex, primaryKey } from 'drizzle-orm/pg-core';
// Site and incident memory: areas, keepouts, station state, incidents, timeline, conversations.
export const memory = pgTable('ripple_memory', {
  kind: text('kind').notNull(), id: text('id').notNull(), robot: text('robot').notNull(),
  data: jsonb('data').notNull(), updatedAt: timestamp('updated_at',{withTimezone:true}).defaultNow(),
}, t => [primaryKey({columns:[t.kind,t.id,t.robot]})]);
export const actions = pgTable('ripple_edge_actions', {
  robot: text('robot').notNull(), request: text('request').notNull(),
  incident: text('incident').notNull(), action: text('action').notNull(),
  fingerprint: text('fingerprint').notNull(), status: text('status').notNull(),
  result: jsonb('result'), createdAt: timestamp('created_at',{withTimezone:true}).defaultNow(),
}, t => [uniqueIndex('ripple_edge_actions_request').on(t.robot,t.request)]);
