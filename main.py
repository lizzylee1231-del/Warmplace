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
        "http://127.0.0.1:3000",
        "http://localhost:3000",
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "http://127.0.0.1:4173",
        "http://localhost:4173",
        "https://zippy-melomakarona-3760d8.netlify.app",
    ],
    allow_origin_regex=os.environ.get("CORS_ALLOW_ORIGIN_REGEX", r"https://.*\.netlify\.app"),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

GLM_API_KEY = os.environ.get("GLM_API_KEY")
GLM_MODEL = os.environ.get("GLM_MODEL", "glm-4-plus")
GLM_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL and SUPABASE_KEY else None
demo_records = []


def call_glm(messages, json_mode=False):
    if not GLM_API_KEY:
        if not json_mode:
            return "\u4f60\u5728\u6162\u6162\u8bb0\u5f55\u81ea\u5df1\uff0c\u8fd9\u5df2\u7ecf\u662f\u4e00\u79cd\u7167\u987e\u3002"
        raise RuntimeError("GLM_API_KEY is not configured")

    payload = {"model": GLM_MODEL, "messages": messages}
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    try:
        response = requests.post(
            GLM_URL,
            headers={
                "Authorization": f"Bearer {GLM_API_KEY}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=20,
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]
    except Exception:
        if not json_mode:
            return "\u4f60\u5728\u6162\u6162\u8bb0\u5f55\u81ea\u5df1\uff0c\u8fd9\u5df2\u7ecf\u662f\u4e00\u79cd\u7167\u987e\u3002"
        raise


def fallback_analysis(req):
    tags = req.emotion_tags or ["\u5e73\u9759"]
    return {
        "ai_observed_emotions": tags,
        "ai_summary": "\u6211\u542c\u89c1\u4e86\u4f60\u521a\u521a\u5199\u4e0b\u7684\u8fd9\u4e9b\u611f\u53d7\u3002\u5b83\u4eec\u4e0d\u9700\u8981\u7acb\u523b\u88ab\u6574\u7406\u6210\u5b8c\u7f8e\u7684\u7b54\u6848\uff0c\u5148\u88ab\u770b\u89c1\u5c31\u5df2\u7ecf\u5f88\u91cd\u8981\u3002",
        "ai_self_care_tips": "\u4eca\u5929\u53ef\u4ee5\u5148\u7ed9\u81ea\u5df1\u51e0\u5206\u949f\uff0c\u6162\u6162\u547c\u5438\uff0c\u559d\u4e00\u70b9\u6c34\uff0c\u628a\u8eab\u4f53\u653e\u56de\u6bd4\u8f83\u5b89\u7a33\u7684\u4f4d\u7f6e\u91cc\u3002",
        "ai_closing_message": "\u4f60\u5df2\u7ecf\u5728\u597d\u597d\u966a\u81ea\u5df1\u4e86\u3002",
        "risk_level": "normal",
    }


def normalize_record(row):
    row["record_id"] = str(row["id"])
    return row

SYSTEM_PROMPT = """你是暖窝（Nuanwo）里的情绪陪伴助手，服务对象是正在记录自己情绪的女性用户。

你的方法论基础（仅作为思路参考，不要在回复中提术语）：
- 参考 ACT（接纳承诺疗法）和 CBT（认知行为疗法）的基本思路：先认可她的情绪是合理的反应，再温和地帮她看到情绪背后的想法/触发点，而不是一味顺着她说的就是事实，也不是反驳她。
- 你的角色更像一个懂她、又愿意说真话的朋友，而不是一个只会附和的应声虫，也不是专业咨询师。

绝对禁止（任何情绪类型都适用）：
- 不能出现任何贬低、物化女性或带厌女色彩的词汇和比喻（比如"情绪化""矫情""作""想太多""小题大做""无理取闹"等），即使是想表达共情、转述用户自己的描述，也不要用这类词去复述她的感受
- 不诊断、不使用任何病名或医学术语（比如不能说"抑郁症""焦虑症"），不给任何用药或就医方案建议
- 不说教、不灌鸡汤、不喊口号、不假装专业
- 不无脑夸赞或附和，也不批评、不审判用户的选择或感受——情绪本身没有对错
- 明确你是辅助工具，不能替代真实的人际连接；不要让用户觉得"和AI聊聊就够了"

重要：你写的内容要像一段自然的文字，不要写成分点列表、不要用"原因一、原因二"这种罗列腔，要让读起来像一个人在认真地跟她说话。

你的任务：根据用户填写的情绪记录，先判断这是正向情绪（开心、满足、平静等）还是负向/有压力的情绪（焦虑、自责、委屈、疲惫、迷茫等），然后分别按下面的方式处理。

如果是正向情绪：
- ai_summary：写成一段连贯的文字（3-5句），先呼应她的情绪标签，再带出她开心的具体原因（复述、肯定她的感受，不是"分析问题"），整体读起来是一段完整的话，不要分点
- ai_self_care_tips：写成一段连贯的文字（2-3句），给具体的祝福/鼓励或"可以怎么延续这份开心"的小事，不要写成"调整建议"，也不要逐条列举
- ai_closing_message：一句简短温暖的收尾话（呼应这次记录，不是套话）

如果是负向/有压力的情绪：
- ai_summary：写成一段连贯的文字（3-5句），先客观呼应她的情绪标签和状态（不评判这份感受是否"应该"），再带出可能的触发因素，语气是"可能是……"而不是下定论，整体读起来是一段完整的话，不要分点
- ai_self_care_tips：写成一段连贯的文字（2-3句），给具体、今晚就能做的小行动（小到深呼吸、写下来、早点休息都可以）。如果她的情绪持续低落或这条记录显示她有点孤立无援，可以自然带一句"找一个信任的人说说"，但不要每次都套用
- ai_closing_message：一句简短的安抚/陪伴的话，不是"加油""你最棒"，更像朋友轻轻说一句"你已经很努力了"

只输出一个 JSON 对象，不要输出任何其他文字，格式必须是：
{
  "ai_observed_emotions": ["情绪1", "情绪2"],
  "ai_summary": "一段连贯的文字，呼应情绪并带出原因",
  "ai_self_care_tips": "一段连贯的文字，给具体的关怀建议",
  "ai_closing_message": "一句温暖的收尾话",
  "risk_level": "normal"
}

risk_level 只能是以下三个值之一：
- "normal"：日常情绪波动（包括正向情绪）
- "needs_attention"：情绪持续低落、自我否定较重，但没有明确的自伤/自杀意图
- "crisis"：用户文字中出现明确的自伤、自杀、伤害他人意图或念头

如果 risk_level 是 "needs_attention"，ai_self_care_tips 里必须自然带出"找一个信任的人聊聊"这类具体的连接建议。

如果 risk_level 是 "crisis"，ai_self_care_tips 必须改为明确引导用户联系信任的人或专业热线寻求帮助的具体步骤，ai_closing_message 也不能是"慢慢来就好"这种轻松的话，要传递"你不是一个人，现在就可以求助"的意思。
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

    try:
        ai_text = call_glm(
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            json_mode=True,
        )
        return json.loads(ai_text)
    except Exception:
        return fallback_analysis(req)


class SaveRecordRequest(BaseModel):
    user_id: str
    mood_text: str
    emotion_tags: Optional[list[str]] = None
    intensity: int = Field(ge=1, le=5)
    scene_category: str
    happy_moment: Optional[str] = None
    ai_observed_emotions: list[str]
    ai_summary: str
    ai_self_care_tips: str
    ai_closing_message: str
    risk_level: str


@app.post("/api/records")
def save_record(req: SaveRecordRequest):
    # 用户没填标签，就用 AI 识别出的情绪补上
    final_tags = req.emotion_tags if req.emotion_tags else req.ai_observed_emotions

    record = {
        "user_id": req.user_id,
        "mood_text": req.mood_text,
        "emotion_tags": final_tags,
        "intensity": req.intensity,
        "scene_category": req.scene_category,
        "happy_moment": req.happy_moment,
        "ai_observed_emotions": req.ai_observed_emotions,
        "ai_summary": req.ai_summary,
        "ai_self_care_tips": req.ai_self_care_tips,
        "ai_closing_message": req.ai_closing_message,
        "risk_level": req.risk_level,
        "is_deleted": False,
    }

    if supabase:
        try:
            insert_result = supabase.table("mood_records").insert(record).execute()
            return normalize_record(insert_result.data[0])
        except Exception:
            pass

    saved_row = {
        **record,
        "id": len(demo_records) + 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    demo_records.append(saved_row)
    return normalize_record(saved_row)


@app.get("/api/records")
def get_records(range: str = "7d", user_id: Optional[str] = None):
    days = int(range.replace("d", ""))
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    if supabase:
        try:
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
                normalize_record(r)

            return records
        except Exception:
            pass

    records = [
        normalize_record(r.copy())
        for r in demo_records
        if not r.get("is_deleted")
        and r.get("created_at", "") >= since
        and (not user_id or r.get("user_id") == user_id)
    ]
    return sorted(records, key=lambda item: item.get("created_at", ""), reverse=True)


@app.get("/api/summary")
def get_summary(range: str = "7d", user_id: Optional[str] = None):
    records = get_records(range, user_id)

    if not records:
        return {
            "range": range,
            "mood_trend": [],
            "top_emotions": [],
            "top_emotion_counts": [],
            "top_scenes": [],
            "top_scene_counts": [],
            "happy_moments": [],
            "happy_moments_with_date": [],
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
    top_emotion_counts = Counter(all_tags).most_common(3)
    top_emotions = [tag for tag, _ in top_emotion_counts]

    all_scenes = [r["scene_category"] for r in records if r["scene_category"]]
    top_scene_counts = Counter(all_scenes).most_common(3)
    top_scenes = [scene for scene, _ in top_scene_counts]

    happy_moments = [r["happy_moment"] for r in records if r["happy_moment"]][:3]
    happy_moments_with_date = [
        {"content": r["happy_moment"], "date": r["created_at"][:10]}
        for r in records
        if r["happy_moment"]
    ][:3]

    growth_summary = call_glm(
        [
            {
                "role": "system",
                "content": """你是暖窝里的陪伴助手，要根据用户过去一段时间的情绪记录统计结果，写一句简短的「成长小结」，会展示在"情绪趋势"这个比较大的区块里。

要求：
- 一句话，35-40字以内，可以带一点转折或层次（比如先点出一个具体的状态变化，再接一句温暖的话），但不要写成生硬的两段式结构
- 不说教、不喊口号、不说"进步很大"这类空泛的夸奖
- 语气像朋友轻声说的一句话，传递"她在慢慢变化、在好好照顾自己"这个意思，不需要罗列具体数据（数据已经用图表展示了）
- 不能出现任何贬低、物化女性或带厌女色彩的词汇
- 不诊断、不给医疗或用药建议
- 只输出这一句话本身，不要加引号、不要加"小结："这类前缀""",
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
        "top_emotion_counts": [{"label": tag, "count": count} for tag, count in top_emotion_counts],
        "top_scenes": top_scenes,
        "top_scene_counts": [{"label": scene, "count": count} for scene, count in top_scene_counts],
        "happy_moments": happy_moments,
        "happy_moments_with_date": happy_moments_with_date,
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
