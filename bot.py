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
    """1. 전체 선물 시장에서 거래대금 제한 없이 1H OI가 증가한 종목 추출"""
    url = "https://api.coinalyze.net/v1/future-markets"
    headers = {"api_key": COINALYZE_API_KEY}

    response = requests.get(url, headers=headers, timeout=15)
    response.raise_for_status()
    markets = response.json()

    oi_increasing = []
    for m in markets:
        oi_change = m.get("oi_change_percent_1h")
        has_ls_data = m.get("has_long_short_ratio_data", False)

        # 거래대금 필터는 제외, OI가 증가(> 0)하고 롱숏 비율 데이터를 제공하는 종목
        if oi_change is not None and oi_change > 0 and has_ls_data:
            oi_increasing.append(m)

    # 1시간 OI 증가율 높은 순으로 정렬
    oi_increasing.sort(key=lambda x: x.get("oi_change_percent_1h", 0), reverse=True)
    return oi_increasing


def check_long_account_conditions(symbols):
    """2. 1시간 전 대비 롱 비율 감소 & 현재 롱 비율 <= 75% 조건 확인"""
    if not symbols:
        return {}

    # Coinalyze API 1회 배치 제한(최대 20개)에 맞춰 상위 20개 우선 조회
    target_symbols = symbols[:20]
    symbols_param = ",".join(target_symbols)

    now = int(time.time())
    from_time = now - (3600 * 3)

    url = "https://api.coinalyze.net/v1/long-short-ratio-history"
    params = {
        "symbols": symbols_param,
        "interval": "1hour",
        "from": from_time,
        "to": now
    }
    headers = {"api_key": COINALYZE_API_KEY}

    try:
        response = requests.get(url, params=params, headers=headers, timeout=15)
        response.raise_for_status()
        ls_data = response.json()
    except Exception as e:
        print(f"롱숏 데이터 조회 실패: {e}")
        return {}

    ratio_results = {}
    for item in ls_data:
        sym = item.get("symbol")
        history = item.get("history", [])

        # 최근 캔들 2개 비교 (1시간 전 vs 현재)
        if len(history) >= 2:
            prev_candle = history[-2]
            curr_candle = history[-1]

            prev_long = prev_candle.get("l", 0)
            curr_long = curr_candle.get("l", 0)

            # [조건 1] 1시간 전보다 롱 비율 감소
            # [조건 2] 현재 롱 비율이 75% 이하
            if curr_long < prev_long and curr_long <= MAX_LONG_RATIO:
                ratio_results[sym] = {
                    "curr_long": curr_long,
                    "prev_long": prev_long,
                    "diff": curr_long - prev_long
                }

    return ratio_results


if __name__ == "__main__":
    candidate_markets = fetch_future_markets()
    candidate_symbols = [m["symbol"] for m in candidate_markets]

    matched_ratios = check_long_account_conditions(candidate_symbols)

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

    # 결과 전송
    if final_list:
        lines = ["🚨 <b>[OI 증가 & Long ≤ 75% 감소 감지]</b>\n"]
        lines.append("<i>(조건: 거래대금 무관 / 1H OI 증가 / 롱 비율 감소 및 75% 이하)</i>\n")
        for m in final_list[:10]:
            lines.append(
                f"• <b>{m['symbol']}</b>\n"
                f"  - 1H OI 변동: <code>+{m['oi_change']:.2f}%</code>\n"
                f"  - Long 계정 %: <b>{m['prev_long']:.1f}% → {m['curr_long']:.1f}%</b> (<code>{m['diff']:.1f}%p</code>)\n"
                f"  - 현재 OI: ${m['oi_usd'] / 1_000_000:.2f}M\n"
            )
        send_telegram_msg("\n".join(lines))
        print(f"전송 완료: {len(final_list)}개")
    else:
        msg = "✅ <b>[Coinalyze 스크리너 점검 완료]</b>\n\n현재 <i>1H OI 증가 + Long 75% 이하 감소</i> 조건을 만족하는 코인이 없습니다."
        send_telegram_msg(msg)
        print("조건 만족 종목 없음 (확인 알림 발송 완료)")
