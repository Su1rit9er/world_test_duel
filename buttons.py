import secrets


class Buttons:
    def __init__(self, quiz):
        self.quiz = quiz
        self.keys = {}
        self.join = set()

    def state(self, uid):
        game = self.quiz.games.get(uid)
        if game:
            tag = game.setdefault('tag', secrets.token_hex(8))
            return ('quiz', tag, game['pos'])
        code = self.quiz.duel.users.get(uid)
        if code:
            room = self.quiz.duel.rooms[code]
            return ('duel', code, room['pos'], len(room['players']), uid in room['answers'])
        return ('menu',)

    def text(self, uid, text):
        text = text.strip().lower()
        if uid in self.join and not text.startswith('/'):
            self.join.discard(uid)
            return self.quiz.handle(uid, '/join ' + text)
        self.join.discard(uid)
        return self.quiz.handle(uid, text)

    def click(self, uid, key):
        item = self.keys.get(uid, {}).get(key)
        if item is None or item[0] != self.state(uid):
            return [(uid, 'Эта кнопка устарела. Открой текущий вопрос или меню кнопками ниже.')]
        self.keys[uid] = {}
        self.join.discard(uid)
        cmd = item[1]
        if cmd == '/levels':
            return [(uid, 'Выбери сложность тренировки:')]
        if cmd == '/enter':
            self.join.add(uid)
            return [(uid, 'Отправь код дуэли сообщением. Например: a1b2c3')]
        return self.quiz.handle(uid, cmd)

    def body(self, uid, text):
        rows = []
        state = self.state(uid)
        game = self.quiz.games.get(uid)
        code = self.quiz.duel.users.get(uid)
        question = None
        cmd = '/quiz'
        if game:
            question = self.quiz.question(uid)
        elif code:
            cmd = '/duel'
            room = self.quiz.duel.rooms[code]
            if len(room['players']) == 2 and uid not in room['answers']:
                question = self.quiz.duel.question(room)
        if game or code:
            if question and question in text:
                rows.append([(str(i), str(i)) for i in range(1, 5)])
                rows.append([('Пропустить', '/skip')])
            else:
                rows.append([('Продолжить', cmd)])
            rows.append([('Остановить', '/stop'), ('Меню', '/start')])
        elif text == 'Выбери сложность тренировки:':
            rows = [[('Простая', '/quiz easy')], [('Средняя', '/quiz medium')],
                    [('Сложная', '/quiz hard')], [('Смешанная', '/quiz')], [('Меню', '/start')]]
        elif uid in self.join:
            rows = [[('Отмена', '/start')]]
        else:
            rows = [[('Тренировка', '/levels')], [('Дуэль', '/duel'), ('Войти', '/enter')],
                    [('Мои ошибки', '/mistakes')], [('Статистика', '/stats'), ('История', '/history')]]
        if (game or code) and text.startswith('Привет!'):
            rows.extend([[('Статистика', '/stats'), ('История', '/history')]])
        self.keys[uid] = {}
        buttons = []
        for row in rows:
            line = []
            for label, action in row:
                key = secrets.token_hex(12)
                self.keys[uid][key] = (state, action)
                line.append({'type': 'callback', 'text': label, 'payload': key})
            buttons.append(line)
        return {'text': text, 'attachments': [{'type': 'inline_keyboard', 'payload': {'buttons': buttons}}]}


def prepare(ui, event):
    kind = event.get('update_type')
    cb = None
    if kind == 'bot_started':
        user, text = event.get('user', {}), '/start'
    elif kind == 'message_created':
        msg = event.get('message', {})
        if msg.get('recipient', {}).get('chat_type') != 'dialog':
            return []
        user = msg.get('sender', {})
        text = msg.get('body', {}).get('text') or ''
    elif kind == 'message_callback':
        cb = event.get('callback', {})
        user = cb.get('user', {})
        text = cb.get('payload') or ''
    else:
        return []
    uid = user.get('user_id')
    if type(uid) is not int or user.get('is_bot'):
        return []
    msgs = ui.click(uid, text) if cb is not None else ui.text(uid, text)
    tasks = []
    if cb is not None and cb.get('callback_id'):
        tasks.append(('/answers', {'callback_id': cb['callback_id']}, {'notification': 'Готово'}))
    tasks.extend(('/messages', {'user_id': p}, ui.body(p, msg)) for p, msg in msgs)
    return tasks
