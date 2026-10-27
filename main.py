import os
import socket
import ssl
import sys
import time
from pathlib import Path

from quiz import Quiz


def console():
    quiz = Quiz()
    print(quiz.reply(0, '/start'))
    while True:
        try:
            text = input('> ')
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if text == '/exit':
            break
        print(quiz.reply(0, text))


def error(err):
    import httpx

    if isinstance(err, httpx.HTTPStatusError):
        code = err.response.status_code
        tips = {401: 'Проверь MAX_BOT_TOKEN в .env.',
                403: 'MAX запретил доступ к этому действию.',
                409: 'Проверь webhook-подписки и другие запущенные копии бота.',
                429: 'Слишком много запросов. Подожди немного.'}
        return f'HTTP {code}. ' + tips.get(code, 'MAX вернул ошибку запроса.')
    cause = err
    while cause.__cause__ is not None:
        cause = cause.__cause__
    if isinstance(cause, ssl.SSLCertVerificationError) or 'CERTIFICATE_VERIFY_FAILED' in str(cause):
        return 'Не удалось проверить сертификат MAX. Проверь файл max-ca.pem рядом с main.py.'
    if isinstance(cause, socket.gaierror):
        return 'Не найден адрес MAX. Проверь интернет, DNS и VPN.'
    if isinstance(err, httpx.TimeoutException):
        return 'MAX не ответил вовремя. Проверь соединение.'
    if isinstance(err, httpx.ConnectError):
        return 'Не удалось соединиться с MAX. Проверь интернет, VPN и прокси.'
    return f'Ошибка {type(err).__name__}. Проверь подключение и формат ответа MAX.'


def call(api, method, path, **args):
    res = api.request(method, path, **args)
    res.raise_for_status()
    data = res.json()
    if data.get('success') is False:
        raise RuntimeError('MAX отклонил запрос')
    return data


def run():
    import httpx
    from dotenv import load_dotenv

    root = Path(__file__).resolve().parent
    load_dotenv(root / '.env')
    token = os.getenv('MAX_BOT_TOKEN', '').strip()
    if not token:
        raise SystemExit('Укажи MAX_BOT_TOKEN в .env или запусти main.py --console.')
    ctx = ssl.create_default_context()
    try:
        ctx.load_verify_locations(cafile=str(root / 'max-ca.pem'))
    except (OSError, ssl.SSLError):
        raise SystemExit('Не удалось прочитать max-ca.pem. Восстанови сертификат в папке проекта.')
    with httpx.Client(base_url='https://platform-api2.max.ru', verify=ctx,
                      headers={'Authorization': token}, timeout=40) as api:
        try:
            call(api, 'GET', '/me')
            data = call(api, 'GET', '/subscriptions')
        except (httpx.HTTPError, ValueError, RuntimeError) as err:
            raise SystemExit(error(err)) from None
        if data.get('subscriptions'):
            raise SystemExit('У бота включён webhook. Для polling сначала отключи подписку в основном проекте или используй отдельного тестового бота.')
        print('Соединение с MAX и токен проверены.')
        if '--check' in sys.argv:
            return
        quiz = Quiz()
        marker = None
        batch = []
        pending = None
        print('Бот запущен. Для остановки нажми Ctrl+C.')
        while True:
            try:
                if not batch:
                    args = {'timeout': 30, 'types': 'bot_started,message_created'}
                    if marker is not None:
                        args['marker'] = marker
                    data = call(api, 'GET', '/updates', params=args)
                    batch = data.get('updates', [])
                    marker = data.get('marker', marker)
                while batch:
                    if pending is None:
                        event = batch[0]
                        kind = event.get('update_type')
                        if kind == 'bot_started':
                            user, text = event.get('user', {}), '/start'
                        elif kind == 'message_created':
                            msg = event.get('message', {})
                            if msg.get('recipient', {}).get('chat_type') != 'dialog':
                                batch.pop(0)
                                continue
                            user = msg.get('sender', {})
                            text = msg.get('body', {}).get('text') or ''
                        else:
                            batch.pop(0)
                            continue
                        uid = user.get('user_id')
                        if uid is None or user.get('is_bot'):
                            batch.pop(0)
                            continue
                        pending = (uid, quiz.reply(uid, text))
                    uid, reply = pending
                    call(api, 'POST', '/messages', params={'user_id': uid}, json={'text': reply})
                    pending = None
                    batch.pop(0)
                    time.sleep(0.6)
            except (httpx.HTTPError, ValueError, RuntimeError) as err:
                print(error(err))
                if isinstance(err, httpx.HTTPStatusError):
                    code = err.response.status_code
                    if code in (401, 409):
                        raise SystemExit('Бот остановлен. Исправь указанную причину и запусти снова.')
                    if 400 <= code < 500 and code != 429:
                        if pending is None:
                            raise SystemExit('Бот остановлен из-за ошибки запроса.')
                        pending = None
                        batch.pop(0)
                print('Повтор через 5 секунд.')
                time.sleep(5)


if __name__ == '__main__':
    try:
        if '--console' in sys.argv:
            console()
        else:
            run()
    except KeyboardInterrupt:
        print('\nБот остановлен.')
