#!/usr/bin/env bash
# test-hooks.sh - self-contained regression suite for scripts/hooks/{pre-commit,pre-push,lib.sh}
# and scripts/install-hooks.sh.
#
#   bash scripts/test-hooks.sh [-v] [-k SECTION]
#
# Builds throwaway repositories in a temp dir, installs the hooks from THIS checkout in public
# and private mode against a temporary blocklist of synthetic terms, then runs real `git commit`
# and `git push` (to local bare repos; the GitHub visibility check talks to a stub curl). Prints
# one PASS/FAIL row per case and exits 1 on any FAIL. No network. Nothing outside the temp dir
# changes: git's global config is replaced by a file in the temp dir for the run.
# Sections (install always runs, it builds the repos):
#   ten blocklist rename names patterns kernel paths config push timing
# -v prints the hook output of failed cases. Needs bash >= 4.4, git >= 2.32, iconv.

set -u
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
FW=${HERE%/*}
VERBOSE=0 ONLY=
while (( $# )); do
  case $1 in
    -v) VERBOSE=1 ;;
    -k) ONLY=${2-}; shift ;;
    *) printf 'usage: bash scripts/test-hooks.sh [-v] [-k SECTION]\n' >&2; exit 2 ;;
  esac
  shift
done
(( BASH_VERSINFO[0] > 4 || ( BASH_VERSINFO[0] == 4 && BASH_VERSINFO[1] >= 4 ) )) ||
  { echo "needs bash >= 4.4" >&2; exit 2; }

T=$(mktemp -d "${TMPDIR:-/tmp}/commcoach-test.XXXXXX") || exit 2
cleanup() { cd / || true; chmod -R u+rwx "$T" 2>/dev/null; rm -rf "$T"; }
trap cleanup EXIT

unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_CONFIG_PARAMETERS GIT_CONFIG_COUNT \
      GIT_AUTHOR_NAME GIT_AUTHOR_EMAIL GIT_COMMITTER_NAME GIT_COMMITTER_EMAIL \
      ALLOW_NO_BLOCKLIST MAX_WORDS COMMCOACH_SKIP_VISIBILITY COMMCOACH_CURL COMMCOACH_TRACE \
      COMMCOACH_MAX_SHOW
export GIT_CONFIG_GLOBAL="$T/global.gitconfig"
NOREPLY=1+test@users.noreply.github.com
write_global() {
  # core.longpaths: Git for Windows otherwise fails a push into a deep TMPDIR ("Filename too long")
  printf '[user]\n\tname = Test User\n\temail = %s\n[init]\n\tdefaultBranch = main\n[commit]\n\tgpgsign = false\n[tag]\n\tgpgsign = false\n[advice]\n\tdetachedHead = false\n[core]\n\tlongpaths = true\n' "$NOREPLY" > "$GIT_CONFIG_GLOBAL"
}
write_global

BL="$T/bl"; BLX="$T/blx"; PUB="$T/pub"; PRV="$T/prv"; STUB="$T/curl-stub"
export STUB_LOG="$T/curl-stub.log"
mkdir -p "$BL" "$BLX"
# synthetic terms only - never put real names in this file
printf '# synthetic test terms\nacmecorp\nzorblax ltd\n供应商甲乙\nCafé Qwyx\n' > "$BL/blocklist.txt"
printf '# synthetic test terms\nquinnfield\nhoverdyne\n' > "$BL/blocklist-public.txt"
cat > "$STUB" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "${STUB_LOG:?}"
code=${STUB_CODE:-404}
if [ "$code" = 000 ]; then printf '000'; exit 7; fi
printf '%s' "$code"
EOF
chmod +x "$STUB"
ZERO=0000000000000000000000000000000000000000

# ---- results ------------------------------------------------------------------------------
NPASS=0 NFAIL=0 NSKIP=0
FAILS=()
row() {  # row <PASS|FAIL|SKIP|INFO> <id> <expect> <got> <ms> <description>
  case $1 in PASS) NPASS=$((NPASS+1)) ;; FAIL) NFAIL=$((NFAIL+1)); FAILS+=("$2 $6") ;; SKIP) NSKIP=$((NSKIP+1)) ;; esac
  printf '%-4s  %-9s %-6s %-14s %6s  %s\n' "$1" "$2" "$3" "$4" "$5" "$6"
}
LAST_OUT= GOT= MS=0
attempt() {  # attempt <cmd...>: GOT = PASS | BLOCK | ERROR(rc); LAST_OUT = its output; MS
  local t0=$EPOCHREALTIME rc
  LAST_OUT=$("$@" 2>&1); rc=$?
  MS=$(( (${EPOCHREALTIME/[.,]/} - ${t0/[.,]/}) / 1000 ))
  if (( rc == 0 )); then GOT=PASS
  elif [[ $LAST_OUT == *"commcoach "*"]: BLOCKED"* || $LAST_OUT == *"commcoach "*": BLOCKED"* ]]; then GOT=BLOCK
  else GOT="ERROR($rc)"; fi
}
check() {  # check <id> <expect> <description> [secret...]: compares GOT; any secret in the output fails
  local id=$1 exp=$2 desc=$3 s got=$GOT res=PASS
  shift 3
  for s in "$@"; do [[ ${LAST_OUT,,} == *"${s,,}"* ]] && got="$got+ECHO"; done
  [ "$got" = "$exp" ] || res=FAIL
  row "$res" "$id" "$exp" "$got" "$MS" "$desc"
  if [ "$res" = FAIL ] && (( VERBOSE )); then
    while IFS= read -r s; do printf '        | %s\n' "$s"; done <<<"$LAST_OUT"
  fi
}
out_has() {  # out_has <id> <substring> <description>: the last output contains the text
  if [[ $LAST_OUT == *"$2"* ]]; then row PASS "$1" has "yes" - "$3"; else row FAIL "$1" has "no" - "$3"; fi
}
hook_ran() {  # a "pass" without the hook's own ok line means git skipped the hook
  [ "$GOT" = PASS ] && [[ $LAST_OUT != *"commcoach pre-"*"]: ok"* ]] && GOT=NOHOOK
  return 0
}
commit_case() { attempt git commit -q -m "case $1"; hook_ran; check "$@"; }
push_case() {  # push_case <id> <expect> <description> <push args...>
  local id=$1 exp=$2 desc=$3; shift 3
  attempt git push -q "$@"; hook_ran; check "$id" "$exp" "$desc" "${SECRETS[@]}"
}
SECRETS=()

declare -A REP=()
parse_report() {  # REP[<path:line>] = label, from the last blocked output
  local l loc
  REP=()
  while IFS= read -r l; do
    [[ $l == '  '* ]] || continue
    l=${l#  }; loc=${l%%  *}
    REP[$loc]=${l#*  }
  done <<<"$LAST_OUT"
}

# ---- helpers ------------------------------------------------------------------------------
reset() { git reset -q --hard base; git clean -fdqx -e framework/ >/dev/null; }   # no leading '/': MSYS would rewrite it
add() { printf '%s\n' "$2" >> "$1"; git add -- "$1"; }
put() { printf '%s' "$2" > "$1"; }
subst() {  # subst <file> <from> <to>: first match on each line, no processes
  local f=$1 from=$2 to=$3 l out=
  local -a L=()
  mapfile -t L <"$f"
  for l in "${L[@]}"; do out+="${l/"$from"/"$to"}"$'\n'; done
  printf '%s' "$out" > "$f"
}
index_only() {  # index_only <mode> <path> <content>: stage a path the file system may not allow
  local blob
  blob=$(printf '%s' "$3" | git hash-object -w --stdin)
  git update-index --add --cacheinfo "$1,$blob,$2"
}
copy_hooks() {  # copy_hooks <scripts dir>
  mkdir -p "$1/hooks"
  cp "$FW/scripts/hooks/pre-commit" "$FW/scripts/hooks/pre-push" "$FW/scripts/hooks/lib.sh" "$1/hooks/"
  cp "$FW/scripts/install-hooks.sh" "$1/"
}
words() { local i s=; for (( i = 0; i < $1; i++ )); do s+="alpha beta gamma delta epsilon "; done; printf '%s\n' "$s"; }

mk_pub() {  # a synthetic public framework repo
  mkdir -p "$PUB" && cd "$PUB" || exit 2
  git init -q
  mkdir -p kernel starter/knowledge starter/eval eval docs scripts
  copy_hooks scripts
  cp "$FW/.gitattributes" .gitattributes
  put README.md $'# Test framework\n\nA synthetic public repo for the hook tests.\n'
  put LICENSE $'MIT License\n\nCopyright (c) 2026 Test\n'
  put .gitignore $'__pycache__/\n*.local.md\n'
  put requirements.txt $'pyyaml>=6\n'
  local i k=$'# Kernel\n\nVersion 1.0 - 2026-01-01\n\n'
  for (( i = 1; i <= 12; i++ )); do k+="Rule $i: keep answers short and ask before assuming."$'\n'; done
  put kernel/instructions.md "$k"
  put kernel/CHANGELOG.md $'# Changelog\n\n## 1.0 - 2026-01-01\n- Initial.\n'
  put starter/knowledge/norms.md $'# Norms\n\nMeetings start on time.\n'
  put starter/eval/situations.yaml $'- id: 1\n  prefix: "L:"\n  situation: "A colleague asks for an update."\n'
  put eval/run_eval.py $'import sys\nEXAMPLE = "someone@example.com"  # code: not pattern-scanned\nprint(sys.argv[1:], "$1")\n'
  put eval/eval-set.md $'# Eval set\n\nHow the eval works.\n'
  put docs/guide.md $'# Guide\n\nUse the coach.\n'
  git add -- .gitattributes .gitignore README.md LICENSE requirements.txt kernel starter eval docs scripts
}
mk_prv() {  # a synthetic private workspace repo with the framework copy inside (gitignored)
  mkdir -p "$PRV" && cd "$PRV" || exit 2
  git init -q
  mkdir -p framework/scripts knowledge eval/runs docs/01-session private
  copy_hooks framework/scripts
  cp "$FW/.gitattributes" .gitattributes
  put .gitignore $'private/*\n!private/README.md\n.resource/\n*.local.md\nframework/\n'
  put AGENTS.md $'# Agents\n\nWorkspace rules.\n'
  put STATUS.md $'# Status\n\nIn progress.\n'
  put knowledge/norms.md $'# Norms\n\nMeetings start on time.\n'
  put knowledge/profile.md $'# Profile\n\nRole: engineer.\n'
  put eval/situations.yaml $'- id: 1\n  prefix: ""\n  situation: "The boss asks for a status."\n  expect: live\n'
  put eval/runs/.gitkeep ''
  put docs/01-session/s1.md $'# Session\n\nNotes.\n'
  put private/README.md $'# private/\n\nLocal only.\n'
  git add -- .gitattributes .gitignore AGENTS.md STATUS.md knowledge eval docs private/README.md
}

# ---- sections -----------------------------------------------------------------------------
sec_install() {
  mk_pub
  attempt bash scripts/install-hooks.sh public --blocklist-dir "$BL" --email "$NOREPLY"
  if [ "$GOT" = PASS ] && [ "$(git config core.hooksPath)" = scripts/hooks ] &&
     [ "$(git config commcoach.mode)" = public ] && [ "$(git config commcoach.blocklistDir)" = ../bl ] &&
     [ "$(git config --local user.email)" = "$NOREPLY" ]; then GOT=PASS; else GOT=WRONG; fi
  check I1 PASS "install public: hooksPath scripts/hooks, mode, blocklistDir ../bl (relative), user.email"
  git update-index --chmod=-x -- scripts/hooks/pre-commit scripts/hooks/pre-push scripts/install-hooks.sh
  commit_case I2 BLOCK "public: hook files stored as 100644 are refused"
  out_has I2b "not executable in git" "I2 names the exec-bit fix"
  attempt bash scripts/install-hooks.sh public --blocklist-dir "$BL" --email "$NOREPLY"
  commit_case I3 PASS "installer marks hooks +x in the index; synthetic scaffold commits"
  git tag base
  git -c core.autocrlf=true clone -q "$PUB" "$T/crlf-clone" 2>/dev/null
  local f cr=0 line
  for f in scripts/hooks/pre-commit scripts/hooks/pre-push scripts/hooks/lib.sh scripts/install-hooks.sh; do
    while IFS= read -r line; do [[ $line == *$'\r' ]] && { cr=1; break; }; done <"$T/crlf-clone/$f"
  done
  if (( cr == 0 )) && [ "$(git -C "$T/crlf-clone" ls-files -s scripts/hooks/pre-commit | cut -c1-6)" = 100755 ]; then GOT=PASS; else GOT=CRLF; fi; MS=-
  check I10 PASS ".gitattributes: an autocrlf=true clone checks the hooks out LF-only and executable"

  attempt bash scripts/install-hooks.sh bogus
  [[ $GOT == ERROR* ]] && [ "$(git config commcoach.mode)" = public ] && GOT=REFUSED
  check I4 REFUSED "installer refuses an unknown mode and changes nothing"
  attempt bash scripts/install-hooks.sh public --blocklist-dir "$BL" --email someone@example.com
  [[ $GOT == ERROR* ]] && [ "$(git config --local user.email)" = "$NOREPLY" ] && GOT=REFUSED
  check I5 REFUSED "installer refuses a non-noreply --email in public mode" someone@example.com
  mkdir -p "$T/bl-half"; printf 'acmecorp\n' > "$T/bl-half/blocklist.txt"
  attempt bash scripts/install-hooks.sh public --blocklist-dir "$T/bl-half"
  [[ $GOT == PASS && $LAST_OUT == *WARNING*blocklist-public.txt* ]] && GOT=WARNED
  check I6 WARNED "installer warns when blocklist-public.txt is missing"
  attempt bash scripts/install-hooks.sh public --blocklist-dir "$BL"

  mk_prv
  attempt bash framework/scripts/install-hooks.sh private --blocklist-dir "$BL"
  if [ "$GOT" = PASS ] && [ "$(git config core.hooksPath)" = framework/scripts/hooks ] &&
     [ "$(git config commcoach.mode)" = private ] && [ "$(git config commcoach.blocklistDir)" = ../bl ]; then GOT=PASS; else GOT=WRONG; fi
  check I7 PASS "install private from the root: hooksPath framework/scripts/hooks"
  commit_case I8 PASS "private workspace scaffold commits"
  git tag base
  cd knowledge || exit 2
  attempt bash ../framework/scripts/install-hooks.sh private --blocklist-dir ../../bl
  cd "$PRV" || exit 2
  if [ "$GOT" = PASS ] && [ "$(git config core.hooksPath)" = framework/scripts/hooks ] &&
     [ "$(git config commcoach.blocklistDir)" = ../bl ]; then GOT=PASS; else GOT=WRONG; fi
  check I9 PASS "installer run from a subfolder stores paths relative to the repo top"
}

sec_ten() {  # the design chat's ten cases, both modes where they apply
  local m f
  for m in pub prv; do
    if [ $m = pub ]; then cd "$PUB"; f=starter/knowledge/norms.md; else cd "$PRV"; f=knowledge/norms.md; fi
    reset; add $f 'Unit cost was RM 5,000 last quarter.';        commit_case "T2-$m" BLOCK "money: RM 5,000" "5,000"
    reset; add $f 'We talked to AcmeCorp today.';                 commit_case "T3-$m" BLOCK "blocklist term (LF list)" acmecorp
    reset; mkdir -p private; put private/key.md $'SUP-A = someone\n'; git add -f private/key.md
                                                                   commit_case "T4-$m" BLOCK "force-added private/key.md"
    reset; add $f 'Reach me at someone@example.com';              commit_case "T5-$m" BLOCK "email address" someone@example.com
    reset; add $f 'Call 012-345 6789 now';                        commit_case "T6-$m" BLOCK "phone number" "345 6789"
    reset; add $f 'On 2026-09-27 at 14:30 scrap fell 15% under v2.1 (eval 4.1 avg), 3 of 20 cases, 30% faster.'
                                                                   commit_case "T10-$m" PASS "ordinary edit: dates, %, times, versions"
  done
  cd "$PUB"
  local -a ms=('USD 1,200' '$1,200' '€300' '¥8000') ids=(b c d e)
  for m in 0 1 2 3; do
    reset; add starter/knowledge/norms.md "Unit cost was ${ms[m]}."
    commit_case "T2${ids[m]}-pub" BLOCK "money: ${ms[m]}" "${ms[m]//[^0-9,]/}"
  done
  reset; subst kernel/instructions.md 'Version 1.0' 'Version 1.1'; git add kernel/instructions.md
  commit_case T7 BLOCK "kernel Version bump without a CHANGELOG entry"
  reset; words 700 >> kernel/instructions.md; subst kernel/instructions.md 'Version 1.0' 'Version 1.1'
  add kernel/CHANGELOG.md '## 1.1 - 2026-02-01'; git add kernel/instructions.md
  commit_case T8 BLOCK "oversize kernel (3500 words > 3200)"
  reset; subst kernel/instructions.md 'Version 1.0' 'Version 1.1'; add kernel/CHANGELOG.md $'\n## 1.1 - 2026-02-01\n- Changed: x'
  git add kernel/instructions.md
  commit_case T9 PASS "kernel Version bump WITH its CHANGELOG entry"
}

bl_prep() {  # blocklist-public.txt stays plain; blocklist.txt is rewritten by each case
  printf 'quinnfield\n' > "$BLX/blocklist-public.txt"
}
bl_case() {  # bl_case <id> <expect> <description> [extra expected output]
  reset; add "$BLF" 'We met ACMECORP today.'
  commit_case "$1" "$2" "$3" acmecorp
  [ -n "${4-}" ] && out_has "$1b" "$4" "$1 says: $4"
  return 0
}
sec_blocklist() {
  local u16
  cd "$PUB"; git config commcoach.blocklistDir ../blx; BLF=starter/knowledge/norms.md; bl_prep
  printf 'acmecorp\n' > "$BLX/blocklist.txt";                         bl_case B1 BLOCK "LF list"
  printf 'acmecorp\r\n' > "$BLX/blocklist.txt";                       bl_case B2 BLOCK "CRLF list"
  printf '# c\r\nother\r\nacmecorp\r\n' > "$BLX/blocklist.txt";       bl_case B3 BLOCK "CRLF list, term on line 3"
  printf '\xef\xbb\xbfacmecorp\n' > "$BLX/blocklist.txt";             bl_case B4 BLOCK "UTF-8 BOM, term on line 1"
  printf '\xef\xbb\xbfacmecorp\r\nother\r\n' > "$BLX/blocklist.txt";  bl_case B5 BLOCK "UTF-8 BOM + CRLF (PowerShell Set-Content UTF8)"
  { printf '\xff\xfe'; printf '# c\r\nacmecorp\r\n' | iconv -f UTF-8 -t UTF-16LE; } > "$BLX/blocklist.txt"
                                                                      bl_case B6 BLOCK "UTF-16LE with BOM (PowerShell 5.1 '>')"
  { printf '\xfe\xff'; printf 'acmecorp\n' | iconv -f UTF-8 -t UTF-16BE; } > "$BLX/blocklist.txt"
                                                                      bl_case B7 BLOCK "UTF-16BE with BOM"
  { printf 'zorblax ltd\n'; printf 'acmecorp\r\n' | iconv -f UTF-8 -t UTF-16LE; } > "$BLX/blocklist.txt"
                                                                      bl_case B8 BLOCK "UTF-8 list + UTF-16LE line appended by '>>'" "mixes encodings"
  { printf '\xff\xfe'; printf 'zorblax ltd\r\n' | iconv -f UTF-8 -t UTF-16LE; printf 'acmecorpx\n'; } > "$BLX/blocklist.txt"
                                                                      bl_case B9 BLOCK "UTF-16 list + UTF-8 line appended (even length)" "UTF-8 lines appended"
  { printf '\xff\xfe'; printf 'zorblax ltd\r\n' | iconv -f UTF-8 -t UTF-16LE; printf 'acmecorp\n'; } > "$BLX/blocklist.txt"
                                                                      bl_case B10 BLOCK "UTF-16 list + UTF-8 line appended (odd length)" "not valid UTF-16"
  printf 'acmecorp  \t\n' > "$BLX/blocklist.txt";                     bl_case B11 BLOCK "trailing spaces and tab after the term"
  printf '\t  acmecorp\n' > "$BLX/blocklist.txt";                     bl_case B12 BLOCK "leading tab and spaces"
  printf '# c\nacmecorp' > "$BLX/blocklist.txt";                      bl_case B13 BLOCK "no final newline"
  printf 'caf\xe9 qwyx\nacmecorp\n' > "$BLX/blocklist.txt";           bl_case B14 BLOCK "ANSI/Latin-1 list with a non-ASCII term" "not UTF-8"
  printf '???\nacmecorp\n' > "$BLX/blocklist.txt";                    bl_case B15 BLOCK "term lost to '?' by an encoding" "only '?'"
  printf '供应商甲乙\n' > "$BLX/blocklist.txt"
  reset; add "$BLF" '我们和供应商甲乙开会。'; commit_case B16 BLOCK "Chinese term" "供应商甲乙"
  printf 'Café Qwyx\n' > "$BLX/blocklist.txt"
  reset; add "$BLF" 'Lunch at CAFÉ QWYX.'; commit_case B17 BLOCK "accented term, other case" "qwyx"
  printf 'zorblax ltd\n' > "$BLX/blocklist.txt"
  reset; add "$BLF" 'Signed with Zorblax Ltd.'; commit_case B18 BLOCK "two-word term, other case" zorblax
  # fail-closed rules, public mode
  printf 'acmecorp\n' > "$BLX/blocklist.txt"; rm -f "$BLX/blocklist-public.txt"
  reset; add "$BLF" 'Clean line.'; commit_case B20 BLOCK "public: blocklist-public.txt missing"
  printf '# none\n' > "$BLX/blocklist.txt"; printf '# none\n\n' > "$BLX/blocklist-public.txt"
  reset; add "$BLF" 'Clean line.'; commit_case B21 BLOCK "public: both lists comment-only (zero terms)"
  bl_prep; rm -f "$BLX/blocklist.txt"
  reset; add "$BLF" 'Clean line.'; commit_case B22 BLOCK "public: blocklist.txt missing"
  export ALLOW_NO_BLOCKLIST=1
  reset; add "$BLF" 'Clean line.'; commit_case B23 PASS "public: blocklist.txt missing + ALLOW_NO_BLOCKLIST=1 (public list present)"
  git config commcoach.blocklistDir ../no-such-dir
  reset; add "$BLF" 'Clean line.'; commit_case B24 BLOCK "public: folder missing + ALLOW_NO_BLOCKLIST=1 still blocks (no public list)"
  unset ALLOW_NO_BLOCKLIST
  reset; add "$BLF" 'Clean line.'; commit_case B25 BLOCK "public: blocklist folder missing"
  git config commcoach.blocklistDir ../bl; reset

  # private mode
  cd "$PRV"; git config commcoach.blocklistDir ../blx; BLF=knowledge/norms.md; bl_prep
  rm -f "$BLX/blocklist.txt"
  reset; add "$BLF" 'Clean line.'; commit_case B30 BLOCK "private: blocklist.txt missing"
  export ALLOW_NO_BLOCKLIST=1
  reset; add "$BLF" 'Clean line.'; commit_case B31 PASS "private: blocklist.txt missing + ALLOW_NO_BLOCKLIST=1"
  out_has B31b "WARNING" "B31 warns"
  unset ALLOW_NO_BLOCKLIST
  printf '# only comments\n\n' > "$BLX/blocklist.txt"
  reset; add "$BLF" 'Clean line.'; commit_case B32 PASS "private: comment-only list passes"
  out_has B32b "NO terms" "B32 warns loudly: real-name check OFF"
  : > "$BLX/blocklist.txt"
  reset; add "$BLF" 'Clean line.'; commit_case B33 PASS "private: empty list passes (loud warning)"
  rm -f "$BLX/blocklist.txt"; mkdir "$BLX/blocklist.txt"
  reset; add "$BLF" 'Clean line.'; commit_case B34 BLOCK "private: blocklist.txt is a folder"
  rmdir "$BLX/blocklist.txt"; printf 'acmecorp\n' > "$BLX/blocklist.txt"; chmod 000 "$BLX/blocklist.txt"
  if [ -r "$BLX/blocklist.txt" ]; then
    row SKIP B35 BLOCK - - "private: unreadable blocklist.txt (chmod 000 has no effect on this file system)"
  else
    reset; add "$BLF" 'Clean line.'; commit_case B35 BLOCK "private: unreadable blocklist.txt"
  fi
  chmod 644 "$BLX/blocklist.txt"
  git config commcoach.blocklistDir ../no-such-dir
  reset; add "$BLF" 'Clean line.'; commit_case B36 BLOCK "private: blocklist folder missing"
  git config commcoach.blocklistDir ../bl
  reset; put knowledge/acmecorp-notes.md $'Clean text.\n'; git add knowledge/acmecorp-notes.md
  commit_case B37 BLOCK "blocklist term only in a file name (name not echoed)" acmecorp
  reset
}

sec_rename() {
  cd "$PRV"
  reset; git mv knowledge/norms.md knowledge/norms2.md; add knowledge/norms2.md 'mail someone@example.com'
  commit_case R1 BLOCK "rename + edit (git reports R) is scanned" someone@example.com
  reset; put note.md $'Budget RM 9,000 for Q3.\n'; git add note.md
  commit_case R2a PASS "private: a root note is outside the money scope"
  git mv note.md knowledge/note.md
  commit_case R2b BLOCK "pure move (R100) of that note into knowledge/ is scanned" "9,000"
  reset; git mv -f private/README.md private/key.md; add private/key.md 'SUP-A = Acmecorp'; git add -f private/key.md
  commit_case R3 BLOCK "rename private/README.md -> private/key.md" acmecorp
  reset; index_only 120000 knowledge/link.md 'norms.md'; git commit -q --no-verify -m symlink
  index_only 100644 knowledge/link.md $'mail someone@example.com\n'
  commit_case R4 BLOCK "type change symlink -> file (T) is scanned" someone@example.com
  reset; index_only 120000 docs/link.md 'someone@example.com'
  commit_case R4b BLOCK "symlink refused: git grep never reads its target" someone@example.com
  reset; add docs/01-session/s1.md 'Met Acmecorp.'; git commit -q --no-verify -m bypass
  add STATUS.md 'Unrelated clean edit.'
  commit_case R5 BLOCK "whole index: a leak committed with --no-verify blocks the next commit" acmecorp
  out_has R5b "docs/01-session/s1.md:" "R5 points at the old leak"
  reset; printf 'mail someone@example.com\n' >> knowledge/norms.md
  attempt git commit -q -a -m x; check R6 BLOCK "git commit -a (temporary index) is scanned" someone@example.com
  reset; printf 'mail someone@example.com\n' >> knowledge/norms.md
  attempt git commit -q -m x -- knowledge/norms.md; check R7 BLOCK "git commit <path> is scanned" someone@example.com
  reset
}

name_case() {  # name_case <id> <path> <content> <description>: SKIP where git refuses the name
  if index_only 100644 "$2" "$3" 2>/dev/null; then
    commit_case "$1" BLOCK "$4" someone@example.com
  else
    row SKIP "$1" BLOCK - - "$4 (this git refuses the name: core.protectNTFS)"
  fi
}
sec_names() {
  cd "$PRV"
  reset; put 'knowledge/供应商资料.md' $'mail someone@example.com\n'; git add 'knowledge/供应商资料.md'
  commit_case N1 BLOCK "Chinese file name is scanned" someone@example.com
  reset; put 'knowledge/café.md' $'mail someone@example.com\n'; git add 'knowledge/café.md'
  commit_case N2 BLOCK "accented file name is scanned" someone@example.com
  reset; put 'knowledge/a b.md' $'mail someone@example.com\n'; git add 'knowledge/a b.md'
  commit_case N3 BLOCK "file name with a space is scanned" someone@example.com
  reset; name_case N4 $'knowledge/a\tb.md' $'mail someone@example.com\n' "file name with a tab (scanned and refused)"
  reset; name_case N5 'knowledge/q"x.md' $'mail someone@example.com\n' "file name with a double quote is scanned"
  reset; name_case N6 $'knowledge/nl\nx.md' $'clean\n' "file name with a line break is refused"
  reset; put 'private/密钥.md' $'secret\n'; git add -f 'private/密钥.md'
  commit_case N7 BLOCK "force-added private/ file with a Chinese name"
  reset; index_only 100644 'Private/Key.md' $'secret\n'
  commit_case N8 BLOCK "Private/Key.md (other case) is still private/"
  reset
}

sec_patterns() {
  local -a pos neg; local i s f=starter/knowledge/samples.md
  cd "$PUB"; export COMMCOACH_MAX_SHOW=500
  pos=('RM 5,000' 'RM5,000' 'USD 1,200' '$1,200.50' '$50' '€300' '¥8000' '5000 USD' '5,000 RM' '12k MYR'
       '1.2 million dollars' 'rm5000' 'Rm 5000' 'usd 1200' '预算5万元' '单价 8500元' '人民币5000' '五千元'
       '约五千令吉' '8500块钱' '价格是MYR 3000' 'RM2.5k' 'US$ 1.2m' 'S$40' 'GBP 99' 'SGD 40' 'JPY 8000'
       'EUR300' '5000 ringgit' '3 million yen')
  reset; s=; for i in "${pos[@]}"; do s+="$i"$'\n'; done; put $f "$s"; git add $f
  attempt git commit -q -m money; parse_report
  for i in "${!pos[@]}"; do
    if [[ ${REP[$f:$((i+1))]-} == 'money amount'* ]]; then GOT=BLOCK; else GOT="${REP[$f:$((i+1))]:-PASS}"; fi
    MS=-; check "M+$((i+1))" BLOCK "money: ${pos[i]}"
  done
  neg=('eval 4.1 avg' 'Version 2.10' '2026-09-27' 'echo $1 and $2' '八折' '50%' 'the 8% increase' 'by 5pm today'
       '准备2块样板' '3万人' '10 RMA returned' '1,000 units' 'within 30 days' '5 LIVE, 5 PREP' 'ARM 64 build'
       'form 5 filed' '第5条' 'section 3.2.1' 'ISO 9001:2015' 'v2.1.10')
  reset; s=; for i in "${neg[@]}"; do s+="$i"$'\n'; done; put $f "$s"; git add $f
  attempt git commit -q -m nomoney; hook_ran; parse_report
  [ "$GOT" = NOHOOK ] && row FAIL "nomoney" PASS NOHOOK "$MS" "the hook did not run"
  for i in "${!neg[@]}"; do
    GOT=${REP[$f:$((i+1))]:-PASS}; MS=-; check "M-$((i+1))" PASS "not money: ${neg[i]}"
  done
  pos=('+60 12-345 6789' '+60123456789' '012-345 6789' '012-3456789' '0123456789' '(03) 1234 5678'
       '03-2345 6789' '+60 3-1234 5678' '+86 138 0013 8000' '13800138000' '+65 6123 4567' '+44 20 7946 0958'
       '(555) 123-4567' '+1 (555) 123-4567' '012.345.6789' '0123 456 789' '电话0123456789' 'Tel:0123456789')
  reset; s=; for i in "${pos[@]}"; do s+="call $i"$'\n'; done; put $f "$s"; git add $f
  attempt git commit -q -m phone; parse_report
  for i in "${!pos[@]}"; do
    if [[ ${REP[$f:$((i+1))]-} == *'phone number'* ]]; then GOT=BLOCK; else GOT="${REP[$f:$((i+1))]:-PASS}"; fi
    MS=-; check "P+$((i+1))" BLOCK "phone: ${pos[i]}"
  done
  neg=('PO 20260927001 dated 2026-09-27' 'batch 202609301234 shipped' 'Invoice 2026 0930 1234'
       'qty 100 200 3000 units' 'part 12-3456-7890' 'epoch 1727654400' 'SKU 123456789' 'CAPA-2026-0345'
       '20260927' 'at 14:30' 'tolerance 0.050 0.100' 'lot 0210-3344' 'dates 03/10/2026' 'order +5 000 000 pcs'
       'sha256: c7a3d4b057562589bf4b9a9e' 'commit f13800138000a2' 'sha256: 0123456789abcdef')
  reset; s=; for i in "${neg[@]}"; do s+="$i"$'\n'; done; put $f "$s"; git add $f
  attempt git commit -q -m nophone; hook_ran; parse_report
  [ "$GOT" = NOHOOK ] && row FAIL "nophone" PASS NOHOOK "$MS" "the hook did not run"
  for i in "${!neg[@]}"; do
    GOT=${REP[$f:$((i+1))]:-PASS}; MS=-; check "P-$((i+1))" PASS "not a phone: ${neg[i]}"
  done
  pos=('someone@example.com' 'a.b+tag@sub.example.co.uk' 'UPPER@EXAMPLE.COM')
  reset; s=; for i in "${pos[@]}"; do s+="mail $i"$'\n'; done; put $f "$s"; git add $f
  attempt git commit -q -m email; parse_report
  for i in "${!pos[@]}"; do
    if [[ ${REP[$f:$((i+1))]-} == *'email address'* ]]; then GOT=BLOCK; else GOT="${REP[$f:$((i+1))]:-PASS}"; fi
    MS=-; check "E+$((i+1))" BLOCK "email: ${pos[i]}" "${pos[i]}"
  done
  unset COMMCOACH_MAX_SHOW
  # scope
  reset; put scripts/tool.sh $'#!/bin/sh\necho "RM 5,000 someone@example.com 012-345 6789" "$1"\n'
  git add scripts/tool.sh; git update-index --chmod=+x scripts/tool.sh
  commit_case S1 PASS "public: patterns skip code (*.sh)"
  reset; add eval/run_eval.py 'PHONE = "012-345 6789"'; commit_case S2 PASS "public: patterns skip code (*.py)"
  reset; add scripts/hooks/lib.sh '# RM 5,000'; commit_case S3 PASS "public: patterns skip scripts/hooks/*"
  reset; add eval/run_eval.py '# met acmecorp'; commit_case S4 BLOCK "public: blocklist still covers code" acmecorp
  reset; add docs/guide.md 'Budget: RM 5,000.'; commit_case S5 BLOCK "public: patterns cover docs/" "5,000"
  reset; add README.md 'Contact someone@example.com'; commit_case S6 BLOCK "public: patterns cover README.md" someone@example.com
  reset; add LICENSE 'Copyright (c) 2026 Acmecorp'; commit_case S7 BLOCK "public: blocklist covers LICENSE" acmecorp
  reset; add starter/knowledge/norms.md 'Meet Quinnfield.'; commit_case S8 BLOCK "public: blocklist-public.txt terms apply" quinnfield
  cd "$PRV"
  reset; add docs/01-session/s1.md 'Budget RM 5,000, someone@example.com'; commit_case S10 PASS "private: patterns skip docs/"
  reset; add eval/situations.yaml '  notes: "ask someone@example.com"'; commit_case S11 BLOCK "private: patterns cover eval/" someone@example.com
  reset; add docs/01-session/s1.md 'Met Acmecorp.'; commit_case S12 BLOCK "private: blocklist covers every text file" acmecorp
  reset; add knowledge/profile.md 'Owner: Quinnfield'; commit_case S13 PASS "private: blocklist-public.txt terms do not apply"
  reset
}

sec_kernel() {
  local i
  cd "$PUB"
  reset; add kernel/instructions.md 'Rule 13: a new rule.'
  commit_case K1 BLOCK "kernel text changed, Version unchanged"
  reset; subst kernel/instructions.md 'Version 1.0' 'Version 1.1'; add kernel/CHANGELOG.md '## 1.10 - 2026-02-01'; git add kernel/instructions.md
  commit_case K2 BLOCK "Version 1.1 with only a '## 1.10' entry"
  reset; subst kernel/instructions.md 'Version 1.0' 'Version 1.10'; add kernel/CHANGELOG.md '## 1.1 - 2026-02-01'; git add kernel/instructions.md
  commit_case K3 BLOCK "Version 1.10 with only a '## 1.1' entry"
  reset; subst kernel/instructions.md 'Version 1.0' 'Version 1.1'; printf '\r\n## 1.1\r\n- x\r\n' >> kernel/CHANGELOG.md
  git add kernel/instructions.md; index_only 100644 kernel/CHANGELOG.md "$(<kernel/CHANGELOG.md)"
  commit_case K4 PASS "CRLF CHANGELOG heading '## 1.1' with no date"
  reset; subst kernel/instructions.md 'Version 1.0' 'Revision 1.0'; git add kernel/instructions.md
  commit_case K5 BLOCK "no 'Version X.Y' line"
  reset; subst kernel/instructions.md 'Version 1.0' 'Version 1.1'; add kernel/CHANGELOG.md '## 1.1 - 2026-02-01'; git add kernel/instructions.md
  export MAX_WORDS=abc; commit_case K6 BLOCK "MAX_WORDS=abc (not a number) blocks"
  export MAX_WORDS=10;  commit_case K7 BLOCK "MAX_WORDS=10: over the limit"
  export MAX_WORDS=03200; commit_case K8 PASS "MAX_WORDS=03200 read as decimal"
  unset MAX_WORDS
  reset; subst kernel/instructions.md 'Version 1.0' 'Version 1.1'; git add kernel/instructions.md
  printf '\n## 1.1 - 2026-02-01\n' >> kernel/CHANGELOG.md
  commit_case K9 BLOCK "entry only in the unstaged working-tree CHANGELOG (staged copy wins)"
  reset; subst kernel/instructions.md 'Version 1.0' 'Version 1.1'; git add kernel/instructions.md
  git rm -q --cached kernel/CHANGELOG.md; printf '\n## 1.1 - 2026-02-01\n' >> kernel/CHANGELOG.md
  commit_case K10 PASS "CHANGELOG not in the index: the working-tree copy is read"
  reset; git rm -q kernel/instructions.md
  commit_case K11 PASS "kernel deleted: kernel checks skipped"
  reset clean; mkdir -p docs/bulk
  for (( i = 1; i <= 1500; i++ )); do printf 'x\n' > "docs/bulk/file-with-a-longish-name-$i.md"; done
  subst kernel/instructions.md 'Version 1.0' 'Version 1.7'; git add docs/bulk kernel/instructions.md
  commit_case K12 BLOCK "1500 extra staged files + bump without entry (old SIGPIPE bypass)"
  out_has K12b "no '## 1.7' entry" "K12 names the missing entry"
  reset
}

sec_paths() {
  cd "$PUB"
  reset; put AGENTS.md $'# a\n'; git add AGENTS.md;                       commit_case H1 BLOCK "public: AGENTS.md is outside the allowlist"
  reset; mkdir -p knowledge; put knowledge/x.md $'x\n'; git add knowledge/x.md; commit_case H2 BLOCK "public: knowledge/ is outside the allowlist"
  reset; mkdir -p .github/workflows; put .github/workflows/ci.yml $'on: push\n'; git add .github
                                                                             commit_case H3 BLOCK "public: .github/ is outside the allowlist"
  reset; put eval/situations.yaml $'- id: 1\n'; git add eval/situations.yaml; commit_case H4 BLOCK "public: eval/*.yaml is outside the allowlist"
  reset; mkdir -p eval/sub; put eval/sub/x.md $'x\n'; git add eval/sub/x.md;  commit_case H5 BLOCK "public: eval/sub/x.md (eval/*.md is one level)"
  reset; put docs/new.md $'x\n'; put starter/knowledge/new.md $'x\n'; put eval/new.md $'x\n'; put scripts/tool.py $'x = 1\n'
  git add docs/new.md starter/knowledge/new.md eval/new.md scripts/tool.py;  commit_case H6 PASS "public: docs/ starter/ eval/*.md scripts/ allowed"
  reset; mkdir -p private; put private/README.md $'x\n'; git add -f private/README.md
                                                                             commit_case H7 BLOCK "public: even private/README.md is refused"
  reset; put docs/notes.local.md $'x\n'; git add -f docs/notes.local.md;     commit_case H8 BLOCK "public: *.local.md refused"
  reset; printf '\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR' > docs/pic.png; git add docs/pic.png
                                                                             commit_case H9 BLOCK "public: binary file refused"
  reset; put scripts/new.sh $'#!/bin/sh\necho hi\n'; git add scripts/new.sh; git update-index --chmod=-x scripts/new.sh
                                                                             commit_case H10 BLOCK "public: scripts/*.sh stored as 100644 refused"
  git update-index --chmod=+x scripts/new.sh;                                commit_case H11 PASS "public: scripts/*.sh stored as 100755 allowed"
  cd "$PRV"
  reset; put docs/pic.png $'\x89PNG\r\n'; printf '\x00\x00' >> docs/pic.png; git add docs/pic.png
                                                                             commit_case H20 PASS "private: binary outside knowledge/ and eval/ allowed"
  reset; printf '%%PDF-1.4\n\x00\x01\x02' > knowledge/spec.pdf; git add knowledge/spec.pdf
                                                                             commit_case H21 BLOCK "private: binary in knowledge/ refused (cannot be scanned)"
  reset; { printf '\xff\xfe'; printf 'mail someone@example.com\r\n' | iconv -f UTF-8 -t UTF-16LE; } > knowledge/u16.md
  git add knowledge/u16.md;                                                  commit_case H22 BLOCK "private: UTF-16 text in knowledge/ refused" someone@example.com
  reset; { printf '\xff\xfe'; printf 'Met Acmecorp\r\n' | iconv -f UTF-8 -t UTF-16LE; } > STATUS.md
  git add STATUS.md;                                                         commit_case H26 BLOCK "private: UTF-16 STATUS.md elsewhere cannot be blocklist-scanned" acmecorp
  reset; put knowledge/x.local.md $'x\n'; git add -f knowledge/x.local.md;   commit_case H23 BLOCK "private: *.local.md refused"
  reset; mkdir -p .resource; put .resource/chat.md $'x\n'; git add -f .resource/chat.md
                                                                             commit_case H24 BLOCK "private: .resource/ refused"
  reset; put private/README.md $'# private/\n\nLocal only. Edited.\n'; git add private/README.md
                                                                             commit_case H25 PASS "private: private/README.md allowed"
  reset
}

sec_config() {
  cd "$PUB"
  printf '[core]\n\tquotePath = true\n[color]\n\tgrep = always\n\tui = always\n[grep]\n\tcolumn = true\n\tlineNumber = true\n\tpatternType = perl\n\tfullName = true\n[diff]\n\trelative = true\n\tnoprefix = true\n\trenames = copies\n[submodule]\n\trecurse = true\n[log]\n\tshowSignature = true\n' >> "$GIT_CONFIG_GLOBAL"
  reset; add starter/knowledge/norms.md 'Met Acmecorp, RM 5,000.'
  commit_case C1 BLOCK "hostile global git config: leak still blocked" acmecorp "5,000"
  reset; add starter/knowledge/norms.md 'Clean.'
  commit_case C2 PASS "hostile global git config: clean commit passes"
  write_global
  reset; add docs/guide.md 'Budget RM 5,000.'
  attempt git -c commcoach.mode=private commit -q -m x
  check C3 BLOCK "settings guessed from .git/config are re-checked: git -c commcoach.mode=private wins"
  out_has C3b "[private mode]" "C3 ran in private mode"
  out_has C3c "eval/run_eval.py:2  email address" "C3 used the private scope (eval/*.py scanned, docs/ not)"
  [[ $LAST_OUT != *"docs/guide.md"* ]] && GOT=PASS || GOT=WRONG; MS=-
  check C3d PASS "C3 did not apply the public scope to docs/"
  reset; git config --unset commcoach.mode
  put AGENTS.md $'# a\n'; git add AGENTS.md
  commit_case C4 BLOCK "commcoach.mode unset => public (strictest)"
  git config commcoach.mode bogus; reset; add starter/knowledge/norms.md 'Clean.'
  commit_case C5 BLOCK "commcoach.mode invalid => blocked"
  git config commcoach.mode public; reset
}

sec_push() {
  local sha k
  export COMMCOACH_CURL=$STUB
  git init -q --bare "$T/pub-remote.git"; git init -q --bare "$T/prv-remote.git"
  cd "$PUB"; reset; git remote add origin "$T/pub-remote.git"
  SECRETS=(); push_case J1 PASS "public: push the baseline (noreply identity)" origin main
  git commit -q --no-verify --allow-empty -m a --author "Someone <someone@gmail.test>"
  SECRETS=(someone@gmail.test); push_case J2 BLOCK "public: author email not noreply" origin main
  git reset -q --hard origin/main
  GIT_COMMITTER_EMAIL=someone@gmail.test git commit -q --no-verify --allow-empty -m b
  push_case J3 BLOCK "public: committer email not noreply" origin main
  git reset -q --hard origin/main
  add docs/guide.md 'Met Acmecorp.'; git commit -q --no-verify -m c
  SECRETS=(acmecorp); push_case J4 BLOCK "public: --no-verify commit with a blocklist term" origin main
  git reset -q --hard origin/main
  put AGENTS.md $'# a\n'; git add AGENTS.md; git commit -q --no-verify -m d
  SECRETS=(); push_case J5 BLOCK "public: --no-verify commit outside the allowlist" origin main
  git reset -q --hard origin/main
  printf '\x89PNG\x00\x00' > docs/p.png; git add docs/p.png; git commit -q --no-verify -m e
  push_case J6 BLOCK "public: --no-verify binary file" origin main
  git reset -q --hard origin/main
  GIT_COMMITTER_EMAIL=someone@gmail.test git tag -a v9 -m tag
  SECRETS=(someone@gmail.test); push_case J7 BLOCK "public: annotated tag with a non-noreply tagger" origin v9
  git tag -d v9 >/dev/null
  git tag -a v1 -m tag
  SECRETS=(); push_case J8 PASS "public: annotated tag with a noreply tagger" origin v1
  git config commcoach.publicEmail "$NOREPLY"
  git commit -q --no-verify --allow-empty -m f --author "Other <2+other@users.noreply.github.com>"
  push_case J9 BLOCK "public: commcoach.publicEmail set to one exact address" origin main
  git config --unset commcoach.publicEmail; git reset -q --hard origin/main
  add docs/guide.md 'Another clean line.'; git commit -q -m g >/dev/null 2>&1
  push_case J10 PASS "public: clean new commit on top of the pushed ones" origin main
  git push -q origin main:side >/dev/null 2>&1
  push_case J11 PASS "public: deleting a remote branch" origin --delete side
  git commit -q --no-verify --allow-empty -m "Notes from the Acmecorp call"
  SECRETS=(acmecorp); push_case J12 BLOCK "public: blocklist term in a commit message" origin main
  git reset -q --hard origin/main
  git commit -q --no-verify --allow-empty -m "Budget is RM 5,000 now"
  SECRETS=("5,000"); push_case J13 BLOCK "public: money amount in a commit message" origin main
  git reset -q --hard origin/main
  git commit -q --no-verify --allow-empty -m "Ask someone@example.com first"
  SECRETS=(someone@example.com); push_case J14 BLOCK "public: email address in a commit message" origin main
  git reset -q --hard origin/main
  git commit -q --no-verify --allow-empty -m $'feat: tidy\n\nCo-Authored-By: Helper Bot <noreply@example.com>'
  SECRETS=(); push_case J15 PASS "public: a Co-Authored-By trailer email is allowed" origin main
  git tag -a v8 -m "Release for Acmecorp"
  SECRETS=(acmecorp); push_case J16 BLOCK "public: blocklist term in an annotated tag message" origin v8
  git tag -d v8 >/dev/null; SECRETS=()
  add docs/guide.md 'Met Acmecorp.'; git commit -q --no-verify -m h1
  subst docs/guide.md 'Met Acmecorp.' 'Met them.'; git add docs/guide.md; git commit -q --no-verify -m h2
  SECRETS=(acmecorp); push_case J17 BLOCK "public: leak in an older commit, removed again by the tip commit" origin main
  out_has J17b "(commit " "J17 names the older commit"
  git reset -q --hard origin/main
  mkdir -p private; put private/key.md $'k\n'; git add -f private/key.md; git commit -q --no-verify -m h3
  git rm -q --cached private/key.md; rm -rf private; git commit -q --no-verify -m h4
  SECRETS=(); push_case J18 BLOCK "public: private/key.md added and removed again before the push" origin main
  git reset -q --hard origin/main
  for k in 1 2 3; do add docs/guide.md "Clean line $k."; git commit -q -m "h5-$k" >/dev/null 2>&1; done
  push_case J19 PASS "public: clean 3-commit push (older commits scanned too)" origin main
  git commit -q --no-verify --allow-empty -m n1 --author "Acmecorp Person <$NOREPLY>"
  SECRETS=(acmecorp); push_case J26 BLOCK "public: blocklist term in the author name" origin main
  git reset -q --hard origin/main
  GIT_COMMITTER_NAME="Zorblax Ltd Bot" git commit -q --no-verify --allow-empty -m n2
  SECRETS=("zorblax ltd"); push_case J27 BLOCK "public: blocklist term in the committer name" origin main
  git reset -q --hard origin/main
  GIT_COMMITTER_NAME="Acmecorp Tagger" git tag -a v7 -m tag
  SECRETS=(acmecorp); push_case J28 BLOCK "public: blocklist term in the tagger name" origin v7
  git tag -d v7 >/dev/null; SECRETS=()

  cd "$PRV"; reset; git remote add origin "$T/prv-remote.git"; : > "$STUB_LOG"
  push_case J20 PASS "private: push to a non-GitHub remote" origin main
  if [ -s "$STUB_LOG" ]; then GOT=CALLED; else GOT=PASS; fi; MS=-
  check J20b PASS "private: no visibility request for a non-GitHub remote"
  add docs/01-session/s1.md 'Met Acmecorp.'; git commit -q --no-verify -m a
  SECRETS=(acmecorp); push_case J21 BLOCK "private: --no-verify commit with a blocklist term" origin main
  git reset -q --hard origin/main
  put private/key.md $'k\n'; git add -f private/key.md; git commit -q --no-verify -m b
  SECRETS=(); push_case J22 BLOCK "private: --no-verify commit of private/key.md" origin main
  git reset -q --hard origin/main
  git commit -q --no-verify --allow-empty -m "Call with Acmecorp"
  SECRETS=(acmecorp); push_case J23 BLOCK "private: blocklist term in a commit message" origin main
  git reset -q --hard origin/main
  git commit -q --no-verify --allow-empty -m "Budget RM 5,000, ask someone@example.com"
  SECRETS=(); push_case J24 PASS "private: money/email in a commit message are allowed (private repo)" origin main
  git reset -q --hard origin/main
  add knowledge/norms.md 'Met Acmecorp.'; git commit -q --no-verify -m p1
  subst knowledge/norms.md 'Met Acmecorp.' 'Met them.'; git add knowledge/norms.md; git commit -q --no-verify -m p2
  SECRETS=(acmecorp); push_case J25 BLOCK "private: leak in an older commit, removed again by the tip commit" origin main
  git reset -q --hard origin/main
  git commit -q --no-verify --allow-empty -m n3 --author "Acmecorp Person <a@example.com>"
  SECRETS=(acmecorp); push_case J29 BLOCK "private: blocklist term in the author name" origin main
  git reset -q --hard origin/main; SECRETS=()
  sha=$(git rev-parse HEAD)
  vis() {  # vis <id> <expect> <description> <url> <http code>
    : > "$STUB_LOG"
    STUB_CODE=$5 attempt bash framework/scripts/hooks/pre-push origin "$4" <<<"refs/heads/main $sha refs/heads/main $ZERO"
    check "$1" "$2" "$3" tok123
  }
  vis J30 BLOCK "visibility 200 (PUBLIC)"          https://github.com/o/r.git 200
  vis J31 PASS  "visibility 404 (not public)"      https://github.com/o/r.git 404
  vis J32 BLOCK "visibility 403 (rate limit)"      https://github.com/o/r.git 403
  vis J33 BLOCK "visibility: network failure"      https://github.com/o/r.git 000
  export COMMCOACH_SKIP_VISIBILITY=1
  vis J34 PASS  "403 + COMMCOACH_SKIP_VISIBILITY=1" https://github.com/o/r.git 403
  vis J35 BLOCK "200 + COMMCOACH_SKIP_VISIBILITY=1 still blocks" https://github.com/o/r.git 200
  unset COMMCOACH_SKIP_VISIBILITY
  vis J36 BLOCK "scp form git@github.com:o/r.git"   git@github.com:o/r.git 200
  vis J37 BLOCK "ssh://git@ssh.github.com:443/..."  ssh://git@ssh.github.com:443/o/r.git 200
  vis J38 BLOCK "https with a trailing slash"        https://github.com/o/r/ 200
  vis J39 BLOCK "ssh alias git@github-work:o/r.git"  git@github-work:o/r.git 200
  vis J40 BLOCK "credentials in the URL (never echoed)" https://user:tok123@github.com/o/r.git 200
  vis J41 BLOCK "unparseable GitHub URL"             https://github.com/onlyowner 404
  vis J42 PASS  "non-GitHub URL is not checked"      https://gitlab.com/o/r.git 200
  if [ -s "$STUB_LOG" ]; then GOT=CALLED; else GOT=PASS; fi; MS=-
  check J42b PASS "no visibility request for gitlab.com"
  vis J43 PASS "404 via the scp form"               git@github.com:o/r.git 404
  local req; req=$(<"$STUB_LOG")
  if [[ $req == *'https://api.github.com/repos/o/r'* && $req != *uthorization* && $req != *' -u '* && $req == '-q '* ]]; then GOT=PASS; else GOT=WRONG; fi; MS=-
  check J44 PASS "request: unauthenticated GET api.github.com/repos/o/r, curlrc ignored (-q)"
  unset COMMCOACH_CURL
  reset
}

sec_timing() {
  local d="$T/timing" i t0 ms r=() s
  mkdir -p "$d" && cd "$d" || return
  git init -q; mkdir -p kernel starter/knowledge docs scripts
  copy_hooks scripts; cp "$FW/.gitattributes" .
  put README.md $'# t\n'
  put kernel/instructions.md "$(printf '# Kernel\n\nVersion 1.0\n'; words 600)"
  put kernel/CHANGELOG.md $'## 1.0\n'
  for (( i = 1; i <= 34; i++ )); do put "docs/n$i.md" "Note $i: meeting on 2026-09-$(( i % 28 + 10 )) at 14:30, 15% faster."$'\n'; done
  for (( i = 1; i <= 3; i++ )); do put "starter/knowledge/k$i.md" "Template $i."$'\n'; done
  bash scripts/install-hooks.sh public --blocklist-dir "$BL" --email "$NOREPLY" >/dev/null 2>&1
  git add -- .gitattributes README.md kernel starter docs scripts
  git update-index --chmod=+x -- scripts/hooks/pre-commit scripts/hooks/pre-push scripts/install-hooks.sh
  s=$(git ls-files | wc -l)
  for i in 1 2 3; do
    t0=$EPOCHREALTIME; bash scripts/hooks/pre-commit >/dev/null 2>&1
    r+=($(( (${EPOCHREALTIME/[.,]/} - ${t0/[.,]/}) / 1000 )))
  done
  row INFO X1 - "${r[*]}" - "pre-commit alone, $((s)) staged files incl. kernel (3 runs, ms)"
  attempt git commit -q -m base; hook_ran; row INFO X2 - "$GOT" "$MS" "real git commit of those $((s)) files"
  add docs/n1.md 'one more line'; attempt git commit -q -m one; hook_ran; row INFO X3 - "$GOT" "$MS" "real git commit, 1 changed file"
  r=()
  add docs/n2.md 'another line'
  for i in 1 2 3; do
    t0=$EPOCHREALTIME; bash scripts/hooks/pre-commit >/dev/null 2>&1
    r+=($(( (${EPOCHREALTIME/[.,]/} - ${t0/[.,]/}) / 1000 )))
  done
  row INFO X4 - "${r[*]}" - "pre-commit alone, 1 staged change on a $((s))-file tree (3 runs, ms)"
}

# ---- run ------------------------------------------------------------------------------------
START=$EPOCHREALTIME
printf 'commcoach hook tests: %s | bash %s | %s | temp %s\n' "$(uname -sr)" "$BASH_VERSION" "$(git --version)" "$T"
printf '%-4s  %-9s %-6s %-14s %6s  %s\n' RES ID EXPECT GOT MS CASE
sec_install
for s in ten blocklist rename names patterns kernel paths config push timing; do
  [ -n "$ONLY" ] && [[ $s != $ONLY ]] && continue
  printf -- '-- %s\n' "$s"
  "sec_$s"
done
printf '\n%d passed, %d failed, %d skipped in %d s\n' "$NPASS" "$NFAIL" "$NSKIP" \
  $(( (${EPOCHREALTIME/[.,]/} - ${START/[.,]/}) / 1000000 ))
if (( NFAIL )); then printf 'FAILED: %s\n' "${FAILS[@]}"; exit 1; fi
exit 0
