import asyncio
import copy
import tempfile
import time
import unittest
from pathlib import Path

import httpx

from backend import Backend, Remote, Sender
from max_adapter import Buttons
from quiz import Quiz
from test_backend import Network


class Latency(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.quiz = Quiz(Path(self.tmp.name) / 'stats.db')
        self.ui = Buttons(self.quiz)
        self.net = Network()
        self.client = httpx.AsyncClient(base_url='https://max.test', transport=httpx.MockTransport(self.net))
        self.group = await asyncio.TaskGroup().__aenter__()
        self.out = Sender(Remote(self.client), self.group)
        self.app = Backend(self.out.api, self.ui, self.out)

    async def asyncTearDown(self):
        await self.out.close()
        await self.group.__aexit__(None, None, None)
        await self.client.aclose()
        self.tmp.cleanup()

    def event(self, uid, pos):
        return {'update_type': 'message_callback', 'callback': {
            'user': {'user_id': uid}, 'callback_id': str(pos), 'payload': f'nav:{uid}:/profile'}}

    async def test_first_reply_has_no_ack_pause(self):
        start = time.monotonic()
        await self.app.handle(self.event(1, 1))
        await self.out.join()
        elapsed = self.net.msgs[1][0][0] - start
        print(f'\nOne callback, 120 ms network: {elapsed:.3f} s', flush=True)
        self.assertLess(elapsed, 0.4)

    async def test_ten_clicks_one_user_keep_order_and_limits(self):
        start = time.monotonic()
        expected = []
        for pos in range(10):
            body = {'text': str(pos)}
            expected.append(body)
            await self.out.put(1, ('/answers', {'callback_id': str(pos)}, {'notification': 'ok'}))
            await self.out.put(1, ('/messages', {'user_id': 1}, body))
        await self.out.join()
        elapsed = self.net.msgs[1][-1][0] - start
        print(f'\nTen clicks, one user: {elapsed:.3f} s', flush=True)
        self.assertLess(elapsed, 6)
        self.assertEqual([body for _, body in self.net.msgs[1]], expected)
        for path in ['/messages', '/answers']:
            stamps = [stamp for stamp, route, _, _ in self.net.calls if route == path]
            self.assertEqual(len(stamps), 10)
            self.assertTrue(all(b - a >= 0.5 for a, b in zip(stamps, stamps[1:])))
        stamps = [stamp for stamp, _, _, _ in self.net.calls]
        self.assertTrue(all(b - a >= 0.045 for a, b in zip(stamps, stamps[1:])))

    async def test_stuck_ack_does_not_block_message(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def fail(req, body):
            if req.url.path == '/answers':
                entered.set()
                await release.wait()
            return None

        self.net.fail = fail
        try:
            await self.app.handle(self.event(1, 1))
            await asyncio.wait_for(entered.wait(), timeout=1)
            async with asyncio.timeout(0.5):
                while not self.net.msgs[1]:
                    await asyncio.sleep(0.005)
        finally:
            release.set()
        await self.out.join()

    async def test_ack_failure_is_not_retried_ahead_of_new_acks(self):
        self.net.delay = 0
        self.out.gap = 0
        self.out.api.gate.gap = 0
        attempts = []

        async def fail(req, body):
            if req.url.path == '/answers':
                attempts.append(req.url.params['callback_id'])
                if len(attempts) == 1:
                    raise httpx.ReadTimeout('simulated', request=req)
            return None

        self.net.fail = fail
        await self.app.handle(self.event(1, 1))
        await self.app.handle(self.event(1, 2))
        await asyncio.wait_for(self.out.join(), timeout=1)
        self.assertEqual(attempts, ['1', '2'])
        self.assertEqual(len(self.net.msgs[1]), 2)
        self.assertEqual(self.out.failed, 1)

    async def test_ack_rate_limit_delays_next_ack_only(self):
        self.net.delay = 0
        self.out.gap = 0
        self.out.api.gate.gap = 0
        calls = []

        async def fail(req, body):
            if req.url.path == '/answers':
                calls.append(req.url.params['callback_id'])
                if len(calls) == 1:
                    return httpx.Response(429, headers={'Retry-After': '0.15'}, json={})
            return None

        self.net.fail = fail
        for pos in [1, 2]:
            await self.out.put(1, ('/answers', {'callback_id': str(pos)}, {'notification': 'ok'}))
            await self.out.put(1, ('/messages', {'user_id': 1}, {'text': str(pos)}))
        await self.out.join()
        self.assertEqual(calls, ['1', '2'])
        stamps = [stamp for stamp, path, _, _ in self.net.calls if path == '/answers']
        self.assertGreaterEqual(stamps[1] - stamps[0], 0.14)
        self.assertLess(self.net.msgs[1][-1][0], stamps[1])

    async def test_stuck_message_does_not_block_ack(self):
        self.net.delay = 0
        self.out.gap = 0
        self.out.api.gate.gap = 0
        entered, release = asyncio.Event(), asyncio.Event()

        async def fail(req, body):
            if req.url.path == '/messages':
                entered.set()
                await release.wait()
            return None

        self.net.fail = fail
        try:
            await self.app.handle(self.event(1, 1))
            await asyncio.wait_for(entered.wait(), timeout=1)
            await self.app.handle(self.event(1, 2))
            await asyncio.wait_for(self.out.acks[1].join(), timeout=0.5)
            self.assertEqual(len([x for x in self.net.calls if x[1] == '/answers']), 2)
            self.assertFalse(self.net.msgs[1])
        finally:
            release.set()
        await self.out.join()

    async def test_message_retry_keeps_game_mutation_single(self):
        self.net.delay = 0
        self.out.gap = 0
        self.out.api.gate.gap = 0
        self.quiz.handle(1, '/quiz')
        self.ui.body(1, self.quiz.question(1))
        key = next(key for key, (_, cmd) in self.ui.keys[1].items() if cmd == '1')
        event = {'update_type': 'message_callback', 'callback': {
            'user': {'user_id': 1}, 'callback_id': 'answer', 'payload': key}}
        bodies = []

        async def fail(req, body):
            if req.url.path == '/messages':
                bodies.append(copy.deepcopy(body))
                if len(bodies) == 1:
                    return httpx.Response(503, headers={'Retry-After': '0'}, json={})
            return None

        self.net.fail = fail
        await self.app.handle(event)
        await self.app.handle(copy.deepcopy(event))
        await self.out.join()
        self.assertEqual(self.quiz.games[1]['pos'], 1)
        self.assertEqual(bodies[0], bodies[1])
        self.assertEqual(self.app.duplicates, 1)


if __name__ == '__main__':
    unittest.main()
