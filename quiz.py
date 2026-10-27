import json
import random
import sqlite3
from pathlib import Path


class Quiz:
    def __init__(self, db=None):
        root = Path(__file__).resolve().parent
        self.bank = json.loads((root / 'questions.json').read_text(encoding='utf-8'))
        self.db = str(db or root / 'stats.db')
        self.games = {}
        with sqlite3.connect(self.db) as con:
            con.execute('CREATE TABLE IF NOT EXISTS results (uid INTEGER, score INTEGER, total INTEGER, level TEXT, date TEXT DEFAULT CURRENT_TIMESTAMP)')

    def question(self, uid):
        game = self.games[uid]
        q = game['items'][game['pos']]
        opts = '\n'.join(f'{i}. {v}' for i, v in enumerate(q['options'], 1))
        return f"Вопрос {game['pos'] + 1} из 5\n\n{q['text']}\n\n{opts}\n\nОтправь цифру от 1 до 4. Пропустить: /skip"

    def stats(self, uid):
        with sqlite3.connect(self.db) as con:
            row = con.execute('SELECT COUNT(*), SUM(score), SUM(total), MAX(score) FROM results WHERE uid=?', (uid,)).fetchone()
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
                    '/stats — мои результаты\n/skip — пропустить вопрос\n/stop — закончить тест')
        if text == '/stats':
            return self.stats(uid)
        if text == '/stop':
            self.games.pop(uid, None)
            return 'Тест остановлен. Новый тест: /quiz'
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
        if game['pos'] == 4:
            with sqlite3.connect(self.db) as con:
                con.execute('INSERT INTO results (uid, score, total, level) VALUES (?, ?, 5, ?)', (uid, score, game['level']))
        game['score'] = score
        game['pos'] += 1
        msg = 'Верно!' if ok else f"Правильный ответ: {q['options'][q['answer']]}"
        msg += '\n' + q['explanation']
        if game['pos'] == 5:
            del self.games[uid]
            return msg + f'\n\nРезультат: {score} из 5.\nЕщё раз: /quiz\nСтатистика: /stats'
        return msg + '\n\n' + self.question(uid)
