import os
import time
import requests
from playwright.sync_api import sync_playwright

TELEGRAM_BOT_TOKEN = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
TELEGRAM_CHAT_ID = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()

# 여기에 1번에서 복사한 본인의 스크리너 전체 URL을 붙여넣으세요.
TARGET_URL = "https://coinalyze.net/?filter=Y20zNDEyX2x0XzAmY20zMzU5X2d0XzAmN19sdF83NQ&columns=YSZlJnMmaSZqJnAmcSY0JjcmY20zNDEyJmNtMzM1OQ&order_by=cm3359&order_dir=desc"


def send_telegram_photo(photo_path, caption=""):
    """텔레그램으로 스크린샷 이미지 발송"""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    with open(photo_path, "rb") as photo_file:
        files = {"photo": photo_file}
        data = {"chat_id": TELEGRAM_CHAT_ID, "caption": caption}
        resp = requests.post(url, files=files, data=data, timeout=30)
        resp.raise_for_status()


def capture_screener():
    screenshot_path = "screener.png"
    print("브라우저를 시작합니다...")

    with sync_playwright() as p:
        # 가상 브라우저 실행 (데스크톱 해상도 설정)
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1600, "height": 1000},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = context.new_page()

        print(f"웹페이지 접속 중: {TARGET_URL}")
        page.goto(TARGET_URL, wait_until="networkidle", timeout=60000)

        # 데이터 테이블이 온전히 렌더링될 때까지 5초 대기
        time.sleep(5)

        # 스크리너 표 영역 위주로 전체 화면 캡처
        page.screenshot(path=screenshot_path, full_page=False)
        print("화면 캡처 완료!")

        browser.close()

    return screenshot_path


if __name__ == "__main__":
    img_file = capture_screener()
    send_telegram_photo(img_file, caption="📸 <b>[Coinalyze 스크리너 실시간 현황]</b>")
    print("텔레그램 전송 완료!")
