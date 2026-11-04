import json
import random
from pathlib import Path

from duel import Duel
from progress import Progress
from prefs import Prefs, SUBJECTS, label
from view import question
from storage import db_path, connect


class Quiz:
    def __init__(self, db=None):
        root = Path(__file__).resolve().parent
        self.bank = json.loads((root / 'questions.json').read_text(encoding='utf-8'))
        self.db = str(db_path(db))
        self.games = {}
        self._app = None
        with connect(self.db) as con:
            con.execute('PRAGMA journal_mode=WAL')
            con.execute('CREATE TABLE IF NOT EXISTS results (uid INTEGER, score INTEGER, total INTEGER, level TEXT, date TEXT DEFAULT CURRENT_TIMESTAMP)')
            con.execute('CREATE INDEX IF NOT EXISTS results_user ON results(uid, date)')

        self.log = Progress(self.db)
        self.prefs = Prefs(self.bank, self.db)
        self.duel = Duel(self.bank, self.db, self.prefs)

    @property
    def app(self):
        if self._app is None:
            from app import App
            self._app = App(self)
        return self._app

    def handle(self, uid, text):
        return [(reply.uid, reply.text) for reply in self.app.text(uid, text)]

    def reply(self, uid, text):
        return next((msg for player, msg in self.handle(uid, text) if player == uid), '')

    def pool(self, uid, level='all'):
        return self.prefs.pool(uid, level)

    def levels(self, uid):
        bank = self.pool(uid)
        return [level for level in ('easy', 'medium', 'hard') if sum(q['difficulty'] == level for q in bank) >= 5]

    def profile(self, uid, note=''):
        subject, grade = self.prefs.get(uid)
        text = '👤 Твой профиль\n\n'
        if note:
            text += note + '\n\n'
        return (text + f'Предмет: {SUBJECTS[subject]}\nКласс: {grade}\n\n'
                'Здесь можно изменить настройки и посмотреть результаты.\n'
                'Статистика и достижения учитывают все игры.')

    def settings(self, uid, text):
        return self.app.settings(uid, text).text

    def question(self, uid):
        game = self.games[uid]
        q = game['items'][game['pos']]
        title = '📖 Работа над ошибками' if game['level'] == 'review' else '✏ Тренировка'
        return question(q, game['pos'], len(game['items']), title, game.get('label', ''))

    def stats(self, uid):
        with connect(self.db) as con:
            row = con.execute("SELECT COUNT(*), SUM(score), SUM(total), MAX(score) FROM results WHERE uid=? AND level != 'review'", (uid,)).fetchone()
        count, score, total, best = row
        if not count:
            return '📊 Тренировки\n\nПока нет результатов.\nПройди первую тренировку — здесь появится твой прогресс.'
        return f'📊 Тренировки\n\nТочность  ·  {score / total:.0%}\nЛучший результат  ·  {best}/5\n\nПройдено тестов  ·  {count}\nВерных ответов  ·  {score}/{total}'

    def again(self, uid):
        with connect(self.db) as con:
            row = con.execute("SELECT level FROM results WHERE uid=? AND level!='review' ORDER BY rowid DESC LIMIT 1",
                              (uid,)).fetchone()
        level = row[0] if row else 'all'
        if level not in self.levels(uid):
            level = 'all'
        return self.start(uid, level)

    def stop(self, uid):
        self.games.pop(uid, None)
        return 'Тренировка остановлена\n\nМожно начать заново или выбрать другой режим в меню.'

    def review(self, uid):
        if uid in self.games:
            return 'У тебя уже идёт тренировка. Нажми «Продолжить» или «Завершить».'
        ids = self.log.ids(uid)
        pool = [q for q in self.pool(uid) if q['id'] in ids]
        if not pool:
            return '📖 Мои ошибки\n\nПо выбранному предмету и классу пока нет ошибок.\nНовые ошибки из тренировок и дуэлей появятся здесь автоматически.'
        items = random.sample(pool, min(5, len(pool)))
        self.games[uid] = {'items': items, 'pos': 0, 'score': 0, 'level': 'review', 'label': label(self.prefs.get(uid))}
        return 'Повторим то, что не получилось.\nВерный ответ уберёт вопрос из списка ошибок.\n\n' + self.question(uid)

    def start(self, uid, level='all'):
        if uid in self.games:
            return 'Продолжаем текущий тест. Для новой тренировки нажми «Завершить».\n\n' + self.question(uid)
        pool = self.pool(uid, level)
        if len(pool) < 5:
            return 'Для этой сложности пока мало вопросов. Нажми «Тренировка» и выбери доступную сложность или «Смешанный».'
        self.games[uid] = {'items': random.sample(pool, 5), 'pos': 0, 'score': 0, 'level': level, 'label': label(self.prefs.get(uid))}
        return self.question(uid)

    def answer(self, uid, text):
        if uid not in self.games:
            return 'Выбери режим кнопками ниже. Кнопка «Тренировка» откроет выбор сложности.'
        if text not in ('1', '2', '3', '4', '/skip'):
            return 'Нажми кнопку с ответом, «Пропустить» или «Завершить».'
        game = self.games[uid]
        q = game['items'][game['pos']]
        ok = text != '/skip' and int(text) - 1 == q['answer']
        score = game['score'] + int(ok)
        total = len(game['items'])
        with connect(self.db) as con:
            self.log.mark(uid, q['id'], ok, con)
            if game['pos'] == total - 1:
                con.execute('INSERT INTO results (uid, score, total, level) VALUES (?, ?, ?, ?)', (uid, score, total, game['level']))
        game['score'] = score
        game['pos'] += 1
        msg = '✅ Верно' if ok else '↪ Вопрос пропущен' if text == '/skip' else 'Пока не получилось'
        msg += f"\nПравильный ответ: {q['options'][q['answer']]}" if not ok else ''
        msg += '\n\n' + q['explanation']
        if game['pos'] == total:
            del self.games[uid]
            note = 'Все ответы верные. Отличная работа!' if score == total else 'Разбери ошибки и попробуй ещё раз.'
            return msg + f'\n\n🏁 Тренировка завершена\n\nТвой результат: {score} из {total}\n\n{note}\n\nВыбери, что сделать дальше:'
        return msg + '\n\n' + self.question(uid)
