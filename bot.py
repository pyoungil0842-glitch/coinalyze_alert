import os
import time
import requests

COINALYZE_API_KEY = (os.getenv("COINALYZE_API_KEY") or "").strip()
TELEGRAM_BOT_TOKEN = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
TELEGRAM_CHAT_ID = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()

MAX_LONG_RATIO_1D = 75.0


def send_telegram_msg(text):
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
        print(f"텔레그램 전송 실패: {e}")


def get_active_markets():
    """1. 바이낸스/바이비트 등 실제 거래량이 있는 선물 종목만 선별 (중복/유령종목 제거)"""
    url = "https://api.coinalyze.net/v1/future-markets"
    headers = {"api_key": COINALYZE_API_KEY}
    
    res = requests.get(url, headers=headers, timeout=15).json()
    
    # 롱숏 데이터가 있고, 무기한 선물(Perp)인 실거래 종목만 압축 (약 150~250개 수준)
    valid_symbols = []
    for m in res:
        if isinstance(m, dict):
            sym = m.get("symbol", "")
            # 바이낸스(.A) 또는 바이비트(.6) 등 대표 거래소 위주로 필터 (중복 제거)
            if m.get("has_long_short_ratio_data") and (sym.endswith(".A") or sym.endswith(".6")):
                valid_symbols.append(sym)

    print(f"실거래 대상 종목 압축 완료: 총 {len(valid_symbols)}개")
    return valid_symbols


def fast_scan(symbols):
    """2. 꼭 필요한 API만 최소한으로 호출하여 10~20초 안에 전수 검사"""
    headers = {"api_key": COINALYZE_API_KEY}
    now = int(time.time())
    chunk_size = 20
    matched = []

    for i in range(0, len(symbols), chunk_size):
        chunk = symbols[i:i + chunk_size]
        symbols_str = ",".join(chunk)

        url_oi = "https://api.coinalyze.net/v1/open-interest-history"
        url_lsr = "https://api.coinalyze.net/v1/long-short-ratio-history"

        try:
            # 1H OI (최근 2시간만 초고속 조회)
            r_oi = requests.get(url_oi, params={
                "symbols": symbols_str, "interval": "1hour", "from": now - 7200, "to": now
            }, headers=headers, timeout=10).json()

            # 1H LSR (최근 2시간)
            r_lsr = requests.get(url_lsr, params={
                "symbols": symbols_str, "interval": "1hour", "from": now - 7200, "to": now
            }, headers=headers, timeout=10).json()

            # Daily LSR (최근 1일)
            r_lsr_1d = requests.get(url_lsr, params={
                "symbols": symbols_str, "interval": "daily", "from": now - 86400, "to": now
            }, headers=headers, timeout=10).json()
        except Exception:
            continue

        daily_map = {item.get("symbol"): item.get("history", [])[-1].get("l", 0) 
                     for item in r_lsr_1d if isinstance(item, dict) and item.get("history")}

        lsr_map = {}
        for item in r_lsr:
            if isinstance(item, dict):
                hist = item.get("history", [])
                if len(hist) >= 2:
                    p_l = hist[-2].get("l", 0)
                    c_l = hist[-1].get("l", 0)
                    lsr_map[item.get("symbol")] = {"p_l": p_l, "c_l": c_l, "diff": c_l - p_l}

        for item in r_oi:
            if isinstance(item, dict):
                sym = item.get("symbol")
                hist = item.get("history", [])
                if len(hist) >= 2 and sym in lsr_map and sym in daily_map:
                    p_oi = hist[-2].get("o", 0)
                    c_oi = hist[-1].get("o", 0)
                    if p_oi > 0:
                        oi_change = ((c_oi - p_oi) / p_oi) * 100
                        
                        # [조건 1] OI 증가 / [조건 2] 1H 롱 감소 / [조건 3] 1D 롱 < 75%
                        if oi_change > 0 and lsr_map[sym]["diff"] < 0 and daily_map[sym] < MAX_LONG_RATIO_1D:
                            matched.append({
                                "symbol": sym,
                                "oi_change": oi_change,
                                "prev_l": lsr_map[sym]["p_l"],
                                "curr_l": lsr_map[sym]["c_l"],
                                "diff_l": lsr_map[sym]["diff"],
                                "daily_l": daily_map[sym]
                            })

        time.sleep(0.2)  # 초고속 진행

    matched.sort(key=lambda x: x["oi_change"], reverse=True)
    return matched


if __name__ == "__main__":
    symbols = get_active_markets()
    matched_items = fast_scan(symbols)

    print(f"스캔 완료: 총 {len(matched_items)}개 조건 만족")

    if matched_items:
        lines = [f"🚨 <b>[Coinalyze 실시간 스크리너 포착]</b>\n"]
        lines.append(f"<i>(총 {len(matched_items)}개 만족 / OI 증가율 상위 20개)</i>\n")

        for m in matched_items[:20]:
            clean_sym = m['symbol'].replace(".A", " (바이낸스)").replace(".6", " (바이비트)")
            lines.append(
                f"• <b>{clean_sym}</b>\n"
                f"  - 1H OI: <code>+{m['oi_change']:.2f}%</code>\n"
                f"  - 1H Long%: {m['prev_l']:.1f}% → <b>{m['curr_l']:.1f}%</b> (<code>{m['diff_l']:.1f}%p</code>)\n"
                f"  - 1D Long%: <b>{m['daily_l']:.1f}%</b>\n"
            )
        send_telegram_msg("\n".join(lines))
    else:
        send_telegram_msg("✅ 스캔 완료: 현재 조건을 만족하는 코인이 없습니다.")
