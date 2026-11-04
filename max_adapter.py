import re
import secrets
from urllib.parse import parse_qs, quote, urlparse

from app import App, Reply
from screens import rows
from view import render


class Buttons:
    def __init__(self, quiz, user=''):
        self.app = quiz if isinstance(quiz, App) else quiz.app
        self.quiz = self.app.quiz
        self.user = user if isinstance(user, str) and re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', user) else ''
        self.keys = {}
        self.join = self.app.join

    def state(self, uid):
        return self.app.state(uid)

    def act(self, uid, cmd):
        return [(msg.uid, msg.text) for msg in self.app.run(uid, cmd)]

    def text(self, uid, text):
        return [(msg.uid, msg.text) for msg in self.on_text(uid, text)]

    def click(self, uid, key):
        return [(msg.uid, msg.text) for msg in self.on_click(uid, key)]

    def on_text(self, uid, text):
        if self.user and text.strip().startswith('https://'):
            try:
                url = urlparse(text.strip())
            except ValueError:
                url = urlparse('')
            payload = parse_qs(url.query).get('start', [''])[0]
            if url.netloc.lower() == 'max.ru' and url.path.strip('/').lower() == self.user.lower():
                match = re.fullmatch(r'duel_([a-f0-9]{6})', payload.lower())
                text = match[1] if match else ''
        return self.app.text(uid, text)

    def on_click(self, uid, key):
        prefix = f'nav:{uid}:'
        if key.startswith(prefix) and self.app.repeat(key[len(prefix):]):
            return self.app.run(uid, key[len(prefix):])
        item = self.keys.get(uid, {}).get(key)
        if item is None or item[0] != self.state(uid):
            return self.app.stale(uid)
        self.keys[uid] = {}
        return self.app.run(uid, item[1])

    def body(self, uid, text, reply=None):
        if reply is None:
            reply = self.app.last.get(uid)
            if reply is None or reply.text != text:
                reply = Reply(uid, text, 'waiting' if self.app.busy(uid) else 'home')
        state = self.state(uid)
        buttons = []
        code = self.app.invite(uid)
        if reply.view in ('question', 'waiting') and code:
            buttons.append([{'type': 'clipboard', 'text': 'Скопировать код', 'payload': code}])
            if self.user:
                link = f'https://max.ru/{self.user}?start=duel_{code}'
                text += '\n\nСсылка для друга:\n' + link
                buttons.append([{'type': 'clipboard', 'text': 'Скопировать ссылку', 'payload': link}])
                share = quote('Сыграем в Дуэль знаний?\n' + link, safe='')
                buttons.append([{'type': 'link', 'text': 'Пригласить друга', 'url': 'https://max.ru/:share?text=' + share}])
        self.keys[uid] = {}
        for row in rows(self.app, reply):
            line = []
            for title, action in row:
                key = f'nav:{uid}:{action}' if self.app.repeat(action) else secrets.token_hex(12)
                self.keys[uid][key] = (state, action)
                line.append({'type': 'callback', 'text': title, 'payload': key})
            buttons.append(line)
        body = {'text': text, 'attachments': [{'type': 'inline_keyboard', 'payload': {'buttons': buttons}}]}
        html = render(text)
        if len(html) <= 4000:
            body.update(text=html, format='html')
        return body


def prepare(ui, event):
    if not isinstance(event, dict):
        return []
    kind = event.get('update_type')
    cb = None
    if kind == 'bot_started':
        user, text = event.get('user', {}), '/start'
        payload = event.get('payload')
        if isinstance(payload, str) and re.fullmatch(r'duel_[a-f0-9]{6}', payload.lower()):
            text = '/join ' + payload[5:].lower()
    elif kind == 'message_created':
        msg = event.get('message')
        if not isinstance(msg, dict):
            return []
        target = msg.get('recipient')
        body = msg.get('body')
        if not isinstance(target, dict) or not isinstance(body, dict) or target.get('chat_type') != 'dialog':
            return []
        user = msg.get('sender', {})
        text = body.get('text') or ''
    elif kind == 'message_callback':
        cb = event.get('callback')
        if not isinstance(cb, dict) or not isinstance(cb.get('callback_id'), str) or not cb['callback_id']:
            return []
        user = cb.get('user', {})
        text = cb.get('payload') or ''
    else:
        return []
    if not isinstance(user, dict) or not isinstance(text, str):
        return []
    uid = user.get('user_id')
    if type(uid) is not int or user.get('is_bot'):
        return []
    ui.app.name(uid, user.get('first_name') or user.get('name'))
    msgs = ui.on_click(uid, text) if cb is not None else ui.on_text(uid, text)
    tasks = []
    if cb is not None and cb.get('callback_id'):
        tasks.append(('/answers', {'callback_id': cb['callback_id']}, {'notification': 'Готово'}))
    tasks.extend(('/messages', {'user_id': msg.uid}, ui.body(msg.uid, msg.text, msg)) for msg in msgs)
    return tasks
