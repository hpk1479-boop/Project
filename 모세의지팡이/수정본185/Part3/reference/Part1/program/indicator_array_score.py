"""Original score predicates/weights against array Facts; no frame wrapper."""
import numpy as np
from indicator_score import TREND_MAX_SCORE, TREND_DENOMINATOR

def score_from_facts(f, direction: str):
    """기존 DOUBLEB의 추세 앙상블 조건을 방향별 0~100점으로 계산합니다."""
    if f is None or len(f) < 60:
        return None
    direction = str(direction or "").upper()
    if direction not in {"LONG", "SHORT"}:
        return None

    c = f["c"]
    e10, e50 = f["e10"], f["e50"]
    s20 = f["s20"]
    w17 = f["w17"]
    h50 = f["h50"]
    st = f["st"]
    sar = f["sar"]
    plus, minus, adx = f["plus"], f["minus"], f["adx"]
    lr = f["lr"]
    rv = f["rv"]
    cv = f["cv"]
    macd = f["macd"]
    sig = f["sig"]
    aup = f["aup"]
    adn = f["adn"]
    vip, vim = f["vip"], f["vim"]
    vw = f["vw"]
    bop = f["bop"]
    bullp, bearp = f["bullp"], f["bearp"]
    cmfv = f["cmfv"]
    mfiv = f["mfiv"]
    vol_surge = f["vol_surge"]
    vol_state = f["vol_state"]
    safe = f["safe"]
    chop = f["chop"]
    hv = f["hv"]
    hvma = f["hvma"]
    mss = f["mss"]
    ten = f["ten"]
    kij = f["kij"]
    sa = f["sa"]
    sb = f["sb"]
    cloud_top = f["cloud_top"]
    cloud_bot = f["cloud_bot"]

    i = -1

    def b(x):
        return bool(x[i]) if np.isfinite(x[i]) else False

    if direction == "LONG":
        cond = [
            (e10[i] > e50[i], 1),
            (vol_state[i] == 1, 1),
            (plus[i] > minus[i] and adx[i] >= 25 and plus[i] > 15, 2),
            (h50[i] > h50[-3], 1),
            (rv[i] >= 50, 1),
            (cmfv[i] > 0, 1),
            (mfiv[i] >= 50, 1),
            (macd[i] > 0 and macd[i] > sig[i], 1),
            (s20[i] > s20[-2], 1),
            (c[i] > vw[i], 2),
            (w17[i] > s20[i], 1),
            (b(safe), 1),
            (ten[i] > kij[i] and sa[i-13] > sb[i-13] and c[i] > cloud_top[i], 1),
            (chop[i] < 45, 1),
            (cv[i] > 0, 1),
            (lr[i] > lr[-2], 1),
            (aup[i] > adn[i], 1),
            (vip[i] > vim[i], 1),
            (bop[i] > 0, 1),
            (bullp[i] > bullp[-2], 1),
            (mss[i] != -1, 2),
            (b(st), 2),
            (c[i] > sar[i], 1),
            (b(vol_surge), 2),
            (hv[i] > hvma[i], 1),
        ]
    else:
        cond = [
            (e10[i] < e50[i], 1),
            (vol_state[i] == -1, 1),
            (minus[i] > plus[i] and adx[i] >= 25 and minus[i] > 15, 2),
            (h50[i] < h50[-3], 1),
            (rv[i] <= 50, 1),
            (cmfv[i] < 0, 1),
            (mfiv[i] <= 50, 1),
            (macd[i] < 0 and macd[i] < sig[i], 1),
            (s20[i] < s20[-2], 1),
            (c[i] < vw[i], 2),
            (w17[i] < s20[i], 1),
            (b(safe), 1),
            (ten[i] < kij[i] and sa[i-13] < sb[i-13] and c[i] < cloud_bot[i], 1),
            (chop[i] < 45, 1),
            (cv[i] < 0, 1),
            (lr[i] < lr[-2], 1),
            (adn[i] > aup[i], 1),
            (vim[i] > vip[i], 1),
            (bop[i] < 0, 1),
            (bearp[i] < bearp[-2], 1),
            (mss[i] != 1, 2),
            (not b(st), 2),
            (c[i] < sar[i], 1),
            (b(vol_surge), 2),
            (hv[i] > hvma[i], 1),
        ]

    raw_score = sum(weight for ok, weight in cond if bool(ok))
    return min(TREND_MAX_SCORE, raw_score / TREND_DENOMINATOR * TREND_MAX_SCORE)
