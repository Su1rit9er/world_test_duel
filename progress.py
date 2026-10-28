import sqlite3


class Progress:
    def __init__(self, db):
        self.db = db
        with sqlite3.connect(db) as con:
            con.execute('CREATE TABLE IF NOT EXISTS mistakes (uid INTEGER, qid TEXT, PRIMARY KEY (uid, qid))')

    def mark(self, uid, qid, ok):
        with sqlite3.connect(self.db) as con:
            if ok:
                con.execute('DELETE FROM mistakes WHERE uid=? AND qid=?', (uid, qid))
            else:
                con.execute('INSERT OR IGNORE INTO mistakes VALUES (?, ?)', (uid, qid))

    def ids(self, uid):
        with sqlite3.connect(self.db) as con:
            return {r[0] for r in con.execute('SELECT qid FROM mistakes WHERE uid=?', (uid,))}

    def history(self, uid):
        with sqlite3.connect(self.db) as con:
            rows = con.execute('''
                SELECT date, level, score, total, NULL AS rival FROM results WHERE uid=?
                UNION ALL
                SELECT date, 'duel', CASE WHEN p1=? THEN s1 ELSE s2 END, 5,
                       CASE WHEN p1=? THEN s2 ELSE s1 END
                FROM matches WHERE p1=? OR p2=?
                ORDER BY date DESC LIMIT 10
            ''', (uid, uid, uid, uid, uid)).fetchall()
        if not rows:
            return 'История пока пуста. Пройди тест: /quiz'
        names = {'all': 'Тренировка', 'easy': 'Простой тест', 'medium': 'Средний тест',
                 'hard': 'Сложный тест', 'review': 'Работа над ошибками', 'duel': 'Дуэль'}
        lines = ['Последние результаты (время UTC):']
        for date, level, score, total, rival in rows:
            line = f'{date} · {names.get(level, level)} · {score}/{total}'
            if rival is not None:
                result = 'ничья' if score == rival else 'победа' if score > rival else 'поражение'
                line += f' · соперник {rival}/{total}, {result}'
            lines.append(line)
        return '\n'.join(lines)
