"""Bài 4: agent nhắc model chốt câu trả lời khi sắp hết max_steps (lỗi A5 trong eval_before)."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent import run_agent
from status_tools import StatusTools
from test_lab import FakeProvider, tool_call, final

DETAILS = '{"provider":"github","incident_id":"xr3myzj7ms51"}'
URL = 'https://stspg.io/pjgptg3cfc6n'


class BudgetWarningTests(unittest.TestCase):
    def test_warning_sent_when_two_steps_left_after_tools(self):
        fake = FakeProvider([tool_call('1', 'get_incident_details', DETAILS), final('Đã xử lý. ' + URL)])
        result = run_agent('x', fake, StatusTools(), max_steps=3)
        self.assertEqual(result['status'], 'completed')
        reminder = fake.histories[1][-1]
        self.assertEqual(reminder['role'], 'user')
        self.assertIn('Đừng gọi thêm tool', reminder['content'])
        self.assertIn(URL, reminder['content'])
        # Tool result vẫn đứng ngay sau assistant message, trước lời nhắc.
        self.assertEqual(fake.histories[1][-2]['tool_call_id'], '1')
        self.assertTrue(any(x['event'] == 'budget_warning' for x in result['trace']))

    def test_reserve_step_allows_source_revision(self):
        # Kịch bản A5: model trả lời thiếu URL ở lượt áp chót, vẫn còn một lượt để sửa.
        fake = FakeProvider([tool_call('1', 'get_incident_details', DETAILS), final('Thiếu nguồn'), final('Có nguồn ' + URL)])
        result = run_agent('x', fake, StatusTools(), max_steps=3)
        self.assertEqual(result['status'], 'completed')
        self.assertIn(URL, result['answer'])

    def test_no_warning_with_enough_budget(self):
        fake = FakeProvider([tool_call('1', 'get_status', '{"provider":"github"}'), final()])
        result = run_agent('x', fake, StatusTools(), max_steps=6)
        self.assertFalse(any(x['event'] == 'budget_warning' for x in result['trace']))
        self.assertEqual(fake.histories[1][-1]['role'], 'tool')


class FailureNoteTests(unittest.TestCase):
    """H7: tool lỗi phải được đánh dấu là chưa kiểm tra, không phải trạng thái dịch vụ."""
    def run_one(self, tools, name, arguments):
        fake = FakeProvider([tool_call('1', name, arguments), final()])
        run_agent('x', fake, tools)
        return json.loads(fake.histories[1][-1]['content'])

    def test_timeout_gets_unverified_note(self):
        result = self.run_one(StatusTools(fail=True), 'get_status', '{"provider":"cloudflare"}')
        self.assertEqual(result['error'], 'TimeoutError')
        self.assertIn('cloudflare CHƯA được kiểm tra', result['agent_note'])

    def test_caller_errors_and_not_found_have_no_note(self):
        for name, arguments in [('get_status', '{'), ('missing', '{}'),
                                ('get_incident_details', '{"provider":"github","incident_id":"missing"}')]:
            with self.subTest(name=name, arguments=arguments):
                self.assertNotIn('agent_note', self.run_one(StatusTools(), name, arguments))

    def test_success_has_no_note(self):
        self.assertNotIn('agent_note', self.run_one(StatusTools(), 'get_status', '{"provider":"github"}'))


if __name__ == '__main__':
    unittest.main()
