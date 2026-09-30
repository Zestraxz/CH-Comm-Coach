# Eval — situations, rubric, backends, gate

The eval set is the evidence behind every kernel tag: the same situations, asked the same way, scored by hand
against the same rubric. `run_eval.py` checks the set, asks the coach, and gates your scores.

Your data stays out of this repo. Point the runner at a private data folder with `--data DIR` (or
`COMM_COACH_DATA`); it reads `DIR/knowledge/*.md` (skipping `*.local.md`), `DIR/eval/situations.yaml`, and
writes `DIR/eval/runs/`. Without `--data` it uses `starter/`, whose ten synthetic situations only show the
schema (their run files stay local: `starter/eval/runs/` is gitignored). The kernel defaults to
`kernel/instructions.md`; `--kernel FILE` tries another.

## Quick start

From the folder that holds `framework/` and your data. Call the venv's Python by path instead of activating it
(README, "Python environment"): `.venv/Scripts/python` in Git Bash, `.\.venv\Scripts\python.exe` in PowerShell,
`.venv/bin/python` in WSL or Linux. Shown for Git Bash:

```
python -m venv .venv
.venv/Scripts/python -m pip install -r framework/requirements.txt
.venv/Scripts/python framework/eval/run_eval.py --data . check                  # schema, coverage, leakage
.venv/Scripts/python framework/eval/run_eval.py --data . run --dry-run          # the plan; sends nothing
.venv/Scripts/python framework/eval/run_eval.py --data . run                    # cli backend, your subscription
.venv/Scripts/python framework/eval/run_eval.py --data . run --ids 3 7          # a subset
.venv/Scripts/python framework/eval/run_eval.py --data . score eval/runs/<file>.yaml
```

Windows PowerShell 5.1: one command per line (it has no `&&`), `.\.venv\Scripts\python.exe framework\eval\run_eval.py ...`,
and `$env:NAME = "value"` for variables. From inside `framework/`, drop the `framework/` prefix and `--data .` to use
the starter set.

## Situation schema — `eval/situations.yaml`

| Key | Required | Meaning |
|---|---|---|
| `id` | yes | Unique positive integer. Never reuse an id for a different situation. |
| `prefix` | no | `"L:"` live · `"P:"` prep · `"D:"` draft · `"X:"` debrief · `"R:"` practice · `""` = mode-detection test |
| `situation` | yes | The exact message you would send the coach. |
| `expect` | when `prefix` is `""` | The mode the coach should pick: `live` · `prep` · `draft` · `debrief` · `practice` |
| `tags` | no | Any of `up` `across` `down` `out` `personal` `zh` `no-reply` `push-back` `safety` `channel-switch` `audience` |
| `notes` | no | What you actually said and what happened — helps you score. |

Quote every prefix and any text that contains `: `. Unknown keys and tags are errors, not silently dropped.
Keep it anonymised: codes and roles, no names, no prices or contract terms.

**Never copy the kernel's calibration examples (section 11) into the set.** The kernel carries their answers,
so they measure memory, not coaching. `check` reads section 11 from the kernel at run time and fails on any
situation that repeats or closely paraphrases one of its user lines (30% or more of its word 6-grams appear in
section 11, a similarity of 0.60 or more to a user line, or half of a user line's 6-grams inside it). Use the same
kind of situation with different facts.

## Rubric — 1 to 5 each

- **Outcome** — would this line have moved the situation toward what I needed?
- **Register** — right tone for the channel, the power gap, and everyone who could see it?
- **Sayable** — could I say or send this as-is and sound like me?
- **Length** — as short as it could be, with nothing missing?
- **Truth** — pass / fail: no invented facts; `[confirm: …]` wherever I hadn't supplied something.

Score the first reply only — it's the one you'd have used in the moment. Compare averages across runs, not rows.

## Backends and cost

Subscription first: the default backend runs on your Claude subscription, and the metered API is a
separate, explicit opt-in. Nothing ever falls back from one to the other.

### `cli` — default, no extra cost

One headless Claude Code call per situation: `claude -p --output-format json --model <id> --effort <level>
--system-prompt-file <kernel + knowledge> --tools "" --strict-mcp-config --disable-slash-commands
--setting-sources project,local --no-session-persistence --max-turns 1`, with the situation on stdin.

- **Auth.** Outside a logged-in terminal, run `claude setup-token` and set `CLAUDE_CODE_OAUTH_TOKEN` (copy it with
  the copy button — a wrapped selection embeds line breaks, which the runner refuses). Without a token the call
  uses the terminal's login, and your user config (CLAUDE.md, rules, hooks) may load into the context.
- **Isolation.** The child gets a fresh temp working folder outside your project folders; every `CLAUDE*` and
  `ANTHROPIC*` variable is dropped (an inherited `ANTHROPIC_API_KEY` would make the call metered), except the
  token; with a token, `CLAUDE_CONFIG_DIR` points at an empty folder. Never `--bare` (it ignores OAuth), never
  `--fallback-model` (a silent model swap).
- **Checks.** `is_error` is read before anything else — a login failure still says `subtype: success`. Auth, model
  or flag errors stop the run at once; three failed rows in a row stop it too.
- **Windows.** The runner resolves the real `claude.exe` behind npm's `claude.cmd` shim and never runs the shim.
  `--claude-bin PATH` overrides; `--timeout` sets the seconds per situation (default 300).

### `api` — metered, opt-in only

`run --backend api --metered`, with `ANTHROPIC_API_KEY` set and `pip install "anthropic>=0.96,<2"`. Before the
first request it prints the cost statement: the capability the subscription path lacks (explicit `max_tokens`
and effort per request, exact per-request usage), the API and endpoint, that it costs money, an estimate, and
the lowest-cost alternative. Without `--metered` it stops there. `--dry-run` prints the statement and sends
nothing.

The request: kernel, then the knowledge block with a cache breakpoint, as the system prompt; the situation as the
user message; `max_tokens` 16000 (thinking counts against it); `output_config.effort` explicit; no `thinking`
field, so the model's default adaptive thinking applies; no server-side fallbacks. The estimate is inference;
the run file records real usage, and `run` prints the cost at list price computed from it.

### Model and effort

`--model` (else `EVAL_MODEL`, else `claude-sonnet-5-5`) and `--effort` (else `EVAL_EFFORT`, else `high`). Use
the model your Project uses, as a full ID. Both are recorded, with the model actually served.

### Option B — in the Project, by hand (quarterly)

New chat, paste the situation with its prefix, score the first reply. This is the only way to see memory, tools
and the Project's own model settings.

## Run files

`run` writes `eval/runs/<date>_<time>_v<kernel>_<backend>.yaml` before the first call and rewrites it after
every situation, so a crash or Ctrl-C keeps the completed rows (`status` says how it ended). Files are UTF-8
with LF line endings; replies are readable blocks.

Each row holds the reply, `auto_flags`, `do_not_score`, empty `scores` and `truth`, and a `meta` block: backend,
model requested and served, `stop_reason`, `is_error`, usage, duration, and the kernel and knowledge hashes. The
top of the file records the backend and its version, effort, the kernel version and hash, each knowledge file's
hash, the situations hash, and the framework's git commit when there is one.

`auto_flags` are heuristics — they raise questions, you decide:

- LIVE (prefix `L:` or `expect: live`): more than 80 words (Chinese counted as one word per two characters), or no
  `Say:` (spoken) / `Send:` (written) line.
- PREP over 250 words; a first line that announces the mode.
- Numbers in the reply that are not in the situation or the knowledge files. List numbering and anything inside
  `[confirm: …]` are ignored; `8%` and `8 percent`, `1,000` and `1000`, `3pm` and `3:00` count as the same.
- Served model differs from the requested one; a context canary when far more input tokens were served than
  kernel + knowledge + situation explain (cli: user config leaking in).

`do_not_score` is set for an error, an empty reply, a truncated reply (`stop_reason: max_tokens`) or a refusal.
Re-run those ids; `score` refuses them.

## Scoring and the gate

Fill `scores` (integers 1–5 for outcome, register, sayable, length) and `truth` (`pass` or `fail`) in every row,
then run `score`. It exits:

- **2 — invalid:** any row unscored, a score that isn't an integer 1–5, truth other than pass/fail, a row with an
  error or `do_not_score`, duplicate ids, or a malformed file. Nothing is logged.
- **1 — below target:** overall average under 4.00 or any truth fail. Or **not taggable**: the scores pass, but the
  run doesn't cover every current situation with unchanged text, the set is under 20 or has coverage gaps, a
  situation matches a calibration example, or the kernel can't be tied to the run — it changed since the run,
  the file has no kernel hash or `Version X.Y`, or some rows came from a different kernel.
- **0 — pass:** it prints the `git tag` command. When HEAD of the framework repo already holds the evaluated
  kernel, the command names that commit; when it doesn't yet, it says to commit `kernel/` first and tag that
  commit (a bare `git tag` would label the old kernel). Tag in the framework repo; the message carries the average.

The average is exact (fractions) and the threshold is compared on the rounded value it prints, so `4.00` passes.
Every valid scoring writes one row per run file to `eval/runs/LOG.md` (header added if missing; re-scoring a file
replaces its row); the Gate column says PASS, BELOW or PARTIAL.

## Coverage minimums

`check` counts these; a set is taggable only when every row is met.

| Check | Minimum | What counts |
|---|---|---|
| `total` | 20 | situations in the set |
| `mode:live` | 5 | LIVE: prefix `L:`, or `expect: live` |
| `mode:prep` | 5 | PREP |
| `mode:draft` | 5 | DRAFT |
| `mode:debrief` | 3 | DEBRIEF |
| `mode:practice` | 1 | PRACTICE |
| `unprefixed` | 2 | mode-detection tests: prefix `""` with `expect` |
| `tag:up` | 1 | upward (leadership) |
| `tag:across` | 1 | across (peers, other teams) |
| `tag:out` | 1 | outward (suppliers, customers, partners) |
| `tag:personal` | 2 | personal life |
| `zh` | 3 | Mandarin or bilingual: tag `zh`, or Chinese text in the situation |
| `tag:no-reply\|channel-switch` | 2 | the right answer is "don't reply now" or "switch to a call" |
| `tag:push-back` | 2 | the coach should push back on the goal, not just the wording |
| `tag:safety` | 2 | a safety, legal or compliance line |
| `tag:audience` | 1 | others can see it: group chat, cc, meeting |

`check` exits 0 when schema, leakage and coverage are clean, 1 for coverage gaps only, 2 for schema errors or
leakage. `check --schema-only` skips coverage. `run` exits 0 when every row can be scored, 1 when some cannot or
the run stopped early, 2 when nothing was sent (bad input, unknown `--ids`, leakage, a malformed token, a
missing `--metered` or API key, `claude` not found or failing `--version`), 130 on Ctrl-C (completed rows kept).

## What this eval does not measure

- **Anything past the first reply.** One user message per situation: LIVE thread state, `status` mid-thread and
  PRACTICE beyond the opening exchange are untested here and in option B.
- **Memory and tools.** `debrief` saving, `patterns`, saved counterparts, the compose card and "zero tool calls in
  LIVE" exist only in the Project: option B.
- **Equivalence** between the cli backend, the api backend and the real Project (system-prompt wrapping, effort,
  model settings). Not measured until the same set is compared across them.
- **cli isolation** — that `--tools ""` locks tools out, that `--system-prompt-file` replaces Claude Code's default
  prompt, and that nothing from your user config loads. Documented behaviour, not measured until your first
  token run; the context canary is the check.
- **Spelled-out numbers** ("ten percent", "三周") — the number flag sees digits only. Truth stays a manual score.
- **Cost.** The api estimate is inference; real usage is in each run file.
