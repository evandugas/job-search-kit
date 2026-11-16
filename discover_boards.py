"""Suggest new company job boards to add to config.toml (run monthly or so).

Reads every job on the VC portfolio boards in config.toml (aggregators.consider_boards), takes the
company's own job board from each job's apply link (Greenhouse / Lever / Ashby / Workable /
Workday), skips boards already configured, and lists the rest with their open local (config.toml locations) / remote
jobs and how many pass the title filter. Nothing is changed; copy the lines you want into config.toml.

    python discover_boards.py
"""
import re
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

import jobsearch as js

ATS = [("greenhouse", r"greenhouse\.io/(?:embed/job_app\?for=)?([a-z0-9_-]+)"),
       ("lever", r"jobs\.lever\.co/([a-z0-9_.-]+)"),
       ("ashby", r"jobs\.ashbyhq\.com/([^/?#]+)"),
       ("workable", r"apply\.workable\.com/([a-z0-9_-]+)"),
       ("workday", r"(https://[a-z0-9.-]+\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?[A-Za-z0-9_-]+)")]
FETCH = {"greenhouse": js.fetch_greenhouse, "lever": js.fetch_lever, "ashby": js.fetch_ashby,
         "workable": js.fetch_workable}


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    cfg = js.tomllib.loads((js.ROOT / "config.toml").read_text(encoding="utf-8"))
    b = cfg["boards"]
    have = {(a, t.lower()) for a in FETCH for t in b.get(a, [])} | \
           {("workday", w["url"].lower()) for w in b.get("workday", [])}
    nofilter = dict(cfg, search={k: v for k, v in cfg["search"].items() if k != "max_posted_age_days"})
    keep = js.make_filter(nofilter)
    title_ok = lambda j: keep(j, full=False)

    boards = defaultdict(set)  # (ats, token) -> company names seen on VC boards
    for vc in cfg.get("aggregators", {}).get("consider_boards", []):
        js.log(f"reading {vc['board']}...")
        for j in js.consider_jobs(vc["host"], vc["board"]):
            url = j.get("applyUrl") or j.get("url") or ""
            for ats, rx in ATS:
                if m := re.search(rx, url):
                    boards[(ats, m.group(1).lower())].add(j.get("companyName") or "?")
                    break
    new = [k for k in boards if k not in have]
    js.log(f"{len(boards)} company boards on VC boards, {len(new)} not in config.toml; checking them...")

    def check(key):
        ats, token = key
        try:
            if ats == "workday":
                jobs = js.fetch_workday(token, token, cfg["boards"].get("workday_search_terms", []), keep)
            else:
                jobs = FETCH[ats](token)
        except Exception as e:
            return key, None, str(e)[:60]
        local = [j for j in jobs if keep(j, full=False, check_location=True)]
        return key, (len(jobs), len(local), [j["title"] for j in local if title_ok(j)][:4]), None

    with ThreadPoolExecutor(12) as ex:
        results = list(ex.map(check, new))
    results.sort(key=lambda r: -(r[1][1] if r[1] else -1))
    print("\nSuggested additions (open jobs / local or remote jobs passing the title filter):")
    for (ats, token), counts, err in results:
        name = ", ".join(sorted(boards[(ats, token)]))
        if err:
            print(f"  [error]  {ats} {token}  ({name}): {err}")
        elif counts[1]:
            print(f"  {counts[0]:>4} / {counts[1]:>3}  {ats:<10} {token}  ({name})  e.g. {'; '.join(counts[2])}")
    quiet = [f"{ats}:{token}" for (ats, token), c, e in results if c and not c[1]]
    if quiet:
        print(f"\nNo matching local/remote jobs right now ({len(quiet)}): {', '.join(quiet)}")



if __name__ == "__main__":
    main()
