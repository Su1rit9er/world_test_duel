import json
import os
import subprocess
import sys
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

from app import Reply
from max_adapter import Buttons, prepare
from quiz import Quiz
from screens import rows


ROOT = Path(__file__).resolve().parents[1]


class Routing(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.quiz = Quiz(Path(self.tmp.name) / 'stats.db')
        self.app = self.quiz.app
        self.ui = Buttons(self.app)
        self.seq = 0

    def text(self, text):
        self.seq += 1
        return prepare(self.ui, {'update_type': 'message_created', 'message': {
            'recipient': {'chat_type': 'dialog'}, 'sender': {'user_id': 1},
            'body': {'mid': str(self.seq), 'text': text}}})

    def click(self, key, uid=1):
        self.seq += 1
        return prepare(self.ui, {'update_type': 'message_callback', 'callback': {
            'user': {'user_id': uid}, 'callback_id': str(self.seq), 'payload': key}})

    def test_new_feature_registers_menu_alias_and_callback(self):
        self.app.add('/about', lambda uid, text: 'Данные новой функции', aliases=('о боте',),
                     title='О боте', view='progress')
        self.text('профиль')
        key = next(k for k, (_, cmd) in self.ui.keys[1].items() if cmd == '/about')
        first = self.click(key)
        self.text('меню')
        second = self.click(key)
        direct = self.app.text(1, 'О БОТЕ')
        self.assertEqual(direct[0].text, 'Данные новой функции')
        for jobs in (first, second):
            self.assertEqual(len([p for p, _, _ in jobs if p == '/answers']), 1)
            self.assertTrue(any('Данные новой функции' in body['text'] for p, _, body in jobs if p == '/messages'))
        self.assertFalse(self.quiz.games)

    def test_screen_does_not_depend_on_message_title(self):
        with patch.object(self.quiz, 'profile', return_value='Новый заголовок без эмодзи'):
            self.text('профиль')
        commands = {cmd for _, cmd in self.ui.keys[1].values()}
        self.assertTrue({'/subjects', '/grades', '/stats', '/history'} <= commands)
        reply = Reply(1, '👤 Твой профиль', 'bots')
        layout = rows(self.app, reply)
        commands = {cmd for row in layout for _, cmd in row}
        self.assertEqual(commands, {'/bot easy', '/bot hard', '/start'})

    def test_custom_layout_can_use_existing_actions(self):
        self.app.add('/custom', lambda uid, text: Reply(uid, 'Новый экран', rows=[[('Начать', '/quiz')]]), title='Новый экран')
        self.text('/custom')
        self.assertEqual({cmd for _, cmd in self.ui.keys[1].values()}, {'/quiz'})
        key = next(iter(self.ui.keys[1]))
        self.click(key)
        self.assertEqual(self.quiz.games[1]['pos'], 0)

    def test_registry_rejects_overrides_atomically(self):
        before = dict(self.app.aliases)
        cases = [('/stop', {}), ('bad', {}), ('/new', {'aliases': ('меню',)}),
                 ('/new', {'aliases': ('/skip',)}), ('/new', {'aliases': ('x', ' X ')}),
                 ('/new', {'choices': 'easy'}), ('/new', {'menu': 'missing'})]
        for cmd, args in cases:
            with self.subTest(cmd=cmd, args=args), self.assertRaises(ValueError):
                self.app.add(cmd, lambda uid, text: 'value', **args)
        self.assertNotIn('/new', self.app.actions)
        self.assertEqual(self.app.aliases, before)
        self.assertFalse(self.app.repeat('/stop'))
        self.assertFalse(self.app.repeat('/skip'))

    def test_parameter_choices_and_owner_are_checked(self):
        calls = []
        self.app.add('/card', lambda uid, text: calls.append((uid, text)) or 'Карточка',
                     choices=('one', 'two'), view='progress')
        self.assertTrue(self.app.repeat('/card one'))
        self.assertFalse(self.app.repeat('/card unknown'))
        self.click('nav:1:/card one', uid=2)
        self.click('nav:1:/card unknown')
        self.assertFalse(calls)
        self.click('nav:1:/card two')
        self.assertEqual(calls, [(1, '/card two')])

    def test_multiple_replies_keep_their_own_layouts(self):
        self.app.add('/pair', lambda uid, text: [Reply(uid, 'Первый', 'profile'), Reply(2, 'Второй', 'bots')])
        jobs = self.text('/pair')
        self.assertEqual([args['user_id'] for path, args, _ in jobs if path == '/messages'], [1, 2])
        self.assertIn('/subjects', {cmd for _, cmd in self.ui.keys[1].values()})
        self.assertEqual({cmd for _, cmd in self.ui.keys[2].values()}, {'/bot easy', '/bot hard', '/start'})

    def test_replies_are_plain_serializable_values(self):
        fixed = Reply(1, 'Постоянный ответ')
        self.app.add('/fixed', lambda uid, text: fixed, view='progress')
        reply = self.app.run(1, '/fixed')[0]
        self.assertEqual(fixed.view, 'auto')
        self.assertEqual(reply.view, 'progress')
        payload = json.loads(json.dumps(asdict(reply), ensure_ascii=False))
        self.assertEqual(set(payload), {'uid', 'text', 'view', 'rows'})
        self.assertNotIn('attachments', payload)
        self.assertNotIn('callback_id', payload)

    def test_invalid_args_never_cancel_or_start_a_game(self):
        self.app.text(1, '/quiz')
        game = self.quiz.games[1]
        for cmd in ['/stop extra', '/again extra', '/mistakes extra', '/levels extra', '/enter extra']:
            self.app.text(1, cmd)
            self.assertIs(self.quiz.games[1], game)
            self.assertEqual(game['pos'], 0)
            self.assertNotIn(1, self.app.join)
        self.app.run(1, '/stop')
        for cmd in ['/again extra', '/levels extra', '/enter extra']:
            self.app.text(1, cmd)
            self.assertFalse(self.quiz.games)
            self.assertNotIn(1, self.app.join)

    def test_console_works_without_max_or_httpx(self):
        script = '''import builtins, sys
from unittest.mock import patch
original = builtins.__import__
blocked = {'max_adapter', 'buttons', 'httpx'}
def limited(name, *args, **kwargs):
    if name.split('.')[0] in blocked:
        raise AssertionError('Transport imported: '+name)
    return original(name, *args, **kwargs)
builtins.__import__ = limited
from main import console
with patch('builtins.input', side_effect=['профиль', '/subject russian', '/grade 6', '/quiz', '/skip', '/stop', '/exit']):
    console()
assert not blocked.intersection(sys.modules)
'''
        env = dict(os.environ, DATABASE_PATH=str(Path(self.tmp.name) / 'console.db'), PYTHONDONTWRITEBYTECODE='1')
        result = subprocess.run([sys.executable, '-c', script], cwd=ROOT, env=env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Русский язык', result.stdout)
        self.assertIn('Вопрос пропущен', result.stdout)


if __name__ == '__main__':
    unittest.main()
