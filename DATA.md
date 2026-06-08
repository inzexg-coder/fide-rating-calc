# FIDE Rating Estimator — Data Sources & Methodology

## 📊 Собранные данные (API Collection)

В ходе работы были собраны рейтинги игроков через публичные API:

| Player | Title | Lichess Rapid | Lichess Blitz | Chess.com Rapid | Chess.com Blitz | FIDE Std | FIDE Rapid | FIDE Blitz |
|--------|-------|--------------|--------------|----------------|----------------|---------|-----------|-----------|
| Magnus Carlsen | GM | 2500 | 3153 | 2916 | 3381 | 2841 | 2832 | 2869 |
| Hikaru Nakamura | GM | — | — | 2839 | 3430 | 2792 | 2742 | 2838 |
| Fabiano Caruana | GM | — | — | 2764 | 3217 | 2792 | 2723 | 2781 |
| Eric Rosen | IM | 2534 | 2575 | — | 2252 | 2004 | 2002 | 1893 |

## 📚 Референсные точки (Reference Data)

Использованы исследования:
1. **Lichess Rating Comparison** — thibault/ornicar
   https://lichess.org/blog/WFvLpiQAACMA8e9L/rating-comparison-graph
2. **API data** — собственные запросы к Lichess API, Chess.com API, FIDE ratings
3. **Community data** — эмпирические соответствия

## 🔬 Метод

Кусочно-линейная интерполяция (Piecewise Linear Interpolation) между референсными точками.
Это позволяет более точно отображать нелинейную зависимость между онлайн и офлайн рейтингами.

**Причины нелинейности:**
- Разные пулы игроков (онлайн привлекает больше любителей)
- Разные контроль времени (Rapid на платформах ≠ FIDE Standard)
- Разные формулы расчёта (Glicko vs Elo)
- Разная частота игры и обновления рейтинга

## ⚠️ Ограничения

1. Lichess ограничивает Rapid рейтинг на 2500 для сильнейших игроков
2. FIDE Rapid/Blitz рейтинги — отдельная система, не идентичная Standard
3. Данные собраны в июне 2026 года, рейтинги могут устареть
4. Для точной оценки желательно иметь несколько партий на платформе (>50)
