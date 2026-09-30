# CH-Comm-Coach

A framework for a real-time communication coach that runs as a [claude.ai](https://claude.ai) Project. You tell the
coach the situation (live, prep, draft, debrief, practice) and it gives you exact wording, not general advice.

The framework has four parts:

| Part | What it is |
|---|---|
| **Kernel** | `kernel/instructions.md`: the coach's rules, about 3,000 words. You paste it into the Project's custom instructions by hand. |
| **Knowledge** | Your own markdown files (profile, counterparts, authority limits, workplace norms, voice samples, bilingual templates). Blank templates are in `starter/knowledge/`. The Project reads your filled copies. |
| **Eval** | `eval/run_eval.py` runs a set of situations through the kernel plus your knowledge, you score the replies by hand, and `score` decides whether a kernel change is good enough to tag. |
| **Hooks** | Git hooks that keep names, money figures, contact details and filled knowledge out of a public repo. |

This repository is the **public** half. It holds only generic, reusable material. Everything about you, your
employer and the people you talk to lives in a second, **private** repository that you create (next section).

Jump to: [the two-repo model](#the-two-repo-model) · [first-time setup](#first-time-setup-safe-by-order) ·
[guard hooks](#guard-hooks) · [the loop](#the-loop) · [the claude.ai side](#the-claudeai-side) · [rules](#rules) ·
[known limits](#known-limits)

## The two-repo model

```
 PUBLIC   this repo (the framework)              PRIVATE   your workspace repo
 ----------------------------------              ------------------------------------------
 kernel/   eval runner   hooks                   knowledge/            your filled files
 starter/  blank templates                       eval/situations.yaml  your situations
 README   LICENSE                                eval/runs/            your scored runs
          |                                      notes, session log, anything personal
          | cloned into ./framework                         |
          | (the workspace gitignores it)                   | GitHub connector: knowledge/ only
          v                                                 v
   supplies the hooks and the eval runner         claude.ai Project (private, never shared)
   that both repos use                            + the kernel, pasted by hand into custom instructions

 LOCAL ONLY (in neither repo):  private/  blocklists, code-to-name key, raw messages
```

- **Why two repos.** Whatever reaches a public git history cannot be recalled: forks, caches and clones keep it.
  So the material that identifies people never shares a repository with the material you publish. Three
  independent barriers back that up: `.gitignore` here ignores `/knowledge/`; the public hook allows only the
  framework layout; the public pre-push hook re-scans everything before it leaves your machine.
- **The Project reads knowledge from your private repo, never from this one.** This repo has no filled knowledge,
  and a public clone must never get any.
- **Nested layout (recommended).** Your workspace is a folder that holds your private repo and, inside it,
  a clone of this repo as `framework/`. The workspace `.gitignore` excludes `framework/`, so the two repos never
  see each other's files. A sibling layout also works: pass `--data <workspace>` to the eval runner (or set
  `COMM_COACH_DATA`) and `--blocklist-dir <dir>` to the hook installer.
- **Two hook modes, one set of scripts.** `public` (this repo) is the strictest. `private` (your workspace) checks the
  whole index for third-party names, checks `knowledge/` and `eval/` for money, email and phone patterns, and refuses to
  push to a remote that answers as public. Details are under "Guard hooks".

## Layout of this repo

```
README.md                      this file
LICENSE                        MIT
.gitignore  .gitattributes     ignore rules; LF line endings on every checkout
requirements.txt               Python dependencies for the eval runner
kernel/instructions.md         the rules: paste into the Project's custom instructions (the hook allows 3,200 words)
kernel/CHANGELOG.md            one entry per kernel version, with the eval result
starter/knowledge/*.md         blank templates for your knowledge files
starter/eval/situations.yaml   synthetic example situations
eval/run_eval.py               run | score | check
eval/eval-set.md               rubric, coverage minimums, how to run
eval/test_run_eval.py          unit tests for the runner
scripts/install-hooks.sh       installs the hooks in public or private mode
scripts/hooks/                 pre-commit, pre-push, lib.sh
scripts/test-hooks.sh          regression suite for the hooks
docs/                          optional: further public docs (allowed by the hook; none yet)
```

Your private workspace, once set up:

```
my-coach/                      PRIVATE repo (the folder name is yours)
  framework/                   a clone of this repo (gitignored by the workspace)
  knowledge/                   your filled files, synced to the Project
  eval/situations.yaml         your situations
  eval/runs/                   scored runs (tracked: they are the evidence behind each kernel tag)
  private/                     local only: blocklist.txt, blocklist-public.txt, key.md, raw/
  .gitignore  .gitattributes
```

## Requirements

- Git 2.28 or newer for the setup below (`git init -b` needs it; the hooks alone run on 2.25). Tested with Git for
  Windows 2.52 and the Git shipped with Ubuntu 22.04 on WSL (2.34).
- bash 4.4 or newer, because the hooks are bash scripts: Git Bash (installed with Git for Windows), WSL or Linux.
  macOS is untested, and its default bash is too old for the hooks (install a newer one).
- `curl`, for the private-mode pre-push check.
- Python 3.10 or newer, for the eval runner. Tested here with 3.14 on Windows. On Windows, install Python from python.org;
  if `python` opens the Microsoft Store, turn off the "App execution alias" for it or use `py -3`.
- The Claude Code CLI (`claude`) signed in to your Claude plan, for the default eval backend.
- A Claude plan that has the GitHub integration, and a GitHub account.

Checked on 2026-09-30 on Windows 10 with Git Bash, Windows PowerShell 5.1 and WSL Ubuntu 22.04.

## First-time setup: safe by order

The order below is the point. Each step makes the next one safe. Do not reorder it, and do not skip a step.

1. **Blocklists first.** The hooks match your real names against them. Create them before anything is staged.
2. **Hooks installed and proven to fire** before the first commit.
3. **Stage by name.** Never `git add .`, `git add -A` or `git add --all`. A blanket add stages whatever is lying around:
   an archive, an environment file, a scratch note. Never use `git commit --no-verify`.
4. **Create the remote as private, and check that it answers as private, before the first push.**
5. **Public commits carry a GitHub noreply address**, set repo-locally, never your personal one.
6. **Fill in real content last**, after steps 1 to 4 have been shown to work.

Where to work: Git Bash on the Windows drive, PowerShell with Git for Windows, or a folder inside the WSL Linux
filesystem (`~/...`). Avoid working under `/mnt/c` or `/mnt/d` in WSL: git is slow there and file modes are not kept.
Do not run these hooks through GitHub Desktop (see Known limits).

Placeholders: `<username>` is your GitHub username; `<id>` is the number in your GitHub noreply address
(GitHub, Settings, Emails, "Keep my email addresses private"); `<private-repo>` is the name you give your private repo.

### Git Bash (Windows), WSL and Linux

These commands are identical in Git Bash, in WSL and on Linux. In WSL, first `cd ~` so the workspace lives in the Linux
filesystem, not under `/mnt/`. Run the three blocks one after another, and read the result of each before you go on.

**Part 1: workspace, blocklists, repo and hooks.**

```bash
# 1. A workspace folder, with the framework cloned inside it
mkdir my-coach
cd my-coach
git clone https://github.com/Zestraxz/CH-Comm-Coach.git framework

# 2. Blocklists FIRST (UTF-8, one term per line); see "The blocklists" below for what goes in each
mkdir private
printf '# Real names of third parties, one per line.\n' > private/blocklist.txt
printf '# Terms that must never reach the public repo: your name, your employer.\n' > private/blocklist-public.txt
printf 'replace-with-a-real-name\n' >> private/blocklist.txt   # repeat with your real terms, then delete this line

# 3. Starter files, ignore rules, line-ending policy
mkdir -p knowledge eval/runs
cp framework/starter/knowledge/*.md knowledge/
cp framework/starter/eval/situations.yaml eval/situations.yaml
touch eval/runs/.gitkeep
cp framework/.gitattributes .gitattributes
printf '%s\n' '/framework/' '/private/*' '!/private/README.md' '.resource/' '*.local.md' '.env' '.env.*' \
  '.venv/' 'venv/' 'env/' '__pycache__/' '*.pyc' '*.zip' '.DS_Store' 'Thumbs.db' > .gitignore

# 4. Repository, identity, hooks
git init -b main
GH_ID='<id>'
GH_USER='<username>'
git config user.name "$GH_USER"
bash framework/scripts/install-hooks.sh private --email "${GH_ID}+${GH_USER}@users.noreply.github.com"
git config core.hooksPath        # must print: framework/scripts/hooks
git config commcoach.mode        # must print: private
```

If either `git config` line printed nothing, the installer failed: read its message and fix it before you continue.

**Part 2: prove the guard fires.** This commit must be refused (a non-zero exit and a message naming the check).

```bash
printf 'x\n' > canary.local.md
git add -f -- canary.local.md
git commit -m "canary"
git reset -q -- canary.local.md
rm canary.local.md
```

If that commit **succeeded**, the guard is not active. It was your first commit, so undo it with `git update-ref -d HEAD`,
then fix the hooks (`git config core.hooksPath`, executable bits, line endings) before you go on.

**Part 3: stage by name, commit, private remote.**

```bash
git add -- .gitattributes .gitignore knowledge eval
git commit -m "chore: workspace scaffold"

# Create an EMPTY repo on GitHub with visibility Private (github.com/new), then:
git remote add origin https://github.com/<username>/<private-repo>.git
curl -s -o /dev/null -w '%{http_code}\n' https://api.github.com/repos/<username>/<private-repo>
```

The `curl` line must print `404` (a private repo answers 404 to an unauthenticated request). `200` means the repo is
PUBLIC: do not push. Anything else (`000` means curl could not connect) is not an answer: do not push until it prints
`404`. Only then:

```bash
git push -u origin main
```

### Windows PowerShell 5.1

Windows PowerShell 5.1 has no `&&`, so every command is on its own line. Never write a blocklist with `>` or `>>`:
in PowerShell 5.1 they write UTF-16. Use .NET with an absolute path (`"$PWD\..."`) as shown, never a relative path.
`curl` is an alias for another command in PowerShell; call `curl.exe`. `bash` may resolve to the WSL launcher, so call
Git Bash by its path (adjust it if Git is installed elsewhere). Run the three parts one after another.

**Part 1: workspace, blocklists, repo and hooks.**

```powershell
# 1. A workspace folder, with the framework cloned inside it
New-Item -ItemType Directory my-coach | Out-Null
Set-Location my-coach
git clone https://github.com/Zestraxz/CH-Comm-Coach.git framework

# 2. Blocklists FIRST
$utf8 = New-Object Text.UTF8Encoding $false
New-Item -ItemType Directory private | Out-Null
[IO.File]::WriteAllText("$PWD\private\blocklist.txt", "# Real names of third parties, one per line.`n", $utf8)
[IO.File]::WriteAllText("$PWD\private\blocklist-public.txt", "# Terms that must never reach the public repo: your name, your employer.`n", $utf8)
[IO.File]::AppendAllText("$PWD\private\blocklist.txt", "replace-with-a-real-name`n", $utf8)   # repeat with your real terms, then delete this line

# 3. Starter files, ignore rules, line-ending policy
New-Item -ItemType Directory knowledge, eval, eval\runs | Out-Null
Copy-Item framework\starter\knowledge\*.md knowledge\
Copy-Item framework\starter\eval\situations.yaml eval\situations.yaml
New-Item -ItemType File eval\runs\.gitkeep | Out-Null
Copy-Item framework\.gitattributes .gitattributes
$ignore = '/framework/', '/private/*', '!/private/README.md', '.resource/', '*.local.md', '.env', '.env.*', '.venv/', 'venv/', 'env/', '__pycache__/', '*.pyc', '*.zip', '.DS_Store', 'Thumbs.db'
[IO.File]::WriteAllText("$PWD\.gitignore", (($ignore -join "`n") + "`n"), $utf8)

# 4. Repository, identity, hooks
git init -b main
$ghId = '<id>'
$ghUser = '<username>'
git config user.name $ghUser
& "$env:ProgramFiles\Git\bin\bash.exe" framework/scripts/install-hooks.sh private --email "${ghId}+${ghUser}@users.noreply.github.com"
git config core.hooksPath        # must print: framework/scripts/hooks
git config commcoach.mode        # must print: private
```

If either `git config` line printed nothing, the installer failed: read its message and fix it before you continue.

**Part 2: prove the guard fires.** This commit must be refused (a non-zero exit and a message naming the check).

```powershell
[IO.File]::WriteAllText("$PWD\canary.local.md", "x`n", $utf8)
git add -f -- canary.local.md
git commit -m "canary"
git reset -q -- canary.local.md
Remove-Item canary.local.md
```

If that commit **succeeded**, the guard is not active. It was your first commit, so undo it with `git update-ref -d HEAD`,
then fix the hooks (`git config core.hooksPath`, executable bits, line endings) before you go on.

**Part 3: stage by name, commit, private remote.**

```powershell
git add -- .gitattributes .gitignore knowledge eval
git commit -m "chore: workspace scaffold"

# Create an EMPTY repo on GitHub with visibility Private (github.com/new), then:
git remote add origin https://github.com/<username>/<private-repo>.git
curl.exe -s -o NUL -w "%{http_code}`n" https://api.github.com/repos/<username>/<private-repo>
```

The `curl.exe` line must print `404` (a private repo answers 404 to an unauthenticated request). `200` means the repo is
PUBLIC: do not push. Anything else (`000` means curl could not connect) is not an answer: do not push until it prints
`404`. Only then:

```powershell
git push -u origin main
```

Whichever shell you used, verify what you ended up with. `git ls-files` must list only the files you staged by name,
nothing under `private/` and nothing under `framework/`:

```bash
git ls-files
git status --short
```

### The blocklists

Two plain-text lists live in `private/` (never committed). Both take one term per line, ignore `#` comments and
blank lines, and match case-insensitively as literal text.

| File | Applied in | Put in it |
|---|---|---|
| `private/blocklist.txt` | both repos | Real names of third parties: colleagues, vendors, customers, project codenames. Add each one the moment you give it a code in `knowledge/`. |
| `private/blocklist-public.txt` | the public repo only | Terms that are fine in your private workspace but must never be published: your own name, your employer, internal terms that identify the industry or site. |

Use full, distinctive terms and their spelling variants. A two- or three-letter term matches inside ordinary words.
Write the files as UTF-8 with LF line endings. The hook also copes with a BOM, UTF-16 with a BOM, CRLF and stray
whitespace, and refuses a file that mixes UTF-8 and UTF-16 (see the PowerShell note above).

The hooks match terms against file contents and file names, and they fail closed: a missing folder or missing
`blocklist.txt` blocks every commit, and the public hook also blocks when `blocklist-public.txt` is missing or the lists
hold no terms. So a fresh clone on another machine cannot commit until `private/` is restored there. That is deliberate.
Back `private/` up somewhere that is not GitHub.

### Python environment

Do not activate the virtual environment. Call its Python by path. A venv made by Windows Python has `.venv/Scripts`, not
`.venv/bin`, so the usual bash activation line fails in Git Bash, and PowerShell's default execution policy often refuses
`Activate.ps1`.

```bash
# Git Bash, from the workspace root
python -m venv .venv
.venv/Scripts/python -m pip install -r framework/requirements.txt
.venv/Scripts/python framework/eval/run_eval.py --data . check --schema-only
```

```powershell
# Windows PowerShell 5.1, from the workspace root
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r framework\requirements.txt
.\.venv\Scripts\python.exe framework\eval\run_eval.py --data . check --schema-only
```

```bash
# WSL / Linux, from the workspace root
sudo apt install python3-venv
python3 -m venv .venv
.venv/bin/python -m pip install -r framework/requirements.txt
.venv/bin/python framework/eval/run_eval.py --data . check --schema-only
```

`requirements.txt` installs PyYAML only. The default `cli` backend needs nothing else; the opt-in metered backend needs the
`anthropic` SDK (see the comment in that file). The last line of each block should report the schema and the leakage check
as OK and exit 0. `--schema-only` skips the coverage minimums, which a fresh workspace cannot meet yet.

The rest of this README writes `python` for whichever of those you use.

### Fill in your knowledge, build the eval set

1. Fill `knowledge/profile.md` first (who you are and what your world looks like: the kernel is generic on purpose),
   then `counterparts.md` and `voice-samples.md`.
2. Use codes for people (`SUP-A`, `LEAD-1`), bands instead of figures, roles instead of names, and keep the code-to-name
   key in `private/key.md`. No prices, margins or contract terms anywhere: the hook blocks money patterns.
3. Replace the synthetic examples in `eval/situations.yaml` with your own anonymised situations, up to at least 20.
   Coverage minimums and the situation format are in `eval/eval-set.md`. Never copy the kernel's own calibration examples in.
4. Validate: `python framework/eval/run_eval.py --data . check`. It checks the situations file against the schema, the
   coverage minimums and overlap with the kernel's own examples, and it calls no model. It exits 0 when clean, 1 when only
   coverage gaps remain, and 2 for schema errors or a situation copied from the kernel's examples.

### Working on the framework repo itself

For maintainers and contributors. `core.hooksPath` is per-clone config, so the hooks are not installed in a fresh clone:
install them before your first commit. The public hook needs both blocklists. Either the clone sits inside your workspace
(it finds `../private`), or you create a `private/` folder inside the clone (gitignored), or you point `--blocklist-dir`
at one. Put your own name, your employer and any third-party names in them. The commands in this section are bash: run
them in Git Bash, WSL or Linux.

**In a clone of this repo:**

```bash
cd framework                       # or your clone
GH_ID='<id>'
GH_USER='<username>'
git config user.name "$GH_USER"    # your username, not your real name: commit authors are public
bash scripts/install-hooks.sh public --email "${GH_ID}+${GH_USER}@users.noreply.github.com"
git config core.hooksPath          # must print: scripts/hooks
git config commcoach.mode          # must print: public
git config user.email              # must print: your noreply address
bash scripts/test-hooks.sh         # the hook regression suite
git ls-files -s scripts            # pre-commit, pre-push and every scripts/*.sh must show mode 100755
```

If one of them shows `100644`, run `git update-index --chmod=+x -- scripts/hooks/pre-commit scripts/hooks/pre-push scripts/*.sh`.
Without the executable bit, Linux git skips a hook and prints only a hint, and the public pre-commit blocks the commit.

**A brand-new public repo: first commit, then first push.** In order:

```bash
cd framework
git init -b main
git remote add origin https://github.com/<username>/<framework-repo>.git
git remote -v                      # must be the repo you intend to publish to
GH_ID='<id>'
GH_USER='<username>'
git config user.name "$GH_USER"
bash scripts/install-hooks.sh public --email "${GH_ID}+${GH_USER}@users.noreply.github.com"
git config core.hooksPath          # must print: scripts/hooks

# prove the guard fires: this commit must be REFUSED
printf 'x\n' > canary.local.md
git add -f -- canary.local.md
git commit -m "canary"
git reset -q -- canary.local.md
rm canary.local.md
```

If the canary commit succeeded, undo it with `git update-ref -d HEAD` and fix the hooks before you go on. Then:

```bash
git add -- README.md LICENSE .gitignore .gitattributes requirements.txt kernel eval scripts starter
git update-index --chmod=+x -- scripts/hooks/pre-commit scripts/hooks/pre-push scripts/*.sh
git ls-files -s scripts            # pre-commit, pre-push and every scripts/*.sh must show mode 100755
git ls-files                       # review: only framework files
bash scripts/test-hooks.sh
git commit -m "chore: initial framework"
```

Before the **first** push of a public repo, also scan the whole history by hand. The pre-push hook checks every commit it
pushes (content, messages, author/committer/tagger names and emails); this scan also reads commits already on the
remote and every diff, message and author line. It prints a count and no matched
text; it must print `0`:

```bash
PRIV=../private                    # "private" if the folder is inside the clone
terms=$(mktemp)
for f in "$PRIV/blocklist.txt" "$PRIV/blocklist-public.txt"; do
  case $(od -An -tx1 -N2 "$f" | tr -d ' \n') in fffe|feff) iconv -f UTF-16 -t UTF-8 "$f" ;; *) cat "$f" ;; esac
  echo
done | tr -d '\r' | sed -e 's/^\xEF\xBB\xBF//' -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' | grep -avE '^(#|$)' > "$terms"
test -s "$terms" && git log -p --all | grep -a -i -c -F -f "$terms"
rm -f "$terms"
```

It reads the lists as the hook does (UTF-8, or UTF-16 with a byte-order mark). If it prints anything but `0`, or prints
nothing at all (an empty terms file), do not push. Otherwise:

```bash
git push -u origin main
```

The public pre-push hook checks the author and committer email, the author and committer name, and the content of every
pushed commit.

## Guard hooks

One set of scripts guards both repos, in two modes. Run the installer from inside the repo you want to protect, with
`bash` (not `./`):

```
bash scripts/install-hooks.sh public|private [--blocklist-dir DIR] [--email ADDR]
```

It writes repo-local git config only: `core.hooksPath` (relative to the repo's top-level folder: `scripts/hooks` in this
repo, `framework/scripts/hooks` in a workspace), `commcoach.mode`, `commcoach.blocklistDir`, and with `--email` a
repo-local `user.email` (in public mode it must match `commcoach.publicEmail`). Inside this repo it also marks the hooks
executable in the index. It warns when a blocklist file is missing, and re-running it is safe. Check the result with
`git config core.hooksPath`. The hooks need bash 4.4 or newer and git 2.25 or newer; the private pre-push also needs
`curl`. On Windows the repo and the hook scripts must be on the same drive: `core.hooksPath` is stored relative to the
repo, and the installer refuses a cross-drive path. Every check fails closed: a check that cannot run blocks.

| Setting | Meaning |
|---|---|
| `git config commcoach.mode` | `public` or `private`. Unset means `public`, the strictest. |
| `git config commcoach.blocklistDir` | Folder holding the blocklists: absolute, or relative to the repo's top-level folder. Unset: `<top-level>/private` if it exists, else `<top-level>/../private`. |
| `git config commcoach.publicEmail` | Pattern that author, committer and tagger email must match in public mode. Default `*@users.noreply.github.com`. |
| `ALLOW_NO_BLOCKLIST=1` | One-shot: lets a commit through when the blocklist folder or `blocklist.txt` is missing or unreadable (an encoding error still blocks). Public mode still needs `blocklist-public.txt` with at least one term. Do not set it in a shell profile. |
| `COMMCOACH_SKIP_VISIBILITY=1` | One-shot: lets a push through when the private pre-push hook cannot get a definite answer from GitHub. |
| `MAX_WORDS=<integer>` | Kernel word limit for the pre-commit check. Default 3200. A non-integer value blocks the commit. |
| `COMMCOACH_CURL`, `COMMCOACH_TRACE`, `COMMCOACH_MAX_SHOW` | Test and debug only: a curl replacement for the visibility check (a stub that answers 404 has the same effect as the skip flag, so never set it outside `test-hooks.sh`), timing trace, and how many problems to list. |

**Pre-commit** scans the whole staged index with `git grep`, not only the diff, so a renamed, moved or type-changed file
cannot slip through. It reports the file, the line and the check, and never prints the matched text.

| Check | Public mode | Private mode |
|---|---|---|
| Blocklist terms | every staged text file, and every staged file name | the same |
| Money amounts, email addresses, phone numbers | every staged text file except code (`*.sh`, `*.py`, `scripts/hooks/*`) | `knowledge/` and `eval/` only |
| Binary files | any staged binary is blocked | blocked under `knowledge/` and `eval/`, and anywhere in a file named as text (`.md`, `.txt`, `.yaml`, `.json`, `.csv` and similar), since UTF-16 or binary content cannot be scanned; other binaries are not checked |
| Path guard | blocks `private/` (except `private/README.md`), `.resource/`, `*.local.md`; allows only `README.md`, `LICENSE`, `.gitignore`, `.gitattributes`, `requirements.txt`, `kernel/**`, `starter/**`, `eval/*.py`, `eval/*.md`, `scripts/**`, `docs/**` | blocks `private/` (except `private/README.md`), `.resource/`, `*.local.md` |
| Symlinks, submodules | both blocked (a link's target cannot be scanned) | symlinks blocked |
| Executable bit | the hooks and `scripts/*.sh` must be mode 100755 in the index | not checked |
| Blocklists | blocks when the folder or `blocklist.txt` is missing, when `blocklist-public.txt` is missing, when a list is unreadable or mixes encodings, or when the lists hold no terms | blocks when the folder or `blocklist.txt` is missing, unreadable or mis-encoded; a list with no terms only warns |

When `kernel/instructions.md` is staged, the hook also checks: at most `MAX_WORDS` words (as the hook counts them,
default 3,200); a `Version X.Y` line; a `## X.Y` entry in `kernel/CHANGELOG.md` (staged or in the working tree); and, if the
kernel text differs from `HEAD`, that the Version differs from the one in `HEAD`.

**Pre-push:**

- *Both modes.* Every pushed tip tree must pass the same blocklist, pattern and path checks, so a commit made with
  `--no-verify` still cannot be pushed. Every older commit the push publishes gets the same content scan on its whole tree
  and the path checks on the files it changed (the executable-bit rule excepted), so a leak that a later commit removed
  still blocks the push. The message of every pushed commit and annotated tag must hold no blocklist term;
  in public mode it must also hold no money amount, email address or phone number (a trailer line such as
  `Co-Authored-By: Name <address>` is exempt from the email check). Author, committer and tagger names must hold no
  blocklist term.
- *Private mode.* For a github.com remote (an SSH host alias that resolves to github.com counts), it asks `api.github.com`
  unauthenticated whether the repo is public. `200` means public: the push is blocked. `404` means private or absent: the
  push is allowed. Anything else is blocked unless `COMMCOACH_SKIP_VISIBILITY=1`.
- *Public mode.* The author and committer email of every pushed commit, and the tagger email of every pushed annotated tag,
  must match `commcoach.publicEmail`.

Never use `--no-verify`. The hooks are a backstop, not the policy: the policy is the two-repo model plus your own care.

## The loop

1. **Edit the kernel** in `framework/kernel/instructions.md`. Keep it generic: anything about you goes in
   `knowledge/profile.md` in your private workspace.
2. **Bump `Version X.Y`** in the kernel and add a `## X.Y` entry to `kernel/CHANGELOG.md` (what changed, why, eval before
   and after). The hook refuses a kernel change without a new Version, without an entry for it, or over 3,200 words.
3. **Run the eval** from the workspace root, score by hand, then gate:

   ```bash
   python framework/eval/run_eval.py --data . check             # schema, coverage minimums, overlap with the kernel's examples; no model call
   python framework/eval/run_eval.py --data . run --dry-run     # validates and shows the plan; sends nothing
   python framework/eval/run_eval.py --data . run               # the cli backend, on your subscription
   #   open the new file in eval/runs/, fill in the four scores and the Truth pass or fail on every row, then:
   python framework/eval/run_eval.py --data . score eval/runs/<file>.yaml
   ```

   `score` exits `0` on a pass and prints the `git tag` command. It exits `1` below the 4.00 average, on any Truth fail, or
   when the run is not taggable (a partial run, fewer than 20 situations, coverage gaps, a situation copied from the
   kernel's own examples, or a kernel that changed since the run). It exits `2` for an invalid file (an unscored row, a
   score outside 1 to 5, a row that errored). It appends one row per run file to `eval/runs/LOG.md`.
4. **Commit** the kernel change in `framework/` (public repo) and the run file in your workspace (private repo).
5. **Tag** in the public repo after a passing run, with the `git tag -a ...` command that `score` printed: it names the
   commit that holds the evaluated kernel, and its message carries only the overall average, the truth-fail count, the number of
   situations and the run file's name (the run file itself stays private). If `score` told you to commit `kernel/` first, commit, then run
   the command it printed. Push the commits and the tag: `git push origin main v2.1`.
6. **Paste the kernel into the Project's custom instructions** by hand, save, close, reopen the field and check that the
   last section is present (the field's size limit is unconfirmed).

To take a newer framework release into your workspace, run `git -C framework pull --ff-only`. If the kernel changed,
re-run the eval on your data and paste the new kernel into the Project.

Eval options. The rubric, the situation format, the coverage minimums and what the eval cannot measure are in
`eval/eval-set.md`.

- `--data DIR`: where your knowledge and situations are. Default: the `COMM_COACH_DATA` environment variable, else
  `framework/starter` (ten synthetic situations that only show the format). From inside `framework/`,
  `python eval/run_eval.py check` runs against them. The eval loads `DATA/knowledge/*.md` except `*.local.md`.
- `--kernel FILE`: default `framework/kernel/instructions.md`.
- `--backend cli` (**default**) uses your Claude plan through the headless Claude Code CLI: no API key and no per-token
  charge. It needs `claude` on the PATH. Outside a terminal where you are signed in (an editor task, a scheduled job) it also
  needs `CLAUDE_CODE_OAUTH_TOKEN` from `claude setup-token`. That token needs a Pro, Max, Team or Enterprise plan and can only
  make model requests. Set it for the session only and never write it into a file: `export CLAUDE_CODE_OAUTH_TOKEN=...` in
  bash, `$env:CLAUDE_CODE_OAUTH_TOKEN = '...'` in PowerShell. Copy it with the copy button: a token pasted from a wrapped
  selection contains line breaks, which the runner refuses. Without a token the call uses the terminal's login, and your
  user-level Claude config (CLAUDE.md, rules, hooks) may load into the eval's context. The runner starts the CLI in a scratch
  folder and drops every `CLAUDE*` and `ANTHROPIC*` environment variable except the token, so an inherited API key cannot turn a
  cli run into a metered one. Usage counts against your plan's limits; the size of a 20-situation run is not measured here.
- `--backend api --metered` is opt-in. It bills your Anthropic API account per token, needs `ANTHROPIC_API_KEY` and
  `pip install "anthropic>=0.96,<2"`, prints a cost statement before the first request, and refuses to run without the
  explicit `--metered` flag. `--dry-run` prints the statement and sends nothing. Nothing ever falls back from `cli` to `api`.
- Model and effort: `--model` (else `EVAL_MODEL`, else `claude-sonnet-5-5`) and `--effort` (else `EVAL_EFFORT`, else `high`).
  Use the full ID of the model your Project uses. Each row records the model requested and served, the backend, stop reason,
  token usage and hashes of the kernel and knowledge files, so a run can be traced to what produced it.

Tests, from `framework/`:

- `bash scripts/test-hooks.sh [-v] [-k SECTION]` runs the hook regression suite in throwaway repos under a temp folder, with
  synthetic blocklist terms and no network. It needs git 2.32 or newer. It is fork-heavy and slow on Git Bash: a full run
  on the test machine took about 14 minutes, not counting one case that hung; `-k` runs one section (the script's header
  lists their names).
- `python -m unittest eval/test_run_eval.py` runs the runner's tests against a fake API and a fake `claude`; no real model is
  called. It took under a minute on the test machine (51 tests, 2026-09-30).

## The claude.ai side

Facts below were checked against the official
[GitHub integration page](https://claude.com/docs/connectors/github) and the
[support article](https://support.claude.com/en/articles/10167454-use-the-github-integration) on 2026-09-30.
Anything marked **unconfirmed** is not stated there.

1. **Create a Project and keep it private.** Repository content can be added only to a private Project that you have not
   shared. In a shared Project the GitHub option is dimmed. Do not share this Project: it holds your knowledge.
2. **Custom instructions: paste the kernel by hand.** The connector's documented job is adding files and folders to
   *project knowledge*; nothing in the docs says it fills the custom-instructions field. After each kernel version bump,
   paste, save, reopen the field and check the end of the text.
3. **Connect GitHub.** Customize, Connectors, "GitHub Integration", Connect.
4. **Give Claude access to the private repo only.** For a private repo, "Claude cannot access that resource" links to the
   Claude GitHub App install page on GitHub; grant access to *only select repositories* and choose your private workspace
   repo. This public framework repo does not need it.
5. **Add knowledge.** In the Project, "+" in project knowledge, GitHub, pick your private repo, and in the file browser tick
   **only `knowledge/`**, then Add files. Never select `eval/` or anything else: the coach would read its own rules
   and test cases as data. (The kernel is not in your private repo at all, by design.)
6. **After every push that touches `knowledge/`: open the repo in project knowledge and choose Sync now.** Sync fetches the
   latest content of the files already selected.
7. **After you add a NEW knowledge file:** open the repo entry in project knowledge, tick the new file, Update, then Sync now.
   Whether a new file in an already-selected folder appears on Sync now alone is **unconfirmed**, so do not rely on it.
8. **Check it worked.** Open project knowledge and confirm every file you expect is listed. Then start a fresh chat, ask
   something only a knowledge file can answer (for example a `P:` prep that names a coded counterpart), and confirm the
   reply uses it.
9. **Which branch:** the docs say files "on a specific branch" are synced but not which. **Unconfirmed**; keep working on the
   default branch (`main`).
10. **Claude sees file names and contents only**, not commit history, pull requests or issues, and the selected content must
    fit in the context window.

Known problem, reported by third parties and not reproduced here: several open issues on the Claude Code issue tracker
(latest seen 2026-09-02) describe a *private personal* repo that appears in the picker but cannot be read, even with the
Claude GitHub App installed. If that happens to you, the fallback is to upload the `knowledge/*.md` files to the Project by
hand after each change. It is slower but does not depend on the connector.

Memory: what the coach remembers between chats is controlled by the platform, not by the kernel's instructions. Keep money
figures and real names out of the chat, and pause memory before personal conversations you do not want carried over. What
the coach saves on a `debrief` can be listed and exported: ask it once a month to list what it saved per counterpart,
replace real names with codes, and paste the result into `knowledge/counterparts.md`. The eval cannot test debrief saving,
`patterns` or memory: those are checked by hand in the Project itself (`eval/eval-set.md`, option B).

## Cadence

- After every conversation that mattered: `debrief` in the Project.
- Weekly: `patterns`.
- Monthly: export memory into `counterparts.md` (codes only); run the eval.
- Quarterly: run five situations inside the Project itself (`eval/eval-set.md`, option B), prune `counterparts.md`, refresh `voice-samples.md`.

## Rules

- **Private stays private.** Filled knowledge, situations, run files, names and figures live in your private workspace
  repo or in `private/`. Nothing from them goes into this repo.
- **Never change your private repo's visibility to public.** To publish something from a private repo, export the generic
  part into a new public repo with fresh history. A visibility flip publishes all of the history at once.
- **Stage by name; never `git add .` or `-A`; never `--no-verify`.**
- **Codes, bands and roles.** Codes for people, bands instead of figures, roles instead of names. The hooks are a backstop.
- **The kernel stays generic.** Version bump and CHANGELOG entry for every change; tag only after a passing eval.
- **Public commits use a noreply address and your username as the author name**, both set repo-locally. GitHub's own
  optional setting, "Block command line pushes that expose my email" (Settings, Emails), checks only the latest commit and
  only for an address you have marked private. The public pre-push hook checks every pushed commit.
- **`private/` never leaves the machine.** Back it up somewhere that is not GitHub.
- **Check your employer's policy** before putting work-related notes in a personal repository, even a private one.

## Known limits

- A pattern hook cannot detect prose. A paragraph describing your employer or a colleague, with none of their listed
  terms in it, passes every check. Only the private boundary covers that.
- A bare digit run with no `+` in front that touches a letter on either side is not treated as a phone number, so the
  hex hashes and commit ids in eval run files do not trip the phone check. A number written that way is not caught.
- Hooks are local. They do not run for commits made in GitHub's web or mobile editor, in a clone where the installer has not
  been run, or with `--no-verify`, and no local hook can stop a visibility change in GitHub's settings. The pre-push hooks
  are the second line: both re-scan what they push, and the private one refuses a public remote.
- The pre-push hooks check the commits a push publishes, not commits already on the remote: a leak that reached the remote
  before the hooks were installed stays there. Run the history scan above before the first push of a public repo.
- The hooks check names against the blocklist only. A real name that is not on `blocklist-public.txt` is pushed as-is,
  so set `user.name` to your GitHub username in the public repo and put your real name on the public blocklist.
- `core.hooksPath` is per-clone. A fresh clone is unguarded until `bash scripts/install-hooks.sh` has run.
  Verify with `git config core.hooksPath`.
- Blocklist terms are literal, case-insensitive substrings, so short terms cause false positives (the hook warns about
  terms of one or two letters).
- The private pre-push hook uses GitHub's unauthenticated API, which has a small hourly limit per IP address. A shared
  network can use it up, and the push is then refused, not allowed. Only github.com remotes are checked: a remote on
  another host is not.
- GUI git clients may run hooks in a different environment from your terminal. On the test machine, GitHub Desktop's bundled
  git ran the hook inside WSL, not Git Bash. Commit and push from a terminal.
- Line endings: `.gitattributes` forces LF. If a hook fails with "bad interpreter" and a `\r` in the message, a checkout turned
  it to CRLF; fix it with `sed -i 's/\r$//' scripts/hooks/* scripts/*.sh` and re-checkout with the `.gitattributes` in place.
- Not measured: the time a commit takes on your machine, and the plan usage of a 20-situation eval run.

## License

MIT. See [LICENSE](LICENSE).
