# Job search automation

Daily pipeline (scheduled `run_daily.bat` / `run_daily.sh` → `jobsearch.py`): collect postings from
job boards, aggregators and LinkedIn (config.toml), score them against every file in `resume/` with
`claude -p`, tailor resumes (+ cover letters for high scores) via `tailor.py`, email a digest, and
queue tailored matches in `applications.json` (`apply.py`). First-time setup: `python setup.py`.

- Personal files (all gitignored, written by setup.py from `templates/`):
  - `profile.toml`: who the candidate is (level, field, deal-breakers, how to count experience,
    preferred vs. backup roles, resume page count and style). `candidate.py` turns it into the
    candidate-specific parts of the scoring/tailoring prompts. Prompts in the code stay generic.
  - `config.toml`: search keywords, locations, sources, score cutoffs.
  - `answers.toml`: the user's standard application answers.
  - `secrets.toml`: the Gmail app password. Never print or echo it.
  - `resume/`: resume files; `resume/corrections.txt` overrides facts in them.
- `--dry-run` still overwrites `reports/<today>.html`, and still tailors resumes for top matches.
- Sources: company boards (Greenhouse/Lever/Ashby/Workable/Workday), aggregators (Built In, LinkedIn,
  compbiojobs) and VC portfolio boards on Consider. `python discover_boards.py` lists company boards
  seen on the VC boards that aren't in config.toml yet. Remote jobs are kept only if US-remote (`NON_US`).
  Date windows: aggregators/LinkedIn use `max_posted_age_days`; company boards use
  `board_max_posted_age_days`, so a missed day or a new board doesn't drop recent postings.
- `seen_jobs.json` keeps every scored posting with its score, reason, gaps and blocker.
  `python jobsearch.py --rescore-below 7` re-scores still-open postings that scored low (after
  editing profile.toml or the resumes). Preferred roles can have lower cutoffs (`*_preferred` in config.toml).
- The digest email is the main interface: each card has an Apply button (`jobsearch.apply_link`), the
  attached resume + cover letter, and the "why interested" text. `python apply.py email` re-sends
  everything still waiting in the queue.
- Chrome extension `extension/` ("Job Search Autofill", loaded unpacked) + `fill_server.py`
  (127.0.0.1:8765, token in extension/config.js, started at logon by the "Job Search Kit - Fill Helper"
  task). On Workday/Greenhouse/Lever/Ashby pages it uploads the queued job's resume + cover letter and
  fills answers.toml fields; custom dropdowns are left to the user. Never submits.

## First-time setup ("help me set this up")

The user may be new to the terminal, Python and git. Use plain words, do one step at a time, and say
what each command does before running it. Run `python setup.py --status` first to see where they are;
skip steps that are already done. (On macOS/Linux use `python3` wherever this says `python`.)

1. **Prerequisites.** Run `python setup.py --check`. Fix each ✗ with the user:
   - No Python 3.11+: Windows `winget install Python.Python.3.12` (or python.org, ticking "Add
     python.exe to PATH"); macOS `brew install python` or python.org; Linux the distro's package manager.
     Afterwards they must open a new terminal (and restart Claude Code) so PATH updates.
   - Missing packages: `python -m pip install -r requirements.txt`.
   - Claude not signed in: explain the two options from README "Connecting Claude" (subscription
     login or an API key) and let them choose. Never ask them to paste an API key into the chat.
   - No browser: install Google Chrome (needed to make PDFs).
   Re-run `--check` until everything is ✓.
2. **Resume.** Ask them to copy their resume file(s) into `resume/` (PDF, DOCX, TXT or MD; several
   versions are fine). Read them.
3. **Profile.** Draft `profile.toml` from the resume, following the format and comments of
   `templates/profile.example.toml`: level, field, location, target roles, realistic titles, and
   `experience_notes` (list their roles with dates and say what counts toward "N years"). Then ASK
   about what a resume can't tell you: what roles they want next, deal-breakers (`hard_blockers`),
   whether they prefer one kind of role over another (`[categories]`, optional), 1- or 2-page
   resume, and any resume style rules. Show the draft and change it until they're happy.
4. **Search config.** Write `config.toml` from `templates/config.example.toml`:
   - `title_keywords` / `exclude_title_keywords` for their roles and level (substring match;
     keywords under 5 letters must start a word). `locations` = their city and nearby towns; add ", XX" (the
     state) only if they're open to anywhere in the state. `include_remote` per their answer.
   - `linkedin_search_terms`, `linkedin_location`, `workday_search_terms`, `builtin_search_terms`
     (and `builtin_sites` if Built In covers their metro, e.g. www.builtinnyc.com).
   - `[email] to` = their email; `enabled = false` until secrets.toml exists.
   - The shipped boards and aggregators are an example for Boston biotech. Clear them (and
     `compbiojobs_pages`, `consider_boards`) unless that is their field and area. Offer to find
     companies for their field and city: verify each board before adding it (Greenhouse
     `https://boards-api.greenhouse.io/v1/boards/<token>/jobs`, Lever
     `https://api.lever.co/v0/postings/<token>`, Ashby `https://api.ashbyhq.com/posting-api/job-board/<token>`
     must return jobs). Never add a board you couldn't verify.
5. **Answers.** Write `answers.toml` from `templates/answers.example.toml` with the contact details
   from the resume. Ask for the rest (work authorization, sponsorship, start date, relocation...)
   or leave blanks; never guess eligibility, salary or EEO answers.
6. **Email (optional).** Walk them through creating a Gmail app password (README, "Email digest").
   Copy `templates/secrets.example.toml` to `secrets.toml` and have THEM open it in a text editor
   (`notepad secrets.toml` / `open -e secrets.toml`) and paste the password. Never ask for the
   password in the chat, and never print secrets.toml. Then set `[email] enabled = true`.
7. **Finish.** Run `python setup.py --finish` (machine paths + extension token).
8. **Test run.** Run `python jobsearch.py --dry-run` (takes 5-20 minutes; it tailors resumes for the
   top matches). Open `reports/<today>.html` with them. If there are too few or off-target matches,
   adjust keywords/locations/boards and run `python jobsearch.py --dry-run --rescore-today`.
9. **Schedule (optional).** Ask before running `python setup.py --schedule`. Windows creates two
   Task Scheduler tasks (daily 7 AM + at logon, and the fill helper at logon). macOS/Linux prints
   cron lines: show them how to add them with `crontab -e`, or add them only if they say so.
10. **Extension (optional).** Explain how to load `extension/` in Chrome (README, "Autofill extension").

## Apply assist ("apply to the <company> <role> role")

The user reviews and clicks Submit on every application. Never submit one yourself.

1. `python apply.py show "<company> <words from title>"` to get the apply URL, tailored resume and
   cover letter paths, and the "why interested" text. If the role isn't queued, run
   `python apply.py prep <url>` first.
2. Read `answers.toml`. A blank answer is not a guess: ask the user for it (and offer to save it to
   answers.toml). Never invent experience, numbers, or eligibility/salary answers.
3. Open the apply URL in a NEW Chrome tab (claude-in-chrome). For a LinkedIn posting, click its
   "Apply" button to go through to the company's own site (shown only when signed in). Do not
   automate LinkedIn Easy Apply (against LinkedIn's terms): give the user the files and answers instead.
   For a recruiter repost, tell the user who the real employer is if the posting says.
4. Upload the tailored resume PDF (and the cover letter PDF when the form has a field for it), then
   fill the fields from answers.toml and the resume. Free-text questions: draft answers from the
   resumes/cover letter and tell the user they're drafts.
5. Leave to the user: account creation/sign-in (Workday needs an account per company), CAPTCHAs,
   email verification, and anything that asks for a password.
6. Stop at the final review/Submit page. List what you filled in, any drafted answers and any skipped
   fields, and ask the user to review and click Submit.
7. After the user confirms they submitted it, run `python apply.py done "<search>"`.
