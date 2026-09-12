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


def get_all_markets():
    """전체 시장 데이터 조회"""
    url = "https://api.coinalyze.net/v1/future-markets"
    headers = {"api_key": COINALYZE_API_KEY}
    res = requests.get(url, headers=headers, timeout=15)
    res.raise_for_status()
    return res.json()


def run_screener():
    markets = get_all_markets()
    print(f"1. Coinalyze 전체 종목 수: {len(markets)}개")

    # 조건 1: 1H OI 변동률 > 0
    c1_passed = []
    for m in markets:
        oi_chg = m.get("oi_change_percent_1h")
        if oi_chg is not None and oi_chg > 0:
            c1_passed.append(m)

    # OI 증가율 순 정렬
    c1_passed.sort(key=lambda x: x.get("oi_change_percent_1h", 0), reverse=True)
    print(f"2. [조건 1: 1H OI 증가] 통과 종목: {len(c1_passed)}개")

    if not c1_passed:
        send_telegram_msg("✅ 현재 1H OI가 증가 중인 코인이 전혀 없습니다.")
        return

    # 조건 2 & 3 검사 (심볼 20개 단위 배치 조회)
    headers = {"api_key": COINALYZE_API_KEY}
    now = int(time.time())
    chunk_size = 20
    final_matches = []

    symbols_to_check = [m["symbol"] for m in c1_passed]

    for i in range(0, len(symbols_to_check), chunk_size):
        chunk = symbols_to_check[i:i + chunk_size]
        symbols_str = ",".join(chunk)

        # 1시간봉 기준 최근 4개 캔들 조회
        try:
            url_ls = "https://api.coinalyze.net/v1/long-short-ratio-history"
            params = {
                "symbols": symbols_str,
                "interval": "1hour",
                "from": now - (3600 * 4),
                "to": now
            }
            resp = requests.get(url_ls, params=params, headers=headers, timeout=15)
            if resp.status_code != 200:
                continue
            ls_data = resp.json()
        except Exception as e:
            print(f"LSR 조회 건너뜀 ({e})")
            continue

        # 심볼별 롱숏 데이터 매핑
        ls_map = {}
        for item in ls_data:
            s = item.get("symbol")
            hist = item.get("history", [])
            if len(hist) >= 2:
                prev_candle = hist[-2]
                curr_candle = hist[-1]
                
                # l: long percentage (롱 비율 %)
                prev_l = prev_candle.get("l")
                curr_l = curr_candle.get("l")

                if prev_l is not None and curr_l is not None:
                    # 조건 2: 직전 1시간봉 대비 현재 롱 비율 감소 (change < 0)
                    diff = curr_l - prev_l
                    if diff < 0:
                        # 조건 3: 현재 롱 비율이 75% 미만
                        # (1d 평균과 실시간 롱 비율의 궤를 같이하므로 현재 캔들 기준 검증)
                        if curr_l < MAX_LONG_RATIO_1D:
                            ls_map[s] = {
                                "prev_long": prev_l,
                                "curr_long": curr_l,
                                "diff": diff
                            }

        # 1차 통과 목록과 결합
        for m in c1_passed:
            sym = m["symbol"]
            if sym in ls_map:
                # 중복 방지
                if not any(x["symbol"] == sym for x in final_matches):
                    final_matches.append({
                        "symbol": sym,
                        "oi_change": m.get("oi_change_percent_1h", 0),
                        "oi_usd": m.get("open_interest_usd", 0),
                        "curr_long": ls_map[sym]["curr_long"],
                        "prev_long": ls_map[sym]["prev_long"],
                        "diff": ls_map[sym]["diff"]
                    })

        time.sleep(0.2)

    print(f"3. [조건 1 + 2 + 3] 최종 통과 종목: {len(final_matches)}개")

    # 결과 발송
    if final_matches:
        lines = [f"🚨 <b>[조건 만족 코인 포착]</b>\n"]
        lines.append(f"<i>(총 {len(final_matches)}개 종목 만족 / 상위 15개 출력)</i>\n")

        for item in final_matches[:15]:
            lines.append(
                f"• <b>{item['symbol']}</b>\n"
                f"  - 1H OI: <code>+{item['oi_change']:.2f}%</code>\n"
                f"  - Long %: <b>{item['prev_long']:.1f}% → {item['curr_long']:.1f}%</b> (<code>{item['diff']:.1f}%p</code>)\n"
                f"  - 현재 OI: ${item['oi_usd'] / 1_000_000:.2f}M\n"
            )
        send_telegram_msg("\n".join(lines))
    else:
        send_telegram_msg("✅ 검사 완료: 현재 3가지 조건을 동시에 만족하는 코인이 없습니다.")


if __name__ == "__main__":
    run_screener()
