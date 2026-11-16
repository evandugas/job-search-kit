"""Tailor the resume to a specific job posting with Claude, then render it to PDF (via headless
Chrome/Edge/Chromium) and DOCX. Claude may only select, reorder, and rephrase content that is already in the
resume files; it is told never to add skills, metrics, or experience.

Standalone use (re-tailor for one posting from the last run):
    python tailor.py "<part of the job title or company>"
Re-render a tailored resume from its saved JSON (layout changes only, no Claude call):
    python tailor.py --render "tailored/<date>/<Name Company (Title)>.json"
"""
import html
import json
import re
import os
import shutil
import subprocess
from pathlib import Path

import candidate

# Browsers that can print HTML to PDF headlessly, checked in order.
_PF, _PF86, _LOCAL = (os.environ.get(k, "") for k in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"))
BROWSER_PATHS = [
    # Windows
    rf"{_PF86}\Microsoft\Edge\Application\msedge.exe", rf"{_PF}\Microsoft\Edge\Application\msedge.exe",
    rf"{_PF}\Google\Chrome\Application\chrome.exe", rf"{_PF86}\Google\Chrome\Application\chrome.exe",
    rf"{_LOCAL}\Google\Chrome\Application\chrome.exe",
    # macOS
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
]
BROWSER_NAMES = ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
                 "microsoft-edge", "msedge", "chrome"]


def find_browser():
    for path in BROWSER_PATHS:
        if path and not path.startswith("\\") and Path(path).exists():
            return path
    return next((p for p in map(shutil.which, BROWSER_NAMES) if p), None)

TAILOR_PROMPT = """You are an expert resume writer. Tailor the candidate's resume to the job posting below.
{profile}

The candidate's documents follow, newest first. There may be several versions targeted at different
role types; start from the version closest to this job and draw TRUE content from any other version. When versions disagree on facts (degree, dates,
GPA, titles), the NEWEST one wins.
A file named corrections.txt, if present, overrides ALL resume files (including the newest).

STRICT HONESTY RULES (most important):
- Use ONLY facts present in the candidate's documents. Never invent or inflate skills, tools,
  metrics, numbers, titles, employers, dates, degrees, or responsibilities.
- You MAY: reorder sections/entries/bullets, choose which projects and bullets to include,
  rephrase bullets to foreground the parts relevant to this job, use the job's terminology
  where it accurately describes what the candidate did, rewrite the summary, and reorder skills.
- If the job wants something the candidate lacks, do NOT add it; list it in "missing_keywords".

FORMAT: {pages_text} at normal spacing (the layout is not stretched, so short content leaves
blank space), so include every relevant entry and bullet rather than cutting strong material:
aim for roughly {words} words in total. Summary of 2-3 sentences, written in implied first person with
no pronouns (never "he", "his", "she", "they" or the candidate's name; e.g. "Designed...", "Brings...").
{entries} experience/project entries with 2-5 bullets each (most relevant first). Concise bullets (ideally
one line, never more than two) that start with strong verbs. Keep entry subtitles short
("Org | Role or team"); put location in the subtitle, not "right". List each degree plainly.
Dates: always "Mon YYYY - Mon YYYY" (e.g. "Jan 2026 - May 2026"); a degree gets only its date.

Respond with ONLY a JSON object, no prose, no code fences:
{{
  "name": "...",
  "contact": ["{location}", "email", "phone or linkedin...", "website/portfolio if any..."],
  "summary": "...",
  "sections": [
    {{"heading": "Education", "entries": [{{"title": "School", "subtitle": "Degree, GPA", "right": "date", "bullets": []}}]}},
    {{"heading": "Skills", "lines": [{{"label": "Category", "text": "comma-separated items"}}]}},
    {{"heading": "Experience", "entries": [{{"title": "Role / project", "subtitle": "Org | Team", "right": "dates", "bullets": ["..."]}}]}}
  ],
  "changes": ["short notes on what you emphasized or reordered for this job"],
  "missing_keywords": ["requirements from the posting the candidate genuinely lacks"]
}}

=== CANDIDATE DOCUMENTS ===
{resume}

=== JOB POSTING ===
Title: {title}
Company: {company}
Location: {job_location}
Description:
{description}
"""


COVER_PROMPT = """You are an expert cover-letter writer. Write a cover letter for the job posting below.
{profile}

STRICT HONESTY RULES (most important):
- Use ONLY facts from the candidate's documents and the tailored resume. Never invent or inflate
  skills, tools, metrics, titles, employers, dates, or degrees.
- Say nothing about the company beyond what the posting itself states (no invented products,
  values, news, or people). Address the hiring team, not a named person, unless the posting names one.
- If the job wants something the candidate lacks, don't claim it; you may point to related,
  real experience.

STYLE: one page, 250-350 words, 3-4 paragraphs. Specific and plain: name the role, connect 2-3
concrete experiences (with their real numbers) to the posting's main duties, close briefly. No
cliches ("I am writing to express my interest", "passionate", "dynamic", "perfect fit").

Also write "why_interested": 2-3 sentences answering "Why are you interested in this role?" for
application forms, under the same rules.

Respond with ONLY a JSON object, no prose, no code fences:
{{"greeting": "Dear Hiring Team,", "paragraphs": ["..."], "why_interested": "..."}}

=== CANDIDATE DOCUMENTS ===
{resume}

=== TAILORED RESUME FOR THIS JOB (JSON) ===
{tailored}

=== JOB POSTING ===
Title: {title}
Company: {company}
Location: {location}
Description:
{description}
"""


def ask_claude(claude, prompt, cwd):
    r = subprocess.run([claude, "-p", "--output-format", "text"], input=prompt, capture_output=True,
                       text=True, encoding="utf-8", timeout=900, cwd=cwd)
    if r.returncode != 0:
        raise RuntimeError(f"claude failed: {r.stderr.strip()[:300]}")
    m = re.search(r"\{.*\}", r.stdout, re.S)
    if not m:
        raise RuntimeError(f"no JSON in Claude output: {r.stdout[:300]}")
    return json.loads(m.group(0))


# --------------------------------------------------------------------------- HTML -> PDF

def sizes(scale):
    """Layout for a fill level: below 1 shrinks everything evenly; above 1 mostly adds vertical
    space and grows the font only slightly, so a stretched page still looks like a resume."""
    font = 10.3 * (scale if scale < 1 else min(1 + (scale - 1) * 0.35, 1.1))
    gap = scale if scale < 1 else scale ** 2.5
    return font, gap


CSS = """
@page { size: Letter; margin: 0.5in 0.6in; }
body { font-family: Calibri, 'Segoe UI', Arial, sans-serif; font-size: __FONT__pt; color: #111;
       line-height: __LH__; margin: 0; }
h1 { font-size: 20pt; margin: 0; text-align: center; letter-spacing: 1px; }
.contact { text-align: center; font-size: 9.5pt; color: #333; margin: 2px 0 calc(6px * __GAP__); }
h2 { font-size: 1.05em; text-transform: uppercase; letter-spacing: .6px; color: #1f3a5f;
     border-bottom: 1px solid #1f3a5f; margin: calc(10px * __GAP__) 0 calc(4px * __GAP__);
     padding-bottom: 1px; break-after: avoid; }
p, li { text-wrap: pretty; }
.summary { margin: 0; }
.entry { margin: 0 0 calc(5px * __GAP__); break-inside: avoid; }
.row { display: flex; justify-content: space-between; align-items: baseline; gap: 12px; }
.title { font-weight: bold; }
.sub { font-style: italic; color: #444; }
.right { white-space: nowrap; color: #333; }
ul { margin: calc(1px * __GAP__) 0 0 0; padding-left: 16px; }
li { margin: 0 0 calc(1.5px * __GAP__); }
.skill { margin: 0 0 calc(2px * __GAP__); }
"""


def blocks(r):
    """The resume as a flat list of (first text, html) blocks: header, headings, skill lines, entries."""
    e = html.escape
    out = [(r.get("name", ""), f"<div><h1>{e(r.get('name', ''))}</h1><div class='contact'>"
            f"{' &nbsp;|&nbsp; '.join(e(c) for c in r.get('contact', []))}</div></div>")]
    if r.get("summary"):
        out += [("Summary", "<h2>Summary</h2>"), (r["summary"], f"<p class='summary'>{e(r['summary'])}</p>")]
    for sec in r.get("sections", []):
        out.append((sec.get("heading", ""), f"<h2>{e(sec.get('heading', ''))}</h2>"))
        for line in sec.get("lines", []):
            out.append((line.get("label", ""), f"<div class='skill'><b>{e(line.get('label', ''))}:</b> "
                                                f"{e(line.get('text', ''))}</div>"))
        for ent in sec.get("entries", []):
            out.append((ent.get("title", ""),
                        "<div class='entry'><div class='row'>"
                        f"<span class='title'>{e(ent.get('title', ''))}</span>"
                        f"<span class='right'>{e(ent.get('right', ''))}</span></div>"
                        + (f"<div class='sub'>{e(ent['subtitle'])}</div>" if ent.get("subtitle") else "")
                        + ("<ul>" + "".join(f"<li>{e(b)}</li>" for b in ent["bullets"]) + "</ul>"
                           if ent.get("bullets") else "")
                        + "</div>"))
    return out


def to_html(r, scale=1.0):
    font, gap = sizes(scale)
    css = (CSS.replace("__FONT__", f"{font:.2f}").replace("__GAP__", f"{gap:.3f}")
           .replace("__LH__", f"{1.22 + max(0, gap - 1) * 0.08:.3f}"))
    parts = [b for _, b in blocks(r)]
    return f"<!doctype html><meta charset='utf-8'><style>{css}</style>\n" + "\n".join(parts)


def html_to_pdf(html_path, pdf_path):
    browser = find_browser()
    if not browser:
        raise RuntimeError("no Chrome, Edge or Chromium found; install one to make PDFs")
    subprocess.run([browser, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                    f"--print-to-pdf={pdf_path}", Path(html_path).resolve().as_uri()],
                   capture_output=True, timeout=120)
    if not Path(pdf_path).exists():
        raise RuntimeError(f"{Path(browser).name} did not produce a PDF")


# --------------------------------------------------------------------------- DOCX

def to_docx(r, path, scale=1.0):
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Inches, Pt

    from docx.shared import RGBColor

    font, gap = sizes(scale)
    doc = Document()
    for s in doc.sections:
        s.top_margin = s.bottom_margin = Inches(0.5)
        s.left_margin = s.right_margin = Inches(0.6)
    normal = doc.styles["Normal"]
    normal.font.name, normal.font.size = "Calibri", Pt(round(font, 1))
    width = doc.sections[0].page_width - doc.sections[0].left_margin - doc.sections[0].right_margin

    def para(space_after=1):
        p = doc.add_paragraph()
        p.paragraph_format.space_after = Pt(space_after * gap)
        p.paragraph_format.space_before = Pt(0)
        return p

    def rule(p):  # bottom border under section headings
        pPr = p._p.get_or_add_pPr()
        bdr = OxmlElement("w:pBdr")
        bottom = OxmlElement("w:bottom")
        for k, v in (("val", "single"), ("sz", "6"), ("space", "1"), ("color", "1F3A5F")):
            bottom.set(qn(f"w:{k}"), v)
        bdr.append(bottom)
        pPr.append(bdr)

    p = para(0)
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run(r.get("name", ""))
    run.bold, run.font.size = True, Pt(19)
    p = para(4)
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.add_run("  |  ".join(r.get("contact", []))).font.size = Pt(9.5)

    def heading(text):
        h = para(3)
        h.paragraph_format.space_before = Pt(7 * gap)
        h.paragraph_format.keep_with_next = True
        run = h.add_run(text.upper())
        run.bold, run.font.size = True, Pt(round(font * 1.05, 1))
        run.font.color.rgb = RGBColor(0x1F, 0x3A, 0x5F)
        rule(h)

    if r.get("summary"):
        heading("Summary")
        para(2).add_run(r["summary"])
    for sec in r.get("sections", []):
        heading(sec.get("heading", ""))
        for line in sec.get("lines", []):
            p = para(1)
            p.add_run(f"{line.get('label', '')}: ").bold = True
            p.add_run(line.get("text", ""))
        for ent in sec.get("entries", []):
            p = para(0)
            p.paragraph_format.tab_stops.add_tab_stop(width, WD_TAB_ALIGNMENT.RIGHT)
            p.add_run(ent.get("title", "")).bold = True
            p.add_run("\t" + ent.get("right", ""))
            if ent.get("subtitle"):
                sp = para(0)
                sp.add_run(ent["subtitle"]).italic = True
            for b in ent.get("bullets", []):
                bp = doc.add_paragraph(b, style="List Bullet")
                bp.paragraph_format.space_after = Pt(1.5 * gap)
            # Keep each entry on one page, like the PDF.
            doc.paragraphs[-1].paragraph_format.space_after = Pt(5 * gap)
            for q in doc.paragraphs[-(1 + bool(ent.get("subtitle")) + len(ent.get("bullets", []))):-1]:
                q.paragraph_format.keep_with_next = True
    doc.save(path)


# --------------------------------------------------------------------------- cover letter

LETTER_CSS = """
@page { size: Letter; margin: 0.9in 1in; }
body { font-family: Calibri, 'Segoe UI', Arial, sans-serif; font-size: 11pt; color: #111; line-height: 1.38; margin: 0; }
h1 { font-size: 17pt; margin: 0; letter-spacing: 1px; }
.contact { font-size: 9.5pt; color: #333; margin: 2px 0 22px; border-bottom: 1px solid #444; padding-bottom: 6px; }
p { margin: 0 0 11px; }
"""


def letter_html(r, name, contact, date_text):
    e = html.escape
    return "\n".join([f"<!doctype html><meta charset='utf-8'><style>{LETTER_CSS}</style>",
                      f"<h1>{e(name)}</h1>",
                      f"<div class='contact'>{' &nbsp;|&nbsp; '.join(e(c) for c in contact)}</div>",
                      f"<p>{e(date_text)}</p>", f"<p>{e(r.get('greeting', 'Dear Hiring Team,'))}</p>"]
                     + [f"<p>{e(p)}</p>" for p in r.get("paragraphs", [])]
                     + [f"<p>Sincerely,<br>{e(name)}</p>"])


def letter_docx(r, name, contact, date_text, path):
    from docx import Document
    from docx.shared import Inches, Pt

    doc = Document()
    for s in doc.sections:
        s.top_margin = s.bottom_margin = Inches(0.9)
        s.left_margin = s.right_margin = Inches(1)
    doc.styles["Normal"].font.name, doc.styles["Normal"].font.size = "Calibri", Pt(11)
    p = doc.add_paragraph()
    run = p.add_run(name)
    run.bold, run.font.size = True, Pt(17)
    p.paragraph_format.space_after = Pt(0)
    doc.add_paragraph("  |  ".join(contact)).runs[0].font.size = Pt(9.5)
    for text in [date_text, r.get("greeting", "Dear Hiring Team,"), *r.get("paragraphs", []),
                 f"Sincerely,\n{name}"]:
        doc.add_paragraph(text).paragraph_format.space_after = Pt(9)
    doc.save(path)


def cover_letter(job, resume_text, tailored, base, claude, cwd):
    """Writes '<base> - Cover Letter.pdf/.docx' next to the tailored resume."""
    import datetime as dt
    prompt = COVER_PROMPT.format(profile=candidate.writer_block(), resume=resume_text[:150000], tailored=json.dumps(tailored)[:20000],
                                 title=job["title"], company=job["company"], location=job["location"],
                                 description=job["description"][:8000])
    r = ask_claude(claude, prompt, cwd)
    name, contact = tailored.get("name", ""), tailored.get("contact", [])
    date_text = f"{dt.date.today():%B} {dt.date.today().day}, {dt.date.today().year}"
    stem = base.parent / f"{base.name} - Cover Letter"
    html_path, pdf_path, docx_path = (stem.parent / (stem.name + ext) for ext in (".html", ".pdf", ".docx"))
    html_path.write_text(letter_html(r, name, contact, date_text), encoding="utf-8")
    pdf_path.unlink(missing_ok=True)
    html_to_pdf(html_path, pdf_path)
    html_path.unlink()
    letter_docx(r, name, contact, date_text, docx_path)
    return {"pdf": pdf_path, "docx": docx_path, "why_interested": r.get("why_interested", "")}


# --------------------------------------------------------------------------- main entry

def page_fill(pdf_path):
    """How full each page is: bottom of its last text line as a fraction of the usable height."""
    import pymupdf
    top, bottom = 0.5 * 72, 10.5 * 72  # @page margins
    with pymupdf.open(pdf_path) as d:
        return [round((max((b[3] for b in p.get_text("blocks")), default=top) - top) / (bottom - top), 3)
                for p in d]


MONTH_YEAR = re.compile(r"\b[A-Z][a-z]{2,8}\.? \d{4}\b")


def clean_date(text):
    """One date format everywhere: "Mon YYYY - Mon YYYY" (plain hyphen). Notes like "Coursework completed
    May 2026; degree conferred Aug 2026" become just the final date ("Aug 2026")."""
    text = re.sub(r"\s*[–—-]\s*", " - ", text.strip())
    if re.search(r"complet|confer|degree|expected|coursework|;", text, re.I):
        dates = MONTH_YEAR.findall(text)
        if dates:
            return dates[-1]
    return text


def render_resume(r, base):
    """Write <base>.pdf/.docx/.json at near-normal spacing (no stretched whitespace). Over the page
    target (profile.toml [resume] pages, 1 or 2) it shrinks to fit; otherwise it picks the spacing
    (0.97-1.08) at which the emptiest page is fullest. Returns (pdf, docx, page fill fractions); short
    pages are filled with content by tailor()."""
    from pypdf import PdfReader
    # Not with_suffix(): titles like "Sr. Scientist" contain dots.
    html_path, pdf_path, docx_path, json_path = (base.parent / (base.name + ext)
                                                 for ext in (".html", ".pdf", ".docx", ".json"))
    for ent in (ent for sec in r.get("sections", []) for ent in sec.get("entries", [])):
        ent["right"] = clean_date(ent.get("right", ""))
    json_path.write_text(json.dumps(r, indent=1), encoding="utf-8")

    def pages(scale):
        html_path.write_text(to_html(r, scale), encoding="utf-8")
        pdf_path.unlink(missing_ok=True)
        html_to_pdf(html_path, pdf_path)
        return len(PdfReader(pdf_path).pages)

    target = candidate.pages()
    n = pages(1.0)
    if n > target:
        best = next((s for s in (0.96, 0.92, 0.88, 0.85) if pages(s) <= target), 0.85)
    else:
        best, best_fill = 1.0, min(page_fill(pdf_path))
        for s in (0.97, 0.98, 0.99, 1.01, 1.02, 1.03, 1.04, 1.05, 1.06, 1.08):
            if pages(s) == n and min(f := page_fill(pdf_path)) > best_fill:
                best, best_fill = s, min(f)
    pages(best)
    fill = page_fill(pdf_path)
    to_docx(r, docx_path, best)
    html_path.unlink()
    return pdf_path, docx_path, fill


FILL_PROMPT = """The tailored resume below renders to {pages} page(s); the page fill is {fill} (1.0 =
full). The candidate wants {target} FULL page(s) with no blank space, and does not want their experience
cut down. Add about {lines} more lines of content (a line is ~110 characters) so the resume fills {target}
page(s): put back relevant entries or bullets that were left out, or give existing entries fuller
bullets. Same STRICT HONESTY RULES: only facts from the candidate's documents below, nothing
invented. Keep everything else as is. Respond with ONLY the full updated JSON object, same format.

=== TAILORED RESUME (JSON) ===
{tailored}

=== CANDIDATE DOCUMENTS ===
{resume}
"""

LEGAL = re.compile(r",?\s+\b(inc|llc|llp|ltd|plc|corp|corporation|co|gmbh|lp|advisory services)\b\.?", re.I)
ALIASES = {"pricewaterhousecoopers": "PwC"}


def file_stem(name, company, title):
    """'Jane Doe PwC (AI Engineer - Data Scientist)': plain enough to send as an attachment."""
    c = LEGAL.sub("", company).strip()
    c = next((v for k, v in ALIASES.items() if k in c.lower()), c)
    if c.isupper() and len(c) > 5:
        c = c.title()
    t = re.sub(r"\s*/\s*", " - ", title)
    s = re.sub(r"[^\w\-.,&() ]+", "", f"{name} {c} ({t})")
    return re.sub(r"\s+", " ", s).strip()[:120]


def tailor(job, resume_text, out_dir, claude, cwd, cover=False):
    """Returns dict with paths + Claude's notes (and a cover letter if `cover`). Raises on failure."""
    target = candidate.pages()
    prompt = TAILOR_PROMPT.format(
        profile=candidate.writer_block(), location=candidate.resume_location(),
        pages_text="ONE FULL page" if target == 1 else "two FULL pages",
        words="450-550" if target == 1 else "1000-1100", entries="3-5" if target == 1 else "5-7",
        resume=resume_text[:150000], title=job["title"], company=job["company"],
        job_location=job["location"], description=job["description"][:8000])
    r = ask_claude(claude, prompt, cwd)
    out_dir.mkdir(parents=True, exist_ok=True)
    base = out_dir / file_stem(r.get("name") or "Resume", job["company"], job["title"])
    pdf_path, docx_path, fill = render_resume(r, base)
    if len(fill) < target or min(fill) < 0.9:  # short: fill with real content, not whitespace
        lines = round(((target - len(fill)) + sum(1 - f for f in fill)) * 55)
        try:
            more = ask_claude(claude, FILL_PROMPT.format(pages=len(fill), fill=fill, lines=lines, target=target,
                                                         tailored=json.dumps(r), resume=resume_text[:150000]), cwd)
            if more.get("sections"):
                r = more
                pdf_path, docx_path, fill = render_resume(r, base)
        except Exception as e:
            print(f"  (couldn't lengthen the resume: {e})")
    res = {"pdf": pdf_path, "docx": docx_path, "changes": r.get("changes", []),
           "missing": r.get("missing_keywords", [])}
    if cover:
        res["cover"] = cover_letter(job, resume_text, r, base, claude, cwd)
    return res


if __name__ == "__main__":
    import datetime as dt
    import shutil
    import sys
    import jobsearch as js

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    if sys.argv[1] == "--render":  # re-lay out a saved resume JSON without asking Claude again
        jp = Path(sys.argv[2]).resolve()
        print(*render_resume(json.loads(jp.read_text(encoding="utf-8")), jp.parent / jp.stem), sep="\n")
        sys.exit()
    needle = sys.argv[1].lower()
    cfg = js.tomllib.loads((js.ROOT / "config.toml").read_text(encoding="utf-8"))
    jobs, _, _ = js.collect(cfg, {})
    matches = [j for j in jobs if needle in f"{j['title']} {j['company']}".lower()]
    if not matches:
        sys.exit(f"No current posting matches {sys.argv[1]!r}")
    claude = shutil.which("claude") or shutil.which("claude.cmd")
    for j in matches[:3]:
        res = tailor(j, js.read_resume(), js.ROOT / "tailored" / str(dt.date.today()), claude, js.ROOT)
        print(f"{j['title']} @ {j['company']}\n  {res['pdf']}\n  {res['docx']}")
        print("  changes:", *res["changes"], sep="\n    - ")
        print("  missing:", ", ".join(res["missing"]) or "none")
