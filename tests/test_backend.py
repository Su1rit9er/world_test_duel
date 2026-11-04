import asyncio
import copy
import json
import os
import sqlite3
import tempfile
import time
import unittest
from collections import defaultdict
from pathlib import Path
from unittest.mock import patch

import httpx

from backend import Backend, Remote, Sender, StopBot
from buttons import Buttons
from quiz import Quiz
from storage import connect


class Network:
    def __init__(self, delay=0.12):
        self.delay = delay
        self.calls = []
        self.fail = None
        self.msgs = defaultdict(list)
        self.events = []

    async def __call__(self, req):
        path = req.url.path
        args = dict(req.url.params)
        body = json.loads(req.content) if req.content else {}
        uid = int(args.get('user_id', 0))
        self.calls.append((time.monotonic(), path, uid, copy.deepcopy(body)))
        await asyncio.sleep(self.delay)
        if self.fail:
            res = await self.fail(req, body)
            if res is not None:
                return res
        if path == '/updates':
            data = {'updates': self.events, 'marker': 7}
            self.events = []
        else:
            data = {}
        if path == '/messages':
            self.msgs[uid].append((time.monotonic(), body))
        return httpx.Response(200, json=data)


class BackendTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / 'stats.db'
        self.quiz = Quiz(self.db)
        self.ui = Buttons(self.quiz, 'SchoolBot')
        self.net = Network()
        self.client = httpx.AsyncClient(base_url='https://max.test', transport=httpx.MockTransport(self.net))
        self.group = await asyncio.TaskGroup().__aenter__()
        self.api = Remote(self.client)
        self.out = Sender(self.api, self.group)
        self.app = Backend(self.api, self.ui, self.out)
        self.proc = self.group.create_task(self.app.process())
        self.seq = 0

    async def asyncTearDown(self):
        self.proc.cancel()
        await self.out.close()
        await self.group.__aexit__(None, None, None)
        await self.client.aclose()
        self.tmp.cleanup()

    def text(self, uid, text):
        self.seq += 1
        return {'update_type': 'message_created', 'message': {
            'recipient': {'chat_type': 'dialog'}, 'sender': {'user_id': uid},
            'body': {'mid': f'msg-{self.seq}', 'text': text}}}

    def click(self, uid, cmd):
        self.seq += 1
        key = next(k for k, (_, action) in self.ui.keys[uid].items() if action == cmd)
        return {'update_type': 'message_callback', 'callback': {
            'user': {'user_id': uid}, 'callback_id': f'cb-{self.seq}', 'payload': key}}

    async def wave(self, events):
        start = time.monotonic()
        for event in events:
            await self.app.inbox.put(event)
        await asyncio.wait_for(self.app.inbox.join(), timeout=10)
        await asyncio.wait_for(self.out.join(), timeout=10)
        return time.monotonic() - start

    async def test_ten_callback_burst(self):
        events = [{'update_type': 'message_callback', 'callback': {
            'user': {'user_id': uid}, 'callback_id': f'burst-{uid}', 'payload': 'old'}} for uid in range(1, 11)]
        elapsed = await self.wave(events)
        self.assertEqual(self.out.sent, 20)
        self.assertEqual(self.app.processed, 10)
        self.assertLess(elapsed, 3)
        print(f'\n10 callbacks / 120 ms network: {elapsed:.3f} s', flush=True)
        times = [stamp for stamp, _, _, _ in self.net.calls]
        self.assertTrue(all(b - a >= 0.045 for a, b in zip(times, times[1:])))

    async def test_ten_training_sessions(self):
        users = range(1, 11)
        await self.wave([self.text(uid, 'меню') for uid in users])
        await self.wave([self.click(uid, '/levels') for uid in users])
        await self.wave([self.click(uid, '/quiz medium') for uid in users])
        old = self.click(1, '1')
        max_wait = 0
        for pos in range(5):
            events = []
            for uid in users:
                game = self.quiz.games[uid]
                answer = str(game['items'][pos]['answer'] + 1)
                events.append(self.click(uid, answer))
            if pos == 0:
                events.extend([copy.deepcopy(events[0]), copy.deepcopy(events[0])])
            max_wait = max(max_wait, await self.wave(events))
        with connect(self.db) as con:
            rows = con.execute('SELECT uid,score,total FROM results ORDER BY uid').fetchall()
        self.assertEqual(rows, [(uid, 5, 5) for uid in users])
        self.assertFalse(self.quiz.games)
        self.assertEqual(self.app.duplicates, 2)
        self.assertEqual(self.out.failed, 0)
        self.assertLess(max_wait, 3)
        await self.wave([self.text(1, 'тренировка')])
        await self.wave([self.click(1, '/quiz easy')])
        await self.wave([old])
        self.assertEqual(self.quiz.games[1]['pos'], 0)
        print(f'\n10 trainings completed; slowest answer wave: {max_wait:.3f} s', flush=True)

    async def test_five_duels(self):
        max_wait = 0
        await self.wave([self.text(uid, 'дуэль') for uid in range(1, 6)])
        await self.wave([self.text(uid + 5, self.quiz.duel.users[uid]) for uid in range(1, 6)])
        self.assertEqual(len(self.quiz.duel.rooms), 5)
        for pos in range(5):
            events = []
            for uid in range(1, 6):
                room = self.quiz.duel.rooms[self.quiz.duel.users[uid]]
                answer = str(room['items'][pos]['answer'] + 1)
                events.extend([self.click(uid, answer), self.click(uid + 5, '/skip')])
            max_wait = max(max_wait, await self.wave(events))
        with connect(self.db) as con:
            rows = con.execute('SELECT p1,p2,s1,s2 FROM matches ORDER BY p1').fetchall()
        self.assertEqual(rows, [(uid, uid + 5, 5, 0) for uid in range(1, 6)])
        self.assertLess(max_wait, 4)
        print(f'\n5 duels completed; slowest answer wave: {max_wait:.3f} s', flush=True)
        self.assertFalse(self.quiz.duel.users)
        self.assertEqual(self.out.failed, 0)

    async def test_slow_user_does_not_block_others(self):
        self.net.delay = 0
        self.out.gap = 0
        self.api.gate.gap = 0
        entered, release = asyncio.Event(), asyncio.Event()

        async def fail(req, body):
            if req.url.path == '/messages' and req.url.params.get('user_id') == '1':
                entered.set()
                await release.wait()
            return None

        self.net.fail = fail
        try:
            for uid in range(1, 11):
                await self.app.inbox.put(self.text(uid, 'меню'))
            await asyncio.wait_for(entered.wait(), timeout=2)
            await asyncio.wait_for(self.app.inbox.join(), timeout=2)
            for uid in range(2, 11):
                await asyncio.wait_for(self.out.queues[uid].join(), timeout=2)
                self.assertTrue(self.net.msgs[uid])
            self.assertFalse(self.net.msgs[1])
        finally:
            release.set()
        await self.out.join()

    async def test_retry_does_not_apply_answer_twice(self):
        self.net.delay = 0
        self.out.gap = 0
        self.api.gate.gap = 0
        await self.wave([self.text(1, '/quiz')])
        event = self.click(1, '1')
        attempts = []

        async def fail(req, body):
            if req.url.path == '/messages':
                attempts.append(copy.deepcopy(body))
                if len(attempts) == 1:
                    return httpx.Response(429, headers={'Retry-After': '0.01'}, json={})
            return None

        self.net.fail = fail
        await self.wave([event, copy.deepcopy(event)])
        self.assertEqual(self.quiz.games[1]['pos'], 1)
        self.assertEqual(len(attempts), 2)
        self.assertEqual(attempts[0], attempts[1])
        self.assertEqual(self.app.duplicates, 1)
        self.assertEqual(self.out.failed, 0)

    async def test_permanent_error_and_bad_events(self):
        self.net.delay = 0
        self.out.gap = 0
        self.api.gate.gap = 0

        async def fail(req, body):
            if req.url.params.get('user_id') == '1':
                return httpx.Response(403, json={})
            return None

        self.net.fail = fail
        events = [None, {}, {'update_type': 'message_callback', 'callback': {'user': []}},
                  {'update_type': 'message_created', 'message': []},
                  {'update_type': 'message_created', 'message': {'recipient': {'chat_type': 'dialog'}, 'body': {'text': 8}}},
                  self.text(1, 'меню'), self.text(2, 'меню')]
        await self.wave(events)
        self.assertTrue(self.net.msgs[2])
        self.assertEqual(self.out.failed, 1)
        self.assertFalse(self.proc.done())

    async def test_polling_continues_while_delivery_waits(self):
        self.net.delay = 0
        self.out.gap = 0
        self.api.gate.gap = 0
        entered, release = asyncio.Event(), asyncio.Event()
        self.net.events = [self.text(1, 'меню')]

        async def fail(req, body):
            if req.url.path == '/messages':
                entered.set()
                await release.wait()
            return None

        self.net.fail = fail
        task = self.group.create_task(self.app.poll())
        try:
            await asyncio.wait_for(entered.wait(), timeout=2)
            await asyncio.sleep(0.2)
            polls = [x for x in self.net.calls if x[1] == '/updates']
            self.assertGreaterEqual(len(polls), 2)
        finally:
            task.cancel()
            release.set()
        await self.out.join()

    async def test_retry_exhaustion_and_auth_error(self):
        self.net.delay = 0
        self.api.gate.gap = 0
        self.out.gap = 0
        self.out.tries = 2

        async def fail(req, body):
            uid = req.url.params.get('user_id')
            if uid == '1':
                return httpx.Response(503, headers={'Retry-After': '0'}, json={})
            if uid == '3':
                return httpx.Response(401, json={})
            return None

        self.net.fail = fail
        await self.wave([self.text(1, 'меню'), self.text(2, 'меню')])
        self.assertEqual(self.out.failed, 1)
        self.assertTrue(self.net.msgs[2])
        self.assertEqual(len([x for x in self.net.calls if x[2] == 1]), 2)
        with self.assertRaises(StopBot):
            await self.out.deliver(3, ('/messages', {'user_id': 3}, {'text': 'ok'}))

    async def test_wal_readers_and_closed_connections(self):
        with connect(self.db) as con:
            self.assertEqual(con.execute('PRAGMA journal_mode').fetchone()[0], 'wal')
        with self.assertRaises(sqlite3.ProgrammingError):
            con.execute('SELECT 1')
        hold = sqlite3.connect(self.db)
        try:
            hold.execute('BEGIN IMMEDIATE')
            hold.execute('INSERT INTO names VALUES (99, "pending")')
            started = time.monotonic()
            await asyncio.to_thread(self.quiz.log.ids, 1)
            self.assertLess(time.monotonic() - started, 1)
        finally:
            hold.rollback()
            hold.close()


class StartupTests(unittest.IsolatedAsyncioTestCase):
    async def test_main_startup_and_shutdown(self):
        import main

        net = Network(delay=0)
        net.events = [{'update_type': 'message_created', 'message': {
            'recipient': {'chat_type': 'dialog'}, 'sender': {'user_id': 1},
            'body': {'mid': 'startup', 'text': 'меню'}}}]
        client = httpx.AsyncClient
        clients = []

        def create(**args):
            obj = client(**args, transport=httpx.MockTransport(net))
            clients.append(obj)
            return obj

        with tempfile.TemporaryDirectory() as tmp:
            env = {'MAX_BOT_TOKEN': 'local-test', 'DATABASE_PATH': str(Path(tmp) / 'stats.db')}
            with patch.dict(os.environ, env), patch('sys.argv', ['main.py']), patch('httpx.AsyncClient', create):
                task = asyncio.create_task(main.run_async())
                try:
                    async with asyncio.timeout(3):
                        while not net.msgs[1]:
                            if task.done():
                                await task
                            await asyncio.sleep(0.01)
                    self.assertEqual([x[1] for x in net.calls[:2]], ['/me', '/subscriptions'])
                finally:
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                self.assertTrue(clients[0].is_closed)


if __name__ == '__main__':
    unittest.main(verbosity=2)
