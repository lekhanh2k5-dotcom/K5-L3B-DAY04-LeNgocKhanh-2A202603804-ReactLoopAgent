"""Bonus: retry 429 có giới hạn, pagination note và cắt dữ liệu nhiễu trong agent loop."""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import agent
from agent import run_agent
from bonus.tools import BonusTools
from test_lab import FakeProvider, tool_call, final


def contents(fake, index):
    """Các tool result (đã parse) trong history của lần gọi model thứ index."""
    return [json.loads(m['content']) for m in fake.histories[index] if m['role'] == 'tool']


class RateLimitTests(unittest.TestCase):
    def test_blocks_after_three_rate_limits(self):
        calls = [tool_call(str(i), 'get_vercel_status', '{}') for i in range(4)]
        fake = FakeProvider(calls + [final('Vercel chưa kiểm tra được')])
        result = run_agent('x', fake, BonusTools(scenario='rate_limit_always'), max_steps=8)
        self.assertEqual(result['status'], 'completed')
        tool_events = [t for t in result['trace'] if t['event'] == 'tool']
        self.assertEqual(len(tool_events), 3)  # lần thứ 4 không gọi tool thật
        self.assertEqual([t['event'] for t in result['trace']].count('retry_blocked'), 1)
        blocked = contents(fake, 4)[-1]
        self.assertEqual(blocked['error'], 'RETRY_LIMIT_REACHED')
        self.assertIn('CHƯA kiểm tra được', blocked['agent_note'])

    def test_rate_limit_note_says_not_outage_and_retry_succeeds(self):
        fake = FakeProvider([tool_call('a', 'get_vercel_status', '{}'), tool_call('b', 'get_vercel_status', '{}'), final()])
        run_agent('x', fake, BonusTools(scenario='rate_limit_once'), max_steps=5)
        first = contents(fake, 1)[-1]
        self.assertEqual(first['error'], 'RATE_LIMITED')
        self.assertIn('KHÔNG phải sự cố của vercel', first['agent_note'])
        self.assertEqual(contents(fake, 2)[-1]['provider'], 'vercel')

    def test_retry_after_is_honored_with_cap(self):
        class SlowTools(BonusTools):
            def execute(self, name, args):
                return {'ok': False, 'error': 'RATE_LIMITED', 'retry_after': '120', 'retriable': True}
        fake = FakeProvider([tool_call('a', 'get_npm_status', '{}'), final()])
        with patch.object(agent.time, 'sleep') as sleep:
            run_agent('x', fake, SlowTools(), max_steps=3)
        sleep.assert_called_once_with(agent.MAX_RETRY_WAIT)


class ContextTests(unittest.TestCase):
    def test_long_lists_are_compacted(self):
        fake = FakeProvider([tool_call('a', 'get_vercel_status', '{}'), final()])
        run_agent('x', fake, BonusTools(scenario='noisy'), max_steps=3)
        status = contents(fake, 1)[-1]
        self.assertEqual(len(status['distractors']), agent.LIST_LIMIT)
        self.assertEqual(status['distractors_omitted_by_agent'], 160 - agent.LIST_LIMIT)
        self.assertEqual(status['provider'], 'vercel')

    def test_pagination_note_when_more_pages(self):
        args = '{"provider":"vercel","incident_id":"sps6n39gm4sg","offset":0,"limit":2}'
        details = BonusTools().execute('get_incident_details', json.loads(args))
        self.assertIsNotNone(details['next_offset'])
        fake = FakeProvider([tool_call('a', 'get_incident_details', args), final(details['url'])])
        run_agent('x', fake, BonusTools(), max_steps=3)
        self.assertIn(f"offset={details['next_offset']}", contents(fake, 1)[-1]['agent_note'])

    def test_bonus_prompt_keeps_tool_prompt_and_adds_rules(self):
        fake = FakeProvider([final()])
        run_agent('x', fake, BonusTools())
        system = fake.histories[0][0]['content']
        self.assertTrue(system.startswith(BonusTools.system_prompt))
        self.assertIn('RATE_LIMITED', system)


class ProviderErrorTests(unittest.TestCase):
    def upstream_error(self):
        return {'message': {'role': 'assistant', 'content': None}, 'calls': [], 'finish': 'error', 'usage': {}}

    def test_empty_finish_error_is_retried_once(self):
        fake = FakeProvider([self.upstream_error(), final('ok')])
        result = run_agent('x', fake, BonusTools(), max_steps=2)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual([t['event'] for t in result['trace']].count('model_retry'), 1)

    def test_repeated_finish_error_is_not_completed(self):
        fake = FakeProvider([self.upstream_error(), self.upstream_error()])
        self.assertEqual(run_agent('x', fake, BonusTools())['status'], 'error')


if __name__ == '__main__':
    unittest.main()
