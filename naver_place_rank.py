"""네이버 통합검색 플레이스 영역 순위 수집 (병원명 | 광고여부 | 순위)

사용법 (사용자 PC에서 실행):
    pip install playwright
    playwright install chromium
    python naver_place_rank.py --keyword 판교통증의학과 --max-pages 58 --out result.csv

동작 요약
- 통합검색 결과의 플레이스 영역에서 업체명(span.q2LdB)을 읽고 페이지를 넘기며 순위를 누적한다.
- 한 페이지 안의 순서는 화면상 좌상단 -> 우하단(위에서 아래, 같은 줄이면 왼쪽에서 오른쪽)으로 센다.
- 카드에 '광고' 글자가 실제로 있을 때만 광고로 기록한다. 판별 불가면 '확인불가'.
- 다음 페이지가 없거나 목록이 바뀌지 않으면 거기서 멈춘다 (58쪽까지 있다고 가정하지 않는다).

주의: 아래 SELECTOR_* 값은 네이버 화면 구조(난독화된 클래스)에 따라 바뀔 수 있다.
      실행 전 한 번 확인하고, 안 맞으면 개발자도구(F12)에서 찾은 값으로 교체한다.
"""
import argparse
import csv
import random
import time
from datetime import datetime
from urllib.parse import quote

from playwright.sync_api import sync_playwright

SELECTOR_NAME = "span.q2LdB"          # 사용자가 제공한 업체명 요소
SELECTOR_CARD_XPATH = "xpath=ancestor::li[1]"   # 업체명 요소의 카드(li) 추정. 안 맞으면 교체
SELECTOR_AD_TEXT = r"text=/^\s*광고\s*$/"       # 카드 안의 '광고' 표시
SELECTOR_NEXT = [
    "a.btn_next",
    "button:has-text('다음')",
    "a:has-text('다음')",
    "[aria-label*='다음']",
]


def read_cards(page):
    """현재 페이지의 (병원명, 광고여부) 목록을 화면 순서대로 반환."""
    items = []
    for el in page.locator(SELECTOR_NAME).all():
        name = (el.inner_text() or "").strip()
        if not name:
            continue
        box = el.bounding_box()
        if box is None:  # 화면에 안 그려진 요소(숨김)는 제외
            continue
        ad = "확인불가"
        try:
            card = el.locator(SELECTOR_CARD_XPATH)
            if card.count() == 1:
                ad = "광고" if card.locator(SELECTOR_AD_TEXT).count() > 0 else "일반"
        except Exception:
            pass
        items.append((round(box["y"] / 10), box["x"], name, ad))  # y는 같은 줄 묶기용 반올림
    items.sort(key=lambda t: (t[0], t[1]))
    return [(n, a) for _, _, n, a in items]


def click_next(page):
    for sel in SELECTOR_NEXT:
        loc = page.locator(sel)
        if loc.count() > 0:
            try:
                if loc.first.is_enabled() and loc.first.is_visible():
                    loc.first.click()
                    return True
            except Exception:
                continue
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keyword", required=True)
    ap.add_argument("--max-pages", type=int, default=58)
    ap.add_argument("--out", default="place_rank.csv")
    ap.add_argument("--min-delay", type=float, default=2.0)
    ap.add_argument("--max-delay", type=float, default=4.0)
    ap.add_argument("--headless", action="store_true", help="서버(GitHub Actions)에서는 지정")
    ap.add_argument("--debug", action="store_true", help="1쪽 화면 캡처와 HTML 저장 (원인 파악용)")
    args = ap.parse_args()

    url = f"https://search.naver.com/search.naver?where=nexearch&ie=utf8&query={quote(args.keyword)}"
    rows, rank = [], 0
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=args.headless)
        context = browser.new_context(
            viewport={"width": 1400, "height": 1000},
            locale="ko-KR",
            timezone_id="Asia/Seoul",
        )
        page = context.new_page()
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
        if args.debug:
            page.screenshot(path=args.out + ".page1.png", full_page=True)
            with open(args.out + ".page1.html", "w", encoding="utf-8") as f:
                f.write(page.content())
            # 실행 화면 알림으로 읽을 수 있도록 진단 정보를 따로 저장
            diag = {"title": page.title(), "url": page.url}
            try:
                diag["body_head"] = page.inner_text("body")[:300]
            except Exception as e:
                diag["body_head"] = f"(읽기 실패: {e})"
            names = page.locator(SELECTOR_NAME)
            diag["name_count"] = names.count()
            diag["card_html"] = ""
            if diag["name_count"] > 0:
                try:
                    card = names.first.locator(SELECTOR_CARD_XPATH)
                    diag["card_count"] = card.count()
                    if card.count() == 1:
                        diag["card_html"] = card.evaluate("e => e.outerHTML")[:2500]
                except Exception as e:
                    diag["card_html"] = f"(카드 HTML 읽기 실패: {e})"
            import json
            with open(args.out + ".diag.json", "w", encoding="utf-8") as f:
                json.dump(diag, f, ensure_ascii=False, indent=1)
        try:
            page.wait_for_selector(SELECTOR_NAME, timeout=15000)
        except Exception:
            print("업체명 요소를 찾지 못함 (차단/캡차/선택자 변경 가능). --debug 캡처를 확인하세요.")
            browser.close()
            raise SystemExit(1)

        prev_names = None
        for page_no in range(1, args.max_pages + 1):
            page.wait_for_timeout(1500)
            cards = read_cards(page)
            names = [n for n, _ in cards]
            if not cards:
                print(f"{page_no}쪽: 목록을 읽지 못해 중단 (확인불가)")
                break
            if names == prev_names:
                print(f"{page_no}쪽: 이전 쪽과 목록이 같아 중단")
                break
            for order, (name, ad) in enumerate(cards, start=1):
                rank += 1
                rows.append([rank, name, ad, page_no, order, stamp])
            print(f"{page_no}쪽: {len(cards)}개 (누적 {rank}위)")
            prev_names = names

            if not click_next(page):
                print(f"{page_no}쪽이 마지막 (다음 버튼 없음)")
                break
            time.sleep(random.uniform(args.min_delay, args.max_delay))

        browser.close()

    with open(args.out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["순위", "병원명", "광고여부", "페이지", "페이지내순서", "확인시각"])
        w.writerows(rows)
    print(f"저장: {args.out} ({len(rows)}행)")


if __name__ == "__main__":
    main()
