import re
import newsroom_runner as base

PERSON_WORDS = {
    "person", "people", "man", "woman", "men", "women", "teen", "teenager", "child",
    "student", "teacher", "doctor", "patient", "nurse", "worker", "workers", "official",
    "minister", "senator", "politician", "mp", "mps", "leader", "premier", "prime",
    "minister", "treasurer", "ceo", "executive", "director", "chair", "chairman", "chairwoman",
    "police", "officer", "officers", "detective", "judge", "lawyer", "prosecutor", "actor",
    "actress", "singer", "author", "activist", "campaigner", "resident", "family", "families",
    "parent", "parents", "father", "mother", "father", "mother", "victim", "survivor",
    "spokesperson", "speaker", "researcher", "expert", "scientist", "professor", "academic",
    "athlete", "player", "coach", "farmer", "consumer", "customer", "refugee", "migrant",
}
BAD_WORDS = {
    "logo", "masthead", "watermark", "screenshot", "poster", "banner", "flag", "map", "infographic",
    "diagram", "chart", "graphic", "collage", "social media", "youtube thumbnail", "book cover",
    "album cover", "newspaper", "illustration", "cartoon", "icon", "advertisement", "advertising",
}
STOP = {
    "australia", "australian", "government", "says", "said", "after", "amid", "over", "with", "from",
    "into", "will", "has", "have", "that", "this", "about", "more", "new", "news", "today", "latest",
    "guardian", "report", "reports", "could", "would", "should", "under", "only", "calls", "warns",
    "warn", "slams", "backs", "faces", "fresh", "push", "plan", "plans", "deal", "laws", "law",
    "reform", "reforms", "amid", "before", "over", "across", "after", "during", "into", "from",
}


def _tokens(text):
    return {x for x in re.findall(r"[a-z0-9]+", str(text or "").lower()) if len(x) >= 4 and x not in STOP}


def _person_tokens(text):
    tokens = _tokens(text)
    return {x for x in tokens if x not in PERSON_WORDS and len(x) >= 4}


def _score(candidate, story_text, headline, title, person_required=True):
    image_title = str(candidate.get("title", ""))
    description = str(candidate.get("description", ""))
    haystack = f"{image_title} {description}".lower()
    target = _tokens(f"{headline} {title} {story_text}")
    score = 0

    for token in target:
        if token in haystack:
            score += 10 if len(token) >= 7 else 5

    if any(x in haystack for x in BAD_WORDS):
        return -1000

    person_hits = [word for word in PERSON_WORDS if re.search(rf"\b{re.escape(word)}\b", haystack)]
    if person_required and not person_hits:
        return -1000
    if person_hits:
        score += 35

    # Named-person stories are strict: the image metadata must contain the named
    # subject token(s). This prevents using a generic politician/official photo.
    named = _person_tokens(title)
    if named:
        exact_hits = [token for token in named if token in haystack]
        # Require at least one strong title token, and prefer candidates matching more.
        if not exact_hits:
            return -1000
        score += 45 * len(exact_hits)

    # For unnamed people stories, require meaningful story overlap as well as a
    # person-indicating description. Generic category photos are rejected.
    story_hits = [token for token in _tokens(f"{headline} {title}") if token in haystack]
    if len(story_hits) < 1:
        return -1000
    score += min(len(story_hits), 5) * 8

    if any(x in haystack for x in ("portrait", "headshot", "photograph", "photo", "speaking", "interview")):
        score += 20

    return score


def _queries(story, website):
    title = str(story.get("title", "")).strip()
    headline = str(website.get("headline", "")).strip()
    tag = str(website.get("tag", "")).strip()
    queries = []
    for q in (
        f"{title} portrait",
        f"{headline} portrait",
        f"{title} person",
        f"{headline} person",
        title,
    ):
        q = q.strip()
        if q and q not in queries:
            queries.append(q[:180])
    if tag:
        queries.append(f"{tag} person")
    return queries


def choose_clean_image(story, result):
    """Select a clean, relevant photograph that contains a person.

    A featured image is never accepted merely because it matches the category.
    Named people require a direct metadata match to that person. If a compliant
    person photo cannot be found, publication is rejected instead of falling back
    to an object, building, skyline, logo, graphic or unrelated person.
    """
    website = result.get("website", {})
    headline = website.get("headline", "")
    title = story.get("title", "")
    story_text = " ".join([
        title,
        story.get("summary", ""),
        story.get("text", "")[:5000],
        headline,
        website.get("excerpt", ""),
        website.get("tag", ""),
    ])

    candidates = []
    seen = set()
    for query in _queries(story, website):
        for item in base.wikimedia_images(query, limit=25):
            url = item.get("url", "")
            if not url or url in seen:
                continue
            seen.add(url)
            score = _score(item, story_text, headline, title, person_required=True)
            if score > 0:
                candidates.append((score, item))

    candidates.sort(key=lambda pair: pair[0], reverse=True)
    if not candidates:
        raise RuntimeError(
            "IMAGE_SEARCH_FAILED: No clean, directly relevant person photo found. "
            "Publication blocked rather than using a generic image."
        )

    score, chosen = candidates[0]
    # High threshold because the image requirement is mandatory.
    if score < 45:
        raise RuntimeError(
            f"IMAGE_SEARCH_FAILED: Best person-photo match was too weak (score={score}). "
            "Publication blocked."
        )

    print("IMAGE MODE: relevant person photo — mandatory")
    print("IMAGE SOURCE: Wikimedia Commons")
    print("IMAGE SCORE:", score)
    print("Wikimedia image:", chosen.get("title", ""))
    return chosen["url"], f"Wikimedia Commons — {chosen.get('title', '')}"
