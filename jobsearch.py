"""Daily job search: pull postings from company job boards, rank new ones against
your resume with Claude Code (`claude -p`), and email a digest.

Usage:
    python jobsearch.py              # normal daily run
    python jobsearch.py --dry-run    # fetch + rank, print results, don't email or mark seen
    python jobsearch.py --no-rank    # just list matching new postings (no Claude call)
"""
import argparse
import datetime as dt
import html
import json
import re
import shutil
import smtplib
import subprocess
import sys
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import candidate

ROOT = Path(__file__).resolve().parent
RESUME_DIR = ROOT / "resume"
REPORTS_DIR = ROOT / "reports"
SEEN_FILE = ROOT / "seen_jobs.json"
LOG_FILE = ROOT / "jobsearch.log"
UA = {"User-Agent": "Mozilla/5.0 (personal job search script)"}


def log(msg):
    line = f"[{dt.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def http_json(url, data=None):
    body = json.dumps(data).encode() if data is not None else None
    headers = dict(UA, **({"Content-Type": "application/json"} if body else {}))
    req = urllib.request.Request(url, data=body, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


BROWSER_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                            "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"}


def http_text(url):
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers=BROWSER_UA)
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code != 429 or attempt == 2:
                raise
            time.sleep(5 * (attempt + 1))  # rate-limited: back off and retry


def ld_json(page, typ):
    """Return all schema.org objects of @type `typ` embedded in a page's JSON-LD."""
    found = []
    for blob in re.findall(r'<script[^>]*ld(?:\+|&#x2B;)json[^>]*>(.*?)</script>', page, re.S | re.I):
        try:
            data = json.loads(blob)
        except ValueError:
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            x = stack.pop()
            if isinstance(x, dict):
                if x.get("@type") == typ:
                    found.append(x)
                stack.extend(v for v in x.values() if isinstance(v, (dict, list)))
            elif isinstance(x, list):
                stack.extend(x)
    return found


def ld_location(posting):
    locs = posting.get("jobLocation") or []
    out = []
    for l in locs if isinstance(locs, list) else [locs]:
        a = l.get("address", {}) if isinstance(l, dict) else {}
        out.append(", ".join(filter(None, [a.get("addressLocality"), a.get("addressRegion"), a.get("addressCountry")])))
    if posting.get("jobLocationType") == "TELECOMMUTE":
        out.append("Remote")
    return "; ".join(filter(None, out))


def strip_html(s):
    s = html.unescape(s or "")  # Greenhouse double-encodes its HTML
    s = re.sub(r"<(br|/p|/li|/h\d)[^>]*>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n", s)).strip()


def job(source, company, title, location, url, description, remote=False, posted=None):
    return {"id": f"{source}:{url}", "company": company, "title": title.strip(),
            "location": (location or "").strip(), "url": url, "remote": remote,
            "description": description, "posted": posted}


def parse_date(value):
    """ISO string or epoch milliseconds -> local date (None if missing/unparseable)."""
    if not value:
        return None
    try:
        if isinstance(value, (int, float)):
            return dt.datetime.fromtimestamp(value / 1000).date()
        d = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return d.astimezone().date() if d.tzinfo else d.date()
    except ValueError:
        return None


def workday_posted_on(text):
    """Workday's 'Posted Today' / 'Posted Yesterday' / 'Posted 5 Days Ago' / 'Posted 30+ Days Ago'."""
    t = (text or "").lower()
    if "today" in t:
        days = 0
    elif "yesterday" in t:
        days = 1
    elif m := re.search(r"(\d+)\+?\s*days", t):
        days = int(m.group(1)) + ("+" in t)
    else:
        return None
    return dt.date.today() - dt.timedelta(days=days)


# --------------------------------------------------------------------------- fetchers

def fetch_greenhouse(token):
    d = http_json(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true")
    return [job("gh", j.get("company_name") or token, j["title"], j.get("location", {}).get("name"),
                j["absolute_url"], strip_html(j.get("content")),
                posted=parse_date(j.get("first_published") or j.get("updated_at")))
            for j in d.get("jobs", [])]


def fetch_lever(token):
    d = http_json(f"https://api.lever.co/v0/postings/{token}?mode=json")
    out = []
    for j in d:
        cat = j.get("categories", {})
        locs = cat.get("allLocations") or [cat.get("location", "")]
        out.append(job("lever", token, j["text"], "; ".join(filter(None, locs)), j["hostedUrl"],
                       j.get("descriptionPlain", "") + "\n" + strip_html(
                           " ".join(l.get("content", "") for l in j.get("lists", []))),
                       remote=j.get("workplaceType") == "remote", posted=parse_date(j.get("createdAt"))))
    return out


def fetch_ashby(token):
    d = http_json(f"https://api.ashbyhq.com/posting-api/job-board/{token}")
    out = []
    for j in d.get("jobs", []):
        locs = [j.get("location", "")] + [s.get("location", "") for s in j.get("secondaryLocations", [])]
        out.append(job("ashby", token, j["title"], "; ".join(filter(None, locs)), j["jobUrl"],
                       j.get("descriptionPlain") or strip_html(j.get("descriptionHtml")),
                       remote=bool(j.get("isRemote")), posted=parse_date(j.get("publishedAt"))))
    return out


def fetch_workable(token):
    d = http_json(f"https://apply.workable.com/api/v1/widget/accounts/{token}?details=true")
    out = []
    for j in d.get("jobs", []):
        locs = ["; ".join(", ".join(filter(None, [l.get("city"), l.get("region"), l.get("country")]))
                          for l in j.get("locations") or [])
                or ", ".join(filter(None, [j.get("city"), j.get("state"), j.get("country")]))]
        remote = bool(j.get("telecommuting"))
        out.append(job("workable", d.get("name") or token, j["title"], "; ".join(locs + (["Remote"] if remote else [])),
                       j["url"], strip_html(j.get("description")), remote=remote,
                       posted=parse_date(j.get("published_on") or j.get("created_at"))))
    return out


def consider_jobs(host, board):
    """All listings on a Consider VC portfolio board. Its search API needs the session cookie +
    CSRF token from the board's page."""
    import http.cookiejar
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    with opener.open(urllib.request.Request(f"https://{host}/jobs", headers=BROWSER_UA), timeout=30) as r:
        token = re.search(r'csrfToken":"([^"]+)', r.read().decode("utf-8", "replace")).group(1)
    listings, seq = [], None
    for _ in range(50):
        body = json.dumps({"meta": {"size": 100, **({"sequence": seq} if seq else {})},
                           "board": {"id": board, "isParent": True}, "query": {}}).encode()
        req = urllib.request.Request(f"https://{host}/api-boards/search-jobs", data=body, headers=dict(
            BROWSER_UA, **{"Content-Type": "application/json", "x-csrf-token": token}))
        with opener.open(req, timeout=30) as r:
            d = json.load(r)
        listings += d.get("jobs", [])
        seq = d.get("meta", {}).get("sequence")
        if not d.get("jobs") or not seq:
            break
    return listings


def fetch_consider(host, board, keep, seen):
    """VC portfolio job boards on Consider (e.g. Atlas Venture, Third Rock): every portfolio
    company's openings, including startups whose own boards we can't read. Descriptions come from
    each job's own posting (fetched only for new matches)."""
    import apply
    out = []
    for j in consider_jobs(host, board):
        url = j.get("applyUrl") or j.get("url")
        if not url:
            continue
        locs = "; ".join(j.get("locations") or [])
        stub = job("consider", j.get("companyName") or "?", j.get("title", ""),
                   locs + ("; Remote" if j.get("remote") and "remote" not in locs.lower() else ""), url, "",
                   remote=bool(j.get("remote")), posted=parse_date(j.get("timeStamp")))
        if not keep(stub, full=False, check_location=True) or stub["id"] in seen:
            continue
        try:
            stub["description"] = apply.fetch_posting(url).get("description", "")
        except Exception as e:
            log(f"  consider detail failed for {url}: {e}")
        out.append(stub)
    return out


def fetch_workday(name, url, terms, keep):
    m = re.match(r"https://([^/]+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([^/?#]+)", url)
    if not m:
        raise ValueError(f"unrecognized Workday URL: {url}")
    host, site = m.groups()
    tenant = host.split(".")[0]
    api = f"https://{host}.myworkdayjobs.com/wday/cxs/{tenant}/{site}"
    listings = {}
    for term in terms:
        d = http_json(f"{api}/jobs", {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": term})
        for p in d.get("jobPostings", []):
            if p.get("externalPath"):
                listings[p["externalPath"]] = p
    out = []
    for path, p in listings.items():
        stub = job("wd", name, p.get("title", ""), p.get("locationsText", ""),
                   f"https://{host}.myworkdayjobs.com/{site}{path}", "",
                   posted=workday_posted_on(p.get("postedOn")))
        if not keep(stub, full=False):
            continue  # skip detail fetch for irrelevant titles / old postings
        try:
            info = http_json(f"{api}{path}").get("jobPostingInfo", {})
            stub["posted"] = parse_date(info.get("startDate")) or stub["posted"]
            stub["location"] = "; ".join(filter(None, [info.get("location")] + info.get("additionalLocations", []))) or stub["location"]
            stub["description"] = strip_html(info.get("jobDescription"))
            stub["remote"] = "remote" in (info.get("remoteType") or "").lower()
        except Exception as e:
            log(f"  workday detail failed for {stub['url']}: {e}")
        out.append(stub)
    return out


def fetch_compbiojobs(pages, keep, seen):
    """compbiojobs.com: aggregator for bioinformatics/compbio roles. Listing pages show title,
    company and location; descriptions come from each job's page (fetched only for new matches)."""
    cards = {}
    for path in pages:
        page = http_text(f"https://www.compbiojobs.com{path}")
        for m in re.finditer(r'<a href="(/jobs/\d+/[^"]+)"[^>]*>(.*?)</a>', page, re.S):
            body = m.group(2)
            title = re.search(r"<h2[^>]*>(.*?)</h2>", body, re.S)
            company = re.search(r"</h2>\s*<p[^>]*>(.*?)</p>", body, re.S)
            loc = re.search(r'</svg>\s*([^<]+?)\s*</span>', body, re.S)
            if title:
                cards[m.group(1)] = job("cbj", strip_html(company.group(1)) if company else "?",
                                       strip_html(title.group(1)), loc.group(1).strip() if loc else "",
                                       f"https://www.compbiojobs.com{m.group(1)}", "")
    out = []
    for stub in cards.values():
        if not keep(stub, full=False, check_location=True) or stub["id"] in seen:
            continue
        try:
            page = http_text(stub["url"])
            post = (ld_json(page, "JobPosting") or [{}])[0]
            stub["description"] = strip_html(post.get("description"))
            stub["posted"] = parse_date(post.get("datePosted"))
            apply = re.search(r'href="(https?://(?!www\.compbiojobs)[^"]+)"[^>]*>[^<]*Apply', page)
            if apply:
                stub["apply_url"] = html.unescape(apply.group(1))
        except Exception as e:
            log(f"  compbiojobs detail failed for {stub['url']}: {e}")
        out.append(stub)
    return out


def fetch_builtin(site, terms, max_pages, keep, seen):
    """Built In (builtinboston.com etc.): search result pages carry a JSON-LD ItemList; each job
    page has a full JobPosting. Job pages are fetched only for new postings with matching titles."""
    listings = {}
    for term in terms:
        for p in range(1, max_pages + 1):
            q = urllib.parse.urlencode({"search": term, "page": p})
            items = [it for lst in ld_json(http_text(f"https://{site}/jobs?{q}"), "ItemList")
                     for it in lst.get("itemListElement", [])]
            for it in items:
                if it.get("url"):
                    listings[it["url"]] = it
            if len(items) < 20:
                break
            time.sleep(1)
    out = []
    for url, it in listings.items():
        stub = job("builtin", "?", it.get("name", ""), "", url, it.get("description", ""))
        if not keep(stub, full=False) or stub["id"] in seen:
            continue
        try:
            post = (ld_json(http_text(url), "JobPosting") or [{}])[0]
            stub["posted"] = parse_date(post.get("datePosted"))
            org = post.get("hiringOrganization") or {}
            stub["company"] = org.get("name", "?") if isinstance(org, dict) else str(org)
            stub["location"] = ld_location(post) or stub["location"]
            stub["remote"] = post.get("jobLocationType") == "TELECOMMUTE"
            stub["description"] = strip_html(post.get("description")) or stub["description"]
            time.sleep(1.5)
        except urllib.error.HTTPError as e:
            if e.code == 429:  # still rate-limited after retries: try the rest tomorrow
                log(f"  {site} is rate-limiting us; skipping remaining job pages this run")
                break
            log(f"  builtin detail failed for {url}: {e}")
        except Exception as e:
            log(f"  builtin detail failed for {url}: {e}")
        out.append(stub)
    return out


def fetch_linkedin(terms, location, max_pages, max_age, keep, seen):
    """LinkedIn's public (logged-out) job search. Cards give title, company, location and date;
    descriptions come from each job's guest page (fetched only for new matches)."""
    base = "https://www.linkedin.com/jobs-guest/jobs/api"
    window = f"r{((max_age if max_age is not None else 6) + 1) * 86400}"  # posted within N seconds
    cards = {}
    for term in terms:
        for p in range(max_pages):
            q = urllib.parse.urlencode({"keywords": term, "location": location, "f_TPR": window, "start": p * 10})
            page = http_text(f"{base}/seeMoreJobPostings/search?{q}")
            found = re.findall(r"<li>(.*?)</li>", page, re.S)
            for c in found:
                jid = re.search(r"urn:li:jobPosting:(\d+)", c)
                title = re.search(r'base-search-card__title">(.*?)</h3>', c, re.S)
                if not (jid and title):
                    continue
                company = re.search(r'base-search-card__subtitle">(.*?)</h4>', c, re.S)
                loc = re.search(r'job-search-card__location">(.*?)</span>', c, re.S)
                posted = re.search(r'<time[^>]*datetime="([\d-]+)"', c)
                cards[jid.group(1)] = job("li", strip_html(company.group(1)) if company else "?",
                                          strip_html(title.group(1)), strip_html(loc.group(1)) if loc else "",
                                          f"https://www.linkedin.com/jobs/view/{jid.group(1)}", "",
                                          posted=parse_date(posted.group(1)) if posted else None)
            if len(found) < 10:
                break
            time.sleep(1.5)
    out = []
    for jid, stub in cards.items():
        stub["remote"] = "remote" in stub["location"].lower()
        if not keep(stub, full=False, check_location=True) or stub["id"] in seen:
            continue
        try:
            page = http_text(f"{base}/jobPosting/{jid}")
            desc = re.search(r'show-more-less-html__markup[^>]*>(.*?)</div>', page, re.S)
            stub["description"] = strip_html(desc.group(1)) if desc else ""
            time.sleep(1.5)
        except Exception as e:
            log(f"  linkedin detail failed for {stub['url']}: {e}")
        out.append(stub)
    return out


# --------------------------------------------------------------------------- filtering

def make_filter(cfg):
    s = cfg["search"]
    # Short keywords (e.g. "ngs") must start a word so they don't match inside "openings".
    as_re = lambda k: re.compile((r"\b" if len(k) < 5 else "") + re.escape(k.lower()))
    inc = [as_re(k) for k in s["title_keywords"]]
    exc = [as_re(k) for k in s.get("exclude_title_keywords", [])]
    locs = [l.lower() for l in s["locations"]]
    bad_locs = [l.lower() for l in s.get("exclude_locations", [])]
    allow_remote = s.get("include_remote", False)
    max_age = s.get("max_posted_age_days")
    # Company boards are deduped by seen_jobs, so a longer window costs little and means a missed
    # day (or a newly added board) doesn't lose postings that are a few days old.
    board_max_age = s.get("board_max_posted_age_days", max_age)
    keep_undated = s.get("keep_undated", False)

    def keep(j, full=True, check_location=None):
        """full=False is a cheap pre-check before fetching a job's detail page: title, plus
        posting age and location only when they're already known."""
        t = j["title"].lower()
        if not any(k.search(t) for k in inc) or any(k.search(t) for k in exc):
            return False
        limit = max_age if j["id"].split(":")[0] in AGGREGATORS else board_max_age
        if limit is not None:
            if j.get("posted"):
                if (dt.date.today() - j["posted"]).days > limit:
                    return False
            elif full and not keep_undated:
                return False
        if not (full if check_location is None else check_location):
            return True
        # Location strings may list several sites separated by ";" — keep if any one is local.
        for seg in j["location"].lower().split(";"):
            if any(b in seg for b in bad_locs):
                continue
            if any(l in seg for l in locs):
                return True
        loc = j["location"].lower()
        return allow_remote and (j["remote"] or "remote" in loc) and not NON_US.search(loc)
    return keep


AGGREGATORS = ("cbj", "builtin", "li", "consider")  # sources that repost other companies' jobs

# Remote postings tied to another country ("Ireland; Remote", "Remote - EMEA") aren't US-remote.
NON_US = re.compile(r"\b(ireland|united kingdom|england|uk|scotland|wales|germany|france|spain|portugal|italy|"
                    r"netherlands|belgium|switzerland|austria|denmark|sweden|norway|finland|poland|czech|"
                    r"romania|hungary|israel|india|china|japan|korea|singapore|australia|new zealand|canada|"
                    r"mexico|brazil|argentina|colombia|philippines|vietnam|thailand|malaysia|indonesia|taiwan|asean|emea|europe|apac|latam|ontario|toronto|"
                    r"london|paris|berlin|bangalore|bengaluru|hyderabad|pune|dublin)\b", re.I)


def read_secrets():
    path = ROOT / "secrets.toml"
    return tomllib.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def dedupe_key(j):
    """Same job listed on an aggregator and the company's own board -> same key."""
    norm = lambda s: re.sub(r"[^a-z0-9]", "", s.lower())
    company = (j["company"].split() or ["?"])[0]  # "Vertex" == "Vertex Pharmaceuticals"
    return f"{norm(j['title'])}|{norm(company)}"


def wait_for_network(timeout=600):
    """After waking from sleep Wi-Fi can take a while to reconnect; wait for DNS to work."""
    import socket
    deadline = time.time() + timeout
    while True:
        try:
            socket.getaddrinfo("boards-api.greenhouse.io", 443)
            return True
        except OSError:
            if time.time() > deadline:
                return False
            log("  waiting for network...")
            time.sleep(30)


def collect(cfg, seen):
    keep = make_filter(cfg)
    b = cfg.get("boards", {})
    agg = cfg.get("aggregators", {})
    if b.get("workday") and not b.get("workday_search_terms"):
        log("  [boards] workday_search_terms is empty; Workday boards are skipped")
        b = {**b, "workday": []}
    tasks = ([("greenhouse " + t, fetch_greenhouse, (t,)) for t in b.get("greenhouse", [])]
             + [("lever " + t, fetch_lever, (t,)) for t in b.get("lever", [])]
             + [("ashby " + t, fetch_ashby, (t,)) for t in b.get("ashby", [])]
             + [("workable " + t, fetch_workable, (t,)) for t in b.get("workable", [])]
             + [("workday " + w["name"], fetch_workday,
                 (w["name"], w["url"], b.get("workday_search_terms", []), keep))
                for w in b.get("workday", [])])
    for vc in agg.get("consider_boards", []):
        tasks.append((f"consider {vc['board']}", fetch_consider, (vc["host"], vc["board"], keep, seen)))
    if agg.get("compbiojobs_pages"):
        tasks.append(("compbiojobs", fetch_compbiojobs, (agg["compbiojobs_pages"], keep, seen)))
    for site in agg.get("builtin_sites", []) if agg.get("builtin_search_terms") else []:
        tasks.append((site, fetch_builtin, (site, agg["builtin_search_terms"],
                                            agg.get("builtin_max_pages", 2), keep, seen)))
    if agg.get("linkedin_search_terms") and not agg.get("linkedin_location"):
        log("  [aggregators] linkedin_location is empty; LinkedIn is skipped")
    elif agg.get("linkedin_search_terms"):
        tasks.append(("linkedin", fetch_linkedin,
                      (agg["linkedin_search_terms"], agg["linkedin_location"],
                       agg.get("linkedin_max_pages", 2), cfg["search"].get("max_posted_age_days"), keep, seen)))

    def run(task):
        label, fn, args = task
        try:
            return label, fn(*args), None
        except Exception as e:
            return label, [], e

    jobs, failures, stale = {}, [], set()
    max_age = cfg["search"].get("max_posted_age_days")
    with ThreadPoolExecutor(max_workers=8) as ex:
        for label, found, err in ex.map(run, tasks):
            if err:
                failures.append(f"{label}: {err}")
                log(f"  FAILED {label}: {err}")
                continue
            # Aggregator postings cost a page fetch to learn their date; remember the ones that
            # turned out too old so they aren't re-fetched every day.
            if max_age is not None:
                stale |= {j["id"] for j in found if j["id"].split(":")[0] in ("cbj", "builtin")
                          and j.get("posted") and (dt.date.today() - j["posted"]).days > max_age}
            matched = [j for j in found if keep(j)]
            log(f"  {label}: {len(found)} postings, {len(matched)} match")
            for j in matched:
                jobs[j["id"]] = j
    # Drop aggregator copies of jobs we already have from a company board (or saw on a past day).
    seen_keys = {v.get("key") for v in seen.values()}
    unique, keys = [], set()
    # Company-board listings sort first, so an aggregator copy always hits `keys`.
    for j in sorted(jobs.values(), key=lambda j: j["id"].split(":")[0] in AGGREGATORS):
        k = j["key"] = dedupe_key(j)
        # A key seen on a past day means the same job was already scored, possibly from another
        # source (e.g. found on Built In first, then on the company's own board).
        if k in keys or k in seen_keys:
            continue
        keys.add(k)
        unique.append(j)
    if len(unique) < len(jobs):
        log(f"  removed {len(jobs) - len(unique)} duplicate listings")
    return unique, failures, stale


# --------------------------------------------------------------------------- resume + ranking

def read_resume():
    """All documents in resume/, newest first and labeled, so Claude treats the latest
    resume as the current profile and older versions / other files as background."""
    texts = []
    for p in sorted(RESUME_DIR.glob("*"), key=lambda p: p.stat().st_mtime, reverse=True):
        ext = p.suffix.lower()
        if p.name.lower() == "readme.txt" or ".example." in p.name.lower():
            continue  # help files shipped with the kit, not the candidate's documents
        try:
            if ext in (".txt", ".md"):
                text = p.read_text(encoding="utf-8", errors="ignore")
            elif ext == ".pdf":
                from pypdf import PdfReader
                text = "\n".join(pg.extract_text() or "" for pg in PdfReader(p).pages)
            elif ext == ".docx":
                with zipfile.ZipFile(p) as z:
                    xml = z.read("word/document.xml").decode("utf-8", "ignore")
                text = strip_html(re.sub(r"</w:p>", "\n", xml))
            else:
                continue
        except Exception as e:
            log(f"  could not read {p.name}: {e}")
            continue
        if text.strip():
            modified = dt.date.fromtimestamp(p.stat().st_mtime)
            texts.append(f"##### FILE: {p.name} (last modified {modified})\n{text.strip()}")
            log(f"  read resume file {p.name}")
    return "\n\n".join(texts)


RANK_PROMPT = """You are helping a job seeker decide which postings to apply to.

Their documents are below, newest first. There may be several resume versions targeted at different
kinds of roles, so judge each posting against the UNION of skills and experience across all versions;
facts like degree and dates come from the newest. If a file named corrections.txt is present it
overrides everything else, and if a file states preferences, honor them. Today's date is {today}.

{profile}

For EACH posting, judge realistic fit: required degree and years of experience vs. the candidate's
actual level, skills/tools overlap, domain match, and whether they'd be a competitive applicant.
Be calibrated, not harsh: missing a realistic opportunity costs the candidate far more than
reviewing a stretch, so when a posting is borderline, lean toward the higher score and say why in
"gaps". Don't inflate clear mismatches either.

Hard blockers vs. near misses:
- Hard blocker (cap the score at 4): a degree that is explicitly REQUIRED and the candidate lacks, with
  no alternative path; required experience clearly beyond the candidate's level; a required license,
  certification or clearance the candidate lacks. Name it in "blocker".
- Near miss (score 5-7, never lower for this reason alone): a year or two more experience than the
  candidate has where related work plausibly counts; "degree X or degree Y with N years" (judge the path
  the candidate qualifies for); one or two required tools or techniques missing when the candidate has
  adjacent experience; a domain they haven't worked in when the core skills match.
- "Senior", "Sr." or "II" in a title is not a blocker by itself; read the actual requirements.
- 1-2 is only for postings in an unrelated field.

How to count experience and requirements:
- Score against the REQUIRED / basic qualifications. "Preferred" or "nice to have" items are a plus when
  met but must not by themselves pull the score below 7 when every required qualification is met.
- Exception: if the role's main day-to-day work centers on something the candidate hasn't done, it is a
  stretch (5-6) even if the listed requirements are technically met. Judge the duties, not just the
  qualifications list.

Score 1-10: 9-10 = strong match, apply today; 7-8 = good match, worth applying; 5-6 = stretch but a
realistic application; 3-4 = blocked by a hard requirement; 1-2 = unrelated field.

Respond with ONLY a JSON array, no prose, no code fences:
[{{"id": "<posting id>", "score": <int>, "category": "preferred" or "backup",
  "reason": "<1-2 sentences: why it fits or doesn't>", "gaps": "<key missing requirements, or empty string>",
  "blocker": "<the hard requirement that rules it out, or empty string>"}}]

=== RESUME ===
{resume}

=== POSTINGS ===
{postings}
"""


def rank(jobs, resume, batch_size):
    claude = shutil.which("claude") or shutil.which("claude.cmd")
    if not claude:
        raise RuntimeError("`claude` CLI not found on PATH")
    results = {}
    for i in range(0, len(jobs), batch_size):
        batch = jobs[i:i + batch_size]
        postings = "\n\n".join(
            f"--- id: {j['id']}\nTitle: {j['title']}\nCompany: {j['company']}\nLocation: {j['location']}\n"
            f"Description:\n{j['description'][:4000]}" for j in batch)
        prompt = RANK_PROMPT.format(resume=resume[:150000], postings=postings, today=dt.date.today(),
                                    profile=candidate.rank_block())
        log(f"  ranking postings {i + 1}-{i + len(batch)} of {len(jobs)} with Claude...")
        r = subprocess.run([claude, "-p", "--output-format", "text"], input=prompt,
                           capture_output=True, text=True, encoding="utf-8", timeout=900, cwd=ROOT)
        if r.returncode != 0:
            log(f"  claude failed: {r.stderr.strip()[:500]}")
            continue
        m = re.search(r"\[.*\]", r.stdout, re.S)
        try:
            for item in json.loads(m.group(0)):
                results[item["id"]] = item
        except Exception as e:
            log(f"  couldn't parse Claude output ({e}): {r.stdout[:500]}")
    return results


# --------------------------------------------------------------------------- report + email

def apply_link(j):
    """Best link straight to the application form (falls back to the posting)."""
    src, url = j["id"].split(":")[0], j["url"]
    if j.get("apply_url"):
        return j["apply_url"]
    if src == "wd" or (src == "lever"):
        return url.rstrip("/") + "/apply"
    if src == "ashby":
        return url.rstrip("/") + "/application"
    return url


def role_category(j):
    """'preferred' or 'backup': Claude's label when ranked, else a guess from the title using
    profile.toml's preferred_title_keywords (everything is 'preferred' without a preference set)."""
    c = (j.get("rank") or {}).get("category")
    c = {"computational": "preferred", "lab": "backup"}.get(c, c)  # labels from older versions
    if c in ("preferred", "backup"):
        return c
    if not candidate.categories():
        return "preferred"
    rx = candidate.preferred_title_re()
    return "preferred" if rx and rx.search(j.get("title", "")) else "backup"


def cutoff(section, key, j, default):
    """A score threshold from config; `<key>_preferred`, if set, applies to preferred roles
    (`<key>_computational` is accepted as an older name)."""
    v = section.get(key, default)
    if role_category(j) != "preferred":
        return v
    return section.get(f"{key}_preferred", section.get(f"{key}_computational", v))


def build_html(ranked, unranked, failures, total_new, min_score):
    def button(href, text, bg):
        return (f"<a href='{html.escape(href)}' style='display:inline-block;background:{bg};color:#fff;"
                f"text-decoration:none;font-weight:bold;border-radius:6px;padding:7px 16px;margin:8px 8px 0 0'>{text}</a>")

    def card(j):
        r = j.get("rank", {})
        score = r.get("score", "?")
        color = "#1a7f37" if isinstance(score, int) and score >= 8 else "#9a6700"
        src = j["id"].split(":")[0]
        link = apply_link(j)
        label = "Apply on LinkedIn &rarr;" if src == "li" else "Apply &rarr;"
        body = button(link, label, "#0a66c2")
        if link != j["url"]:
            body += f"<a href='{html.escape(j['url'])}' style='font-size:13px'>view posting</a>"
        if src == "li":
            body += ("<div style='color:#666;font-size:12px'>LinkedIn repost: the Apply button there takes you to the "
                     "employer's own site" + (" (the real employer isn't named in the listing)"
                                             if j["company"] in ("BioSpace", "CHEManager International") else "") + ".</div>")
        import apply
        if pay := apply.salary_range(j.get("description", "")):
            body += f"<div style='font-size:13px;margin-top:6px'><b>Pay (from posting):</b> {html.escape(pay)}</div>"
        if r.get("gaps"):
            body += f"<div style='color:#666;font-size:13px;margin-top:6px'><b>Gaps:</b> {html.escape(r['gaps'])}</div>"
        if r.get("blocker"):
            body += f"<div style='color:#a33;font-size:13px;margin-top:4px'><b>Blocker:</b> {html.escape(r['blocker'])}</div>"
        if j.get("tailored"):
            tr = j["tailored"]
            cover = tr.get("cover") or {}
            files = f"📄 {html.escape(Path(tr['pdf']).name)}"
            if cover.get("pdf"):
                files += f"<br>✉️ {html.escape(Path(cover['pdf']).name)}"
            body += (f"<div style='font-size:13px;background:#f3f7ff;border-radius:6px;padding:8px 10px;margin-top:8px'>"
                     f"<b>Attached for this job:</b><br>{files}")
            if cover.get("why_interested"):
                body += (f"<div style='margin-top:6px'><b>\"Why are you interested?\"</b> (copy into the form)<br>"
                         f"<span style='color:#222'>{html.escape(cover['why_interested'])}</span></div>")
            if tr.get("changes"):
                body += ("<details style='margin-top:6px'><summary>What was tailored</summary><ul style='margin:4px 0'>"
                         + "".join(f"<li>{html.escape(c)}</li>" for c in tr["changes"]) + "</ul></details>")
            if tr.get("missing"):
                body += (f"<div style='margin-top:4px'><b>Not on your resume (left out, not invented):</b> "
                         f"{html.escape(', '.join(tr['missing']))}</div>")
            body += "</div>"
        return (f"<div style='border:1px solid #ddd;border-radius:8px;padding:12px 14px;margin:10px 0'>"
                f"<div><span style='background:{color};color:#fff;border-radius:4px;padding:2px 7px;font-weight:bold'>{score}/10</span> "
                f"<a href='{html.escape(j['url'])}' style='font-size:16px;font-weight:bold'>{html.escape(j['title'])}</a></div>"
                f"<div style='color:#444;margin:4px 0'>{html.escape(j['company'])} · {html.escape(j['location'])}"
                f"{' · posted ' + j['posted'].strftime('%b %d') if j.get('posted') else ''}</div>"
                f"<div style='font-size:14px'>{html.escape(r.get('reason', ''))}</div>{body}</div>")

    today = dt.date.today().strftime("%A, %B %d, %Y")
    parts = [f"<div style='font-family:Segoe UI,Arial,sans-serif;max-width:720px'>",
             f"<h2 style='margin-bottom:4px'>Job matches: {today}</h2>",
             f"<p style='color:#555'>{total_new} new matching postings today; {len(ranked)} scored {min_score}+.</p>"]
    if any(j.get("tailored") for j in ranked):
        parts.append("<p style='color:#555;font-size:13px'>Tip: on your PC, click <b>Fill with Job Search</b> on the application "
                     "page to upload that job's resume + cover letter and fill your details.</p>")
    if ranked:
        cats = candidate.categories()
        pref = [j for j in ranked if role_category(j) == "preferred"]
        backup = [j for j in ranked if role_category(j) != "preferred"]
        labels = (cats[0]["label"], cats[1]["label"]) if cats else ("Matches", "Other matches")
        for heading, group in ((f"⭐ {labels[0]}", pref), (f"🔹 {labels[1]}", backup)):
            if group:
                parts.append(f"<h3 style='margin:22px 0 4px;border-bottom:2px solid #0a66c2;padding-bottom:3px'>"
                             f"{heading} ({len(group)})</h3>")
                parts += [card(j) for j in group]
    else:
        parts.append("<p>No strong new matches today.</p>")
    if unranked:
        parts.append("<h3>Not scored (Claude ranking failed or was skipped)</h3><ul>")
        parts += [f"<li><a href='{html.escape(j['url'])}'>{html.escape(j['title'])}</a>: {html.escape(j['company'])}, {html.escape(j['location'])}</li>" for j in unranked]
        parts.append("</ul>")
    if failures:
        parts.append("<p style='color:#999;font-size:12px'>Boards that failed: " + html.escape("; ".join(failures)) + "</p>")
    parts.append(answers_html())
    parts.append("</div>")
    return "\n".join(parts)


def answers_html():
    """Your filled-in answers.toml values, as a copy-paste sheet for application forms."""
    path = ROOT / "answers.toml"
    if not path.exists():
        return ""
    rows = [(k.replace("_", " "), str(v)) for sec, vals in tomllib.loads(path.read_text(encoding="utf-8")).items()
            if sec not in ("notes",) and isinstance(vals, dict) for k, v in vals.items() if str(v).strip()]
    if not rows:
        return ""
    return ("<details style='margin-top:18px;font-size:13px'><summary><b>Your standard form answers</b> "
            "(edit answers.toml; blanks are left out)</summary><table style='border-collapse:collapse;margin-top:6px'>"
            + "".join(f"<tr><td style='color:#666;padding:2px 12px 2px 0'>{html.escape(k)}</td><td>{html.escape(v)}</td></tr>"
                      for k, v in rows) + "</table></details>")


def send_email(cfg, subject, body_html, attachments=()):
    sec = read_secrets()
    if not sec:
        log("  secrets.toml missing; skipping email")
        return
    msg = MIMEMultipart()
    msg.attach(MIMEText(body_html, "html", "utf-8"))
    for path in attachments:
        part = MIMEApplication(Path(path).read_bytes(), Name=Path(path).name)
        part["Content-Disposition"] = f'attachment; filename="{Path(path).name}"'
        msg.attach(part)
    msg["Subject"], msg["From"], msg["To"] = subject, sec["gmail_address"], cfg["email"]["to"]
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as s:
        s.login(sec["gmail_address"], sec["gmail_app_password"].replace(" ", ""))
        s.send_message(msg)
    log(f"  emailed digest to {cfg['email']['to']}")


def tailor_top(cfg, ranked, resume):
    """Write a tailored resume (PDF + DOCX) for each top-scoring job; stores paths on the job."""
    t = cfg.get("tailoring", {})
    if not t.get("enabled"):
        return
    import tailor
    claude = shutil.which("claude") or shutil.which("claude.cmd")
    picks = [j for j in ranked if j["rank"]["score"] >= cutoff(t, "min_score", j, 7)][:t.get("max_per_run", 5)]
    out_dir = ROOT / "tailored" / str(dt.date.today())
    cover_min = t.get("cover_letter_min_score", 8)
    for j in picks:
        cover = j["rank"]["score"] >= cover_min
        log(f"  tailoring resume{' + cover letter' if cover else ''} for {j['title']} @ {j['company']}...")
        try:
            j["tailored"] = tailor.tailor(j, resume, out_dir, claude, ROOT, cover=cover)
        except Exception as e:
            log(f"  tailoring failed: {e}")


# --------------------------------------------------------------------------- main

def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows console/log is cp1252
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="don't email or update seen_jobs.json")
    ap.add_argument("--no-rank", action="store_true", help="skip Claude ranking")
    ap.add_argument("--rescore-today", action="store_true",
                    help="also re-score postings first seen today (e.g. after updating your resumes)")
    ap.add_argument("--rescore-below", type=int, metavar="N",
                    help="re-score still-open postings that earlier scored below N (e.g. after tuning scoring)")
    args = ap.parse_args()

    cfg = tomllib.loads((ROOT / "config.toml").read_text(encoding="utf-8"))
    log("=== job search run ===")
    if not wait_for_network():
        log("  no network after 10 min; giving up without a report so a later run can retry")
        return 1
    seen = json.loads(SEEN_FILE.read_text()) if SEEN_FILE.exists() else {}
    retry = {k for k, v in seen.items()
             if args.rescore_below and v.get("score") is not None and v["score"] < args.rescore_below}
    # Hidden from collect() so aggregators re-fetch their pages instead of skipping them as seen.
    jobs, failures, stale = collect(cfg, {k: v for k, v in seen.items() if k not in retry})
    if not jobs and failures and all("getaddrinfo" in f or "urlopen error" in f for f in failures):
        log("  every source failed to connect; no report saved so a later run can retry")
        return 1
    today = dt.date.today().isoformat()
    import apply
    done = {k for k, v in apply.load().items() if v["status"] != "to apply"}  # applied or skipped
    new = [j for j in jobs if j["id"] not in done and (j["id"] not in seen or j["id"] in retry
           or (args.rescore_today and seen[j["id"]].get("date") == today))]
    if retry:
        log(f"  re-scoring {sum(j['id'] in retry for j in new)} of {len(retry)} earlier low scores (others closed)")
    log(f"{len(jobs)} matching postings, {len(new)} new")

    min_score = cfg["ranking"]["min_score_to_email"]
    ranked, unranked = [], []
    if new and not args.no_rank:
        resume = read_resume()
        if not resume:
            log("  no resume found in resume/ ; listing postings without scores")
            unranked = new
        else:
            scores = rank(new, resume, cfg["ranking"]["max_jobs_per_claude_call"])
            for j in new:
                if j["id"] in scores:
                    j["rank"] = scores[j["id"]]
                    if j["rank"].get("score", 0) >= cutoff(cfg["ranking"], "min_score_to_email", j, min_score):
                        ranked.append(j)
                else:
                    unranked.append(j)
            ranked.sort(key=lambda j: -j["rank"]["score"])
            tailor_top(cfg, ranked, resume)
    else:
        unranked = new

    body = build_html(ranked, unranked, failures, len(new), min_score)
    REPORTS_DIR.mkdir(exist_ok=True)
    report = REPORTS_DIR / f"{dt.date.today()}.html"
    report.write_text(f"<!doctype html><meta charset='utf-8'><title>Job matches</title>{body}", encoding="utf-8")
    log(f"report saved: {report}")

    for j in sorted(new, key=lambda j: -j.get("rank", {}).get("score", 0)):
        s = j.get("rank", {}).get("score", "-")
        print(f"  [{s:>2}] {j.get('posted') or '?'} | {j['title']} | {j['company']} | {j['location']}")

    if args.dry_run:
        return
    if cfg["email"].get("enabled") and (ranked or unranked):
        top = f" (top: {ranked[0]['title']} @ {ranked[0]['company']})" if ranked else ""
        try:
            attach = ([p for j in ranked if j.get("tailored")
                       for p in (j["tailored"]["pdf"], j["tailored"].get("cover", {}).get("pdf")) if p]
                      if cfg.get("tailoring", {}).get("attach_to_email", True) else [])
            send_email(cfg, f"{len(ranked)} job matches for {dt.date.today():%b %d}{top}", body, attach)
        except Exception as e:
            log(f"  email failed: {e}")
    import apply
    apply.enqueue([j for j in ranked if j.get("tailored")])
    # Mark as seen only postings that were actually scored, so failed rankings retry tomorrow.
    now = dt.date.today().isoformat()
    for j in new:
        if "rank" in j or args.no_rank:
            r = j.get("rank", {})
            seen[j["id"]] = {"date": seen.get(j["id"], {}).get("date", now), "title": j["title"],
                             "company": j["company"], "key": j["key"], "url": j["url"],
                             "score": r.get("score"), "category": r.get("category"), "reason": r.get("reason"),
                             "gaps": r.get("gaps"), "blocker": r.get("blocker")}
            if j["id"] in retry:
                seen[j["id"]]["rescored"] = now
    for sid in stale:
        seen.setdefault(sid, {"date": now, "stale": True})
    SEEN_FILE.write_text(json.dumps(seen, indent=1))


if __name__ == "__main__":
    sys.exit(main())
