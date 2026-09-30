#!/usr/bin/env python3
"""Check, run and score the communication coach's eval set.

Usage (from the framework folder):
  python eval/run_eval.py check                      schema, coverage and leakage of situations.yaml
  python eval/run_eval.py run [--ids 1 3]            ask the coach every (or some) situation
  python eval/run_eval.py score eval/runs/<file>     gate a hand-scored run file into runs/LOG.md
From a private data folder that holds knowledge/ and eval/:
  python framework/eval/run_eval.py --data . check | run | score ...

Data (--data DIR, else $COMM_COACH_DATA, else framework/starter):
  DIR/knowledge/*.md        project knowledge, as the Project sees it (*.local.md is skipped)
  DIR/eval/situations.yaml  the eval set
  DIR/eval/runs/            run files and LOG.md
Kernel (--kernel FILE): default framework/kernel/instructions.md

Backends (run):
  --backend cli   DEFAULT. Headless `claude -p` on your Claude subscription: $0 incremental.
                  Outside a logged-in terminal set CLAUDE_CODE_OAUTH_TOKEN (from `claude setup-token`).
  --backend api   Metered Messages API. Opt-in only: needs --metered and ANTHROPIC_API_KEY, and
                  prints a cost statement before the first request. Never used as a fallback.
Env: EVAL_MODEL (default claude-sonnet-5-5), EVAL_EFFORT (default high), COMM_COACH_DATA.

Exit codes: 0 ok / pass · 1 below target, not taggable, coverage gaps or failed rows ·
            2 invalid input or setup (nothing was sent) · 130 interrupted.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from difflib import SequenceMatcher
from fractions import Fraction
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - exercised by hand
    sys.stderr.write("run_eval.py needs PyYAML: pip install -r requirements.txt\n")
    raise SystemExit(2)

HARNESS_VERSION = "3.0"
FRAMEWORK = Path(__file__).resolve().parents[1]
DEFAULT_KERNEL = FRAMEWORK / "kernel" / "instructions.md"
DEFAULT_DATA = FRAMEWORK / "starter"

DEFAULT_MODEL = "claude-sonnet-5-5"
DEFAULT_EFFORT = "high"
EFFORTS = ("low", "medium", "high", "xhigh", "max")
API_MAX_TOKENS = 16000          # thinking + reply share this cap; ~16K keeps non-streaming calls safe
CLI_TIMEOUT_S = 300
CONSECUTIVE_ERROR_LIMIT = 3

SCORED = ("outcome", "register", "sayable", "length")
TARGET = Decimal("4.00")
MIN_TOTAL = 20
LIVE_WORD_LIMIT = 80
PREP_WORD_LIMIT = 250

PREFIX_MODE = {"L:": "live", "P:": "prep", "D:": "draft", "X:": "debrief", "R:": "practice"}
MODES = ("live", "prep", "draft", "debrief", "practice")
TAGS = ("up", "across", "down", "out", "personal", "zh", "no-reply", "push-back", "safety",
        "channel-switch", "audience")
SITUATION_KEYS = ("id", "prefix", "situation", "expect", "tags", "notes")

# Coverage minimums for a taggable set. eval-set.md shows the same table; a unit test keeps them equal.
COVERAGE: tuple[tuple[str, int, str], ...] = (
    ("total", 20, "situations in the set"),
    ("mode:live", 5, "LIVE (prefix L:, or expect: live)"),
    ("mode:prep", 5, "PREP"),
    ("mode:draft", 5, "DRAFT"),
    ("mode:debrief", 3, "DEBRIEF"),
    ("mode:practice", 1, "PRACTICE"),
    ("unprefixed", 2, "mode-detection tests: prefix '' with expect"),
    ("tag:up", 1, "upward (leadership)"),
    ("tag:across", 1, "across (peers, other teams)"),
    ("tag:out", 1, "outward (suppliers, customers, partners)"),
    ("tag:personal", 2, "personal life"),
    ("zh", 3, "Mandarin or bilingual (tag zh, or Chinese text)"),
    ("tag:no-reply|channel-switch", 2, "right answer is 'don't reply now' or 'switch to a call'"),
    ("tag:push-back", 2, "coach should push back on the goal, not just the wording"),
    ("tag:safety", 2, "safety, legal or compliance line"),
    ("tag:audience", 1, "others can see it (group chat, cc, meeting)"),
)

# List prices, USD per million tokens: input, output, cache read. Cached from the Claude API
# model table on 2026-09-25 - check current pricing before relying on an estimate.
PRICES = {
    "claude-sonnet-5-5": (2.00, 10.00, 0.20),
    "claude-sonnet-5": (2.00, 10.00, 0.20),
    "claude-opus-5-5": (4.00, 20.00, 0.20),
    "claude-haiku-4-5": (1.00, 5.00, 0.10),
}

CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
TOKEN_RE = re.compile(r"[a-z0-9%]+(?:'[a-z]+)?|[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
WORD_RE = re.compile(r"[^\W_]+(?:['\u2019][^\W_]+)*")
SAY_SEND_RE = re.compile(r"^[ \t>*_\-\u2022]*(?:\*\*|__)?(?:Say|Send)(?:\*\*|__)?\s*[:\uff1a]",
                         re.IGNORECASE | re.MULTILINE)
MODE_ANNOUNCE_RE = re.compile(r"^(?:mode\s*[:\uff1a\-]|(?:live|prep|draft|debrief|practice)\s+mode\b)",
                              re.IGNORECASE)
CONFIRM_RE = re.compile(r"\[\s*confirm\s*[:\uff1a][^\]]*\]", re.IGNORECASE)
LIST_MARKER_RE = re.compile(r"^(\s*(?:[-*\u2022>]\s*)?(?:#+\s*)?\(?)\d{1,2}[.)](?=\s)", re.MULTILINE)
SKIP_NUMBER_RE = re.compile(r"\b1:1\b|\b24/7\b")
NUMBER_RE = re.compile(r"(?<![A-Za-z0-9])(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?(?::\d{2})?)")
VERSION_RE = re.compile(r"^\s*(?:\*\*)?Version\s+(\d+\.\d+)\b", re.MULTILINE)
USER_LINE_RE = re.compile(
    r"^\s*(?:[-*>]\s*)*(?:\*\*|__)?(?:User|Input|Situation)(?:\*\*|__)?\s*[:\uff1a]\s*(?:\*\*|__)?\s*(.+)$",
    re.IGNORECASE | re.MULTILINE)
FATAL_RE = re.compile(
    r"not logged in|/login|invalid api key|authenticat|unauthori[sz]ed|oauth|token (?:has )?expired|"
    r"forbidden|permission|credit balance|not_found_error|unknown option|"
    r"model\b[^\n]{0,80}\b(?:not found|does not exist|invalid|unknown|not available|not supported)",
    re.IGNORECASE)
LEAK_SHINGLE = 6
LEAK_CONTAINMENT = 0.30
LEAK_RATIO = 0.60


class InputError(Exception):
    """Bad input or setup. Reported as 'error: ...' with exit code 2."""


# --------------------------------------------------------------------------- small helpers

def _utf8_stdio() -> None:
    """Piped stdout on Windows is cp1252; a Chinese reply would crash the run mid-way."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def say(msg: str = "", *, err: bool = False) -> None:
    print(msg, file=sys.stderr if err else sys.stdout, flush=True)


def read_text(path: Path) -> str:
    """UTF-8 (BOM tolerated), newlines normalised to LF."""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        raise InputError(f"file not found: {show(path)}") from None
    except UnicodeDecodeError as exc:
        raise InputError(f"{show(path)} is not UTF-8 ({exc.reason} at byte {exc.start})") from None
    return text.replace("\r\n", "\n").replace("\r", "\n")


def read_yaml(path: Path, what: str) -> Any:
    text = read_text(path)
    try:
        return yaml.safe_load(text)
    except yaml.MarkedYAMLError as exc:
        mark = exc.problem_mark
        where = f"line {mark.line + 1}, column {mark.column + 1}" if mark else "unknown position"
        raise InputError(
            f"{what} {show(path)}: YAML error at {where}: {exc.problem}. "
            "Hint: quote prefixes (\"L:\") and any text that contains ': '.") from None
    except yaml.YAMLError as exc:
        raise InputError(f"{what} {show(path)}: YAML error: {exc}") from None


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def show(path: Path) -> str:
    """A path for humans: relative to the cwd when possible, forward slashes."""
    p = Path(path)
    try:
        return p.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except (ValueError, OSError):
        return p.resolve().as_posix() if p.is_absolute() or p.exists() else p.as_posix()


def quote(text: str) -> str:
    return f'"{text}"' if (" " in text or not text) else text


def nfkc(text: str) -> str:
    return unicodedata.normalize("NFKC", text)


def tokens(text: str) -> list[str]:
    return TOKEN_RE.findall(nfkc(text).lower().replace("\u2019", "'"))


def length_words(text: str) -> tuple[int, int]:
    """(words, cjk_chars): Latin words plus one word per two CJK characters."""
    cjk = len(CJK_RE.findall(text))
    latin = len(WORD_RE.findall(CJK_RE.sub(" ", text)))
    return latin + math.ceil(cjk / 2), cjk


def est_tokens(text: str) -> int:
    """Rough token estimate for statements and the context canary: never a measurement."""
    cjk = len(CJK_RE.findall(text))
    return int((len(text) - cjk) / 3.5 + cjk * 1.2) + 1


def round2(value: Fraction) -> Decimal:
    return (Decimal(value.numerator) / Decimal(value.denominator)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP)


def plain(obj: Any) -> Any:
    """SDK objects -> plain dicts for the run file."""
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, dict):
        return {str(k): plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [plain(v) for v in obj]
    for attr in ("model_dump", "to_dict", "dict"):
        fn = getattr(obj, attr, None)
        if callable(fn):
            try:
                return plain(fn())
            except Exception:  # noqa: BLE001 - best effort provenance only
                continue
    return str(obj)


# --------------------------------------------------------------------------- situations

@dataclass(frozen=True)
class Situation:
    id: int
    prefix: str
    situation: str
    mode: str
    tags: tuple[str, ...] = ()
    notes: str = ""

    @property
    def prompt(self) -> str:
        return f"{self.prefix} {self.situation}".strip()


def validate_situations(raw: Any, where: str) -> list[Situation]:
    """Every problem at once, each naming the entry number and id."""
    if raw is None:
        raise InputError(f"{where}: no situations (the file is empty)")
    if not isinstance(raw, list):
        raise InputError(f"{where}: expected a list of situations ('- id: 1' entries), "
                         f"got a {type(raw).__name__}")
    errors: list[str] = []
    items: list[Situation] = []
    seen: dict[int, int] = {}
    for n, entry in enumerate(raw, 1):
        label = f"entry #{n}"
        if not isinstance(entry, dict):
            errors.append(f"{label}: expected a mapping with id/prefix/situation, got {type(entry).__name__}")
            continue
        sid = entry.get("id")
        if type(sid) is not int or sid < 1:
            errors.append(f"{label}: id must be a positive integer (got {sid!r})")
            sid = None
        else:
            label = f"entry #{n} (id {sid})"
            if sid in seen:
                errors.append(f"{label}: duplicate id - entry #{seen[sid]} already uses it")
            seen.setdefault(sid, n)
        unknown = sorted(str(k) for k in entry if k not in SITUATION_KEYS)
        if unknown:
            errors.append(f"{label}: unknown key(s) {', '.join(unknown)} (allowed: {', '.join(SITUATION_KEYS)})")
        prefix = entry.get("prefix")
        if prefix is None:
            prefix = ""
        if not isinstance(prefix, str):
            errors.append(f"{label}: prefix must be a string (got {prefix!r})")
            prefix = ""
        prefix = prefix.strip()
        if prefix and prefix not in PREFIX_MODE:
            errors.append(f"{label}: prefix {prefix!r} is not one of {', '.join(PREFIX_MODE)} or ''")
        text = entry.get("situation")
        if not isinstance(text, str) or not text.strip():
            errors.append(f"{label}: situation must be non-empty text")
            text = ""
        text = text.strip()
        if not prefix and re.match(r"^[LPDXR]:", text):
            errors.append(f"{label}: the situation starts with a prefix - move it into 'prefix'")
        expect = entry.get("expect")
        mode = PREFIX_MODE.get(prefix, "")
        if expect is not None:
            if not isinstance(expect, str) or expect.strip().lower() not in MODES:
                errors.append(f"{label}: expect must be one of {', '.join(MODES)} (got {expect!r})")
            else:
                expect = expect.strip().lower()
                if mode and expect != mode:
                    errors.append(f"{label}: expect {expect!r} contradicts prefix {prefix!r} ({mode})")
                mode = mode or expect
        elif not prefix:
            errors.append(f"{label}: an unprefixed situation is a mode-detection test and needs "
                          f"expect: one of {', '.join(MODES)}")
        tags = entry.get("tags")
        if tags is None:
            tags = []
        if not isinstance(tags, list):
            errors.append(f"{label}: tags must be a list, e.g. [up, zh] (got {tags!r})")
            tags = []
        bad = [t for t in tags if t not in TAGS]
        if bad:
            errors.append(f"{label}: unknown tag(s) {', '.join(map(repr, bad))} (allowed: {', '.join(TAGS)})")
        clean_tags = tuple(dict.fromkeys(t for t in tags if t in TAGS))
        notes = entry.get("notes")
        if notes is None:
            notes = ""
        if not isinstance(notes, str):
            errors.append(f"{label}: notes must be text (got {type(notes).__name__})")
            notes = str(notes)
        if sid is not None:
            items.append(Situation(sid, prefix, text, mode or "live", clean_tags, notes))
    if errors:
        raise InputError(f"{where}: {len(errors)} schema error(s)\n  " + "\n  ".join(errors))
    if not items:
        raise InputError(f"{where}: no situations")
    return items


def load_situations(path: Path) -> list[Situation]:
    return validate_situations(read_yaml(path, "situations"), show(path))


def select_ids(sits: list[Situation], ids: list[int] | None) -> list[Situation]:
    if ids is None:
        return list(sits)
    wanted = list(dict.fromkeys(ids))
    known = {s.id for s in sits}
    missing = [i for i in wanted if i not in known]
    if missing:
        raise InputError(f"--ids: no situation with id {', '.join(map(str, missing))} "
                         f"(known: {', '.join(map(str, sorted(known)))})")
    return [s for s in sits if s.id in set(wanted)]


# --------------------------------------------------------------------------- coverage

def coverage_counts(sits: list[Situation]) -> Counter:
    counts: Counter = Counter()
    counts["total"] = len(sits)
    for s in sits:
        counts[f"mode:{s.mode}"] += 1
        if not s.prefix:
            counts["unprefixed"] += 1
        for tag in s.tags:
            counts[f"tag:{tag}"] += 1
        if "zh" in s.tags or CJK_RE.search(s.situation):
            counts["zh"] += 1
        if "no-reply" in s.tags or "channel-switch" in s.tags:
            counts["tag:no-reply|channel-switch"] += 1
    return counts


def coverage_gaps(sits: list[Situation]) -> list[tuple[str, int, int, str]]:
    counts = coverage_counts(sits)
    return [(key, counts[key], minimum, label) for key, minimum, label in COVERAGE if counts[key] < minimum]


# --------------------------------------------------------------------------- kernel and leakage

def kernel_version(text: str) -> str:
    match = VERSION_RE.search(text)
    return match.group(1) if match else "unknown"


def calibration_section(kernel: str) -> str | None:
    """The body of kernel section 11 (calibration examples), read at run time."""
    lines = kernel.split("\n")
    start = next((i for i, ln in enumerate(lines) if re.match(r"^#{1,3}\s*(?:\u00a7\s*)?11\b", ln)), None)
    if start is None:
        start = next((i for i, ln in enumerate(lines)
                      if re.match(r"^#{1,3}\s", ln) and "calibration" in ln.lower()), None)
    if start is None:
        return None
    level = len(re.match(r"^(#+)", lines[start]).group(1))
    end = len(lines)
    for j in range(start + 1, len(lines)):
        heading = re.match(r"^(#+)\s", lines[j])
        if heading and len(heading.group(1)) <= level:
            end = j
            break
    return "\n".join(lines[start + 1:end])


def _shingles(toks: list[str], n: int) -> set[tuple[str, ...]]:
    return {tuple(toks[i:i + n]) for i in range(len(toks) - n + 1)}


@dataclass
class Leak:
    situation_id: int
    detail: str


def find_leakage(sits: list[Situation], kernel: str) -> tuple[list[Leak], str]:
    """A situation that matches a kernel section-11 example is an error: the kernel carries its answer."""
    section = calibration_section(kernel)
    if section is None:
        return [], "kernel has no section 11 (calibration examples) - leakage not checked"
    users = [m.group(1).strip() for m in USER_LINE_RE.finditer(section)]
    sec_shingles = _shingles(tokens(section), LEAK_SHINGLE)
    user_toks = [(u, tokens(u)) for u in users]
    leaks: list[Leak] = []
    for s in sits:
        toks = tokens(s.situation)
        reasons: list[str] = []
        if len(toks) >= LEAK_SHINGLE:
            mine = _shingles(toks, LEAK_SHINGLE)
            shared = len(mine & sec_shingles)
            if shared / len(mine) >= LEAK_CONTAINMENT:
                reasons.append(f"{shared}/{len(mine)} word {LEAK_SHINGLE}-grams appear in section 11")
        joined = " ".join(toks)
        for user, utoks in user_toks:
            if not utoks:
                continue
            ratio = SequenceMatcher(None, joined, " ".join(utoks)).ratio()
            theirs = _shingles(utoks, LEAK_SHINGLE) if len(utoks) >= LEAK_SHINGLE else set()
            covered = (len(theirs & _shingles(toks, LEAK_SHINGLE)) / len(theirs)) if theirs and len(toks) >= LEAK_SHINGLE else 0.0
            if ratio >= LEAK_RATIO or covered >= 0.5:
                reasons.append(f"close to the example user line '{user[:60]}' "
                               f"(similarity {ratio:.2f}, {covered:.0%} of its {LEAK_SHINGLE}-grams)")
                break
        if reasons:
            leaks.append(Leak(s.id, "; ".join(reasons)))
    note = f"section 11: {len(users)} example user line(s)"
    return leaks, note


# --------------------------------------------------------------------------- knowledge and context

@dataclass
class Context:
    data: Path
    kernel_path: Path
    situations_path: Path
    runs_dir: Path
    data_explicit: bool

    def kernel_text(self) -> str:
        return read_text(self.kernel_path)

    def knowledge(self) -> tuple[list[tuple[str, str]], list[str]]:
        kdir = self.data / "knowledge"
        if not kdir.is_dir():
            return [], []
        files: list[tuple[str, str]] = []
        skipped: list[str] = []
        for p in sorted(kdir.glob("*.md"), key=lambda q: q.name.lower()):
            if p.name.lower().endswith(".local.md"):
                skipped.append(p.name)   # local-only by convention: never synced, never sent
                continue
            files.append((p.name, read_text(p)))
        return files, skipped


def knowledge_block(files: list[tuple[str, str]]) -> str:
    if not files:
        return ""
    return "Project knowledge:\n\n" + "\n\n".join(
        f'<file name="{name}">\n{text.rstrip()}\n</file>' for name, text in files)


def make_context(args: argparse.Namespace) -> Context:
    explicit = getattr(args, "data", None)
    env_data = os.environ.get("COMM_COACH_DATA", "").strip()
    data = Path(explicit) if explicit else (Path(env_data) if env_data else DEFAULT_DATA)
    kernel = Path(args.kernel) if getattr(args, "kernel", None) else DEFAULT_KERNEL
    return Context(data=data, kernel_path=kernel, situations_path=data / "eval" / "situations.yaml",
                   runs_dir=data / "eval" / "runs", data_explicit=bool(explicit or env_data))


def framework_git() -> dict | None:
    """The framework repo's HEAD and whether kernel/ or eval/ differ from it; None outside git."""
    try:
        top = subprocess.run(["git", "-C", str(FRAMEWORK), "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, timeout=10)
        if top.returncode != 0 or Path(top.stdout.strip()).resolve() != FRAMEWORK:
            return None
        head = subprocess.run(["git", "-C", str(FRAMEWORK), "rev-parse", "HEAD"],
                              capture_output=True, text=True, timeout=10)
        dirty = subprocess.run(["git", "-C", str(FRAMEWORK), "status", "--porcelain", "--", "kernel", "eval"],
                               capture_output=True, text=True, timeout=10)
        return {"sha": head.stdout.strip() or None, "dirty": bool(dirty.stdout.strip())}
    except (OSError, subprocess.SubprocessError):
        return None


def committed_kernel(kernel_path: Path) -> dict | None:
    """HEAD's sha and the sha256 of the kernel as committed there (None: not committed yet).

    None overall when the kernel is not inside the framework git repo: then only the working-tree
    hash can be checked. The tag goes on HEAD, so HEAD must hold the kernel that was evaluated.
    """
    git = framework_git()
    if not git or not git.get("sha"):
        return None
    try:
        rel = Path(kernel_path).resolve().relative_to(FRAMEWORK).as_posix()
        proc = subprocess.run(["git", "-C", str(FRAMEWORK), "show", f"HEAD:{rel}"],
                              capture_output=True, timeout=10)
    except (ValueError, OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return {"head": git["sha"], "sha256": None}
    text = proc.stdout.decode("utf-8-sig", "replace").replace("\r\n", "\n").replace("\r", "\n")
    return {"head": git["sha"], "sha256": sha256(text)}


# --------------------------------------------------------------------------- auto flags

def canonical_numbers(text: str) -> set[str]:
    out: set[str] = set()
    for match in NUMBER_RE.finditer(text):
        s = match.group(0).replace(",", "")
        if ":" in s:
            hour, minute = s.split(":", 1)
            s = str(int(hour)) if minute == "00" else f"{int(hour)}:{minute}"
        elif "." in s:
            whole, frac = s.split(".", 1)
            frac = frac.rstrip("0")
            s = f"{int(whole)}.{frac}" if frac else str(int(whole))
        else:
            s = str(int(s))
        out.add(s)
    return out


def invented_numbers(reply: str, supplied: str) -> list[str]:
    """Numbers in the reply that the situation and knowledge never gave. Advisory only."""
    text = nfkc(reply)
    text = CONFIRM_RE.sub(" ", text)
    text = LIST_MARKER_RE.sub(lambda m: m.group(1), text)
    text = SKIP_NUMBER_RE.sub(" ", text)
    have = canonical_numbers(SKIP_NUMBER_RE.sub(" ", nfkc(supplied)))
    extra = canonical_numbers(text) - have
    return sorted(extra, key=lambda v: (float(v.split(":")[0]), v))


def auto_flags(sit: Situation, reply: str, supplied: str = "") -> list[str]:
    """Cheap heuristics. They raise questions; the scorer decides."""
    text = reply.strip()
    if not text:
        return ["EMPTY reply - do not score; re-run this id"]
    flags: list[str] = []
    first = text.split("\n", 1)[0].strip().lstrip("#*_> ").strip()
    if MODE_ANNOUNCE_RE.match(first):
        flags.append("first line announces the mode (the kernel says never announce it)")
    words, cjk = length_words(text)
    unit = " (Chinese counted as 1 word per 2 characters)" if cjk else ""
    if sit.mode == "live":
        if words > LIVE_WORD_LIMIT:
            flags.append(f"LIVE reply is ~{words} words (limit {LIVE_WORD_LIMIT}){unit}")
        if not SAY_SEND_RE.search(text):
            flags.append("LIVE reply has no 'Say:' or 'Send:' line")
    elif sit.mode == "prep" and words > PREP_WORD_LIMIT:
        flags.append(f"PREP reply is ~{words} words (limit {PREP_WORD_LIMIT} unless asked){unit}")
    numbers = invented_numbers(text, sit.prompt + "\n" + supplied)
    if numbers:
        flags.append(f"number(s) not in the situation or knowledge: {', '.join(numbers)} "
                     "- invented, or should be [confirm: ...]")
    return flags


# --------------------------------------------------------------------------- backends

@dataclass
class Reply:
    text: str = ""
    error: str | None = None
    fatal: bool = False
    stop_reason: str | None = None
    served: list[str] = field(default_factory=list)
    usage: dict | None = None
    extra: dict = field(default_factory=dict)
    duration_s: float = 0.0


def outside_ch(path: Path) -> bool:
    return not any(part.lower().startswith(".ch-") for part in Path(path).resolve().parts)


def resolve_claude(explicit: str | None) -> list[str]:
    """argv prefix for the real claude executable. Never runs a .cmd/.bat shim (cmd.exe re-parses args)."""
    if explicit:
        target = Path(explicit)
        if not target.is_file():
            raise InputError(f"--claude-bin {explicit}: no such file")
    else:
        found = shutil.which("claude")
        if not found:
            raise InputError("claude executable not found on PATH. Install Claude Code, or pass "
                             "--claude-bin <path to claude / claude.exe>.")
        target = Path(found)
    return _command_for(target.resolve())      # absolute: the child runs from a temp folder


def _command_for(target: Path) -> list[str]:
    suffix = target.suffix.lower()
    if os.name == "nt" and suffix == "":
        # Git Bash's extensionless `claude` is a sh script CreateProcess cannot run: use its siblings.
        for sibling in (target.with_suffix(".exe"), target.with_suffix(".cmd")):
            if sibling.exists():
                return _command_for(sibling)
    if suffix == ".py":
        return [sys.executable, str(target)]
    if suffix in (".cmd", ".bat"):
        return _resolve_shim(target)
    if suffix == ".ps1":
        sibling = target.with_suffix(".cmd")
        if sibling.exists():
            return _resolve_shim(sibling)
        raise InputError(f"{target} is a PowerShell shim; pass --claude-bin <path to claude.exe>")
    return [str(target)]


def _resolve_shim(shim: Path) -> list[str]:
    """npm writes claude.cmd pointing at bin/claude.exe (newer installs) or at node + cli.js (older)."""
    text = shim.read_text(encoding="utf-8", errors="replace")
    base = shim.parent
    targets = []
    for raw in re.findall(r'"%~?dp0%?\\?([^"%]+?\.(?:exe|js|cjs|mjs|py))"', text, re.IGNORECASE):
        targets.append(base / raw.replace("\\", os.sep))
    for exe in (t for t in targets if t.suffix.lower() == ".exe" and t.name.lower() != "node.exe"):
        if exe.exists():
            return [str(exe)]
    for script in (t for t in targets if t.suffix.lower() in (".js", ".cjs", ".mjs")):
        if script.exists():
            node = base / "node.exe"
            node_cmd = str(node) if node.exists() else shutil.which("node")
            if not node_cmd:
                raise InputError(f"{shim} runs {script.name} with node, and node is not on PATH")
            return [node_cmd, str(script)]
    for script in (t for t in targets if t.suffix.lower() == ".py"):
        if script.exists():
            return [sys.executable, str(script)]
    raise InputError(f"cannot resolve the real executable behind {shim}; "
                     "pass --claude-bin <path to claude.exe>")


class CliBackend:
    """Headless Claude Code on the subscription (the policy's 'Headless Claude Code' notes)."""

    name = "cli"

    def __init__(self, args: argparse.Namespace, model: str, effort: str, system_text: str):
        self.model, self.effort = model, effort
        self.timeout = args.timeout
        self.system_text = system_text
        self.cmd = resolve_claude(args.claude_bin)
        token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "").strip()
        if token and re.search(r"\s", token):
            raise InputError("CLAUDE_CODE_OAUTH_TOKEN contains whitespace or a line break - copy it with "
                             "the copy button, not by selecting wrapped text, and set it again")
        self.auth = "oauth-token" if token else "inherited-login"
        self.tmp = Path(tempfile.mkdtemp(prefix="commcoach-cli-"))
        if not outside_ch(self.tmp):
            shutil.rmtree(self.tmp, ignore_errors=True)
            raise InputError(f"the temp folder {self.tmp} is inside a .CH-* folder; point TEMP/TMP elsewhere "
                             "so claude -p starts outside every project")
        try:
            self.cwd = self.tmp / "cwd"
            self.cwd.mkdir()
            self.system_file = self.tmp / "system-prompt.md"
            with open(self.system_file, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(system_text)
            # Drop every CLAUDE*/ANTHROPIC* variable: an inherited ANTHROPIC_API_KEY would make claude -p
            # metered, a Desktop session's CLAUDE_EFFORT or base URL would change what is measured.
            env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("CLAUDE", "ANTHROPIC"))}
            if token:
                env["CLAUDE_CODE_OAUTH_TOKEN"] = token
                config = self.tmp / "config"      # empty: no user CLAUDE.md, rules, hooks, plugins or MCP
                config.mkdir()
                env["CLAUDE_CONFIG_DIR"] = str(config)
            elif os.environ.get("CLAUDE_CONFIG_DIR"):
                env["CLAUDE_CONFIG_DIR"] = os.environ["CLAUDE_CONFIG_DIR"]   # the login lives there
            self.env = env
            self.version = self._version()
        except BaseException:
            shutil.rmtree(self.tmp, ignore_errors=True)
            raise

    def _version(self) -> str | None:
        """`claude --version` (no model call): proves the executable starts before any situation is sent."""
        try:
            proc = subprocess.run(self.cmd + ["--version"], capture_output=True, cwd=self.cwd,
                                  env=self.env, timeout=60)
        except (OSError, subprocess.SubprocessError) as exc:
            raise InputError(f"could not start claude ({' '.join(self.cmd)}): {exc}") from None
        out = proc.stdout.decode("utf-8", "replace").strip()
        if proc.returncode != 0:
            tail = (proc.stderr.decode("utf-8", "replace").strip() or out)[-300:]
            raise InputError(f"`claude --version` failed (exit {proc.returncode}): {tail}")
        return out.splitlines()[0] if out else None

    def argv(self) -> list[str]:
        # No --bare (it ignores OAuth), no --fallback-model (a silent model swap), no
        # --append-system-prompt (it keeps Claude Code's coding prompt). The kernel + knowledge go in a
        # UTF-8 file, the situation on stdin, so nothing long or non-ASCII rides on argv.
        return self.cmd + [
            "-p", "--output-format", "json",
            "--model", self.model, "--effort", self.effort,
            "--system-prompt-file", str(self.system_file),
            "--tools", "", "--strict-mcp-config", "--disable-slash-commands",
            "--setting-sources", "project,local", "--no-session-persistence", "--max-turns", "1",
        ]

    def describe(self) -> dict:
        return {"backend_version": self.version, "cli_auth": self.auth, "max_tokens": None,
                "claude_command": [Path(c).name for c in self.cmd]}

    def ask(self, prompt: str) -> Reply:
        start = time.monotonic()
        try:
            proc = subprocess.run(self.argv(), input=prompt.encode("utf-8"), capture_output=True,
                                  cwd=self.cwd, env=self.env, timeout=self.timeout)
        except subprocess.TimeoutExpired:
            return Reply(error=f"claude -p timed out after {self.timeout}s", duration_s=self.timeout)
        except OSError as exc:
            return Reply(error=f"could not start claude: {exc}", fatal=True)
        elapsed = round(time.monotonic() - start, 2)
        out = proc.stdout.decode("utf-8", "replace").strip()
        err_tail = proc.stderr.decode("utf-8", "replace").strip()[-300:]
        data = _parse_cli_json(out)
        if data is None:
            msg = f"claude -p exited {proc.returncode} without JSON output"
            if err_tail:
                msg += f": {err_tail}"
            return Reply(error=msg, fatal=bool(FATAL_RE.search(msg)), duration_s=elapsed)
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else None
        model_usage = data.get("modelUsage") if isinstance(data.get("modelUsage"), dict) else {}
        extra = {"subtype": data.get("subtype"), "num_turns": data.get("num_turns"),
                 "cli_reported_cost_usd": data.get("total_cost_usd")}
        common = dict(usage=usage, served=sorted(model_usage), stop_reason=data.get("stop_reason"),
                      extra=extra, duration_s=elapsed)
        if data.get("is_error"):                      # before anything else: subtype can say "success"
            msg = str(data.get("result") or data.get("error") or data.get("subtype") or "is_error set")
            return Reply(error=f"claude -p is_error: {msg[:500]}", fatal=bool(FATAL_RE.search(msg)), **common)
        if proc.returncode != 0:
            return Reply(error=f"claude -p exited {proc.returncode}: {err_tail}", **common)
        text = data.get("result")
        if not isinstance(text, str):
            return Reply(error="claude -p returned no result text", **common)
        return Reply(text=text, **common)

    def canary(self, prompt: str, usage: dict | None) -> str | None:
        """Served input far above kernel + knowledge + prompt means something else was loaded."""
        if not usage:
            return None
        served = sum(int(usage.get(k) or 0) for k in
                     ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
        expected = est_tokens(self.system_text + prompt)
        if served > expected * 1.5 + 2000:
            return (f"context canary: ~{served:,} input tokens served vs ~{expected:,} expected - something "
                    "besides kernel + knowledge + prompt was loaded (user CLAUDE.md, rules, tools?)")
        return None

    def close(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)


def _parse_cli_json(out: str) -> dict | None:
    if not out:
        return None
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        last = out.splitlines()[-1]
        try:
            data = json.loads(last)
        except json.JSONDecodeError:
            return None
    if isinstance(data, list):
        results = [d for d in data if isinstance(d, dict) and d.get("type") == "result"]
        data = results[-1] if results else None
    return data if isinstance(data, dict) else None


class ApiBackend:
    """Metered Messages API. Only reached through --backend api --metered."""

    name = "api"

    def __init__(self, args: argparse.Namespace, model: str, effort: str, kernel: str, kblock: str):
        try:
            import anthropic  # noqa: PLC0415 - imported only for the metered backend
        except ImportError:
            raise InputError("--backend api needs the anthropic SDK: pip install \"anthropic>=0.96,<2\" "
                             "(see the optional line in requirements.txt)") from None
        if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
            raise InputError("--backend api needs ANTHROPIC_API_KEY in the environment (never in a repo)")
        self.sdk = anthropic
        self.model, self.effort, self.max_tokens = model, effort, args.max_tokens
        self.client = anthropic.Anthropic()
        cache = {"type": "ephemeral"}
        if kblock:
            self.system = [{"type": "text", "text": kernel},
                           {"type": "text", "text": kblock, "cache_control": cache}]
        else:
            self.system = [{"type": "text", "text": kernel, "cache_control": cache}]

    def describe(self) -> dict:
        return {"backend_version": f"anthropic {getattr(self.sdk, '__version__', '?')}",
                "max_tokens": self.max_tokens}

    def ask(self, prompt: str) -> Reply:
        a = self.sdk
        start = time.monotonic()
        try:
            # No `thinking` field: the model's default adaptive thinking, as in the Project. No
            # server-side fallbacks: an eval must measure one model, and a refusal is flagged instead.
            msg = self.client.messages.create(
                model=self.model, max_tokens=self.max_tokens, system=self.system,
                messages=[{"role": "user", "content": prompt}],
                output_config={"effort": self.effort})
        except (a.AuthenticationError, a.PermissionDeniedError, a.NotFoundError, a.BadRequestError) as exc:
            return Reply(error=f"API {getattr(exc, 'status_code', '?')}: {exc}", fatal=True)
        except a.APIStatusError as exc:
            return Reply(error=f"API {exc.status_code}: {exc}")
        except a.APIConnectionError as exc:
            return Reply(error=f"API connection error: {exc}")
        except Exception as exc:  # noqa: BLE001 - one bad row must not lose the run
            return Reply(error=f"{type(exc).__name__}: {exc}")
        text = "".join(getattr(b, "text", "") for b in msg.content if getattr(b, "type", "") == "text")
        extra = {}
        details = getattr(msg, "stop_details", None)
        if details is not None:
            extra["stop_details"] = plain(details)
        return Reply(text=text, stop_reason=msg.stop_reason, served=[msg.model], usage=plain(msg.usage),
                     extra=extra, duration_s=round(time.monotonic() - start, 2))

    def canary(self, prompt: str, usage: dict | None) -> str | None:
        return None

    def close(self) -> None:
        close = getattr(self.client, "close", None)
        if callable(close):
            close()


def api_endpoint() -> str:
    base = os.environ.get("ANTHROPIC_BASE_URL", "").strip()
    if not base:
        return "api.anthropic.com (SDK default)"
    match = re.match(r"^(\w+://)?([^/@]*@)?([^/]+)", base)
    return f"{match.group(3) if match else '?'} (ANTHROPIC_BASE_URL)"


def metered_statement(model: str, system_text: str, prompts: list[str], max_tokens: int) -> str:
    """The metered-run cost statement: printed before the first metered request."""
    n = len(prompts)
    prefix = est_tokens(system_text)
    fresh = sum(est_tokens(p) for p in prompts)
    lines = [
        "METERED RUN - --backend api --metered (opt-in only; the subscription path comes first)",
        "  Capability the subscription path lacks: explicit max_tokens and effort per request, and exact",
        "    per-request usage from the Messages API. The eval itself runs fine on the default cli backend.",
        f"  API introduced: Anthropic Messages API at {api_endpoint()}, model {model}, {n} request(s).",
        "  Incremental cost: yes - every request is billed to the API key's account.",
        f"  Tokens: ~{prefix:,} prefix (kernel + knowledge, cached after the first request) + ~{fresh:,} "
        f"situation tokens in; at most {n * max_tokens:,} out (the max_tokens cap, thinking included).",
    ]
    price = PRICES.get(model)
    if price:
        p_in, p_out, p_read = price
        worst = (prefix * n + fresh) * p_in / 1e6 + n * max_tokens * p_out / 1e6
        typical = (prefix * 1.25 * p_in + prefix * max(n - 1, 0) * p_read + fresh * p_in + n * 1500 * p_out) / 1e6
        lines.append(f"  Estimate at list price (${p_in:.2f} in / ${p_out:.2f} out / ${p_read:.2f} cache read per MTok):"
                     f" ~${typical:.2f} if replies average ~1,500 output tokens; worst case ${worst:.2f}.")
        lines.append("    INFERENCE, not measured - the run file records real usage per row.")
    else:
        lines.append(f"  No list price on file for {model}: cost not estimated (check current pricing).")
    lines += [
        "  Lowest-cost alternative: the same command without --backend (cli: claude -p on your",
        "    subscription, $0 incremental), or option B - by hand in the Project.",
        "  No silent switch: nothing falls back from cli to api, and this run never changes model.",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- run files

class _RunDumper(yaml.SafeDumper):
    def ignore_aliases(self, data: Any) -> bool:
        return True


def _str_representer(dumper: yaml.SafeDumper, data: str) -> yaml.ScalarNode:
    style = "|" if "\n" in data else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


_RunDumper.add_representer(str, _str_representer)


def dump_yaml(payload: Any) -> str:
    return yaml.dump(payload, Dumper=_RunDumper, allow_unicode=True, sort_keys=False, width=100,
                     default_flow_style=False)


def write_text_lf(path: Path, text: str) -> None:
    """UTF-8, LF, written to a temp file and moved into place so a crash never leaves half a file."""
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    try:
        os.replace(tmp, path)
    except PermissionError:          # Windows: the file is open in an editor
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        tmp.unlink(missing_ok=True)


def new_run_path(runs_dir: Path, version: str, backend: str) -> Path:
    runs_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{dt.datetime.now():%Y-%m-%d_%H%M}_v{version}_{backend}"
    path = runs_dir / f"{stem}.yaml"
    n = 2
    while path.exists():
        path = runs_dir / f"{stem}_{n}.yaml"
        n += 1
    return path


HOW_TO_SCORE = ("For every row: scores 1-5 (integers) for outcome, register, sayable, length, and truth "
                "pass|fail. Rows with an error or do_not_score: true cannot be scored - re-run those ids. "
                "Then: run_eval.py score <this file>.")

STOP_OK = {"end_turn", "stop_sequence", None}


def build_row(sit: Situation, reply: Reply, backend: Any, model: str, hashes: dict, supplied: str) -> dict:
    text = (reply.text or "").replace("\r\n", "\n").strip()
    # A line ending in a space or tab makes PyYAML fall back to an escaped "..." string; strip those so the
    # reply stays a readable | block for scoring by hand.
    text = "\n".join(line.rstrip() for line in text.split("\n")).replace("\t", "    ")
    flags: list[str] = []
    dns: list[str] = []
    if reply.error:
        dns.append("error")
    else:
        flags = auto_flags(sit, text, supplied)
        if not text:
            dns.append("empty reply")
        if reply.stop_reason in ("max_tokens", "model_context_window_exceeded"):
            flags.insert(0, f"TRUNCATED (stop_reason {reply.stop_reason}) - do not score; re-run this id")
            dns.append("truncated")
        elif reply.stop_reason == "refusal":
            flags.insert(0, "REFUSAL (stop_reason refusal) - do not score")
            dns.append("refusal")
        elif reply.stop_reason not in STOP_OK:
            flags.insert(0, f"unexpected stop_reason {reply.stop_reason} - do not score")
            dns.append(f"stop_reason {reply.stop_reason}")
        if reply.served and model not in reply.served:
            flags.append(f"served model {', '.join(reply.served)} differs from the requested {model}")
        elif len(reply.served) > 1:
            flags.append(f"more than one model served: {', '.join(reply.served)}")
        canary = backend.canary(sit.prompt, reply.usage)
        if canary:
            flags.append(canary)
    return {
        "id": sit.id,
        "prefix": sit.prefix,
        "mode": sit.mode,
        "tags": list(sit.tags),
        "situation": sit.situation,
        "notes": sit.notes,
        "response": text,
        "error": reply.error,
        "do_not_score": ", ".join(dns) if dns else False,
        "auto_flags": flags,
        "scores": {k: None for k in SCORED},
        "truth": None,
        "comment": "",
        "meta": {
            "backend": backend.name,
            "model_requested": model,
            "model_served": list(reply.served),
            "stop_reason": reply.stop_reason,
            "is_error": bool(reply.error),
            "usage": reply.usage,
            "duration_s": reply.duration_s,
            **{k: v for k, v in reply.extra.items() if v is not None},
            "kernel_sha256": hashes["kernel"],
            "knowledge_sha256": hashes["knowledge"],
        },
    }


# --------------------------------------------------------------------------- commands

def _model_and_effort(args: argparse.Namespace) -> tuple[str, str]:
    model = (args.model or os.environ.get("EVAL_MODEL", "") or DEFAULT_MODEL).strip()
    effort = (args.effort or os.environ.get("EVAL_EFFORT", "") or DEFAULT_EFFORT).strip().lower()
    if effort not in EFFORTS:
        raise InputError(f"effort {effort!r} is not one of {', '.join(EFFORTS)}")
    if not re.search(r"\d", model):
        say(f"warning: model {model!r} looks like an alias; the served model is recorded, "
            "but a full ID (e.g. claude-sonnet-5-5) keeps runs comparable", err=True)
    return model, effort


def _report_leaks(leaks: list[Leak]) -> None:
    for leak in leaks:
        say(f"  id {leak.situation_id}: matches a kernel calibration example - {leak.detail}")
    say("  Calibration examples carry their own answers; rewrite these situations with different facts.")


def cmd_check(args: argparse.Namespace, ctx: Context) -> int:
    kernel = ctx.kernel_text()
    files, skipped = ctx.knowledge()
    say(f"Data:       {show(ctx.data)}")
    say(f"Kernel:     {show(ctx.kernel_path)} (Version {kernel_version(kernel)})")
    say(f"Knowledge:  {len(files)} file(s)" + (f", {len(skipped)} *.local.md skipped" if skipped else ""))
    sits = load_situations(ctx.situations_path)
    say(f"Schema:     OK - {len(sits)} situation(s) in {show(ctx.situations_path)}")
    leaks, note = find_leakage(sits, kernel)
    if leaks:
        say(f"Leakage:    {len(leaks)} ERROR(S) ({note})")
        _report_leaks(leaks)
    elif note.startswith("kernel has no"):
        say(f"Leakage:    WARNING - {note}")
    else:
        say(f"Leakage:    OK ({note})")
    gaps: list = []
    if not args.schema_only:
        counts = coverage_counts(sits)
        say("Coverage (minimums from eval-set.md):")
        for key, minimum, label in COVERAGE:
            have = counts[key]
            mark = "ok " if have >= minimum else "GAP"
            say(f"  {mark} {key:<28} {have:>3} / {minimum:<3} {label}")
        gaps = coverage_gaps(sits)
        say(f"Coverage:   {'OK' if not gaps else f'{len(gaps)} gap(s) - not taggable until filled'}")
    if leaks:
        return 2
    return 1 if gaps else 0


def cmd_run(args: argparse.Namespace, ctx: Context) -> int:
    model, effort = _model_and_effort(args)
    kernel = ctx.kernel_text()
    version = kernel_version(kernel)
    sits = load_situations(ctx.situations_path)
    leaks, _ = find_leakage(sits, kernel)
    if leaks:
        say("error: situations match the kernel's calibration examples - nothing was sent", err=True)
        _report_leaks(leaks)
        return 2
    selected = select_ids(sits, args.ids)
    gaps = coverage_gaps(sits)
    if gaps:
        say(f"note: the set has {len(gaps)} coverage gap(s) (run 'check'); a run is fine, a tag is not")
    files, skipped = ctx.knowledge()
    kblock = knowledge_block(files)
    system_text = kernel + ("\n\n" + kblock if kblock else "")
    if version == "unknown":
        say("warning: no 'Version X.Y' line in the kernel; the run file will say 'unknown'", err=True)
    if args.backend == "api":
        say(metered_statement(model, system_text, [s.prompt for s in selected], args.max_tokens))
        if args.dry_run:
            say("dry run: nothing sent.")
            return 0
        if not args.metered:
            raise InputError("--backend api is metered and opt-in: add --metered to confirm. "
                             "Without it, drop --backend to use the cli backend on your subscription.")
        backend: Any = ApiBackend(args, model, effort, kernel, kblock)
    else:
        backend = CliBackend(args, model, effort, system_text)
        if backend.auth == "inherited-login":
            say("note: CLAUDE_CODE_OAUTH_TOKEN is not set, so claude -p relies on this terminal's login and "
                "your user config (CLAUDE.md, rules, hooks) may load into the context - the context canary "
                "flags it. Recommended: claude setup-token, then set CLAUDE_CODE_OAUTH_TOKEN.", err=True)
        if args.dry_run:
            say(f"dry run: {' '.join(backend.cmd)} ({backend.version or 'version unknown'}), "
                f"{len(selected)} situation(s), model {model}, effort {effort}. Nothing sent.")
            backend.close()
            return 0
    hashes = {"kernel": sha256(kernel), "knowledge": sha256(kblock)}
    supplied = kblock
    try:     # until the loop's finally takes over: never leave kernel + knowledge in the temp folder
        payload: dict = {
            "run_file_format": 2,
            "status": "running",
            "date": dt.date.today().isoformat(),
            "started_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            "finished_at": None,
            "harness": f"run_eval.py {HARNESS_VERSION}",
            "backend": backend.name,
            "model_requested": model,
            "effort": effort,
            **backend.describe(),
            "kernel_version": version,
            "kernel_sha256": hashes["kernel"],
            "knowledge_files": [{"name": n, "sha256": sha256(t)} for n, t in files],
            "knowledge_sha256": hashes["knowledge"],
            "knowledge_skipped": skipped,
            "situations_sha256": sha256(read_text(ctx.situations_path)),
            "situations_total": len(sits),
            "ids_run": [s.id for s in selected],
            "framework_git": framework_git(),
            "how_to_score": HOW_TO_SCORE,
            "results": [],
        }
        path = new_run_path(ctx.runs_dir, version, backend.name)
        write_text_lf(path, dump_yaml(payload))
    except OSError as exc:
        backend.close()
        raise InputError(f"cannot write a run file in {show(ctx.runs_dir)}: {exc} - nothing was sent") from None
    except BaseException:
        backend.close()
        raise
    say(f"Run: {len(selected)} situation(s) · backend {backend.name} · model {model} · effort {effort} "
        f"· kernel {version} -> {show(path)}")
    bad = 0
    streak = 0
    try:
        for i, sit in enumerate(selected, 1):
            reply = backend.ask(sit.prompt)
            row = build_row(sit, reply, backend, model, hashes, supplied)
            payload["results"].append(row)
            write_text_lf(path, dump_yaml(payload))
            words, _ = length_words(row["response"])
            preview = re.sub(r"\s+", " ", sit.situation)[:40]
            state = f"ERROR: {reply.error[:160]}" if reply.error else f"{words} words"
            tail = f" - {'; '.join(row['auto_flags'])}" if row["auto_flags"] else ""
            say(f"[{i}/{len(selected)}] id {sit.id} {sit.prefix or '(' + sit.mode + '?)'} {preview} - {state}{tail}")
            if row["do_not_score"]:
                bad += 1
            streak = streak + 1 if reply.error else 0
            if reply.fatal or streak >= CONSECUTIVE_ERROR_LIMIT:
                why = "a setup error (auth, model or CLI flags)" if reply.fatal else f"{streak} errors in a row"
                payload["status"] = f"aborted: {why}"
                break
        else:
            payload["status"] = "complete"
    except KeyboardInterrupt:
        payload["status"] = "aborted: interrupted"
        payload["finished_at"] = dt.datetime.now().astimezone().isoformat(timespec="seconds")
        write_text_lf(path, dump_yaml(payload))
        say(f"\nInterrupted. {len(payload['results'])} completed row(s) kept in {show(path)}", err=True)
        return 130
    finally:
        backend.close()
    payload["finished_at"] = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    write_text_lf(path, dump_yaml(payload))
    _usage_summary(payload, model)
    done = len(payload["results"])
    if payload["status"] != "complete":
        last = payload["results"][-1]["error"] if payload["results"] else ""
        say(f"\nRun {payload['status']}. {done} row(s) kept in {show(path)}.", err=True)
        if last and re.search(r"login|oauth|token|authenticat", last, re.IGNORECASE) and backend.name == "cli":
            say("Fix: run `claude setup-token`, set CLAUDE_CODE_OAUTH_TOKEN (copy button), and re-run. "
                "The harness never falls back to the metered API.", err=True)
        return 1
    script = show(Path(__file__))
    data_arg = f" --data {quote(show(ctx.data))}" if ctx.data_explicit else ""
    say(f"\nWrote {show(path)}: {done} row(s), {bad} not scorable.")
    say("Fill scores (1-5) and truth (pass|fail) in every row, then:")
    say(f"  python {quote(script)}{data_arg} score {quote(show(path))}")
    return 1 if bad else 0


def _usage_summary(payload: dict, model: str) -> None:
    totals: Counter = Counter()
    for row in payload["results"]:
        usage = row["meta"].get("usage") or {}
        for key in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens"):
            value = usage.get(key)
            if isinstance(value, int):
                totals[key] += value
    if not totals:
        say("Usage: not reported by the backend (not measured).")
        return
    say("Usage: " + ", ".join(f"{k} {v:,}" for k, v in totals.items()))
    if payload["backend"] == "api" and model in PRICES:
        p_in, p_out, p_read = PRICES[model]
        cost = (totals["input_tokens"] * p_in + totals["cache_creation_input_tokens"] * p_in * 1.25
                + totals["cache_read_input_tokens"] * p_read + totals["output_tokens"] * p_out) / 1e6
        say(f"Metered cost at list price, computed from the recorded usage: ~${cost:.2f}")
    elif payload["backend"] == "cli":
        say("Cost: $0 incremental on the subscription (claude -p's total_cost_usd is informational only).")


def _resolve_runfile(arg: str, ctx: Context) -> Path:
    candidates = [Path(arg), ctx.data / arg, ctx.runs_dir / arg, ctx.runs_dir / Path(arg).name]
    for cand in candidates:
        if cand.is_file():
            return cand
    raise InputError(f"no such run file: {arg} (also looked in {show(ctx.runs_dir)})")


def _validate_run(data: Any) -> tuple[list[dict], list[str]]:
    errors: list[str] = []
    if not isinstance(data, dict):
        return [], ["not a run file: expected a mapping with 'results' (the file is empty or malformed)"]
    results = data.get("results")
    if not isinstance(results, list) or not results:
        return [], ["the run file has no results"]
    if not isinstance(data.get("kernel_version"), (str, int, float)):
        errors.append("missing kernel_version at the top of the file")
    seen: set = set()
    for n, row in enumerate(results, 1):
        if not isinstance(row, dict):
            errors.append(f"result #{n}: not a mapping")
            continue
        rid = row.get("id")
        label = f"id {rid}" if type(rid) is int else f"result #{n}"
        if type(rid) is not int:
            errors.append(f"{label}: id must be an integer (got {rid!r})")
        elif rid in seen:
            errors.append(f"{label}: appears twice in the run file")
        else:
            seen.add(rid)          # ints only: a list or mapping id is unhashable
        if row.get("error"):
            errors.append(f"{label}: the run recorded an error - re-run it (run --ids {rid}), don't score it")
        if row.get("do_not_score"):
            errors.append(f"{label}: marked do_not_score ({row.get('do_not_score')}) - re-run it")
        scores = row.get("scores")
        if not isinstance(scores, dict):
            errors.append(f"{label}: scores missing (expected {', '.join(SCORED)})")
        else:
            for key in SCORED:
                value = scores.get(key)
                if type(value) is not int or not 1 <= value <= 5:
                    errors.append(f"{label}: scores.{key} = {value!r} - must be an integer 1-5")
        truth = row.get("truth")
        norm = truth.strip().lower() if isinstance(truth, str) else None
        if norm not in ("pass", "fail"):
            errors.append(f"{label}: truth = {truth!r} - must be pass or fail")
    return results, errors


LOG_HEADER = ("| Run file | Date | Kernel | Backend | Model | Rows | Outcome | Register | Sayable | Length "
              "| Overall | Truth fails | Gate |")
LOG_SEP = "|---|---|---|---|---|---|---|---|---|---|---|---|---|"
LOG_SEP_RE = re.compile(r"^\|(\s*:?-+:?\s*\|)+\s*$")


def update_log(log: Path, run_name: str, row_line: str) -> None:
    """One row per run file under a header; re-scoring replaces the row. LF, UTF-8."""
    if log.exists():
        lines = read_text(log).split("\n")
        while lines and not lines[-1].strip():
            lines.pop()
    else:
        lines = ["# Eval run log", "",
                 "One row per run file. `run_eval.py score` writes it; re-scoring a file replaces its row.", ""]
    head = next((i for i, ln in enumerate(lines)
                 if ln.strip() == LOG_HEADER and i + 1 < len(lines) and LOG_SEP_RE.match(lines[i + 1].strip())),
                None)
    if head is None:
        while lines and not lines[-1].strip():
            lines.pop()
        if lines:
            lines.append("")
        lines += [LOG_HEADER, LOG_SEP]
        head = len(lines) - 2
    j = head + 2
    placed = False
    while j < len(lines) and lines[j].startswith("|"):
        first = lines[j].strip().strip("|").split("|")[0].strip()
        if first == run_name:
            if placed:
                del lines[j]
                continue
            lines[j] = row_line
            placed = True
        j += 1
    if not placed:
        lines.insert(j, row_line)
    write_text_lf(log, "\n".join(lines) + "\n")


def cmd_score(args: argparse.Namespace, ctx: Context) -> int:
    path = _resolve_runfile(args.runfile, ctx)
    if not ctx.data_explicit and path.resolve().parent.name == "runs" and path.resolve().parent.parent.name == "eval":
        ctx = Context(data=path.resolve().parents[2], kernel_path=ctx.kernel_path,
                      situations_path=path.resolve().parents[2] / "eval" / "situations.yaml",
                      runs_dir=path.resolve().parent, data_explicit=False)
    data = read_yaml(path, "run file")
    results, errors = _validate_run(data)
    if errors:
        say(f"INVALID run file {show(path)} - nothing logged, not taggable:", err=True)
        for err in errors:
            say(f"  {err}", err=True)
        return 2
    n = len(results)
    totals = {k: sum(r["scores"][k] for r in results) for k in SCORED}
    metric = {k: round2(Fraction(totals[k], n)) for k in SCORED}
    overall = round2(Fraction(sum(totals.values()), len(SCORED) * n))
    fails = sum(1 for r in results if r["truth"].strip().lower() == "fail")
    version = str(data.get("kernel_version"))

    reasons: list[str] = []
    if str(data.get("status", "complete")) != "complete":
        reasons.append(f"run status is {data.get('status')!r}")
    try:
        sits = load_situations(ctx.situations_path)
    except InputError as exc:
        sits = []
        reasons.append(f"cannot read the current situations: {str(exc).splitlines()[0]}")
    if sits:
        current = {s.id: s for s in sits}
        rows = {r["id"]: r for r in results}
        missing = sorted(set(current) - set(rows))
        if missing:
            reasons.append(f"{len(missing)} of {len(current)} situations are not in this run "
                           f"(ids {', '.join(map(str, missing[:12]))}{' ...' if len(missing) > 12 else ''})")
        gone = sorted(set(rows) - set(current))
        if gone:
            reasons.append(f"ids no longer in situations.yaml: {', '.join(map(str, gone))}")
        changed = sorted(i for i in set(rows) & set(current)
                         if (str(rows[i].get("prefix") or ""), str(rows[i].get("situation") or "").strip())
                         != (current[i].prefix, current[i].situation))
        if changed:
            reasons.append(f"situation text changed since this run: ids {', '.join(map(str, changed))}")
        if len(current) < MIN_TOTAL:
            reasons.append(f"the set has {len(current)} situations; a tag needs at least {MIN_TOTAL}")
        gaps = [g for g in coverage_gaps(sits) if g[0] != "total"]
        if gaps:
            reasons.append("coverage gaps: " + ", ".join(f"{k} {h}/{m}" for k, h, m, _ in gaps))
    if not re.fullmatch(r"\d+\.\d+", version):
        reasons.append(f"kernel_version is {version!r}; a tag needs a 'Version X.Y' line in the kernel")
    run_kernel = data.get("kernel_sha256")
    committed = None
    try:
        kernel = ctx.kernel_text()
        if sits:
            leaks, _ = find_leakage(sits, kernel)
            if leaks:
                reasons.append(f"ids {', '.join(str(x.situation_id) for x in leaks)} match kernel calibration examples")
        if not run_kernel:
            reasons.append("the run file has no kernel_sha256, so the evaluated kernel cannot be identified")
        elif run_kernel != sha256(kernel):
            reasons.append("the kernel changed since this run - re-run before tagging")
        else:
            committed = committed_kernel(ctx.kernel_path)      # where the tag must go; see the pass message
    except InputError as exc:
        reasons.append(f"cannot read the kernel: {exc}")
    mixed = sorted(r["id"] for r in results if isinstance(r.get("meta"), dict)
                   and r["meta"].get("kernel_sha256") is not None and r["meta"].get("kernel_sha256") != run_kernel)
    if run_kernel and mixed:
        reasons.append(f"rows run on a different kernel than the file header: ids {', '.join(map(str, mixed))}")

    if overall < TARGET or fails:
        gate = "BELOW"
    elif reasons:
        gate = "PARTIAL"
    else:
        gate = "PASS"
    model = data.get("model_requested") or data.get("model") or "?"
    row_line = (f"| {path.name} | {data.get('date', '?')} | {version} | {data.get('backend', '?')} | {model} | {n} | "
                + " | ".join(str(metric[k]) for k in SCORED)
                + f" | {overall} | {fails} | {gate} |")
    log = path.resolve().parent / "LOG.md"
    update_log(log, path.name, row_line)

    say(f"Run file: {show(path)} ({n} rows, kernel {version}, backend {data.get('backend', '?')}, model {model})")
    say("  " + " · ".join(f"{k} {metric[k]}" for k in SCORED) + f" · overall {overall} · truth fails {fails}")
    say(f"Logged in {show(log)} (gate {gate}).")
    if gate == "BELOW":
        say(f"\nBelow target (overall >= {TARGET} on the rounded value, 0 truth fails). Don't tag {version}.")
        return 1
    if gate == "PARTIAL":
        say("\nMeets the score target, but NOT TAGGABLE:")
        for reason in reasons:
            say(f"  - {reason}")
        return 1
    message = f"-m \"eval {overall} avg, {fails} truth fails, {n} situations, {path.name}\""
    if committed and committed["sha256"] == data.get("kernel_sha256"):
        # Pinned to the commit that holds the evaluated kernel, so a later commit can't take the tag.
        say(f"\nPasses. Tag it in the framework repo:\n  git tag -a v{version} {committed['head'][:12]} {message}")
    elif committed:
        # A bare `git tag` now would label HEAD, which still holds another kernel.
        say(f"\nPasses. HEAD of the framework repo does not hold this kernel yet: commit kernel/ first, then "
            f"tag that commit:\n  git tag -a v{version} {message}")
    else:
        say(f"\nPasses. Tag it in the framework repo:\n  git tag -a v{version} {message}")
    return 0


# --------------------------------------------------------------------------- CLI

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="run_eval.py", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", help="data folder with knowledge/ and eval/ (default: $COMM_COACH_DATA, "
                                       "else framework/starter)")
    parser.add_argument("--kernel", help="kernel file (default: framework/kernel/instructions.md)")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--data", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    common.add_argument("--kernel", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command", required=True)

    p_check = sub.add_parser("check", parents=[common], help="validate situations.yaml: schema, coverage, leakage")
    p_check.add_argument("--schema-only", action="store_true", help="schema and leakage only, skip coverage")
    p_check.set_defaults(func=cmd_check)

    p_run = sub.add_parser("run", parents=[common], help="ask the coach each situation; write a run file")
    p_run.add_argument("--ids", nargs="+", type=int, metavar="ID", help="only these situation ids")
    p_run.add_argument("--backend", choices=("cli", "api"), default="cli",
                       help="cli (default): claude -p on the subscription; api: metered, needs --metered")
    p_run.add_argument("--metered", action="store_true", help="confirm the metered api backend (billed per token)")
    p_run.add_argument("--model", help=f"model ID (default: $EVAL_MODEL, else {DEFAULT_MODEL})")
    p_run.add_argument("--effort", help=f"effort {'|'.join(EFFORTS)} (default: $EVAL_EFFORT, else {DEFAULT_EFFORT})")
    p_run.add_argument("--max-tokens", type=int, default=API_MAX_TOKENS,
                       help=f"api backend: max_tokens, thinking included (default {API_MAX_TOKENS})")
    p_run.add_argument("--claude-bin", help="cli backend: path to the claude executable (default: PATH)")
    p_run.add_argument("--timeout", type=int, default=CLI_TIMEOUT_S,
                       help=f"cli backend: seconds per situation (default {CLI_TIMEOUT_S})")
    p_run.add_argument("--dry-run", action="store_true", help="validate and show the plan; send nothing")
    p_run.set_defaults(func=cmd_run)

    p_score = sub.add_parser("score", parents=[common], help="gate a scored run file and log it in runs/LOG.md")
    p_score.add_argument("runfile")
    p_score.set_defaults(func=cmd_score)
    return parser


def main(argv: list[str] | None = None) -> int:
    _utf8_stdio()
    args = build_parser().parse_args(argv)
    try:
        if getattr(args, "max_tokens", 1) < 1 or getattr(args, "timeout", 1) < 1:
            raise InputError("--max-tokens and --timeout must be positive")
        return args.func(args, make_context(args))
    except InputError as exc:
        say(f"error: {exc}", err=True)
        return 2


if __name__ == "__main__":
    sys.exit(main())
