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
# 复制 .env.example 为 .env，填写后端密钥、公开发布密钥和 AI 密钥
uvicorn main:app --reload
```

使用 Python 3.12。前端使用 Supabase 匿名认证；私有接口必须携带 `Authorization: Bearer <access_token>`。后端向 Auth 验证令牌，从返回身份取得用户 ID，并在请求独立客户端上用同一令牌访问数据库。`user_id` 仅作兼容参数，若与认证身份不符则返回 403。服务端 secret/service-role key 只放在部署环境，不能交给浏览器。

- 本地/API 文档：`/api/docs`；OpenAPI：`/api/openapi.json`。
- 存活检查：`/api/health`；数据库就绪检查：`/api/ready`。后者实际探测数据库，不自动调用付费 AI；`configured_not_probed` 不代表模型调用成功。
- 保存失败和查询错误：503；记录不存在或不属于本人：404；无会话：401。
- AI 响应包含 `generation_status`。`fallback` 是通用提示，风险状态为 `unknown`，不表示已完成分析。
- 保存请求可附 UUID `client_request_id`；相同用户、相同请求重试返回原记录，不覆盖原内容。

## 测试

```bash
python run_tests.py
```

测试不加载本地 `.env`，数据库和模型请求均被隔离。包含原有周信测试及身份隔离、数据库失败、模型异常、幂等重试与请求校验。数据库策略的回滚验证脚本位于 `tests/rls_assertions.sql`。

## 配套发布

本次身份升级需要与前端一起发布。**不要单独合并本 PR 后假定线上功能已经修复。** 先阅读 [发布步骤和旧数据边界](docs/security-release-2026-10-23.md)。

业务范围：情绪记录、AI 回应、日历历史、统计与周信。仍待完善：共享存储支持的限流/缓存、用户时区统计、完整账号恢复与旧记录人工迁移。当前匿名会话清除后无法自行找回历史，不应宣称具备永久账号恢复能力。
