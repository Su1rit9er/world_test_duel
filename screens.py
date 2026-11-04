from prefs import SUBJECTS


def rows(app, reply):
    if reply.rows is not None:
        return reply.rows
    uid, view = reply.uid, reply.view
    quiz = app.quiz
    busy = app.busy(uid)
    home = [[('▶ Тренировка', '/levels')], [('⚔ Дуэль', '/duel'), ('🤖 С ботом', '/bots')],
            [('🎲 Найти соперника', '/random')], [('Войти по коду', '/enter')],
            [('Ошибки', '/mistakes'), ('Повторить', '/advice')], [('Мой профиль', '/profile')],
            [('Как играть', '/help')]] + app.extras('home')
    if view == 'home':
        return [[('Продолжить', '/resume'), ('Завершить', '/stop')]] + home if busy else home
    if view == 'profile':
        menu = [[('Предмет', '/subjects'), ('Класс', '/grades')],
                [('Статистика', '/stats'), ('История', '/history')], [('Рейтинг', '/rating')],
                [('Достижения', '/awards')], [('Ещё тренировку', '/again')]]
        menu += app.extras('profile')
        menu.append([('Как играть', '/help'), ('‹ Меню', '/start')])
        return [[('Продолжить', '/resume')]] + menu if busy else menu
    if view == 'subjects':
        subject, grade = quiz.prefs.get(uid)
        menu = [[(('✓ ' if sub == subject else '') + name, '/subject ' + sub)]
                for sub, name in SUBJECTS.items() if quiz.prefs.grades(sub)]
        return menu + [[('‹ Профиль', '/profile'), ('‹ Меню', '/start')]]
    if view == 'grades':
        subject, grade = quiz.prefs.get(uid)
        opts = [(('✓ ' if n == grade else '') + f'{n} класс', f'/grade {n}') for n in quiz.prefs.grades(subject)]
        return [opts[i:i + 2] for i in range(0, len(opts), 2)] + [[('‹ Профиль', '/profile'), ('‹ Меню', '/start')]]
    if view in ('progress', 'advice'):
        menu = [[('Продолжить', '/resume')]] if busy else [[('Ещё тренировку', '/again')]]
        if view == 'advice' and not busy:
            bank = quiz.pool(uid)
            level = quiz.log.suggest(uid, bank)
            ids = quiz.log.ids(uid) & {q['id'] for q in bank}
            cmd = '/quiz' if level == 'all' else '/quiz ' + level
            menu = [[('Мои ошибки', '/mistakes')]] if ids else [[('Начать тренировку', cmd)]]
        else:
            menu.append([('Мои ошибки', '/mistakes')])
        return menu + [[('‹ Профиль', '/profile'), ('‹ Меню', '/start')]]
    if view in ('question', 'waiting'):
        menu = [[(str(i), str(i)) for i in range(1, 5)], [('Пропустить', '/skip')]] if view == 'question' else [[('Продолжить', '/resume')]]
        return menu + [[('Завершить', '/stop'), ('Меню', '/start')]]
    if view == 'levels':
        names = {'easy': 'Базовый', 'medium': 'Средний', 'hard': 'Сложный'}
        opts = [(names[level], '/quiz ' + level) for level in quiz.levels(uid)] + [('Смешанный', '/quiz')]
        return [opts[i:i + 2] for i in range(0, len(opts), 2)] + [[('‹ Меню', '/start')]]
    if view == 'bots':
        return [[('Полегче', '/bot easy'), ('Посложнее', '/bot hard')], [('‹ Меню', '/start')]]
    if view == 'enter':
        return [[('Отмена', '/start')]]
    if view == 'quiz_done':
        return [[('↻ Ещё тренировку', '/again')], [('Ошибки', '/mistakes'), ('Результаты', '/stats')], [('‹ Меню', '/start')]]
    if view == 'duel_done':
        return [[('Сыграть ещё', '/duel')], [('Ошибки', '/mistakes'), ('Результаты', '/stats')], [('‹ Меню', '/start')]]
    return home
