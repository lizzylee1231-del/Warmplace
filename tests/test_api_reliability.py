import json
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
import main

USER = "4ea7493f-b5ef-4a13-9a08-69190eb2d691"
OTHER = "ac312fe4-a333-4ffb-8098-0aad55a394a0"
PAYLOAD = {
    "user_id": USER, "mood_text": "完成了今天的小目标", "emotion_tags": ["开心"],
    "intensity": 3, "scene_category": "工作/学习", "happy_moment": None,
    "ai_observed_emotions": ["开心"], "ai_summary": "松了一口气",
    "ai_self_care_tips": "如果想休息，可以歇一会儿", "ai_closing_message": "慢慢来就好",
    "risk_level": "normal",
}
AI = {**{k: PAYLOAD[k] for k in ("ai_observed_emotions", "ai_summary", "ai_self_care_tips", "ai_closing_message", "risk_level")},
      "ai_reply": "这件小事完成了，我也替你高兴。", "updated_profile": "完成了小目标"}


def query_mock(data=None, error=None):
    db = MagicMock()
    query = db.table.return_value
    for method in ("select", "insert", "update", "upsert", "eq", "gte", "lte", "lt", "order", "limit", "range"):
        getattr(query, method).return_value = query
    query.execute.return_value.data = data if data is not None else []
    query.execute.side_effect = error
    return db, query


class AuthenticationTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app, raise_server_exceptions=False)

    def test_all_private_routes_reject_missing_identity(self):
        cases = [("GET", "/api/records", None), ("GET", "/api/records/42", None), ("GET", "/api/summary", None),
                 ("GET", "/api/weekly-letter?user_id=" + USER, None),
                 ("POST", "/api/records", PAYLOAD), ("POST", "/api/ai/analyze", PAYLOAD),
                 ("POST", "/api/records/42/analyze", None),
                 ("POST", "/api/moments", {"user_id": USER, "happy_moment": "test", "scene_category": "test"}),
                 ("DELETE", "/api/records/42", None)]
        for method, url, body in cases:
            with self.subTest(url=url, method=method):
                self.assertEqual(self.client.request(method, url, json=body).status_code, 401)

    def test_forged_token_is_not_accepted(self):
        fake = MagicMock()
        from supabase_auth.errors import AuthApiError
        fake.auth.get_user.side_effect = AuthApiError("bad token", 401, "bad_jwt")
        with patch.object(main, "create_client", return_value=fake), patch.object(main, "SUPABASE_URL", "https://example.supabase.co"), patch.object(main, "SUPABASE_KEY", "test"):
            response = self.client.get("/api/records", headers={"Authorization": "Bearer forged"})
        self.assertEqual(response.status_code, 401)

    def test_verified_token_is_applied_to_request_database(self):
        db, query = query_mock()
        db.auth.get_user.return_value.user.id = USER
        with patch.object(main, "create_client", return_value=db), patch.object(main, "SUPABASE_URL", "https://example.supabase.co"), patch.object(main, "SUPABASE_KEY", "test"):
            response = self.client.get("/api/records", headers={"Authorization": "Bearer verified"})
        self.assertEqual(response.status_code, 200)
        db.auth.get_user.assert_called_once_with("verified")
        db.postgrest.auth.assert_called_once_with("verified")
        query.eq.assert_any_call("user_id", USER)


class PrivateApiTest(unittest.TestCase):
    def setUp(self):
        self.db, self.query = query_mock()
        # Use the public dependency contract; patching no endpoint implementation.
        dependency = getattr(main, "require_user", lambda: None)
        self.dependency = dependency
        main.app.dependency_overrides[dependency] = lambda: SimpleNamespace(user_id=USER, db=self.db)
        self.client = TestClient(main.app, raise_server_exceptions=False)

    def tearDown(self):
        main.app.dependency_overrides.clear()

    def test_cannot_read_or_write_another_users_identity(self):
        for method, url, body in [("GET", "/api/records?user_id=" + OTHER, None),
                                  ("GET", "/api/summary?user_id=" + OTHER, None),
                                  ("GET", "/api/weekly-letter?user_id=" + OTHER, None),
                                  ("POST", "/api/records", {**PAYLOAD, "user_id": OTHER}),
                                  ("POST", "/api/ai/analyze", {**PAYLOAD, "user_id": OTHER}),
                                  ("POST", "/api/moments", {"user_id": OTHER, "happy_moment": "test", "scene_category": "test"})]:
            with self.subTest(url=url):
                self.assertEqual(self.client.request(method, url, json=body).status_code, 403)
        self.db.table.assert_not_called()

    def test_insert_failure_is_not_a_saved_record(self):
        self.query.execute.side_effect = RuntimeError("database down")
        self.assertEqual(self.client.post("/api/records", json=PAYLOAD).status_code, 503)

    def test_empty_insert_result_is_not_success(self):
        self.assertEqual(self.client.post("/api/records", json=PAYLOAD).status_code, 503)

    def test_successful_save_can_be_read_back(self):
        row = {**PAYLOAD, "id": 42, "created_at": "2026-10-23T03:00:00Z", "is_deleted": False}
        self.query.execute.return_value.data = [row]
        saved = self.client.post("/api/records", json=PAYLOAD)
        listed = self.client.get("/api/records?range=all")
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.json()["record_id"], "42")
        self.assertEqual(listed.json()[0]["record_id"], "42")
        self.query.eq.assert_any_call("user_id", USER)

    def test_retry_with_same_request_id_reads_original_record(self):
        key = "9a39965d-a586-4685-9097-899827a10f67"
        row = {**PAYLOAD, "id": 42, "client_request_id": key}
        self.query.execute.side_effect = [SimpleNamespace(data=[]), SimpleNamespace(data=[row])]
        response = self.client.post("/api/records", json={**PAYLOAD, "client_request_id": key})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["record_id"], "42")
        self.query.upsert.assert_called_once()
        self.query.eq.assert_any_call("client_request_id", key)

    def test_note_can_be_saved_before_ai_is_available(self):
        note = {k: PAYLOAD[k] for k in ("mood_text", "emotion_tags", "intensity", "scene_category")}
        self.query.execute.return_value.data = [{**note, "id": 42}]
        with patch.object(main, "call_deepseek") as generate:
            response = self.client.post("/api/records", json=note)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["record_id"], "42")
        generate.assert_not_called()
        inserted = self.query.insert.call_args.args[0]
        self.assertEqual(inserted["mood_text"], note["mood_text"])
        self.assertEqual(inserted["risk_level"], "unknown")

    def test_analysis_updates_saved_record_before_returning(self):
        row = {**PAYLOAD, "id": 42}
        self.query.execute.side_effect = [SimpleNamespace(data=[row]), SimpleNamespace(data=[row])]
        with patch.object(main, "analyze", return_value={**AI, "generation_status": "generated"}) as generate:
            response = self.client.post("/api/records/42/analyze")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["record_id"], "42")
        self.assertEqual(generate.call_args.args[0].mood_text, row["mood_text"])
        update = self.query.update.call_args.args[0]
        self.assertEqual(update["ai_reply"], AI["ai_reply"])
        self.assertNotIn("mood_text", update)
        self.query.eq.assert_any_call("id", 42)
        self.query.eq.assert_any_call("user_id", USER)
        self.query.eq.assert_any_call("is_deleted", False)
        self.query.insert.assert_not_called()

    def test_reply_can_be_loaded_by_record_id_without_generating_again(self):
        self.query.execute.return_value.data = [{**PAYLOAD, "id": 42, "ai_reply": AI["ai_reply"]}]
        with patch.object(main, "call_deepseek") as generate:
            response = self.client.get("/api/records/42")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["record_id"], "42")
        self.assertEqual(response.json()["ai_reply"], AI["ai_reply"])
        self.query.eq.assert_any_call("id", 42)
        self.query.eq.assert_any_call("user_id", USER)
        self.query.eq.assert_any_call("is_deleted", False)
        generate.assert_not_called()
        self.query.update.assert_not_called()

    def test_loading_missing_foreign_or_deleted_record_is_404(self):
        response = self.client.get("/api/records/42")
        self.assertEqual(response.status_code, 404)
        self.query.eq.assert_any_call("user_id", USER)
        self.query.eq.assert_any_call("is_deleted", False)

    def test_completed_reply_is_reused_on_analysis_retry(self):
        self.query.execute.return_value.data = [{**PAYLOAD, "id": 42, "ai_reply": AI["ai_reply"]}]
        with patch.object(main, "analyze") as generate:
            response = self.client.post("/api/records/42/analyze")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["ai_reply"], AI["ai_reply"])
        self.assertEqual(response.json()["generation_status"], "generated")
        self.assertEqual(response.json()["record_id"], "42")
        generate.assert_not_called()
        self.query.update.assert_not_called()

    def test_analysis_failure_leaves_original_note_untouched(self):
        self.query.execute.return_value.data = [{**PAYLOAD, "id": 42}]
        with patch.object(main, "analyze", return_value={"generation_status": "fallback"}):
            response = self.client.post("/api/records/42/analyze")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["generation_status"], "fallback")
        self.query.update.assert_not_called()
        self.query.insert.assert_not_called()

    def test_analysis_rejects_missing_or_other_users_record(self):
        with patch.object(main, "analyze") as generate:
            response = self.client.post("/api/records/42/analyze")
        self.assertEqual(response.status_code, 404)
        self.query.eq.assert_any_call("user_id", USER)
        generate.assert_not_called()

    def test_ai_update_failure_is_not_reported_as_persisted(self):
        self.query.execute.side_effect = [SimpleNamespace(data=[{**PAYLOAD, "id": 42}]), RuntimeError("database down")]
        with patch.object(main, "analyze", return_value={**AI, "generation_status": "generated"}):
            response = self.client.post("/api/records/42/analyze")
        self.assertEqual(response.status_code, 503)
        self.query.insert.assert_not_called()

    def test_deleted_record_during_generation_is_not_updated(self):
        self.query.execute.side_effect = [SimpleNamespace(data=[{**PAYLOAD, "id": 42}]), SimpleNamespace(data=[])]
        with patch.object(main, "analyze", return_value={**AI, "generation_status": "generated"}):
            response = self.client.post("/api/records/42/analyze")
        self.assertEqual(response.status_code, 503)
        self.query.eq.assert_any_call("is_deleted", False)

    def test_weekly_provider_failure_is_identified_as_fallback(self):
        row = {**PAYLOAD, "id": 42, "created_at": "2026-10-23T03:00:00Z"}
        self.query.execute.return_value.data = [row]
        with patch.object(main, "call_deepseek", side_effect=RuntimeError("offline")):
            response = self.client.get("/api/weekly-letter")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["generation_status"], "fallback")

    def test_summary_still_returns_statistics_when_model_fails(self):
        row = {**PAYLOAD, "id": 42, "created_at": "2026-10-23T03:00:00Z"}
        self.query.execute.return_value.data = [row]
        with patch.object(main, "call_deepseek", side_effect=RuntimeError("offline")):
            response = self.client.get("/api/summary")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["generation_status"], "fallback")
        self.assertEqual(len(response.json()["mood_trend"]), 1)

    def test_read_error_is_not_an_empty_history(self):
        self.query.execute.side_effect = RuntimeError("database down")
        for path in ("/api/records", "/api/summary", "/api/weekly-letter"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 503)

    def test_delete_is_scoped_to_owner_and_not_already_deleted(self):
        self.query.execute.return_value.data = [{"id": 42}]
        response = self.client.delete("/api/records/42")
        self.assertEqual(response.status_code, 200)
        self.query.eq.assert_any_call("user_id", USER)
        self.query.eq.assert_any_call("is_deleted", False)

    def test_missing_or_foreign_delete_is_404(self):
        self.assertEqual(self.client.delete("/api/records/42").status_code, 404)

    def test_missing_moment_is_404(self):
        response = self.client.post("/api/moments", json={"user_id": USER, "happy_moment": "test", "record_id": "42"})
        self.assertEqual(response.status_code, 404)

    def test_ai_invalid_outputs_have_identifiable_fallback(self):
        for output in ("not JSON", "[]", "{}", json.dumps({**AI, "ai_reply": ""}), json.dumps({**AI, "risk_level": "safe"})):
            with self.subTest(output=output), patch.object(main, "DEEPSEEK_API_KEY", "test"), patch.object(main, "call_deepseek", return_value=output):
                response = self.client.post("/api/ai/analyze", json=PAYLOAD)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["generation_status"], "fallback")
                self.assertEqual(response.json()["risk_level"], "unknown")
                self.assertTrue(response.json()["ai_reply"].strip())
        self.query.insert.assert_not_called()

    def test_valid_ai_is_marked_generated(self):
        with patch.object(main, "DEEPSEEK_API_KEY", "test"), patch.object(main, "call_deepseek", return_value=json.dumps(AI)):
            response = self.client.post("/api/ai/analyze", json=PAYLOAD)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["generation_status"], "generated")
        self.assertEqual(response.json()["risk_level"], "normal")

    def test_provider_outage_does_not_claim_normal_risk(self):
        with patch.object(main, "DEEPSEEK_API_KEY", "test"), patch.object(main, "call_deepseek", side_effect=RuntimeError("provider down")):
            response = self.client.post("/api/ai/analyze", json=PAYLOAD)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["risk_level"], "unknown")

    def test_limits_and_date_validation(self):
        for payload in ({**PAYLOAD, "mood_text": " "}, {**PAYLOAD, "mood_text": "x" * 4001}, {**PAYLOAD, "risk_level": "anything"}):
            self.assertEqual(self.client.post("/api/records", json=payload).status_code, 422)
        for query in ("range=-3d", "range=99999999999999d", "start_date=bad", "start_date=2026-10-23&end_date=2026-10-01"):
            self.assertEqual(self.client.get("/api/records?" + query).status_code, 422)

    def test_explicit_start_replaces_default_range(self):
        self.client.get("/api/records?start_date=2026-01-01&end_date=2026-01-31")
        self.assertEqual(self.query.gte.call_count, 1)
        self.query.lt.assert_called_once_with("created_at", "2026-02-01T00:00:00+00:00")


class HealthTest(unittest.TestCase):
    def test_readiness_fails_when_database_unavailable(self):
        db, query = query_mock(error=RuntimeError("database down"))
        with patch.object(main, "supabase", db):
            response = TestClient(main.app).get("/api/ready")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["database"], "unavailable")

    def test_openapi_is_served_under_api_path(self):
        self.assertEqual(TestClient(main.app).get("/api/openapi.json").status_code, 200)
