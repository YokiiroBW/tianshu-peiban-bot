-- Queueing/render ordering has no implicit business expiry. Preserve every
-- existing plan's deadline; only new ordinary plans can omit it.
ALTER TABLE conversation.expression_plans ALTER COLUMN deadline_at DROP NOT NULL;
