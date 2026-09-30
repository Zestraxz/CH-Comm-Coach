# Changelog — kernel

One entry per version. Bump the `Version X.Y` line in `instructions.md` in the same commit — the pre-commit hook checks that the entry exists, and that a changed kernel carries a new version. Tag after a passing eval: `git tag -a v2.0 -m "eval 4.1 avg, 0 truth fails"`.

Entry format: what changed · why · eval delta (overall before → after, truth fails).

## 2.1 — 2026-09-30
- Changed:
  - §2 is generic. Who the user is (role, work, frequent situations, channels, languages, vocabulary) now comes from a new knowledge file, `profile.md`, listed first. The kernel carries no person, employer, country or industry. The §11 examples are generic too (a partner, a rollout, a team chat, a peer's slides).
  - Truth: the §11 leadership example no longer invents a cause size or a deadline; the supplier example no longer claims an approval or assumes the supplier's reason; every placeholder is `[confirm: …]`. "I have to explain it upstairs" only when `authority-limits.md` says so, and §6 counts an invented approval as bluffing.
  - LIVE: `Send:` (written) alongside `Say:` (spoken); labels stay in English. "Zero tool calls before the line" scoped: no memory reads, searches or cards; a requested save comes after the line; knowledge files already in context are not lookups. One priority order for the single trailing ask (question → exact words → offer). Assumptions go in Why, never a preamble. Thread state tells the counterpart's line apart from commands and the user's own added context.
  - Routing: pasted text → LIVE (chat), DRAFT (email), or "Send it" / the fix (the user's own draft). `debrief` is an alias of X:. `patterns` draws on this chat, saved debriefs and past chats. The one-question opener fires only on a greeting with no situation.
  - Memory (§12): save only in DEBRIEF or on "remember"; the counterpart's code or role, never a real name; one habit per save; no figures. Written as what the coach does, not what the platform guarantees. A real name not in `counterparts.md`: proceed by role or ask once which entry it is, never save it.
  - Smaller: PREP is ≤250 words in both places; "Stop" ends a roleplay; the channel switch adds a written confirmation after the call; a default language for scripted lines (the counterpart's, unless the user, `counterparts.md` or `profile.md` says otherwise, in that order); the bilingual row is consistent; §9 overrides every mode, format and word limit and names gifts, kickbacks and conflicts of interest; §7 is one line per situation with four rows that repeated other sections dropped; the §3 mode table is a list; the §6 "Written" bullet is folded into DRAFT.
- Why: the adversarial review of 2.0 (kernel findings plus two the verifier added), and the kernel now ships in a public repo, so user-specific detail moves to `profile.md` in the user's private knowledge.
- Size: 3,019 → 2,977 words (hook count); ceiling 3,200.
- Eval: not run — first eval pending.

## 2.0 — 2026-09-27
- Changed: zero tool calls in LIVE; `[confirm: …]` placeholder policy (delivery vs substance); prefixes L/P/D/X/R and `status` / `debrief` / `patterns`; audience-over-addressee and channel-switch rules; hooks to the five knowledge files; memory policy (no prices, personal only on "remember"); quality gate gains a truth check.
- Why: critic review — latency, cold start, voice mismatch, fabrication, mode misfires, no learning loop, confidentiality, dyadic bias.
- Eval: baseline not yet run.

## 1.0 — 2026-09-27
- Initial kernel: five modes, intake rules, output formats, thinking principles, situation playbook, stance, red lines, quality gate, calibration examples.
