import random
import secrets

from storage import connect
from progress import Progress
from prefs import Prefs, label
from view import question


class Duel:
    def __init__(self, bank, db, prefs=None):
        self.bank = bank
        self.db = db
        self.prefs = prefs or Prefs(bank, db)
        self.log = Progress(db)
        self.rooms = {}
        self.users = {}
        with connect(db) as con:
            con.execute('CREATE TABLE IF NOT EXISTS matches (code TEXT PRIMARY KEY, p1 INTEGER, p2 INTEGER, s1 INTEGER, s2 INTEGER, date TEXT DEFAULT CURRENT_TIMESTAMP)')
            con.execute('CREATE INDEX IF NOT EXISTS matches_p1 ON matches(p1, date)')
            con.execute('CREATE INDEX IF NOT EXISTS matches_p2 ON matches(p2, date)')

    def question(self, room):
        q = room['items'][room['pos']]
        title = '🤖 Дуэль с ботом' if 'bot' in room else '⚔ Дуэль'
        return question(q, room['pos'], 5, title, room.get('label', ''))

    def stats(self, uid):
        with connect(self.db) as con:
            rows = con.execute('SELECT p1, s1, s2 FROM matches WHERE p1=? OR p2=?', (uid, uid)).fetchall()
        wins = sum((a > b if p == uid else b > a) for p, a, b in rows)
        ties = sum(a == b for _, a, b in rows)
        return f'⚔ Дуэли\n\nПобеды  ·  {wins}\nНичьи  ·  {ties}\nПоражения  ·  {len(rows) - wins - ties}\n\nВсего игр  ·  {len(rows)}'

    def close(self, code):
        room = self.rooms.pop(code)
        for uid in room['players']:
            self.users.pop(uid, None)

    def new_code(self):
        with connect(self.db) as con:
            while True:
                code = secrets.token_hex(3)
                if code not in self.rooms and not con.execute('SELECT 1 FROM matches WHERE code=?', (code,)).fetchone():
                    return code

    def bot(self, uid, level):
        rating = self.log.rating(uid)
        chance = max(0.2, min(0.95, 0.25 + 0.6 * rating / 1000 + (-0.15 if level == 'easy' else 0.15)))
        code = self.new_code()
        items = random.sample(self.prefs.pool(uid), 5)
        moves = [q['answer'] if random.random() < chance else random.choice([i for i in range(4) if i != q['answer']])
                 for q in items]
        room = {'players': [uid, -1], 'items': items, 'pos': 0, 'scores': {uid: 0, -1: 0},
                'answers': {}, 'bot': moves, 'pick': self.prefs.get(uid), 'label': label(self.prefs.get(uid))}
        self.rooms[code] = room
        self.users[uid] = code
        name = 'Полегче' if level == 'easy' else 'Посложнее'
        return [(uid, f'Дуэль с ботом · {name}\nТвой рейтинг силы: {rating}/1000.\n'
                 'Соперник играет по алгоритму, его точность зависит от твоих последних результатов.\n'
                 'За верный ответ — 1 балл. После твоего ответа сразу начнётся следующий вопрос.\n\n'
                 + self.question(room))]

    def handle(self, uid, text):
        parts = text.split()
        code = self.users.get(uid)
        if text == '/random':
            if code:
                return self.handle(uid, '/duel')
            pick = self.prefs.get(uid)
            for key, room in self.rooms.items():
                if room.get('mode') == 'random' and room.get('pick') == pick and len(room['players']) == 1 and uid not in room['players']:
                    return self.handle(uid, '/join ' + key)
            self.handle(uid, '/duel')
            room = self.rooms[self.users[uid]]
            room['mode'] = 'random'
            return [(uid, 'Ищем случайного соперника. Игра начнётся, когда другой участник нажмёт такую же кнопку.\n'
                     'Приглашения друзьям в этот поиск не попадают. Отменить ожидание можно кнопкой «Завершить».')]
        if parts and parts[0] == '/bot':
            if len(parts) > 2 or (len(parts) == 2 and parts[1] not in ('easy', 'hard')):
                return [(uid, 'Выбери уровень через кнопку «С ботом» в меню.')]
            if code:
                return [(uid, 'У тебя уже есть дуэль. Нажми «Продолжить» или «Завершить».')]
            return self.bot(uid, parts[1] if len(parts) == 2 else 'easy')
        if text == '/duel':
            if code:
                room = self.rooms[code]
                if len(room['players']) == 1:
                    if room.get('mode') == 'random':
                        return [(uid, 'Поиск соперника продолжается. Для отмены нажми «Завершить».')]
                    return [(uid, f'Ждём друга. Код приглашения: {code}')]
                if uid in room['answers']:
                    return [(uid, '⏳ Ответ принят\n\nЖдём соперника. Следующий вопрос появится, когда вы ответите оба.\nВыйти из дуэли можно кнопкой «Завершить».')]
                return [(uid, self.question(room))]
            code = self.new_code()
            self.rooms[code] = {'players': [uid], 'items': random.sample(self.prefs.pool(uid), 5),
                                'pos': 0, 'scores': {uid: 0}, 'answers': {},
                                'pick': self.prefs.get(uid), 'label': label(self.prefs.get(uid))}
            self.users[uid] = code
            return [(uid, f'⚔ Пригласи друга\n\nКод дуэли: {code}\n\n'
                     'Скопируй код кнопкой ниже и отправь другу.\n'
                     'Ему нужно открыть этого бота и вставить код.\n'
                     'Когда друг присоединится, игра начнётся у вас обоих.\n\n'
                     'Для отмены нажми «Завершить».')]
        if parts and parts[0] == '/join':
            if code:
                return [(uid, 'У тебя уже есть дуэль. Нажми «Продолжить» или «Завершить».')]
            if len(parts) != 2:
                return [(uid, 'Нажми «Войти по коду» и вставь код, который получил от друга.')]
            code = parts[1]
            room = self.rooms.get(code)
            if room is None or len(room['players']) != 1:
                return [(uid, 'Код не найден или дуэль уже началась.')]
            if self.prefs.get(uid) != room['pick']:
                return [(uid, 'У друга выбрано: ' + room['label'] + '\n\nВыбери такой же предмет и класс в профиле, затем введи код ещё раз.')]
            room['players'].append(uid)
            room['scores'][uid] = 0
            self.users[uid] = code
            msg = 'Соперник найден\n\nПять одинаковых вопросов. За верный ответ — один балл.\nНачинаем!\n\n' + self.question(room)
            return [(p, msg) for p in room['players']]
        if not code:
            return [(uid, 'Нажми «Дуэль», чтобы пригласить друга, или «Войти по коду», чтобы принять приглашение.')]
        room = self.rooms[code]
        if text == '/stop':
            msgs = [(p, 'Дуэль остановлена\n\nРезультат этой игры не учитывается.\nМожно пригласить друга снова или выбрать другой режим.')
                    for p in room['players'] if p != -1]
            self.close(code)
            return msgs
        if len(room['players']) == 1:
            if room.get('mode') == 'random':
                return [(uid, 'Ждём случайного соперника. Для отмены нажми «Завершить».')]
            return [(uid, f'Ждём друга. Код приглашения: {code}\nДля отмены нажми «Завершить».')]
        if text not in ('1', '2', '3', '4', '/skip'):
            return [(uid, 'Выбери ответ кнопкой. Также можно пропустить вопрос или завершить дуэль.')]
        if uid in room['answers']:
            return [(uid, '⏳ Ответ уже принят\n\nИзменить его нельзя. Ждём соперника.')]
        answer = None if text == '/skip' else int(text) - 1
        q = room['items'][room['pos']]
        answers = dict(room['answers'])
        answers[uid] = answer
        if 'bot' in room:
            answers[-1] = room['bot'][room['pos']]
        scores = dict(room['scores'])
        pos = room['pos']
        if len(answers) == 2:
            for player, value in answers.items():
                scores[player] += int(value == q['answer'])
            pos += 1
        with connect(self.db) as con:
            self.log.mark(uid, q['id'], answer == q['answer'], con)
            if pos == 5:
                a, b = room['players']
                con.execute('INSERT INTO matches (code, p1, p2, s1, s2) VALUES (?, ?, ?, ?, ?)',
                            (code, a, b, scores[a], scores[b]))
        room.update(scores=scores, pos=pos, answers=answers if len(answers) < 2 else {})
        if len(answers) < 2:
            return [(uid, '⏳ Ответ принят\n\nЖдём соперника. Следующий вопрос появится, когда вы ответите оба.\nВыйти из дуэли можно кнопкой «Завершить».')]
        msg = f"Разбор вопроса\nПравильный ответ: {q['options'][q['answer']]}\n\n{q['explanation']}"
        if pos < 5:
            return [(p, msg + f"\n\nТвой счёт  ·  {scores[p]}"
                     + (f" · Бот: {scores[-1]}" if 'bot' in room else '')
                     + '\n\n' + self.question(room)) for p in room['players'] if p != -1]
        a, b = room['players']
        sa, sb = scores[a], scores[b]
        msgs = []
        for p, own, other in [(a, sa, sb), (b, sb, sa)]:
            if p == -1:
                continue
            result = 'Ничья' if own == other else 'Победа!' if own > other else 'Дуэль завершена'
            msgs.append((p, msg + f'\n\n🏁 {result}\nТы  ·  {own}/5\nСоперник  ·  {other}/5\n\n' + ('Соперник набрал больше баллов. Реванш?' if own < other else 'Сыграем ещё?') + '\nДля новой игры нажми «Сыграть ещё».'))
        self.close(code)
        return msgs
