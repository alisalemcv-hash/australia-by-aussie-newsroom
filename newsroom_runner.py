import os, re, json, time, hashlib
from datetime import datetime, timezone, timedelta
from urllib.parse import quote_plus
import requests, feedparser
from bs4 import BeautifulSoup

# Load the proven newsroom implementation without running its main().
source = open("newsroom.py", "r", encoding="utf-8").read()
ns = {"__name__": "newsroom_loaded"}
exec(compile(source, "newsroom.py", "exec"), ns)

http_get = ns["http_get"]
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
        url = (
            f"{GOOGLE_NEWS_RSS}?q={quote_plus(query)}"
            "&hl=en-AU&gl=AU&ceid=AU:en"
        )
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

    return sorted(
        candidates.values(),
        key=lambda item: item["published"],
        reverse=True,
    )


def find_reusable_image(query):
    """Find a freely reusable image; never strip branding/watermarks."""
    api = "https://commons.wikimedia.org/w/api.php"
    params = {
        "action": "query",
        "generator": "search",
        "gsrsearch": query,
        "gsrnamespace": 6,
        "gsrlimit": 10,
        "prop": "imageinfo",
        "iiprop": "url|extmetadata",
        "iiurlwidth": 1600,
        "format": "json",
    }

    try:
        response = requests.get(
            api,
            params=params,
            timeout=30,
            headers={"User-Agent": "AustraliaByAussie-Newsroom/1.0"},
        )
        response.raise_for_status()
        pages = response.json().get("query", {}).get("pages", {}).values()
    except Exception as exc:
        print("Wikimedia Commons search failed:", exc)
        return None

    for page in pages:
        info = (page.get("imageinfo") or [{}])[0]
        meta = info.get("extmetadata") or {}
        license_name = clean_text(
            meta.get("LicenseShortName", {}).get("value", "")
        )
        license_lower = license_name.lower()

        if any(term in license_lower for term in ("noncommercial", "no derivatives")):
            continue

        image_url = info.get("thumburl") or info.get("url")
        if not image_url or not image_url.startswith(("http://", "https://")):
            continue

        return {
            "url": image_url,
            "title": clean_text(page.get("title", "")).replace("File:", "", 1),
            "author": clean_text(meta.get("Artist", {}).get("value", "")),
            "license": license_name or "See Wikimedia Commons file page",
        }

    return None


def run_one():
    state = load_state()
    processed = set(state.get("processed", []))
    candidates = discover_old_guardian_stories()
    print(f"Found {len(candidates)} Guardian candidates aged 24h+ and <=14d.")

    story = next(
        (candidate for candidate in candidates if candidate["id"] not in processed),
        None,
    )

    if not story:
        print("No eligible unprocessed Guardian story found.")
        return False

    print("Selected:", story["title"])

    page = ns["get_article_page"](story["url"])
    if not page.get("text"):
        print("Source page unavailable. Skipping.")
        return False
    story.update(page)

    # Do not strip or bypass Guardian branding/watermarks.
    # Use a separately reusable image instead.
    image = find_reusable_image(story["title"])
    if not image:
        print("No verified reusable image found. Skipping story safely.")
        return False

    print("Reusable image:", image["url"])

    sources = collect_sources(story)
    result = ask_openrouter(story, sources)
    verification = result.get("verification", {})
    status = verification.get("status", "")
    print("Verification status:", status)

    if not result.get("publish", False) or status == "INSUFFICIENT VERIFIED INFORMATION":
        print("Story not approved for publishing.")
        processed.add(story["id"])
        state["processed"] = list(processed)
        save_state(state)
        return False

    validate_story(result)
    website = result["website"]

    website["alt_text"] = (
        f"{website.get('alt_text', '').strip()} "
        "(illustrative image from Wikimedia Commons)"
    ).strip()
    website["caption"] = (
        f"Illustrative image: {image['title']}. "
        f"Source: Wikimedia Commons; licence: {image['license']}."
    )
    website["description"] = (
        "Illustrative image used under the stated Wikimedia Commons licence. "
        f"Creator: {image['author'] or 'as credited on the source page'}."
    )

    extension = ".jpg"
    lower_url = image["url"].lower()
    if ".png" in lower_url:
        extension = ".png"
    elif ".webp" in lower_url:
        extension = ".webp"

    filename = (
        re.sub(r"[^a-zA-Z0-9]+", "-", website["headline"])
        .strip("-")
        .lower()
        + extension
    )

    media_id = upload_image(
        image["url"],
        filename,
        website["alt_text"],
    )

    post = publish_post(website, media_id)

    print("PUBLISHED SUCCESSFULLY")
    print("Title:", post.get("title", {}).get("rendered"))
    print("URL:", post.get("link"))
    print("Post ID:", post.get("id"))

    processed.add(story["id"])
    state["processed"] = list(processed)
    state["last_run"] = datetime.now(timezone.utc).isoformat()
    save_state(state)
    return True


if __name__ == "__main__":
    run_one()
