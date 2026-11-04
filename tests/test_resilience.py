import asyncio
import copy
import math
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from backend import Backend, Remote, Sender, retry_after
from buttons import Buttons, prepare
from quiz import Quiz
from storage import connect
from test_backend import Network


class SavingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / 'stats.db'
        self.quiz = Quiz(self.db)

    def force_error(self, table):
        with connect(self.db) as con:
            con.execute(f"CREATE TRIGGER fail BEFORE INSERT ON {table} BEGIN SELECT RAISE(ABORT,'forced'); END")

    def allow(self):
        with connect(self.db) as con:
            con.execute('DROP TRIGGER fail')

    def test_training_rollback_and_retry(self):
        self.quiz.handle(1, '/quiz')
        for _ in range(4):
            game = self.quiz.games[1]
            self.quiz.handle(1, str(game['items'][game['pos']]['answer'] + 1))
        game = copy.deepcopy(self.quiz.games[1])
        q = game['items'][4]
        self.quiz.log.mark(1, q['id'], False)
        self.force_error('results')
        with self.assertRaises(sqlite3.IntegrityError):
            self.quiz.handle(1, str(q['answer'] + 1))
        self.assertEqual(self.quiz.games[1], game)
        self.assertIn(q['id'], self.quiz.log.ids(1))
        self.allow()
        self.quiz.handle(1, str(q['answer'] + 1))
        self.assertNotIn(1, self.quiz.games)
        with connect(self.db) as con:
            self.assertEqual(con.execute('SELECT score,total FROM results').fetchall(), [(5, 5)])
        self.assertNotIn(q['id'], self.quiz.log.ids(1))

    def test_duel_rollback_and_retry(self):
        duel = self.quiz.duel
        duel.handle(1, '/duel')
        code = duel.users[1]
        duel.handle(2, '/join ' + code)
        for _ in range(4):
            room = duel.rooms[code]
            answer = str(room['items'][room['pos']]['answer'] + 1)
            duel.handle(1, answer)
            duel.handle(2, answer)
        q = duel.rooms[code]['items'][4]
        answer = str(q['answer'] + 1)
        duel.handle(1, answer)
        room = copy.deepcopy(duel.rooms[code])
        duel.log.mark(2, q['id'], False)
        self.force_error('matches')
        with self.assertRaises(sqlite3.IntegrityError):
            duel.handle(2, answer)
        self.assertEqual(duel.rooms[code], room)
        self.assertIn(q['id'], duel.log.ids(2))
        self.allow()
        duel.handle(2, answer)
        self.assertFalse(duel.rooms)
        self.assertFalse(duel.users)
        with connect(self.db) as con:
            self.assertEqual(con.execute('SELECT s1,s2 FROM matches').fetchall(), [(5, 5)])

    def test_reused_invite_does_not_conflict_with_history(self):
        with connect(self.db) as con:
            con.execute("INSERT INTO matches(code,p1,p2,s1,s2) VALUES ('aaaaaa',1,2,5,0)")
        with patch('duel.secrets.token_hex', side_effect=['aaaaaa', 'bbbbbb']):
            self.quiz.duel.handle(3, '/duel')
        self.assertEqual(self.quiz.duel.users[3], 'bbbbbb')

    def test_retry_after_must_be_finite(self):
        req = httpx.Request('POST', 'https://max.test/messages')
        for value in ['nan', 'inf', '-inf', 'invalid']:
            res = httpx.Response(429, headers={'Retry-After': value}, request=req)
            err = httpx.HTTPStatusError('limited', request=req, response=res)
            delay = retry_after(err, 2)
            self.assertTrue(math.isfinite(delay), value)
            self.assertEqual(delay, 2, value)


class QueueTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / 'stats.db'
        self.quiz = Quiz(self.db)
        self.ui = Buttons(self.quiz)
        self.net = Network(delay=0)
        self.client = httpx.AsyncClient(base_url='https://max.test', transport=httpx.MockTransport(self.net))
        self.group = await asyncio.TaskGroup().__aenter__()
        self.out = Sender(Remote(self.client, gap=0), self.group, gap=0)
        self.app = Backend(self.out.api, self.ui, self.out)

    async def asyncTearDown(self):
        await self.out.close()
        await self.group.__aexit__(None, None, None)
        await self.client.aclose()
        self.tmp.cleanup()

    def event(self, uid, mid, text='меню'):
        return {'update_type': 'message_created', 'message': {
            'recipient': {'chat_type': 'dialog'}, 'sender': {'user_id': uid},
            'body': {'mid': mid, 'text': text}}}

    async def test_flood_does_not_block_other_player(self):
        release = asyncio.Event()

        async def fail(req, body):
            if req.url.params.get('user_id') == '1':
                await release.wait()
            return None

        self.net.fail = fail
        try:
            async with asyncio.timeout(2):
                for pos in range(120):
                    await self.app.handle(self.event(1, str(pos)))
                await self.app.handle(self.event(2, 'other'))
                await self.out.queues[2].join()
            self.assertTrue(self.net.msgs[2])
            self.assertLessEqual(self.out.queues[1].qsize(), 100)
            self.assertGreater(self.app.skipped, 0)
        finally:
            release.set()

    async def test_overload_preserves_answer_and_buttons(self):
        await self.app.handle(self.event(1, 'start', '/quiz'))
        await self.out.join()
        keys = copy.deepcopy(self.ui.keys[1])
        key = next(k for k, (_, action) in keys.items() if action == '1')
        event = {'update_type': 'message_callback', 'callback': {
            'user': {'user_id': 1}, 'payload': key, 'callback_id': 'answer'}}
        queue = self.out.queues[1]
        for _ in range(97):
            queue.put_nowait(('/messages', {'user_id': 1}, {'text': 'queued'}))
        await self.app.handle(event)
        self.assertEqual(self.quiz.games[1]['pos'], 0)
        self.assertEqual(self.ui.keys[1], keys)
        self.assertEqual(self.app.skipped, 1)
        await self.out.join()
        await self.app.handle(event)
        await self.out.join()
        self.assertEqual(self.quiz.games[1]['pos'], 1)

    async def test_db_error_keeps_backend_and_game_usable(self):
        await self.app.handle(self.event(1, 'start', '/quiz'))
        await self.out.join()
        pos = self.quiz.games[1]['pos']
        with patch('backend.prepare', side_effect=sqlite3.OperationalError('forced')):
            await self.app.handle(self.event(1, 'failed', '1'))
        await self.app.handle(self.event(2, 'other'))
        await self.out.join()
        self.assertEqual(self.quiz.games[1]['pos'], pos)
        self.assertTrue(self.net.msgs[2])
        self.assertGreaterEqual(len(self.net.msgs[1]), 2)
        self.assertTrue(any(action == '/resume' for _, action in self.ui.keys[1].values()))

    async def test_failed_final_callback_can_be_retried(self):
        await self.app.handle(self.event(1, 'start', '/quiz'))
        await self.out.join()
        for pos in range(4):
            game = self.quiz.games[1]
            answer = str(game['items'][pos]['answer'] + 1)
            await self.app.handle(self.event(1, 'round-' + str(pos), answer))
            await self.out.join()
        game = self.quiz.games[1]
        answer = str(game['items'][4]['answer'] + 1)
        key = next(k for k, (_, action) in self.ui.keys[1].items() if action == answer)
        event = {'update_type': 'message_callback', 'callback': {
            'user': {'user_id': 1}, 'payload': key, 'callback_id': 'last'}}
        with connect(self.db) as con:
            con.execute("CREATE TRIGGER fail BEFORE INSERT ON results BEGIN SELECT RAISE(ABORT,'forced'); END")
        await self.app.handle(event)
        await self.out.join()
        self.assertEqual(self.quiz.games[1]['pos'], 4)
        resume = next(k for k, (_, action) in self.ui.keys[1].items() if action == '/resume')
        with connect(self.db) as con:
            con.execute('DROP TRIGGER fail')
        await self.app.handle({'update_type': 'message_callback', 'callback': {
            'user': {'user_id': 1}, 'payload': resume, 'callback_id': 'resume'}})
        await self.out.join()
        key = next(k for k, (_, action) in self.ui.keys[1].items() if action == answer)
        await self.app.handle({'update_type': 'message_callback', 'callback': {
            'user': {'user_id': 1}, 'payload': key, 'callback_id': 'retry'}})
        await self.out.join()
        await self.app.handle(event)
        await self.out.join()
        self.assertNotIn(1, self.quiz.games)
        with connect(self.db) as con:
            self.assertEqual(con.execute('SELECT score,total FROM results').fetchall(), [(5, 5)])

    async def test_invalid_callback_id_does_not_apply_answer(self):
        await self.app.handle(self.event(1, 'start', '/quiz'))
        await self.out.join()
        key = next(k for k, (_, action) in self.ui.keys[1].items() if action == '1')
        for value in ['', None, [], {'id': 'bad'}, 12]:
            event = {'update_type': 'message_callback', 'callback': {
                'user': {'user_id': 1}, 'payload': key, 'callback_id': value}}
            self.assertEqual(prepare(self.ui, event), [])
            self.assertEqual(self.quiz.games[1]['pos'], 0)

    async def test_poll_retry_keeps_marker(self):
        reached = asyncio.Event()
        markers = []

        async def call(method, path, **args):
            markers.append(args['params'].get('marker'))
            if len(markers) == 1:
                return {'updates': [self.event(1, 'first')], 'marker': 17}
            if len(markers) == 2:
                req = httpx.Request('GET', 'https://max.test/updates')
                res = httpx.Response(503, headers={'Retry-After': '0'}, request=req)
                raise httpx.HTTPStatusError('temporary', request=req, response=res)
            if len(markers) == 3:
                return {'updates': [self.event(2, 'second')], 'marker': 18}
            reached.set()
            await asyncio.Event().wait()

        with patch.object(self.out.api, 'call', side_effect=call):
            task = asyncio.create_task(self.app.poll())
            try:
                await asyncio.wait_for(reached.wait(), timeout=2)
                self.assertEqual(markers, [None, 17, 17, 18])
                self.assertEqual(self.app.inbox.qsize(), 2)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def test_empty_ids_do_not_hide_distinct_events(self):
        for uid in [1, 2]:
            event = self.event(uid, '', '/quiz')
            await self.app.handle(event)
        await self.out.join()
        self.assertEqual(set(self.quiz.games), {1, 2})


if __name__ == '__main__':
    unittest.main(verbosity=2)
