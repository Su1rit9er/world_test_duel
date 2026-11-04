import re
import secrets
from dataclasses import dataclass, replace
from collections.abc import Callable

from prefs import SUBJECTS, label


@dataclass(frozen=True)
class Reply:
    uid: int
    text: str
    view: str = 'auto'
    rows: list | None = None


@dataclass(frozen=True)
class Action:
    run: Callable
    view: str
    repeat: bool
    choices: tuple
    title: str
    menu: str


class App:
    def __init__(self, quiz):
        self.quiz = quiz
        self.actions = {}
        self.aliases = {}
        self.join = set()
        self.last = {}
        self.add('/start', self.welcome, view='home', aliases=('меню', 'назад', 'начать', 'привет', 'отмена'))
        self.add('/help', self.help, view='home', aliases=('помощь', 'как играть'))
        self.add('/profile', lambda uid, text: quiz.profile(uid), view='profile', aliases=('профиль',))
        self.add('/subjects', self.settings, aliases=('предмет',))
        self.add('/grades', self.settings, aliases=('класс',))
        self.add('/subject', self.settings, choices=tuple(SUBJECTS))
        self.add('/grade', self.settings, choices=tuple(str(n) for n in range(5, 12)))
        self.add('/stats', lambda uid, text: quiz.stats(uid) + '\n\n' + quiz.duel.stats(uid), view='progress', aliases=('статистика',))
        self.add('/history', lambda uid, text: quiz.log.history(uid), view='progress', aliases=('история',))
        self.add('/rating', lambda uid, text: quiz.log.board(uid), view='progress', aliases=('рейтинг',))
        self.add('/awards', lambda uid, text: quiz.log.awards(uid), view='progress')
        self.add('/advice', lambda uid, text: quiz.log.advice(uid, quiz.pool(uid)), view='advice')
        self.add('/levels', self.open, aliases=('тренировка',))
        self.add('/bots', self.open, aliases=('с ботом',))
        self.add('/enter', self.open, aliases=('войти',))
        self.add('/resume', self.resume, aliases=('продолжить',))
        self.add('/quiz', self.practice, choices=('', 'easy', 'medium', 'hard'))
        self.add('/again', self.again)
        self.add('/mistakes', self.review, aliases=('ошибки',))
        self.add('/stop', self.stop, view='home', repeat=False, aliases=('стоп', 'завершить'))
        self.add('/skip', self.move, repeat=False)
        for cmd, choices in [('/duel', ('',)), ('/random', ('',)), ('/bot', ('', 'easy', 'hard')), ('/join', ())]:
            self.add(cmd, self.duel, choices=choices, repeat=cmd != '/join', aliases={'/duel': ('дуэль',)}.get(cmd, ()))

    def add(self, cmd, run, *, view='auto', aliases=(), repeat=True, choices=('',), title='', menu='profile'):
        if not isinstance(cmd, str) or not re.fullmatch(r'/[a-z][a-z0-9_]*', cmd) or cmd in self.actions:
            raise ValueError('Действие уже существует или имеет неверное имя')
        if isinstance(aliases, str) or isinstance(choices, str):
            raise ValueError('Псевдонимы и параметры задаются списком')
        aliases = tuple(aliases)
        choices = tuple(choices)
        if any(not isinstance(value, str) for value in choices):
            raise ValueError('Параметры должны быть строками')
        if any(not isinstance(name, str) for name in aliases):
            raise ValueError('Неверный псевдоним')
        names = tuple(name.strip().lower() for name in aliases)
        if not callable(run) or len(set(names)) != len(names) or any(not name or name.startswith('/') or name in self.aliases for name in names):
            raise ValueError('Неверный обработчик или повтор псевдонима')
        if menu not in ('home', 'profile'):
            raise ValueError('Неизвестное меню')
        self.actions[cmd] = Action(run, view, repeat, tuple(choices), title, menu)
        self.aliases.update((name, cmd) for name in names)

    def repeat(self, text):
        cmd, _, arg = text.partition(' ')
        action = self.actions.get(cmd)
        return bool(action and action.repeat and arg in action.choices)

    def extras(self, menu):
        return [[(action.title, cmd)] for cmd, action in self.actions.items() if action.title and action.menu == menu]

    def state(self, uid):
        game = self.quiz.games.get(uid)
        if game:
            return ('quiz', game.setdefault('tag', secrets.token_hex(8)), game['pos'])
        code = self.quiz.duel.users.get(uid)
        if code:
            room = self.quiz.duel.rooms[code]
            return ('duel', code, room['pos'], len(room['players']), uid in room['answers'])
        return ('menu',)

    def busy(self, uid):
        return uid in self.quiz.games or uid in self.quiz.duel.users

    def current(self, uid):
        if uid in self.quiz.games:
            return self.quiz.question(uid)
        code = self.quiz.duel.users.get(uid)
        if code:
            room = self.quiz.duel.rooms[code]
            if len(room['players']) == 2 and uid not in room['answers']:
                return self.quiz.duel.question(room)
        return ''

    def peers(self, uid):
        peers = {uid} if uid is not None else set()
        code = self.quiz.duel.users.get(uid)
        if code:
            peers.update(p for p in self.quiz.duel.rooms[code]['players'] if p != -1)
        return peers

    def invite(self, uid):
        code = self.quiz.duel.users.get(uid)
        room = self.quiz.duel.rooms.get(code)
        return code if room and len(room['players']) == 1 and room.get('mode') != 'random' else None

    def name(self, uid, name):
        self.quiz.log.name(uid, name)

    def game_view(self, uid):
        return 'question' if self.current(uid) else 'waiting' if self.busy(uid) else 'enter' if uid in self.join else 'home'

    def remember(self, replies):
        for reply in replies:
            self.last[reply.uid] = reply
        return replies

    def run(self, uid, text):
        self.join.discard(uid)
        text = text.strip().lower()
        cmd = text.split()[0] if text.split() else ''
        before = self.state(uid)[0]
        action = self.actions.get(cmd)
        result = action.run(uid, text) if action else self.move(uid, text)
        items = [result] if isinstance(result, (str, Reply)) else result
        replies = []
        for item in items:
            reply = item if isinstance(item, Reply) else Reply(uid, item) if isinstance(item, str) else Reply(*item)
            view = reply.view if reply.view != 'auto' else action.view if action else 'auto'
            if view == 'auto':
                view = self.game_view(reply.uid)
                if not self.busy(reply.uid) and text in ('1', '2', '3', '4', '/skip'):
                    view = {'quiz': 'quiz_done', 'duel': 'duel_done'}.get(before, view)
            replies.append(replace(reply, view=view))
        return self.remember(replies)

    def text(self, uid, text):
        text = text.strip()
        low = text.lower()
        if low in self.aliases:
            return self.run(uid, self.aliases[low])
        if not self.busy(uid) and (uid in self.join or re.fullmatch(r'[a-f0-9]{6}', low)) and not low.startswith('/'):
            if not re.fullmatch(r'[a-f0-9]{6}', low):
                self.join.add(uid)
                return self.remember([Reply(uid, 'Код не распознан\n\nВставь шесть символов из приглашения или ссылку на дуэль. Можно отменить ввод кнопкой ниже.', 'enter')])
            replies = self.run(uid, '/join ' + low)
            if uid not in self.quiz.duel.users:
                self.join.add(uid)
                replies = [replace(reply, view='enter') for reply in replies]
            return self.remember(replies)
        if not self.busy(uid) and not low.startswith('/'):
            return self.run(uid, '/start')
        return self.run(uid, low)

    def stale(self, uid):
        text = 'Кнопка уже неактуальна\n\nВот текущий вопрос или доступные действия.'
        question = self.current(uid)
        if question:
            text += '\n\n' + question
        return self.remember([Reply(uid, text, self.game_view(uid))])

    def open(self, uid, text):
        if text not in ('/levels', '/bots', '/enter'):
            return self.move(uid, text)
        if self.busy(uid):
            return Reply(uid, 'Игра уже идёт\n\nНажми «Продолжить» или «Завершить», чтобы выбрать другой режим.', 'waiting')
        if text == '/levels':
            return Reply(uid, '✏ Выбери сложность\n\n' + label(self.quiz.prefs.get(uid)) + '\n\nВ тесте пять вопросов. Выбери доступную сложность или смешанный набор.', 'levels')
        if text == '/bots':
            return Reply(uid, '🤖 Дуэль с ботом\n\nПять вопросов и соперник, который подстраивается под твои результаты.\nВыбери, насколько уверенно он будет играть.', 'bots')
        self.join.add(uid)
        return Reply(uid, '🔑 Войти в дуэль\n\nВставь код или ссылку, которые получил от друга.\nКоманду вводить не нужно.', 'enter')

    def practice(self, uid, text):
        if uid in self.quiz.duel.users:
            return self.quiz.duel.handle(uid, text)
        parts = text.split()
        if len(parts) > 2 or (len(parts) == 2 and parts[1] not in ('easy', 'medium', 'hard')):
            return Reply(uid, 'Выбери сложность через кнопку «Тренировка» в меню.', 'waiting' if self.busy(uid) else 'home')
        return self.quiz.start(uid, parts[1] if len(parts) == 2 else 'all')

    def again(self, uid, text):
        if text != '/again':
            return self.move(uid, text)
        if uid in self.quiz.duel.users:
            return Reply(uid, 'Сначала заверши дуэль. Используй кнопки «Продолжить» или «Завершить».', 'waiting')
        return self.quiz.again(uid)

    def review(self, uid, text):
        if text != '/mistakes':
            return self.move(uid, text)
        if self.busy(uid):
            result = self.quiz.duel.handle(uid, text) if uid in self.quiz.duel.users else self.quiz.review(uid)
            return [Reply(player, msg, 'waiting') for player, msg in result] if isinstance(result, list) else Reply(uid, result, 'waiting')
        return self.quiz.review(uid)

    def stop(self, uid, text):
        if text != '/stop':
            return self.move(uid, text)
        return self.quiz.duel.handle(uid, text) if uid in self.quiz.duel.users else self.quiz.stop(uid)

    def resume(self, uid, text):
        if text != '/resume':
            return self.move(uid, text)
        if uid in self.quiz.games:
            return self.quiz.start(uid)
        if uid in self.quiz.duel.users:
            return self.quiz.duel.handle(uid, '/duel')
        return Reply(uid, self.welcome(uid, '/start'), 'home')

    def duel(self, uid, text):
        if uid in self.quiz.games:
            return Reply(uid, 'Сначала заверши тренировку или нажми «Продолжить», чтобы вернуться к вопросу.', 'waiting')
        before = self.quiz.duel.users.get(uid)
        result = self.quiz.duel.handle(uid, text)
        if before and text.split()[0] in ('/bot', '/join'):
            return [Reply(player, msg, 'waiting') for player, msg in result]
        return result

    def move(self, uid, text):
        if uid in self.quiz.duel.users:
            result = self.quiz.duel.handle(uid, text)
        else:
            result = [(uid, self.quiz.answer(uid, text))]
        if self.busy(uid) and text not in ('1', '2', '3', '4', '/skip'):
            return [Reply(player, msg, 'waiting') for player, msg in result]
        return result

    def settings(self, uid, text):
        if text == '/profile':
            return Reply(uid, self.quiz.profile(uid), 'profile')
        subject, grade = self.quiz.prefs.get(uid)
        if text == '/subjects':
            return Reply(uid, '📚 Выбери предмет\n\nСейчас: ' + label((subject, grade)) + '\n\nПосле выбора можно изменить класс.', 'subjects')
        if text == '/grades':
            return Reply(uid, f'🎒 Выбери класс\n\nПредмет: {SUBJECTS[subject]}\nТекущий класс: {grade}', 'grades')
        if self.busy(uid):
            return Reply(uid, 'Игра уже идёт\n\nСначала заверши её, чтобы изменить предмет или класс. Нажми «Продолжить» или «Завершить».', 'waiting')
        parts = text.split()
        if parts and parts[0] == '/subject':
            if len(parts) != 2 or parts[1] not in SUBJECTS or not self.quiz.prefs.grades(parts[1]):
                return self.settings(uid, '/subjects')
            subject = parts[1]
            grades = self.quiz.prefs.grades(subject)
            note = 'Настройки сохранены.'
            if grade not in grades:
                grade = min(grades, key=lambda value: abs(value - grade))
                note = f'Предмет сохранён. Выбран {grade} класс: прежний класс для этого предмета недоступен.'
        elif parts and parts[0] == '/grade':
            if len(parts) != 2 or parts[1] not in {str(num) for num in self.quiz.prefs.grades(subject)}:
                return self.settings(uid, '/grades')
            grade = int(parts[1])
            note = 'Настройки сохранены.'
        else:
            return Reply(uid, self.quiz.profile(uid), 'profile')
        self.quiz.prefs.save(uid, subject, grade)
        return Reply(uid, self.quiz.profile(uid, note), 'profile')

    def welcome(self, uid, text):
        return (f'🎓 Дуэль знаний\n{label(self.quiz.prefs.get(uid))}\n\n'
                'Пять вопросов — один шаг вперёд.\n'
                'Тренируйся в своём темпе или брось вызов сопернику.\n\n'
                'Выбери, с чего начнём ↓')

    def help(self, uid, text):
        return ('Как играть\n\n'
                'Предмет и класс можно выбрать в «Мой профиль».\n\n'
                'Тренировка — пять вопросов выбранной сложности.\n'
                'Дуэль — игра с другом, случайным участником или ботом.\n'
                'Мои ошибки — повторение вопросов, которые пока не получились.\n\n'
                'Выбирай ответы кнопками или отправляй цифры 1–4.\n'
                'В дуэли решение появляется после ответов обоих игроков.\n\n'
                'Меню — выбрать другой режим.\n'
                'Продолжить — вернуться к текущему вопросу.\n'
                'Пропустить — перейти дальше без балла.\n'
                'Завершить — остановить текущую игру.\n\n'
                'Для игры с другом нажми «Дуэль» и отправь ему приглашение.\n'
                'Друг может открыть ссылку или нажать «Войти по коду» и вставить код.\n\n'
                'Если кнопки потерялись, напиши «меню».')
