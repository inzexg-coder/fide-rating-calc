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

<p align="center">
  Anchor-оценка FIDE-рейтинга по партиям с Lichess и Chess.com.<br>
  Веб-интерфейс + REST API + CLI-агент.
</p>

<p align="center">
  <a href="#алгоритм-оценки">Алгоритм оценки</a> &middot;
  <a href="#anchor-метод">Anchor-метод</a> &middot;
  <a href="#регрессионная-модель">Регрессионная модель</a> &middot;
  <a href="#краудсорсинг">Краудсорсинг</a> &middot;
  <a href="#cli-агент">CLI-агент</a> &middot;
  <a href="#api">API</a> &middot;
  <a href="#архитектура">Архитектура</a>
</p>

---

## Алгоритм оценки

Оценка FIDE-рейтинга основана на **трёхуровневой архитектуре**: при наличии достаточного количества титулованных соперников с известным FIDE-рейтингом используется прямой Anchor-метод; при их недостатке — регрессионная аппроксимация на основе верифицированных данных курсовой работы; в отсутствие обоих — краудсорсинговое усреднение смещений других пользователей в том же рейтинговом диапазоне.

```
                        ┌─────────────────────────┐
                        │  Загрузка партий         │
                        │  (Lichess / Chess.com)   │
                        │  до 200 на каждый ТК     │
                        └───────────┬─────────────┘
                                    ▼
                     ┌───────────────────────────┐
                     │  Поиск титулованных        │
                     │  соперников (GM,IM,FM,CM)  │
                     │  с известным FIDE-рейтингом│
                     └──────┬────────────┬───────┘
                            ▼            ▼
              ┌─────────────────┐  ┌─────────────────┐
              │ ≥2 Anchor'ов   │  │ <2 Anchor'ов    │
              │ Anchor-метод   │  │ Регрессия       │
              └────────┬───────┘  └────────┬────────┘
                       ▼                   ▼
              ┌─────────────────────────────────────┐
              │  Финальная оценка FIDE-рейтинга      │
              │  с weighting и confidence interval   │
              └─────────────────────────────────────┘
```

---

## Anchor-метод

### Теоретическая основа

Anchor-метод базируется на предположении, что разница (offset) между рейтингом игрока на платформе (Lichess/Chess.com) и его официальным рейтингом FIDE является систематической величиной для данной платформы и временного контроля. Титулованные игроки (GM, IM, FM, CM) имеют верифицированный рейтинг FIDE, доступный через API платформ, что позволяет вычислить этот offset.

Для пользователя `u` оценка FIDE-рейтинга производится по формуле:

$$FIDE_u = R_u + \frac{\sum_{i=1}^{n} w_i \cdot \delta_i}{\sum_{i=1}^{n} w_i}$$

где:
- $R_u$ — средневзвешенный рейтинг пользователя на платформе
- $\delta_i$ — скорректированное смещение $i$-го Anchor'а
- $w_i$ — вес $i$-го Anchor'а
- $n$ — количество Anchor'ов

### Вычисление смещения (Raw Offset)

Для каждого титулованного соперника $i$ с известным FIDE-рейтингом $F_i$ и рейтингом на платформе $P_i$ вычисляется сырое смещение:

$$\delta_{raw,i} = F_i - P_i$$

Положительное смещение означает, что FIDE-рейтинг соперника выше его платформенного (игрок сильнее в официальных турнирах). Отрицательное — рейтинг на платформе выше FIDE (игрок сильнее онлайн).

### Коррекция смещения (Adjusted Offset)

Сырое смещение корректируется с учётом качества партии, определяемого через accuracy (точность) обоих игроков:

$$\delta_i = \delta_{raw,i} \cdot \left(1 + \frac{a_{avg} - 0.5}{2}\right)$$

где $a_{avg}$ — нормированная средняя точность партии:

$$a_{avg} = \begin{cases}
\frac{acc_u + acc_o}{200}, & \text{если известны оба показателя} \\[6pt]
\frac{acc_u}{100}, & \text{если известен только accuracy пользователя} \\[6pt]
\frac{acc_o}{100}, & \text{если известен только accuracy соперника} \\[6pt]
0.5, & \text{если accuracy недоступен}
\end{cases}$$

**Обоснование**: при высокой точности (близкой к 100%) партия считается более показательной — оба игрока играли в свою силу, и смещение более репрезентативно. Коэффициент коррекции варьируется от 1.0 (при $a_{avg}=0.5$) до 1.25 (при $a_{avg}=1.0$).

### Расчёт весов (Weighting)

Вес каждого Anchor'а $w_i$ вычисляется как произведение трёх факторов:

$$w_i = w_{acc} \cdot w_{direct} \cdot w_{title}$$

| Фактор | Формула | Диапазон | Описание |
|--------|---------|----------|----------|
| $w_{acc}$ | $\max(a_{avg}, 0.1)$ | [0.1, 1.0] | Точность: чем выше, тем весомее Anchor |
| $w_{direct}$ | $\begin{cases}1.0, & \text{pryamoi}\\0.5, & \text{tsepevoi}\end{cases}$ | {0.5, 1.0} | Прямой Anchor (FIDE из профиля) vs косвенный |
| $w_{title}$ | $\begin{cases}1.0, & \text{yesli yest titul}\\0.5, & \text{yesli net titula}\end{cases}$ | {0.5, 1.0} | Титулованный соперник vs нетитулованный, но с FIDE |

Итоговый вес $w_i \geq 0.01$ (ограничение снизу для численной устойчивости).

### Усреднение через медиану

Финальное смещение вычисляется не как среднее арифметическое, а как **взвешенная медиана**:

$$\delta_{final} = \text{mediana}\left(\{\delta_1, \delta_2, ..., \delta_n\}, \{w_1, w_2, ..., w_n\}\right)$$

Процедура:
1. Anchor'ы сортируются по $\delta_i$
2. Вычисляется совокупный вес $W = \sum w_i$
3. Anchor'ы перебираются в порядке сортировки, накапливая вес, пока $cum\_weight \geq W/2$
4. Значение $\delta$ на этом Anchor'е является взвешенной медианой

**Обоснование**: медиана устойчива к выбросам — ошибочные FIDE-рейтинги в профилях (игрок мог указать завышенный рейтинг) не искажают результат так сильно, как при использовании среднего арифметического.

---

## Регрессионная модель

При недостаточном количестве Anchor'ов ($n < 2$) используется регрессионная аппроксимация, построенная на верифицированных данных курсовой работы.

### Линейная регрессия (Rapid → Standard)

Для каждого временного контроля «rapid» построена линейная регрессия вида:

$$FIDE = \alpha \cdot R_{rapid} + \beta$$

| Платформа | $\alpha$ | $\beta$ | $R^2$ |
|-----------|---------|---------|-------|
| Lichess | 1.0005 | -247.62 | 0.9987 |
| Chess.com | 0.9748 | -185.04 | 0.9992 |

Высокий коэффициент детерминации ($R^2 > 0.998$) указывает на почти линейную зависимость между рейтингом на платформе и FIDE в диапазоне 800–3000.

### Референтные таблицы

Для интерполяции используются референтные точки:

**Lichess Rapid → FIDE Standard:**

| Рейтинг Lichess | FIDE |
|:---------------:|:----:|
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
| 2100 | 1880 |
| 2200 | 1980 |
| 2300 | 2070 |
| 2400 | 2150 |
| 2500 | 2200 |

**Chess.com Rapid → FIDE Standard:**

| Рейтинг Chess.com | FIDE |
|:-----------------:|:----:|
| 800 | 600 |
| 1000 | 800 |
| 1200 | 1000 |
| 1400 | 1200 |
| 1500 | 1300 |
| 1600 | 1380 |
| 1700 | 1460 |
| 1800 | 1540 |
| 1900 | 1630 |
| 2000 | 1730 |
| 2100 | 1840 |
| 2200 | 1950 |
| 2300 | 2060 |
| 2400 | 2160 |
| 2500 | 2260 |
| 2600 | 2360 |
| 2700 | 2460 |
| 2800 | 2550 |
| 2900 | 2650 |
| 3000 | 2750 |

Интерполяция — кусочно-линейная между референтными точками. Экстраполяция за пределами таблицы — линейная по последним двум точкам.

### Масштабирование по временным контролам

Регрессионная модель построена для «rapid» (наиболее репрезентативный контроль для FIDE Standard). Для других временных контролей применяются эмпирические коэффициенты масштабирования:

$$\delta_{tc} = f(R_{tc}) \cdot s_{tc} + i_{tc}$$

где $f(R_{tc})$ — оценка по rapid-регрессии, $s_{tc}$ — коэффициент наклона, $i_{tc}$ — поправка интерцепта:

| Платформа | Временной контроль | $s_{tc}$ | $i_{tc}$ |
|-----------|-------------------|:--------:|:--------:|
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

**Обоснование**: более быстрые контроли (bullet, blitz) имеют большее расхождение с FIDE, так как FIDE-рейтинг отражает игру в классических турнирах. Коэффициенты получены из кросс-референтного анализа распределений FIDE и платформенных рейтингов.

---

## Краудсорсинг

Система накапливает вычисленные смещения в `cache/crowd_offsets.json`. Данные организованы по ключу `{time_class}:{rating_bracket}`:

### Кластеризация по рейтинговым корзинам

Рейтинговый диапазон разбивается на корзины шириной 100 пунктов:

$$B(R) = \left[ \left\lfloor \frac{R}{100} \right\rfloor \cdot 100, \; \left\lfloor \frac{R}{100} \right\rfloor \cdot 100 + 99 \right]$$

Для каждой корзины хранятся все вычисленные offset'ы с весами.

### Использование краудсорсинга

Когда для пользователя недостаточно прямых Anchor'ов:

1. Определяется текущий рейтинг пользователя $R_u$ и его временной контроль
2. Вычисляется $B(R_u)$
3. Загружаются offset'ы из соответствующей корзины
4. Если корзина пуста — проверяются соседние: $B \pm 100$, $B \pm 200$
5. Вычисляется средневзвешенный offset:

$$\delta_{crowd} = \frac{\sum_{j} \delta_j \cdot w_j}{\sum_{j} w_j}$$

6. Создаётся синтетический Anchor с весом $w_{crowd} = 0.3$ (низкая уверенность)

---

## CLI-агент

CLI-агент входит в состав репозитория и не требует установки дополнительных зависимостей — только `python3`.

### Установка

```bash
git clone https://github.com/inzexg-coder/fide-rating-calc.git
cd fide-rating-calc
export PATH="$PWD/.ameni/bin:$PATH"
```

### Команды

```
ameni fide estimate <username>          Оценка FIDE-рейтинга
ameni fide rating <username>            Итоговый рейтинг одной строкой
ameni fide anchors <username>           Список якорей (таблица)
ameni fide daily <username>             Дневная динамика
ameni fide check <username>             Полная информация (estimate + anchors)
ameni fide about                        Информация об агенте
ameni fide help                         Полный мануал
```

### Опции

| Опция | Описание |
|-------|----------|
| `--platform, -p` | Платформа: `lichess` (по умолч.) или `chesscom` |
| `--chesscom` | Сокращение для `--platform chesscom` |
| `--host URL` | API-хост (по умолч. `https://amenoke.ru`) |

### Примеры

```bash
ameni fide estimate magnuscarlsen
ameni fide estimate hikaru --chesscom
ameni fide rating drnkat --chesscom
ameni fide anchors fabianocaruana --platform chesscom
ameni fide check levy --host http://127.0.0.1:8200
```

### Пример вывода

```
=== FIDE Rating Estimate ===

  Игрок:       magnuscarlsen
  Платформа:   lichess
  API:         https://amenoke.ru/api/

  Временной контроль     Рейтинг      FIDE    Точн.
  ────────────────────────────────────────────
  blitz                    2830       2730    94.2
  rapid                    2765       2720    96.1
  classical                2850       2830    97.8

  Итоговая оценка:  2760 ± 45
  Якорей:           12
```

---

## API

Бэкенд предоставляет REST API для интеграции.

### Health Check

```
GET /api/health

→ {"status": "ok", "version": "3.0"}
```

### Оценка рейтинга

```
POST /api/estimate
Content-Type: application/json

{
  "platform": "lichess",
  "username": "magnuscarlsen"
}

→ {
    "username": "magnuscarlsen",
    "platform": "lichess",
    "final_estimate": 2760,
    "confidence": 0.87,
    "num_anchors": 12,
    "time_controls": [...],
    "anchors": [...]
  }
```

### Server-Sent Events (SSE)

```
GET /api/estimate/stream?platform=lichess&username=magnuscarlsen

→ event: progress
  data: {"step": "fetch", "message": "Загрузка...", "percent": 10}

  event: result
  data: { ... }
```

### Клиентские партии

```
POST /api/estimate/games
Content-Type: application/json

{
  "platform": "lichess",
  "username": "user",
  "games": [...]
}
```

---

## Архитектура

```
fide-rating-calc/
├── .ameni/                    # CLI-агент
│   ├── assets/
│   │   └── ameni-logo.svg
│   ├── bin/
│   │   └── ameni              # bash-диспетчер
│   └── lib/
│       └── fide.py            # Python-логика CLI
├── backend/                   # FastAPI-бэкенд
│   ├── main.py                # Точка входа, роутинг
│   ├── estimator.py           # Core: Anchor-метод + краудсорсинг
│   ├── fetchers.py            # Загрузка партий (Lichess, Chess.com)
│   ├── fide_client.py         # FIDE API (отключён)
│   ├── fide_titles.py         # Справочник титулов
│   └── regression.py          # Регрессионная модель
├── cache/
│   └── crowd_offsets.json     # Краудсорсинговые offset'ы
├── frontend/
│   └── index.html             # Веб-интерфейс
├── fide-app.service           # systemd-юнит
├── requirements.txt           # Зависимости
├── setup_server.sh            # Скрипт развёртывания
└── README.md
```

### Компоненты

| Компонент | Технология | Назначение |
|-----------|-----------|------------|
| Бэкенд | Python / FastAPI | API, Anchor-анализ, интеграция |
| Фронтенд | HTML / CSS / JS + Chart.js | Веб-интерфейс |
| CLI | Bash + Python3 | Консольный клиент |
| Сервер | Uvicorn + Nginx | Продакшен |

---

## FIDE Title Reference

| Титул | Полное название |
|-------|----------------|
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

## Ссылки

- **Репозиторий**: [github.com/inzexg-coder/fide-rating-calc](https://github.com/inzexg-coder/fide-rating-calc)
- **Продакшен**: [amenoke.ru/fide-estimator](https://amenoke.ru/fide-estimator/)
- **API**: [amenoke.ru/api/health](https://amenoke.ru/api/health)
- **CLI**: `.ameni/bin/ameni fide`

---

<p align="center">
  <img src=".ameni/assets/ameni-logo.svg" alt="Ameni" width="32">
  <br>
  <a href="https://github.com/inzexg-coder">@inzexg-coder</a>
</p>
