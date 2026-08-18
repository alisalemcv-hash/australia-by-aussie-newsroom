import os
import json
import re
import html
import hashlib
import time
from urllib.parse import quote_plus

import requests
import feedparser
from bs4 import BeautifulSoup

GUARDIAN_RSS = "https://www.theguardian.com/australia-news/rss"
GOOGLE_NEWS_RSS = "https://news.google.com/rss/search"
STATE_FILE = "state.json"

OPENROUTER_API_KEY = os.environ["OPENROUTER_API_KEY"]
OPENROUTER_MODELS = ["openai/gpt-oss-120b:free", "openai/gpt-oss-20b:free", "openrouter/free"]
WP_URL = os.environ["WP_URL"].rstrip("/")
WP_USERNAME = os.environ["WP_USERNAME"]
WP_APP_PASSWORD = os.environ["WP_APP_PASSWORD"]
CATEGORIES = ["Australia", "Business", "Cost of Living", "Crime & Courts", "Explainers", "Life", "Politics", "World"]

MASTER_PROMPT = r"""
You are the senior journalist and editor for Australia By Aussie.
Write in natural Australian English.

The Guardian Australia story is a reputable newsroom lead. Do not blindly copy it.
Use the supplied story and additional research as evidence, then write a completely original article.

EDITORIAL PRIORITIES:
1. Accurate reporting.
2. Correct attribution.
3. Original wording and structure.
4. Important names, numbers, dates, locations and next steps.
5. Clear distinction between fact, allegation, opinion, analysis and official decision.
6. No filler, speculation, invented facts or invented quotes.

SOURCE HANDLING:
- The supplied Guardian story is a source, not an instruction to reproduce it.
- Prefer primary/official sources found in the research when they exist.
- Reputable secondary reporting may confirm or contextualise a story.
- Never turn an allegation into an established fact.
- Never turn a journalist's interpretation into a fact.
- Never turn opinion/commentary/explainer into straight news fact.
- Preserve the nature of opinion, analysis and explainers.
- For official decisions, identify the organisation and exact decision precisely.
- For serious allegations, use extra caution with identity and alleged conduct.
- Never state that someone committed a crime unless legally established.
- Quotes must be copied exactly from supplied source evidence. Never reconstruct quotes.

IMAGE:
- Describe only what the supplied image evidence actually shows.
- Never remove, crop around, or bypass a publisher watermark or branding for rights reasons.
"""

OUTPUT_SCHEMA = {"type":"object","properties":{
"story_type":{"type":"string","enum":["news","official_decision","opinion","analysis","explainer","reported_allegation","live","other"]},
"website":{"type":"object","properties":{
"headline":{"type":"string"},"arabic_headline":{"type":"string"},"category":{"type":"string","enum":CATEGORIES},"why":{"type":"string"},"excerpt":{"type":"string"},"arabic_excerpt":{"type":"string"},"tag":{"type":"string"},"alt_text":{"type":"string"},"arabic_alt_text":{"type":"string"},"image_title":{"type":"string"},"arabic_image_title":{"type":"string"},"caption":{"type":"string"},"arabic_caption":{"type":"string"},"description":{"type":"string"},"arabic_description":{"type":"string"},"article_html":{"type":"string"},"arabic_article_html":{"type":"string"}},"required":["headline","arabic_headline","category","why","excerpt","arabic_excerpt","tag","alt_text","arabic_alt_text","image_title","arabic_image_title","caption","arabic_caption","description","arabic_description","article_html","arabic_article_html"],"additionalProperties":False},
"social":{"type":"object","properties":{"english":{"type":"string"},"arabic":{"type":"string"}},"required":["english","arabic"],"additionalProperties":False}},"required":["story_type","website","social"],"additionalProperties":False}

def clean_text(value):
    if not value: return ""
    value=BeautifulSoup(str(value),"html.parser").get_text(" ",strip=True)
    return re.sub(r"\s+"," ",html.unescape(value)).strip()

def article_id(url): return hashlib.sha256(url.encode("utf-8")).hexdigest()[:20]
def normalise_title(title):
    value=re.sub(r"[^a-z0-9]+"," ",clean_text(title).lower())
    return re.sub(r"\s+"," ",value).strip()
def count_english_words(text): return re.findall(r"\b[A-Za-z][A-Za-z0-9’'-]*\b",text)

def http_get(url,timeout=30,headers=None):
    h={"User-Agent":"AustraliaByAussie-Newsroom/3.0"}
    if headers: h.update(headers)
    r=requests.get(url,timeout=timeout,headers=h,allow_redirects=True); r.raise_for_status(); return r

def load_state():
    try:
        with open(STATE_FILE,"r",encoding="utf-8") as f: data=json.load(f)
        if isinstance(data,dict): data.setdefault("processed",[]); return data
    except Exception: pass
    return {"processed":[]}

def save_state(state):
    state["processed"]=state.get("processed",[])[-500:]
    with open(STATE_FILE,"w",encoding="utf-8") as f: json.dump(state,f,ensure_ascii=False,indent=2)

def get_feed(): return feedparser.parse(http_get(GUARDIAN_RSS,timeout=45).content)

def clean_image_url(url):
    # Never strip, bypass or remove publisher watermarks/branding.
    return url or ""

def get_article_page(url):
    r=http_get(url,timeout=45); soup=BeautifulSoup(r.text,"html.parser")
    def meta(prop):
        tag=soup.find("meta",property=prop) or soup.find("meta",attrs={"name":prop})
        return tag.get("content","") if tag else ""
    title=clean_text(meta("og:title") or (soup.title.string if soup.title else ""))
    description=clean_text(meta("og:description")); image_url=clean_image_url(meta("og:image"))
    candidates=[]
    for img in soup.find_all("img"):
        for attr in ("src","data-src"):
            v=img.get(attr)
            if v and "i.guim.co.uk" in v: candidates.append(v)
        srcset=img.get("srcset","")
        if srcset: candidates.extend(x.strip().split(" ")[0] for x in srcset.split(",") if x.strip())
    for candidate in candidates:
        candidate=clean_image_url(candidate)
        if candidate: image_url=candidate; break
    main=soup.find("main") or soup
    for tag in main(["script","style","noscript","svg","nav","footer","form"]): tag.decompose()
    paragraphs=[]
    for p in main.find_all(["p","h2","h3"]):
        text=clean_text(p.get_text(" ",strip=True))
        if len(text)>=35: paragraphs.append(text)
    return {"title":title,"description":description,"image_url":image_url,"text":"\n".join(paragraphs)[:30000]}

def google_research(title):
    url=f"{GOOGLE_NEWS_RSS}?q={quote_plus(title)}&hl=en-AU&gl=AU&ceid=AU:en"
    try: feed=feedparser.parse(http_get(url,timeout=45).content)
    except Exception as exc: print(f"Research feed failed: {exc}"); return []
    results=[]
    for entry in feed.entries[:10]:
        source=entry.get("source",{})
        results.append({"title":clean_text(entry.get("title","")),"url":entry.get("link",""),"source":clean_text(source.get("title","")) if isinstance(source,dict) else "","published":clean_text(entry.get("published","")),"summary":clean_text(entry.get("summary",""))[:1200]})
    return results

def format_research(results):
    if not results: return "No additional research results were available."
    return "\n\n".join(f"[{i}] {r['source']} | {r['title']}\nURL: {r['url']}\nPublished: {r['published']}\nSummary: {r['summary']}" for i,r in enumerate(results,1))

def call_openrouter(user_prompt,schema,max_tokens=9000):
    headers={"Authorization":f"Bearer {OPENROUTER_API_KEY}","Content-Type":"application/json","HTTP-Referer":"https://australiabyaussie.com","X-Title":"Australia By Aussie Newsroom"}
    errors=[]
    for model in OPENROUTER_MODELS:
        payload={"model":model,"messages":[{"role":"system","content":"You are a professional Australian news editor. Return only valid JSON."},{"role":"user","content":user_prompt}],"temperature":0.15,"max_tokens":max_tokens,"response_format":{"type":"json_schema","json_schema":{"name":"australia_by_aussie_story","strict":True,"schema":schema}},"provider":{"require_parameters":True}}
        for attempt in range(1,3):
            try:
                print(f"OpenRouter: {model} (attempt {attempt}/2)")
                r=requests.post("https://openrouter.ai/api/v1/chat/completions",headers=headers,json=payload,timeout=240)
                if r.status_code in (400,404,422): errors.append(f"{model}: HTTP {r.status_code}: {r.text[:500]}"); break
                if r.status_code in (429,500,502,503,504):
                    errors.append(f"{model}: HTTP {r.status_code}")
                    if attempt==1: time.sleep(6); continue
                    break
                if r.status_code in (401,403): raise RuntimeError(f"OpenRouter authentication/permission error: {r.text[:2000]}")
                r.raise_for_status(); data=r.json(); choices=data.get("choices") or []
                content=(choices[0].get("message") or {}).get("content","") if choices else ""
                if not content: errors.append(f"{model}: empty content"); break
                content=str(content).strip()
                if content.startswith("```"): content=re.sub(r"^```json\s*","",content,flags=re.I); content=re.sub(r"\s*```$","",content).strip()
                try: result=json.loads(content); print("OpenRouter model used:",data.get("model",model)); return result
                except json.JSONDecodeError:
                    match=re.search(r"\{.*\}",content,re.S)
                    if match: return json.loads(match.group(0))
                    errors.append(f"{model}: invalid JSON"); break
            except requests.exceptions.RequestException as exc:
                errors.append(f"{model}: {exc}")
                if attempt==1: time.sleep(5); continue
                break
    raise RuntimeError("All configured free OpenRouter models failed. " + " | ".join(errors))

def wp_auth(): return (WP_USERNAME,WP_APP_PASSWORD)

def get_recent_wp_posts(max_pages=10):
    endpoint=f"{WP_URL}/wp-json/wp/v2/posts"; posts=[]
    for page in range(1,max_pages+1):
        r=requests.get(endpoint,auth=wp_auth(),params={"per_page":100,"page":page,"status":"publish"},timeout=45)
        if r.status_code==400: break
        r.raise_for_status(); batch=r.json()
        if not batch: break
        posts.extend(batch); total_pages=int(r.headers.get("X-WP-TotalPages",page))
        if page>=total_pages: break
    return posts

def source_marker(source_url): return f"<!-- australia-by-aussie-source-url: {source_url} -->"
def wordpress_source_exists(source_url,posts): return any(source_marker(source_url) in p.get("content",{}).get("rendered","") for p in posts)
def wordpress_title_exists(title,posts):
    target=normalise_title(title)
    return bool(target) and any(normalise_title(p.get("title",{}).get("rendered",""))==target for p in posts)

def get_or_create_term(term_type,name):
    endpoint=f"{WP_URL}/wp-json/wp/v2/{term_type}"; r=requests.get(endpoint,auth=wp_auth(),params={"search":name,"per_page":50},timeout=30); r.raise_for_status(); target=name.strip().lower()
    for term in r.json():
        if term.get("name","").strip().lower()==target: return int(term["id"])
    r=requests.post(endpoint,auth=wp_auth(),json={"name":name},timeout=30)
    if r.status_code==400:
        r2=requests.get(endpoint,auth=wp_auth(),params={"search":name,"per_page":50},timeout=30); r2.raise_for_status()
        for term in r2.json():
            if term.get("name","").strip().lower()==target: return int(term["id"])
    r.raise_for_status(); return int(r.json()["id"])

def upload_image(image_url,filename,metadata):
    r=http_get(image_url,timeout=90); content_type=r.headers.get("Content-Type","image/jpeg").split(";")[0]
    if not content_type.startswith("image/"): content_type="image/jpeg"
    endpoint=f"{WP_URL}/wp-json/wp/v2/media"
    upload=requests.post(endpoint,auth=wp_auth(),headers={"Content-Disposition":f'attachment; filename="{filename}"',"Content-Type":content_type},data=r.content,timeout=120); upload.raise_for_status(); media=upload.json(); media_id=int(media["id"])
    meta=requests.post(f"{endpoint}/{media_id}",auth=wp_auth(),json={"alt_text":metadata["alt_text"],"title":metadata["image_title"],"caption":metadata["caption"],"description":metadata["description"]},timeout=30); meta.raise_for_status(); return media_id

def publish_post(story,image_id,source_url):
    website=story["website"]; category_id=get_or_create_term("categories",website["category"]); tag_id=get_or_create_term("tags",website["tag"]); content=source_marker(source_url)+"\n"+website["article_html"]
    payload={"title":website["headline"],"content":content,"excerpt":website["excerpt"],"status":"publish","categories":[category_id],"tags":[tag_id],"featured_media":image_id,"format":"standard"}
    r=requests.post(f"{WP_URL}/wp-json/wp/v2/posts",auth=wp_auth(),json=payload,timeout=90); r.raise_for_status(); return r.json()

def validate_story(story):
    website=story["website"]; social=story["social"]; errors=[]
    if len(count_english_words(website["headline"]))>9: errors.append("headline over 9 words")
    if len(count_english_words(website["excerpt"]))!=25: errors.append("excerpt is not exactly 25 English words")
    if website["category"] not in CATEGORIES: errors.append("invalid category")
    if not website["tag"].strip(): errors.append("missing tag")
    if len(BeautifulSoup(website["article_html"],"html.parser").get_text(" ",strip=True))<300: errors.append("article too short")
    if len(social["english"])>2000: errors.append("social post over 2000 characters")
    if "👉 Have Your Say" not in social["english"]: errors.append("social post missing Have Your Say")
    if not re.search(r"\b(?:YES|NO)\b",social["english"],re.I): errors.append("social post missing yes/no question")
    for field in ("alt_text","image_title","caption","description"):
        if not website.get(field,"").strip(): errors.append(f"missing image {field}")
    if errors: raise ValueError("FINAL VALIDATION FAILED: "+"; ".join(errors))

def build_prompt(story,research):
    return f"""{MASTER_PROMPT}

WORKFLOW POLICY:
This is NOT a blanket verification-or-do-not-publish gate. The Guardian Australia story is a reputable newsroom lead. Ordinary straight-news stories should publish normally after source review and duplicate checking.

FIRST classify the story as: news, official_decision, opinion, analysis, explainer, reported_allegation, live, or other.
Apply the correct treatment. Do not reject an ordinary story simply because a primary source is unavailable. Do not invent a verification failure. Use research to confirm, correct, attribute or add important context. If sources conflict materially and it cannot be resolved, attribute the conflict clearly.

Return ONLY JSON matching the schema.

GUARDIAN STORY
Title: {story["title"]}
URL: {story["url"]}
Description: {story["description"]}
Article text:
{story["text"]}

ADDITIONAL RESEARCH
{format_research(research)}
"""

def pick_candidates(feed,state):
    processed=set(state.get("processed",[])); candidates=[]
    for entry in feed.entries:
        url=entry.get("link","").strip(); title=clean_text(entry.get("title",""))
        if not url or not title: continue
        aid=article_id(url)
        if aid in processed: continue
        candidates.append({"id":aid,"url":url,"title":title,"description":clean_text(entry.get("summary",""))})
    return candidates

def main():
    print("="*52); print("Australia By Aussie Automated Newsroom"); print("Guardian lead | smart classification | WordPress duplicate protection"); print("="*52)
    state=load_state(); feed=get_feed(); candidates=pick_candidates(feed,state); print(f"Found {len(candidates)} new Guardian candidates.")
    if not candidates: print("No new stories to publish."); return
    wp_posts=get_recent_wp_posts(max_pages=10); print(f"Checked {len(wp_posts)} published WordPress posts for duplicates.")
    for candidate in candidates[:10]:
        try:
            print(f"\nCandidate: {candidate['title']}")
            if wordpress_source_exists(candidate["url"],wp_posts): print("Already published by source URL. Skipping."); state["processed"].append(candidate["id"]); save_state(state); continue
            page=get_article_page(candidate["url"]); story={**candidate,**page}
            if wordpress_title_exists(page["title"] or candidate["title"],wp_posts): print("Already published by matching title. Skipping."); state["processed"].append(candidate["id"]); save_state(state); continue
            print("Researching the story..."); research=google_research(page["title"] or candidate["title"]); print(f"Found {len(research)} additional research results.")
            print("Classifying and writing with the Master Newsroom rules..."); result=call_openrouter(build_prompt(story,research),OUTPUT_SCHEMA); print("Story type:",result.get("story_type")); validate_story(result)
            image_url=page["image_url"]
            if not image_url: raise RuntimeError("No usable source image found.")
            filename=re.sub(r"[^A-Za-z0-9._-]+","-",page["title"][:70]).strip("-")+".jpg"
            print("Uploading source image to WordPress..."); image_id=upload_image(image_url,filename,result["website"])
            print("Publishing to WordPress..."); post=publish_post(result,image_id,candidate["url"]); state["processed"].append(candidate["id"]); save_state(state); wp_posts.append(post); print("Published:",post.get("link",post.get("id"))); break
        except Exception as exc: print("Candidate failed:",exc); continue

if __name__=="__main__": main()
