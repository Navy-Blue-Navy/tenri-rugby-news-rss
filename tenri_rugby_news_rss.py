import os
import sys
import time
import hashlib
import requests
import xml.etree.ElementTree as ET

from bs4 import BeautifulSoup
from datetime import datetime, timezone, timedelta
from email.utils import format_datetime, parsedate_to_datetime
from urllib.parse import urljoin


BASE_URL = "https://rugby.tenri-u.net"
LIST_URL = "https://rugby.tenri-u.net/all-news/"

OUTPUT_FILE = "tenri_rugby_news.xml"

FEED_TITLE = "天理大学ラグビー部 NEWS"
FEED_LINK = LIST_URL
FEED_DESCRIPTION = "天理大学ラグビー部 お知らせ 新着記事RSS"

MAX_PAGES = 3

REQUEST_TIMEOUT = 30
REQUEST_INTERVAL = 1

JST = timezone(timedelta(hours=9))

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/155.0.0.0 Safari/537.36"
    )
}


def rss_date(dt):
    return format_datetime(dt)


def parse_date(text):
    text = text.strip()

    for fmt in ("%Y.%m.%d", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            dt = datetime.strptime(text, fmt)
            return dt.replace(tzinfo=JST)
        except ValueError:
            pass

    raise ValueError(f"日付を解析できません: {text}")


def make_guid(url):
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def get_page(url):
    print(f"GET {url}")

    response = requests.get(
        url,
        headers=HEADERS,
        timeout=REQUEST_TIMEOUT,
    )

    print(f"HTTP: {response.status_code} / {len(response.content)} bytes")

    response.raise_for_status()

    return response


def is_article_url(href):
    if not href:
        return False

    full_url = urljoin(BASE_URL, href)

    if "/news/" not in full_url:
        return False

    last_part = full_url.rstrip("/").split("/")[-1]

    return last_part.isdigit()


def fetch_articles():
    articles = {}
    previous_page_urls = None

    for page in range(1, MAX_PAGES + 1):
        if page == 1:
            url = LIST_URL
        else:
            url = f"{LIST_URL}page/{page}/"

        print()
        print(f"一覧ページ取得: {page}/{MAX_PAGES}")

        response = get_page(url)

        soup = BeautifulSoup(response.content, "html.parser")

        page_articles = []
        page_urls = set()

        links = soup.find_all(
            "a",
            href=lambda x: is_article_url(x),
        )

        for link in links:
            href = link.get("href", "").strip()

            if not href:
                continue

            article_url = urljoin(BASE_URL, href)

            time_tag = link.find("time")
            title_tag = link.find("p")

            if time_tag is None or title_tag is None:
                continue

            date_text = time_tag.get_text(" ", strip=True)
            title = title_tag.get_text(" ", strip=True)

            if not date_text or not title:
                continue

            try:
                published = parse_date(date_text)
            except Exception as e:
                print(f"日付解析失敗: {title}")
                print(f"  {date_text}")
                print(f"  {e}")
                continue

            article = {
                "title": title,
                "url": article_url,
                "published": published,
            }

            page_articles.append(article)
            page_urls.add(article_url)

        print(f"このページの記事取得件数: {len(page_articles)}")

        if len(page_articles) == 0:
            raise RuntimeError(
                f"{page}ページ目から記事を1件も取得できませんでした"
            )

        if previous_page_urls is not None and page_urls == previous_page_urls:
            raise RuntimeError(
                f"{page}ページ目が直前のページと同じ内容です。"
                "ページ送りが正常に機能していない可能性があります。"
            )

        previous_page_urls = page_urls

        for article in page_articles:
            articles[article["url"]] = article

        if page < MAX_PAGES:
            time.sleep(REQUEST_INTERVAL)

    print()
    print(f"今回取得できたユニーク記事数: {len(articles)}")

    return articles


def load_existing_feed():
    articles = {}

    if not os.path.exists(OUTPUT_FILE):
        print("既存RSSなし：新規作成します")
        return articles

    try:
        tree = ET.parse(OUTPUT_FILE)
        root = tree.getroot()

        for item in root.findall("./channel/item"):
            title = item.findtext("title", default="").strip()
            link = item.findtext("link", default="").strip()
            pub_date = item.findtext("pubDate", default="").strip()

            if not link:
                continue

            try:
                published = parsedate_to_datetime(pub_date)
            except Exception:
                continue

            if published.tzinfo is None:
                published = published.replace(tzinfo=JST)

            articles[link] = {
                "title": title,
                "url": link,
                "published": published,
            }

        print(f"既存RSS読込件数: {len(articles)}")

    except Exception as e:
        print(f"既存RSSの読込に失敗しました: {e}")
        print("安全のため処理を中止します。")
        sys.exit(1)

    return articles


def build_rss(articles):
    rss = ET.Element(
        "rss",
        {
            "version": "2.0",
        },
    )

    channel = ET.SubElement(rss, "channel")

    ET.SubElement(channel, "title").text = FEED_TITLE
    ET.SubElement(channel, "link").text = FEED_LINK
    ET.SubElement(channel, "description").text = FEED_DESCRIPTION
    ET.SubElement(channel, "language").text = "ja"

    now = datetime.now(JST)
    ET.SubElement(channel, "lastBuildDate").text = rss_date(now)

    sorted_articles = sorted(
        articles.values(),
        key=lambda x: x["published"],
        reverse=True,
    )

    for article in sorted_articles:
        item = ET.SubElement(channel, "item")

        ET.SubElement(item, "title").text = article["title"]
        ET.SubElement(item, "link").text = article["url"]

        guid = ET.SubElement(
            item,
            "guid",
            {"isPermaLink": "false"},
        )
        guid.text = make_guid(article["url"])

        ET.SubElement(item, "pubDate").text = rss_date(
            article["published"]
        )

        ET.SubElement(item, "description").text = (
            "掲載日: "
            + article["published"]
            .astimezone(JST)
            .strftime("%Y.%m.%d")
        )

    tree = ET.ElementTree(rss)

    ET.indent(tree, space="  ")

    tree.write(
        OUTPUT_FILE,
        encoding="utf-8",
        xml_declaration=True,
    )

    print()
    print(
        f"保存完了: {OUTPUT_FILE} "
        f"({len(sorted_articles)}件)"
    )


def main():
    print("天理大学ラグビー部 NEWS RSS生成")
    print("=" * 50)

    existing_articles = load_existing_feed()

    try:
        current_articles = fetch_articles()

    except Exception as e:
        print()
        print("一覧ページの取得・解析に失敗しました。")
        print(f"エラー: {e}")
        print()
        print("既存RSSは変更せず、そのまま残します。")
        sys.exit(1)

    new_urls = [
        url
        for url in current_articles
        if url not in existing_articles
    ]

    combined_articles = dict(existing_articles)

    for url, article in current_articles.items():
        combined_articles[url] = article

    print()
    print(f"今回取得した記事: {len(current_articles)}")
    print(f"今回初めて確認した記事: {len(new_urls)}")
    print(f"累積RSS件数: {len(combined_articles)}")

    sorted_current = sorted(
        current_articles.values(),
        key=lambda x: x["published"],
        reverse=True,
    )

    print()
    print("今回取得した記事（先頭20件）")
    print("-" * 50)

    for article in sorted_current[:20]:
        date_text = (
            article["published"]
            .astimezone(JST)
            .strftime("%Y-%m-%d")
        )

        print(f"{date_text} / {article['title']}")
        print(f"  {article['url']}")

    build_rss(combined_articles)


if __name__ == "__main__":
    main()