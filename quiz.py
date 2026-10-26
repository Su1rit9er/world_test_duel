import json
import random
from pathlib import Path


class Quiz:
    def __init__(self):
        path = Path(__file__).with_name('questions.json')
        self.bank = json.loads(path.read_text(encoding='utf-8'))
        self.games = {}

    def question(self, uid):
        game = self.games[uid]
        q = game['items'][game['pos']]
        opts = '\n'.join(f'{i}. {v}' for i, v in enumerate(q['options'], 1))
        return f"Вопрос {game['pos'] + 1} из 5\n\n{q['text']}\n\n{opts}\n\nОтправь цифру от 1 до 4."

    def reply(self, uid, text):
        text = text.strip()
        if text in ('/start', '/help'):
            return 'Привет! Это Дуэль знаний.\nПока доступна тренировка по математике за 5 класс.\n/quiz — начать тест\n/stop — закончить тест'
        if text == '/stop':
            self.games.pop(uid, None)
            return 'Тест остановлен. Новый тест: /quiz'
        if text == '/quiz':
            self.games[uid] = {'items': random.sample(self.bank, 5), 'pos': 0, 'score': 0}
            return self.question(uid)
        if uid not in self.games:
            return 'Чтобы начать тест, отправь /quiz.'
        if text not in ('1', '2', '3', '4'):
            return 'Отправь цифру от 1 до 4 или /stop.'
        game = self.games[uid]
        q = game['items'][game['pos']]
        ok = int(text) - 1 == q['answer']
        game['score'] += int(ok)
        msg = 'Верно!' if ok else f"Неверно. Правильный ответ: {q['options'][q['answer']]}"
        msg += '\n' + q['explanation']
        game['pos'] += 1
        if game['pos'] == 5:
            msg += f"\n\nРезультат: {game['score']} из 5.\nЕщё раз: /quiz"
            del self.games[uid]
            return msg
        return msg + '\n\n' + self.question(uid)
