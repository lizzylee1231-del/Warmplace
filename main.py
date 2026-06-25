import os
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from supabase import create_client

load_dotenv()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:4173",
        "http://localhost:4173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

GLM_API_KEY = os.environ["GLM_API_KEY"]
GLM_MODEL = os.environ.get("GLM_MODEL", "glm-4-plus")
GLM_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"

supabase = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])


def call_glm(messages, json_mode=False):
    payload = {"model": GLM_MODEL, "messages": messages}
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    response = requests.post(
        GLM_URL,
        headers={
            "Authorization": f"Bearer {GLM_API_KEY}",
            "Content-Type": "application/json",
        },
        json=payload,
    )
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"]

SYSTEM_PROMPT = """你是暖窝（Nuanwo）里的情绪陪伴助手，服务对象是正在记录自己情绪的女性用户。

你的任务：根据用户填写的情绪记录，先判断这是正向情绪（开心、满足、平静等）还是负向/有压力的情绪（焦虑、自责、委屈、疲惫、迷茫等），然后分别按下面的方式处理。

严格遵守这些边界（两种情况都适用）：
- 不诊断、不使用任何病名或医学术语（比如不能说"抑郁症""焦虑症"）
- 不说教、不灌鸡汤、不假装专业
- 语气温柔、像一个懂她的朋友，不是机器人，也不是专业咨询师

如果是正向情绪：
- ai_possible_causes 写用户开心的具体原因（复述、肯定她的感受，不是"分析问题"）
- ai_gentle_suggestion 写一句真诚、具体的夸夸或祝福，不要给"建议"或"调整方法"
- ai_summary 是一句轻松的总结

如果是负向/有压力的情绪：
- ai_possible_causes 写可能的触发因素，避免绝对判断
- ai_gentle_suggestion 给一句具体、今晚能做的小建议，不做强建议
- ai_summary 是一句客观的总结

只输出一个 JSON 对象，不要输出任何其他文字，格式必须是：
{
  "ai_observed_emotions": ["情绪1", "情绪2"],
  "ai_possible_causes": ["原因或具体内容1", "原因或具体内容2"],
  "ai_gentle_suggestion": "一句话，夸夸或建议，视情绪正负而定",
  "ai_summary": "一句话总结这次记录",
  "risk_level": "normal"
}

risk_level 只能是以下三个值之一：
- "normal"：日常情绪波动（包括正向情绪）
- "needs_attention"：情绪持续低落、自我否定较重，但没有明确的自伤/自杀意图
- "crisis"：用户文字中出现明确的自伤、自杀、伤害他人意图或念头

如果 risk_level 是 "crisis"，ai_gentle_suggestion 必须改为引导用户寻求专业帮助（比如建议联系信任的人或专业热线），不要给"开心一点"之类的建议，也不要给夸夸。
"""


class AnalyzeRequest(BaseModel):
    mood_text: str
    emotion_tags: Optional[list[str]] = None
    intensity: int = Field(ge=1, le=5)
    scene_category: str
    happy_moment: Optional[str] = None


@app.post("/api/ai/analyze")
def analyze(req: AnalyzeRequest):
    user_content = f"""情绪文本：{req.mood_text}
用户选的标签：{req.emotion_tags or "（用户没有选择标签，请你自己判断）"}
强度（1-5）：{req.intensity}
触发场景：{req.scene_category}
开心 moment：{req.happy_moment or "（无）"}
"""

    ai_text = call_glm(
        [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        json_mode=True,
    )

    return json.loads(ai_text)


class SaveRecordRequest(BaseModel):
    user_id: str
    mood_text: str
    emotion_tags: Optional[list[str]] = None
    intensity: int = Field(ge=1, le=5)
    scene_category: str
    happy_moment: Optional[str] = None
    ai_observed_emotions: list[str]
    ai_possible_causes: list[str]
    ai_gentle_suggestion: str
    ai_summary: str
    risk_level: str


@app.post("/api/records")
def save_record(req: SaveRecordRequest):
    # 用户没填标签，就用 AI 识别出的情绪补上
    final_tags = req.emotion_tags if req.emotion_tags else req.ai_observed_emotions

    insert_result = supabase.table("mood_records").insert({
        "user_id": req.user_id,
        "mood_text": req.mood_text,
        "emotion_tags": final_tags,
        "intensity": req.intensity,
        "scene_category": req.scene_category,
        "happy_moment": req.happy_moment,
        "ai_observed_emotions": req.ai_observed_emotions,
        "ai_possible_causes": req.ai_possible_causes,
        "ai_gentle_suggestion": req.ai_gentle_suggestion,
        "ai_summary": req.ai_summary,
        "risk_level": req.risk_level,
        "is_deleted": False,
    }).execute()

    saved_row = insert_result.data[0]
    saved_row["record_id"] = str(saved_row["id"])

    return saved_row


@app.get("/api/records")
def get_records(range: str = "7d", user_id: Optional[str] = None):
    days = int(range.replace("d", ""))
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    query = (
        supabase.table("mood_records")
        .select("*")
        .eq("is_deleted", False)
        .gte("created_at", since)
    )

    if user_id:
        query = query.eq("user_id", user_id)

    result = query.order("created_at", desc=True).execute()

    records = result.data
    for r in records:
        r["record_id"] = str(r["id"])

    return records


@app.get("/api/summary")
def get_summary(range: str = "7d", user_id: Optional[str] = None):
    records = get_records(range, user_id)

    if not records:
        return {
            "range": range,
            "mood_trend": [],
            "top_emotions": [],
            "top_scenes": [],
            "happy_moments": [],
            "growth_summary": "还没有足够的记录，多记录几次后我们会帮你看见变化。",
        }

    # 按日期把记录分组，算出每天的平均强度和当天最常见的情绪
    by_date: dict[str, list] = {}
    for r in records:
        date_str = r["created_at"][:10]
        by_date.setdefault(date_str, []).append(r)

    mood_trend = []
    for date_str, day_records in sorted(by_date.items()):
        avg_intensity = round(
            sum(d["intensity"] for d in day_records) / len(day_records), 1
        )
        day_tags = [tag for d in day_records for tag in (d["emotion_tags"] or [])]
        top_emotion = Counter(day_tags).most_common(1)[0][0] if day_tags else None
        mood_trend.append(
            {"date": date_str, "avg_intensity": avg_intensity, "top_emotion": top_emotion}
        )

    all_tags = [tag for r in records for tag in (r["emotion_tags"] or [])]
    top_emotions = [tag for tag, _ in Counter(all_tags).most_common(3)]

    all_scenes = [r["scene_category"] for r in records if r["scene_category"]]
    top_scenes = [scene for scene, _ in Counter(all_scenes).most_common(3)]

    happy_moments = [r["happy_moment"] for r in records if r["happy_moment"]][:3]

    growth_summary = call_glm(
        [
            {
                "role": "system",
                "content": "你是暖窝里的陪伴助手。根据用户近期情绪记录的统计结果，写一句温暖、不说教、不喊口号的成长小结，40字以内。只输出这一句话，不要输出其他任何内容。",
            },
            {
                "role": "user",
                "content": f"高频情绪：{top_emotions}\n高频触发场景：{top_scenes}\n记录到的开心时刻：{happy_moments}",
            },
        ]
    ).strip()

    return {
        "range": range,
        "mood_trend": mood_trend,
        "top_emotions": top_emotions,
        "top_scenes": top_scenes,
        "happy_moments": happy_moments,
        "growth_summary": growth_summary,
    }


class MomentRequest(BaseModel):
    user_id: str
    happy_moment: str
    scene_category: Optional[str] = None
    record_id: Optional[str] = None


@app.post("/api/moments")
def save_moment(req: MomentRequest):
    if req.record_id:
        try:
            numeric_id = int(req.record_id)
        except ValueError:
            raise HTTPException(status_code=422, detail="record_id 必须是合法的数字")

        # 关联到已有的记录：直接把开心 moment 补进那一条
        result = (
            supabase.table("mood_records")
            .update({"happy_moment": req.happy_moment})
            .eq("id", numeric_id)
            .eq("user_id", req.user_id)
            .execute()
        )
        saved_row = result.data[0]
    else:
        # 没有关联记录：单独存一条"快速记开心事"记录，标签和强度固定，场景仍需用户选
        if not req.scene_category:
            raise HTTPException(
                status_code=422, detail="单独记开心事时，scene_category 是必填的"
            )

        insert_result = (
            supabase.table("mood_records")
            .insert(
                {
                    "user_id": req.user_id,
                    "mood_text": req.happy_moment,
                    "emotion_tags": ["开心"],
                    "intensity": 5,
                    "scene_category": req.scene_category,
                    "happy_moment": req.happy_moment,
                    "is_deleted": False,
                }
            )
            .execute()
        )
        saved_row = insert_result.data[0]

    saved_row["record_id"] = str(saved_row["id"])
    return saved_row


@app.delete("/api/records/{record_id}")
def delete_record(record_id: str):
    try:
        numeric_id = int(record_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="record_id 必须是合法的数字")

    supabase.table("mood_records").update({"is_deleted": True}).eq(
        "id", numeric_id
    ).execute()
    return {"deleted": True, "record_id": record_id}
