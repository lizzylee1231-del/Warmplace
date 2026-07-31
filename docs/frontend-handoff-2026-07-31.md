# 暖窝后端 → 前端交接文档

更新时间：2026-07-31  
后端版本：f15ab87  
生产前端：https://www.warmnest.top  
后端源站：https://warmplace-one.vercel.app

## 1. 当前结论

当前 MVP 所需后端功能已经完成并部署：

- 用户情绪内容的 AI 分析
- 情绪记录保存、查询和软删除
- 日历记录
- 周期统计与成长小结
- 开心时刻补记
- 周回顾朋友来信
- Supabase 持久化
- DeepSeek AI 生成
- 正式域名同源代理

前端在生产环境中不要直接请求 *.vercel.app。所有接口都使用同源
/api/...，由前端 Vercel 项目转发到后端。

    export const API_BASE_URL = "";

## 2. 前端公共约定

### 2.1 用户标识

当前没有登录系统。前端首次打开时生成 UUID，写入 localStorage，以后所有接口
都使用同一个 user_id。

当前前端使用：

    const USER_ID_STORAGE_KEY = "nuanwo_user_id";
    window.USER_ID = getOrCreateUserId();

不要每次刷新都生成新 ID，否则历史记录、周统计和来信会像属于不同用户一样
彼此断开。

### 2.2 请求和编码

- 请求体使用 application/json
- 响应均为 JSON
- 时间为 ISO 8601 字符串
- record_id 按字符串使用
- 中文查询参数必须使用 URLSearchParams 或 encodeURIComponent
- AI 接口需要 Loading、重试和空状态

推荐 URL 写法：

    const url = new URL("/api/records", window.location.origin);
    url.searchParams.set("user_id", window.USER_ID);
    url.searchParams.set("range", "7d");
    const response = await fetch(url);

## 3. 推荐业务流程

用户提交一条心情记录时：

1. 调用 POST /api/ai/analyze
2. 使用响应渲染 AI 回复、情绪、照顾建议和风险状态
3. 调用 POST /api/records 保存原文与 AI 字段
4. 用户进入日历页时调用 GET /api/records
5. 用户进入周回顾时分别调用 GET /api/summary 和 GET /api/weekly-letter

AI 分析接口不会自动保存 mood_records，前端必须执行第 3 步。

## 4. 接口清单

### 4.1 健康检查

    GET /api/health

响应：

    {
      "status": "ok",
      "supabase_configured": true,
      "deepseek_configured": true,
      "cors_regex": "https://.*\\.vercel\\.app"
    }

这个接口只能确认环境变量存在，不能替代真实 AI 请求验收。

### 4.2 AI 分析

    POST /api/ai/analyze
    Content-Type: application/json

请求：

    {
      "user_id": "uuid",
      "mood_text": "今天工作临时改了好几次，回家时很累。",
      "emotion_tags": ["疲惫", "委屈"],
      "intensity": 4,
      "scene_category": "工作",
      "happy_moment": "下班时看到了很好看的晚霞"
    }

字段：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| user_id | string | 是 | 当前用户 UUID |
| mood_text | string | 是 | 用户原始记录 |
| emotion_tags | string[] / null | 否 | 用户选择的情绪 |
| intensity | integer | 是 | 1–5 |
| scene_category | string | 是 | 工作、家庭、关系、生活等 |
| happy_moment | string / null | 否 | 当次开心片段 |

响应：

    {
      "ai_reply": "……",
      "ai_summary": "……",
      "ai_self_care_tips": "第一条\n第二条\n第三条",
      "ai_closing_message": "……",
      "ai_observed_emotions": ["疲惫", "委屈"],
      "risk_level": "normal"
    }

risk_level 可能为 normal、needs_attention 或 crisis。前端至少应为 crisis
准备醒目的支持入口，不要只按普通卡片展示。

生产实测 DeepSeek 通常约 5 秒。模型失败时后端会返回兜底内容，HTTP 仍可能
是 200，因此前端不能只根据状态码判断文案是否来自模型。

### 4.3 保存记录

    POST /api/records
    Content-Type: application/json

请求：

    {
      "user_id": "uuid",
      "mood_text": "今天工作临时改了好几次，回家时很累。",
      "emotion_tags": ["疲惫", "委屈"],
      "intensity": 4,
      "scene_category": "工作",
      "happy_moment": "下班时看到了很好看的晚霞",
      "ai_observed_emotions": ["疲惫", "委屈"],
      "ai_summary": "反复修改让人疲惫。",
      "ai_self_care_tips": "今晚可以早点休息。",
      "ai_closing_message": "先让今天慢慢结束。",
      "risk_level": "normal"
    }

建议直接把 /api/ai/analyze 的对应字段带入，不要在前端自行改字段名。

响应是保存后的完整记录，额外包含数字 id、字符串 record_id 和 created_at。

### 4.4 查询记录 / 日历

    GET /api/records?user_id=<uuid>&range=365d

查询参数：

| 参数 | 必填 | 默认值 | 说明 |
|---|---:|---|---|
| user_id | 是 | 无 | 缺失时返回空数组 |
| range | 否 | 7d | 支持 7d、30d、365d、all 等 |
| start_date | 否 | 无 | ISO 日期或时间 |
| end_date | 否 | 无 | ISO 日期或时间 |

响应是记录数组，按 created_at 倒序。

    const url = new URL("/api/records", window.location.origin);
    url.searchParams.set("range", "365d");
    url.searchParams.set("user_id", window.USER_ID);
    const records = await fetch(url).then((response) => response.json());

### 4.5 周回顾数据

    GET /api/summary?user_id=<uuid>&range=7d

响应：

    {
      "range": "7d",
      "mood_trend": [
        {
          "date": "2026-07-28",
          "avg_intensity": 3.5,
          "top_emotion": "疲惫"
        }
      ],
      "top_emotions": ["疲惫", "轻松"],
      "top_emotion_counts": [{"label": "疲惫", "count": 3}],
      "top_scenes": ["工作"],
      "top_scene_counts": [{"label": "工作", "count": 4}],
      "happy_moments": ["买到了惦记很久的桂花拿铁"],
      "happy_moments_with_date": [
        {
          "content": "买到了惦记很久的桂花拿铁",
          "date": "2026-07-30"
        }
      ],
      "growth_summary": "……"
    }

无记录时，各数组为空，growth_summary 返回空状态文案。

### 4.6 周回顾朋友来信

    GET /api/weekly-letter?user_id=<uuid>&user_name=<昵称>&range=7d

user_id 必填；user_name 可选。没有昵称时，信件以“亲爱的你：”开头。

响应：

    {
      "range": "7d",
      "record_count": 5,
      "letter": "亲爱的小暖：\n\n……",
      "generated_at": "2026-07-31T08:00:00+00:00"
    }

调用示例：

    async function loadWeeklyLetter(userName) {
      const url = new URL("/api/weekly-letter", window.location.origin);
      url.searchParams.set("user_id", window.USER_ID);
      url.searchParams.set("user_name", userName?.trim() || "");
      url.searchParams.set("range", "7d");

      const response = await fetch(url);
      if (!response.ok) {
        throw new Error("加载来信失败：" + response.status);
      }
      return response.json();
    }

接入要求：

- 当前前端没有用户名字段，需要增加昵称来源；未完成前可不传 user_name
- letter 包含换行，渲染时使用 white-space: pre-line
- 不使用 innerHTML 直接渲染信件，优先 textContent
- 有记录时生产实测约 4–6 秒，需要独立 Loading
- 没有记录时不调用模型，仍返回 200 和一封短的空状态信
- 每次调用都会重新生成，当前没有缓存；不要在重复 render 时反复请求
- 建议一页生命周期只请求一次，重试由用户主动触发

统计和来信可以并行请求。如果希望统计先显示、来信稍后出现，不要使用
Promise.all 阻塞整个页面，应分别维护两个 Loading 状态。

### 4.7 补记开心时刻

    POST /api/moments

关联已有记录：

    {
      "user_id": "uuid",
      "record_id": "123",
      "happy_moment": "回家时看到了晚霞"
    }

新建独立开心时刻：

    {
      "user_id": "uuid",
      "happy_moment": "买到了喜欢的咖啡",
      "scene_category": "生活"
    }

独立新增时 scene_category 必填。

### 4.8 删除记录

    DELETE /api/records/{record_id}

响应：

    {
      "deleted": true,
      "record_id": "123"
    }

这是软删除，后续查询不会返回该记录。

## 5. 页面接入建议

### AI 回复页

- 提交后立刻显示 Loading
- 成功后分别渲染 ai_reply、照顾建议和收尾
- ai_self_care_tips 使用换行拆分
- 保存记录失败时不要抹掉已生成的 AI 回复
- 提供明确重试按钮

### 日历页

- 请求 range=365d 或 range=all
- 使用 created_at.slice(0, 10) 分组
- URL 必须相对当前站点构造
- 分别处理加载、空数据、失败和重试状态

### 周回顾页

- 数据图表来自 /api/summary
- 私人来信来自 /api/weekly-letter
- 两块内容分别 Loading，避免来信阻塞图表
- 来信区保留自然段和信纸式排版，不要把统计卡片塞进信件容器

## 6. 错误处理

常见状态：

- 200：成功；部分 AI 上游失败也可能返回兜底文案
- 422：字段缺失、类型错误或 intensity 超出 1–5
- 500：未被兜底的服务端错误

前端不要直接展示整段服务端异常。给用户显示简短文案，同时在控制台保留状态码。

## 7. 当前已知边界

以下不阻塞 MVP 联调，但公开上线前需要明确：

1. **没有真正的用户认证**

   当前只依赖客户端生成的 user_id。它适合匿名 MVP，不等同于安全登录。

2. **删除接口尚未校验用户归属**

   DELETE /api/records/{record_id} 当前只按记录 ID 删除，没有同时验证 user_id。
   正式开放前应修复；前端也不要向用户暴露可枚举的数据库 ID。

3. **Supabase 免费项目可能因低活跃暂停**

   暂停期间历史数据不可用。测试期可以手动 Resume；正式服务应考虑付费或监控。

4. **周信不缓存**

   每次调用都会产生一次 DeepSeek 请求、延迟和费用。前端需避免重复调用；后续
   可以按 user_id + week 存储生成结果。

5. **AI 接口使用降级响应**

   上游异常时可能返回 HTTP 200 和兜底文字。后续若需精确监控，可增加
   generation_status。

6. **昵称尚未持久化**

   周信支持 user_name，但当前数据库和前端没有统一昵称资料。前端可以先存
   localStorage，后续登录系统上线后迁入用户资料。

## 8. 联调验收清单

- [ ] 同一个 user_id 刷新后保持不变
- [ ] AI 回复能生成并正确拆分字段
- [ ] AI 回复后记录成功入库
- [ ] 日历能读取并按日期显示全部记录
- [ ] 周统计只显示当前用户数据
- [ ] 周信称呼正确、自然段保留
- [ ] 无昵称时显示“亲爱的你”
- [ ] 无记录时周统计和周信都有空状态
- [ ] DeepSeek 请求期间页面有 Loading
- [ ] 请求失败时有重试入口
- [ ] needs_attention / crisis 有对应展示策略

## 9. 相关资料

- 后端仓库：https://github.com/lizzylee1231-del/Warmplace
- 前端仓库：https://github.com/lizzylee1231-del/Warmplace-frontend
- FastAPI 在线文档：https://warmplace-one.vercel.app/docs
- 周信设计：docs/superpowers/specs/2026-07-31-weekly-letter-api-design.md
- 周信实现提交：f15ab87
