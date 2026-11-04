from contextlib import nullcontext

from storage import connect


class Progress:
    def __init__(self, db):
        self.db = db
        with connect(db) as con:
            con.execute('CREATE TABLE IF NOT EXISTS mistakes (uid INTEGER, qid TEXT, PRIMARY KEY (uid, qid))')
            con.execute('CREATE TABLE IF NOT EXISTS names (uid INTEGER PRIMARY KEY, name TEXT NOT NULL)')

    def name(self, uid, name):
        if not isinstance(name, str) or not name.strip():
            return
        name = ' '.join(name.split())[:40]
        with connect(self.db) as con:
            con.execute('INSERT INTO names VALUES (?,?) ON CONFLICT(uid) DO UPDATE SET name=excluded.name WHERE names.name != excluded.name', (uid, name))

    def board(self, uid):
        with connect(self.db) as con:
            rows = con.execute('''
                SELECT uid,SUM(win) AS wins,COUNT(*) AS games,SUM(score) AS points FROM (
                    SELECT p1 AS uid,s1>s2 AS win,s1 AS score FROM matches WHERE p1>=0 AND p2>=0
                    UNION ALL
                    SELECT p2,s2>s1,s2 FROM matches WHERE p1>=0 AND p2>=0
                ) GROUP BY uid ORDER BY wins DESC,points DESC,games ASC,uid LIMIT 10
            ''').fetchall()
            names = dict(con.execute('SELECT uid,name FROM names'))
        lines = ['🏆 Рейтинг', 'Дуэли с другими игроками', '']
        if not rows:
            lines.append('Заверши первую дуэль с другом или случайным соперником, чтобы открыть рейтинг.')
        for index, (player, wins, games, points) in enumerate(rows, 1):
            name = names.get(player, f'Участник {index}')
            label = ' · ты' if player == uid else ''
            lines.append(f'{index}. {name}{label}\nПобеды  ·  {wins}   Баллы  ·  {points}   Игры  ·  {games}\n')
        lines.append('Выше — больше побед, затем баллов; при равенстве — меньше игр.\nТренировки и игры с ботом не учитываются.')
        return '\n'.join(lines)

    def mark(self, uid, qid, ok, con=None):
        with connect(self.db) if con is None else nullcontext(con) as con:
            if ok:
                con.execute('DELETE FROM mistakes WHERE uid=? AND qid=?', (uid, qid))
            else:
                con.execute('INSERT OR IGNORE INTO mistakes VALUES (?, ?)', (uid, qid))

    def ids(self, uid):
        with connect(self.db) as con:
            return {r[0] for r in con.execute('SELECT qid FROM mistakes WHERE uid=?', (uid,))}

    def rating(self, uid):
        with connect(self.db) as con:
            rows = con.execute('''
                SELECT score, total FROM (
                    SELECT score, total, date, rowid AS n FROM results WHERE uid=? AND level!='review'
                    UNION ALL
                    SELECT CASE WHEN p1=? THEN s1 ELSE s2 END, 5, date, rowid
                    FROM matches WHERE p1=? OR p2=?
                ) ORDER BY date DESC, n DESC LIMIT 20
            ''', (uid, uid, uid, uid)).fetchall()
        score = sum(row[0] for row in rows)
        total = sum(row[1] for row in rows)
        return round(1000 * (score + 7.5) / (total + 15))

    def awards(self, uid):
        with connect(self.db) as con:
            tests, perfect = con.execute('''
                SELECT COUNT(*), COALESCE(MAX(score=5 AND total=5),0)
                FROM results WHERE uid=? AND level!='review'
            ''', (uid,)).fetchone()
            duels, wins = con.execute('''
                SELECT COUNT(*), COALESCE(SUM(CASE WHEN p1=? THEN s1>s2 ELSE s2>s1 END),0)
                FROM matches WHERE p1=? OR p2=?
            ''', (uid, uid, uid)).fetchone()
        items = [('Первый шаг', tests > 0, 'Пройди первую тренировку.', 'Первая тренировка завершена.'),
                 ('Без ошибок', bool(perfect), 'Набери 5 из 5 в обычной тренировке.', 'Пять верных ответов из пяти.'),
                 ('Соперник найден', duels > 0, 'Заверши дуэль с другом или ботом.', 'Первая дуэль завершена.'),
                 ('Первая победа', wins > 0, 'Выиграй дуэль.', 'Первая победа в дуэли.'),
                 ('В игре', tests + duels >= 10, 'Пройди 10 тренировок и дуэлей.', 'Десять игр позади!')]
        count = sum(ok for _, ok, _, _ in items)
        blocks = [f'🏅 Достижения\nОткрыто: {count} из {len(items)}']
        for name, ok, rule, note in items:
            blocks.append(f"{'✅' if ok else '🔒'} {name}\n{note if ok else rule}")
        if tests + duels < 10:
            blocks[-1] += f'\nПрогресс: {tests + duels} из 10'
        return '\n\n'.join(blocks)

    def suggest(self, uid, bank):
        rating = self.rating(uid)
        level = 'easy' if rating < 500 else 'medium' if rating < 750 else 'hard'
        return level if sum(q['difficulty'] == level for q in bank) >= 5 else 'all'

    def advice(self, uid, bank):
        ids = self.ids(uid)
        counts = {level: sum(q['id'] in ids and q['difficulty'] == level for q in bank)
                  for level in ('easy', 'medium', 'hard')}
        with connect(self.db) as con:
            rows = con.execute("SELECT score,total FROM results WHERE uid=? AND level!='review' ORDER BY rowid DESC LIMIT 5",
                               (uid,)).fetchall()
        lines = ['🎯 Что повторить', '', f'Вопросов на повторение  ·  {sum(counts.values())}', '']
        names = {'easy': 'Простые', 'medium': 'Средние', 'hard': 'Сложные', 'all': 'Смешанные'}
        lines += [f'{names[level]}: {count}' for level, count in counts.items()]
        if rows:
            score, total = sum(row[0] for row in rows), sum(row[1] for row in rows)
            lines.append(f'Последние {len(rows)} тренировок: {score}/{total} верных ответов.')
        if any(counts.values()):
            lines.append('Нажми «Мои ошибки»: там вопросы, в которых ты ошибся. Верный ответ убирает ошибку.')
        else:
            level = self.suggest(uid, bank)
            lines.append(f'Попробуй тренировку: {names[level].lower()} вопросы. Кнопка «Начать тренировку» уже выберет эту сложность.')
        lines.append('Это подсказка по сыгранным заданиям, а не оценка по всему курсу.')
        return '\n'.join(lines)

    def history(self, uid):
        with connect(self.db) as con:
            rows = con.execute('''
                SELECT date, level, score, total, NULL AS rival FROM results WHERE uid=?
                UNION ALL
                SELECT date, CASE WHEN p1=-1 OR p2=-1 THEN 'bot' ELSE 'duel' END,
                       CASE WHEN p1=? THEN s1 ELSE s2 END, 5,
                       CASE WHEN p1=? THEN s2 ELSE s1 END
                FROM matches WHERE p1=? OR p2=?
                ORDER BY date DESC LIMIT 10
            ''', (uid, uid, uid, uid, uid)).fetchall()
        if not rows:
            return '🕘 История игр\n\nЗдесь появятся твои завершённые тренировки и дуэли.\nНачни с тренировки или пригласи друга.'
        names = {'all': 'Тренировка', 'easy': 'Простой тест', 'medium': 'Средний тест',
                 'hard': 'Сложный тест', 'review': 'Работа над ошибками', 'duel': 'Дуэль', 'bot': 'Дуэль с ботом'}
        lines = ['🕘 История игр', 'Последние 10 результатов · время UTC', '']
        for date, level, score, total, rival in rows:
            line = f"{date[:16]} · {names.get(level, level)}\nРезультат  ·  {score}/{total}"
            if rival is not None:
                result = 'ничья' if score == rival else 'победа' if score > rival else 'поражение'
                line += f'   Соперник  ·  {rival}/{total}\n{result.capitalize()}'
            lines.append(line + '\n')
        return '\n'.join(lines)
