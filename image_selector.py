import re


def _tokens(text):
    return {x for x in re.findall(r"[a-z0-9]+", str(text or "").lower()) if len(x) >= 4}


def _score(candidate, story_text, headline):
    title = str(candidate.get("title", ""))
    description = str(candidate.get("description", ""))
    haystack = f"{title} {description}".lower()
    target = _tokens(f"{headline} {story_text}")
    score = 0

    # Exact subject matches are strongly preferred.
    for token in target:
        if token in haystack:
            score += 8 if len(token) >= 7 else 4

    # Reject obvious logos, diagrams and generic unrelated media unless the
    # article itself is specifically about that item.
    low = haystack
    bad = ("logo", "masthead", "flag of", "map of the world", "social media")
    if any(x in low for x in bad):
        score -= 30

    return score


def choose_clean_image(story, result):
    """Select a semantically relevant, clean Wikimedia Commons image.

    Guardian/SBS article images are deliberately never used. A weak or unrelated
    image is worse than delaying publication, so there is no generic category
    fallback.
    """
    website = result.get("website", {})
    headline = website.get("headline", "")
    story_text = " ".join([
        story.get("title", ""),
        story.get("summary", ""),
        headline,
        website.get("tag", ""),
    ])

    # Search several increasingly focused queries. Exact named subjects get first
    # priority, then the headline, then the article title.
    queries = []
    for q in [headline, story.get("title", ""), website.get("tag", "")]:
        q = str(q or "").strip()
        if q and q not in queries:
            queries.append(q[:180])

    candidates = []
    seen = set()
    for query in queries:
        for item in base.wikimedia_images(query, limit=15):
            url = item.get("url", "")
            if not url or url in seen:
                continue
            seen.add(url)
            score = _score(item, story_text, headline)
            candidates.append((score, item))

    if not candidates:
        raise RuntimeError("IMAGE_SEARCH_FAILED: No Wikimedia Commons results for the story subject.")

    candidates.sort(key=lambda pair: pair[0], reverse=True)
    score, chosen = candidates[0]

    # Require an actual subject match. Never silently publish an unrelated
    # decorative image just because a search returned something.
    if score < 8:
        raise RuntimeError(
            f"IMAGE_SEARCH_FAILED: No sufficiently relevant clean image found; best score={score}."
        )

    print("IMAGE SOURCE: Wikimedia Commons — relevance verified")
    print("IMAGE SCORE:", score)
    print("Wikimedia image:", chosen.get("title", ""))
    return chosen["url"], f"Wikimedia Commons — {chosen.get('title', '')}"
