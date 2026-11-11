"""Chấm lại kết quả eval đã có bằng một judge khác, không chạy lại agent.

Dùng đúng grade_case của lab (cùng rubric và kiểm tra bằng code); chỉ đổi judge để đo
thiên lệch tự chấm. Ví dụ:
    python analysis/rejudge.py --judge-provider openrouter --judge-model google/gemini-2.5-flash \
        eval_before.json eval_after.json --out-dir rejudged
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'evals'))
from grading import grade_case, summarize
from providers import Provider


def main():
    p = argparse.ArgumentParser(description='Chấm lại file eval bằng judge khác')
    p.add_argument('files', nargs='+')
    p.add_argument('--judge-provider', required=True)
    p.add_argument('--judge-model', required=True)
    p.add_argument('--out-dir', default='rejudged')
    p.add_argument('--workers', type=int, default=6)
    args = p.parse_args()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(exist_ok=True)
    for name in args.files:
        report = json.loads(Path(name).read_text(encoding='utf-8'))
        bonus = report['config'].get('dataset') == 'bonus'
        cases = {c['id']: c for c in json.loads((ROOT / ('bonus/cases.json' if bonus else 'evals/cases.json')).read_text(encoding='utf-8'))}

        def regrade(item):
            grade = grade_case(cases[item['id']], item['result'], Provider(args.judge_provider, args.judge_model))
            return {**item, 'judge_provider': args.judge_provider, 'judge_model': args.judge_model,
                    'original_score': item['grade'].get('score'), 'grade': grade}

        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            items = list(pool.map(regrade, report['cases']))
        out = {'summary': summarize(items),
               'config': {**report['config'], 'judge_provider': args.judge_provider, 'judge_model': args.judge_model,
                          'rejudged_from': name},
               'cases': items}
        target = out_dir / Path(name).name
        target.write_text(json.dumps(out, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(f"{name}: {report['summary'].get('score_0_100')} -> {out['summary']['score_0_100']} "
              f"({out['summary']['scored_cases']}/{out['summary']['total_cases']} chấm được) -> {target}")


if __name__ == '__main__':
    main()
