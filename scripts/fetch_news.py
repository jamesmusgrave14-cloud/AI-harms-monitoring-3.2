import json
import os
import re
import html
import hashlib
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus, urlparse, parse_qs
import feedparser
ROOT = os.path.dirname(os.path.dirname(__file__))
PUBLIC = os.path.join(ROOT, "public")
CATEGORIES = os.path.join(PUBLIC, "harm_categories.json")
SOURCES = os.path.join(PUBLIC, "sources.json")
TRUSTED = os.path.join(PUBLIC, "trusted_domains.json")
WATCH = os.path.join(PUBLIC, "watchlist.json")
OUT = os.path.join(PUBLIC, "news_data.json")
SCAN_WINDOW = os.getenv("SCAN_WINDOW", "90d")
MIN_SCORE = int(os.getenv("MIN_SCORE", "6"))
MAX_TOTAL = int(os.getenv("MAX_TOTAL", "900"))
GLOBAL_BLOCK = ["stock", "share price", "earnings", "funding round", "valuation", "appoints", "business solutions summit", "industrial automation", "workflow automation", "forex", "trading bot", "wallpaper", "grammar", "sponsored post", "press release"]
LOW_SIGNAL_DOMAINS = {"prnewswire.com", "businesswire.com", "globenewswire.com", "accesswire.com", "einnews.com", "tipranks.com", "benzinga.com", "tradingview.com"}
OPEN_SOURCE_STRONG_TERMS = ["open weight", "open-weight", "open weights", "open-weights", "open model", "open-model", "model weights", "checkpoint", "fine-tune", "finetune", "lora", "adapter", "safetensors", "hugging face", "huggingface", "civitai", "ollama", "gguf", "model repository", "model hub"]
OPEN_SOURCE_MEDIUM_TERMS = ["open source", "open-source", "downloadable model", "uncensored model", "local model"]
PROPRIETARY_FRONTIER_TERMS = ["frontier model", "proprietary model", "closed model", "closed-source", "api-only", "api only", "openai", "anthropic", "gemini", "claude", "gpt-4", "gpt-5"]
def load_json(path, fallback):
    try:
        with open(path, encoding="utf-8") as f: return json.load(f)
    except Exception: return fallback
def clean(value):
    value = html.unescape(str(value or "")); value = re.sub(r"<[^<]+?>", " ", value); return re.sub(r"\s+", " ", value.replace("\xa0", " ")).strip()
def norm(value):
    value = clean(value).lower(); value = re.sub(r"[^a-z0-9\s\-/]", " ", value); return re.sub(r"\s+", " ", value).strip()
def contains(text, phrase):
    phrase = norm(phrase); return bool(phrase) and phrase in text
def open_source_signal(text):
    t = norm(text); strong = [x for x in OPEN_SOURCE_STRONG_TERMS if contains(t, x)]; medium = [x for x in OPEN_SOURCE_MEDIUM_TERMS if contains(t, x)]
    if strong: return True, "Strong", strong[:5]
    if medium: return True, "Medium", medium[:5]
    return False, "None", []
def parse_date(value):
    if not value: return datetime.now(timezone.utc)
    for fn in (parsedate_to_datetime, lambda x: datetime.fromisoformat(str(x).replace("Z", "+00:00"))):
        try:
            dt = fn(value); return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except Exception: pass
    return datetime.now(timezone.utc)
def window_days(value):
    match = re.match(r"^(\d+)d$", str(value).strip()); return int(match.group(1)) if match else 90
def canonical_url(url):
    try:
        parsed = urlparse(url or ""); q = parse_qs(parsed.query)
        if "url" in q and q["url"] and q["url"][0].startswith("http"): return q["url"][0]
    except Exception: pass
    return url or ""
def domain_of(url):
    try:
        host = urlparse(url or "").netloc.lower().split(":")[0]; return host[4:] if host.startswith("www.") else host
    except Exception: return ""
def dom_in(domain, domains): return any(domain == d or domain.endswith("." + d) for d in domains)
def stable_id(text): return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]
def item_key(item):
    url = item.get("url") or ""
    if url: return "url:" + url.lower().split("#")[0]
    return "title:" + norm(item.get("title", ""))[:180]
def google_url(query): return "https://news.google.com/rss/search?q=" + quote_plus(query) + "&hl=en-GB&gl=GB&ceid=GB:en"
def google_url_for_category(category): return google_url(f"{category.get('query') or ''} -stock -shares -earnings -forex when:{SCAN_WINDOW}")
def google_urls_for_watchlist(watchlist):
    urls=[]
    for item in watchlist.get("items", []):
        aliases=[a for a in item.get("aliases", []) if a]
        if not aliases: continue
        alias_query=" OR ".join(f'"{a}"' if " " in a else a for a in aliases[:8])
        q=f"({alias_query}) (AI OR artificial intelligence OR generative AI OR LLM OR model OR chatbot OR safety OR misuse OR security) -stock -shares -earnings when:{SCAN_WINDOW}"
        urls.append((f"Google News watchlist: {item.get('name')}", "watchlist_news", google_url(q), None))
    return urls
def google_urls_for_open_source_evidence():
    qs=[f'("open weights" OR "open-weight" OR "Hugging Face" OR CivitAI OR LoRA OR safetensors) (AI OR model OR generative AI) (misuse OR abuse OR fraud OR terrorism OR safeguarding OR "child safety" OR weapon OR "illegal") when:{SCAN_WINDOW}', f'("model repository" OR "model hub" OR "downloadable model") (AI OR LLM) (harm OR misuse OR safety OR abuse) when:{SCAN_WINDOW}']
    return [("Google News open-source evidence", "open_source_evidence", google_url(q), None) for q in qs]
def feed_items(url, source_name, source_type):
    feed=feedparser.parse(url)
    for entry in getattr(feed, "entries", []) or []:
        title=clean(getattr(entry,"title","") or "").rsplit(" - ",1)[0].strip(); summary=clean(getattr(entry,"summary","") or getattr(entry,"description", ""))[:900]; link=canonical_url(getattr(entry,"link","") or ""); published=parse_date(getattr(entry,"published","") or getattr(entry,"updated", ""))
        yield {"title":title,"summary":summary,"url":link,"domain":domain_of(link),"source":source_name,"source_type":source_type,"publishedAt":published.isoformat(),"_dt":published}
def score_item(item, category, trusted_domains):
    text_raw=f"{item.get('title','')} {item.get('summary','')} {item.get('domain','')}"; text=norm(text_raw); title=norm(item.get("title", "")); domain=item.get("domain", ""); os_sig, os_strength, os_hits=open_source_signal(text_raw)
    if any(block in text for block in GLOBAL_BLOCK): return -100, [], "blocked_generic_business"
    ai=[x for x in category.get("ai_terms", []) if contains(text,x)]; core=[x for x in category.get("core_terms", []) if contains(text,x)]; support=[x for x in category.get("support_terms", []) if contains(text,x)]; uk=[x for x in category.get("uk_terms", []) if contains(text,x)]
    if not ai: return 0, [], "no_ai_signal"
    if not core: return 0, [], "no_harm_signal"
    score=2; why=["AI-related"]
    title_core=[x for x in core if contains(title,x)]
    if title_core: score += 5; why.extend(title_core[:4])
    else: score += 3; why.extend(core[:4])
    if support: score += min(2, len(support))
    if uk: score += 3; why.append("UK signal")
    if os_sig: score += 2 if os_strength == "Strong" else 1; why.append("open-source/open-weights signal")
    if dom_in(domain, trusted_domains): score += 2; why.append("higher-trust source")
    if not os_sig and [x for x in PROPRIETARY_FRONTIER_TERMS if contains(text,x)]: score -= 3
    if dom_in(domain, LOW_SIGNAL_DOMAINS): score -= 2
    try:
        if (datetime.now(timezone.utc)-item["_dt"]).days <= 14: score += 1
    except Exception: pass
    return score, why, "kept" if score >= MIN_SCORE else "below_threshold"
def best_category(item, categories, trusted_domains):
    best=None
    for name, category in categories.items():
        score, why, reason = score_item(item, category, trusted_domains)
        if reason == "kept" and (best is None or score > best[0]): best=(score, why, name, category)
    return best
def enrich(item, score, why, category_name, category, discovered_via):
    os_sig, os_strength, os_hits=open_source_signal(f"{item.get('title','')} {item.get('summary','')} {item.get('domain','')}"); shown=([category.get("display_reason","Sensitive signal"), "UK signal" if "UK signal" in why else None, "higher-trust source" if "higher-trust source" in why else None] if category.get("sensitive") else why); item.pop("_dt", None); source_type=item.get("source_type","")
    item.update({"id":stable_id(category_name+item.get("title","")+item.get("url","")),"category":category_name,"risk_area":category.get("risk_area"),"category_family":category.get("family"),"relevance_score":score,"why":[x for x in shown if x],"uk_relevant":"UK signal" in why,"priority":"High" if ("UK signal" in why and score >= 10) else ("Medium" if score >= 8 or "UK signal" in why else "Watch"),"sensitive":bool(category.get("sensitive")),"policy_implications":category.get("policy_implications", []),"discoveredVia":discovered_via,"open_source_signal":bool(os_sig),"open_source_strength":os_strength,"open_source_hits":os_hits,"evidence_kind":"Open weights / repository signal" if os_sig else ("Model/tool watchlist" if "watchlist" in source_type else "General harm signal")})
    return item
def add_watchlist_hits(items, watchlist):
    for item in items:
        text=norm(f"{item.get('title','')} {item.get('summary','')} {item.get('category','')}"); hits=[]
        for watch in watchlist.get("items", []):
            aliases=(watch.get("aliases", []) or [])+[watch.get("name", "")]
            if any(contains(text, alias) for alias in aliases): hits.append({"name":watch.get("name"),"type":watch.get("type"),"reason":watch.get("reason"),"owner_hint":watch.get("owner_hint")})
        item["watchlist_hits"]=hits
def text_vector(text):
    vector=[0.0]*256
    for word in [w for w in norm(text).split() if len(w)>2]: vector[int(hashlib.md5(word.encode()).hexdigest(),16)%256]+=1
    mag=math.sqrt(sum(x*x for x in vector)) or 1
    return [x/mag for x in vector]
def add_semantic(items, categories):
    names=list(categories.keys()); profiles=[text_vector(f"{n}: {categories[n].get('semantic_profile') or categories[n].get('description','')}") for n in names]
    for item in items:
        article_vec=text_vector(f"{item.get('title','')} {item.get('summary','')}"); sims=[sum(a*b for a,b in zip(article_vec,p)) for p in profiles]
        if sims:
            idx=max(range(len(sims)), key=lambda i:sims[i]); item["semantic_category"]=names[idx]; item["semantic_score"]=round(float(sims[idx]),4); item["semantic_mode"]="local_hash_similarity"
    return "local_hash_similarity" if items else "none"
def clusters(items):
    grouped=defaultdict(list)
    for item in items:
        hits=item.get("watchlist_hits") or []
        if hits: key="Watchlist: "+hits[0]["name"]
        elif item.get("open_source_signal"): key="Open-source evidence: "+item.get("category","Uncategorised")
        else:
            words=[w for w in norm(item.get("title","")).split() if len(w)>4][:3]; key=item.get("category","Uncategorised")+": "+" ".join(words)
        grouped[key].append(item)
    output=[]
    for key, rows in grouped.items():
        rows=sorted(rows, key=lambda x:x.get("publishedAt",""), reverse=True); output.append({"id":stable_id(key),"title":key,"count":len(rows),"category":rows[0].get("category"),"latest":rows[0].get("publishedAt"),"first_seen":min(r.get("publishedAt","") for r in rows),"uk_count":sum(1 for r in rows if r.get("uk_relevant")),"open_source_count":sum(1 for r in rows if r.get("open_source_signal")),"highest_score":max(r.get("relevance_score",0) for r in rows),"item_ids":[r["id"] for r in rows[:30]],"top_titles":[r["title"] for r in rows[:6]]})
    return sorted(output, key=lambda c:(c["count"], c["uk_count"], c.get("open_source_count",0), c["highest_score"], c.get("latest") or ""), reverse=True)[:100]
def briefing(items, cluster_rows):
    return {"generatedAt":datetime.now(timezone.utc).replace(microsecond=0).isoformat(),"headline":f"{len(items)} items retained; {sum(i.get('priority')=='High' for i in items)} high priority; {sum(i.get('uk_relevant') for i in items)} UK-relevant; {sum(i.get('open_source_signal') for i in items)} open-source/open-weights signals.","top_categories":Counter(i.get("category") for i in items).most_common(8),"top_watchlist_hits":Counter(h["name"] for i in items for h in i.get("watchlist_hits", [])).most_common(8),"top_clusters":cluster_rows[:8],"highest_priority_items":[{"title":i["title"],"category":i.get("category"),"priority":i.get("priority"),"score":i.get("relevance_score"),"open_source_signal":i.get("open_source_signal"),"url":i.get("url")} for i in sorted(items, key=lambda x:(x.get("priority")=="High", x.get("uk_relevant"), x.get("open_source_signal"), x.get("relevance_score",0), x.get("publishedAt","")), reverse=True)[:12]],"suggested_use":"Use as a triage aid. Verify sources before using in advice, briefings or commissions."}
def main():
    categories=load_json(CATEGORIES,{}); sources=load_json(SOURCES,[]); trusted_domains=set(load_json(TRUSTED,[])); watchlist=load_json(WATCH,{"items":[]}); cutoff=datetime.now(timezone.utc)-timedelta(days=window_days(SCAN_WINDOW)); feed_plan=[]
    for category_name, category in categories.items(): feed_plan.append((f"Google News category: {category_name}", "google_news", google_url_for_category(category), category_name))
    feed_plan.extend(google_urls_for_open_source_evidence()); feed_plan.extend(google_urls_for_watchlist(watchlist))
    for source in sources:
        if source.get("enabled") and source.get("type")=="rss" and source.get("url"): feed_plan.append((source.get("name","RSS"), source.get("source_type","rss"), source.get("url"), None))
    seen=set(); items=[]; rejects=Counter(); errors={}
    for source_name, source_type, url, forced_category in feed_plan:
        try: candidates=list(feed_items(url, source_name, source_type))
        except Exception as exc: errors[source_name]=str(exc)[:240]; continue
        for item in candidates:
            if item.get("_dt") and item["_dt"] < cutoff: rejects["outside_window"] += 1; continue
            if forced_category and forced_category in categories:
                category=categories[forced_category]; score, why, reason = score_item(item, category, trusted_domains)
                if reason != "kept": rejects[reason] += 1; continue
                enriched=enrich(item, score, why, forced_category, category, source_name)
            else:
                best=best_category(item, categories, trusted_domains)
                if not best: rejects["no_matching_category"] += 1; continue
                score, why, category_name, category = best; enriched=enrich(item, score, why, category_name, category, source_name)
            key=item_key(enriched)
            if key in seen: rejects["duplicate"] += 1; continue
            seen.add(key); items.append(enriched)
    add_watchlist_hits(items, watchlist); semantic_mode=add_semantic(items, categories); items=sorted(items, key=lambda x:(x.get("priority")=="High", x.get("uk_relevant"), x.get("open_source_signal"), x.get("relevance_score",0), x.get("publishedAt", "")), reverse=True)[:MAX_TOTAL]; cluster_rows=clusters(items)
    meta={"ok":True,"generatedAt":datetime.now(timezone.utc).replace(microsecond=0).isoformat(),"mode":"broad-live-monitoring-v2","total":len(items),"categoryCount":len(categories),"sourceCount":len(feed_plan),"scanWindow":SCAN_WINDOW,"minScore":MIN_SCORE,"maxTotal":MAX_TOTAL,"byCategory":dict(Counter(i.get("category") for i in items)),"byPriority":dict(Counter(i.get("priority","Watch") for i in items)),"openSourceSignals":sum(1 for i in items if i.get("open_source_signal")),"rejectCounts":dict(rejects),"errors":errors,"semanticMode":semantic_mode}
    output={"meta":meta,"briefing":briefing(items, cluster_rows),"clusters":cluster_rows,"watchlist":watchlist.get("items", []),"articles":items}
    with open(OUT,"w",encoding="utf-8") as f: json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"Wrote {len(items)} items from {len(feed_plan)} source queries to {OUT}")
if __name__ == "__main__": main()
