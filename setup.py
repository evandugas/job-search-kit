"""First-run setup: checks prerequisites, asks a few questions, and writes your personal files
(profile.toml, config.toml, answers.toml, secrets.toml) from templates/. Optionally schedules the
daily run. Safe to re-run: it never overwrites an existing file without asking.

    python setup.py              # interactive: asks questions, writes everything
Non-interactive steps (what Claude Code uses when it walks you through setup; see CLAUDE.md):
    python setup.py --check      # prerequisites, incl. a tiny test prompt to confirm Claude is signed in
    python setup.py --status     # which personal files and resumes exist
    python setup.py --finish     # save Python/Claude paths for the scheduled run + make the extension token
    python setup.py --schedule   # Windows: create the daily + fill-helper tasks; macOS/Linux: print cron lines
"""
import getpass
import importlib.util
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TEMPLATES = ROOT / "templates"
TASK_DAILY = "Job Search Kit - Daily"
TASK_FILL = "Job Search Kit - Fill Helper"


# --------------------------------------------------------------------------- prompts

def ask(question, default=""):
    suffix = f" [{default}]" if default else ""
    try:
        answer = input(f"{question}{suffix}: ").strip()
    except EOFError:
        answer = ""
    return answer or default


def yes(question, default=True):
    answer = ask(f"{question} ({'Y/n' if default else 'y/N'})").lower()
    return default if not answer else answer.startswith("y")


def secret(question):
    if sys.stdin.isatty():
        return getpass.getpass(f"{question} (hidden): ").strip()
    return ask(question)


def as_list(text):
    return [s.strip() for s in text.split(",") if s.strip()]


def toml_str(s):
    return json.dumps(s, ensure_ascii=False)  # JSON strings are valid TOML basic strings


def toml_list(items, indent="  ", width=96):
    if not items:
        return "[]"
    lines, line = [], indent
    for item in map(toml_str, items):
        if len(line) + len(item) + 2 > width and line.strip():
            lines.append(line.rstrip())
            line = indent
        line += item + ", "
    if line.strip():
        lines.append(line.rstrip())
    return "[\n" + "\n".join(lines) + "\n]"


def toml_block(text):
    """Multi-line TOML string, or "" when empty."""
    text = text.strip().replace("\\", "\\\\").replace('"""', '\\"\\"\\"')
    return f'"""\n{text}\n"""' if text else '""'


def set_array(text, key, items):
    new, n = re.subn(rf"^{re.escape(key)} = \[.*?\](?=[ \t]*(#.*)?\n)", f"{key} = {toml_list(items)}",
                     text, count=1, flags=re.M | re.S)
    return new if n else text


def set_value(text, key, value):
    """Replace `key = <anything>` (first occurrence), keeping any trailing comment."""
    return re.sub(rf'^({re.escape(key)} = )("[^"\n]*"|true|false|\d+)', lambda m: m.group(1) + value,
                  text, count=1, flags=re.M)


def write(path, text):
    if path.exists() and not yes(f"  {path.name} already exists. Overwrite it?", default=False):
        print(f"  kept your existing {path.name}")
        return False
    path.write_text(text, encoding="utf-8")
    print(f"  wrote {path.name}")
    return True


# --------------------------------------------------------------------------- checks

def find_claude():
    found = shutil.which("claude") or shutil.which("claude.cmd")
    if found:
        return found
    for p in (Path.home() / ".local" / "bin" / "claude.exe", Path.home() / ".local" / "bin" / "claude",
              Path(os.environ.get("APPDATA", "")) / "npm" / "claude.cmd"):
        if p.exists():
            return str(p)
    return None


def claude_signed_in(claude):
    """Sends a tiny prompt through `claude -p`, the same way the pipeline does. (ok, detail)"""
    try:
        # stdin=DEVNULL: `claude -p` would otherwise read (and use up) the terminal's input.
        r = subprocess.run([claude, "-p", "Reply with just the word OK."], stdin=subprocess.DEVNULL,
                           capture_output=True, text=True,
                           encoding="utf-8", timeout=180, cwd=ROOT)
    except Exception as e:
        return False, str(e)
    out = (r.stdout or "").strip()
    if r.returncode == 0 and out:
        return True, out[:40]
    return False, ((r.stderr or "") + out).strip()[:300]


def check_prereqs(interactive=True):
    print("Checking prerequisites...")
    ok = True
    if sys.version_info < (3, 11):
        print(f"  ✗ Python 3.11+ is required (this is {platform.python_version()}). Install a newer Python\n"
              "    from https://www.python.org/downloads/ and run setup with it.")
        sys.exit(1)
    print(f"  ✓ Python {platform.python_version()} ({sys.executable})")

    missing = [m for m, mod in (("pypdf", "pypdf"), ("python-docx", "docx"), ("pymupdf", "pymupdf"))
               if importlib.util.find_spec(mod) is None]
    if missing:
        print(f"  ✗ missing packages: {', '.join(missing)}\n"
              f"    install with: \"{sys.executable}\" -m pip install -r requirements.txt")
        if interactive and yes("    Install them now?"):
            subprocess.run([sys.executable, "-m", "pip", "install", "-r", str(ROOT / "requirements.txt")])
        else:
            ok = False
    else:
        print("  ✓ Python packages (pypdf, python-docx, pymupdf)")

    claude = find_claude()
    if not claude:
        ok = False
        print("  ✗ Claude Code (`claude`) not found. Install it (see README, step 1), open a NEW terminal,\n"
              "    run `claude` once to sign in, then re-run this.")
    else:
        print(f"  ✓ Claude Code: {claude}")
        print("    testing that Claude Code is signed in (sends one tiny prompt)...")
        signed_in, detail = claude_signed_in(claude)
        if signed_in:
            print("  ✓ Claude Code is signed in and answering")
        else:
            ok = False
            print(f"  ✗ Claude Code didn't answer: {detail or 'no output'}\n"
                  "    Run `claude` in a terminal and sign in (Claude subscription or Anthropic Console account),\n"
                  "    or set ANTHROPIC_API_KEY as a permanent user environment variable. See README, \"Connecting Claude\".")

    sys.path.insert(0, str(ROOT))
    import tailor
    browser = tailor.find_browser()
    if browser:
        print(f"  ✓ browser for PDFs: {browser}")
    else:
        ok = False
        print("  ✗ no Chrome, Edge or Chromium found; tailored resumes need one to make PDFs.")
    return ok, claude


# --------------------------------------------------------------------------- files

def build_profile(a):
    cats = ""
    if a["pref"]:
        cats = f"""
[categories]
# Preferred roles get +bonus, backup roles -bonus, and the email shows them in two groups.
# Delete this section if you have no preference.
preferred_label = {toml_str(a['pref_label'])}
preferred = {toml_str(a['pref'])}
backup_label = {toml_str(a['backup_label'])}
backup = {toml_str(a['backup'])}
bonus = 1
# Only used to guess the group of a posting Claude hasn't labeled (substring match on the title).
preferred_title_keywords = {toml_list(a['pref_keywords'])}
"""
    return f"""# Who you are, in plain words. Claude reads this when it scores postings and writes your tailored
# resume and cover letters. See templates/profile.example.toml for a filled-in example.
# Facts about your experience belong in your resume files (resume/), not here.

[candidate]
level = {toml_str(a['level'])}
field = {toml_str(a['field'])}
location = {toml_str(a['location'])}                # shown on your resume header
target_roles = {toml_str(a['target_roles'])}
realistic_titles = {toml_str(a['realistic_titles'])}

# Requirements that truly rule a posting out for you (score capped at 4). One per line.
hard_blockers = {toml_block(a['hard_blockers'])}

# Gaps that are NOT deal-breakers for you (score 5-7). One per line.
near_misses = ""

# How Claude should count your experience (do internships, research, part-time or freelance work
# count toward "N years"?). Name the roles and dates so it can add them up.
experience_notes = {toml_block(a['experience_notes'])}

# Fields that are clearly not a fit (score 1-2).
unrelated_fields = ""
{cats}
[resume]
pages = {a['pages']}              # 1 or 2: tailored resumes are filled to exactly this many full pages
# Your own formatting rules for tailored resumes and cover letters, one per line, e.g.
# "- Never put GPA in the summary".
style = ""
"""


def build_config(a):
    text = (TEMPLATES / "config.example.toml").read_text(encoding="utf-8")
    text = set_array(text, "title_keywords", a["title_keywords"])
    text = set_array(text, "exclude_title_keywords", a["exclude_title_keywords"])
    text = set_array(text, "locations", a["locations"])
    text = set_array(text, "exclude_locations", a["exclude_locations"])
    text = set_value(text, "include_remote", "true" if a["remote"] else "false")
    for key in ("linkedin_search_terms", "builtin_search_terms", "workday_search_terms"):
        text = set_array(text, key, a["search_terms"])
    text = set_value(text, "linkedin_location", toml_str(a["linkedin_location"]))
    text = set_array(text, "builtin_sites", [a["builtin_site"]] if a["builtin_site"] else [])
    text = set_value(text, "to", toml_str(a["email"]))
    if not a["gmail_password"]:
        text = re.sub(r'(\[email\]\s*\n)enabled = true', r'\1enabled = false', text)
    if not a["keep_example_boards"]:
        for key in ("compbiojobs_pages", "consider_boards", "greenhouse", "lever", "ashby", "workable"):
            text = set_array(text, key, [])
        text = re.sub(r"^workday = \[.*?^\]", "workday = [\n  # { name = \"Company\", url = \"https://<host>.myworkdayjobs.com/<site>\" },\n]",
                      text, count=1, flags=re.M | re.S)
    return text


def build_answers(a):
    text = (TEMPLATES / "answers.example.toml").read_text(encoding="utf-8")
    for key in ("first_name", "last_name", "email", "phone", "city", "state", "country", "linkedin", "website"):
        if a.get(key):
            text = set_value(text, key, toml_str(a[key]))
    return text


# --------------------------------------------------------------------------- scheduling

def schedule_windows(claude):
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    pythonw = pythonw if pythonw.exists() else Path(sys.executable)
    ps = f"""
$ErrorActionPreference = 'Stop'
$daily = New-ScheduledTaskAction -Execute '{ROOT / "run_daily.bat"}' -WorkingDirectory '{ROOT}'
$triggers = @((New-ScheduledTaskTrigger -Daily -At 7:00am), (New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME))
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName '{TASK_DAILY}' -Action $daily -Trigger $triggers -Settings $settings -Force | Out-Null
$fill = New-ScheduledTaskAction -Execute '{pythonw}' -Argument '"{ROOT / "fill_server.py"}"' -WorkingDirectory '{ROOT}'
$fillSettings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit 0
Register-ScheduledTask -TaskName '{TASK_FILL}' -Action $fill -Trigger (New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME) -Settings $fillSettings -Force | Out-Null
Start-ScheduledTask -TaskName '{TASK_FILL}'
"""
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True)
    if r.returncode == 0:
        print(f"  ✓ scheduled '{TASK_DAILY}' (7:00 AM daily + at logon) and '{TASK_FILL}' (at logon).")
        print("    Change the time in Task Scheduler if you like.")
    else:
        print(f"  ✗ couldn't create the scheduled tasks:\n{r.stderr.strip()[:600]}")


def schedule_unix():
    sh = ROOT / "run_daily.sh"
    sh.chmod(sh.stat().st_mode | 0o111)
    print("  Add these lines with `crontab -e` (daily run at 7:00 AM; fill helper at boot):")
    print(f"    0 7 * * * {sh}")
    print(f"    @reboot cd {ROOT} && {sys.executable} fill_server.py >> fill_server.log 2>&1")
    if platform.system() == "Darwin":
        print("  (macOS: cron jobs don't run while the Mac sleeps. A missed day is picked up on the next run,\n"
              "   because company boards look back 30 days.)")


def schedule(claude=None):
    schedule_windows(claude) if platform.system() == "Windows" else schedule_unix()


def finish(claude):
    """Machine-specific bits: which Python / claude the scheduled run should use, and the extension token."""
    (ROOT / ".python-path").write_text(sys.executable, encoding="utf-8")
    print(f"  saved Python path for the scheduled run: {sys.executable}")
    if claude:
        (ROOT / ".claude-dir").write_text(str(Path(claude).parent), encoding="utf-8")
        print(f"  saved Claude Code folder for the scheduled run: {Path(claude).parent}")
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    import fill_server  # creates extension/config.js with a fresh token for the Chrome extension
    print(f"  extension/config.js is ready (fill helper token, port {fill_server.PORT})")


def status():
    """What's set up and what isn't (for Claude-guided setup)."""
    print("Personal files:")
    for name, note in (("profile.toml", "who you are (required for good scores)"),
                       ("config.toml", "what/where to search (required)"),
                       ("answers.toml", "application form answers"),
                       ("secrets.toml", "Gmail app password (optional: without it, reports only)")):
        print(f"  {'✓' if (ROOT / name).exists() else '✗'} {name:<13} {note}")
    resumes = [p.name for p in (ROOT / "resume").glob("*")
               if p.suffix.lower() in (".pdf", ".docx", ".txt", ".md")
               and p.name.lower() != "readme.txt" and ".example." not in p.name.lower()]
    print(f"  {'✓' if resumes else '✗'} resume/       {', '.join(resumes) or 'no resume files yet'}")
    print(f"  {'✓' if (ROOT / 'extension' / 'config.js').exists() else '✗'} extension/config.js  (run setup.py --finish)")


# --------------------------------------------------------------------------- main

def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    os.chdir(ROOT)
    args = sys.argv[1:]
    if args:
        # Non-interactive steps, used when Claude Code walks someone through setup (see CLAUDE.md).
        cmd = args[0]
        if cmd == "--check":
            ok, _ = check_prereqs(interactive=False)
            print("\nAll prerequisites OK." if ok else "\nSome prerequisites are missing (see ✗ above).")
            sys.exit(0 if ok else 1)
        if cmd == "--status":
            return status()
        if cmd == "--finish":
            return finish(find_claude())
        if cmd == "--schedule":
            return schedule(find_claude())
        sys.exit("Usage: python setup.py [--check | --status | --finish | --schedule]")
    print(__doc__)
    ok, claude = check_prereqs()
    if not ok and not yes("\nSome prerequisites are missing. Continue anyway?", default=False):
        sys.exit(1)

    a = {}
    print("\n--- About you (goes into answers.toml and profile.toml; all local, never committed) ---")
    a["first_name"] = ask("First name")
    a["last_name"] = ask("Last name")
    a["email"] = ask("Email (job digests are sent here)")
    a["phone"] = ask("Phone (optional)")
    a["city"] = ask("City you're job hunting in (e.g. Austin)")
    a["state"] = ask("State/region (e.g. TX)")
    a["country"] = ask("Country", "United States")
    a["linkedin"] = ask("LinkedIn URL (optional)")
    a["website"] = ask("Website / portfolio (optional)")
    a["location"] = ask("Location to show on your resume", ", ".join(x for x in (a["city"], a["state"]) if x))

    print("\n--- What you're looking for (profile.toml) ---")
    a["level"] = ask("Your level, e.g. 'new grad (BS)', 'MS new grad', 'mid-level, 5 years', 'senior, 10+ years'")
    a["field"] = ask("Your field, e.g. 'software engineering', 'bioinformatics', 'marketing'")
    a["target_roles"] = ask("Kinds of roles you want (comma-separated)")
    a["realistic_titles"] = ask("Titles that fit your level (optional), e.g. 'Analyst I, Associate, Engineer II'")
    a["hard_blockers"] = "\n".join(f"- {b}" for b in as_list(ask(
        "Deal-breaker requirements (comma-separated, optional), e.g. 'PhD required, 8+ years, security clearance'")))
    a["experience_notes"] = ask("How should your experience be counted? (optional, one line; edit profile.toml for more)")
    a["pages"] = 1 if ask("Resume length in pages (1 or 2)", "2").strip() == "1" else 2
    a["pref"] = ""
    if yes("Do you prefer one kind of role, with another as a backup?", default=False):
        a["pref_label"] = ask("  Name for the preferred group", "Preferred roles")
        a["pref"] = ask("  Describe the preferred roles")
        a["backup_label"] = ask("  Name for the backup group", "Backup roles")
        a["backup"] = ask("  Describe the backup roles")
        a["pref_keywords"] = as_list(ask("  Title words that mark a preferred role (comma-separated)"))

    print("\n--- Search (config.toml) ---")
    print("Title keywords are matched as substrings of job titles, case-insensitive: 'engineer' matches\n"
          "'Software Engineer II'. Short ones (under 5 letters, like 'qa') must start a word.")
    a["title_keywords"] = as_list(ask("Title keywords to KEEP (comma-separated)", a["target_roles"]))
    a["exclude_title_keywords"] = as_list(ask(
        "Title keywords to DROP (comma-separated)",
        "intern, director, vice president, head of, principal, staff, manager, sales"))
    a["search_terms"] = as_list(ask("Search terms for LinkedIn / Built In / Workday (comma-separated)",
                                    ", ".join(a["title_keywords"][:6])))
    print("Locations are matched as substrings of the posting's location. Add nearby towns; a state is\n"
          "added as ', XX' (with the comma) so 'TX' doesn't match inside other words.")
    a["locations"] = as_list(ask("Location words to accept (comma-separated)", a["city"].lower()))
    st = a["state"].strip().lower()
    if st and f", {st}" not in a["locations"]:
        a["locations"].append(f", {st}" if len(st) <= 3 else st)
    a["exclude_locations"] = as_list(ask("Location words to reject (optional), e.g. 'cambridgeshire, ontario'"))
    a["remote"] = yes("Also keep US-remote postings?")
    a["linkedin_location"] = ask("LinkedIn search location", ", ".join(x for x in (a["city"], a["state"]) if x))
    print("Built In covers some US metros (builtinboston.com, builtinnyc.com, builtinchicago.org, builtinla.com,\n"
          "builtinsf.com, builtinseattle.com, builtinaustin.com, builtincolorado.com). Leave blank to skip.")
    a["builtin_site"] = ask("Built In site, e.g. www.builtinaustin.com").removeprefix("https://").strip("/")
    a["keep_example_boards"] = yes(
        "Keep the example company boards (~150 Boston-area biotech/pharma/AI companies)? Say no if you're in\n"
        "  another field; add your own companies to config.toml [boards] later", default=False)

    print("\n--- Email digest (secrets.toml) ---")
    print("The digest is sent from a Gmail account using an app password (not your normal password):\n"
          "  https://myaccount.google.com/apppasswords  (needs 2-Step Verification)")
    gmail = ask("Gmail address to send from (blank = no email, just HTML reports in reports/)", a["email"]
                if a["email"].endswith("@gmail.com") else "")
    a["gmail_password"] = secret("Gmail app password (16 characters)") if gmail else ""

    print("\nWriting your files...")
    write(ROOT / "profile.toml", build_profile(a))
    write(ROOT / "config.toml", build_config(a))
    write(ROOT / "answers.toml", build_answers(a))
    if gmail and a["gmail_password"]:
        write(ROOT / "secrets.toml", f"# Gmail app password for the digest. Never commit this file.\n"
                                     f"gmail_address = {toml_str(gmail)}\n"
                                     f"gmail_app_password = {toml_str(a['gmail_password'])}\n")
    finish(claude)

    print("\n--- Scheduling ---")
    if yes("Run the search automatically every morning?", default=False):
        schedule(claude)

    print(f"""
Done. Next steps:
  1. Put your resume(s) (PDF/DOCX/TXT) in {ROOT / 'resume'}
     Optional: copy resume/corrections.example.txt to corrections.txt for facts and skills to add.
  2. Read profile.toml and answers.toml and fill in anything left blank.
  3. Try a run that sends no email:  python jobsearch.py --dry-run
     then open reports/<today>.html
  4. Optional: load the Chrome extension: chrome://extensions > Developer mode > Load unpacked >
     {ROOT / 'extension'}
""")


if __name__ == "__main__":
    main()
