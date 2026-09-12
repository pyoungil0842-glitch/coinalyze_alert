import os
import time
import requests

COINALYZE_API_KEY = (os.getenv("COINALYZE_API_KEY") or "").strip()
TELEGRAM_BOT_TOKEN = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
TELEGRAM_CHAT_ID = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()

MAX_LONG_RATIO_1D = 75.0


def send_telegram_msg(text):
    """텔레그램 메시지 전송"""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML"
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
    except Exception as e:
        print(f"텔레그램 전송 오류: {e}")


def get_screener_symbols():
    """1. 웹 스크리너와 동일한 통합/주요 선물 심볼 목록 추출"""
    url = "https://api.coinalyze.net/v1/future-markets"
    headers = {"api_key": COINALYZE_API_KEY}
    
    res = requests.get(url, headers=headers, timeout=15).json()
    
    symbols = []
    for m in res:
        if isinstance(m, dict):
            sym = m.get("symbol", "")
            # 롱숏 데이터가 지원되는 종목 수집
            if m.get("has_long_short_ratio_data") and sym:
                symbols.append(sym)
                
    print(f"1. 검사 대상 심볼: {len(symbols)}개")
    return symbols


def scan_markets(symbols):
    """2. 웹 스크리너 수식 검증 (1H OI 증가, 1H Long 감소, 1D Long < 75%)"""
    headers = {"api_key": COINALYZE_API_KEY}
    now = int(time.time())
    chunk_size = 20
    matched = []

    # API 쿼터 안전 범위 내에서 순차 스캔 (상위 80개 종목 우선 스캔)
    target_symbols = symbols[:80]

    for i in range(0, len(target_symbols), chunk_size):
        chunk = target_symbols[i:i + chunk_size]
        symbols_str = ",".join(chunk)

        # 캔들이 누락되지 않도록 최근 12시간 넉넉히 요청
        from_1h = now - 43200
        from_1d = now - 259200

        try:
            r_oi = requests.get(
                "https://api.coinalyze.net/v1/open-interest-history",
                params={"symbols": symbols_str, "interval": "1hour", "from": from_1h, "to": now},
                headers=headers, timeout=12
            ).json()

            r_lsr = requests.get(
                "https://api.coinalyze.net/v1/long-short-ratio-history",
                params={"symbols": symbols_str, "interval": "1hour", "from": from_1h, "to": now},
                headers=headers, timeout=12
            ).json()

            r_1d = requests.get(
                "https://api.coinalyze.net/v1/long-short-ratio-history",
                params={"symbols": symbols_str, "interval": "daily", "from": from_1d, "to": now},
                headers=headers, timeout=12
            ).json()
        except Exception as e:
            print(f"데이터 수신 실패 ({e})")
            continue

        if not isinstance(r_oi, list): r_oi = []
        if not isinstance(r_lsr, list): r_lsr = []
        if not isinstance(r_1d, list): r_1d = []

        # 1D Long% 매핑
        daily_map = {}
        for item in r_1d:
            if isinstance(item, dict):
                sym = item.get("symbol")
                hist = item.get("history", [])
                if hist:
                    daily_map[sym] = hist[-1].get("l", 0)

        # 1H LSR 매핑
        lsr_map = {}
        for item in r_lsr:
            if isinstance(item, dict):
                sym = item.get("symbol")
                hist = item.get("history", [])
                if len(hist) >= 2:
                    p_l = hist[-2].get("l", 0)
                    c_l = hist[-1].get("l", 0)
                    lsr_map[sym] = {"p_l": p_l, "c_l": c_l, "diff": c_l - p_l}

        # OI 비교 및 3조건 검사
        for item in r_oi:
            if isinstance(item, dict):
                sym = item.get("symbol")
                hist = item.get("history", [])
                if len(hist) >= 2 and sym in lsr_map and sym in daily_map:
                    p_oi = hist[-2].get("o", 0)
                    c_oi = hist[-1].get("o", 0)

                    if p_oi > 0:
                        oi_chg = ((c_oi - p_oi) / p_oi) * 100

                        # [1] OI 증가 / [2] Long 감소 / [3] 1D Long < 75%
                        if oi_chg > 0 and lsr_map[sym]["diff"] < 0 and daily_map[sym] < MAX_LONG_RATIO_1D:
                            matched.append({
                                "symbol": sym,
                                "oi_change": oi_chg,
                                "prev_l": lsr_map[sym]["p_l"],
                                "curr_l": lsr_map[sym]["c_l"],
                                "diff_l": lsr_map[sym]["diff"],
                                "daily_l": daily_map[sym]
                            })

        time.sleep(0.3)

    matched.sort(key=lambda x: x["oi_change"], reverse=True)
    return matched


if __name__ == "__main__":
    syms = get_screener_symbols()
    
    # REZ 종목이 목록에 있는지 로그로 바로 확인
    rez_symbols = [s for s in syms if "REZ" in s.upper()]
    print(f"발견된 REZ 관련 심볼: {rez_symbols}")

    results = scan_markets(syms)
    print(f"최종 통과 코인 수: {len(results)}개")

    if results:
        lines = ["🚨 <b>[Coinalyze 스크리너 포착]</b>\n"]
        lines.append("<i>(조건: 1H OI증가 + 1H Long감소 + 1D Long<75%)</i>\n")

        for m in results[:15]:
            lines.append(
                f"• <b>{m['symbol']}</b>\n"
                f"  - 1H OI: <code>+{m['oi_change']:.2f}%</code>\n"
                f"  - 1H Long%: {m['prev_l']:.1f}% → <b>{m['curr_l']:.1f}%</b> (<code>{m['diff_l']:.1f}%p</code>)\n"
                f"  - 1D Long%: <b>{m['daily_l']:.1f}%</b>\n"
            )
        send_telegram_msg("\n".join(lines))
    else:
        send_telegram_msg("✅ 검사 완료: 현재 조건을 만족하는 코인이 없습니다.")
