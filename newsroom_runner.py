import os, re, json, time, hashlib
from datetime import datetime, timezone, timedelta
from urllib.parse import quote_plus
import requests, feedparser
from bs4 import BeautifulSoup

source = open("newsroom.py", "r", encoding="utf-8").read()
ns = {"__name__": "newsroom_loaded"}
exec(compile(source, "newsroom.py", "exec"), ns)

clean_text = ns["clean_text"]
article_id = ns["article_id"]
load_state = ns["load_state"]
save_state = ns["save_state"]
collect_sources = ns["collect_sources"]
ask_openrouter = ns["ask_openrouter"]
validate_story = ns["validate_story"]
upload_image = ns["upload_image"]
publish_post = ns["publish_post"]

GOOGLE_NEWS_RSS = "https://news.google.com/rss/search"
MIN_AGE = timedelta(hours=24)
MAX_AGE = timedelta(days=14)


def parse_entry_time(entry):
    for key in ("published_parsed", "updated_parsed"):
        value = entry.get(key)
        if value:
            return datetime(*value[:6], tzinfo=timezone.utc)
    return None


def resolve_guardian_url(url):
    try:
        r = requests.get(
            url,
            timeout=20,
            headers={"User-Agent": "AustraliaByAussie-Newsroom/1.0"},
            allow_redirects=True,
        )
        if "theguardian.com/australia-news/" in r.url:
            return r.url
    except Exception:
        pass
    return url


def discover_old_guardian_stories():
    now = datetime.now(timezone.utc)
    candidates = {}
    queries = [
        "site:theguardian.com/australia-news Australia",
        "site:theguardian.com/australia-news politics Australia",
        "site:theguardian.com/australia-news business Australia",
        "site:theguardian.com/australia-news cost of living Australia",
        "site:theguardian.com/australia-news crime courts Australia",
    ]

    for query in queries:
        url = f"{GOOGLE_NEWS_RSS}?q={quote_plus(query)}&hl=en-AU&gl=AU&ceid=AU:en"
        try:
            response = requests.get(
                url,
                timeout=30,
                headers={"User-Agent": "AustraliaByAussie-Newsroom/1.0"},
            )
            response.raise_for_status()
            feed = feedparser.parse(response.content)
        except Exception as exc:
            print("Google News discovery failed:", exc)
            continue

        for entry in feed.entries[:25]:
            published = parse_entry_time(entry)
            if not published:
                continue
            age = now - published
            if age < MIN_AGE or age > MAX_AGE:
                continue

            title = clean_text(entry.get("title", ""))
            link = resolve_guardian_url(entry.get("link", "").strip())
            if not title or "theguardian.com/australia-news/" not in link:
                continue

            key = article_id(link)
            candidates[key] = {
                "id": key,
                "url": link,
                "title": title,
                "published": published,
            }

    return sorted(candidates.values(), key=lambda item: item["published"], reverse=True)


def exact_25_word_excerpt(result):
    website = result.get("website", {})
    excerpt = clean_text(website.get("excerpt", ""))
    words = re.findall(r"\b[\w’'-]+\b", excerpt, flags=re.UNICODE)
    if len(words) == 25:
        return

    article = BeautifulSoup(website.get("article_html", ""), "html.parser").get_text(" ", strip=True)
    article_words = re.findall(r"\b[\w’'-]+\b", article, flags=re.UNICODE)

    if len(words) > 25:
        website["excerpt"] = " ".join(words[:25])
        return

    combined = words[:]
    for word in article_words:
        if len(combined) >= 25:
            break
        combined.append(word)
    website["excerpt"] = " ".join(combined[:25])


def image_extension(url):
    lower = url.lower().split("?", 1)[0]
    if lower.endswith(".png"):
        return ".png"
    if lower.endswith(".webp"):
        return ".webp"
    return ".jpg"


def run_one():
    state = load_state()
    processed = set(state.get("processed", []))
    candidates = discover_old_guardian_stories()
    print(f"Found {len(candidates)} Guardian candidates aged 24h+ and <=14d.")

    story = next((c for c in candidates if c["id"] not in processed), None)
    if not story:
        raise RuntimeError("NO_PUBLICATION: No eligible unprocessed Guardian story found.")

    print("Selected:", story["title"])
    page = ns["get_article_page"](story["url"])
    if not page.get("text"):
        raise RuntimeError("NO_PUBLICATION: Guardian source page was unavailable.")
    story.update(page)

    image_url = story.get("image_url", "").strip()
    if not image_url:
        raise RuntimeError("NO_PUBLICATION: No Guardian source image was found.")
    print("Guardian image found:", image_url)

    sources = collect_sources(story)
    result = ask_openrouter(story, sources)
    verification = result.get("verification", {})
    status = verification.get("status", "")
    print("Verification status:", status)

    if not result.get("publish", False) or status == "INSUFFICIENT VERIFIED INFORMATION":
        raise RuntimeError(
            "NO_PUBLICATION: Story was not approved for publishing. "
            f"publish={result.get('publish', False)}, status={status!r}"
        )

    exact_25_word_excerpt(result)
    validate_story(result)
    website = result["website"]

    filename = re.sub(r"[^a-zA-Z0-9]+", "-", website["headline"]).strip("-").lower() + image_extension(image_url)
    media_id = upload_image(image_url, filename, website["alt_text"])
    print("Image uploaded. Media ID:", media_id)

    post = publish_post(website, media_id)
    post_id = post.get("id")
    post_status = post.get("status")
    if not post_id or post_status != "publish":
        raise RuntimeError(
            "WORDPRESS_PUBLISH_FAILED: WordPress did not confirm a published post. "
            f"id={post_id!r}, status={post_status!r}"
        )

    print("PUBLISHED SUCCESSFULLY")
    print("Title:", post.get("title", {}).get("rendered"))
    print("URL:", post.get("link"))
    print("Post ID:", post_id)

    processed.add(story["id"])
    state["processed"] = list(processed)
    state["last_run"] = datetime.now(timezone.utc).isoformat()
    save_state(state)
    return True


if __name__ == "__main__":
    run_one()
