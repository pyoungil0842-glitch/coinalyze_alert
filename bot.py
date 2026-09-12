import os
import time
import requests
from playwright.sync_api import sync_playwright

TELEGRAM_BOT_TOKEN = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
TELEGRAM_CHAT_ID = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()
COINALYZE_COOKIE = (os.getenv("COINALYZE_COOKIE") or "").strip()

TARGET_URL = "https://coinalyze.net/screener"


def send_telegram_photo(photo_path, caption=""):
    """텔레그램 사진 전송"""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    with open(photo_path, "rb") as photo_file:
        files = {"photo": photo_file}
        data = {"chat_id": TELEGRAM_CHAT_ID, "caption": caption}
        resp = requests.post(url, files=files, data=data, timeout=30)
        resp.raise_for_status()


def parse_cookies(cookie_str):
    """문자열 형태의 쿠키를 Playwright 형식으로 변환"""
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
    print("브라우저를 시작합니다...")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1600, "height": 1000},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )

        # 내 계정의 세션 및 설정 쿠키 주입
        cookies = parse_cookies(COINALYZE_COOKIE)
        if cookies:
            context.add_cookies(cookies)
            print(f"쿠키 {len(cookies)}개 주입 완료")

        page = context.new_page()

        print(f"스크리너 접속 중: {TARGET_URL}")
        page.goto(TARGET_URL, wait_until="networkidle", timeout=60000)

        # 내 커스텀 필터와 표 데이터가 다 그려질 때까지 7초 여유 대기
        time.sleep(7)

        # 화면 캡처
        page.screenshot(path=screenshot_path, full_page=False)
        print("화면 캡처 완료!")

        browser.close()

    return screenshot_path


if __name__ == "__main__":
    img_file = capture_screener()
    send_telegram_photo(img_file, caption="📸 <b>[Coinalyze 내 맞춤 스크리너 현황]</b>")
    print("텔레그램 전송 완료!")
