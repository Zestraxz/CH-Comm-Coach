#!/usr/bin/env bash
# install-hooks.sh - point the current git repository at the CH-Comm-Coach guard hooks.
#
#   bash scripts/install-hooks.sh public|private [--blocklist-dir DIR] [--email ADDR]
#
# Run it from inside the repository to protect, for example:
#   public framework repo:    cd framework && bash scripts/install-hooks.sh public --email <id>+<user>@users.noreply.github.com
#   private workspace repo:   bash framework/scripts/install-hooks.sh private
# Works from Git Bash, from PowerShell as `bash scripts/install-hooks.sh ...` (that `bash` is
# usually WSL) and from WSL. It only writes repo-local git config:
#   core.hooksPath          this framework's scripts/hooks, relative to the repo top
#   commcoach.mode          public | private
#   commcoach.blocklistDir  DIR, or <top>/private if that exists, else <top>/../private; stored
#                           relative to the repo top when possible so Git Bash and WSL agree
#   user.email              only with --email (public mode: must match commcoach.publicEmail,
#                           default *@users.noreply.github.com, or pre-push refuses the commits)
# Inside the framework repo it also marks the hooks and scripts executable in the index.
# Re-running it is safe. Check afterwards: git config core.hooksPath

set -euo pipefail

say()  { printf '%s\n' "$*"; }
warn() { printf 'WARNING: %s\n' "$*" >&2; }
die()  { printf 'install-hooks: %s\n' "$*" >&2; exit 1; }
usage() {
  printf '%s\n' 'usage: bash scripts/install-hooks.sh public|private [--blocklist-dir DIR] [--email ADDR]' \
    '       run it from inside the repository to protect' >&2
  exit 2
}

mode= bldir= email=
while (( $# )); do
  case $1 in
    public|private) [ -z "$mode" ] || usage; mode=$1 ;;
    --blocklist-dir) (( $# >= 2 )) || usage; bldir=$2; shift ;;
    --blocklist-dir=*) bldir=${1#*=} ;;
    --email) (( $# >= 2 )) || usage; email=$2; shift ;;
    --email=*) email=${1#*=} ;;
    -h|--help) usage ;;
    *) printf 'install-hooks: unknown argument: %s\n' "$1" >&2; usage ;;
  esac
  shift
done
[ -n "$mode" ] || usage

native_path() {  # Windows <-> WSL drive paths, as the hooks do
  local p=$1 d
  case ${OSTYPE:-} in
    msys*|cygwin*) ;;
    *) if [[ $p =~ ^([A-Za-z]):[\\/](.*)$ ]]; then
         d=${BASH_REMATCH[1],,}; [ -d "/mnt/$d" ] && p="/mnt/$d/${BASH_REMATCH[2]//\\//}"
       fi ;;
  esac
  printf '%s' "$p"
}
WINDOWS=0
case ${OSTYPE:-} in msys*|cygwin*) WINDOWS=1 ;; esac
canon() {  # canon <path>... -> CANON: one spelling per folder (Git Bash has /tmp/x AND /c/Users/.../Temp/x)
  local p
  CANON=()
  if (( WINDOWS )) && command -v cygpath >/dev/null 2>&1; then
    mapfile -t CANON < <(cygpath -m -a -- "$@")          # one process for all of them
    return
  fi
  for p in "$@"; do
    if [ -d "$p" ]; then CANON+=("$(cd "$p" && pwd -P)")
    elif [ -d "${p%/*}" ]; then CANON+=("$(cd "${p%/*}" && pwd -P)/${p##*/}")
    else CANON+=("$p"); fi
  done
}
relpath() {  # relpath <target> <base> (canonical) -> REL; fails across drives or via "/"
  local t=${1%/} b=${2%/} up= tt bb
  while :; do
    [ -z "$b" ] && { REL=; return 1; }
    tt=$t; bb=$b
    (( WINDOWS )) && { tt=${t,,}; bb=${b,,}; }              # Windows paths ignore case
    if [ "$tt" = "$bb" ]; then REL=${up%/}; REL=${REL:-.}; return 0; fi
    case $tt in "$bb"/*) REL=$up${t:${#b}+1}; return 0 ;; esac
    case $b in /|[A-Za-z]:|/[a-zA-Z]|/mnt|/mnt/[a-zA-Z]|/cygdrive/[a-zA-Z]) REL=; return 1 ;; esac
    b=${b%/*}; up=../$up
  done
}
portable_abs() {  # an absolute path both Git Bash and WSL hooks can read
  local p=$1
  [[ $p =~ ^/mnt/([a-z])/(.*)$ ]] && p="${BASH_REMATCH[1]^^}:/${BASH_REMATCH[2]}"
  printf '%s' "$p"
}

case ${BASH_SOURCE[0]} in */*) here=${BASH_SOURCE[0]%/*} ;; *) here=. ;; esac
for f in pre-commit pre-push lib.sh; do
  [ -f "$here/hooks/$f" ] || die "missing $here/hooks/$f"
done
top_raw=$(git rev-parse --show-toplevel 2>/dev/null) || die "not inside a git repository (run git init first)"

abs=
if [ -n "$bldir" ]; then
  bldir=$(native_path "$bldir")
  case $bldir in /*|[A-Za-z]:[\\/]*) abs=$bldir ;; *) abs="$PWD/$bldir" ;; esac
fi
if [ -n "$abs" ]; then canon "$here" "$top_raw" "$abs"; else canon "$here" "$top_raw"; fi
here=${CANON[0]}; top=${CANON[1]}; hooks="$here/hooks"

# ---- hooks path ---------------------------------------------------------------------------
relpath "$hooks" "$top" || die "the hooks ($hooks) must be on the same drive as the repo ($top)"
hooks_rel=$REL

# ---- blocklist folder ---------------------------------------------------------------------
if [ -n "$abs" ]; then
  abs=${CANON[2]}
  if relpath "$abs" "$top"; then bl_store=$REL; else bl_store=$(portable_abs "$abs"); fi
elif [ -d "$top/private" ]; then
  bl_store=private; abs="$top/private"
else
  bl_store=../private; abs="$top/../private"
fi

# ---- identity -----------------------------------------------------------------------------
public_email=$(git config --get commcoach.publicEmail || true)
public_email=${public_email:-'*@users.noreply.github.com'}
matches_public() {
  local e=$1 rc=1
  shopt -s nocasematch
  # shellcheck disable=SC2053
  [[ $e == $public_email ]] && rc=0
  shopt -u nocasematch
  return $rc
}
if [ -n "$email" ]; then
  [[ $email == ?*@?* ]] || die "--email '$email' is not an email address"
  if [ "$mode" = public ] && ! matches_public "$email"; then
    die "--email must match commcoach.publicEmail ($public_email) in public mode - pre-push would refuse every commit. Use your GitHub noreply address."
  fi
fi

# ---- apply ---------------------------------------------------------------------------------
git config core.hooksPath "$hooks_rel"
git config commcoach.mode "$mode"
git config commcoach.blocklistDir "$bl_store"
[ -n "$email" ] && git config user.email "$email"

chmod +x "$hooks/pre-commit" "$hooks/pre-push" "$here"/*.sh 2>/dev/null || true
untracked=()
if [ "$hooks_rel" = scripts/hooks ]; then                      # this is the framework repo
  cd "$top"
  exe=(scripts/hooks/pre-commit scripts/hooks/pre-push)
  for f in scripts/*.sh; do [ -f "$f" ] && exe+=("$f"); done
  declare -A staged_mode=()
  while IFS= read -r line; do
    staged_mode[${line#*$'\t'}]=${line%% *}
  done < <(git ls-files -s -- "${exe[@]}")
  for f in "${exe[@]}"; do
    case ${staged_mode[$f]-} in
      100755) ;;
      '') untracked+=("$f") ;;
      *) git update-index --chmod=+x -- "$f" && say "marked executable in the index: $f" ;;
    esac
  done
fi

# ---- report ----------------------------------------------------------------------------------
say "core.hooksPath         = $hooks_rel"
say "commcoach.mode         = $mode"
say "commcoach.blocklistDir = $bl_store"
[ -n "$email" ] && say "user.email             = set (repo-local)"
[ "$(git config --get core.hooksPath)" = "$hooks_rel" ] || die "core.hooksPath did not stick"
# git silently runs NO hook when core.hooksPath points nowhere, so prove it resolves from the top
[ -f "$top/$hooks_rel/pre-commit" ] && [ -f "$top/$hooks_rel/pre-push" ] ||
  die "core.hooksPath '$hooks_rel' does not reach the hooks from $top"

if [ ! -d "$abs" ]; then
  warn "blocklist folder not found: $bl_store - every commit is blocked until it exists (ALLOW_NO_BLOCKLIST=1 overrides in private mode only)"
  [ -n "$bldir" ] && [[ $bldir != */* ]] &&
    warn "from PowerShell, write --blocklist-dir with forward slashes (../private): WSL's bash drops backslashes"
else
  [ -f "$abs/blocklist.txt" ] || warn "missing $bl_store/blocklist.txt (one term per line: the real names that must never be committed)"
  [ "$mode" = public ] && [ ! -f "$abs/blocklist-public.txt" ] &&
    warn "missing $bl_store/blocklist-public.txt - public mode blocks every commit until it exists"
fi
if [ "$mode" = public ] && [ -z "$email" ]; then
  cur=$(git config --get user.email || true)
  matches_public "$cur" || warn "user.email does not match $public_email - pre-push will refuse these commits (re-run with --email <id>+<user>@users.noreply.github.com)"
fi
if (( ${#untracked[@]} )); then
  warn "not tracked yet: ${untracked[*]} - after 'git add', run: git update-index --chmod=+x -- ${untracked[*]}  (the public pre-commit blocks until you do)"
fi
for f in "$hooks/pre-commit" "$hooks/pre-push" "$hooks/lib.sh"; do
  IFS= read -r first <"$f" || true
  [[ $first == *$'\r' ]] && warn "$f has CRLF line endings; WSL cannot run it. Fix: sed -i 's/\r\$//' scripts/hooks/* scripts/*.sh"
done
say "Hooks installed. Test them: bash ${hooks_rel%/hooks}/test-hooks.sh"
