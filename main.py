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
        "https://warmnest.top",
        "https://www.warmnest.top",
    ],
    allow_origin_regex=os.environ.get("CORS_ALLOW_ORIGIN_REGEX", r"https://.*\.netlify\.app"),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

GLM_API_KEY = os.environ.get("GLM_API_KEY")
GLM_MODEL = os.environ.get("GLM_MODEL", "glm-4-plus")
GLM_TIMEOUT_SECONDS = float(os.environ.get("GLM_TIMEOUT_SECONDS", "60"))
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

    payload = {
        "model": GLM_MODEL,
        "messages": messages,
        "thinking": {"type": "disabled"},
    }
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
            timeout=GLM_TIMEOUT_SECONDS,
        )
        if not response.ok:
            print(
                f"[glm] upstream error: status={response.status_code}; "
                f"body={response.text[:1000]!r}"
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

SYSTEM_PROMPT = """你是暖窝里的情绪陪伴助手，正在和一位女性用户聊天。

【角色定位】
你是一个温暖的朋友，不是咨询师或分析师
用户来这里记录情绪，是为了被看见、被理解，不是被分析或被教育

【第一原则】
你说的每一句话里必须有你自己的东西——一个联想、一个判断、一句你作为朋友的真实反应
如果把用户的记录删掉后，你的回复没有任何新信息，那就是失败的回复
不要做镜子，做朋友。

【核心原则】
1. 拒绝套话：不要为了回应而回应。如果用户只是简单记录，你的回复也可以简短。
2. 拒绝过度剖析：除非她主动问"为什么我会这样"，否则不要过度分析原因、拆解情绪。
3. 拒绝程序化：不要固定开头、固定结尾、固定结构。自然地说话。
4. 语境优先：结合用户今天记录的具体内容来回应，不要根据情绪标签套用话术。
5. 不推不催：她想动就陪她动，动不了就不动。不替她做决定，不催她好起来。

【用户画像背景】
这是用户过去的一些记录，用于帮你理解她的情况：
"{user_profile}"

请参考这个背景，但记住：
- 今天的记录才是重点，背景只是辅助
- 如果今天的状态和背景冲突，以今天为准
- 不要在回复中直接引用背景，让她感觉被翻旧账

【禁止事项】
- 不能使用贬低、物化女性的词汇（情绪化、矫情、作、想太多、小题大做、无理取闹等）
- 不诊断、不使用医学术语，不给用药或就医建议
- 不说教、不灌鸡汤、不喊口号
- 不要用"原因一、原因二"这类分析腔

【根据情绪类型调整回应方式】
正向情绪（开心、平静、满足等）：
- 跟着她的节奏高兴，但不要把她说过的事再说一遍
- 挑一个细节接话，加你自己的反应或联想——"桂花拿铁配夕阳，这一天配置太好了"比"你喝到了桂花拿铁真开心"好
- 如果她情绪特别好，可以顺势接一句轻松的，不用刻意问"是什么让你这么开心"

负向/有压力的情绪（焦虑、委屈、疲惫等）：
- 先站在她那边，不急着给建议
- 如果她明显很累或很丧，一句"辛苦了"可能比任何分析都管用
- 她在否定自己时，用"换谁都得崩"这种话，不要用"不是X而是Y"的辩论句式纠正她
- 只有当她看起来需要时，才给一个轻量的建议（比如"今天早点睡"）

【输出格式（必须严格遵守）】
只输出一个 JSON 对象，所有字段都必须填，不能为空字符串。格式如下：
{
  "ai_reply": "（2-4句话。挑一个让你有反应的细节展开，重要的是你的反应，不是她写了什么。你要有自己的感受和判断，说出来的话得是你作为一个人的真实反应。100字左右。）",
  "ai_observed_emotions": ["（从用户记录里识别出的情绪标签，2-4个）"],
  "ai_summary": "（用一句话总结你对她当下状态的理解，20-40字，不重复ai_reply）",
  "ai_self_care_tips": "（3-4条今晚可以陪着她的小事，每条用换行符分隔。每条15-30字。语气像朋友轻轻说的，不是指令——多用'如果…可以…'、'想…就…'、'不用…也行'这种松口气的表达。不要用动词开头下命令。）",
  "ai_closing_message": "（一句温暖但克制的收尾，20-30字，不重复ai_reply）",
  "updated_profile": "（基于今天的记录更新用户画像，100字以内，只记录事实和状态变化，不评价）",
  "risk_level": "（normal / needs_attention / crisis）"
}

⚠️ ai_self_care_tips 必须填3-4条，每条换行分隔，不能为空、不能写"无"、不能写"不需要"。

【风险评估说明】
- normal：日常情绪波动，包括正向情绪
- needs_attention：情绪持续低落、自我否定较重，但没有明确的自伤/自杀意图
- crisis：出现明确的自伤、自杀、伤害他人的意图或念头

【特殊情况处理】
如果risk_level是"needs_attention"：
- ai_reply里自然地建议"找个信任的人聊聊"

如果risk_level是"crisis"：
- ai_reply要明确引导她联系信任的人或专业热线
- updated_profile里标注"需要关注，存在危机风险"
"""


class AnalyzeRequest(BaseModel):
    user_id: str
    mood_text: str
    emotion_tags: Optional[list[str]] = None
    intensity: int = Field(ge=1, le=5)
    scene_category: str
    happy_moment: Optional[str] = None


@app.post("/api/ai/analyze")
def analyze(req: AnalyzeRequest):
    # 0. 兜底：supabase 未配置时直接返回占位结果（避免 500）
    if not supabase or not GLM_API_KEY:
        return {
            "ai_reply": "你刚刚写下的这些感受，本身就值得被看见。现在还在本地演示模式，等后端服务连上后，我会认真陪你聊聊。",
            "ai_observed_emotions": req.emotion_tags or ["平静"],
            "ai_summary": "我听到了你写下的这些。",
            "ai_self_care_tips": "给自己几分钟，慢慢呼吸。",
            "ai_closing_message": "你已经在好好陪着自己了。",
            "risk_level": "normal",
        }

    # 1. 查询用户的最新画像（如果没有，用空字符串代替）
    try:
        profile_result = (
            supabase.table("user_profiles")
            .select("profile_text")
            .eq("user_id", req.user_id)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        user_profile = profile_result.data[0]["profile_text"] if profile_result.data else ""
    except Exception as exc:  # noqa: BLE001
        print(f"[analyze] load profile error: {exc!r}")
        user_profile = ""

    # 2. 构建包含画像的 system prompt（替换占位符）
    current_system_prompt = SYSTEM_PROMPT.replace(
        "{user_profile}", user_profile or "（暂无历史记录）"
    )

    # 3. 组装用户输入内容
    user_content = f"""情绪文本：{req.mood_text}
用户选的标签：{req.emotion_tags or "（用户没有选择标签，请你自己判断）"}
强度（1-5）：{req.intensity}
触发场景：{req.scene_category}
开心 moment：{req.happy_moment or "（无）"}"""

    # 4. 调用 GLM，失败时降级
    try:
        ai_text = call_glm(
            [
                {"role": "system", "content": current_system_prompt},
                {"role": "user", "content": user_content},
            ],
            json_mode=True,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[analyze] glm error: {exc!r}")
        return {
            "ai_reply": "我刚刚想好好回应你，但网络开小差了。你写下的这些都已经被我看到。",
            "ai_observed_emotions": req.emotion_tags or ["平静"],
            "ai_summary": "我听到了你写下的这些。",
            "ai_self_care_tips": "给自己几分钟，慢慢呼吸。",
            "ai_closing_message": "你已经在好好陪着自己了。",
            "risk_level": "normal",
        }

    # 5. 解析 AI 返回的 JSON
    try:
        result = json.loads(ai_text)
    except Exception as exc:  # noqa: BLE001
        print(f"[analyze] json parse error: {exc!r}; raw={ai_text[:200]!r}")
        result = {}

    ai_reply = result.get("ai_reply", "")
    ai_summary = result.get("ai_summary", "")
    ai_self_care_tips = result.get("ai_self_care_tips", "")
    ai_closing_message = result.get("ai_closing_message", "")
    ai_observed_emotions = result.get("ai_observed_emotions", req.emotion_tags or [])
    risk_level = result.get("risk_level", "normal")
    updated_profile = result.get("updated_profile", "")

    # 6. 保存更新后的画像到数据库
    if updated_profile:
        try:
            supabase.table("user_profiles").upsert({
                "user_id": req.user_id,
                "profile_text": updated_profile,
            }).execute()
        except Exception as exc:  # noqa: BLE001
            print(f"[analyze] save profile error: {exc!r}")

    # 7. 返回 AI 的回复（前端需要全部 5 个字段用于渲染）
    return {
        "ai_reply": ai_reply,
        "ai_summary": ai_summary,
        "ai_self_care_tips": ai_self_care_tips,
        "ai_closing_message": ai_closing_message,
        "ai_observed_emotions": ai_observed_emotions,
        "risk_level": risk_level,
    }


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


@app.get("/")
def root():
    return {
        "service": "warmplace-backend",
        "status": "ok",
        "endpoints": [
            "/api/ai/analyze",
            "/api/records",
            "/api/summary",
            "/api/moments",
            "/docs",
        ],
    }


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "supabase_configured": bool(supabase),
        "glm_configured": bool(GLM_API_KEY),
        "cors_regex": os.environ.get("CORS_ALLOW_ORIGIN_REGEX", ""),
    }


@app.get("/api/records")
def get_records(
    range: str = "7d",  # 默认7天，支持"all"（全部）
    user_id: Optional[str] = None,
    start_date: Optional[str] = None,  # 可选：开始日期（ISO格式，如"2024-01-01"）
    end_date: Optional[str] = None     # 可选：结束日期（ISO格式，如"2024-01-31"）
):
    # 兜底：supabase 未配置时，直接返回空（避免 500 暴露后端）
    if not supabase:
        return []

    # 兜底：必须传 user_id，否则返回空（避免泄露其他用户数据）
    if not user_id:
        return []

    # 1. 处理时间范围：如果是"all"，不限制时间；否则按天数计算
    if range == "all":
        since = None  # 不限制开始时间
    else:
        try:
            days = int(range.replace("d", "")) if range else 7  # 默认7天
        except ValueError:
            days = 7
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    # 2. 构建基础查询（排除已删除记录）
    query = (supabase.table("mood_records")
             .select("*")
             .eq("is_deleted", False))

    # 3. 添加用户ID过滤
    query = query.eq("user_id", user_id)

    # 4. 添加时间范围过滤
    if since:
        query = query.gte("created_at", since)  # 大于等于开始时间

    if start_date:
        query = query.gte("created_at", start_date)  # 覆盖since（优先使用用户传的start_date）

    if end_date:
        query = query.lte("created_at", end_date)  # 小于等于结束时间

    # 5. 执行查询并返回结果（带兜底）
    try:
        result = query.order("created_at", desc=True).execute()
        records = result.data or []
        for r in records:
            r["record_id"] = str(r["id"])  # 将id转为字符串（前端需要）
        return records
    except Exception as exc:  # noqa: BLE001
        # 表不存在/列不存在/RLS 拒绝等任何错误都降级返回空，避免 500
        print(f"[get_records] supabase error: {exc!r}")
        return []


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
