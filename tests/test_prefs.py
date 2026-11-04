import sqlite3
import tempfile
import unittest
from pathlib import Path

from buttons import Buttons, prepare
from prefs import SUBJECTS
from quiz import Quiz
from storage import connect


class Settings(unittest.TestCase):
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

    def keys(self, uid):
        return {action: key for key, (_, action) in self.ui.keys[uid].items()}

    def click(self, uid, action, key=None):
        self.seq += 1
        return prepare(self.ui, {'update_type': 'message_callback', 'callback': {
            'user': {'user_id': uid}, 'callback_id': str(self.seq),
            'payload': key or self.keys(uid)[action]}})

    def test_profile_selectors_and_persistence(self):
        self.text(1, 'профиль')
        self.assertTrue({'/subjects', '/grades'} <= self.keys(1).keys())
        self.click(1, '/subjects')
        self.assertTrue({f'/subject {sub}' for sub in SUBJECTS} <= self.keys(1).keys())
        self.click(1, '/subject informatics')
        self.assertEqual(self.quiz.prefs.get(1), ('informatics', 7))
        self.click(1, '/grades')
        self.assertEqual({cmd for cmd in self.keys(1) if cmd.startswith('/grade ')},
                         {f'/grade {grade}' for grade in range(7, 12)})
        self.click(1, '/grade 9')
        self.assertEqual(self.quiz.prefs.get(1), ('informatics', 9))
        self.quiz = Quiz(self.db)
        self.assertEqual(self.quiz.prefs.get(1), ('informatics', 9))
        self.assertEqual(self.quiz.prefs.get(2), ('math', 5))

    def test_all_courses_and_available_levels(self):
        self.assertEqual(len(self.quiz.prefs.catalog), 19)
        for pick, bank in self.quiz.prefs.catalog.items():
            with self.subTest(pick=pick):
                self.quiz.prefs.save(1, *pick)
                self.text(1, 'тренировка')
                commands = {cmd for cmd in self.keys(1) if cmd.startswith('/quiz')}
                expected = {'/quiz'} | {'/quiz ' + level for level in self.quiz.levels(1)}
                self.assertEqual(commands, expected)
                for cmd in sorted(commands):
                    self.quiz.handle(1, cmd)
                    game = self.quiz.games[1]
                    self.assertEqual(len(game['items']), 5)
                    self.assertTrue({q['id'] for q in game['items']} <= {q['id'] for q in bank})
                    if cmd != '/quiz':
                        self.assertTrue(all(q['difficulty'] == cmd.split()[1] for q in game['items']))
                    self.quiz.handle(1, '/stop')

    def test_mistakes_are_filtered_without_deleting_others(self):
        math = next(q for q in self.quiz.bank if q['subject'] == 'math' and q['grade_min'] == 5)
        russian = next(q for q in self.quiz.bank if q['subject'] == 'russian' and q['grade_min'] == 6)
        self.quiz.log.mark(1, math['id'], False)
        self.quiz.log.mark(1, russian['id'], False)
        self.quiz.prefs.save(1, 'russian', 6)
        self.quiz.handle(1, '/mistakes')
        self.assertEqual([q['id'] for q in self.quiz.games[1]['items']], [russian['id']])
        self.quiz.handle(1, '/stop')
        self.assertIn(math['id'], self.quiz.log.ids(1))
        self.quiz.prefs.save(1, 'math', 5)
        self.quiz.handle(1, '/mistakes')
        self.assertEqual([q['id'] for q in self.quiz.games[1]['items']], [math['id']])

    def test_setting_changes_are_blocked_during_training(self):
        self.text(1, '/quiz')
        game = self.quiz.games[1]
        self.text(1, 'профиль')
        self.click(1, '/subjects')
        self.click(1, '/subject russian')
        self.quiz.handle(1, '/grade 9')
        self.assertEqual(self.quiz.prefs.get(1), ('math', 5))
        self.assertIs(self.quiz.games[1], game)
        self.assertEqual(game['pos'], 0)
        self.click(1, '/resume')
        self.click(1, '/skip')
        self.assertEqual(game['pos'], 1)

    def test_setting_changes_are_blocked_while_waiting(self):
        self.quiz.handle(1, '/duel')
        code = self.quiz.duel.users[1]
        self.quiz.handle(1, '/subject russian')
        self.quiz.handle(1, '/grade 7')
        self.assertEqual(self.quiz.prefs.get(1), ('math', 5))
        self.assertEqual(self.quiz.duel.rooms[code]['players'], [1])

    def test_random_matching_requires_same_course(self):
        self.quiz.prefs.save(2, 'russian', 6)
        self.quiz.handle(1, '/random')
        self.quiz.handle(2, '/random')
        self.assertNotEqual(self.quiz.duel.users[1], self.quiz.duel.users[2])
        self.quiz.prefs.save(3, 'russian', 6)
        self.quiz.handle(3, '/random')
        code = self.quiz.duel.users[2]
        self.assertEqual(code, self.quiz.duel.users[3])
        room = self.quiz.duel.rooms[code]
        self.assertTrue(all(q['subject'] == 'russian' and q['grade_min'] == 6 for q in room['items']))

    def test_friend_invite_requires_same_course(self):
        self.quiz.prefs.save(1, 'informatics', 8)
        self.quiz.handle(1, '/duel')
        code = self.quiz.duel.users[1]
        self.quiz.handle(2, '/join ' + code)
        self.assertNotIn(2, self.quiz.duel.users)
        self.assertEqual(self.quiz.duel.rooms[code]['players'], [1])
        self.quiz.prefs.save(2, 'informatics', 8)
        self.quiz.handle(2, '/join ' + code)
        self.assertEqual(self.quiz.duel.users[2], code)
        self.quiz.handle(2, '/grade 10')
        self.assertEqual(self.quiz.prefs.get(2), ('informatics', 8))

    def test_bot_match_uses_selected_questions(self):
        self.quiz.prefs.save(1, 'informatics', 9)
        self.quiz.handle(1, '/bot easy')
        code = self.quiz.duel.users[1]
        room = self.quiz.duel.rooms[code]
        self.assertTrue(all(q['subject'] == 'informatics' and q['grade_min'] == 9 for q in room['items']))
        for q in room['items']:
            self.quiz.handle(1, str(q['answer'] + 1))
        self.assertNotIn(1, self.quiz.duel.users)
        with connect(self.db) as con:
            self.assertEqual(con.execute('SELECT p1,p2,s1 FROM matches').fetchall(), [(1, -1, 5)])

    def test_invalid_or_foreign_settings_cannot_change_profile(self):
        self.text(1, 'предмет')
        key = self.keys(1)['/subject russian']
        self.click(2, '', key)
        self.assertEqual(self.quiz.prefs.get(2), ('math', 5))
        for cmd in ['/subject unknown', '/subject', '/grade -1', '/grade 12', '/grade 7.0', '/grade ²', '/grade ' + '9' * 4500, '/grade', '/grade 9 extra']:
            self.quiz.handle(1, cmd)
            self.assertEqual(self.quiz.prefs.get(1), ('math', 5))
        self.assertFalse(self.quiz.prefs.save(1, 'informatics', 5))

    def test_old_grade_selector_validates_new_subject(self):
        self.text(1, 'класс')
        key = self.keys(1)['/grade 5']
        self.quiz.handle(1, '/subject informatics')
        self.click(1, '', key)
        self.assertEqual(self.quiz.prefs.get(1), ('informatics', 7))

    def test_again_and_advice_use_available_course(self):
        with connect(self.db) as con:
            con.execute("INSERT INTO results(uid,score,total,level) VALUES (1,2,5,'hard')")
        math = next(q for q in self.quiz.bank if q['subject'] == 'math' and q['grade_min'] == 5)
        self.quiz.log.mark(1, math['id'], False)
        self.quiz.prefs.save(1, 'russian', 7)
        self.text(1, '/advice')
        self.assertIn('/quiz', self.keys(1))
        self.assertNotIn('/mistakes', self.keys(1))
        self.quiz.handle(1, '/again')
        self.assertEqual(self.quiz.games[1]['level'], 'all')
        self.assertTrue(all(q['subject'] == 'russian' and q['grade_min'] == 7 for q in self.quiz.games[1]['items']))

    def test_failed_save_preserves_settings_and_statistics(self):
        self.quiz.prefs.save(1, 'russian', 6)
        with connect(self.db) as con:
            con.execute("INSERT INTO results(uid,score,total,level) VALUES (1,3,5,'easy')")
            con.execute("CREATE TRIGGER fail BEFORE UPDATE ON prefs BEGIN SELECT RAISE(ABORT,'forced'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.quiz.handle(1, '/grade 7')
        self.assertEqual(self.quiz.prefs.get(1), ('russian', 6))
        with connect(self.db) as con:
            self.assertEqual(con.execute('SELECT score FROM results').fetchall(), [(3,)])


if __name__ == '__main__':
    unittest.main()
