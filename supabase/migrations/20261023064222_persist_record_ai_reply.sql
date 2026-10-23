-- Additive: the previous application can run unchanged during rollout/rollback.
alter table public.mood_records add column if not exists ai_reply text;
