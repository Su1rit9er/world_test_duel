import os
import sys
import time
from pathlib import Path

from quiz import Quiz
import httpx
from dotenv import load_dotenv


def console():
    quiz = Quiz()
    print(quiz.reply(1, '/start'))
    while True:
        try:
            text = input('> ')
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if text == '/exit':
            break
        print(quiz.reply(1, text))


def run():
    load_dotenv(Path(__file__).with_name('.env'))
    token = os.getenv('MAX_BOT_TOKEN', '').strip()
    if not token:
        raise SystemExit('Укажи MAX_BOT_TOKEN в .env или запусти main.py --console.')
    quiz = Quiz()
    marker = None
    with httpx.Client(base_url='https://platform-api2.max.ru',
                      headers={'Authorization': token}, timeout=40) as api:
        print('Бот запущен. Для остановки нажми Ctrl+C.')
        while True:
            args = {'timeout': 30, 'types': 'bot_started,message_created'}
            if marker is not None:
                args['marker'] = marker
            try:
                res = api.get('/updates', params=args)
                res.raise_for_status()
                data = res.json()
                for event in data.get('updates', []):
                    kind = event.get('update_type')
                    if kind == 'bot_started':
                        user = event.get('user', {})
                        text = '/start'
                    elif kind == 'message_created':
                        msg = event.get('message', {})
                        if msg.get('recipient', {}).get('chat_type') != 'dialog':
                            continue
                        user = msg.get('sender', {})
                        text = msg.get('body', {}).get('text') or ''
                    else:
                        continue
                    uid = user.get('user_id')
                    if uid is None or user.get('is_bot'):
                        continue
                    reply = quiz.reply(uid, text)
                    res = api.post('/messages', params={'user_id': uid}, json={'text': reply})
                    res.raise_for_status()
                    if res.json().get('success') is False:
                        raise RuntimeError('Не удалось отправить сообщение')
                marker = data.get('marker', marker)
            except (httpx.HTTPError, ValueError, RuntimeError) as err:
                print(f'Ошибка связи: {type(err).__name__}. Повтор через 3 секунды.')
                time.sleep(3)


if __name__ == '__main__':
    try:
        if '--console' in sys.argv:
            console()
        else:
            run()
    except KeyboardInterrupt:
        print('\nБот остановлен.')
