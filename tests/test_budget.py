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

    def test_null_optional_argument_is_dropped(self):
        # H2 (v4): model gửi component=null, tool cho sẵn lỗi và agent coi là github chưa kiểm tra được.
        result = self.run_one(StatusTools(), 'get_status', '{"provider":"github","component":null}')
        self.assertEqual(result['provider'], 'github')
        self.assertNotIn('error', result)

    def test_success_has_no_failure_note(self):
        result = self.run_one(StatusTools(), 'get_status', '{"provider":"github"}')
        self.assertNotIn('CHƯA được kiểm tra', result.get('agent_note', ''))


class PartialListTests(unittest.TestCase):
    """A1: get_status chỉ trả 5 trong số nhiều incident khớp; model phải được báo là danh sách chưa đủ."""
    def first_result(self, tools, name, arguments):
        fake = FakeProvider([tool_call('1', name, arguments), final()])
        run_agent('x', fake, tools)
        return json.loads(fake.histories[1][-1]['content'])

    def test_partial_incident_list_gets_note(self):
        result = self.first_result(StatusTools(), 'get_status', '{"provider":"github","component":"Actions"}')
        total, shown = result['matching_incidents_in_last_50'], len(result['incidents'])
        self.assertGreater(total, shown)
        self.assertIn(f'{shown}/{total}', result['agent_note'])
        self.assertIn('Không được nói là không có incident khác', result['agent_note'])

    def test_complete_list_has_no_note(self):
        result = self.first_result(StatusTools(), 'get_status', '{"provider":"cloudflare","component":"Spectrum"}')
        self.assertEqual(result['matching_incidents_in_last_50'], len(result['incidents']))
        self.assertNotIn('agent_note', result)

    def test_bonus_match_count_supported(self):
        from bonus.tools import BonusTools
        result = self.first_result(BonusTools(), 'get_github_status', '{}')
        if result['match_count'] > len(result['incidents']):
            self.assertIn('agent_note', result)


class ProviderCoverageTests(unittest.TestCase):
    """A1 (v3): mở nhiều incident GitHub nhưng không mở incident Cloudflare nào, nên thiếu URL Cloudflare."""
    GH_STATUS = tool_call('s1', 'get_status', '{"provider":"github","component":"Actions"}')
    CF_STATUS = tool_call('s2', 'get_status', '{"provider":"cloudflare","component":"Spectrum"}')
    GH_DETAILS = tool_call('d1', 'get_incident_details', DETAILS)
    CF_DETAILS = tool_call('d2', 'get_incident_details', '{"provider":"cloudflare","incident_id":"c60k3g4vsoc1"}')
    CF_URL = 'https://www.cloudflarestatus.com/incidents/c60k3g4vsoc1'

    def events(self, result, name):
        return [t for t in result['trace'] if t['event'] == name]

    def test_coverage_warning_for_searched_provider_without_opened_incident(self):
        fake = FakeProvider([self.GH_STATUS, self.CF_STATUS, self.GH_DETAILS, self.CF_DETAILS, final(URL + ' ' + self.CF_URL)])
        result = run_agent('x', fake, StatusTools(), max_steps=6)
        self.assertEqual(result['status'], 'completed')
        warning = self.events(result, 'coverage_warning')
        self.assertEqual([(w['step'], w['pending']) for w in warning], [(4, ['cloudflare'])])
        reminder = fake.histories[3][-1]
        self.assertEqual(reminder['role'], 'user')
        self.assertIn('chưa có incident nào được đọc: cloudflare', reminder['content'])
        self.assertEqual(fake.histories[3][-2]['role'], 'tool')  # tool result vẫn đứng trước lời nhắc

    def test_coverage_warning_for_provider_named_in_question_but_unchecked(self):
        fake = FakeProvider([self.GH_STATUS, self.CF_STATUS, final()])
        result = run_agent('So sánh GitHub Actions và Cloudflare Spectrum', fake, StatusTools(), max_steps=4)
        self.assertEqual([w['pending'] for w in self.events(result, 'coverage_warning')], [['cloudflare']])

    def test_no_coverage_warning_for_single_provider_question(self):
        fake = FakeProvider([self.GH_STATUS, self.GH_DETAILS, final(URL)])
        result = run_agent('GitHub Actions có sự cố không?', fake, StatusTools(), max_steps=4)
        self.assertFalse(self.events(result, 'coverage_warning'))

    def test_parallel_calls_count_as_checked(self):
        both = {'message': {'role': 'assistant', 'content': None, 'tool_calls': []}, 'finish': 'tool_calls', 'usage': {},
                'calls': [{'id': 'a', 'name': 'get_status', 'arguments': '{"provider":"github"}'},
                          {'id': 'b', 'name': 'get_status', 'arguments': '{"provider":"cloudflare"}'}]}
        fake = FakeProvider([both, final()])
        result = run_agent('GitHub và Cloudflare có sự cố không?', fake, StatusTools(), max_steps=4)
        self.assertFalse(self.events(result, 'coverage_warning'))

    def test_unfiltered_status_does_not_count_as_search(self):
        fake = FakeProvider([tool_call('s0', 'get_status', '{"provider":"cloudflare"}'), self.GH_STATUS,
                             self.GH_DETAILS, final(URL)])
        result = run_agent('x', fake, StatusTools(), max_steps=6)
        self.assertFalse(self.events(result, 'coverage_warning'))

    def test_source_gate_requires_url_for_each_provider(self):
        fake = FakeProvider([self.GH_DETAILS, self.CF_DETAILS, final('Chỉ có ' + URL), final(URL + ' và ' + self.CF_URL)])
        result = run_agent('x', fake, StatusTools(), max_steps=6)
        self.assertEqual(result['status'], 'completed')
        gate = self.events(result, 'validation_missing_source')
        self.assertEqual(len(gate), 1)
        self.assertEqual(gate[0]['missing_providers'], ['cloudflare'])
        self.assertIn(self.CF_URL, fake.histories[3][-1]['content'])

    def test_budget_warning_names_unopened_provider(self):
        fake = FakeProvider([self.GH_STATUS, self.CF_STATUS, self.GH_DETAILS, final(URL + ' — cloudflare chưa có URL incident')])
        run_agent('x', fake, StatusTools(), max_steps=5)
        reminder = fake.histories[3][-1]
        self.assertEqual(reminder['role'], 'user')
        self.assertIn('Chưa mở incident nào của cloudflare', reminder['content'])


if __name__ == '__main__':
    unittest.main()
