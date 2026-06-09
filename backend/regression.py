"""
Regression-based FIDE estimation from the course work data.
Used as fallback when no anchors are found.

Lichess rapid → FIDE standard:  FIDE = rating × 1.0005 - 247.62  (R²=0.9987)
Chess.com rapid → FIDE standard: FIDE = rating × 0.9748 - 185.04  (R²=0.9992)

Reference points for other time controls derived from cross-reference data.
"""

# Reference mapping tables from course work
# [platform_rating, estimated_fide_rating]
LICHESS_RAPID_REF = [
    [800, 550], [1000, 750], [1200, 950], [1400, 1150],
    [1500, 1250], [1600, 1350], [1700, 1450], [1800, 1550],
    [1900, 1650], [2000, 1770], [2100, 1880], [2200, 1980],
    [2300, 2070], [2400, 2150], [2500, 2200],
]

CHESSCOM_RAPID_REF = [
    [800, 600], [1000, 800], [1200, 1000], [1400, 1200],
    [1500, 1300], [1600, 1380], [1700, 1460], [1800, 1540],
    [1900, 1630], [2000, 1730], [2100, 1840], [2200, 1950],
    [2300, 2060], [2400, 2160], [2500, 2260], [2600, 2360],
    [2700, 2460], [2800, 2550], [2900, 2650], [3000, 2750],
]

# ── Scaling factors for other time controls relative to rapid ──────
# Derived from FIDE distribution means and platform data
# bullet → blitz: scale factor
TC_SCALE = {
    # (platform_tc, fide_cat): (slope_adjustment, intercept_adjustment)
    # These are relative to the rapid formula
    ("lichess", "bullet"): (0.88, -150),
    ("lichess", "blitz"): (0.93, -100),
    ("lichess", "rapid"): (1.0, 0),
    ("lichess", "classical"): (1.02, 50),
    ("lichess", "correspondence"): (1.02, 50),
    ("chesscom", "bullet"): (0.90, -120),
    ("chesscom", "blitz"): (0.95, -80),
    ("chesscom", "rapid"): (1.0, 0),
    ("chesscom", "classical"): (1.01, 30),
    ("chesscom", "daily"): (1.01, 30),
}


def estimate_via_regression(platform: str, time_class: str,
                              fide_category: str, rating: float) -> int:
    """Estimate FIDE rating using regression formula."""
    if not rating or rating <= 0:
        return None

    # Get base formula from rapid reference
    if platform == "lichess":
        ref = LICHESS_RAPID_REF
    elif platform == "chesscom":
        ref = CHESSCOM_RAPID_REF
    else:
        return None

    # Interpolate in the reference table
    if rating <= ref[0][0]:
        # Below table — linear extrapolation from first two points
        x1, y1 = ref[0]
        x2, y2 = ref[1]
        slope = (y2 - y1) / (x2 - x1)
        fide = y1 + slope * (rating - x1)
    elif rating >= ref[-1][0]:
        # Above table — linear extrapolation from last two points
        x1, y1 = ref[-2]
        x2, y2 = ref[-1]
        slope = (y2 - y1) / (x2 - x1)
        fide = y2 + slope * (rating - x2)
    else:
        # Interpolate
        for i in range(len(ref) - 1):
            if ref[i][0] <= rating <= ref[i+1][0]:
                x1, y1 = ref[i]
                x2, y2 = ref[i+1]
                t = (rating - x1) / (x2 - x1)
                fide = y1 + t * (y2 - y1)
                break

    # Apply time control scaling
    key = (platform, time_class)
    scale = TC_SCALE.get(key, (1.0, 0))
    fide = fide * scale[0] + scale[1]

    return max(round(fide), 100)


def estimate_via_formula(platform: str, rating: float) -> int:
    """Direct formula-based estimate (for rapid → standard mostly)."""
    if platform == "lichess":
        fide = rating * 1.0005 - 247.62
    elif platform == "chesscom":
        fide = rating * 0.9748 - 185.04
    else:
        return None
    return max(round(fide), 100)
