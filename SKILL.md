---
name: fide-rating-calc
description: "FIDE chess rating estimation using the Anchor method. Use when the task involves estimating a chess player's FIDE rating based on online games from Lichess or Chess.com, calculating rating offsets between platforms and FIDE, analyzing titled opponents (GM/IM/FM/CM) as anchors, regression-based approximation, or crowd-sourced offset averaging. Also use for setting up the FastAPI backend, running the CLI agent, or deploying the Amenoke FIDE Rating service."
---

# FIDE Rating Calculator Skill

This repo is an **Anchor-method FIDE rating estimator**. It estimates a chess player's FIDE rating from their online games (Lichess / Chess.com).

## Architecture

```
fide-rating-calc/
├── backend/            # FastAPI сервер
│   ├── main.py         # FastAPI app + роутинг
│   ├── estimator.py    # Anchor-метод + краудсорсинг
│   ├── fetchers.py     # Загрузка партий (Lichess, Chess.com API)
│   ├── fide_titles.py  # Справочник FIDE-титулов
│   ├── regression.py   # Регрессионная аппроксимация
│   └── fide_client.py  # FIDE API client
├── frontend/index.html # Веб-интерфейс (Chart.js)
├── cache/              # Краудсорсинговые смещения
├── .ameni/             # CLI агент
│   ├── bin/ameni       # Bash-диспетчер
│   └── lib/fide.py     # CLI логика
├── requirements.txt    # Python зависимости
└── SKILL.md            # Этот файл
```

## Workflows

### Запуск бэкенда

```bash
cd /path/to/fide-rating-calc
pip install -r requirements.txt
uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

Доступен по `http://localhost:8000`, документация API — `/docs`.

### CLI агент

```bash
./.ameni/bin/ameni estimate <username>       # Оценка FIDE-рейтинга
./.ameni/bin/ameni rating <username>          # Только итоговый рейтинг
./.ameni/bin/ameni anchors <username>         # Список Anchor-соперников
./.ameni/bin/ameni check <username>           # estimate + anchors
```

Опции: `--platform lichess|chesscom`, `--host URL`.

### REST API

| Endpoint | Method | Описание |
|---|---|---|
| `/api/health` | GET | Health check |
| `/api/estimate` | POST | Оценка рейтинга |
| `/api/estimate/stream` | GET | SSE-поток прогресса |
| `/api/estimate/games` | POST | Оценка по переданным партиям |

## Алгоритм (Anchor-метод)

Трёхуровневая архитектура:

1. **Anchor-метод** — поиск титулованных соперников (GM/IM/FM/CM) с известным FIDE-рейтингом
2. **Регрессионная модель** — при недостатке Anchor'ов (< 2)
3. **Краудсорсинг** — усреднение смещений других пользователей в диапазоне

Формула: `FIDE(user) = R(user) + weighted_median(delta_1, ..., delta_n)`, где `delta = FIDE(opponent) - Rating(opponent)`.

## When to Use

- Загружать этот скилл при работе с FIDE-рейтингами, шахматными онлайн-платформами, или оценкой силы игрока
- Использовать `references/` при детальном разборе алгоритма
- Вызывать CLI для быстрых оценок, API для интеграций
