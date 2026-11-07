from html import escape

LEVELS = {
    'easy': 'Базовый',
    'medium': 'Средний',
    'hard': 'Сложный',
    'all': 'Смешанный'
}

def question(q, pos, total, title, context=''):
    level = LEVELS.get(q.get('difficulty'), 'Смешанный')
    opts = '\n\n'.join(
        f'{i}) {v}' for i, v in enumerate(q['options'], 1)
    )
    info = context + '\n' if context else ''
    head = f'{title} · {pos + 1}/{total}\n{info}Сложность: {level.lower()}'
    topic = q.get('topic', '').strip()
    if topic:
        head += f'\nТема: {topic}'
    return (
        f"{head}\n\n{q['text']}\n\n{opts}\n\n"
        'Выбери ответ кнопкой ниже.'
    )

def render(text):
    marks = ('🏁 ', '📊 ', '⚔ ', '✏ ', '🎯 ', '📖 ')
    if text.startswith('🏅 Достижения'):
        marks += ('✅ ', '🔒 ')
    lines = text.split('\n')
    for i, line in enumerate(lines):
        safe = escape(line, quote=False)
        if line and (i == 0 or line.startswith(marks)):
            safe = f'<b>{safe}</b>'
        lines[i] = safe
    return '\n'.join(lines)