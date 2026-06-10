<p align="center">
  <img src=".ameni/assets/ameni-logo.svg" alt="Ameni" width="100">
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Ameni_FIDE_Rating_Calculator-v3.0-a78bfa?labelColor=222" alt="FIDE Rating Calculator">
  <img src="https://img.shields.io/badge/python-3.12+-blue?labelColor=222" alt="Python 3.12+">
  <img src="https://img.shields.io/badge/fastapi-0.136+-05998b?logo=fastapi&labelColor=222" alt="FastAPI">
  <img src="https://img.shields.io/badge/license-MIT-lightgrey?labelColor=222" alt="MIT">
</p>

<h1 align="center">Ameni FIDE Rating Calculator</h1>
<h1 align="center">https://amenoke.ru/fide-estimate</h1>


<p align="center">
  Anchor-оценка FIDE-рейтинга по партиям с Lichess и Chess.com.<br>
  Веб-интерфейс + REST API + CLI-агент.
</p>

<p align="center">
  <a href="#алгоритм-оценки">Алгоритм</a> &middot;
  <a href="#anchor-метод">Anchor-метод</a> &middot;
  <a href="#регрессионная-модель">Регрессия</a> &middot;
  <a href="#краудсорсинг">Краудсорсинг</a> &middot;
  <a href="#cli-агент">CLI</a> &middot;
  <a href="#api">API</a> &middot;
  <a href="#связанные-скиллы">Скиллы</a>
</p>

---

## Алгоритм оценки

Оценка FIDE-рейтинга основана на трёхуровневой архитектуре:

1. **Anchor-метод** — при наличии титулованных соперников с известным FIDE-рейтингом
2. **Регрессионная аппроксимация** — при их недостатке (на основе верифицированных данных)
3. **Краудсорсинг** — усреднение смещений других пользователей в том же рейтинговом диапазоне

```
Партии пользователя
       |
       v
Поиск титулованных соперников (GM, IM, FM, CM)
       |
       +---> достаточно anchor'ов (>=2) --> Anchor-метод
       |
       +---> мало anchor'ов -----------> Регрессионная модель
       |
       +---> нет данных ---------------> Краудсорсинг
       |
       v
Финальная оценка FIDE-рейтинга
```

---

## Anchor-метод

### Теоретическая основа

Разница (offset) между рейтингом на платформе (Lichess/Chess.com) и официальным рейтингом FIDE является систематической величиной для данной платформы и временного контроля. Титулованные игроки имеют верифицированный рейтинг FIDE, что позволяет вычислить этот offset.

### Формула оценки

```
FIDE(user) = R(user) + weighted_median(delta_1, ..., delta_n)
```

где:
- `R(user)` — средневзвешенный рейтинг пользователя на платформе
- `delta_i` — скорректированное смещение i-го Anchor'а
- `weighted_median` — взвешенная медиана (устойчива к выбросам)

### Вычисление смещения (Raw Offset)

```
delta_raw = FIDE(opponent) - Rating(opponent)
```

- `delta > 0` — FIDE-рейтинг соперника выше платформенного (сильнее в офлайн-турнирах)
- `delta < 0` — платформенный рейтинг выше FIDE (сильнее онлайн)

### Коррекция смещения (Adjusted Offset)

Сырое смещение корректируется с учётом accuracy (точности) обоих игроков:

```
delta = delta_raw * (1 + (avg_accuracy - 0.5) / 2)
```

Средняя точность `avg_accuracy` нормируется:
- Если известны accuracy обоих: `(acc_user + acc_opponent) / 200`
- Если только пользователя: `acc_user / 100`
- Если только соперника: `acc_opponent / 100`
- Если accuracy недоступен: `0.5`

**Обоснование**: при высокой точности партия более показательна — оба играли в свою силу. Коэффициент коррекции: от 1.0 (avg=0.5) до 1.25 (avg=1.0).

### Расчёт весов (Weighting)

Вес каждого Anchor'а вычисляется как произведение трёх факторов:

| Фактор | Значение | Диапазон | Пояснение |
|--------|----------|----------|-----------|
| accuracy | `max(avg_accuracy, 0.1)` | 0.1–1.0 | Чем выше точность, тем весомее Anchor |
| direct | 1.0 (прямой) / 0.5 (косвенный) | 0.5–1.0 | Прямой: FIDE из профиля соперника |
| titled | 1.0 (титул) / 0.5 (нет титула) | 0.5–1.0 | Титулованный соперник vs нетитулованный |

Итоговый вес: `w = w_acc * w_direct * w_title`, ограничение `w >= 0.01`.

### Взвешенная медиана

Финальное смещение — не среднее, а **взвешенная медиана**:

1. Anchor'ы сортируются по `delta`
2. Вычисляется общий вес `W = sum(w)`
3. Anchor'ы перебираются, накапливая вес, пока `cum_weight >= W/2`
4. `delta` на этом Anchor'е — финальное смещение

**Обоснование**: медиана устойчива к выбросам — ошибочные FIDE-рейтинги в профилях не искажают результат.

---

## Регрессионная модель

При недостатке Anchor'ов (n < 2) используется линейная регрессия:

### Rapid → FIDE Standard

```
FIDE = alpha * Rating(rapid) + beta
```

| Платформа | alpha | beta | R² |
|-----------|-------|------|-----|
| Lichess | 1.0005 | -247.62 | 0.9987 |
| Chess.com | 0.9748 | -185.04 | 0.9992 |

R² > 0.998 указывает на почти линейную зависимость.

### Референтные таблицы

**Lichess Rapid → FIDE:**

| Lichess | FIDE |
|:-------:|:----:|
| 800 | 550 |
| 1000 | 750 |
| 1200 | 950 |
| 1400 | 1150 |
| 1500 | 1250 |
| 1600 | 1350 |
| 1700 | 1450 |
| 1800 | 1550 |
| 1900 | 1650 |
| 2000 | 1770 |
| 2200 | 1980 |
| 2400 | 2150 |
| 2500 | 2200 |

**Chess.com Rapid → FIDE:**

| Chess.com | FIDE |
|:---------:|:----:|
| 800 | 600 |
| 1000 | 800 |
| 1200 | 1000 |
| 1400 | 1200 |
| 1600 | 1380 |
| 1800 | 1540 |
| 2000 | 1730 |
| 2200 | 1950 |
| 2400 | 2160 |
| 2600 | 2360 |
| 2800 | 2550 |
| 3000 | 2750 |

Интерполяция — кусочно-линейная. Экстраполяция — по последним двум точкам.

### Масштабирование по временным контролам

Поправка к рейтингу на других временных контролях:

```
FIDE(tc) = FIDE(rapid) * slope(tc) + intercept(tc)
```

| Платформа | Контроль | slope | intercept |
|-----------|----------|:-----:|:---------:|
| Lichess | bullet | 0.88 | -150 |
| Lichess | blitz | 0.93 | -100 |
| Lichess | rapid | 1.00 | 0 |
| Lichess | classical | 1.02 | +50 |
| Lichess | correspondence | 1.02 | +50 |
| Chess.com | bullet | 0.90 | -120 |
| Chess.com | blitz | 0.95 | -80 |
| Chess.com | rapid | 1.00 | 0 |
| Chess.com | classical | 1.01 | +30 |
| Chess.com | daily | 1.01 | +30 |

**Обоснование**: быстрые контроли (bullet, blitz) имеют большее расхождение с FIDE, т.к. FIDE-рейтинг отражает игру в классических турнирах.

---

## Краудсорсинг

Система накапливает смещения в `cache/crowd_offsets.json`. Данные организованы по ключу:

```
{time_class}:{rating_bracket}
```

### Кластеризация

Рейтинг разбивается на корзины шириной 100 пунктов:

```
B(R) = [floor(R/100) * 100, floor(R/100) * 100 + 99]
```

### Использование

Когда для пользователя мало прямых Anchor'ов:

1. Определяется текущий рейтинг `R(user)` и временной контроль
2. Вычисляется корзина `B(R)`
3. Если корзина пуста — проверяются соседние: B ± 100, B ± 200
4. Вычисляется средневзвешенный offset
5. Создаётся синтетический Anchor с весом 0.3 (низкая уверенность)

---

## CLI-агент

CLI входит в состав репозитория, не требует установки доп. зависимостей — только Python 3.

```bash
export PATH="$PWD/.ameni/bin:$PATH"

ameni fide estimate magnuscarlsen       # оценка FIDE
ameni fide rating hikaru --chesscom     # только рейтинг
ameni fide anchors fabianocaruana      # список якорей
ameni fide daily levy                   # дневная динамика
ameni fide check drnkat                 # всё сразу
ameni fide help                         # справка
```

### Команды

| Команда | Описание |
|---------|----------|
| `estimate <user>` | Полная оценка FIDE-рейтинга |
| `rating <user>` | Итоговый рейтинг одной строкой |
| `anchors <user>` | Таблица якорей (титулованные соперники) |
| `daily <user>` | Дневная динамика |
| `check <user>` | estimate + anchors |
| `about` | Информация об агенте |
| `help` | Полный мануал |

### Опции

| Опция | Описание |
|-------|----------|
| `--platform, -p` | `lichess` (по умолч.) или `chesscom` |
| `--chesscom` | Сокращение для `--platform chesscom` |
| `--host URL` | API-хост (по умолч. `https://amenoke.ru`) |

---

## API

Бэкенд предоставляет REST API.

### Health Check

```
GET /api/health
-> {"status": "ok", "version": "3.0"}
```

### Оценка рейтинга

```
POST /api/estimate
Content-Type: application/json
Body: {"platform": "lichess", "username": "magnuscarlsen"}

-> {
    "final_estimate": 2760,
    "confidence": 0.87,
    "num_anchors": 12,
    "time_controls": [...],
    "anchors": [...]
  }
```

### SSE (Server-Sent Events)

```
GET /api/estimate/stream?platform=lichess&username=magnuscarlsen

event: progress
data: {"step": "fetch", "message": "Загрузка...", "percent": 10}

event: result
data: { ... }
```

### Клиентские партии (для Lichess, если заблокирован с сервера)

```
POST /api/estimate/games
Body: {"platform": "lichess", "username": "user", "games": [...]}
```

### Модели данных

**GameData:**

| Поле | Тип | Описание |
|------|-----|----------|
| game_id | string | ID партии |
| date | string | ISO-дата |
| speed | string | bullet/blitz/rapid/classical |
| time_class | string | bullet/blitz/rapid/classical |
| user_rating | int | Рейтинг пользователя |
| opponent | string | Имя соперника |
| opponent_rating | int | Рейтинг соперника |
| opponent_title | string | Титул (GM/IM/FM/...) |
| user_accuracy | float | Точность пользователя (0–100) |
| opponent_accuracy | float | Точность соперника (0–100) |
| user_color | string | "white"/"black" |
| result | string | Результат |
| platform | string | "lichess"/"chesscom" |

---


## Архитектура

```
fide-rating-calc/
  .ameni/
    assets/          # Логотипы
    bin/ameni        # CLI-диспетчер (bash)
    lib/fide.py      # CLI-логика (Python)
  backend/
    main.py          # FastAPI, роутинг
    estimator.py     # Anchor-метод + краудсорсинг
    fetchers.py      # Загрузка партий (Lichess, Chess.com)
    fide_titles.py   # Справочник титулов
    regression.py    # Регрессионная модель
  cache/
    crowd_offsets.json
  frontend/index.html  # Веб-интерфейс
  fide-app.service     # systemd-юнит
```

### Компоненты

| Компонент | Технология |
|-----------|-----------|
| Бэкенд | Python / FastAPI |
| Фронтенд | HTML / JS / Chart.js |
| CLI | Bash + Python3 |
| Сервер | Uvicorn + Nginx |

---

## FIDE Title Reference

| Титул | Описание |
|-------|----------|
| GM | Grandmaster |
| IM | International Master |
| FM | FIDE Master |
| CM | Candidate Master |
| WGM | Woman Grandmaster |
| WIM | Woman International Master |
| WFM | Woman FIDE Master |
| WCM | Woman Candidate Master |
| NM | National Master |

---

<p align="center">
  <img src=".ameni/assets/ameni-logo.svg" alt="Ameni" width="32">
  <br>
  <a href="https://github.com/inzexg-coder">@inzexg-coder</a>
  <br>
</p>
