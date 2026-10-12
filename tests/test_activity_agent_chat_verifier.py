"""Guard the live acceptance checker against false discovery successes."""

import json
import unittest

from tools.verify_activity_agent_chat import (
    activity_names, activity_responses, claims_current_collector_state, date_mentioned, history_signature,
)


class ActivityAgentChatVerifierTests(unittest.TestCase):
    def test_mentions_and_skill_reads_are_not_activity_invocations(self):
        calls = [
            {"name": "tool_search", "arguments": {"query": "mirally-activity activity_health"}},
            {"name": "read", "arguments": {"path": "skills/mirally-activity/SKILL.md"}},
            {"name": "tool_call", "arguments": {"id": "openclaw:memory-core:memory_search"}},
            {"name": "tool_call", "arguments": {
                "id": "mcp:mirally-activity:mirally-activity__activity_health"}},
            {"name": "mirally-activity__activity_events", "arguments": {}},
        ]
        self.assertEqual(activity_names(calls), [
            "mirally-activity__activity_health", "mirally-activity__activity_events",
        ])

    def test_complete_mcp_content_is_decoded_even_when_duplicate_details_are_truncated(self):
        payload = {"status": "ready", "data_status": "ready", "result": {
            "history": {"available": True, "observation_count": 94},
        }}
        wrapped = {"result": {"content": [{
            "type": "text", "text": "structuredContent:\n" + json.dumps(payload),
        }]}}
        text = json.dumps(wrapped)[:-1] + ', "details": {"unfinished":'
        events = [
            {"seq": 1, "event": {"type": "message", "message": {
                "role": "assistant", "content": [{"type": "toolCall", "id": "call-1",
                "name": "tool_call", "arguments": {
                    "id": "mcp:mirally-activity:mirally-activity__activity_health"}}],
            }}},
            {"seq": 2, "event": {"type": "message", "message": {
                "role": "toolResult", "toolCallId": "call-1", "isError": False,
                "content": [{"type": "text", "text": text}],
            }}},
        ]
        self.assertEqual(activity_responses(events), [("mirally-activity__activity_health", payload)])
        events[1]["event"]["message"]["isError"] = True
        self.assertEqual(activity_responses(events), [])

    def test_wrong_calendar_date_or_missing_timezone_cannot_pass(self):
        timestamp = 1791702575297
        self.assertFalse(date_mentioned("2026-10-10 22:29:35 GMT+8", timestamp))
        self.assertFalse(date_mentioned("2026-10-11", timestamp))
        self.assertTrue(date_mentioned("2026-10-11T07:09:35.297Z", timestamp))

    def test_main_history_comparison_ignores_routine_entry_metadata(self):
        events = [{"seq": 1, "event": {"type": "message", "message": {"role": "user", "content": "hello"}}}]
        before = ({"sessionId": "session-1", "inputTokens": 0}, events)
        after = ({"sessionId": "session-1", "inputTokens": 100}, events)
        self.assertEqual(history_signature(before), history_signature(after))
        self.assertNotEqual(history_signature(before), history_signature(({"sessionId": "session-2"}, events)))

    def test_last_recorded_state_does_not_authorize_a_current_liveness_claim(self):
        self.assertTrue(claims_current_collector_state("采集器现在**没有在跑**"))
        self.assertTrue(claims_current_collector_state("采集器目前处于 stopped 状态"))
        self.assertFalse(claims_current_collector_state("上次记录的采集会话已停止，当前运行状态未核验。"))


if __name__ == "__main__":
    unittest.main()
