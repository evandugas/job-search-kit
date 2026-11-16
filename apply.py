"""Application queue: top matches with their tailored resume + cover letter, and what you've
applied to. The daily run adds its top matches automatically. Filling in the actual application
form is done with Claude Code in your browser (see CLAUDE.md); you always click Submit yourself.

Usage:
    python apply.py list [--all]          # what's waiting (or everything, incl. applied/skipped)
    python apply.py email [search]        # email the waiting roles (apply links + files attached)
    python apply.py show <search>         # everything needed to fill one application
    python apply.py prep <url>            # tailor resume + cover letter for any posting URL
    python apply.py rank [search]         # score queued roles that have no score (e.g. ones added by prep)
    python apply.py backfill [min_score]  # prep today's already-emailed matches (default 8+)
    python apply.py done <search>         # mark as applied
    python apply.py skip <search>         # not applying
<search> is any part of the company + title, e.g. "sanofi quality".
"""
import datetime as dt
import json
import re
import shutil
import sys
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent
QUEUE_FILE = ROOT / "applications.json"


def load():
    return json.loads(QUEUE_FILE.read_text(encoding="utf-8")) if QUEUE_FILE.exists() else {}


def save(q):
    QUEUE_FILE.write_text(json.dumps(q, indent=1, default=str), encoding="utf-8")


def apply_link(j):
    import jobsearch as js
    return js.apply_link(j)


def as_job(k, v):
    """Queue entry -> the job shape jobsearch.build_html expects."""
    cover = {"pdf": v["cover_pdf"], "why_interested": v.get("why_interested", "")} if v.get("cover_pdf") else None
    return {"id": k, "title": v["title"], "company": v["company"], "location": v["location"], "url": v["url"],
            "apply_url": v["apply_url"] if v["apply_url"] != v["url"] else None, "posted": None,
            "description": v.get("description", ""),
            "rank": {"score": v.get("score"), "category": v.get("category"), "reason": v.get("reason", ""),
                     "gaps": v.get("gaps", "")},
            "tailored": {"pdf": v["resume_pdf"], "changes": [], "missing": [], "cover": cover} if v.get("resume_pdf") else None}


def matches(v, search):
    return all(w in f"{v['company']} {v['title']}".lower() for w in search.lower().split())


def rank_queue(search=""):
    """Score waiting roles that have no score yet (prep doesn't score), and record it in seen_jobs."""
    import jobsearch as js
    cfg = js.tomllib.loads((ROOT / "config.toml").read_text(encoding="utf-8"))
    q = load()
    todo = [as_job(k, v) for k, v in q.items()
            if v["status"] == "to apply" and v.get("score") is None and matches(v, search)]
    if not todo:
        sys.exit("No unscored roles waiting.")
    scores = js.rank(todo, js.read_resume(), cfg["ranking"]["max_jobs_per_claude_call"])
    seen = json.loads(js.SEEN_FILE.read_text())
    for j in todo:
        r = scores.get(j["id"])
        if not r:
            print(f"  not scored: {j['company']} | {j['title']}")
            continue
        fields = {k: r.get(k) for k in ("score", "category", "reason", "gaps", "blocker")}
        q[j["id"]].update(fields)
        if j["id"] in seen:
            seen[j["id"]].update(fields)
        print(f"  [{r.get('score')}] {j['company']} | {j['title']}")
    save(q)
    js.SEEN_FILE.write_text(json.dumps(seen, indent=1))


def email_queue(search=""):
    """Email everything still waiting in the queue (or just the roles matching `search`), with files attached."""
    import jobsearch as js
    cfg = js.tomllib.loads((ROOT / "config.toml").read_text(encoding="utf-8"))
    jobs = sorted((as_job(k, v) for k, v in load().items() if v["status"] == "to apply" and matches(v, search)),
                  key=lambda j: -(j["rank"]["score"] or 0))
    if not jobs:
        sys.exit("Nothing waiting in the queue.")
    body = js.build_html(jobs, [], [], len(jobs), 0).replace(
        f"{len(jobs)} new matching postings today; {len(jobs)} scored 0+.",
        f"{len(jobs)} roles ready to apply to, each with its tailored resume and cover letter attached.")
    attach = [p for j in jobs if j.get("tailored")
              for p in (j["tailored"]["pdf"], (j["tailored"].get("cover") or {}).get("pdf")) if p and Path(p).exists()]
    js.send_email(cfg, f"{len(jobs)} applications ready (top: {jobs[0]['title']} @ {jobs[0]['company']})", body, attach)
    print(f"Emailed {len(jobs)} roles with {len(attach)} attachments.")


MONEY = r"\$\s?\d{1,3}(?:,\d{3})+(?:\.\d{2})?|\$\s?\d+(?:\.\d{1,2})?\s?[kK]?"


def salary_range(text):
    """First pay range stated in a posting, e.g. '$85,000 - $105,000' or '$30 - $38/hour' ('' if none)."""
    m = re.search(rf"({MONEY})\s*(?:-|–|—|to)\s*({MONEY})", text or "")
    if not m:
        return ""
    lo, hi = (re.sub(r"\s|\.00$", "", g) for g in m.groups())
    hourly = (re.search(r"per hour|/\s?h(ou)?r|hourly", text[m.end():m.end() + 40], re.I)
              or (not hi.lower().endswith("k") and float(re.sub(r"[$,]", "", hi)) < 200))  # e.g. "$27.24 - $36.32"
    return f"{lo} - {hi}" + ("/hour" if hourly else "")


def entry(j):
    t = j.get("tailored") or {}
    c = t.get("cover") or {}
    r = j.get("rank") or {}
    return {"title": j["title"], "company": j["company"], "location": j["location"], "url": j["url"],
            "apply_url": apply_link(j), "score": r.get("score"), "category": r.get("category"), "reason": r.get("reason", ""),
            "gaps": r.get("gaps", ""), "description": j.get("description", ""),
            "salary_range": salary_range(j.get("description", "")),
            "found": str(dt.date.today()), "status": "to apply",
            "resume_pdf": str(t.get("pdf", "")), "resume_docx": str(t.get("docx", "")),
            "cover_pdf": str(c.get("pdf", "")), "cover_docx": str(c.get("docx", "")),
            "why_interested": c.get("why_interested", "")}


def enqueue(jobs):
    """Add (or refresh) postings; never resets the status of something already applied/skipped."""
    q = load()
    for j in jobs:
        e = entry(j)
        if j["id"] in q:
            e["status"], e["found"] = q[j["id"]]["status"], q[j["id"]]["found"]
            e = {**q[j["id"]], **{k: v for k, v in e.items() if v}}
        q[j["id"]] = e
    save(q)


def find(needle, q=None, statuses=None):
    q = load() if q is None else q
    words = needle.lower().split()
    hits = [(k, v) for k, v in q.items() if all(w in f"{v['company']} {v['title']}".lower() for w in words)
            and (statuses is None or v["status"] in statuses)]
    if not hits:
        sys.exit(f"Nothing in the queue matches {needle!r}. Try `python apply.py list --all`.")
    if len(hits) > 1:
        print(f"{len(hits)} matches; be more specific:")
        for _, v in hits:
            print(f"  - {v['company']} | {v['title']} [{v['status']}]")
        sys.exit(1)
    return hits[0]


# --------------------------------------------------------------------------- fetch a posting by URL

def fetch_posting(url):
    """Title, company, location and description for a posting URL (LinkedIn, Workday, Greenhouse,
    Lever, Ashby, or any page with schema.org JobPosting data)."""
    import jobsearch as js
    u = urllib.parse.urlparse(url)
    host, parts = u.netloc.lower(), [p for p in u.path.split("/") if p]

    if "linkedin.com" in host:
        jid = re.search(r"(\d{8,})", url).group(1)
        page = js.http_text(f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{jid}")
        g = lambda pat: js.strip_html(m.group(1)) if (m := re.search(pat, page, re.S)) else ""
        # The company's own apply link is only shown to signed-in users, so apply_url stays the
        # LinkedIn page; apply assist clicks through to the company site in the user's browser.
        return js.job("li", g(r'topcard__org-name-link[^>]*>(.*?)</a>'), g(r'top-card-layout__title[^>]*>(.*?)</h2>'),
                      g(r'topcard__flavor--bullet[^>]*>(.*?)</span>'), f"https://www.linkedin.com/jobs/view/{jid}",
                      g(r'show-more-less-html__markup[^>]*>(.*?)</div>'))

    if "myworkdayjobs.com" in host:
        site_i = 1 if re.match(r"[a-z]{2}-[A-Z]{2}$", parts[0]) else 0
        site, path = parts[site_i], "/" + "/".join(parts[site_i + 1:])
        d = js.http_json(f"https://{host}/wday/cxs/{host.split('.')[0]}/{site}{path}")
        info = d.get("jobPostingInfo", {})
        company = (d.get("hiringOrganization") or {}).get("name") or host.split(".")[0]
        return js.job("wd", company, info.get("title", ""),
                      "; ".join(filter(None, [info.get("location")] + info.get("additionalLocations", []))),
                      url, js.strip_html(info.get("jobDescription")), posted=js.parse_date(info.get("startDate")))

    if "greenhouse.io" in host and "jobs" in parts:
        token, jid = parts[parts.index("jobs") - 1], parts[parts.index("jobs") + 1]
        d = js.http_json(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs/{jid}?content=true")
        return js.job("gh", d.get("company_name") or token, d["title"], d.get("location", {}).get("name"),
                      d["absolute_url"], js.strip_html(d.get("content")))

    if host == "jobs.lever.co" and len(parts) >= 2:
        d = js.http_json(f"https://api.lever.co/v0/postings/{parts[0]}/{parts[1]}")
        return js.job("lever", parts[0], d["text"], d.get("categories", {}).get("location", ""), d["hostedUrl"],
                      d.get("descriptionPlain", "") + "\n" + js.strip_html(
                          " ".join(l.get("content", "") for l in d.get("lists", []))))

    if host == "jobs.ashbyhq.com" and len(parts) >= 2:
        for j in js.fetch_ashby(parts[0]):
            if parts[1] in j["url"]:
                return j

    page = js.http_text(url)
    post = (js.ld_json(page, "JobPosting") or [None])[0]
    if not post:
        raise RuntimeError("couldn't read a job posting from that page; paste a LinkedIn, Workday, "
                           "Greenhouse, Lever or Ashby link instead")
    org = post.get("hiringOrganization") or {}
    return js.job("web", org.get("name", "?") if isinstance(org, dict) else str(org), post.get("title", ""),
                  js.ld_location(post), url, js.strip_html(post.get("description")))


def prep(job_or_url, score=None, rank=None):
    import jobsearch as js
    import tailor
    j = fetch_posting(job_or_url) if isinstance(job_or_url, str) else job_or_url
    if not j.get("description"):
        sys.exit(f"No description found for {j['url']}; can't tailor.")
    if rank:
        j["rank"] = rank
    claude = shutil.which("claude") or shutil.which("claude.cmd")
    print(f"Tailoring resume + cover letter for {j['title']} @ {j['company']}...")
    j["tailored"] = tailor.tailor(j, js.read_resume(), ROOT / "tailored" / str(dt.date.today()), claude, ROOT,
                                  cover=True)
    enqueue([j])
    return j


# --------------------------------------------------------------------------- CLI

def show(v):
    print(f"{v['company']} | {v['title']}  [{v['status']}]")
    print(f"  location:   {v['location']}")
    print(f"  score:      {v.get('score')}  {v.get('reason', '')}")
    if v.get("gaps"):
        print(f"  gaps:       {v['gaps']}")
    print(f"  posting:    {v['url']}")
    print(f"  apply at:   {v['apply_url']}")
    for k in ("resume_pdf", "resume_docx", "cover_pdf", "cover_docx"):
        print(f"  {k + ':':<12}{v.get(k) or '-'}")
    if v.get("why_interested"):
        print(f"  why interested (for forms): {v['why_interested']}")


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = sys.argv[1:]
    if not args:
        sys.exit(__doc__)
    cmd, rest = args[0], " ".join(a for a in args[1:] if not a.startswith("--"))
    q = load()

    if cmd == "list":
        rows = [v for v in q.values() if "--all" in args or v["status"] == "to apply"]
        for v in sorted(rows, key=lambda v: (v["status"], -(v.get("score") or 0))):
            files = "resume+cover" if v.get("cover_pdf") else ("resume" if v.get("resume_pdf") else "no files")
            print(f"[{v.get('score') or '-':>2}] {v['status']:<9} {v['company']} | {v['title']}  ({files})")
        if not rows:
            print("Queue is empty.")
    elif cmd == "rank":
        rank_queue(rest)
    elif cmd == "email":
        email_queue(rest)
    elif cmd == "show":
        show(find(rest, q)[1])
    elif cmd == "prep":
        j = prep(rest)
        show(load()[j["id"]])
    elif cmd == "backfill":
        min_score = int(rest or 8)
        seen = json.loads((ROOT / "seen_jobs.json").read_text())
        today = str(dt.date.today())
        picks = [(sid, s) for sid, s in seen.items()
                 if s.get("date") == today and (s.get("score") or 0) >= min_score and sid not in q]
        print(f"{len(picks)} of today's matches scored {min_score}+ and aren't queued yet.")
        for sid, s in picks:
            url = sid.split(":", 1)[1]
            try:
                j = fetch_posting(url)
                j["id"], j["title"], j["company"] = sid, s["title"], s["company"]
                prep(j, rank={"score": s["score"]})
            except Exception as e:
                print(f"  failed for {s['company']} | {s['title']}: {e}")
        for k, v in load().items():
            if k in dict(picks):
                print(f"  queued: [{v['score']}] {v['company']} | {v['title']}")
    elif cmd in ("done", "skip"):
        k, v = find(rest, q)
        v["status"] = "applied" if cmd == "done" else "skipped"
        v[f"{v['status']}_on"] = str(dt.date.today())
        save(q)
        print(f"Marked {v['status']}: {v['company']} | {v['title']}")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
