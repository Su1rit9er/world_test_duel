import tempfile
import unittest
from pathlib import Path

from buttons import Buttons, prepare
from quiz import Quiz


class Navigation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / 'stats.db'
        self.quiz = Quiz(self.db)
        self.ui = Buttons(self.quiz)
        self.seq = 0

    def text(self, uid, text):
        self.seq += 1
        return prepare(self.ui, {'update_type': 'message_created', 'message': {
            'recipient': {'chat_type': 'dialog'}, 'sender': {'user_id': uid},
            'body': {'mid': str(self.seq), 'text': text}}})

    def click(self, uid, key):
        self.seq += 1
        return prepare(self.ui, {'update_type': 'message_callback', 'callback': {
            'user': {'user_id': uid}, 'callback_id': str(self.seq), 'payload': key}})

    def keys(self, uid):
        return {action: key for key, (_, action) in self.ui.keys[uid].items()}

    def shown(self, jobs):
        return '\n'.join(body['text'] for path, _, body in jobs if path == '/messages')

    def test_profile_buttons_remain_available(self):
        self.text(1, 'профиль')
        keys = self.keys(1)
        for action, title in [('/rating', 'Рейтинг'), ('/stats', 'Тренировки'),
                              ('/history', 'История игр'), ('/awards', 'Достижения'),
                              ('/rating', 'Рейтинг')]:
            body = self.shown(self.click(1, keys[action]))
            self.assertIn(title, body)
            self.assertNotIn('неактуальна', body)
        self.assertFalse(self.quiz.games)
        self.assertFalse(self.quiz.duel.users)

    def test_old_menu_can_start_training(self):
        self.text(1, 'меню')
        home = self.keys(1)
        self.click(1, home['/levels'])
        levels = self.keys(1)
        self.text(1, 'профиль')
        profile = self.keys(1)
        self.click(1, profile['/rating'])
        self.click(1, home['/levels'])
        self.click(1, levels['/quiz easy'])
        game = self.quiz.games[1]
        self.assertEqual(game['level'], 'easy')
        self.click(1, levels['/quiz easy'])
        self.assertIs(self.quiz.games[1], game)
        self.assertEqual(game['pos'], 0)
        self.click(1, home['/duel'])
        self.assertIs(self.quiz.games[1], game)
        self.assertFalse(self.quiz.duel.users)

    def test_navigation_survives_restart(self):
        self.text(1, 'профиль')
        keys = self.keys(1)
        self.quiz = Quiz(self.db)
        self.ui = Buttons(self.quiz)
        self.assertIn('Рейтинг', self.shown(self.click(1, keys['/rating'])))
        self.assertIn('История игр', self.shown(self.click(1, keys['/history'])))

    def test_navigation_during_game_preserves_progress(self):
        self.text(1, 'профиль')
        keys = self.keys(1)
        self.text(1, '/quiz')
        for _ in range(2):
            self.click(1, self.keys(1)['/skip'])
        game = self.quiz.games[1]
        self.assertEqual(game['pos'], 2)
        self.assertIn('Рейтинг', self.shown(self.click(1, keys['/rating'])))
        self.assertIn('История игр', self.shown(self.click(1, keys['/history'])))
        self.assertIs(self.quiz.games[1], game)
        self.assertEqual(game['pos'], 2)

    def test_navigation_is_bound_to_owner(self):
        self.text(1, 'профиль')
        key = self.keys(1)['/again']
        self.text(2, 'меню')
        self.assertIn('неактуальна', self.shown(self.click(2, key)))
        self.assertFalse(self.quiz.games)
        self.click(1, key)
        self.assertEqual(set(self.quiz.games), {1})

    def test_game_buttons_cannot_be_navigation(self):
        self.text(1, '/quiz')
        game = self.quiz.games[1]
        for action in ['1', '/skip', '/stop', '/join aaaaaa', '/unknown']:
            self.click(1, f'nav:1:{action}')
            self.assertIs(self.quiz.games[1], game)
            self.assertEqual(game['pos'], 0)

    def test_old_game_buttons_cannot_change_new_game(self):
        self.text(1, '/quiz')
        keys = self.keys(1)
        self.click(1, keys['1'])
        self.click(1, keys['1'])
        self.click(1, keys['/skip'])
        self.assertEqual(self.quiz.games[1]['pos'], 1)
        self.click(1, self.keys(1)['/stop'])
        self.text(1, '/quiz')
        game = self.quiz.games[1]
        for action in ['1', '/skip', '/stop']:
            self.click(1, keys[action])
        self.assertIs(self.quiz.games[1], game)
        self.assertEqual(game['pos'], 0)


if __name__ == '__main__':
    unittest.main()
