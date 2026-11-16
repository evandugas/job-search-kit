# Job Search Kit

A daily job search that runs on your own computer, powered by [Claude Code](https://claude.com/claude-code).
Every morning it:

1. **Collects** new postings from company job boards (Greenhouse, Lever, Ashby, Workable, Workday),
   LinkedIn's public search, Built In, and VC portfolio boards.
2. **Scores** each one 1-10 against your resume and goals, explaining the fit, the gaps, and any
   deal-breaker.
3. **Tailors** your resume (PDF + Word) for the best matches, plus a cover letter for the top ones.
   Claude may only reorder, select and rephrase what's already in your resume. It is told never to
   invent skills, numbers or experience.
4. **Emails** you a digest: Apply buttons, the tailored files attached, and a drafted answer to
   "Why are you interested in this role?"
5. **Helps you apply.** A Chrome extension fills application forms with your files and standard
   answers. Or you can ask Claude Code to fill one in for you. Either way, **you** review the form and
   click Submit.

Everything personal (resume, answers, password, results) stays on your computer.

---

## Quick start: let Claude set it up

You need two things installed first: **Claude Code** (step 1 below) and the project files (step 2).
After that, Claude can walk you through the rest. It installs Python if you need it, reads your
resume, drafts your profile and search settings with you, and runs a test search.

1. Do steps 1 and 2 below.
2. Open a terminal **in the `job-search-kit` folder** (step 3 below shows how), type `claude`, and press Enter.
3. Type: **`help me set this up`**

Claude follows the setup guide in `CLAUDE.md`. It asks before running each command and never asks
for your passwords. The whole thing takes about 20-30 minutes, most of it the first test search.

If you'd rather answer a list of questions yourself, run `python setup.py` instead (see
[Manual setup](#manual-setup)).

---

## Step 1: Install Claude Code and sign in

Claude Code is Anthropic's command-line assistant. This kit uses it for all the AI work (scoring,
resumes, cover letters), so you need it either way.

**Windows:** open **PowerShell** (Start menu > type "PowerShell") and run:
```powershell
irm https://claude.ai/install.ps1 | iex
```
**macOS / Linux:** open **Terminal** and run:
```bash
curl -fsSL https://claude.ai/install.sh | bash
```
If these commands have changed, use the current instructions at
[claude.com/claude-code](https://claude.com/claude-code). On Windows, if the installer asks for
**Git for Windows**, install it from [git-scm.com](https://git-scm.com/download/win) and run the
command again.

Then **close the terminal, open a new one**, type `claude`, and sign in (see below).

### Connecting Claude

The kit never handles your Claude account or keys itself. It runs `claude -p` (Claude Code in
non-interactive mode) and uses whatever account Claude Code is signed in to. When you first run
`claude`, choose one of these:

| Option | Best for | How usage is paid |
|---|---|---|
| **Claude subscription** (Pro or Max): sign in with your claude.ai account | most people | included in your plan, within its usage limits |
| **Anthropic Console (API) account**: sign in with your Console account, or set an `ANTHROPIC_API_KEY` | people who already use the API | billed per use to your API account |

If you use an API key, set it as a **permanent user environment variable**, not just in one terminal
window, so the daily scheduled run can see it. On Windows: Start > "Edit environment variables for
your account". On macOS/Linux: add it to `~/.zshrc` or `~/.bashrc`. Never put your key in any
file in this folder.

**How much it uses:** each daily run makes about one Claude call per 20 new postings for scoring,
plus 2-3 calls for each tailored resume and one more for each cover letter. `max_per_run` in
`config.toml` caps the number of tailored resumes per day (default 15). `python setup.py --check` sends
one tiny test prompt to confirm you're signed in.

## Step 2: Get the project

- **Easiest:** on this project's GitHub page, click the green **Code** button, then **Download ZIP**.
  Unzip it somewhere you'll keep it, for example your Documents folder. Rename the folder to
  `job-search-kit` if it's called something like `job-search-kit-main`.
- **With git:** `git clone <this repo's URL> job-search-kit`

Keep the folder in one place: the daily schedule points at it.

## Step 3: Open a terminal in the folder

- **Windows:** open the `job-search-kit` folder in File Explorer, click the address bar, type
  `powershell`, and press Enter.
- **macOS:** right-click the folder in Finder and choose **New Terminal at Folder**. (If you don't see
  it, turn it on under System Settings > Keyboard > Keyboard Shortcuts > Services.)
- **Linux:** right-click in the folder and choose **Open in Terminal**.

Now type `claude` and say **help me set this up**, or continue with the manual setup below.

---

## Manual setup

You need:

- **Python 3.11 or newer.**
  - Windows: install from [python.org](https://www.python.org/downloads/) and **tick "Add python.exe to
    PATH"** on the first installer screen. Or run `winget install Python.Python.3.12`.
  - macOS: [python.org](https://www.python.org/downloads/) or `brew install python`.
  - Linux: your package manager.

  Check it with `python --version` (`python3 --version` on macOS/Linux).
- **Google Chrome** (or Microsoft Edge / Chromium), used to make the PDFs.
- **Claude Code**, signed in (step 1).

Then, in a terminal in the folder (on macOS/Linux, type `python3` instead of `python`):

```bash
python -m pip install -r requirements.txt
python setup.py
```

`setup.py` checks everything, asks about you and your search, and writes your personal files:

| File | What it holds |
|---|---|
| `profile.toml` | Who you are: level, field, deal-breakers, how to count your experience, preferred vs. backup roles, resume length and style. **This is what Claude reads to judge fit**, so be specific. `templates/profile.example.toml` is a filled-in example. |
| `config.toml` | What and where to search: title keywords, locations, search terms, company boards, score cutoffs, email on/off. |
| `answers.toml` | Standard application answers (contact details, work authorization, start date, EEO...). Blanks are never guessed. |
| `secrets.toml` | Gmail address + app password for the digest (optional). |

Then:

1. Put your resume(s) in the `resume/` folder (PDF, Word, TXT or MD). Several versions are fine,
   for example one aimed at data roles and one at lab roles: Claude starts each tailored resume from the
   closest one. Optionally copy `resume/corrections.example.txt` to `corrections.txt` for corrections,
   extra skills, and things never to claim.
2. Do a test run: `python jobsearch.py --dry-run`. This sends no email and takes 5-20 minutes.
   Then open `reports/<today's date>.html` in your browser.
3. If the matches look off, adjust `profile.toml` / `config.toml` (see [Tuning](#tuning-your-search))
   and run `python jobsearch.py --dry-run --rescore-today`.

### Email digest (optional)

The digest is sent from a Gmail account using an **app password**. This is a separate 16-character
password just for this tool, not your normal Gmail password.

1. Turn on 2-Step Verification for your Google account: [myaccount.google.com/security](https://myaccount.google.com/security).
2. Go to [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords), name it
   "Job Search", and click **Create**.
3. Copy the 16 characters into `secrets.toml` (setup.py asks for them, or copy
   `templates/secrets.example.toml` to `secrets.toml` and edit it).

Without email you still get the HTML report in `reports/` every day.

### Run it every morning

`python setup.py --schedule`:

- **Windows** creates two Task Scheduler tasks. "Job Search Kit - Daily" runs at 7:00 AM, and at logon
  if the computer was off at 7. "Job Search Kit - Fill Helper" starts the autofill helper at logon. You
  can change the time in Task Scheduler.
- **macOS / Linux** prints two lines to add with `crontab -e`.

The computer has to be on (or wake up) for the run. If a day is missed, the next run still catches
postings from company boards (they look back 30 days).

---

## Tuning your search

- **Which postings are considered** (`config.toml`, `[search]`): a posting is kept if its title
  contains one of `title_keywords`, doesn't contain any of `exclude_title_keywords`, and its location
  matches `locations` (or it's US-remote and `include_remote = true`). Matching is by substring:
  `"engineer"` matches "Software Engineer II".
- **Where to look** (`[boards]`, `[aggregators]`): add the companies you want.
  - Greenhouse, Lever and Ashby use the name in the board's URL, e.g.
    `boards.greenhouse.io/`**`acme`** or `jobs.lever.co/`**`acme`**.
  - Workday takes any job URL from that company.

  The example config lists Boston-area biotech/pharma companies; setup clears them unless you keep
  them. Claude can find and check boards for your field and city: just ask it.
- **How postings are scored** (`profile.toml`): `hard_blockers` (what rules a job out),
  `experience_notes` (how to count your years), and `[categories]` (preferred vs. backup roles).
  After changes, run `python jobsearch.py --rescore-below 7` to re-score earlier low scores.
- **Thresholds** (`[ranking]`, `[tailoring]`): the minimum score to be emailed, to get a tailored
  resume, and to get a cover letter.

## Daily use

| Command | What it does |
|---|---|
| `python jobsearch.py` | full run (the schedule does this for you) |
| `python jobsearch.py --dry-run` | run without emailing or marking postings as seen |
| `python apply.py list` | tailored matches waiting for you to apply |
| `python apply.py show "acme analyst"` | apply link, files and "why interested" text for one role |
| `python apply.py prep <url>` | tailor a resume + cover letter for any posting URL |
| `python apply.py done "acme analyst"` / `skip` | mark as applied / not applying |
| `python apply.py email` | re-send everything still waiting |
| `python setup.py --status` | what's set up and what's missing |

### Autofill extension (optional)

1. The helper has to be running. The logon task starts it, or run `python fill_server.py` yourself.
2. In Chrome, open `chrome://extensions`, turn on **Developer mode** (top right), click **Load
   unpacked**, and choose the `extension` folder inside `job-search-kit`.
3. On a Workday, Greenhouse, Lever or Ashby application page, click **Fill with Job Search**. It
   uploads that job's tailored resume and cover letter and fills your answers. Dropdowns it can't
   handle are left for you. It never submits.

### Apply with Claude Code

Open Claude Code in the folder (with the Claude in Chrome browser extension
installed) and say *"apply to the Acme Data Analyst role"*. Claude fills the form from your files
and stops at the Submit page for you to review. It never submits, never makes up answers, and leaves
sign-ins and CAPTCHAs to you. It does not automate LinkedIn Easy Apply; for those it gives you the
files and answers to use yourself.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `python` / `claude` "is not recognized" or "command not found" | Close the terminal and open a new one after installing. On Windows, reinstall Python with "Add python.exe to PATH" ticked. On macOS/Linux, use `python3`. |
| `setup.py --check` says Claude didn't answer | Run `claude` and sign in again. With an API key, check it's set as a permanent environment variable and the account has credit. |
| No PDFs, only Word files | Install Google Chrome, then re-run. |
| Very few matches | Widen `title_keywords` and `locations`, add company boards, or set `include_remote = true`. |
| Lots of irrelevant matches | Add words to `exclude_title_keywords`, and make `hard_blockers` in `profile.toml` more specific. |
| "Boards that failed" in the report | That site changed or is rate-limiting; it usually works again the next day. |
| No email arrived | Check `secrets.toml` holds an app password (not your normal password) and `[email] enabled = true`. Look in `jobsearch.log` for the error. |
| Scheduled run didn't happen | Windows: open Task Scheduler and look at "Job Search Kit - Daily" > History. Check `jobsearch_stdout.log`. |

## Privacy

`.gitignore` keeps your personal files out of git: `profile.toml`, `config.toml`, `answers.toml`,
`secrets.toml`, everything in `resume/`, `tailored/`, `reports/`, `applications.json`,
`seen_jobs.json`, logs and the extension token. If you fork this project and publish changes, run
`git status` before each commit to make sure none of them are included.

Postings are read through public job-board pages and APIs. LinkedIn and Built In are read slowly
through their logged-out pages, and running the search many times a day may get you rate-limited.
