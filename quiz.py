import json
import random
import sqlite3
from pathlib import Path

from duel import Duel
from progress import Progress


class Quiz:
    def __init__(self, db=None):
        root = Path(__file__).resolve().parent
        self.bank = json.loads((root / 'questions.json').read_text(encoding='utf-8'))
        self.db = str(db or root / 'stats.db')
        self.games = {}
        with sqlite3.connect(self.db) as con:
            con.execute('CREATE TABLE IF NOT EXISTS results (uid INTEGER, score INTEGER, total INTEGER, level TEXT, date TEXT DEFAULT CURRENT_TIMESTAMP)')

        self.log = Progress(self.db)
        self.duel = Duel(self.bank, self.db)

    def handle(self, uid, text):
        text = text.strip().lower()
        if text == '/history':
            return [(uid, self.log.history(uid))]
        if text == '/stats':
            return [(uid, self.stats(uid) + '\n\n' + self.duel.stats(uid))]
        if text in ('/start', '/help'):
            return [(uid, self.reply(uid, text))]
        parts = text.split()
        cmd = parts[0] if parts else ''
        if cmd in ('/duel', '/join') or uid in self.duel.users:
            if uid in self.games:
                return [(uid, 'Сначала заверши тренировку или отправь /stop.')]
            return self.duel.handle(uid, text)
        return [(uid, self.reply(uid, text))]

    def question(self, uid):
        game = self.games[uid]
        q = game['items'][game['pos']]
        opts = '\n'.join(f'{i}. {v}' for i, v in enumerate(q['options'], 1))
        return f"Вопрос {game['pos'] + 1} из {len(game['items'])}\n\n{q['text']}\n\n{opts}\n\nОтправь цифру от 1 до 4. Пропустить: /skip"

    def stats(self, uid):
        with sqlite3.connect(self.db) as con:
            row = con.execute("SELECT COUNT(*), SUM(score), SUM(total), MAX(score) FROM results WHERE uid=? AND level != 'review'", (uid,)).fetchone()
        count, score, total, best = row
        if not count:
            return 'Пока нет завершённых тестов. Начать: /quiz'
        return f'Тестов: {count}\nВерных ответов: {score} из {total}\nТочность: {score / total:.0%}\nЛучший результат: {best} из 5'

    def reply(self, uid, text):
        text = text.strip().lower()
        if text in ('/start', '/help'):
            return ('Привет! Это Дуэль знаний.\nМатематика, 5 класс.\n'
                    '/quiz — начать или продолжить тест\n'
                    '/quiz easy — простой тест\n/quiz medium — средний\n/quiz hard — сложный\n'
                    '/duel — дуэль с другом\n/join код — войти в дуэль\n'
                    '/mistakes — работа над ошибками\n/history — последние результаты\n'
                    '/stats — мои результаты\n/skip — пропустить вопрос\n/stop — закончить тест')
        if text == '/stats':
            return self.stats(uid)
        if text == '/stop':
            self.games.pop(uid, None)
            return 'Тест остановлен. Новый тест: /quiz'
        if text == '/mistakes':
            if uid in self.games:
                return 'Сначала заверши текущий тест или отправь /stop.'
            ids = self.log.ids(uid)
            pool = [q for q in self.bank if q['id'] in ids]
            if not pool:
                return 'Нерешённых ошибок пока нет. Начать тренировку: /quiz'
            items = random.sample(pool, min(5, len(pool)))
            self.games[uid] = {'items': items, 'pos': 0, 'score': 0, 'level': 'review'}
            return 'Работа над ошибками. Верный ответ убирает вопрос из списка.\n\n' + self.question(uid)
        parts = text.split()
        if parts and parts[0] == '/quiz':
            if len(parts) > 2 or (len(parts) == 2 and parts[1] not in ('easy', 'medium', 'hard')):
                return 'Используй /quiz или /quiz easy, /quiz medium, /quiz hard.'
            if uid in self.games:
                return 'Продолжаем текущий тест. Для нового сначала отправь /stop.\n\n' + self.question(uid)
            level = parts[1] if len(parts) == 2 else 'all'
            pool = [q for q in self.bank if level == 'all' or q['difficulty'] == level]
            if len(pool) < 5:
                return 'Для этой сложности пока недостаточно вопросов.'
            self.games[uid] = {'items': random.sample(pool, 5), 'pos': 0, 'score': 0, 'level': level}
            return self.question(uid)
        if uid not in self.games:
            return 'Чтобы начать тест, отправь /quiz.'
        if text not in ('1', '2', '3', '4', '/skip'):
            return 'Отправь цифру от 1 до 4, /skip или /stop.'
        game = self.games[uid]
        q = game['items'][game['pos']]
        ok = text != '/skip' and int(text) - 1 == q['answer']
        score = game['score'] + int(ok)
        self.log.mark(uid, q['id'], ok)
        total = len(game['items'])
        if game['pos'] == total - 1:
            with sqlite3.connect(self.db) as con:
                con.execute('INSERT INTO results (uid, score, total, level) VALUES (?, ?, ?, ?)', (uid, score, total, game['level']))
        game['score'] = score
        game['pos'] += 1
        msg = 'Верно!' if ok else f"Правильный ответ: {q['options'][q['answer']]}"
        msg += '\n' + q['explanation']
        if game['pos'] == total:
            del self.games[uid]
            return msg + f'\n\nРезультат: {score} из {total}.\nЕщё раз: /quiz\nРабота над ошибками: /mistakes\nСтатистика: /stats'
        return msg + '\n\n' + self.question(uid)
