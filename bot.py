import os
import time
import requests

COINALYZE_API_KEY = (os.getenv("COINALYZE_API_KEY") or "").strip()
TELEGRAM_BOT_TOKEN = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
TELEGRAM_CHAT_ID = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()

MAX_LONG_RATIO_1D = 75.0


def send_telegram_msg(text):
    """텔레그램 메시지 발송 함수 (글자 수 초과 시 분할 발송)"""
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
        print(f"텔레그램 발송 오류: {e}")


def safe_request(url, params):
    """Rate Limit(429) 발생 시 자동 대기 후 재시도하는 안전 요청 함수"""
    headers = {"api_key": COINALYZE_API_KEY}
    while True:
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=15)
            if resp.status_code == 429:
                # 서버가 대기하라고 지정한 시간 확인 (기본 15초)
                wait_time = int(resp.headers.get("Retry-After", 15))
                print(f"API 한도 도달: {wait_time}초 동안 대기 후 재시도...")
                time.sleep(wait_time + 1)
                continue
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, list):
                return data
            return []
        except Exception as e:
            print(f"요청 중 일시적 오류 발생: {e}, 3초 후 재시도")
            time.sleep(3)


def get_all_symbols():
    """1. 시장의 모든 영구선물 종목 전수 수집"""
    url = "https://api.coinalyze.net/v1/future-markets"
    data = safe_request(url, {})
    
    symbols = []
    for m in data:
        if isinstance(m, dict):
            sym = m.get("symbol")
            has_ls = m.get("has_long_short_ratio_data", False)
            if sym and has_ls:
                symbols.append(sym)
                
    print(f"전수 조사 대상 심볼: 총 {len(symbols)}개")
    return symbols


def scan_all_markets(symbols):
    """2. 20개씩 묶어 시장의 모든 종목을 끝까지 전수 검사"""
    now = int(time.time())
    chunk_size = 20
    matched = []
    total_chunks = (len(symbols) + chunk_size - 1) // chunk_size

    url_oi = "https://api.coinalyze.net/v1/open-interest-history"
    url_lsr = "https://api.coinalyze.net/v1/long-short-ratio-history"

    for idx, i in enumerate(range(0, len(symbols), chunk_size)):
        chunk = symbols[i:i + chunk_size]
        symbols_str = ",".join(chunk)

        print(f"진행 중: [{idx + 1}/{total_chunks}] ({len(chunk)}개 심볼 검사)")

        # 1. 1H OI 히스토리
        r_oi = safe_request(url_oi, {
            "symbols": symbols_str,
            "interval": "1hour",
            "from": now - 14400,
            "to": now
        })

        # 2. 1H LSR 히스토리
        r_lsr = safe_request(url_lsr, {
            "symbols": symbols_str,
            "interval": "1hour",
            "from": now - 14400,
            "to": now
        })

        # 3. Daily LSR 히스토리
        r_lsr_1d = safe_request(url_lsr, {
            "symbols": symbols_str,
            "interval": "daily",
            "from": now - 259200,
            "to": now
        })

        # 1D 롱 비율 매핑
        daily_map = {}
        for item in r_lsr_1d:
            sym = item.get("symbol")
            hist = item.get("history", [])
            if hist and isinstance(hist[-1], dict):
                daily_map[sym] = hist[-1].get("l", 0)

        # 1H LSR 매핑
        lsr_map = {}
        for item in r_lsr:
            sym = item.get("symbol")
            hist = item.get("history", [])
            if len(hist) >= 2 and isinstance(hist[-1], dict) and isinstance(hist[-2], dict):
                prev_l = hist[-2].get("l", 0)
                curr_l = hist[-1].get("l", 0)
                lsr_map[sym] = {
                    "prev_l": prev_l,
                    "curr_l": curr_l,
                    "diff": curr_l - prev_l
                }

        # OI 비교 및 3가지 조건 검사
        for item in r_oi:
            sym = item.get("symbol")
            hist = item.get("history", [])

            if len(hist) >= 2 and sym in lsr_map and sym in daily_map:
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
                            "prev_l": lsr_map[sym]["prev_l"],
                            "curr_l": lsr_map[sym]["curr_l"],
                            "diff_l": lsr_map[sym]["diff"],
                            "daily_l": daily_map[sym]
                        })

        # API 호출 속도 조절 (분당 40회 제한 준수용 기본 딜레이)
        time.sleep(1.0)

    # 1H OI 증가율 순으로 내림차순 정렬
    matched.sort(key=lambda x: x["oi_change"], reverse=True)
    return matched


if __name__ == "__main__":
    symbols = get_all_symbols()
    matched_items = scan_all_markets(symbols)

    print(f"전수 조사 완료! 최종 조건 만족 종목: {len(matched_items)}개")

    if matched_items:
        # 텔레그램 한 번에 전송 가능한 길이(4096자)를 고려해 20개씩 묶어 발송
        batch_size = 20
        for b_idx in range(0, len(matched_items), batch_size):
            sub_list = matched_items[b_idx:b_idx + batch_size]
            lines = [f"🚨 <b>[Coinalyze 전수 검사 결과 ({b_idx + 1}~{b_idx + len(sub_list)}/{len(matched_items)})]</b>\n"]
            lines.append("<i>(조건: 1H OI증가 + 1H Long감소 + 1D Long<75%)</i>\n")

            for m in sub_list:
                lines.append(
                    f"• <b>{m['symbol']}</b>\n"
                    f"  - 1H OI 변동: <code>+{m['oi_change']:.2f}%</code>\n"
                    f"  - 1H Long%: {m['prev_l']:.1f}% → <b>{m['curr_l']:.1f}%</b> (<code>{m['diff_l']:.1f}%p</code>)\n"
                    f"  - 1D Long%: <b>{m['daily_l']:.1f}%</b> (< 75%)\n"
                )
            send_telegram_msg("\n".join(lines))
            time.sleep(0.5)

        print("모든 조건 만족 종목 텔레그램 발송 완료!")
    else:
        send_telegram_msg("✅ 전수 검사 완료: 현재 해당 3가지 조건을 만족하는 코인이 시장 전체에 없습니다.")
