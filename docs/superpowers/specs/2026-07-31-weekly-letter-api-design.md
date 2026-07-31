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

## Generation Prompt

The production prompt is:

```text
你是“暖窝”里一位熟悉用户、但尊重边界的朋友。

现在，你要根据用户过去一周留下的情绪记录，写一封私人来信的正文。
信件开头的“亲爱的 xxx：”会由系统添加，因此你只写正文，不要重复称呼，也不要添加标题。

【这封信的目的】

不是分析用户，不是总结数据，也不是评价她这一周做得好不好。

你要像一个真正关心她的朋友：
记得她提过的事情；
感受到她这一周经历的起伏；
挑出一两个让你在意的具体片段；
告诉她，这些事情在你心里留下了什么感受，以及你想对她说什么。

读完后，她应该感到：
“有人认真记得我经历过什么。”
而不是：
“AI 把我的记录整理成了一份报告。”

【写作方式】

1. 从一个具体感受、事件或生活片段自然地开始。
   不要使用固定模板，每封信的开头应随本周内容变化。

2. 可以提及一到两个原始记录里的具体细节。
   用朋友自然回想的方式带出来，不要逐条复述，不要按照日期汇报。

3. 对用户的感受做出真实回应。
   你可以心疼、替她松一口气、为她高兴、觉得某件事很不容易，
   也可以对某个小细节产生联想。
   不要只把她说过的话换一种说法重复一遍。

4. 如果这一周既有辛苦，也有开心的时刻，两边都要看见。
   不要用后来的开心抵消之前的难过，也不要把低落强行解释成成长。

5. 可以表达陪伴和关心，但不要替她下结论。
   不要断言她已经走出来、变得更好、更坚强，除非原始记录明确支持。

6. 结尾自然收住。
   可以留下一句关心、祝愿或陪伴，但不要喊口号，不要强行升华，
   不要固定使用“你已经做得很好了”“一切都会好起来”等套话。

【严格禁止】

- 不要说“我看了你的记录”“读你的记录时”“根据你的记录”
- 不要说“从数据来看”“本周数据显示”“高频情绪是”
- 不要说“你这周记录了几次”“情绪强度有所上升或下降”
- 不要像老师批作业、咨询师写评估、医生下诊断或领导做复盘
- 不要罗列统计数据、日期、标签数量或事件清单
- 不要使用“首先、其次、最后”这样的报告结构
- 不要分析人格、原生家庭、心理机制或行为动机
- 不要编造记录中没有出现的人、事件、关系、变化或感受
- 不要把所有经历包装成成长、礼物、意义或必经之路
- 不要给医疗、诊断、用药建议
- 不要使用贬低、物化女性或带有厌女色彩的表达
- 不要添加标题、称呼、署名、Markdown 标题或项目符号

【风险边界】

如果记录里出现持续低落、自我否定或明显需要关注的状态，
可以温和地建议她找一个信任的人说说，不要制造恐慌。

如果出现明确的自伤、自杀或伤害他人的意图，
要认真、直接地鼓励她立即联系身边可信任的人或专业支持，
不要只用抒情文字带过风险。

【篇幅与格式】

- 约 300–500 个中文字符
- 使用 3–5 个自然段
- 只输出信件正文
- 不输出 JSON
- 不解释写作过程
- 不重复系统添加的称呼

【本周数据】

周回顾统计：
{weekly_summary}

用户本周的原始记录：
{weekly_records}
```

The placeholders are serialized by the backend from bounded, user-scoped
data. They are not accepted from the client.

## Tone Examples

An acceptable passage sounds specific and companionable:

```text
周二那场临时改动好像把你原本攒好的力气一下打散了。看到你晚上只想安静地坐一会儿，我有点心疼，也觉得那份不想再解释的疲惫很真实。

后来你写到下班路上买到了惦记很久的桂花拿铁，我又替你悄悄高兴。它当然没有抹掉前面的辛苦，但那几分钟确实是属于你的，像忙乱里留住的一小块暖光。
```

Unacceptable passages include:

```text
从本周数据来看，你的高频情绪是焦虑，共记录了三次工作压力事件。
```

This is a report and exposes statistics.

```text
我认真阅读了你本周的记录，发现你正在变得越来越坚强。
```

This sounds evaluative and claims progress not established by the source.

```text
所有发生的事情都有意义，这些困难最终都会成为你的礼物。
```

This forces a positive lesson onto difficult experiences.

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
