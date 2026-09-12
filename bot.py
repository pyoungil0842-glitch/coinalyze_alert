import os
import time
import requests

COINALYZE_API_KEY = (os.getenv("COINALYZE_API_KEY") or "").strip()
TELEGRAM_BOT_TOKEN = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
TELEGRAM_CHAT_ID = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()

# 필터 기준: 1D(일봉) 롱 계좌수 비율 75% 미만
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


def fetch_oi_increasing_markets():
    """조건 1: pchange(oi_1h[0,-1]) > 0
    전체 선물 시장에서 1H OI 변동률이 0보다 큰 종목 추출
    """
    url = "https://api.coinalyze.net/v1/future-markets"
    headers = {"api_key": COINALYZE_API_KEY}

    response = requests.get(url, headers=headers, timeout=15)
    response.raise_for_status()
    markets = response.json()

    candidates = []
    for m in markets:
        oi_change = m.get("oi_change_percent_1h")
        has_ls_data = m.get("has_long_short_ratio_data", False)

        # 1. 1시간 OI 증가 (> 0) 및 롱숏 비율 데이터 지원 종목
        if oi_change is not None and oi_change > 0 and has_ls_data:
            candidates.append(m)

    # 1시간 OI 증가율이 높은 순으로 정렬
    candidates.sort(key=lambda x: x.get("oi_change_percent_1h", 0), reverse=True)
    return candidates


def filter_lsr_conditions(symbols):
    """조건 2 & 조건 3 전수 검사
    - 조건 2: change(lsr_1h[0,-1]) < 0 (1시간 롱 비율 감소)
    - 조건 3: long accounts % (1d) less than 75 (일봉 기준 롱 비율 75% 미만)
    """
    if not symbols:
        return {}

    headers = {"api_key": COINALYZE_API_KEY}
    now = int(time.time())
    chunk_size = 20  # Coinalyze API 1회 최대 심볼 수
    matched_results = {}

    for i in range(0, len(symbols), chunk_size):
        chunk = symbols[i:i + chunk_size]
        symbols_param = ",".join(chunk)

        # --- A. [1시간봉 데이터 조회] 조건 2 검사용 ---
        try:
            url_1h = "https://api.coinalyze.net/v1/long-short-ratio-history"
            params_1h = {
                "symbols": symbols_param,
                "interval": "1hour",
                "from": now - (3600 * 6),  # 최근 6시간 캔들
                "to": now
            }
            res_1h = requests.get(url_1h, params=params_1h, headers=headers, timeout=15)
            res_1h.raise_for_status()
            data_1h = res_1h.json()
        except Exception as e:
            print(f"1시간봉 조회 오류: {e}")
            data_1h = []

        # --- B. [일봉(daily) 데이터 조회] 조건 3 검사용 ---
        try:
            params_1d = {
                "symbols": symbols_param,
                "interval": "daily",
                "from": now - (86400 * 3),  # 최근 3일 캔들
                "to": now
            }
            res_1d = requests.get(url_1h, params=params_1d, headers=headers, timeout=15)
            res_1d.raise_for_status()
            data_1d = res_1d.json()
        except Exception as e:
            print(f"일봉 조회 오류: {e}")
            data_1d = []

        # 1D 데이터 매핑
        daily_long_map = {}
        for item in data_1d:
            sym = item.get("symbol")
            hist = item.get("history", [])
            if hist:
                latest_candle = hist[-1]
                daily_long_map[sym] = latest_candle.get("l", 0)

        # 1H 데이터와 함께 두 조건 동시 검증
        for item in data_1h:
            sym = item.get("symbol")
            hist = item.get("history", [])

            if len(hist) >= 2 and sym in daily_long_map:
                prev_1h_long = hist[-2].get("l", 0)
                curr_1h_long = hist[-1].get("l", 0)
                curr_1d_long = daily_long_map[sym]

                # 조건 2: 1시간 롱 비율 감소 (change < 0)
                cond2 = (curr_1h_long - prev_1h_long) < 0

                # 조건 3: 1D 롱 비율 < 75%
                cond3 = curr_1d_long < MAX_LONG_RATIO_1D

                if cond2 and cond3:
                    matched_results[sym] = {
                        "prev_1h_long": prev_1h_long,
                        "curr_1h_long": curr_1h_long,
                        "diff_1h": curr_1h_long - prev_1h_long,
                        "daily_long": curr_1d_long
                    }

        time.sleep(0.3)  # API 요청 간 안전 딜레이

    return matched_results


if __name__ == "__main__":
    # 1. 조건 1: 1H OI 증가 종목 전체 취합 (OI 높은 순 정렬)
    oi_candidates = fetch_oi_increasing_markets()
    candidate_symbols = [m["symbol"] for m in oi_candidates]
    print(f"1H OI 증가 후보: {len(candidate_symbols)}개. 롱숏 세부 검사를 시작합니다...")

    # 2. 조건 2 & 3 전수 검사
    matched_data = filter_lsr_conditions(candidate_symbols)

    # 3. 최종 매칭 결과 취합
    final_list = []
    for m in oi_candidates:
        sym = m["symbol"]
        if sym in matched_data:
            final_list.append({
                "symbol": sym,
                "oi_change": m.get("oi_change_percent_1h", 0),
                "oi_usd": m.get("open_interest_usd", 0),
                "prev_1h_long": matched_data[sym]["prev_1h_long"],
                "curr_1h_long": matched_data[sym]["curr_1h_long"],
                "diff_1h": matched_data[sym]["diff_1h"],
                "daily_long": matched_data[sym]["daily_long"]
            })

    # 4. 텔레그램 전송
    if final_list:
        lines = [f"🚨 <b>[스크리너 조건 만족 종목]</b>\n"]
        lines.append(f"<i>(총 {len(final_list)}개 검출 / 1H OI 증가율 순 정렬)</i>\n")

        for m in final_list[:15]:  # 상위 15개 출력
            lines.append(
                f"• <b>{m['symbol']}</b>\n"
                f"  - 1H OI: <code>+{m['oi_change']:.2f}%</code>\n"
                f"  - 1H Long: {m['prev_1h_long']:.1f}% → <b>{m['curr_1h_long']:.1f}%</b> (<code>{m['diff_1h']:.1f}%p</code>)\n"
                f"  - 1D Long: <b>{m['daily_long']:.1f}%</b> (< 75%)\n"
                f"  - 현재 OI: ${m['oi_usd'] / 1_000_000:.2f}M\n"
            )
        send_telegram_msg("\n".join(lines))
        print(f"전송 완료: {len(final_list)}개")
    else:
        msg = "✅ <b>[스크리너 점검 완료]</b>\n\n현재 3가지 조건을 동시에 만족하는 코인이 없습니다."
        send_telegram_msg(msg)
        print("조건 만족 종목 없음")
