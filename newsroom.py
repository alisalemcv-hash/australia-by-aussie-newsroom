import os
import json
import re
import html
import hashlib
from datetime import datetime, timezone

import requests
import feedparser
from bs4 import BeautifulSoup


# ============================================================
# AUSTRALIA BY AUSSIE — AUTOMATED NEWSROOM
# ============================================================

GUARDIAN_RSS = "https://www.theguardian.com/australia-news/rss"
STATE_FILE = "state.json"

AI_API_KEY = os.environ["AI_API_KEY"]
WP_URL = os.environ["WP_URL"].rstrip("/")
WP_USERNAME = os.environ["WP_USERNAME"]
WP_APP_PASSWORD = os.environ["WP_APP_PASSWORD"]

# Current stable Gemini Flash model
GEMINI_MODEL = "gemini-3.6-flash"


MASTER_PROMPT = r"""
🇦🇺 AUSTRALIA BY AUSSIE
MASTER NEWSROOM PROMPT 2026

You are the senior journalist, researcher and fact-checker for Australia By Aussie.

Write in natural Australian English.

PRIORITY:

SOURCE ACCURACY
→ VERIFIED FACTS
→ ORIGINAL WRITING
→ IMPORTANT DETAILS
→ CLEAR PRESENTATION

RESEARCH

Research the story using live web sources before writing.

Treat the supplied material as a lead.

Find the ORIGINAL / PRIMARY SOURCE whenever possible.

Prioritise:

• Government
• Police
• Courts
• Regulators
• Official organisations
• Official statements
• Direct statements
• Reputable Australian media

Use reputable secondary sources to confirm facts when necessary.

Verify all important:

• Names
• Ages
• Dates
• Times
• Locations
• Numbers
• Statistics
• Dollar amounts
• Events
• Statements
• Quotes
• Current status
• What happens next

Do not include information simply because it appears in the supplied article.

If an important claim cannot be verified, do not present it as fact.

ORIGINAL ARTICLE

Write a completely ORIGINAL Australia By Aussie article.

Do not copy, translate or closely rewrite the source.

Use the verified facts and write the story independently.

Include everything a reader genuinely needs to understand the story.

Do NOT add:

• Filler
• Generic introductions
• Repeated information
• Unnecessary background
• Speculation
• Unverified claims

Every paragraph must provide useful information.

QUOTES

Use important VERIFIED direct quotes when available.

Quotes must come from the original or reliable source.

Use exact verified wording.

Clearly identify who said it.

Never invent, reconstruct or alter a quote.

If exact wording cannot be verified, paraphrase it without quotation marks.

VERIFICATION

Give:

VERIFICATION STATUS:
VERIFIED / PARTIALLY VERIFIED / DEVELOPING / INSUFFICIENT VERIFIED INFORMATION

If the story is not sufficiently verified:

INSUFFICIENT VERIFIED INFORMATION — DO NOT PUBLISH

CATEGORY

Use ONLY ONE:

Australia
Business
Cost of Living
Crime & Courts
Explainers
Life
Politics
World

WEBSITE

HEADLINE
Maximum 9 English words.

CATEGORY
One approved category.

WHY
One short explanation.

EXCERPT
EXACTLY 25 ENGLISH WORDS.

TAG
Exactly ONE relevant WordPress tag.

IMAGE
Provide:
ALT TEXT
TITLE
CAPTION
DESCRIPTION

ARTICLE
Complete original English article.

Then complete Arabic translation.

FACEBOOK / INSTAGRAM

Maximum 2,000 English characters INCLUDING spaces.

Target 1,700–1,950 characters.

The reader should understand the actual story without opening the website.

End with:

👉 Have Your Say

Then ONE specific YES / NO question.

VIDEO

VIDEO TITLE

VOICEOVER

Complete news voiceover based only on verified information.

VIDEO CAPTION

Immediately followed by exactly three hashtags.

One MUST be:

#AustraliaByAussies

FINAL RULE

NO FILLER.
NO SPECULATION.
NO INVENTED FACTS.
NO INVENTED QUOTES.
NO COPIED ARTICLES.

EXCERPT = EXACTLY 25 ENGLISH WORDS.

FACEBOOK / INSTAGRAM = MAXIMUM 2,000 ENGLISH CHARACTERS.
"""


# ============================================================
# HELPERS
# ============================================================

def load_state():
    if not os.path.exists(STATE_FILE):
        return {"processed": []}

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, dict):
            return {"processed": []}

        data.setdefault("processed", [])
        return data

    except Exception:
        return {"processed": []}


def save_state(state):
    # Keep state small
    state["processed"] = state.get("processed", [])[-200:]

    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def clean_text(value):
    if not value:
        return ""

    value = BeautifulSoup(
        str(value),
        "html.parser"
    ).get_text(" ", strip=True)

    value = html.unescape(value)
    value = re.sub(r"\s+", " ", value)

    return value.strip()


def article_id(url):
    return hashlib.sha256(
        url.encode("utf-8")
    ).hexdigest()[:20]


def get_feed():

    response = requests.get(
        GUARDIAN_RSS,
        timeout=30,
        headers={
            "User-Agent": "AustraliaByAussie-Newsroom/1.0"
        }
    )

    response.raise_for_status()

    return feedparser.parse(response.content)


def get_article_page(url):

    response = requests.get(
        url,
        timeout=30,
        headers={
            "User-Agent": "AustraliaByAussie-Newsroom/1.0"
        }
    )

    response.raise_for_status()

    soup = BeautifulSoup(
        response.text,
        "html.parser"
    )

    title = ""
    description = ""
    image_url = ""

    og_title = soup.find(
        "meta",
        property="og:title"
    )

    og_description = soup.find(
        "meta",
        property="og:description"
    )

    og_image = soup.find(
        "meta",
        property="og:image"
    )

    if og_title:
        title = og_title.get(
            "content",
            ""
        )

    if og_description:
        description = og_description.get(
            "content",
            ""
        )

    if og_image:
        image_url = og_image.get(
            "content",
            ""
        )

    # Fallback image
    if not image_url:

        image = soup.find(
            "meta",
            attrs={
                "name": "twitter:image"
            }
        )

        if image:
            image_url = image.get(
                "content",
                ""
            )

    # Extract useful page text
    paragraphs = []

    for p in soup.find_all("p"):

        text = clean_text(
            p.get_text(
                " ",
                strip=True
            )
        )

        if text and len(text) > 40:
            paragraphs.append(text)

    page_text = "\n".join(
        paragraphs[:80]
    )

    return {
        "title": clean_text(title),
        "description": clean_text(description),
        "image_url": image_url,
        "text": page_text
    }


# ============================================================
# GEMINI
# ============================================================

def ask_gemini(story):

    endpoint = (
        "https://generativelanguage.googleapis.com/v1beta/"
        f"models/{GEMINI_MODEL}:generateContent"
    )

    prompt = f"""
{MASTER_PROMPT}

IMPORTANT AUTOMATION RULES:

1. The supplied Guardian article is ONLY a lead.
2. You MUST independently verify important facts using Google Search.
3. Prefer primary/official sources.
4. Do not rely on the Guardian article alone.
5. Never invent a quote.
6. Only use a direct quote if its exact wording can be verified.
7. If there is not enough reliable information, mark the story:
   INSUFFICIENT VERIFIED INFORMATION — DO NOT PUBLISH
8. Do not publish stories about rumours or unsupported claims.
9. Return ONLY valid JSON.
10. Do not wrap the JSON in Markdown fences.
11. Do not include commentary outside the JSON object.

SOURCE LEAD:

Title:
{story["title"]}

URL:
{story["url"]}

Description:
{story["description"]}

Source page text:
{story["text"]}

Return this JSON structure:

{{
  "verification": {{
    "status": "",
    "confirmed": [],
    "not_confirmed": []
  }},

  "publish": true,

  "website": {{
    "headline": "",
    "arabic_headline": "",
    "category": "",
    "why": "",
    "excerpt": "",
    "arabic_excerpt": "",
    "tag": "",
    "alt_text": "",
    "arabic_alt_text": "",
    "image_title": "",
    "arabic_image_title": "",
    "caption": "",
    "arabic_caption": "",
    "description": "",
    "arabic_description": "",
    "article_html": "",
    "arabic_article_html": ""
  }},

  "social": {{
    "english": "",
    "arabic": ""
  }},

  "video": {{
    "title": "",
    "arabic_title": "",
    "voiceover": "",
    "arabic_voiceover": "",
    "caption": "",
    "hashtags": []
  }}
}}

The article_html must contain the complete original English article.

The arabic_article_html must contain the complete Arabic translation.

The English Facebook/Instagram post MUST be under 2,000 characters including spaces.

The excerpt MUST contain exactly 25 English words.

The hashtags array MUST contain exactly 3 hashtags.

One hashtag MUST be #AustraliaByAussies.

Do not put hashtags anywhere else.

Do not add information that cannot be verified.
"""

    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {
                        "text": prompt
                    }
                ]
            }
        ],

        # Gemini 3.6 Flash supports Google Search grounding.
        "tools": [
            {
                "google_search": {}
            }
        ],

        "generationConfig": {
            # Keep JSON output.
            "responseMimeType": "application/json"
        }
    }

    try:

        response = requests.post(
            endpoint,

            # Use the API key in the request header.
            # This is the recommended authentication style.
            headers={
                "x-goog-api-key": AI_API_KEY,
                "Content-Type": "application/json"
            },

            json=payload,
            timeout=180
        )

        response.raise_for_status()

    except requests.exceptions.HTTPError as e:

        try:
            error_data = response.json()
            error_message = json.dumps(
                error_data,
                ensure_ascii=False
            )
        except Exception:
            error_message = response.text[:4000]

        raise RuntimeError(
            f"Gemini API HTTP error "
            f"{response.status_code}:\n"
            f"{error_message}"
        ) from e

    except requests.exceptions.RequestException as e:

        raise RuntimeError(
            f"Gemini API request failed: {e}"
        ) from e

    data = response.json()

    try:

        text = (
            data["candidates"][0]
            ["content"]["parts"][0]
            ["text"]
        )

    except Exception:

        raise RuntimeError(
            "Gemini returned an unexpected response:\n"
            + json.dumps(
                data,
                ensure_ascii=False
            )[:4000]
        )

    text = text.strip()

    # Safety fallback in case JSON is returned inside Markdown.
    if text.startswith("```"):

        text = re.sub(
            r"^```json\s*",
            "",
            text,
            flags=re.IGNORECASE
        )

        text = re.sub(
            r"\s*```$",
            "",
            text
        )

    # Remove accidental leading/trailing whitespace.
    text = text.strip()

    try:

        return json.loads(text)

    except json.JSONDecodeError as e:

        raise RuntimeError(
            "Gemini returned invalid JSON.\n\n"
            + text[:4000]
        ) from e


# ============================================================
# WORDPRESS
# ============================================================

def wp_auth():
    return (
        WP_USERNAME,
        WP_APP_PASSWORD
    )


def upload_image(
    image_url,
    filename,
    alt_text
):

    response = requests.get(
        image_url,
        timeout=60,
        headers={
            "User-Agent":
                "AustraliaByAussie-Newsroom/1.0"
        }
    )

    response.raise_for_status()

    content_type = response.headers.get(
        "Content-Type",
        "image/jpeg"
    )

    if "image" not in content_type:
        content_type = "image/jpeg"

    media_endpoint = (
        f"{WP_URL}/wp-json/wp/v2/media"
    )

    upload = requests.post(
        media_endpoint,
        auth=wp_auth(),

        headers={
            "Content-Disposition":
                f'attachment; filename="{filename}"',

            "Content-Type":
                content_type
        },

        data=response.content,
        timeout=90
    )

    upload.raise_for_status()

    media = upload.json()

    media_id = media["id"]

    # Update image metadata
    metadata_response = requests.post(
        f"{media_endpoint}/{media_id}",
        auth=wp_auth(),

        json={
            "alt_text": alt_text
        },

        timeout=30
    )

    metadata_response.raise_for_status()

    return media_id


def publish_post(
    content,
    featured_media
):

    endpoint = (
        f"{WP_URL}/wp-json/wp/v2/posts"
    )

    response = requests.post(
        endpoint,
        auth=wp_auth(),

        json={
            "title": content["headline"],
            "content": content["article_html"],
            "excerpt": content["excerpt"],
            "status": "publish",
            "featured_media": featured_media
        },

        timeout=90
    )

    response.raise_for_status()

    return response.json()


# ============================================================
# VALIDATION
# ============================================================

def count_words(text):

    return re.findall(
        r"\b[\w’'-]+\b",
        text,
        flags=re.UNICODE
    )


def validate_story(result):

    website = result.get(
        "website",
        {}
    )

    social = result.get(
        "social",
        {}
    )

    video = result.get(
        "video",
        {}
    )

    errors = []

    headline = website.get(
        "headline",
        ""
    )

    excerpt = website.get(
        "excerpt",
        ""
    )

    tag = website.get(
        "tag",
        ""
    )

    category = website.get(
        "category",
        ""
    )

    facebook = social.get(
        "english",
        ""
    )

    hashtags = video.get(
        "hashtags",
        []
    )

    allowed_categories = {
        "Australia",
        "Business",
        "Cost of Living",
        "Crime & Courts",
        "Explainers",
        "Life",
        "Politics",
        "World"
    }

    if len(headline.split()) > 9:

        errors.append(
            "Headline exceeds 9 words."
        )

    if len(count_words(excerpt)) != 25:

        errors.append(
            "Excerpt is not exactly 25 words: "
            f"{len(count_words(excerpt))}"
        )

    if not tag:

        errors.append(
            "Missing WordPress tag."
        )

    if category not in allowed_categories:

        errors.append(
            f"Invalid category: {category}"
        )

    if len(facebook) > 2000:

        errors.append(
            "Facebook post exceeds 2,000 "
            f"characters: {len(facebook)}"
        )

    if "👉 Have Your Say" not in facebook:

        errors.append(
            "Facebook post is missing "
            "Have Your Say."
        )

    if len(hashtags) != 3:

        errors.append(
            "Hashtag count is not exactly 3."
        )

    if "#AustraliaByAussies" not in hashtags:

        errors.append(
            "Missing #AustraliaByAussies."
        )

    if errors:

        raise ValueError(
            "Validation failed:\n"
            + "\n".join(errors)
        )


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        "=============================================="
    )

    print(
        "Australia By Aussie Automated Newsroom"
    )

    print(
        "=============================================="
    )

    print(
        "Gemini model:",
        GEMINI_MODEL
    )

    state = load_state()

    processed = set(
        state.get(
            "processed",
            []
        )
    )

    print(
        "Checking Guardian Australia RSS..."
    )

    feed = get_feed()

    if not feed.entries:

        print(
            "No Guardian stories found."
        )

        return

    candidates = []

    for entry in feed.entries[:25]:

        url = entry.get(
            "link",
            ""
        ).strip()

        if not url:
            continue

        key = article_id(url)

        if key in processed:
            continue

        title = clean_text(
            entry.get(
                "title",
                ""
            )
        )

        description = clean_text(
            entry.get(
                "summary",
                ""
            )
        )

        candidates.append(
            {
                "id": key,
                "url": url,
                "title": title,
                "description": description
            }
        )

    if not candidates:

        print(
            "No new stories."
        )

        return

    print(
        f"Found {len(candidates)} new candidates."
    )

    # Process only one story per run.
    # This prevents excessive API usage.
    story = candidates[0]

    print("Selected:")
    print(story["title"])

    print(
        "Opening source page..."
    )

    page = get_article_page(
        story["url"]
    )

    story.update(page)

    if not story.get(
        "image_url"
    ):

        print(
            "No image found. Skipping story."
        )

        processed.add(
            story["id"]
        )

        state["processed"] = list(
            processed
        )

        save_state(state)

        return

    print("Image found.")

    print(
        story["image_url"]
    )

    print(
        "Sending story to Gemini + Google Search..."
    )

    result = ask_gemini(
        story
    )

    verification = result.get(
        "verification",
        {}
    )

    status = verification.get(
        "status",
        ""
    )

    print(
        "Verification status:",
        status
    )

    if not result.get(
        "publish",
        False
    ):

        print(
            "Story was not approved for publishing."
        )

        processed.add(
            story["id"]
        )

        state["processed"] = list(
            processed
        )

        save_state(state)

        return

    if status == (
        "INSUFFICIENT VERIFIED INFORMATION"
    ):

        print(
            "Insufficient verified information. "
            "Not publishing."
        )

        processed.add(
            story["id"]
        )

        state["processed"] = list(
            processed
        )

        save_state(state)

        return

    validate_story(
        result
    )

    website = result["website"]

    print(
        "Uploading source image to WordPress..."
    )

    extension = ".jpg"

    try:

        head_response = requests.head(
            story["image_url"],
            timeout=30,
            headers={
                "User-Agent":
                    "AustraliaByAussie-Newsroom/1.0"
            },

            allow_redirects=True
        )

        content_type = head_response.headers.get(
            "Content-Type",
            ""
        )

    except Exception:

        content_type = ""

    if "png" in content_type.lower():

        extension = ".png"

    elif "webp" in content_type.lower():

        extension = ".webp"

    filename = (
        re.sub(
            r"[^a-zA-Z0-9]+",
            "-",
            website["headline"]
        )
        .strip("-")
        .lower()
        + extension
    )

    media_id = upload_image(
        story["image_url"],
        filename,
        website["alt_text"]
    )

    print(
        "Image uploaded. Media ID:",
        media_id
    )

    print(
        "Publishing WordPress article..."
    )

    post = publish_post(
        website,
        media_id
    )

    print(
        "=============================================="
    )

    print(
        "PUBLISHED SUCCESSFULLY"
    )

    print(
        "=============================================="
    )

    print(
        "Title:",
        post.get(
            "title",
            {}
        ).get(
            "rendered"
        )
    )

    print(
        "URL:",
        post.get(
            "link"
        )
    )

    print(
        "Post ID:",
        post.get(
            "id"
        )
    )

    processed.add(
        story["id"]
    )

    state["processed"] = list(
        processed
    )

    state["last_run"] = (
        datetime.now(
            timezone.utc
        ).isoformat()
    )

    save_state(
        state
    )

    print(
        "Newsroom state saved."
    )


if __name__ == "__main__":
    main()
