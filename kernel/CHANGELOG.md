# Changelog — kernel

One entry per version. Bump the `Version X.Y` line in `instructions.md` in the same commit — the pre-commit hook checks that the entry exists, and that a changed kernel carries a new version. Tag after a passing eval: `git tag -a v2.0 -m "eval 4.1 avg, 0 truth fails"`.

Entry format: what changed · why · eval delta (overall before → after, truth fails).

## 2.3 — 2026-10-02
- Changed:
  - §4: the placeholder paragraph is now a three-part **Truth check** that applies to every scripted line, If they push included. A caveat in Why doesn't fix the line.
    1. A fact you weren't given (relative times too) → placeholder.
    2. An event you weren't told about (approved, done, tested, sent, agreed, or why it happened) → ask or check, never assert or presuppose, even with placeholders. The counterpart's claims stay theirs.
    3. A decision or limit the user hasn't stated (accept, refuse, concede, an inability, a rule, an approval step) → hold it open.
  - §7: new entries "Asked something you don't know" and "Ultimatum or deadline". The traps that quoted phrases to avoid (§7) and the corporate-filler examples (§8) are now described, not quoted, because quoting a banned line primes it.
  - §10 item 4 points at the §4 truth check.
  - §11: new LIVE examples "ultimatum" (the decision held open in every line) and "unconfirmed" (check, don't claim). The leadership example was removed: it nearly copied a starter eval item.
- Why: in the real Project, 2.2 failed truth in 2 of 3 tests. It refused the slot ultimatum for the user, and it asserted "[confirm: who] signed off" when nobody had said anything was approved.
- Eval: proxy only. Coach replies came from Explore-type subagents on Fable 5.1 at medium effort. That agent type does not load the Brain's evidence rules, which had made earlier proxy runs look more truthful than the real Project. 6 truth-trap situations ×2, strict judge.
  - Calibration: 2.2 reproduced the real Project's failures, scoring truth 4/12.
  - Single-angle drafts: examples 7/12, playbook rules 6/12, pre-send check 5/12.
  - Merged 2.3: truth 9/12, format 12/12, quality 4.0/5.
  - Remaining failures: conceding the counterpart's frame as fact (addressed by the "claims stay theirs" sentence, not re-tested), an implied concession, and presuming who holds a record.
  - Not measured: the real eval set. Don't tag until it passes.

## 2.2 — 2026-10-01
- Changed:
  - LIVE format: two lines by default (Say/Send + Why). A third line only when it earns it: "If they push" holds the decision open, and "Don't" is a few words with no quote. Target ≤60 words, ceiling 80, labels count; in Chinese ≤120 characters. Why carries any assumption as a short tag ("Assumes: boss, not peer.").
  - Truth: placeholders now cover relative times ("today", "5 minutes"). A placeholder fills a detail, never an event or a conclusion: a line doesn't claim that something was approved, reviewed, agreed or sent, or why it happened, unless the user said so. New rule: never decide for the user (accept, refuse or give up); hold the decision open.
  - §10 gate item 5 checks the LIVE line count and the 60-word target.
  - §7 price/terms: the literal "I have to explain it upstairs" line is gone. Naming the phrase, even inside a restriction, made the coach use it: the real Project produced "explain it properly upstairs" on 2.2-pre with `authority-limits.md` blank. The trap now reads "citing an approval you don't need".
  - §11: three LIVE examples replaced (customer trade, director 1:1, team-chat credit) and the personal example cut to two lines. The old supplier, leadership and group-chat examples were close to real test situations, and the model was copying them word for word.
- Why: a smoke test in the Project on 2.1 ran 87–95 words at every effort (Max, Medium, Low), over the 80-word limit, and at Low it invented an approval ("can't take 'final' upstairs").
- Eval: proxy test only, not the eval set. Coach replies came from Fable 5.1 at medium effort through Claude Code subagents given the kernel and the knowledge files; a stricter judge scored them.
  - 2.1 baseline (5 situations): 2/5 over 80 words (max 98); Chinese reply 162 characters; truth 2/5.
  - 2.2 (8 situations ×2, 3 of them held out): 0/16 over 80 (mean 61, max 74); Chinese replies 67–107 characters; format 16/16; truth 10/16, judged stricter. The "who approved this?" case went from a claimed approval to no approval claim.
  - Truth failures that remain: a push line that concedes the decision, and a single known cause stretched to explain the whole delay.
  - Not measured: the real eval set. Don't tag until the eval passes.
- Real Project check after deploying 2.2 (Fable 5.1 medium, n = 3): all 3 replies ≤80 words (about 50–70) in 13.6–14.4 s, against 93 words on 2.1 at the same setting. Format 3/3. Truth 1/3:
  - A push line refused for the user ("the answer is no … tomorrow").
  - An unknown approval was still asserted with placeholders, and only hedged in Why.
  - The truth rules hold less well at medium effort than in the proxy. Next: a calibration example that shows an unknown event being asked about, not claimed; prove it with the eval.

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
