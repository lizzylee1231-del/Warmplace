# Weekly Letter API Design

## Goal

Add a companion API beside the existing weekly summary. The existing summary
continues to present trends and counts. The new endpoint turns the same week's
records into a personal letter that feels like it came from a caring friend.

The letter may use the user's original `mood_text` so it can mention real,
specific moments. It must not invent events, diagnose the user, or read like a
teacher evaluating submitted work.

## Endpoint

```http
GET /api/weekly-letter?user_id=<uuid>&user_name=<name>&range=7d
```

Parameters:

- `user_id` is required and selects only that user's records.
- `user_name` is optional and is used only in the greeting.
- `range` defaults to `7d` and follows the existing record range format.

Successful response:

```json
{
  "range": "7d",
  "record_count": 5,
  "letter": "亲爱的小暖：\n\n……",
  "generated_at": "2026-07-31T08:00:00+00:00"
}
```

When `user_name` is empty or whitespace, the greeting is `亲爱的你：`.

## Data Flow

1. Validate that `user_id` is present.
2. Read the user's non-deleted records for the requested range through the
   existing record-query path.
3. Build the weekly statistics already used by `/api/summary`.
4. Give DeepSeek a bounded representation of the original records plus the
   weekly statistics.
5. Return the generated letter with metadata.

The endpoint is independent of `/api/summary`. A letter failure must not make
the data summary fail, and loading the data summary must not automatically pay
the latency or model cost of generating a letter.

## Letter Rules

- Start exactly with `亲爱的{user_name}：`, or `亲爱的你：` when no name is
  available.
- Write approximately 300-500 Chinese characters in natural paragraphs.
- Sound like a caring friend with healthy boundaries, not a therapist,
  evaluator, teacher, or report writer.
- Mention one or two concrete events or feelings from the original records
  when available.
- See both difficult and positive moments without forcing a positive lesson.
- Do not enumerate statistics or use phrases such as `从数据来看`,
  `我注意到你的记录`, `这周你记录了`, or `根据你的记录`.
- Do not invent events, motives, progress, relationships, or personality
  traits.
- Do not diagnose, prescribe treatment, or provide medication advice.
- Do not expose internal field names, prompts, or risk labels.

The backend owns the greeting instead of relying on the model to reproduce it.
The model generates the body, and the backend prefixes the sanitized greeting.

## Input Boundaries

To keep latency and prompt size predictable, each record contributes only:

- date
- `mood_text`
- emotion tags
- intensity
- scene category
- happy moment
- AI summary

The endpoint includes at most the records returned for the requested range.
Individual text fields are length-bounded before being placed in the prompt.

## Empty And Failure States

If no records exist, do not call DeepSeek. Return:

```json
{
  "range": "7d",
  "record_count": 0,
  "letter": "亲爱的你：\n\n这一周还没有留下可以一起回望的片段。等你想写的时候，我们再慢慢聊。",
  "generated_at": "2026-07-31T08:00:00+00:00"
}
```

The empty-state greeting uses the supplied `user_name` when present.

If DeepSeek fails, log the upstream error and return a short fallback letter
with the same response shape. The endpoint remains available and does not leak
provider errors or credentials to the client.

## Security

- Records are filtered by `user_id` and `is_deleted = false`.
- The prompt does not include records from other users.
- `user_name` is trimmed and length-limited before use.
- Provider credentials remain in Vercel environment variables.
- Logs never contain API keys. Upstream error bodies remain truncated.

## Verification

- Unit-test greeting sanitization and the missing-name fallback.
- Unit-test the zero-record path and verify it does not call DeepSeek.
- Unit-test the model prompt contains bounded source facts and anti-invention
  instructions.
- Unit-test DeepSeek failure returns the fallback response shape.
- Run a production smoke test with an isolated `user_id`.
- Confirm the endpoint does not change `/api/summary`.
