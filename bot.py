import os
import time
import requests

COINALYZE_API_KEY = (os.getenv("COINALYZE_API_KEY") or "").strip()
TELEGRAM_BOT_TOKEN = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
TELEGRAM_CHAT_ID = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()

# 기본 필터링 기준
MIN_VOLUME_USD = 5_000_000  # 최소 24시간 거래대금 (잡코인 제외용: $5M)


def fetch_future_markets():
    """1. 전체 선물 시장에서 OI가 증가한 종목 1차 추출"""
    url = "https://api.coinalyze.net/v1/future-markets"
    headers = {"api_key": COINALYZE_API_KEY}
    
    response = requests.get(url, headers=headers, timeout=15)
    response.raise_for_status()
    markets = response.json()

    # 1시간 OI 변동률이 0보다 큰(증가한) 종목만 1차 필터링
    oi_increasing = []
    for m in markets:
        vol = m.get("volume_usd_24h", 0)
        oi_change = m.get("oi_change_percent_1h")
        has_ls_data = m.get("has_long_short_ratio_data", False)

        if oi_change is not None and vol is not None:
            if oi_change > 0 and vol >= MIN_VOLUME_USD and has_ls_data:
                oi_increasing.append(m)

    # OI 증가율 높은 순으로 정렬
    oi_increasing.sort(key=lambda x: x.get("oi_change_percent_1h", 0), reverse=True)
    return oi_increasing


def check_long_account_decreased(symbols):
    """2. 1차 선별된 종목들의 Long Accounts %가 1시간 전보다 줄었는지 확인"""
    if not symbols:
        return {}

    # API 레이트 리밋을 고려해 상위 30개 심볼만 배치 조회
    target_symbols = symbols[:30]
    symbols_param = ",".join(target_symbols)

    now = int(time.time())
    one_hour_ago = now - 3600

    url = "https://api.coinalyze.net/v1/long-short-ratio-history"
    params = {
        "symbols": symbols_param,
        "interval": "1hour",
        "from": one_hour_ago - 3600,
        "to": now
    }
    headers = {"api_key": COINALYZE_API_KEY}

    try:
        response = requests.get(url, params=params, headers=headers, timeout=15)
        response.raise_for_status()
        ls_data = response.json()
    except Exception as e:
        print(f"롱/숏 데이터 조회 중 오류: {e}")
        return {}

    ratio_results = {}
    for item in ls_data:
        sym = item.get("symbol")
        history = item.get("history", [])

        # 최소 2개 이상의 캔들 데이터가 있어야 1시간 전과 비교 가능
        if len(history) >= 2:
            prev_candle = history[-2]  # 1시간 전
            curr_candle = history[-1]  # 현재

            prev_long = prev_candle.get("l", 0)
            curr_long = curr_candle.get("l", 0)

            # Long %가 줄어들었는지 검사
            if curr_long < prev_long:
                ratio_results[sym] = {
                    "curr_long": curr_long,
                    "prev_long": prev_long,
                    "diff": curr_long - prev_long
                }

    return ratio_results


def send_telegram_alert(matched_items):
    """3. 최종 조건 만족 종목 텔레그램 전송"""
    if not matched_items:
        print("조건(OI 증가 + Long 비율 감소)을 만족하는 종목이 없습니다.")
        return

    lines = ["🚨 <b>[OI 증가 & Long % 감소 감지]</b>\n"]
    lines.append("<i>(기준: 1시간 전 대비 OI 증가 + 롱 비율 하락)</i>\n")

    for m in matched_items[:10]:  # 상위 10개 표시
        lines.append(
            f"• <b>{m['symbol']}</b>\n"
            f"  - 1H OI 변동: <code>+{m['oi_change']:.2f}%</code>\n"
            f"  - Long 계정 %: <b>{m['prev_long']:.1f}% → {m['curr_long']:.1f}%</b> (<code>{m['diff']:.1f}%p</code>)\n"
            f"  - 현재 OI: ${m['oi_usd'] / 1_000_000:.2f}M\n"
            f"  - 24H 거래대금: ${m['volume_24h'] / 1_000_000:.2f}M\n"
        )

    text = "\n".join(lines)
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML"
    }

    resp = requests.post(url, json=payload, timeout=10)
    resp.raise_for_status()
    print(f"{len(matched_items)}개 종목 전송 완료")


if __name__ == "__main__":
    # 1. OI 증가 종목 선별 (OI 높은 순 정렬)
    candidate_markets = fetch_future_markets()
    candidate_symbols = [m["symbol"] for m in candidate_markets]

    # 2. Long Account 감소 종목 선별
    decreased_ratios = check_long_account_decreased(candidate_symbols)

    # 3. 두 조건 모두 만족하는 최종 종목 취합
    final_list = []
    for m in candidate_markets:
        sym = m["symbol"]
        if sym in decreased_ratios:
            final_list.append({
                "symbol": sym,
                "oi_change": m["oi_change_percent_1h"],
                "oi_usd": m.get("open_interest_usd", 0),
                "volume_24h": m.get("volume_usd_24h", 0),
                "curr_long": decreased_ratios[sym]["curr_long"],
                "prev_long": decreased_ratios[sym]["prev_long"],
                "diff": decreased_ratios[sym]["diff"]
            })

    # 4. 텔레그램 전송
    send_telegram_alert(final_list)
