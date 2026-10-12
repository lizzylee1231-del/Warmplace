# 暖窝 Warm Nest · 后端

暖窝是一款面向女性的私密情绪记录 App：用户写下当天的心情，AI 用朋友的口吻回应，不诊断、不说教；每周再收到一封回顾这一周的"周信"。

- 体验地址：https://warmnest.top
- 前端仓库：https://github.com/lizzylee1231-del/Warmplace-frontend
- 来源：Herstory 黑客松作品（4 人团队，获优秀奖）

## 接口一览

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| POST | `/api/records` | 保存一条情绪记录 |
| GET | `/api/records` | 查看历史记录 |
| DELETE | `/api/records/{id}` | 删除记录 |
| POST | `/api/ai/analyze` | 生成 AI 陪伴回应 |
| GET | `/api/summary` | 一段时间的情绪回顾 |
| GET | `/api/weekly-letter` | 生成周信 |
| POST | `/api/moments` | 记录生活片段 |
| GET | `/api/health` | 健康检查 |

## 技术栈

FastAPI · Supabase · DeepSeek API · Vercel

## 本地运行

```bash
pip install -r requirements.txt
# 在 .env 中配置 SUPABASE_URL、SUPABASE_KEY、DEEPSEEK_API_KEY
uvicorn main:app --reload
```
