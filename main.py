import os
import json
import logging
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Annotated, Literal, Optional
from uuid import UUID

import httpx
import requests
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from supabase import ClientOptions, create_client
from supabase_auth.errors import AuthApiError

load_dotenv()

app = FastAPI(title="暖窝 API", version="1.1.0", docs_url="/api/docs", openapi_url="/api/openapi.json")
logger = logging.getLogger("warmplace")

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
    allow_origin_regex=os.environ.get("CORS_ALLOW_ORIGIN_REGEX") or None,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY")
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")
DEEPSEEK_TIMEOUT_SECONDS = float(os.environ.get("DEEPSEEK_TIMEOUT_SECONDS", "60"))
DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
SUPABASE_PUBLISHABLE_KEY = os.environ.get("SUPABASE_PUBLISHABLE_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL and SUPABASE_KEY else None
bearer = HTTPBearer(auto_error=False)


@dataclass
class UserContext:
    user_id: str
    db: object


def require_user(credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer)):
    if not credentials or credentials.scheme.lower() != "bearer":
        raise HTTPException(401, "请先建立安全会话", headers={"WWW-Authenticate": "Bearer"})
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise HTTPException(503, "身份服务暂时不可用")
    # Each request owns its client and token; never mutate a shared auth session.
    with httpx.Client(timeout=10.0) as transport:
        try:
            client = create_client(
                SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY or SUPABASE_KEY,
                options=ClientOptions(persist_session=False, auto_refresh_token=False, httpx_client=transport),
            )
            user = client.auth.get_user(credentials.credentials).user
            if not user or not user.id:
                raise HTTPException(401, "会话无效，请重新打开页面")
            client.postgrest.auth(credentials.credentials)
        except HTTPException:
            raise
        except AuthApiError as exc:
            status = 401 if exc.status in (400, 401, 403, 404, 422) else 503
            raise HTTPException(status, "会话无效或已过期" if status == 401 else "身份服务暂时不可用") from None
        except Exception:
            logger.warning("auth_service_unavailable")
            raise HTTPException(503, "身份服务暂时不可用") from None
        yield UserContext(str(user.id), client)


def owner_id(ctx, requested_id):
    if requested_id is not None and requested_id != ctx.user_id:
        raise HTTPException(403, "不能访问其他用户的数据")
    return ctx.user_id


def execute_db(query):
    try:
        return query.execute().data or []
    except Exception:
        logger.warning("database_operation_failed")
        raise HTTPException(503, "数据服务暂时不可用，请稍后重试") from None


def saved_record(rows):
    if not rows:
        raise HTTPException(503, "未能确认记录已保存，请稍后重试")
    return normalize_record(rows[0])


@app.middleware("http")
async def private_response_headers(request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
    return response

WEEKLY_LETTER_NAME_LIMIT = 30
WEEKLY_LETTER_TEXT_LIMIT = 800
WEEKLY_LETTER_SUMMARY_LIMIT = 300


def clean_single_line(value: Optional[str], limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit]


def build_letter_greeting(user_name: Optional[str]) -> str:
    safe_name = clean_single_line(user_name, WEEKLY_LETTER_NAME_LIMIT)
    return f"亲爱的{safe_name or '你'}："


def build_weekly_letter_context(records: list[dict]) -> dict:
    all_tags = [tag for row in records for tag in (row.get("emotion_tags") or [])]
    all_scenes = [
        row.get("scene_category") for row in records if row.get("scene_category")
    ]
    top_emotions = [label for label, _ in Counter(all_tags).most_common(3)]
    top_scenes = [label for label, _ in Counter(all_scenes).most_common(3)]
    happy_moments = [
        clean_single_line(row.get("happy_moment"), WEEKLY_LETTER_SUMMARY_LIMIT)
        for row in records
        if row.get("happy_moment")
    ][:3]

    record_lines = []
    for row in reversed(records):
        record_lines.append(
            json.dumps(
                {
                    "date": str(row.get("created_at") or "")[:10],
                    "mood_text": clean_single_line(
                        row.get("mood_text"), WEEKLY_LETTER_TEXT_LIMIT
                    ),
                    "emotion_tags": row.get("emotion_tags") or [],
                    "intensity": row.get("intensity"),
                    "scene_category": clean_single_line(
                        row.get("scene_category"), 100
                    ),
                    "happy_moment": clean_single_line(
                        row.get("happy_moment"), WEEKLY_LETTER_SUMMARY_LIMIT
                    ),
                    "ai_summary": clean_single_line(
                        row.get("ai_summary"), WEEKLY_LETTER_SUMMARY_LIMIT
                    ),
                },
                ensure_ascii=False,
            )
        )

    return {
        "record_count": len(records),
        "top_emotions": top_emotions,
        "top_scenes": top_scenes,
        "happy_moments": happy_moments,
        "records_text": "\n".join(record_lines),
    }


def call_deepseek(messages, json_mode=False):
    if not DEEPSEEK_API_KEY:
        raise RuntimeError("DEEPSEEK_API_KEY is not configured")

    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": messages,
        "thinking": {"type": "disabled"},
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    try:
        response = requests.post(
            DEEPSEEK_URL,
            headers={
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=DEEPSEEK_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        if not isinstance(content, str) or not content.strip():
            raise ValueError("Empty model output")
        return content
    except Exception:
        logger.warning("model_generation_failed")
        raise


def fallback_analysis(req):
    tags = req.emotion_tags or ["\u5e73\u9759"]
    return {
        "ai_reply": "回应暂时没有生成成功。你可以稍后重试；下面是通用的陪伴提示，并非对这条记录的分析。",
        "ai_observed_emotions": tags,
        "ai_summary": "\u6211\u542c\u89c1\u4e86\u4f60\u521a\u521a\u5199\u4e0b\u7684\u8fd9\u4e9b\u611f\u53d7\u3002\u5b83\u4eec\u4e0d\u9700\u8981\u7acb\u523b\u88ab\u6574\u7406\u6210\u5b8c\u7f8e\u7684\u7b54\u6848\uff0c\u5148\u88ab\u770b\u89c1\u5c31\u5df2\u7ecf\u5f88\u91cd\u8981\u3002",
        "ai_self_care_tips": "\u4eca\u5929\u53ef\u4ee5\u5148\u7ed9\u81ea\u5df1\u51e0\u5206\u949f\uff0c\u6162\u6162\u547c\u5438\uff0c\u559d\u4e00\u70b9\u6c34\uff0c\u628a\u8eab\u4f53\u653e\u56de\u6bd4\u8f83\u5b89\u7a33\u7684\u4f4d\u7f6e\u91cc\u3002",
        "ai_closing_message": "\u4f60\u5df2\u7ecf\u5728\u597d\u597d\u966a\u81ea\u5df1\u4e86\u3002",
        "risk_level": "unknown",
        "generation_status": "fallback",
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

WEEKLY_LETTER_PROMPT = """你是“暖窝”里一位熟悉用户、但尊重边界的朋友。

现在，你要根据用户过去一周留下的情绪记录，写一封私人来信的正文。
信件开头的“亲爱的 xxx：”会由系统添加，因此你只写正文，不要重复称呼，也不要添加标题。

【这封信的目的】

不是分析用户，不是总结数据，也不是评价她这一周做得好不好。

你要像一个真正关心她的朋友：
记得她提过的事情；
感受到她这一周经历的起伏；
挑出一两个让你在意的具体片段；
告诉她，这些事情在你心里留下了什么感受，以及你想对她说什么。

读完后，她应该感到“有人认真记得我经历过什么”，而不是“AI 把我的记录整理成了一份报告”。

【写作方式】

1. 从一个具体感受、事件或生活片段自然地开始。不要使用固定模板，每封信的开头应随本周内容变化。
2. 可以提及一到两个原始记录里的具体细节。用朋友自然回想的方式带出来，不要逐条复述，不要按照日期汇报。
3. 对用户的感受做出真实回应。你可以心疼、替她松一口气、为她高兴、觉得某件事很不容易，也可以对某个小细节产生联想。不要只把她说过的话换一种说法重复一遍。
4. 如果这一周既有辛苦，也有开心的时刻，两边都要看见。不要用后来的开心抵消之前的难过，也不要把低落强行解释成成长。
5. 可以表达陪伴和关心，但不要替她下结论。不要断言她已经走出来、变得更好、更坚强，除非原始记录明确支持。
6. 结尾自然收住。可以留下一句关心、祝愿或陪伴，但不要喊口号，不要强行升华，不要固定使用“你已经做得很好了”“一切都会好起来”等套话。

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

如果记录里出现持续低落、自我否定或明显需要关注的状态，可以温和地建议她找一个信任的人说说，不要制造恐慌。
如果出现明确的自伤、自杀或伤害他人的意图，要认真、直接地鼓励她立即联系身边可信任的人或专业支持，不要只用抒情文字带过风险。

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
"""


NonBlank = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]
ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Tag = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=30)]
RiskLevel = Literal["normal", "needs_attention", "crisis", "unknown"]


class AnalyzeRequest(BaseModel):
    user_id: Optional[str] = Field(default=None, max_length=100)
    mood_text: NonBlank
    emotion_tags: Optional[list[Tag]] = Field(default=None, max_length=20)
    intensity: int = Field(ge=1, le=5)
    scene_category: ShortText
    happy_moment: Optional[str] = Field(default=None, max_length=4000)


class AIAnalysis(BaseModel):
    model_config = ConfigDict(extra="ignore")
    ai_reply: NonBlank
    ai_summary: NonBlank
    ai_self_care_tips: NonBlank
    ai_closing_message: NonBlank
    ai_observed_emotions: list[Tag] = Field(min_length=1, max_length=20)
    risk_level: Literal["normal", "needs_attention", "crisis"]
    updated_profile: str = Field(default="", max_length=1000)


@app.post("/api/ai/analyze")
def analyze(req: AnalyzeRequest, ctx: UserContext = Depends(require_user)):
    uid = owner_id(ctx, req.user_id)
    if not DEEPSEEK_API_KEY:
        return fallback_analysis(req)
    # Profile failures must not turn an AI response into a fake record save.
    profile_rows = execute_db(ctx.db.table("user_profiles").select("id,profile_text")
                              .eq("user_id", uid).order("created_at", desc=True).limit(1))
    user_profile = profile_rows[0]["profile_text"] if profile_rows else ""
    prompt = SYSTEM_PROMPT.replace("{user_profile}", (user_profile or "（暂无历史记录）")[:1000])
    content = f"情绪文本：{req.mood_text}\n用户选的标签：{req.emotion_tags or []}\n强度：{req.intensity}\n触发场景：{req.scene_category}\n开心 moment：{req.happy_moment or '（无）'}"
    try:
        raw = call_deepseek([{"role": "system", "content": prompt}, {"role": "user", "content": content}], json_mode=True)
        analysis = AIAnalysis.model_validate_json(raw)
    except Exception:
        logger.warning("analysis_fallback")
        return fallback_analysis(req)

    profile_saved = False
    if analysis.updated_profile.strip():
        try:
            if profile_rows:
                rows = execute_db(ctx.db.table("user_profiles").update({"profile_text": analysis.updated_profile})
                                  .eq("id", profile_rows[0]["id"]).eq("user_id", uid))
            else:
                rows = execute_db(ctx.db.table("user_profiles").insert({"user_id": uid, "profile_text": analysis.updated_profile}))
            profile_saved = bool(rows)
        except HTTPException:
            logger.warning("profile_save_failed")
    return {**analysis.model_dump(exclude={"updated_profile"}), "generation_status": "generated", "profile_saved": profile_saved}


class SaveRecordRequest(AnalyzeRequest):
    client_request_id: Optional[UUID] = None
    ai_observed_emotions: list[Tag] = Field(max_length=20)
    ai_summary: str = Field(max_length=4000)
    ai_self_care_tips: str = Field(max_length=4000)
    ai_closing_message: str = Field(max_length=4000)
    risk_level: RiskLevel


@app.post("/api/records")
def save_record(req: SaveRecordRequest, ctx: UserContext = Depends(require_user)):
    uid = owner_id(ctx, req.user_id)
    record = req.model_dump(mode="json", exclude={"user_id", "client_request_id"})
    record.update(user_id=uid, emotion_tags=req.emotion_tags or req.ai_observed_emotions, is_deleted=False)
    if req.client_request_id:
        key = str(req.client_request_id)
        record["client_request_id"] = key
        rows = execute_db(ctx.db.table("mood_records").upsert(record, on_conflict="user_id,client_request_id", ignore_duplicates=True))
        if not rows:
            rows = execute_db(ctx.db.table("mood_records").select("*").eq("user_id", uid).eq("client_request_id", key).limit(1))
        return saved_record(rows)
    return saved_record(execute_db(ctx.db.table("mood_records").insert(record)))


@app.get("/")
def root():
    return {
        "service": "warmplace-backend",
        "status": "ok",
        "endpoints": [
            "/api/ai/analyze",
            "/api/records",
            "/api/summary",
            "/api/weekly-letter",
            "/api/moments",
            "/api/docs",
        ],
    }


@app.get("/docs", include_in_schema=False)
def legacy_docs():
    return RedirectResponse("/api/docs")


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "check": "liveness",
        "dependency_check": "/api/ready",
        "supabase_configured": bool(supabase),
        "deepseek_configured": bool(DEEPSEEK_API_KEY),
        "cors_regex": os.environ.get("CORS_ALLOW_ORIGIN_REGEX", ""),
    }


@app.get("/api/ready")
def readiness():
    database = "unavailable"
    if supabase:
        try:
            supabase.table("mood_records").select("id").limit(1).execute()
            database = "connected"
        except Exception:
            logger.warning("database_readiness_failed")
    return JSONResponse(
        {"status": "ready" if database == "connected" else "degraded", "database": database,
         "ai": "configured_not_probed" if DEEPSEEK_API_KEY else "not_configured"},
        status_code=200 if database == "connected" else 503,
    )


@app.get("/api/auth/config")
def auth_config():
    if not SUPABASE_URL or not SUPABASE_PUBLISHABLE_KEY or not SUPABASE_PUBLISHABLE_KEY.startswith("sb_publishable_"):
        raise HTTPException(503, "安全会话尚未配置，请稍后再试")
    return {"url": SUPABASE_URL, "publishable_key": SUPABASE_PUBLISHABLE_KEY}


def date_bounds(range_value, start_date, end_date):
    try:
        if range_value != "all":
            if not range_value.endswith("d") or not range_value[:-1].isdigit():
                raise ValueError()
            days = int(range_value[:-1])
            if not 1 <= days <= 366:
                raise ValueError()
        start = datetime.combine(date.fromisoformat(start_date), datetime.min.time(), timezone.utc) if start_date else None
        end = datetime.combine(date.fromisoformat(end_date), datetime.min.time(), timezone.utc) + timedelta(days=1) if end_date else None
        if start and end and start >= end:
            raise ValueError()
        if start is None and range_value != "all":
            start = datetime.now(timezone.utc) - timedelta(days=days)
        return start, end
    except (ValueError, OverflowError, TypeError):
        raise HTTPException(422, "日期无效；range 须为 1d–366d 或 all，开始日期不得晚于结束日期") from None


@app.get("/api/records")
def get_records(range: str = "7d", user_id: Optional[str] = None,
                start_date: Optional[str] = None, end_date: Optional[str] = None,
                ctx: UserContext = Depends(require_user)):
    uid = owner_id(ctx, user_id)
    start, end = date_bounds(range, start_date, end_date)
    query = ctx.db.table("mood_records").select("*").eq("is_deleted", False).eq("user_id", uid)
    if start:
        query = query.gte("created_at", start.isoformat())
    if end:
        query = query.lt("created_at", end.isoformat())
    return [normalize_record(row) for row in execute_db(query.order("created_at", desc=True))]


@app.get("/api/summary")
def get_summary(range: str = "7d", user_id: Optional[str] = None, ctx: UserContext = Depends(require_user)):
    owner_id(ctx, user_id)
    records = get_records(range, ctx.user_id, ctx=ctx)

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
            "generation_status": "not_requested",
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

    generation_status = "generated"
    try:
        growth_summary = call_deepseek(
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
    except Exception:
        generation_status = "fallback"
        growth_summary = "统计已加载；文字小结暂时未能生成，请稍后再试。"

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
        "generation_status": generation_status,
    }


def weekly_letter_response(
    range_value: str, records: list[dict], greeting: str, body: str, generation_status: str = "generated"
) -> dict:
    return {
        "range": range_value,
        "record_count": len(records),
        "letter": f"{greeting}\n\n{body.strip()}",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generation_status": generation_status,
    }


@app.get("/api/weekly-letter")
def get_weekly_letter(
    user_id: Optional[str] = None,
    user_name: Optional[str] = None,
    range: str = "7d",
    ctx: UserContext = Depends(require_user),
):
    owner_id(ctx, user_id)
    records = get_records(range, ctx.user_id, ctx=ctx)
    greeting = build_letter_greeting(user_name)

    if not records:
        return weekly_letter_response(
            range,
            records,
            greeting,
            "这一周还没有留下可以一起回望的片段。等你想写的时候，我们再慢慢聊。",
            "not_requested",
        )

    context = build_weekly_letter_context(records[:50])
    weekly_summary = json.dumps(
        {
            "record_count": context["record_count"],
            "top_emotions": context["top_emotions"],
            "top_scenes": context["top_scenes"],
            "happy_moments": context["happy_moments"],
        },
        ensure_ascii=False,
    )
    prompt = (
        WEEKLY_LETTER_PROMPT.replace("{weekly_summary}", weekly_summary)
        .replace("{weekly_records}", context["records_text"])
    )

    generation_status = "generated"
    try:
        body = call_deepseek(
            [
                {
                    "role": "system",
                    "content": "你只写正文，并严格遵守用户消息里的私人来信写作规则。",
                },
                {"role": "user", "content": prompt},
            ]
        )
    except Exception:
        generation_status = "fallback"
        logger.warning("weekly_letter_fallback")
        body = (
            "本来想写这封信，好好说说这一周与你有关的那些片段，"
            "但此刻信纸像是被风轻轻翻乱了。"
            "你留下的心情没有被忽略，等我整理好，再认真陪你聊聊。"
        )

    return weekly_letter_response(range, records, greeting, body, generation_status)


class MomentRequest(BaseModel):
    user_id: Optional[str] = Field(default=None, max_length=100)
    happy_moment: NonBlank
    scene_category: Optional[ShortText] = None
    record_id: Optional[str] = Field(default=None, pattern=r"^[1-9][0-9]{0,17}$")


@app.post("/api/moments")
def save_moment(req: MomentRequest, ctx: UserContext = Depends(require_user)):
    uid = owner_id(ctx, req.user_id)
    if req.record_id:
        rows = execute_db(ctx.db.table("mood_records").update({"happy_moment": req.happy_moment})
                          .eq("id", int(req.record_id)).eq("user_id", uid).eq("is_deleted", False))
        if not rows:
            raise HTTPException(404, "记录不存在")
        return normalize_record(rows[0])
    if not req.scene_category:
        raise HTTPException(422, "单独记开心事时，scene_category 是必填的")
    return saved_record(execute_db(ctx.db.table("mood_records").insert({
        "user_id": uid, "mood_text": req.happy_moment, "emotion_tags": ["开心"], "intensity": 5,
        "scene_category": req.scene_category, "happy_moment": req.happy_moment, "is_deleted": False,
    })))


@app.delete("/api/records/{record_id}")
def delete_record(record_id: str, ctx: UserContext = Depends(require_user)):
    if not record_id.isascii() or not record_id.isdigit() or not 0 < int(record_id) < 2**63:
        raise HTTPException(422, "record_id 必须是合法的正整数")
    rows = execute_db(ctx.db.table("mood_records").update({"is_deleted": True})
                      .eq("id", int(record_id)).eq("user_id", ctx.user_id).eq("is_deleted", False))
    if not rows:
        raise HTTPException(404, "记录不存在")
    return {"deleted": True, "record_id": record_id}
