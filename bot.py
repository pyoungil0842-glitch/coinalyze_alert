import os
import json
import time
import requests
from playwright.sync_api import sync_playwright

TELEGRAM_BOT_TOKEN = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
TELEGRAM_CHAT_ID = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()
COINALYZE_SESSION = (os.getenv("COINALYZE_SESSION") or "").strip()

# 본인의 실제 Coinalyze 스크리너 URL로 확인해 주세요
TARGET_URL = "https://coinalyze.net"  


def send_telegram_photo(photo_path, caption=""):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    with open(photo_path, "rb") as photo_file:
        files = {"photo": photo_file}
        data = {"chat_id": TELEGRAM_CHAT_ID, "caption": caption}
        resp = requests.post(url, files=files, data=data, timeout=30)
        resp.raise_for_status()


def parse_cookies(cookie_str):
    cookies = []
    if not cookie_str:
        return cookies
    for item in cookie_str.split(";"):
        if "=" in item:
            name, value = item.strip().split("=", 1)
            cookies.append({
                "name": name,
                "value": value,
                "domain": ".coinalyze.net",
                "path": "/"
            })
    return cookies


def capture_screener():
    screenshot_path = "screener.png"
    print("가상 브라우저 준비 중...")

    # 저장된 세션 데이터 파싱
    session_data = {}
    if COINALYZE_SESSION:
        try:
            session_data = json.loads(COINALYZE_SESSION)
        except Exception as e:
            print(f"세션 파싱 실패: {e}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1650, "height": 1050},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )

        # 1. 쿠키 주입
        cookie_raw = session_data.get("cookies", "")
        cookies = parse_cookies(cookie_raw)
        if cookies:
            context.add_cookies(cookies)

        page = context.new_page()

        # 도메인 선접속 (LocalStorage 주입을 위한 기초 페이지 로딩)
        page.goto("https://coinalyze.net", wait_until="commit", timeout=30000)

        # 2. LocalStorage(개인 설정 및 필터) 통째 복원
        local_storage_items = session_data.get("localStorage", [])
        if local_storage_items:
            for k, v in local_storage_items:
                try:
                    page.evaluate(f"([key, val]) => localStorage.setItem(key, val)", [k, v])
                except Exception:
                    pass
            print(f"로컬스토리지 항목 {len(local_storage_items)}개 복원 완료")

        # 3. 내 맞춤 스크리너 페이지로 최종 이동
        screener_url = "https://coinalyze.net/crypto-screener"  # 또는 본인의 저장된 뷰 URL
        print(f"스크리너 페이지 접속: {screener_url}")
        page.goto(screener_url, wait_until="networkidle", timeout=60000)

        # 필터링 및 표 로딩 대기
        time.sleep(8)

        # 화면 캡처
        page.screenshot(path=screenshot_path, full_page=False)
        print("로그인 상태 맞춤 화면 캡처 완료!")

        browser.close()

    return screenshot_path


if __name__ == "__main__":
    img_file = capture_screener()
    send_telegram_photo(img_file, caption="📸 <b>[Coinalyze 맞춤 스크리너 현황]</b>")
    print("텔레그램 전송 완료!")
