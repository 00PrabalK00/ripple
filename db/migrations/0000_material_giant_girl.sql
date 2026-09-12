CREATE TABLE "receipts" (
	"sequence" bigserial PRIMARY KEY NOT NULL,
	"time" timestamp with time zone DEFAULT now() NOT NULL,
	"kind" text NOT NULL,
	"payload" jsonb NOT NULL
);
--> statement-breakpoint
CREATE INDEX "receipts_kind_idx" ON "receipts" USING btree ("kind");