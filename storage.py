import os
import sqlite3
from pathlib import Path
from contextlib import closing, contextmanager


def db_path(db=None):
    root = Path(__file__).resolve().parent
    path = Path(db or os.getenv('DATABASE_PATH') or root / 'stats.db')
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


@contextmanager
def connect(path):
    con = sqlite3.connect(path, timeout=10)
    try:
        con.execute('PRAGMA busy_timeout=10000')
        with con:
            yield con
    finally:
        con.close()


def import_db(src):
    src = Path(src).resolve()
    dst = db_path().resolve()
    if not src.is_file():
        raise SystemExit('Исходная база не найдена.')
    try:
        with dst.open('xb'):
            pass
    except FileExistsError:
        raise SystemExit('В хранилище уже есть база. Импорт остановлен, данные сохранены.') from None
    try:
        with closing(sqlite3.connect(src.as_uri() + '?mode=ro', uri=True)) as old:
            if old.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise ValueError('Исходная база повреждена.')
            with closing(sqlite3.connect(dst)) as new:
                old.backup(new)
    except Exception:
        dst.unlink(missing_ok=True)
        raise
    print('Статистика перенесена в постоянное хранилище.')
