import re
from datetime import datetime, timezone, timedelta
import feedparser
import requests

import newsroom_runner as base

GUARDIAN_RSS = "https://www.theguardian.com/australia-news/rss"
SBS_RSS = "https://www.sbs.com.au/news/topic/australia/feed"
BUCKETS = [1, 2, 3, 4, 6, 8, 12, 18, 24]
MAX_AGE = timedelta(hours=24)
MAX_POSTS = 10

ROUTER_PROMPT = r"""
You are the senior journalist and fact-checker for Australia By Aussie.

Write in natural Australian English. Every publishable field must be English only.
The lead may come from Guardian Australia or SBS News Australia. Treat the supplied
material as a news lead, not text to copy. Write a completely original article.
Do not invent facts or quotes. If a quote is not present in the supplied material,
do not create one.

AUSTRALIA-ONLY:
Publish Australian news only. Reject stories that are primarily about another
country unless the Australian government, Australian people, Australian businesses,
or Australia is materially central to the story.

CATEGORY:
Choose exactly one: Australia, Politics, Business, Cost of Living, Life, World, Finance.
Politics normally applies to Albanese, ministers, cabinet, parliament, federal policy,
Labor/Coalition and political disputes. Use Finance for markets/rates/banks/investing,
Cost of Living for household affordability, Business for companies/industry, Life for
health/community/education/sport/everyday life, World only when an international story
is materially relevant to Australia, otherwise Australia.

IMAGE:
Do not request or use a Guardian or SBS image. The runner will attach a separate
clean, licence-filtered Wikimedia Commons image. If the story is about a person,
the image should preferably depict that person; otherwise depict the event, place
or subject.

Headline: maximum 9 English words.
Excerpt: exactly 25 English words.
Tag: exactly one relevant English WordPress tag.
Facebook/Instagram: English only, under 2,000 characters, ending with
👉 Have Your Say and one YES/NO question.
Return valid JSON only using the schema requested by the application.
"""

base.ask_openrouter_original.__globals__["MASTER_PROMPT"] = ROUTER_PROMPT


def clean_title_key(title):
    text = (title or "").lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    stop = {"a", "an", "the", "and", "of", "to", "in", "on", "for", "as", "at"}
    return " ".join(w for w in text.split() if w not in stop)


def parse_time(entry):
    for key in ("published_parsed", "updated_parsed"):
        value = entry.get(key)
        if value:
            try:
                return datetime(*value[:6], tzinfo=timezone.utc)
            except Exception:
                pass
    return None


def fetch_feed(url, source):
    try:
        response = requests.get(
            url,
            timeout=30,
            headers={"User-Agent": "AustraliaByAussie-Newsroom/1.0"},
        )
        response.raise_for_status()
        feed = feedparser.parse(response.content)
    except Exception as exc:
        print(f"{source} RSS discovery failed:", exc)
        return []

    stories = []
    for entry in feed.entries:
        published = parse_time(entry)
        if not published:
            continue
        title = base.clean_text(entry.get("title", ""))
        summary = base.clean_text(entry.get("summary", "") or entry.get("description", ""))
        link = (entry.get("link") or "").strip()
        if not title or not link:
            continue
        stories.append({
            "source": source,
            "id": f"{source.lower()}:{base.article_id(link)}",
            "url": link,
            "title": title,
            "summary": summary,
            "published": published,
        })
    return stories


def discover_all():
    now = datetime.now(timezone.utc)
    all_items = fetch_feed(GUARDIAN_RSS, "Guardian Australia")
    all_items += fetch_feed(SBS_RSS, "SBS News Australia")

    recent = []
    for item in all_items:
        age = now - item["published"]
        if timedelta(0) <= age <= MAX_AGE:
            recent.append(item)

    merged = {}
    for item in sorted(recent, key=lambda x: x["published"], reverse=True):
        key = clean_title_key(item["title"])
        if key and key not in merged:
            merged[key] = item
    return list(merged.values())


def choose_candidates(state):
    items = discover_all()
    processed = set(state.get("processed", []))
    published_titles = set(state.get("published_titles", []))

    for item in items:
        score, matched = base.priority_score(item)
        item["priority_score"] = score
        item["priority_matches"] = matched

    available = [
        x for x in items
        if x["id"] not in processed
        and clean_title_key(x["title"]) not in published_titles
    ]

    now = datetime.now(timezone.utc)
    selected = []
    selected_keys = set()

    for hours in BUCKETS:
        bucket = [
            x for x in available
            if 0 <= (now - x["published"]).total_seconds() <= hours * 3600
        ]
        bucket.sort(key=lambda x: (x["priority_score"], x["published"]), reverse=True)
        for item in bucket:
            key = clean_title_key(item["title"])
            if key in selected_keys:
                continue
            selected.append(item)
            selected_keys.add(key)
            if len(selected) >= MAX_POSTS:
                break
        print(f"TIME BUCKET {hours}h: {len(bucket)} eligible / {len(selected)} selected")
        if len(selected) >= MAX_POSTS:
            break
    return selected


def feed_lead(story):
    return {
        "title": story["title"],
        "description": story["summary"],
        "text": story["summary"],
    }


def commons_images(query, limit=20):
    api = "https://commons.wikimedia.org/w/api.php"
    params = {
        "action": "query",
        "generator": "search",
        "gsrsearch": query,
        "gsrnamespace": 6,
        "gsrlimit": limit,
        "prop": "imageinfo",
        "iiprop": "url|extmetadata|size",
        "iiurlwidth": 1600,
        "format": "json",
    }
    try:
        r = requests.get(api, params=params, timeout=40,
                         headers={"User-Agent": "AustraliaByAussie-Newsroom/1.0"})
        r.raise_for_status()
        data = r.json()
    except Exception as exc:
        print("Wikimedia search failed:", exc)
        return []

    out = []
    for page in data.get("query", {}).get("pages", {}).values():
        info = (page.get("imageinfo") or [{}])[0]
        url = info.get("thumburl") or info.get("url")
        if not url:
            continue
        meta = info.get("extmetadata", {})
        license_name = base.clean_text(meta.get("LicenseShortName", {}).get("value", ""))
        license_url = meta.get("LicenseUrl", {}).get("value", "")
        artist = base.clean_text(meta.get("Artist", {}).get("value", ""))
        desc = base.clean_text(meta.get("ImageDescription", {}).get("value", ""))
        title = base.clean_text(page.get("title", ""))
        searchable = f"{title} {desc}".lower()
        if any(bad in searchable for bad in (
            "logo", "watermark", "screenshot", "poster", "infographic",
            "social media graphic", "guardian", "sbs news", "collage"
        )):
            continue
        lic = license_name.lower()
        if "noncommercial" in lic or "non-commercial" in lic:
            continue
        if license_name and not any(x in lic for x in (
            "public domain", "cc0", "cc by", "cc-by", "cc by-sa", "cc-by-sa"
        )):
            continue
        score = 0
        for word in [w for w in re.findall(r"[a-z0-9]+", query.lower()) if len(w) > 3]:
            if word in searchable:
                score += 2
        if "cc by" in lic or "cc-by" in lic:
            score += 1
        out.append({"url": url, "title": title, "license": license_name,
                    "license_url": license_url, "artist": artist,
                    "description": desc, "score": score})
    out.sort(key=lambda x: x["score"], reverse=True)
    return out


def choose_image(story, result):
    website = result.get("website", {})
    query = " ".join([
        website.get("headline", ""),
        story.get("title", ""),
        story.get("summary", ""),
    ])[:220]
    images = commons_images(query, 20)
    if not images:
        category_queries = {
            "Politics": "Australian Parliament politicians",
            "Business": "Australia business industry",
            "Finance": "Australia finance Reserve Bank",
            "Cost of Living": "Australia supermarket household",
            "Life": "Australia community people",
            "World": "Australia international relations",
            "Australia": "Australia news",
        }
        images = commons_images(category_queries.get(website.get("category", "Australia"), "Australia news"), 20)
    if not images:
        raise RuntimeError("IMAGE_SEARCH_FAILED: no suitable licensed Wikimedia Commons image")
    chosen = images[0]
    credit = f"Wikimedia Commons — {chosen['title']}"
    if chosen["artist"]:
        credit += f" — {chosen['artist']}"
    if chosen["license"]:
        credit += f" — {chosen['license']}"
    print("IMAGE SOURCE:", credit)
    return chosen["url"], credit


def run_one():
    state = base.load_state()
    selected = choose_candidates(state)
    print("=" * 60)
    print(f"SELECTED {len(selected)}/{MAX_POSTS} articles for this run")

    published_count = 0
    attempted_count = 0
    processed = set(state.get("processed", []))
    published_titles = set(state.get("published_titles", []))

    for story in selected:
        attempted_count += 1
        print("-" * 60)
        print("SOURCE:", story["source"])
        print("TITLE:", story["title"])
        print("AGE:", datetime.now(timezone.utc) - story["published"])
        print("PRIORITY:", story["priority_score"], story["priority_matches"])
        try:
            story.update(feed_lead(story))
            result = base.ask_openrouter_original(story, [])
            result = base.clean_english_result(result)
            verification = result.get("verification", {})
            status = verification.get("status", "")
            if not result.get("publish", False) or status == "INSUFFICIENT VERIFIED INFORMATION":
                raise RuntimeError(f"NO_PUBLICATION: publish={result.get('publish', False)}, status={status!r}")
            base.validate_story(result)
            website = result["website"]
            image_url, image_credit = choose_image(story, result)
            filename = re.sub(r"[^a-zA-Z0-9]+", "-", website["headline"]).strip("-").lower() + ".jpg"
            media_id = base.upload_external_image(image_url, filename, website["alt_text"], image_credit)
            post = base.publish_post(website, media_id)
            post_id = post.get("id")
            if not post_id or post.get("status") != "publish":
                raise RuntimeError(f"WORDPRESS_PUBLISH_FAILED: id={post_id!r}, status={post.get('status')!r}")

            title_key = clean_title_key(story["title"])
            processed.add(story["id"])
            published_titles.add(title_key)
            state["processed"] = list(processed)
            state["published_titles"] = list(published_titles)
            state["last_run"] = datetime.now(timezone.utc).isoformat()
            base.save_state(state)
            published_count += 1
            print("PUBLISHED SUCCESSFULLY:", post_id, post.get("link"))
        except Exception as exc:
            print("SKIPPED CANDIDATE:", story["title"])
            print("Reason:", exc)
            continue

    print("=" * 60)
    print(f"RUN COMPLETE: published {published_count}/{MAX_POSTS} articles.")
    print(f"ATTEMPTED: {attempted_count}")
    if published_count == 0:
        raise RuntimeError("NO_PUBLICATIONS: run completed without publishing an article. See the errors above.")
    return True


if __name__ == "__main__":
    run_one()
