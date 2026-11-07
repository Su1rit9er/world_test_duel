import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from storage import connect, import_db


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.src = root / 'old.db'
        self.dst = root / 'new.db'
        env = patch.dict(os.environ, {'DATABASE_PATH': str(self.dst)})
        env.start()
        self.addCleanup(env.stop)
        with connect(self.src) as con:
            con.execute('CREATE TABLE scores (uid INTEGER, score INTEGER)')
            con.execute('INSERT INTO scores VALUES (?, ?)', (1, 4))

    def read(self, path):
        with connect(path) as con:
            return con.execute('SELECT uid, score FROM scores').fetchall()

    def test_copy_is_independent(self):
        import_db(self.src)
        self.assertEqual(self.read(self.dst), [(1, 4)])
        with connect(self.dst) as con:
            con.execute('UPDATE scores SET score=5 WHERE uid=1')
        self.assertEqual(self.read(self.dst), [(1, 5)])
        self.assertEqual(self.read(self.src), [(1, 4)])

    def test_existing_file_is_preserved(self):
        with connect(self.dst) as con:
            con.execute('CREATE TABLE scores (uid INTEGER, score INTEGER)')
            con.execute('INSERT INTO scores VALUES (?, ?)', (2, 3))
        with self.assertRaises(SystemExit):
            import_db(self.src)
        self.assertEqual(self.read(self.dst), [(2, 3)])
        self.assertEqual(self.read(self.src), [(1, 4)])

    def test_corrupt_source_leaves_no_copy(self):
        self.src.write_bytes(b'broken database')
        with self.assertRaises(sqlite3.DatabaseError):
            import_db(self.src)
        self.assertFalse(self.dst.exists())
        self.assertEqual(self.src.read_bytes(), b'broken database')

    def test_missing_source_leaves_no_copy(self):
        self.src.unlink()
        with self.assertRaises(SystemExit):
            import_db(self.src)
        self.assertFalse(self.dst.exists())


if __name__ == '__main__':
    unittest.main()