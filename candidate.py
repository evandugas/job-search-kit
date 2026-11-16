"""Reads profile.toml (who the candidate is) and turns it into the candidate-specific parts of the
scoring, resume and cover-letter prompts. Everything personal lives in profile.toml, config.toml,
answers.toml and resume/, never in the code.

(Named candidate.py, not profile.py, because `profile` is a Python standard-library module.)
"""
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROFILE_FILE = ROOT / "profile.toml"


def load():
    if not PROFILE_FILE.exists():
        return {}
    return tomllib.loads(PROFILE_FILE.read_text(encoding="utf-8"))


def _get(p, section, key, default=""):
    v = p.get(section, {}).get(key, default)
    return v.strip() if isinstance(v, str) else v


def categories(p=None):
    """(preferred, backup) dicts with label/description, or None if no preference is set."""
    p = load() if p is None else p
    c = p.get("categories", {})
    if not c.get("preferred"):
        return None
    return ({"label": c.get("preferred_label") or "Preferred roles", "desc": c["preferred"].strip()},
            {"label": c.get("backup_label") or "Other roles", "desc": (c.get("backup") or "everything else").strip()})


def preferred_title_re(p=None):
    """Regex guessing 'preferred' from a title, used only when Claude didn't label a posting."""
    p = load() if p is None else p
    kws = p.get("categories", {}).get("preferred_title_keywords", [])
    return re.compile("|".join(re.escape(k) for k in kws), re.I) if kws else None


def pages(p=None):
    p = load() if p is None else p
    n = int(_get(p, "resume", "pages", 2) or 2)
    return 1 if n <= 1 else 2


def rank_block(p=None):
    """Candidate description for the scoring prompt."""
    p = load() if p is None else p
    level = _get(p, "candidate", "level") or "job seeker"
    field = _get(p, "candidate", "field")
    lines = [f"CANDIDATE: {level}" + (f", field: {field}" if field else "") + "."]
    if t := _get(p, "candidate", "target_roles"):
        lines.append(f"Target roles: {t}")
    if t := _get(p, "candidate", "hard_blockers"):
        lines.append("Hard blockers for this candidate (cap the score at 4 and name it in \"blocker\"):\n" + t)
    if t := _get(p, "candidate", "near_misses"):
        lines.append("Near misses for this candidate (score 5-7, never lower for this reason alone):\n" + t)
    if t := _get(p, "candidate", "experience_notes"):
        lines.append("How to count the candidate's experience:\n" + t)
    if t := _get(p, "candidate", "realistic_titles"):
        lines.append(f"Realistic target titles: {t}")
    if t := _get(p, "candidate", "unrelated_fields"):
        lines.append(f"Unrelated fields (score 1-2): {t}")
    cats = categories(p)
    if cats:
        pref, backup = cats
        bonus = int(p.get("categories", {}).get("bonus", 1))
        lines.append(
            f"ROLE PREFERENCE: the candidate prefers {pref['label']} ({pref['desc']}). {backup['label']} "
            f"({backup['desc']}) are a backup. Score fit honestly first, then give a preferred role +{bonus} "
            f"(max 10) and a backup role -{bonus} (min 1). Set \"category\" to \"preferred\" or \"backup\".")
    else:
        lines.append('Set "category" to "preferred" for every posting (the candidate has no role-type preference).')
    return "\n\n".join(lines)


def writer_block(p=None):
    """Candidate/field description + style rules for the resume and cover-letter prompts."""
    p = load() if p is None else p
    lines = []
    if t := _get(p, "candidate", "level"):
        lines.append(f"Candidate level: {t}.")
    if t := _get(p, "candidate", "field"):
        lines.append(f"Field: {t}.")
    if t := _get(p, "candidate", "target_roles"):
        lines.append(f"Target roles: {t}.")
    if t := _get(p, "resume", "style"):
        lines.append("Candidate's own formatting rules (follow them):\n" + t)
    return "\n".join(lines)


def resume_location(p=None):
    p = load() if p is None else p
    return _get(p, "resume", "location") or _get(p, "candidate", "location") or "City, ST"
