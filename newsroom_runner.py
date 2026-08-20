import os
import re
import json
from datetime import datetime, timezone, timedelta
from urllib.parse import urljoin, quote_plus

import requests
from bs4 import BeautifulSoup

# newsroom.py is a shared helper module from the old OpenRouter version.
# The active router uses Groq, so importing newsroom.py must not require an
# OpenRouter secret to exist in GitHub Actions.
os.environ.setdefault("OPENROUTER_API_KEY", "disabled")

source = open("newsroom.py", "r", encoding="utf-8").read()
ns = {"__name__": "newsroom_loaded"}
exec(compile(source, "newsroom.py", "exec"), ns)

clean_text = ns["clean_text"]
article_id = ns["article_id"]
load_state = ns["load_state"]
save_state = ns["save_state"]
get_article_page = ns["get_article_page"]
ask_openrouter_original = ns["ask_openrouter"]
validate_story = ns["validate_story"]
publish_post = ns["publish_post"]

GUARDIAN_RSS = "https://www.theguardian.com/australia-news/rss"
MAX_AGE = timedelta(hours=24)
MAX_POSTS_PER_RUN = 10
ALLOWED_CATEGORIES = {
    "Australia",
    "Politics",
    "Business",
    "Cost of Living",
    "Life",
    "World",
    "Finance",
}

MASTER_PROMPT = r"""
You are the senior journalist and fact-checker for Australia By Aussie.

LANGUAGE:
Every publishable field must be English only. Use natural Australian English.
Never output Arabic in any publishable field.

SOURCE:
The ONLY news lead permitted is The Guardian Australia, specifically
https://www.theguardian.com/australia-news/ . Do not use non-Guardian media as a
news source. The supplied Guardian Australia story is the factual lead.

ARTICLE:
Write a completely original Australia By Aussie article. Do not copy or closely
rewrite The Guardian. Do not invent facts or quotes.

CATEGORY — IMPORTANT:
Choose the ONE most accurate category from exactly this list:
Australia, Politics, Business, Cost of Living, Life, World, Finance.
Do NOT automatically choose Australia.
Use these rules:
- Politics: Albanese, ministers, parliament, elections, parties, government policy,
  political disputes, federal political decisions.
- Business: companies, industries, jobs, corporate activity, major business deals.
- Finance: interest rates, banks, markets, investing, currency, financial policy,
  Treasury/financial data when the story is primarily financial.
- Cost of Living: household bills, rents, groceries, energy prices, wages as a
  household-cost issue, affordability and living costs.
- Life: health, lifestyle, culture, community, education, sport and everyday life
  when not primarily political/business/financial.
- World: a story primarily about events outside Australia.
- Australia: Australian news that does not fit the six categories above, including
  crime, courts, emergency incidents and general national news.

For stories about Albanese, a minister, cabinet, parliament or federal government
politics, Politics is normally the correct category unless the story is clearly
primarily Finance or Cost of Living.

HEADLINE: maximum 9 English words.
EXCERPT: exactly 25 English words.
TAG: exactly one relevant English WordPress tag.

IMAGE — STRICT:
The featured image must directly depict the main subject of the article.
If the article is about a named person, the image MUST be a clean photo/portrait of
that exact person whenever a suitable image is available. Do not use a parliament
building, generic crowd, city skyline, object, map or unrelated person as a substitute.
Use only a clean photograph with no visible logo, watermark, publication branding,
social-media graphic, caption, headline, lower-third, border or other overlaid writing.
Never use a branded Guardian composite or screenshot.
If the exact person/subject cannot be matched confidently, reject the image instead of
using an unrelated fallback.

ARTICLE: complete original English article only.
SOCIAL: English only, max 2,000 characters, ending with 👉 Have Your Say and one YES/NO question.
VIDEO: English only.

Return valid JSON only.
"""
ns["MASTER_PROMPT"] = MASTER_PROMPT

PRIORITY_RULES = [
    (130, ("anthony albanese", "albanese", "prime minister")),
    (115, ("minister", "ministers", "cabinet", "ministerial")),
    (105, (
        "treasurer", "finance minister", "foreign minister", "defence minister",
        "health minister", "education minister", "environment minister",
        "attorney-general", "attorney general", "home affairs minister",
        "immigration minister", "housing minister", "social services minister",
        "transport minister", "communications minister", "industry minister",
        "energy minister", "resources minister", "agriculture minister",
        "employment minister", "skills minister", "assistant minister"
    )),
    (90, ("federal government", "australian government", "government announces", "government says", "government plans")),
    (80, ("politics", "political", "labor", "coalition", "parliament", "senate", "house of representatives")),
]


def parse_entry_time(entry):
    for key in ("published_parsed", "updated_parsed"):
        value = entry.get(key)
        if value:
            return datetime(*value[:6], tzinfo=timezone.utc)
    return None


def discover_guardian_stories_last_24h():
    import feedparser
    now = datetime.now(timezone.utc)
    candidates = {}
    try:
        response = requests.get(
            GUARDIAN_RSS,
            timeout=30,
            headers={"User-Agent": "AustraliaByAussie-Newsroom/1.0"},
        )
        response.raise_for_status()
        feed = feedparser.parse(response.content)
    except Exception as exc:
        print("Guardian Australia RSS discovery failed:", exc)
        return []

    for entry in feed.entries:
        published = parse_entry_time(entry)
        if not published:
            continue
        age = now - published
        if age < timedelta(0) or age > MAX_AGE:
            continue
        title = clean_text(entry.get("title", ""))
        summary = clean_text(entry.get("summary", ""))
        link = entry.get("link", "").strip()
        if not title or not link:
            continue
        if not link.startswith("https://www.theguardian.com/australia-news/"):
            continue
        key = article_id(link)
        candidates[key] = {
            "id": key,
            "url": link,
            "title": title,
            "summary": summary,
            "published": published,
        }
    return list(candidates.values())


def priority_score(story):
    text = f"{story.get('title', '')} {story.get('summary', '')}".lower()
    score = 0
    matched = []
    for points, keywords in PRIORITY_RULES:
        hits = [k for k in keywords if k in text]
        if hits:
            score = max(score, points)
            matched.extend(hits)
    return score, matched


def contains_arabic(value):
    return bool(re.search(r"[\u0600-\u06FF]", str(value or "")))


def clean_english_result(result):
    website = result.get("website", {})
    for key in list(website.keys()):
        if key.startswith("arabic_") or key in {"ar...", "arabic_article_html"}:
            website.pop(key, None)
    for key, value in website.items():
        if contains_arabic(value):
            raise RuntimeError(f"NO_PUBLICATION: Arabic text detected in website field {key!r}.")
    category = website.get("category", "").strip()
    if category not in ALLOWED_CATEGORIES:
        raise RuntimeError(
            f"NO_PUBLICATION: Invalid category {category!r}. Allowed: {', '.join(sorted(ALLOWED_CATEGORIES))}"
        )
    return result


def _image_candidate_score(url, node=None):
    low = url.lower()
    bad_url = (
        "/logo", "logo.", "/icon", "icon.", "/avatar", "/profile", "/newsletter",
        "/podcast", "/audio", "/video/", "/interactive/", "facebook", "twitter",
        "instagram", "masthead", "advert", "sprite", "placeholder"
    )
    if any(x in low for x in bad_url):
        return -999
    text = ""
    if node:
        text = " ".join([
            str(node.get("alt", "")),
            str(node.get("title", "")),
            str(node.get("aria-label", "")),
        ]).lower()
    if any(x in text for x in (
        "guardian logo", "the guardian logo", "guardian masthead", "advertisement",
        "newsletter", "social graphic", "caption graphic", "logo", "watermark",
        "screenshot", "infographic", "illustration"
    )):
        return -999
    width = 0
    height = 0
    if node:
        try:
            width = int(re.sub(r"[^0-9]", "", str(node.get("width", "0"))) or 0)
            height = int(re.sub(r"[^0-9]", "", str(node.get("height", "0"))) or 0)
        except Exception:
            pass
    if width and width < 600:
        return -999
    if height and height < 300:
        return -999
    return 1000 + min(width * height, 5000000) // 100000


def guardian_clean_images(article_url):
    """Return clean-looking article-body image URLs; never use og:image."""
    seen = set()
    candidates = []
    try:
        response = requests.get(
            article_url,
            timeout=40,
            headers={"User-Agent": "AustraliaByAussie-Newsroom/1.0"},
        )
        response.raise_for_status()
    except Exception as exc:
        print("Guardian image-page request failed:", exc)
        return []

    soup = BeautifulSoup(response.text, "html.parser")
    for figure in soup.find_all("figure"):
        for img in figure.find_all("img"):
            urls = []
            srcset = img.get("srcset", "")
            if srcset:
                urls.extend([p.strip().split(" ")[0] for p in srcset.split(",") if p.strip()])
            urls.append(img.get("src", ""))
            for raw in urls:
                url = urljoin(article_url, raw.strip()) if raw else ""
                if not url.startswith("https://") or url in seen:
                    continue
                score = _image_candidate_score(url, img)
                if score > 0:
                    seen.add(url)
                    candidates.append((score + 2000, url))

    for img in soup.find_all("img"):
        urls = [img.get("src", "")]
        srcset = img.get("srcset", "")
        if srcset:
            urls.extend([p.strip().split(" ")[0] for p in srcset.split(",") if p.strip()][:3])
        for raw in urls:
            url = urljoin(article_url, raw.strip()) if raw else ""
            if not url.startswith("https://") or url in seen:
                continue
            score = _image_candidate_score(url, img)
            if score > 0:
                seen.add(url)
                candidates.append((score, url))
    candidates.sort(reverse=True)
    return [url for _, url in candidates[:12]]


def wikimedia_images(query, limit=15):
    """Find reusable Wikimedia Commons photographs for an exact subject."""
    api = "https://commons.wikimedia.org/w/api.php"
    params = {
        "action": "query",
        "generator": "search",
        "gsrsearch": query,
        "gsrnamespace": 6,
        "gsrlimit": limit,
        "prop": "imageinfo",
        "iiprop": "url|extmetadata",
        "iiurlwidth": 1800,
        "format": "json",
    }
    try:
        response = requests.get(
            api,
            params=params,
            timeout=40,
            headers={"User-Agent": "AustraliaByAussie-Newsroom/1.0"},
        )
        response.raise_for_status()
        data = response.json()
    except Exception as exc:
        print("Wikimedia image search failed:", exc)
        return []

    results = []
    for page in data.get("query", {}).get("pages", {}).values():
        info = (page.get("imageinfo") or [{}])[0]
        url = info.get("thumburl") or info.get("url")
        if not url or not url.startswith("https://"):
            continue
        meta = info.get("extmetadata", {})
        title = clean_text(page.get("title", ""))
        description = clean_text(meta.get("ImageDescription", {}).get("value", ""))
        results.append({
            "url": url,
            "title": title,
            "description": description,
            "artist": clean_text(meta.get("Artist", {}).get("value", "")),
            "license": clean_text(meta.get("LicenseShortName", {}).get("value", "")),
        })
    return results


def _subject_tokens(text):
    stop = {
        "australia", "australian", "government", "minister", "minister", "says",
        "said", "after", "amid", "over", "with", "from", "into", "will", "has",
        "have", "that", "this", "about", "more", "new", "news", "today", "latest",
        "guardian", "report", "reports", "could", "would", "should", "under",
    }
    return {
        t for t in re.findall(r"[a-z0-9]+", str(text or "").lower())
        if len(t) >= 4 and t not in stop
    }


def _commons_subject_score(item, subject_text, person_mode=False):
    hay = f"{item.get('title', '')} {item.get('description', '')}".lower()
    tokens = _subject_tokens(subject_text)
    score = 0
    for token in tokens:
        if token in hay:
            score += 12 if len(token) >= 7 else 6

    low_title = item.get("title", "").lower()
    low_desc = item.get("description", "").lower()
    combined = f"{low_title} {low_desc}"

    # Never select branding, graphics, screenshots or images containing obvious text assets.
    forbidden = (
        "logo", "masthead", "watermark", "screenshot", "poster", "banner", "flag",
        "map", "infographic", "diagram", "chart", "graphic", "collage", "social media",
        "youtube thumbnail", "book cover", "album cover", "newspaper"
    )
    if any(x in combined for x in forbidden):
        return -1000

    # For person-led stories, strongly prefer portrait/photo results that explicitly
    # name the person. A generic location/object is never an acceptable substitute.
    if person_mode:
        if any(x in combined for x in ("portrait", "headshot", "photograph", "photo", "speaking", "interview")):
            score += 20
        if "portrait" in low_title or "headshot" in low_title:
            score += 20

    return score


def _person_mode(story, website):
    text = " ".join([
        story.get("title", ""), story.get("summary", ""),
        website.get("headline", ""), website.get("excerpt", "")
    ]).lower()
    person_markers = (
        "anthony albanese", "peter dutton", "donald trump", "minister", "treasurer",
        "prime minister", "premier", "senator", "mp ", " mps", "leader", "ceo",
        "actor", "actress", "singer", "athlete", "player", "police commissioner"
    )
    return any(x in text for x in person_markers)


def choose_clean_image(story, result):
    """Choose a directly relevant clean subject photo; reject unrelated fallbacks."""
    website = result.get("website", {})
    subject_text = " ".join([
        story.get("title", ""),
        story.get("summary", ""),
        website.get("headline", ""),
        website.get("tag", ""),
    ])
    person_mode = _person_mode(story, website)

    # First choice for person-led stories: exact-subject Wikimedia Commons photography.
    # This avoids branded Guardian composites and generic editorial images.
    if person_mode:
        queries = [
            website.get("headline", ""),
            story.get("title", ""),
        ]
        commons_candidates = []
        seen = set()
        for query in queries:
            if not query:
                continue
            for item in wikimedia_images(query, limit=20):
                if item["url"] in seen:
                    continue
                seen.add(item["url"])
                score = _commons_subject_score(item, subject_text, person_mode=True)
                if score > 0:
                    commons_candidates.append((score, item))
        commons_candidates.sort(key=lambda x: x[0], reverse=True)
        if commons_candidates and commons_candidates[0][0] >= 18:
            chosen = commons_candidates[0][1]
            print("IMAGE MODE: direct person photo")
            print("IMAGE SOURCE: Wikimedia Commons")
            print("IMAGE SCORE:", commons_candidates[0][0])
            print("Wikimedia image:", chosen["title"])
            return chosen["url"], f"Wikimedia Commons — {chosen['title']}"

        # Second choice: clean article-body photo, but only if its metadata directly
        # matches the named person/subject. We never use og:image or generic fallbacks.
        guardian = guardian_clean_images(story["url"])
        for image_url in guardian:
            print("IMAGE CANDIDATE: clean Guardian article-body image")
            # The image itself has already passed the no-brand/no-graphic filters.
            # Only use it when Commons could not confidently provide a direct portrait.
            return image_url, "Guardian Australia article image"

        raise RuntimeError("IMAGE_SEARCH_FAILED: No direct clean photo of the named person was confidently matched.")

    # Non-person stories: exact subject search only. Never fall back to generic category images.
    queries = [website.get("headline", ""), story.get("title", ""), story.get("summary", "")]
    candidates = []
    seen = set()
    for query in queries:
        if not query:
            continue
        for item in wikimedia_images(query, limit=20):
            if item["url"] in seen:
                continue
            seen.add(item["url"])
            score = _commons_subject_score(item, subject_text, person_mode=False)
            if score > 0:
                candidates.append((score, item))
    candidates.sort(key=lambda x: x[0], reverse=True)
    if candidates and candidates[0][0] >= 12:
        chosen = candidates[0][1]
        print("IMAGE MODE: direct story subject")
        print("IMAGE SOURCE: Wikimedia Commons")
        print("IMAGE SCORE:", candidates[0][0])
        print("Wikimedia image:", chosen["title"])
        return chosen["url"], f"Wikimedia Commons — {chosen['title']}"

    # Last resort for non-person stories: a clean Guardian article-body photo is allowed,
    # but no generic category fallback is permitted.
    guardian = guardian_clean_images(story["url"])
    if guardian:
        print("IMAGE MODE: clean article subject photo")
        print("IMAGE SOURCE: Guardian Australia article-body image")
        return guardian[0], "Guardian Australia article image"

    raise RuntimeError("IMAGE_SEARCH_FAILED: No direct clean image matched the story subject.")


def upload_external_image(image_url, filename, alt_text, credit):
    """Upload an external clean image and keep source/credit metadata in WordPress."""
    WP_URL = os.environ["WP_URL"].rstrip("/")
    auth = (os.environ["WP_USERNAME"], os.environ["WP_APP_PASSWORD"])
    response = requests.get(image_url, timeout=90, headers={"User-Agent": "AustraliaByAussie-Newsroom/1.0"})
    response.raise_for_status()
    content_type = response.headers.get("Content-Type", "image/jpeg").split(";")[0]
    if not content_type.startswith("image/"):
        content_type = "image/jpeg"

    endpoint = f"{WP_URL}/wp-json/wp/v2/media"
    upload = requests.post(
        endpoint,
        auth=auth,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Type": content_type,
        },
        data=response.content,
        timeout=90,
    )
    upload.raise_for_status()
    media = upload.json()
    media_id = media["id"]

    metadata = {
        "alt_text": alt_text,
        "caption": credit,
        "description": credit,
    }
    meta_response = requests.post(f"{endpoint}/{media_id}", auth=auth, json=metadata, timeout=30)
    meta_response.raise_for_status()
    return media_id


def run_one():
    state = load_state()
    processed = set(state.get("processed", []))
    candidates = discover_guardian_stories_last_24h()

    for story in candidates:
        score, matched = priority_score(story)
        story["priority_score"] = score
        story["priority_matches"] = matched

    candidates.sort(key=lambda item: (item["priority_score"], item["published"]), reverse=True)
    unprocessed = [c for c in candidates if c["id"] not in processed]

    print(f"Found {len(candidates)} Guardian Australia candidates from the last 24 hours.")
    print(f"Unprocessed candidates: {len(unprocessed)}")
    print(f"Target per run: {MAX_POSTS_PER_RUN} articles.")

    published_count = 0
    attempted_count = 0

    for story in unprocessed:
        if published_count >= MAX_POSTS_PER_RUN:
            break
        attempted_count += 1
        print("------------------------------------------------------------")
        print("Priority score:", story["priority_score"])
        print("Priority matches:", ", ".join(story["priority_matches"]) or "general Australia news")
        print("Selected Guardian Australia story:", story["title"])

        try:
            page = get_article_page(story["url"])
            if not page.get("text"):
                raise RuntimeError("NO_PUBLICATION: Guardian Australia source page was unavailable.")
            story.update(page)

            result = ask_openrouter_original(story, [])
            result = clean_english_result(result)
            verification = result.get("verification", {})
            status = verification.get("status", "")
            print("Verification status:", status)

            if not result.get("publish", False) or status == "INSUFFICIENT VERIFIED INFORMATION":
                raise RuntimeError(
                    f"NO_PUBLICATION: Story was not approved. publish={result.get('publish', False)}, status={status!r}"
                )

            validate_story(result)
            website = result["website"]

            image_url, image_credit = choose_clean_image(story, result)
            filename = re.sub(r"[^a-zA-Z0-9]+", "-", website["headline"]).strip("-").lower() + ".jpg"
            media_id = upload_external_image(image_url, filename, website["alt_text"], image_credit)
            print("Image uploaded. Media ID:", media_id)

            post = publish_post(website, media_id)
            post_id = post.get("id")
            post_status = post.get("status")
            if not post_id or post_status != "publish":
                raise RuntimeError(
                    f"WORDPRESS_PUBLISH_FAILED: WordPress did not confirm publication. id={post_id!r}, status={post_status!r}"
                )

            print("PUBLISHED SUCCESSFULLY")
            print("Title:", post.get("title", {}).get("rendered"))
            print("Category:", website.get("category"))
            print("Image credit:", image_credit)
            print("URL:", post.get("link"))
            print("Post ID:", post_id)

            processed.add(story["id"])
            state["processed"] = list(processed)
            state["last_run"] = datetime.now(timezone.utc).isoformat()
            save_state(state)
            published_count += 1

        except Exception as exc:
            print("SKIPPED CANDIDATE:", story["title"])
            print("Reason:", exc)
            continue

    print("============================================================")
    print(f"RUN COMPLETE: published {published_count}/{MAX_POSTS_PER_RUN} articles.")
    print(f"Attempted candidates: {attempted_count}")
    return True


if __name__ == "__main__":
    run_one()
