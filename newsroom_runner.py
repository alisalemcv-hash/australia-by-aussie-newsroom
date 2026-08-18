import os
import re
import json
from datetime import datetime, timezone, timedelta
from urllib.parse import urljoin, quote_plus

import requests
from bs4 import BeautifulSoup

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

IMAGE:
The final featured image must be a clean editorial image with NO visible Guardian
logo, watermark, publication branding, social graphic, caption text or overlaid text.
The automation will search for a relevant clean image after the article is written.
If the story is about a person, prefer a clean portrait/photo of that person.
If it is about an event, prefer a clean photo of the event/location/subject.
Never deliberately publish a branded Guardian composite.

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
        "instagram", "masthead", "advert"
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
        "newsletter", "social graphic", "caption graphic", "logo"
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
    if width and width < 500:
        return -999
    if height and height < 250:
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


def wikimedia_images(query, limit=10):
    """Find reusable Wikimedia Commons images for the subject."""
    api = "https://commons.wikimedia.org/w/api.php"
    params = {
        "action": "query",
        "generator": "search",
        "gsrsearch": query,
        "gsrnamespace": 6,
        "gsrlimit": limit,
        "prop": "imageinfo",
        "iiprop": "url|extmetadata",
        "iiurlwidth": 1600,
        "format": "json",
    }
    try:
        response = requests.get(api, params=params, timeout=40, headers={"User-Agent": "AustraliaByAussie-Newsroom/1.0"})
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
        results.append({
            "url": url,
            "title": title,
            "description": clean_text(meta.get("ImageDescription", {}).get("value", "")),
            "artist": clean_text(meta.get("Artist", {}).get("value", "")),
            "license": clean_text(meta.get("LicenseShortName", {}).get("value", "")),
        })
    return results


def choose_clean_image(story, result):
    """Prefer a clean Guardian article photo; otherwise search Wikimedia Commons.

    This function intentionally does not return a Guardian branded social image.
    It also never makes image availability a reason to skip an otherwise approved story.
    """
    # 1. Clean image embedded in the Guardian article.
    guardian = guardian_clean_images(story["url"])
    if guardian:
        print("IMAGE SOURCE: clean Guardian article-body image")
        return guardian[0], "Guardian Australia article image"

    website = result.get("website", {})
    search_terms = [
        website.get("headline", ""),
        story.get("title", ""),
        story.get("summary", ""),
    ]
    query = " ".join(x for x in search_terms if x)[:240]

    # 2. Wikimedia Commons: use the article headline/subject rather than a generic image.
    commons = wikimedia_images(query, limit=12)
    if not commons:
        commons = wikimedia_images(story.get("title", "Australia"), limit=12)

    if commons:
        chosen = commons[0]
        print("IMAGE SOURCE: Wikimedia Commons")
        print("Wikimedia image:", chosen["title"])
        return chosen["url"], f"Wikimedia Commons — {chosen['title']}"

    # 3. Category-specific clean fallback. This keeps an important story publishable
    # even if a particular subject has no searchable image.
    category = website.get("category", "Australia")
    fallback_queries = {
        "Politics": "Australian Parliament Canberra",
        "Business": "Australia business Sydney",
        "Finance": "Reserve Bank Australia Sydney",
        "Cost of Living": "Australian supermarket shopping",
        "Life": "Australia Sydney people",
        "World": "Australia world map",
        "Australia": "Sydney Australia city",
    }
    commons = wikimedia_images(fallback_queries.get(category, "Australia"), limit=8)
    if commons:
        chosen = commons[0]
        print("IMAGE SOURCE: Wikimedia Commons category fallback")
        return chosen["url"], f"Wikimedia Commons — {chosen['title']}"

    raise RuntimeError("IMAGE_SEARCH_FAILED: No clean image source was available.")


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

            # Write the story first so the image search can use the final headline and subject.
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
            # A bad image/source/model response must not stop the other slots in the run.
            print("SKIPPED CANDIDATE:", story["title"])
            print("Reason:", exc)
            continue

    print("============================================================")
    print(f"RUN COMPLETE: published {published_count}/{MAX_POSTS_PER_RUN} articles.")
    print(f"Attempted candidates: {attempted_count}")
    return True


if __name__ == "__main__":
    run_one()
