import json
import re
import unittest
from fractions import Fraction as F
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def value(text):
    text = re.sub(r' (км|руб\.)$', '', text).replace(',', '.')
    parts = text.split()
    return F(parts[0]) + F(parts[1]) if len(parts) == 2 else F(text)


class Questions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bank = json.loads((ROOT / 'questions.json').read_text())
        cls.refs = json.loads((ROOT / 'question_sources.json').read_text())
        cls.items = {q['id']: q for q in cls.bank}

    def test_format_and_topics(self):
        keys = {'id', 'subject', 'topic', 'grade_min', 'grade_max', 'text', 'options',
                'answer', 'explanation', 'difficulty', 'curriculum_id'}
        self.assertEqual(len(self.items), len(self.bank))
        self.assertEqual(len({(q['subject'], q['grade_min'], q['text'].strip().casefold(), tuple(sorted(q['options']))) for q in self.bank}), len(self.bank))
        for q in self.bank:
            with self.subTest(q=q['id']):
                self.assertEqual(set(q), keys)
                self.assertIn(q['subject'], ('math', 'russian', 'informatics'))
                self.assertEqual(type(q['grade_min']), int)
                self.assertEqual(q['grade_min'], q['grade_max'])
                self.assertIn(q['grade_min'], range(7 if q['subject'] == 'informatics' else 5, 12))
                self.assertEqual(q['curriculum_id'], f"{q['subject']}_{q['grade_min']}")
                self.assertTrue(q['topic'].strip())
                self.assertIn(q['difficulty'], ('easy', 'medium', 'hard'))
                self.assertEqual(type(q['answer']), int)
                self.assertIn(q['answer'], range(4))
                self.assertEqual(len(q['options']), 4)
                self.assertEqual(len(set(q['options'])), 4)
                self.assertTrue(all(isinstance(s, str) and s.strip() for s in [q['text'], q['explanation'], *q['options']]))

    def test_source_links(self):
        self.assertEqual(len(self.refs['questions']), 12)
        for qid, ref in self.refs['questions'].items():
            with self.subTest(q=qid):
                self.assertIn(qid, self.items)
                source = self.refs['sources'][ref['source']]
                self.assertTrue(source['url'].startswith('https://'))
                self.assertRegex(source['sha256'], r'^[a-f0-9]{64}$')
                self.assertGreater(ref['page'], 0)
                self.assertEqual(ref['mode'], 'adapted')

    def test_computed_answers(self):
        checks = {
            1: F(2, 7) + F(3, 7),
            2: F('2.96') - F('1.03'),
            3: max(F(v) for v in ['9.8', '10.14', '10.3', '9.4']),
            4: next(x for x in map(F, ['8/5', '3/12', '7/7', '9/6']) if x < 1),
            5: next(x for x in map(F, ['5/9', '2/8', '6/4', '1/3']) if x >= 1),
            6: next(x for x in map(F, ['2/7', '15/19', '20/9', '11/8']) if x < F(1, 2)),
            7: F(2) + F(3, 7),
            8: F(13, 5),
            9: F(7, 12),
            10: 60 * (1 - F(2, 5)),
            11: F(20) / (1 - F(1, 4) - F(1, 3)),
            12: min(F(price) / F(mass) for mass, price in [('0.2', 52), ('0.25', 62), ('0.5', 125), ('0.2', 85)]),
        }
        for n, expected in checks.items():
            q = self.items[f'math_5_school_{n:04d}']
            with self.subTest(q=q['id']):
                opts = list(map(value, q['options']))
                self.assertEqual(len(set(opts)), 4)
                self.assertEqual(opts.count(expected), 1)
                self.assertEqual(opts[q['answer']], expected)

    def test_display(self):
        from view import question, render
        for qid in self.refs['questions']:
            q = self.items[qid]
            with self.subTest(q=qid):
                text = question(q, 0, 5, 'Тренировка')
                self.assertLessEqual(len(render(text)), 4000)
                self.assertNotIn('http', text)
                self.assertTrue(all(v in text for v in q['options']))


if __name__ == '__main__':
    unittest.main()
