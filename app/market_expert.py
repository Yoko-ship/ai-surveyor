"""
«ИИ специалист» — ветка «рынок» (фасад). Код переехал в пакет app/legal (04.10.2026):
  app/legal/market.py   — распознавание вопроса о рынке, ответы из market_stats, рэнкинга и docs/market_facts.json;
  app/legal/memory.py   — память диалога (последние 8 реплик, 2 часа, только в памяти процесса);
  app/legal/intents.py  — словари распознавания (ru/uz/en).
Прежние имена (detect, resolve, answer, suggest, memory, FACTS_FILE, facts_file …) доступны отсюда как раньше;
присваивание (например, mx.FACTS_FILE в тестах) доходит до app/legal/market.py.
"""
from . import legal as _legal

# имена частей в самом фасаде не держим: иначе mx.memory вернул бы модуль, а не память диалога
_legal._facade.install(__name__, (_legal.market, _legal.memory, _legal.intents))
del _legal
