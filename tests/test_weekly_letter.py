import unittest
from unittest.mock import patch

import main


class WeeklyLetterHelpersTest(unittest.TestCase):
    def test_build_letter_greeting_uses_trimmed_name(self):
        self.assertEqual(main.build_letter_greeting("  小暖  "), "亲爱的小暖：")

    def test_build_letter_greeting_uses_fallback_for_missing_name(self):
        self.assertEqual(main.build_letter_greeting(""), "亲爱的你：")
        self.assertEqual(main.build_letter_greeting(None), "亲爱的你：")

    def test_build_letter_greeting_removes_newlines_and_limits_length(self):
        greeting = main.build_letter_greeting("小暖\n管理员")
        long_greeting = main.build_letter_greeting("暖" * 100)

        self.assertNotIn("\n", greeting)
        self.assertLessEqual(len(long_greeting), 35)

    def test_build_weekly_letter_context_contains_bounded_source_facts(self):
        records = [
            {
                "created_at": "2026-07-30T08:00:00+00:00",
                "mood_text": "完成了拖延很久的任务",
                "emotion_tags": ["轻松", "开心"],
                "intensity": 2,
                "scene_category": "工作",
                "happy_moment": "买到桂花拿铁",
                "ai_summary": "如释重负",
            }
        ]

        context = main.build_weekly_letter_context(records)

        self.assertEqual(context["record_count"], 1)
        self.assertEqual(context["top_emotions"], ["轻松", "开心"])
        self.assertEqual(context["top_scenes"], ["工作"])
        self.assertIn("完成了拖延很久的任务", context["records_text"])
        self.assertIn("买到桂花拿铁", context["records_text"])

    def test_build_weekly_letter_context_truncates_long_fields(self):
        records = [
            {
                "created_at": "2026-07-30T08:00:00+00:00",
                "mood_text": "心" * 2000,
                "emotion_tags": ["平静"],
                "intensity": 2,
                "scene_category": "生活",
                "happy_moment": None,
                "ai_summary": "慢" * 1000,
            }
        ]

        context = main.build_weekly_letter_context(records)

        self.assertLess(len(context["records_text"]), 1600)


class WeeklyLetterRouteTest(unittest.TestCase):
    @patch("main.call_deepseek")
    @patch("main.get_records")
    def test_weekly_letter_generates_friend_letter(
        self, mock_get_records, mock_call_deepseek
    ):
        mock_get_records.return_value = [
            {
                "created_at": "2026-07-30T08:00:00+00:00",
                "mood_text": "完成了拖延很久的任务",
                "emotion_tags": ["轻松"],
                "intensity": 2,
                "scene_category": "工作",
                "happy_moment": "买到桂花拿铁",
                "ai_summary": "如释重负",
            }
        ]
        mock_call_deepseek.return_value = (
            "那件拖了很久的事终于完成了，我也替你松了一口气。"
        )

        result = main.get_weekly_letter(
            user_id="user-1", user_name="小暖", range="7d"
        )

        self.assertEqual(result["range"], "7d")
        self.assertEqual(result["record_count"], 1)
        self.assertTrue(result["letter"].startswith("亲爱的小暖：\n\n"))
        self.assertIn("替你松了一口气", result["letter"])
        self.assertIn("generated_at", result)
        mock_get_records.assert_called_once_with("7d", "user-1")
        messages = mock_call_deepseek.call_args.args[0]
        self.assertIn("只写正文", messages[0]["content"])
        self.assertIn("完成了拖延很久的任务", messages[1]["content"])

    @patch("main.call_deepseek")
    @patch("main.get_records", return_value=[])
    def test_weekly_letter_skips_model_when_no_records(
        self, mock_get_records, mock_call_deepseek
    ):
        result = main.get_weekly_letter(
            user_id="user-1", user_name="", range="7d"
        )

        self.assertEqual(result["record_count"], 0)
        self.assertTrue(result["letter"].startswith("亲爱的你：\n\n"))
        mock_call_deepseek.assert_not_called()

    @patch("main.call_deepseek", side_effect=RuntimeError("provider down"))
    @patch("main.get_records")
    def test_weekly_letter_returns_fallback_on_provider_failure(
        self, mock_get_records, mock_call_deepseek
    ):
        mock_get_records.return_value = [
            {
                "created_at": "2026-07-30T08:00:00+00:00",
                "mood_text": "今天很累",
                "emotion_tags": ["疲惫"],
                "intensity": 3,
                "scene_category": "工作",
                "happy_moment": None,
                "ai_summary": "需要休息",
            }
        ]

        result = main.get_weekly_letter(
            user_id="user-1", user_name="小暖", range="7d"
        )

        self.assertEqual(result["record_count"], 1)
        self.assertTrue(result["letter"].startswith("亲爱的小暖：\n\n"))
        self.assertIn("想写这封信", result["letter"])


if __name__ == "__main__":
    unittest.main()
