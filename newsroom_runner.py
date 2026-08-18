import os, re, json
from datetime import datetime, timezone, timedelta
from urllib.parse import urljoin

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
upload_image = ns["upload_image"]
publish_post = ns["publish_post"]

GUARDIAN_RSS_FEEDS = [
    "https://www.theguardian.com/australia-news/rss",
]
MAX_AGE = timedelta(hours=24)
MAX_POSTS_PER_RUN = 10

ENGLISH_ONLY_PROMPT = r"""
You are the senior journalist and fact-checker for Australia By Aussie.

ABSOLUTE LANGUAGE RULE:
EVERY PUBLISHABLE FIELD MUST BE ENGLISH ONLY.
Use natural Australian English.
NEVER write Arabic. Do not translate anything into Arabic.
Do not output Arabic text in any field.

SOURCE RULE:
The ONLY news source permitted for this newsroom run is The Guardian Australia,
from https://www.theguardian.com/australia-news/ .
Do not use or cite Google News, non-Guardian media, or Guardian sections outside Australia news.
The supplied Guardian Australia article is the sole source/lead.

ARTICLE RULE:
Write a completely original Australia By Aussie article in English.
Do not copy or closely rewrite The Guardian. Use verified facts from the supplied Guardian Australia article.
Do not invent facts or quotes.

CATEGORY:
Use ONLY the category Australia.

HEADLINE:
Maximum 9 English words.

EXCERPT:
Exactly 25 English words.

TAG:
Exactly one relevant English WordPress tag.

IMAGE:
The image supplied by the automation is a Guardian Australia article image selected from the article body.
It must be a clean editorial photograph/illustration with NO visible Guardian logo, watermark, caption,
overlaid text, publication branding, or social-media graphic.
Do not add a Guardian logo or any source branding to the article.

WEBSITE ARTICLE:
Complete English article only.

FACEBOOK/INSTAGRAM:
English only, maximum 2,000 characters, ending with 👉 Have Your Say and one YES/NO question.

VIDEO:
English only.

If the source is not clearly an Australia news story from The Guardian Australia, set publish=false.

Return valid JSON only.
"""

ns["MASTER_PROMPT"] = ENGLISH_ONLY_PROMPT

PRIORITY_RULES = [
    (120, ("anthony albanese", "albanese", "prime minister", "prime-minister")),
    (105, ("minister", "ministers", "cabinet", "ministerial")),
    (95, (
        "treasurer", "finance minister", "foreign minister", "defence minister",
        "health minister", "education minister", "environment minister",
        "attorney-general", "attorney general", "home affairs minister",
        "immigration minister", "housing minister", "social services minister",
        "transport minister", "communications minister", "industry minister",
        "energy minister", "resources minister", "agriculture minister",
        "employment minister", "skills minister", "assistant minister"
    )),
    (85, ("federal government", "australian government", "government announces", "government says", "government plans")),
    (75, ("politics", "political", "labor", "coalition", "parliament", "senate", "house of representatives")),
]


def parse_entry_time(entry):
    for key in ("published_parsed", "updated_parsed"):
        value = entry.get(key)
        if value:
            return datetime(*value[:6], tzinfo=timezone.utc)
    return None


def discover_guardian_stories_last_24h():
    import requests, feedparser
    now = datetime.now(timezone.utc)
    candidates = {}
    for feed_url in GUARDIAN_RSS_FEEDS:
        try:
            response = requests.get(
                feed_url,
                timeout=30,
                headers={"User-Agent": "AustraliaByAussie-Newsroom/1.0"},
            )
            response.raise_for_status()
            feed = feedparser.parse(response.content)
        except Exception as exc:
            print("Guardian Australia RSS discovery failed:", exc)
            continue

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
        if any(keyword in text for keyword in keywords):
            score = max(score, points)
            matched.extend(keyword for keyword in keywords if keyword in text)
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
    if website.get("category") != "Australia":
        raise RuntimeError(f"NO_PUBLICATION: Invalid category {website.get('category')!r}; only Australia is allowed.")
    return result


def select_clean_guardian_image(article_url, fallback_url=""):
    """Choose an article-body image instead of Guardian's branded social/OG image.

    Guardian's og:image can be a branded composite containing the Guardian logo.
    We therefore inspect the actual article HTML and prefer editorial images inside
    figure/article content. If no credible clean candidate exists, return empty so
    the story is skipped rather than publishing a branded image.
    """
    import requests
    from bs4 import BeautifulSoup

    try:
        response = requests.get(
            article_url,
            timeout=40,
            headers={"User-Agent": "AustraliaByAussie-Newsroom/1.0"},
        )
        response.raise_for_status()
    except Exception as exc:
        print("Guardian image-page request failed:", exc)
        return ""

    soup = BeautifulSoup(response.text, "html.parser")
    candidates = []
    seen = set()

    def add_candidate(url, node=None, priority=0):
        if not url:
            return
        url = urljoin(article_url, url.strip())
        if not url.startswith("https://") or url in seen:
            return
        low = url.lower()
        if any(bad in low for bad in (
            "/logo", "logo.", "/icon", "icon.", "/avatar", "/profile", "/newsletter",
            "/podcast", "/audio", "/video/", "/interactive/", "facebook", "twitter", "instagram"
        )):
            return

        text = ""
        if node:
            text = " ".join([
                str(node.get("alt", "")),
                str(node.get("title", "")),
                str(node.get("aria-label", "")),
            ]).lower()
        if any(bad in text for bad in (
            "guardian logo", "the guardian logo", "guardian masthead", "advertisement",
            "newsletter", "social graphic", "caption graphic"
        )):
            return

        width = 0
        height = 0
        if node:
            try:
                width = int(re.sub(r"[^0-9]", "", str(node.get("width", "0"))) or 0)
                height = int(re.sub(r"[^0-9]", "", str(node.get("height", "0"))) or 0)
            except Exception:
                pass
        if width and width < 500:
            return
        if height and height < 250:
            return

        seen.add(url)
        candidates.append((priority, width * height, url))

    # 1) Real article figures are preferred over metadata/social images.
    for figure in soup.find_all("figure"):
        for img in figure.find_all("img"):
            src = img.get("src")
            srcset = img.get("srcset", "")
            if srcset:
                parts = [p.strip().split(" ")[0] for p in srcset.split(",") if p.strip()]
                for part in reversed(parts[:5]):
                    add_candidate(part, img, priority=100)
            add_candidate(src, img, priority=95)

    # 2) Other article-body images.
    for img in soup.find_all("img"):
        add_candidate(img.get("src"), img, priority=70)
        srcset = img.get("srcset", "")
        if srcset:
            parts = [p.strip().split(" ")[0] for p in srcset.split(",") if p.strip()]
            for part in reversed(parts[:3]):
                add_candidate(part, img, priority=65)

    # Never fall back to Guardian's og:image: it is exactly the image that caused
    # the branded-logo problem. No image is safer than a branded image.
    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)

    if candidates:
        selected = candidates[0][2]
        print("Selected clean Guardian article-body image:", selected)
        return selected

    print("No clean Guardian article-body image candidate found; skipping image.")
    return ""


def run_one():
    state = load_state()
    processed = set(state.get("processed", []))
    candidates = discover_guardian_stories_last_24h()

    for story in candidates:
        score, matched = priority_score(story)
        story["priority_score"] = score
        story["priority_matches"] = matched

    candidates.sort(
        key=lambda item: (
            item["priority_score"],
            item["published"],
        ),
        reverse=True,
    )

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
        print("Candidate priority score:", story["priority_score"])
        print("Priority matches:", ", ".join(story["priority_matches"]) or "general Australia news")
        print("Selected Guardian Australia story:", story["title"])

        try:
            page = get_article_page(story["url"])
            if not page.get("text"):
                raise RuntimeError("NO_PUBLICATION: Guardian Australia source page was unavailable.")
            story.update(page)

            # IMPORTANT: do not use og:image/twitter:image. Those can be Guardian-branded
            # composites. Select a real article-body image instead.
            image_url = select_clean_guardian_image(story["url"])
            if not image_url:
                raise RuntimeError("NO_PUBLICATION: No clean Guardian Australia article-body image was found.")
            story["image_url"] = image_url

            result = ask_openrouter_original(story, [])
            result = clean_english_result(result)
            verification = result.get("verification", {})
            status = verification.get("status", "")
            print("Verification status:", status)

            if not result.get("publish", False) or status == "INSUFFICIENT VERIFIED INFORMATION":
                raise RuntimeError(
                    f"NO_PUBLICATION: Story was not approved for publishing. "
                    f"publish={result.get('publish', False)}, status={status!r}"
                )

            validate_story(result)
            website = result["website"]
            filename = re.sub(r"[^a-zA-Z0-9]+", "-", website["headline"]).strip("-").lower() + ".jpg"
            media_id = upload_image(image_url, filename, website["alt_text"])
            print("Image uploaded. Media ID:", media_id)

            post = publish_post(website, media_id)
            post_id = post.get("id")
            post_status = post.get("status")
            if not post_id or post_status != "publish":
                raise RuntimeError(
                    f"WORDPRESS_PUBLISH_FAILED: WordPress did not confirm a published post. "
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
