import json, os, requests, re, hashlib
from playwright.sync_api import sync_playwright
from urllib.parse import urljoin, urlparse

DISCORD_WEBHOOK_URL = os.environ["DISCORD_WEBHOOK_URL"]
TARGET_SITE_URL = os.environ["TARGET_SITE_URL"]

CACHE_FILE = "known_hashes.json"
FORCE_OVERWRITE = False
MAX_CATEGORIES = 25


def hash_key(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def is_static_asset(url):
    return bool(re.search(r'\.(jpg|jpeg|png|gif|css|js|ico|webp|svg|xml|json|pdf)(\?|$)', url, re.IGNORECASE))


def is_same_site(url, base_netloc):
    return urlparse(url).netloc in ("", base_netloc)


def collect_links(page, base_url, base_netloc):
    hrefs = page.eval_on_selector_all("a[href]", "els => els.map(e => e.getAttribute('href'))")
    links = set()
    for href in hrefs:
        if not href or href.startswith("#") or href.startswith("mailto:") or href.startswith("tel:") or href.startswith("javascript:"):
            continue
        full = urljoin(base_url, href)
        full = full.split("#")[0]
        if is_same_site(full, base_netloc) and not is_static_asset(full):
            links.add(full)
    return links


def looks_like_item(url):
    path = urlparse(url).path.strip("/")
    if not path:
        return False
    return len(path.split("/")) >= 2


def name_from_url(url):
    path = urlparse(url).path.strip("/")
    parts = path.split("/")
    raw = parts[-1] if parts else "UNKNOWN"
    raw = re.sub(r'\.(html?|php)$', '', raw, flags=re.IGNORECASE)
    return raw.replace("-", " ").upper()


def fetch_all_items():
    items = {}
    base_netloc = urlparse(TARGET_SITE_URL).netloc

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        print("対象ページへアクセス中...")
        page.goto(TARGET_SITE_URL, wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(3000)

        top_links = collect_links(page, TARGET_SITE_URL, base_netloc)
        categories = set([TARGET_SITE_URL])
        for url in top_links:
            path = urlparse(url).path.strip("/")
            if path and len(path.split("/")) <= 2:
                categories.add(url)

        print(f"巡回候補セクション数: {len(categories)}")
        categories = list(categories)[:MAX_CATEGORIES]
        print(f"実際に巡回するセクション数: {len(categories)}")

        for i, cat_url in enumerate(categories):
            try:
                page.goto(cat_url, wait_until="networkidle", timeout=45000)
                page.wait_for_timeout(1500)
                links = collect_links(page, cat_url, base_netloc)
                for url in links:
                    if looks_like_item(url) and url not in items:
                        items[url] = name_from_url(url)
                print(f"[{i+1}/{len(categories)}] セクション巡回完了 → 累計 {len(items)}件")
            except Exception as e:
                print(f"セクション取得失敗 ({i+1}/{len(categories)}): {e}")
                continue

        browser.close()

    return items


def load_known_hashes():
    if not FORCE_OVERWRITE and os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except:
            return set()
    return set()


def save_known_hashes(hash_set):
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(hash_set), f)


def send_to_discord(text):
    try:
        res = requests.post(DISCORD_WEBHOOK_URL, json={"content": "🚨 " + text})
        print(f"Discord送信ステータス: {res.status_code}")
    except Exception as e:
        print(f"Discord送信エラー: {e}")


def run():
    known_hashes = load_known_hashes()
    is_first = len(known_hashes) == 0 or FORCE_OVERWRITE

    current_items = fetch_all_items()
    current_hashes = {hash_key(url): url for url in current_items.keys()}

    print(f"【判定結果】検出件数: {len(current_items)}件 ／ 既知件数: {len(known_hashes)}件")

    if is_first:
        save_known_hashes(set(current_hashes.keys()))
        send_to_discord(f"監視を開始しました。\n現在検出された件数: {len(current_items)}件")
        print("初回登録を完了しました。")
        return

    new_hashes = [h for h in current_hashes.keys() if h not in known_hashes]

    if new_hashes:
        new_urls = [current_hashes[h] for h in new_hashes]
        batch_size = 5
        for i in range(0, len(new_urls), batch_size):
            chunk = new_urls[i:i + batch_size]
            msg = f"新着を検知 ({i+1}~{i+len(chunk)}件 / 全{len(new_urls)}件)\n\n"
            for url in chunk:
                msg += f"・{current_items[url]}\n{url}\n\n"
            send_to_discord(msg)
        print(f"新着 {len(new_hashes)}件を通知しました。")
    else:
        print("更新なし。")

    known_hashes.update(current_hashes.keys())
    save_known_hashes(known_hashes)


if __name__ == "__main__":
    run()
