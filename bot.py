import os
import time
import requests

COINALYZE_API_KEY = (os.getenv("COINALYZE_API_KEY") or "").strip()
TELEGRAM_BOT_TOKEN = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
TELEGRAM_CHAT_ID = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()

# 필터링 기준값
MAX_LONG_RATIO = 75.0  # 현재 롱 계좌수 비율 최대 한도 (75% 이하)


def send_telegram_msg(text):
    """텔레그램 메시지 발송 공통 함수"""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML"
    }
    resp = requests.post(url, json=payload, timeout=10)
    resp.raise_for_status()


def fetch_future_markets():
    """1. 전체 선물 시장에서 거래대금 제한 없이 1H OI가 증가한 '모든' 종목 추출"""
    url = "https://api.coinalyze.net/v1/future-markets"
    headers = {"api_key": COINALYZE_API_KEY}

    response = requests.get(url, headers=headers, timeout=15)
    response.raise_for_status()
    markets = response.json()

    oi_increasing = []
    for m in markets:
        oi_change = m.get("oi_change_percent_1h")
        has_ls_data = m.get("has_long_short_ratio_data", False)

        # OI가 증가(> 0)하고 롱숏 비율 데이터를 제공하는 종목 모두 수집
        if oi_change is not None and oi_change > 0 and has_ls_data:
            oi_increasing.append(m)

    # 1시간 OI 증가율 높은 순으로 정렬
    oi_increasing.sort(key=lambda x: x.get("oi_change_percent_1h", 0), reverse=True)
    return oi_increasing


def check_long_account_conditions_all(symbols):
    """2. 20개씩 묶어서 끝까지 반복 조회 (전 종목 누락 없이 전수 검사)"""
    if not symbols:
        return {}

    now = int(time.time())
    from_time = now - (3600 * 3)
    url = "https://api.coinalyze.net/v1/long-short-ratio-history"
    headers = {"api_key": COINALYZE_API_KEY}

    ratio_results = {}
    chunk_size = 20  # Coinalyze 1회 최대 요청 허용치

    # symbols 리스트를 20개씩 쪼개어 끝까지 순회합니다.
    for i in range(0, len(symbols), chunk_size):
        chunk = symbols[i:i + chunk_size]
        symbols_param = ",".join(chunk)

        params = {
            "symbols": symbols_param,
            "interval": "1hour",
            "from": from_time,
            "to": now
        }

        try:
            response = requests.get(url, params=params, headers=headers, timeout=15)
            response.raise_for_status()
            ls_data = response.json()

            for item in ls_data:
                sym = item.get("symbol")
                history = item.get("history", [])

                if len(history) >= 2:
                    prev_candle = history[-2]
                    curr_candle = history[-1]

                    prev_long = prev_candle.get("l", 0)
                    curr_long = curr_candle.get("l", 0)

                    # [조건] 1시간 전보다 롱 감소 & 현재 롱 75% 이하
                    if curr_long < prev_long and curr_long <= MAX_LONG_RATIO:
                        ratio_results[sym] = {
                            "curr_long": curr_long,
                            "prev_long": prev_long,
                            "diff": curr_long - prev_long
                        }
        except Exception as e:
            print(f"조회 실패 (심볼 {chunk[:3]}... 외): {e}")

        # 무료 API 요청 횟수 제한(Rate Limit) 방지용 짧은 휴식 (0.3초)
        time.sleep(0.3)

    return ratio_results


if __name__ == "__main__":
    # 1. 조건에 맞는 모든 코인 후보 수집
    candidate_markets = fetch_future_markets()
    candidate_symbols = [m["symbol"] for m in candidate_markets]
    print(f"OI 증가 후보 종목 총 {len(candidate_symbols)}개 발견. 전수 조사를 시작합니다...")

    # 2. 20개 제한 없이 끝까지 전수 검사
    matched_ratios = check_long_account_conditions_all(candidate_symbols)

    # 3. 데이터 결합
    final_list = []
    for m in candidate_markets:
        sym = m["symbol"]
        if sym in matched_ratios:
            final_list.append({
                "symbol": sym,
                "oi_change": m.get("oi_change_percent_1h", 0),
                "oi_usd": m.get("open_interest_usd", 0),
                "curr_long": matched_ratios[sym]["curr_long"],
                "prev_long": matched_ratios[sym]["prev_long"],
                "diff": matched_ratios[sym]["diff"]
            })

    # 4. 결과 전송 (텔레그램 메시지 길이 한계를 고려해 상위 15개 출력)
    if final_list:
        lines = [f"🚨 <b>[전수 검사: OI 증가 & Long ≤ 75% 감소]</b>\n"]
        lines.append(f"<i>(총 {len(final_list)}개 만족 / 상위 15개 표시)</i>\n")
        
        for m in final_list[:15]:
            lines.append(
                f"• <b>{m['symbol']}</b>\n"
                f"  - 1H OI 변동: <code>+{m['oi_change']:.2f}%</code>\n"
                f"  - Long 계정 %: <b>{m['prev_long']:.1f}% → {m['curr_long']:.1f}%</b> (<code>{m['diff']:.1f}%p</code>)\n"
                f"  - 현재 OI: ${m['oi_usd'] / 1_000_000:.2f}M\n"
            )
        send_telegram_msg("\n".join(lines))
        print(f"전송 완료: 총 {len(final_list)}개 중 상위 15개 전송")
    else:
        msg = "✅ <b>[전수 검사 완료]</b>\n\n현재 전체 시장에서 <i>1H OI 증가 + Long 75% 이하 감소</i> 조건을 만족하는 코인이 없습니다."
        send_telegram_msg(msg)
        print("조건 만족 종목 없음")
