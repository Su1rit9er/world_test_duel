import asyncio
import logging
import os
import socket
import ssl
import sys
from pathlib import Path

from quiz import Quiz
from storage import import_db


def console():
    app = Quiz().app
    print(app.text(0, 'меню')[0].text)
    while True:
        try:
            text = input('> ')
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if text == '/exit':
            break
        for reply in app.text(0, text):
            print(reply.text)


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


async def run_async():
    import httpx
    from dotenv import load_dotenv
    from backend import Backend, Remote, Sender, StopBot
    from max_adapter import Buttons

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
    limits = httpx.Limits(max_connections=20, max_keepalive_connections=12)
    timeout = httpx.Timeout(35, connect=5, write=10, pool=5)
    async with httpx.AsyncClient(base_url='https://platform-api2.max.ru', verify=ctx,
                                 headers={'Authorization': token}, timeout=timeout, limits=limits) as client:
        api = Remote(client)
        try:
            me = await api.call('GET', '/me')
            data = await api.call('GET', '/subscriptions')
        except (httpx.HTTPError, ValueError, RuntimeError) as err:
            raise SystemExit(error(err)) from None
        if data.get('subscriptions'):
            raise SystemExit('У бота включён webhook. Для polling сначала отключи подписку в основном проекте или используй отдельного тестового бота.')
        print('Соединение с MAX и токен проверены.')
        if '--check' in sys.argv:
            return
        quiz = Quiz()
        app = quiz.app
        ui = Buttons(app, me.get('username', ''))
        print('Бот запущен. Для остановки нажми Ctrl+C.')
        try:
            async with asyncio.TaskGroup() as group:
                out = Sender(api, group)
                backend = Backend(api, ui, out)
                group.create_task(backend.poll())
                group.create_task(backend.process())
        except* StopBot as errors:
            raise SystemExit(str(errors.exceptions[0])) from None


def run():
    logging.basicConfig(level=logging.WARNING, format='%(levelname)s: %(message)s')
    asyncio.run(run_async())


if __name__ == '__main__':
    try:
        if '--import-db' in sys.argv:
            if len(sys.argv) != 3 or sys.argv[1] != '--import-db':
                raise SystemExit('Укажи --import-db и путь к исходной базе.')
            import_db(sys.argv[2])
        elif '--console' in sys.argv:
            console()
        else:
            run()
    except KeyboardInterrupt:
        print('\nБот остановлен.')
