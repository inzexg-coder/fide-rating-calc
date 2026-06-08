/**
 * FIDE Rating Estimator
 * Конвертация рейтингов Lichess и Chess.com в FIDE
 * Метод: кусочно-линейная интерполяция по референсным точкам
 */

// ===========================
// REFERENCE DATA
// ===========================
// Источники:
// 1. Lichess rating comparison study (lichess.org/blog/WFvLpiQAACMA8e9L)
// 2. Собранные данные через API Lichess, Chess.com, FIDE
// 3. Эмпирические данные шахматного сообщества

const LICHESS_RAPID_FIDE = [
    { online: 800,  fide: 550 },   // Начальный уровень
    { online: 1000, fide: 750 },   // Новичок
    { online: 1200, fide: 950 },   // Любитель
    { online: 1400, fide: 1150 },  // Средний клубный
    { online: 1500, fide: 1250 },  // Клубный игрок
    { online: 1600, fide: 1350 },  // Сильный клубный
    { online: 1700, fide: 1450 },  // Турнирный игрок
    { online: 1800, fide: 1550 },  // 1 разряд
    { online: 1900, fide: 1650 },  // КМС
    { online: 2000, fide: 1770 },  // Уровень FM
    { online: 2100, fide: 1880 },  // Уровень IM
    { online: 2200, fide: 1980 },  // Сильный IM
    { online: 2300, fide: 2070 },  // Начальный GM
    { online: 2400, fide: 2150 },  // GM
    { online: 2500, fide: 2200 },  // GM (Lichess cap)
];

const CHESS_RAPID_FIDE = [
    { online: 800,  fide: 600 },
    { online: 1000, fide: 800 },
    { online: 1200, fide: 1000 },
    { online: 1400, fide: 1200 },
    { online: 1500, fide: 1300 },
    { online: 1600, fide: 1380 },
    { online: 1700, fide: 1460 },
    { online: 1800, fide: 1540 },
    { online: 1900, fide: 1630 },
    { online: 2000, fide: 1730 },
    { online: 2100, fide: 1840 },
    { online: 2200, fide: 1950 },
    { online: 2300, fide: 2060 },
    { online: 2400, fide: 2160 },
    { online: 2500, fide: 2260 },
    { online: 2600, fide: 2360 },
    { online: 2700, fide: 2460 },
    { online: 2800, fide: 2550 },
    { online: 2900, fide: 2650 },
    { online: 3000, fide: 2750 },
];

// ===========================
// INTERPOLATION
// ===========================
function interpolate(points, x) {
    const sorted = [...points].sort((a, b) => a.online - b.online);
    
    if (x <= sorted[0].online) return sorted[0].fide;
    if (x >= sorted[sorted.length - 1].online) return sorted[sorted.length - 1].fide;
    
    for (let i = 0; i < sorted.length - 1; i++) {
        const x1 = sorted[i].online, y1 = sorted[i].fide;
        const x2 = sorted[i + 1].online, y2 = sorted[i + 1].fide;
        
        if (x >= x1 && x <= x2) {
            const t = (x - x1) / (x2 - x1);
            return Math.round(y1 + t * (y2 - y1));
        }
    }
    return sorted[sorted.length - 1].fide;
}

// ===========================
// CONFIDENCE INTERVAL
// ===========================
function getConfidence(points, x) {
    // Чем дальше от известных точек, тем ниже уверенность
    const sorted = [...points].sort((a, b) => a.online - b.online);
    
    let minDist = Infinity;
    for (const p of sorted) {
        const dist = Math.abs(x - p.online);
        if (dist < minDist) minDist = dist;
    }
    
    // Расстояние до ближайшей референсной точки
    // До 100 пунктов рейтинга = ±50, до 300 = ±75, до 500 = ±100, больше = ±150
    if (minDist <= 100) return { plus: 50, minus: 50, level: 'high' };
    if (minDist <= 200) return { plus: 60, minus: 60, level: 'good' };
    if (minDist <= 300) return { plus: 75, minus: 75, level: 'moderate' };
    if (minDist <= 500) return { plus: 100, minus: 100, level: 'low' };
    return { plus: 150, minus: 150, level: 'rough' };
}

// ===========================
// MAIN CONVERSION
// ===========================
function convertFromLichess(rapid, blitz) {
    let fideFromRapid = null, fideFromBlitz = null;
    let confRapid = null, confBlitz = null;
    
    if (rapid !== null && rapid !== '' && rapid >= 400) {
        fideFromRapid = interpolate(LICHESS_RAPID_FIDE, parseInt(rapid));
        confRapid = getConfidence(LICHESS_RAPID_FIDE, parseInt(rapid));
    }
    
    if (blitz !== null && blitz !== '' && blitz >= 400) {
        // Blitz conversion - shift down from rapid (blitz ≈ rapid - 100 for same FIDE)
        fideFromBlitz = interpolate(LICHESS_RAPID_FIDE, parseInt(blitz));
        // Apply blitz adjustment (blitz rating is deflated relative to rapid on Lichess)
        const blitzRapidDiff = 50; // FIDE blitz is typically lower than FIDE standard
        fideFromBlitz = Math.round(fideFromBlitz - blitzRapidDiff);
        confBlitz = getConfidence(LICHESS_RAPID_FIDE, parseInt(blitz));
    }
    
    // If both available, average them weighted by confidence
    let fide, conf;
    if (fideFromRapid !== null && fideFromBlitz !== null) {
        const wRapid = confRapid.level === 'high' ? 2 : 1;
        const wBlitz = confBlitz.level === 'high' ? 2 : 1;
        fide = Math.round((fideFromRapid * wRapid + fideFromBlitz * wBlitz) / (wRapid + wBlitz));
        conf = {
            plus: Math.round((confRapid.plus * wRapid + confBlitz.plus * wBlitz) / (wRapid + wBlitz)),
            minus: Math.round((confRapid.minus * wRapid + confBlitz.minus * wBlitz) / (wRapid + wBlitz)),
            level: confRapid.level === 'high' && confBlitz.level === 'high' ? 'high' : 
                   confRapid.level === 'moderate' || confBlitz.level === 'moderate' ? 'moderate' : 'good'
        };
    } else if (fideFromRapid !== null) {
        fide = fideFromRapid;
        conf = confRapid;
    } else if (fideFromBlitz !== null) {
        fide = fideFromBlitz;
        conf = confBlitz;
    }
    
    return { fide, conf, fideFromRapid, fideFromBlitz, confRapid, confBlitz };
}

function convertFromChesscom(rapid, blitz) {
    let fideFromRapid = null, fideFromBlitz = null;
    let confRapid = null, confBlitz = null;
    
    if (rapid !== null && rapid !== '' && rapid >= 400) {
        fideFromRapid = interpolate(CHESS_RAPID_FIDE, parseInt(rapid));
        confRapid = getConfidence(CHESS_RAPID_FIDE, parseInt(rapid));
    }
    
    if (blitz !== null && blitz !== '' && blitz >= 400) {
        fideFromBlitz = interpolate(CHESS_RAPID_FIDE, parseInt(blitz));
        const blitzRapidDiff = 50;
        fideFromBlitz = Math.round(fideFromBlitz - blitzRapidDiff);
        confBlitz = getConfidence(CHESS_RAPID_FIDE, parseInt(blitz));
    }
    
    let fide, conf;
    if (fideFromRapid !== null && fideFromBlitz !== null) {
        const wRapid = confRapid.level === 'high' ? 2 : 1;
        const wBlitz = confBlitz.level === 'high' ? 2 : 1;
        fide = Math.round((fideFromRapid * wRapid + fideFromBlitz * wBlitz) / (wRapid + wBlitz));
        conf = {
            plus: Math.round((confRapid.plus * wRapid + confBlitz.plus * wBlitz) / (wRapid + wBlitz)),
            minus: Math.round((confRapid.minus * wRapid + confBlitz.minus * wBlitz) / (wRapid + wBlitz)),
            level: confRapid.level === 'high' && confBlitz.level === 'high' ? 'high' : 'good'
        };
    } else if (fideFromRapid !== null) {
        fide = fideFromRapid;
        conf = confRapid;
    } else if (fideFromBlitz !== null) {
        fide = fideFromBlitz;
        conf = confBlitz;
    }
    
    return { fide, conf, fideFromRapid, fideFromBlitz, confRapid, confBlitz };
}

// ===========================
// BLEND LICHESS + CHESS.COM
// ===========================
function blendEstimates(li, cc) {
    if (!li.fide && !cc.fide) return null;
    
    let fide, conf;
    
    if (li.fide && cc.fide) {
        // Weighted average
        const wLi = li.fideFromRapid !== null && li.fideFromBlitz !== null ? 2 : 1;
        const wCc = cc.fideFromRapid !== null && cc.fideFromBlitz !== null ? 2 : 1;
        
        fide = Math.round((li.fide * Math.max(wLi, 1.5) + cc.fide * wCc) / (Math.max(wLi, 1.5) + wCc));
        
        const avgPlus = Math.round((li.conf.plus + cc.conf.plus) / 2);
        const avgMinus = Math.round((li.conf.minus + cc.conf.minus) / 2);
        const levels = [li.conf.level, cc.conf.level];
        let level = 'good';
        if (levels.includes('high') && !levels.includes('low') && !levels.includes('rough')) level = 'high';
        if (levels.includes('rough') || levels.includes('low')) level = levels.includes('rough') ? 'rough' : 'low';
        
        conf = { plus: avgPlus, minus: avgMinus, level };
    } else if (li.fide) {
        fide = li.fide;
        conf = li.conf;
    } else {
        fide = cc.fide;
        conf = cc.conf;
    }
    
    return { fide, conf };
}
