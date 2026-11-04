import asyncio
import hashlib
import json
import logging
import math
import sqlite3
import time
from collections import OrderedDict
from email.utils import parsedate_to_datetime

import httpx

from max_adapter import prepare


log = logging.getLogger(__name__)


class StopBot(RuntimeError):
    pass


class Gate:
    def __init__(self, gap=0.05):
        self.gap = gap
        self.next = 0
        self.lock = asyncio.Lock()

    async def wait(self):
        async with self.lock:
            delay = self.next - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            self.next = time.monotonic() + self.gap


class Remote:
    def __init__(self, client, gap=0.05):
        self.client = client
        self.gate = Gate(gap)

    async def request(self, method, path, **args):
        started = time.monotonic()
        try:
            return await self.client.request(method, path, **args)
        finally:
            elapsed = time.monotonic() - started
            if path != '/updates' and elapsed >= 2:
                log.warning('Медленный запрос MAX %s: %.2f с', path, elapsed)

    async def call(self, method, path, **args):
        await self.gate.wait()
        res = await self.request(method, path, **args)
        res.raise_for_status()
        data = res.json()
        if not isinstance(data, dict) or data.get('success') is False:
            raise ValueError('MAX вернул некорректный ответ')
        return data


def retry_after(err, fallback):
    if not isinstance(err, httpx.HTTPStatusError):
        return fallback
    value = err.response.headers.get('Retry-After', '')
    try:
        delay = float(value)
        return max(0, delay) if math.isfinite(delay) else fallback
    except ValueError:
        try:
            return max(0, parsedate_to_datetime(value).timestamp() - time.time())
        except (TypeError, ValueError, OverflowError):
            return fallback


class Sender:
    def __init__(self, api, group, gap=0.55, tries=5):
        self.api = api
        self.group = group
        self.gap = gap
        self.tries = tries
        self.queues = {}
        self.acks = {}
        self.tasks = {}
        self.next = {}
        self.sent = 0
        self.failed = 0

    def ready(self, uid):
        return all(uid not in queues or queues[uid].qsize() <= queues[uid].maxsize - 4
                   for queues in (self.queues, self.acks))

    async def put(self, uid, job):
        path = '/answers' if job[0] == '/answers' else '/messages'
        queues = self.acks if path == '/answers' else self.queues
        key = (uid, path)
        if uid not in queues:
            queue = asyncio.Queue(maxsize=100)
            queues[uid] = queue
            self.tasks[key] = self.group.create_task(self.worker(uid, queue, path))
        try:
            queues[uid].put_nowait(job)
        except asyncio.QueueFull:
            self.failed += 1
            log.warning('Очередь отправки переполнена: сообщение пропущено')

    async def worker(self, uid, queue, path):
        key = (uid, path)
        queues = self.acks if path == '/answers' else self.queues
        try:
            while True:
                try:
                    job = await asyncio.wait_for(queue.get(), timeout=60)
                except TimeoutError:
                    if queue.empty():
                        return
                    continue
                try:
                    await self.deliver(uid, job)
                finally:
                    queue.task_done()
        finally:
            queues.pop(uid, None)
            self.tasks.pop(key, None)
            self.next.pop(key, None)

    async def deliver(self, uid, job):
        path, args, body = job
        ack = path == '/answers'
        key = (uid, path)
        tries = 1 if ack else self.tries
        timeout = httpx.Timeout(3, connect=3) if ack else httpx.Timeout(10, connect=5)
        for attempt in range(tries):
            delay = self.next.get(key, 0) - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            try:
                await self.api.gate.wait()
                self.next[key] = time.monotonic() + self.gap
                res = await self.api.request('POST', path, params=args, json=body, timeout=timeout)
                res.raise_for_status()
                data = res.json()
                if not isinstance(data, dict) or data.get('success') is False:
                    raise ValueError('MAX отклонил отправку')
                self.sent += 1
                return
            except (httpx.HTTPError, ValueError) as err:
                code = err.response.status_code if isinstance(err, httpx.HTTPStatusError) else None
                if code == 401:
                    raise StopBot('MAX отклонил токен. Проверь настройки и запусти бота снова.') from None
                if code is not None and 400 <= code < 500 and code != 429:
                    self.failed += 1
                    log.warning('Отправка пропущена: HTTP %s, метод %s', code, path)
                    return
                if ack:
                    if code == 429:
                        self.next[key] = max(self.next[key], time.monotonic() + retry_after(err, 1))
                    self.failed += 1
                    log.warning('Подтверждение кнопки пропущено: %s', type(err).__name__)
                    return
                if attempt + 1 == tries:
                    self.failed += 1
                    log.error('Не удалось отправить сообщение после %s попыток: %s', tries, type(err).__name__)
                    return
                delay = retry_after(err, min(30, 2 ** attempt))
                log.warning('Повтор отправки через %.1f с: %s', delay, type(err).__name__)
                await asyncio.sleep(delay)

    async def join(self):
        queues = list(self.queues.values()) + list(self.acks.values())
        await asyncio.gather(*(queue.join() for queue in queues))

    async def close(self):
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def event_key(event):
    kind = event.get('update_type')
    cb = event.get('callback')
    if kind == 'message_callback' and isinstance(cb, dict) and isinstance(cb.get('callback_id'), str) and cb['callback_id']:
        return 'callback:' + cb['callback_id']
    msg = event.get('message')
    if kind == 'message_created' and isinstance(msg, dict):
        body = msg.get('body')
        if isinstance(body, dict) and isinstance(body.get('mid'), str) and body['mid']:
            return 'message:' + body['mid']
    raw = json.dumps(event, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def event_uid(event):
    kind = event.get('update_type')
    if kind == 'message_created':
        msg = event.get('message')
        user = msg.get('sender') if isinstance(msg, dict) else None
    elif kind == 'message_callback':
        cb = event.get('callback')
        user = cb.get('user') if isinstance(cb, dict) else None
    else:
        user = event.get('user')
    uid = user.get('user_id') if isinstance(user, dict) else None
    return uid if type(uid) is int else None


class Backend:
    def __init__(self, api, ui, out):
        self.api = api
        self.ui = ui
        self.out = out
        self.inbox = asyncio.Queue(maxsize=1000)
        self.seen = OrderedDict()
        self.processed = 0
        self.duplicates = 0
        self.skipped = 0

    async def handle(self, event):
        if not isinstance(event, dict):
            return
        key = event_key(event)
        if key in self.seen:
            self.duplicates += 1
            return
        uid = event_uid(event)
        peers = self.ui.app.peers(uid)
        if any(not self.out.ready(player) for player in peers):
            self.skipped += 1
            return
        started = time.monotonic()
        try:
            jobs = await asyncio.to_thread(prepare, self.ui, event)
        except sqlite3.Error as err:
            log.error('Ошибка хранилища при обработке события: %s', type(err).__name__)
            jobs = []
            if uid is not None:
                msg = 'Не удалось выполнить действие. Попробуй ещё раз. Если шла игра, нажми «Продолжить».'
                body = await asyncio.to_thread(self.ui.body, uid, msg)
                cb = event.get('callback')
                if isinstance(cb, dict) and isinstance(cb.get('callback_id'), str) and cb['callback_id']:
                    jobs.append(('/answers', {'callback_id': cb['callback_id']}, {'notification': 'Попробуй ещё раз'}))
                jobs.append(('/messages', {'user_id': uid}, body))
        except (TypeError, ValueError, KeyError, IndexError, AttributeError) as err:
            log.warning('Некорректное событие пропущено: %s', type(err).__name__)
            jobs = []
        finally:
            elapsed = time.monotonic() - started
            if elapsed >= 0.5:
                log.warning('Обработка события заняла %.2f с', elapsed)
        self.seen[key] = None
        if len(self.seen) > 10000:
            self.seen.popitem(last=False)
        for job in jobs:
            user = uid if job[0] == '/answers' else job[1]['user_id']
            await self.out.put(user, job)
        self.processed += 1

    async def process(self):
        while True:
            event = await self.inbox.get()
            try:
                await self.handle(event)
            finally:
                self.inbox.task_done()

    async def poll(self):
        marker = None
        wait = 1
        while True:
            try:
                args = {'timeout': 25, 'limit': 100, 'types': 'bot_started,message_created,message_callback'}
                if marker is not None:
                    args['marker'] = marker
                data = await self.api.call('GET', '/updates', params=args)
                events = data.get('updates', [])
                if not isinstance(events, list):
                    raise ValueError('Некорректный список событий')
                for event in events:
                    await self.inbox.put(event)
                marker = data.get('marker', marker)
                wait = 1
                if not events:
                    await asyncio.sleep(0.1)
            except (httpx.HTTPError, ValueError) as err:
                code = err.response.status_code if isinstance(err, httpx.HTTPStatusError) else None
                if code in (401, 409):
                    raise StopBot('Проверь токен, webhook и другие запущенные копии бота.') from None
                delay = retry_after(err, wait)
                log.warning('Приём событий: %s. Повтор через %.1f с', type(err).__name__, delay)
                await asyncio.sleep(delay)
                wait = min(30, wait * 2)
