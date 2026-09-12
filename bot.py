import os
import time
import requests

COINALYZE_API_KEY = (os.getenv("COINALYZE_API_KEY") or "").strip()
TELEGRAM_BOT_TOKEN = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
TELEGRAM_CHAT_ID = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()

MAX_LONG_RATIO_1D = 75.0


def send_telegram_msg(text):
    """텔레그램 메시지 발송 함수"""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML"
    }
    resp = requests.post(url, json=payload, timeout=10)
    resp.raise_for_status()


def get_market_symbols():
    """1. 활성화된 선물 심볼 목록 가져오기"""
    url = "https://api.coinalyze.net/v1/future-markets"
    headers = {"api_key": COINALYZE_API_KEY}
    res = requests.get(url, headers=headers, timeout=15)
    res.raise_for_status()
    markets = res.json()
    
    # 롱숏 비율 데이터를 제공하는 심볼들만 우선 추출
    symbols = [
        m["symbol"] for m in markets 
        if m.get("symbol") and m.get("has_long_short_ratio_data", True)
    ]
    print(f"1. 대상 심볼 수: {len(symbols)}개")
    return symbols


def scan_markets(symbols):
    """2. OI 및 LSR 히스토리를 20개씩 묶어 조회 후 3가지 조건 동시 검증"""
    headers = {"api_key": COINALYZE_API_KEY}
    now = int(time.time())
    chunk_size = 20
    matched = []

    # 전체 심볼을 20개씩 순회 (시간 관계상 우선 상위 100개 또는 전체 순회)
    target_symbols = symbols[:120]  # GitHub Actions 실행 시간(6시간 제한 중 1~2분 소요) 고려
    
    for i in range(0, len(target_symbols), chunk_size):
        chunk = target_symbols[i:i + chunk_size]
        symbols_str = ",".join(chunk)

        # A. 1시간봉 미체결약정(OI) 히스토리 조회
        url_oi = "https://api.coinalyze.net/v1/open-interest-history"
        params_oi = {
            "symbols": symbols_str,
            "interval": "1hour",
            "from": now - (3600 * 4),
            "to": now
        }
        
        # B. 1시간봉 롱숏비율(LSR) 히스토리 조회
        url_lsr = "https://api.coinalyze.net/v1/long-short-ratio-history"
        params_lsr = {
            "symbols": symbols_str,
            "interval": "1hour",
            "from": now - (3600 * 4),
            "to": now
        }

        # C. 일봉(daily) 롱숏비율 조회 (조건 3용)
        params_lsr_1d = {
            "symbols": symbols_str,
            "interval": "daily",
            "from": now - (86400 * 3),
            "to": now
        }

        try:
            r_oi = requests.get(url_oi, params=params_oi, headers=headers, timeout=15).json()
            r_lsr = requests.get(url_lsr, params=params_lsr, headers=headers, timeout=15).json()
            r_lsr_1d = requests.get(url_lsr, params=params_lsr_1d, headers=headers, timeout=15).json()
        except Exception as e:
            print(f"조회 실패 ({e})")
            continue

        # 1D 롱 비율 매핑
        daily_map = {}
        for item in r_lsr_1d:
            sym = item.get("symbol")
            hist = item.get("history", [])
            if hist:
                daily_map[sym] = hist[-1].get("l", 0)

        # 1H LSR 매핑
        lsr_map = {}
        for item in r_lsr:
            sym = item.get("symbol")
            hist = item.get("history", [])
            if len(hist) >= 2:
                prev_l = hist[-2].get("l", 0)
                curr_l = hist[-1].get("l", 0)
                lsr_map[sym] = {
                    "prev_l": prev_l,
                    "curr_l": curr_l,
                    "diff": curr_l - prev_l
                }

        # OI 비교 및 최종 3대 조건 검증
        for item in r_oi:
            sym = item.get("symbol")
            hist = item.get("history", [])

            if len(hist) >= 2 and sym in lsr_map and sym in daily_map:
                # OI 값 비교 (o: open interest)
                prev_oi = hist[-2].get("o", 0)
                curr_oi = hist[-1].get("o", 0)

                if prev_oi > 0:
                    oi_pchange = ((curr_oi - prev_oi) / prev_oi) * 100

                    # 1. pchange(oi_1h[0,-1]) > 0
                    cond1 = oi_pchange > 0
                    # 2. change(lsr_1h[0,-1]) < 0
                    cond2 = lsr_map[sym]["diff"] < 0
                    # 3. long accounts % (1d) < 75
                    cond3 = daily_map[sym] < MAX_LONG_RATIO_1D

                    if cond1 and cond2 and cond3:
                        matched.append({
                            "symbol": sym,
                            "oi_change": oi_pchange,
                            "curr_oi": curr_oi,
                            "prev_l": lsr_map[sym]["prev_l"],
                            "curr_l": lsr_map[sym]["curr_l"],
                            "diff_l": lsr_map[sym]["diff"],
                            "daily_l": daily_map[sym]
                        })

        time.sleep(0.3)

    # OI 증가율 기준 내림차순 정렬
    matched.sort(key=lambda x: x["oi_change"], reverse=True)
    return matched


if __name__ == "__main__":
    symbols = get_market_symbols()
    matched_items = scan_markets(symbols)

    print(f"최종 조건 만족 종목: {len(matched_items)}개")

    if matched_items:
        lines = [f"🚨 <b>[Coinalyze 스크리너 감지]</b>\n"]
        lines.append(f"<i>(총 {len(matched_items)}개 코인 만족 / 상위 15개 출력)</i>\n")

        for m in matched_items[:15]:
            lines.append(
                f"• <b>{m['symbol']}</b>\n"
                f"  - 1H OI 변동: <code>+{m['oi_change']:.2f}%</code>\n"
                f"  - 1H Long%: {m['prev_l']:.1f}% → <b>{m['curr_l']:.1f}%</b> (<code>{m['diff_l']:.1f}%p</code>)\n"
                f"  - 1D Long%: <b>{m['daily_l']:.1f}%</b> (< 75%)\n"
            )
        send_telegram_msg("\n".join(lines))
        print("텔레그램 전송 완료")
    else:
        send_telegram_msg("✅ 검사 완료: 현재 3가지 조건을 동시에 만족하는 코인이 없습니다.")
