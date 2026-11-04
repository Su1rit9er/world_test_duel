from storage import connect


SUBJECTS = {'math': 'Математика', 'russian': 'Русский язык', 'informatics': 'Информатика'}


def label(pick):
    subject, grade = pick
    return f'{SUBJECTS[subject]} · {grade} класс'


class Prefs:
    def __init__(self, bank, db):
        self.db = db
        self.catalog = {}
        for q in bank:
            for grade in range(q['grade_min'], q['grade_max'] + 1):
                self.catalog.setdefault((q['subject'], grade), []).append(q)
        self.catalog = {pick: rows for pick, rows in self.catalog.items() if len(rows) >= 5}
        with connect(db) as con:
            con.execute('CREATE TABLE IF NOT EXISTS prefs (uid INTEGER PRIMARY KEY, subject TEXT NOT NULL, grade INTEGER NOT NULL)')

    def get(self, uid):
        with connect(self.db) as con:
            row = con.execute('SELECT subject,grade FROM prefs WHERE uid=?', (uid,)).fetchone()
        return row if row in self.catalog else ('math', 5)

    def grades(self, subject):
        return sorted(grade for sub, grade in self.catalog if sub == subject)

    def save(self, uid, subject, grade):
        pick = (subject, grade)
        if pick not in self.catalog:
            return False
        with connect(self.db) as con:
            con.execute('INSERT INTO prefs VALUES (?,?,?) ON CONFLICT(uid) DO UPDATE SET subject=excluded.subject,grade=excluded.grade',
                        (uid, subject, grade))
        return True

    def pool(self, uid, level='all'):
        return [q for q in self.catalog[self.get(uid)] if level == 'all' or q['difficulty'] == level]
