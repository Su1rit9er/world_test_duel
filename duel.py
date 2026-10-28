import random
import secrets
import sqlite3

from progress import Progress


class Duel:
    def __init__(self, bank, db):
        self.bank = bank
        self.db = db
        self.log = Progress(db)
        self.rooms = {}
        self.users = {}
        with sqlite3.connect(db) as con:
            con.execute('CREATE TABLE IF NOT EXISTS matches (code TEXT PRIMARY KEY, p1 INTEGER, p2 INTEGER, s1 INTEGER, s2 INTEGER, date TEXT DEFAULT CURRENT_TIMESTAMP)')

    def question(self, room):
        q = room['items'][room['pos']]
        opts = '\n'.join(f'{i}. {v}' for i, v in enumerate(q['options'], 1))
        return f"Дуэль: вопрос {room['pos'] + 1} из 5\n\n{q['text']}\n\n{opts}\n\nОтвет: 1–4. Пропуск: /skip. Выход: /stop"

    def stats(self, uid):
        with sqlite3.connect(self.db) as con:
            rows = con.execute('SELECT p1, s1, s2 FROM matches WHERE p1=? OR p2=?', (uid, uid)).fetchall()
        wins = sum((a > b if p == uid else b > a) for p, a, b in rows)
        ties = sum(a == b for _, a, b in rows)
        return f'Дуэлей: {len(rows)}\nПобед: {wins}\nНичьих: {ties}\nПоражений: {len(rows) - wins - ties}'

    def close(self, code):
        room = self.rooms.pop(code)
        for uid in room['players']:
            del self.users[uid]

    def handle(self, uid, text):
        parts = text.split()
        code = self.users.get(uid)
        if text == '/duel':
            if code:
                room = self.rooms[code]
                if len(room['players']) == 1:
                    return [(uid, f'Ждём друга. Команда для него: /join {code}')]
                if uid in room['answers']:
                    return [(uid, 'Ответ принят. Ждём соперника.')]
                return [(uid, self.question(room))]
            code = secrets.token_hex(3)
            while code in self.rooms:
                code = secrets.token_hex(3)
            self.rooms[code] = {'players': [uid], 'items': random.sample(self.bank, 5),
                                'pos': 0, 'scores': {uid: 0}, 'answers': {}}
            self.users[uid] = code
            return [(uid, f'Дуэль создана! Отправь другу команду:\n/join {code}\n\nОн должен отправить её этому боту. Отменить: /stop')]
        if parts and parts[0] == '/join':
            if code:
                return [(uid, 'У тебя уже есть дуэль. Вернуться: /duel. Выйти: /stop')]
            if len(parts) != 2:
                return [(uid, 'Отправь /join и код друга, например: /join a1b2c3')]
            code = parts[1]
            room = self.rooms.get(code)
            if room is None or len(room['players']) != 1:
                return [(uid, 'Код не найден или дуэль уже началась.')]
            room['players'].append(uid)
            room['scores'][uid] = 0
            self.users[uid] = code
            msg = 'Соперник найден! За верный ответ — 1 балл.\n\n' + self.question(room)
            return [(p, msg) for p in room['players']]
        if not code:
            return [(uid, 'Создать дуэль: /duel. Войти: /join код')]
        room = self.rooms[code]
        if text == '/stop':
            msgs = [(p, 'Дуэль отменена. Результат не учитывается. Новая дуэль: /duel') for p in room['players']]
            self.close(code)
            return msgs
        if len(room['players']) == 1:
            return [(uid, f'Ждём друга. Команда для него: /join {code}\nОтменить: /stop')]
        if text not in ('1', '2', '3', '4', '/skip'):
            return [(uid, 'Ответь цифрой 1–4, /skip или /stop. Текущий вопрос: /duel')]
        if uid in room['answers']:
            return [(uid, 'Ответ уже принят. Ждём соперника.')]
        answer = None if text == '/skip' else int(text) - 1
        q = room['items'][room['pos']]
        self.log.mark(uid, q['id'], answer == q['answer'])
        room['answers'][uid] = answer
        if len(room['answers']) < 2:
            return [(uid, 'Ответ принят. Ждём соперника.')]
        q = room['items'][room['pos']]
        for p, answer in room['answers'].items():
            room['scores'][p] += int(answer == q['answer'])
        room['pos'] += 1
        room['answers'] = {}
        msg = f"Правильный ответ: {q['options'][q['answer']]}\n{q['explanation']}"
        if room['pos'] < 5:
            return [(p, msg + f"\nТвои баллы: {room['scores'][p]}\n\n" + self.question(room)) for p in room['players']]
        a, b = room['players']
        sa, sb = room['scores'][a], room['scores'][b]
        with sqlite3.connect(self.db) as con:
            con.execute('INSERT INTO matches (code, p1, p2, s1, s2) VALUES (?, ?, ?, ?, ?)', (code, a, b, sa, sb))
        msgs = []
        for p, own, other in [(a, sa, sb), (b, sb, sa)]:
            result = 'Ничья!' if own == other else 'Ты победил!' if own > other else 'Победил соперник.'
            msgs.append((p, msg + f'\n\n{result}\nТвой результат: {own} из 5\nСоперник: {other} из 5\nНовая дуэль: /duel\nСтатистика: /stats'))
        self.close(code)
        return msgs
