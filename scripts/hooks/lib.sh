# shellcheck shell=bash
# lib.sh - shared by scripts/hooks/pre-commit and scripts/hooks/pre-push. Sourced, never run.
#
# Contract (README, "Guard hooks"):
#   mode        git config commcoach.mode = public | private; unset => public (the strictest).
#   blocklists  <commcoach.blocklistDir>/blocklist.txt (both modes) and blocklist-public.txt
#               (public mode). UTF-8 BOM, UTF-16LE/BE with BOM, CRLF and surrounding whitespace
#               are normalised; blank and '#' lines are skipped; terms match case-insensitively
#               as fixed strings. Missing/unreadable => block unless ALLOW_NO_BLOCKLIST=1;
#               public mode also needs blocklist-public.txt and at least one term.
#   content     the WHOLE index (pre-commit) or every pushed tip tree (pre-push) is scanned:
#               one `git grep` for the blocklist, one for money/email/phone. A rename, move or
#               type change cannot hide a file.
#   output      file:line and a check label only. Matched text is never printed.
# Every check fails closed: a check that cannot run blocks.
# Needs bash >= 4.4 (mapfile -d, wait on process substitutions) and git >= 2.25.
#
# Speed: under Git Bash on a busy Windows machine a fork costs ~0.15 s and a git start ~0.7 s,
# so git calls run in parallel on their own pipes, nothing in a loop forks (helpers return via
# CC_R), and the content scan starts before `git config` answers, from a guess read out of
# .git/config that is then checked against git's answer (a wrong guess only costs a respawn).

CC_MAX_SHOW=${COMMCOACH_MAX_SHOW:-40}
CC_HOOK=${CC_HOOK:-hook}
CC_MODE=public
CC_MODE_CFG=
CC_BLDIR_CFG=
CC_BLDIR=
CC_BLDIR_SHOW=
CC_PUBLIC_EMAIL='*@users.noreply.github.com'
CC_TOP=$PWD
CC_R=
CC_READ_ERR=
CC_SCAN_SRC=index
CC_SPEC=0
CC_G_MODE=
CC_G_BLDIR=
CC_BLOCKS=()
CC_WARNS=()
CC_NOTES=()
CC_BL_BLOCKS=()
CC_BL_WARNS=()
CC_TERMS=()
CC_TERMS_LC=()
CC_TERM_REF=()
CC_P=()
CC_PAT_SPEC=()
CC_H_PATH=(); CC_H_LINE=(); CC_H_TEXT=()
declare -A CC_FD=() CC_PID=() CC_P_MODE=() CC_P_OLD=() CC_P_NEW=() CC_P_BIN=()

# ---- output ------------------------------------------------------------------------------

cc_block() { CC_BLOCKS+=("$1"$'\t'"$2"); }   # cc_block <location> <label>
cc_warn()  { CC_WARNS+=("$1"); }
cc_note()  { CC_NOTES+=("$1"); }
CC_T0=${EPOCHREALTIME:-}
cc_trace() {  # COMMCOACH_TRACE=1: elapsed milliseconds per phase, on stderr
  [ "${COMMCOACH_TRACE:-0}" = 1 ] && [ -n "$CC_T0" ] || return 0
  local now=${EPOCHREALTIME/[.,]/} t0=${CC_T0/[.,]/}
  printf 'commcoach %s trace: %6d ms  %s\n' "$CC_HOOK" $(( (10#$now - 10#$t0) / 1000 )) "$1" >&2
}

cc_finish() {  # print notes, warnings and blocks; return 1 when anything blocked
  local w loc line i=0
  local -a blocks=("${CC_BL_BLOCKS[@]}" "${CC_BLOCKS[@]}") warns=("${CC_BL_WARNS[@]}" "${CC_WARNS[@]}")
  local n=${#blocks[@]}
  for w in "${CC_NOTES[@]}"; do printf 'commcoach %s [%s mode]: %s\n' "$CC_HOOK" "$CC_MODE" "$w" >&2; done
  for w in "${warns[@]}"; do printf 'commcoach %s [%s mode]: WARNING: %s\n' "$CC_HOOK" "$CC_MODE" "$w" >&2; done
  if (( n == 0 )); then
    printf 'commcoach %s [%s mode]: ok\n' "$CC_HOOK" "$CC_MODE" >&2
    return 0
  fi
  printf 'commcoach %s [%s mode]: BLOCKED - %d problem(s):\n' "$CC_HOOK" "$CC_MODE" "$n" >&2
  for line in "${blocks[@]}"; do
    (( i++ < CC_MAX_SHOW )) || break
    loc=${line%%$'\t'*}
    printf '  %s  %s\n' "${loc//[[:cntrl:]]/?}" "${line#*$'\t'}" >&2
  done
  (( n > CC_MAX_SHOW )) && printf '  ... and %d more\n' $(( n - CC_MAX_SHOW )) >&2
  return 1
}

# ---- process helpers ---------------------------------------------------------------------

declare -A CC_CMD=() CC_IN=()
cc_spawn() {  # cc_spawn <name> <cmd...>          (stdin = /dev/null)
  local _n=$1 _fd; shift
  exec {_fd}< <(exec "$@" </dev/null)
  CC_PID[$_n]=$!; CC_FD[$_n]=$_fd
  printf -v "CC_CMD[$_n]" '%q ' "$@"
}
cc_spawn_in() {  # cc_spawn_in <name> <stdin text> <cmd...>
  local _n=$1 _in=$2 _fd; shift 2
  exec {_fd}< <(exec "$@" <<<"$_in")
  CC_PID[$_n]=$!; CC_FD[$_n]=$_fd
  printf -v "CC_CMD[$_n]" '%q ' "$@"; CC_IN[$_n]=$_in
}
cc_spawned() { [ -n "${CC_FD[$1]-}" ]; }
cc_reap() {  # cc_reap <name>, after reading it to EOF -> the command's exit status
  local _n=$1 _fd=${CC_FD[$1]} _rc
  wait "${CC_PID[$_n]}"; _rc=$?                   # wait BEFORE closing: fewer lost statuses
  exec {_fd}<&-
  # bash 5.1/5.2 now and then lose a process substitution's status: wait then reports -1,
  # 255 or 127 (seen in about 1 wait in 4000). Re-run the command once, output discarded,
  # for its real status; a command that truly fails that way fails again and still blocks.
  if (( _rc < 0 || _rc == 127 || _rc == 255 )); then
    if [ -n "${CC_IN[$_n]+x}" ]; then eval "${CC_CMD[$_n]}" <<<"${CC_IN[$_n]}" >/dev/null 2>&1
    else eval "${CC_CMD[$_n]}" </dev/null >/dev/null 2>&1; fi
    _rc=$?
  fi
  unset "CC_FD[$_n]" "CC_PID[$_n]" "CC_CMD[$_n]" "CC_IN[$_n]"
  return "$_rc"
}
cc_collect() {  # cc_collect <name> <array> [nul] -> array filled; returns the command's status
  local _n=$1 _fd=${CC_FD[$1]}
  if [ "${3-}" = nul ]; then mapfile -d '' -u "$_fd" "$2"; else mapfile -t -u "$_fd" "$2"; fi
  cc_reap "$_n"
}
cc_discard() {  # stop reading a spawn and reap it; its status is ignored
  local _fd=${CC_FD[$1]-}
  [ -n "$_fd" ] || return 0
  exec {_fd}<&-
  wait "${CC_PID[$1]}" 2>/dev/null
  unset "CC_FD[$1]" "CC_PID[$1]" "CC_CMD[$1]" "CC_IN[$1]"
  return 0
}
cc_read_exact() {  # <fd> <bytes> -> CC_R = exactly that many bytes of the stream
  local LC_ALL=C
  CC_R=
  (( $2 > 0 )) && IFS= read -r -N "$2" -u "$1" CC_R
  return 0
}

# git with every user setting that could change the output format pinned
CC_GREP=(git -c grep.column=false -c grep.lineNumber=false -c grep.fullName=false
         -c grep.patternType=basic -c color.grep=never -c core.quotePath=false
         -c submodule.recurse=false grep --no-color --no-column -z -n -I)
CC_DIFF=(git -c core.quotePath=false -c diff.relative=false -c diff.renames=false
         diff --no-color --no-ext-diff --no-textconv --no-renames --no-abbrev --raw --numstat -z)

# ---- configuration -----------------------------------------------------------------------

cc_spawn_config() { cc_spawn cfg git config -z --get-regexp '^commcoach\.'; }

cc_apply_mode() {  # CC_MODE from the raw setting; 1 when the value is invalid
  case ${CC_MODE_CFG,,} in
    ''|public) CC_MODE=public ;;
    private) CC_MODE=private ;;
    *) CC_MODE=public; return 1 ;;
  esac
}

cc_collect_config() {  # git's answer - the only one that counts
  local -a kv=(); local rc rec k v
  cc_collect cfg kv nul; rc=$?
  (( rc == 0 || rc == 1 )) ||
    cc_block "(git config)" "cannot read the commcoach.* settings (git config status $rc)"
  CC_MODE_CFG=; CC_BLDIR_CFG=
  for rec in "${kv[@]}"; do
    k=${rec%%$'\n'*}
    if [[ $rec == *$'\n'* ]]; then v=${rec#*$'\n'}; else v=; fi
    case ${k,,} in
      commcoach.mode) CC_MODE_CFG=$v ;;
      commcoach.blocklistdir) CC_BLDIR_CFG=$v ;;
      commcoach.publicemail) [ -n "$v" ] && CC_PUBLIC_EMAIL=$v ;;
    esac
  done
  cc_apply_mode ||
    cc_block "(git config)" "commcoach.mode must be 'public' or 'private' - checked as public"
}

cc_guess_config() {  # <config file>: CC_G_MODE / CC_G_BLDIR from a plain read; 1 when unsure
  local f=$1 line sec= k v lead trail
  local -a lines=()
  CC_G_MODE=; CC_G_BLDIR=
  [ -f "$f" ] || return 1
  mapfile -t lines <"$f" || return 1
  for line in "${lines[@]}"; do
    line=${line%$'\r'}
    lead=${line%%[![:space:]]*}; line=${line#"$lead"}
    case $line in
      '['*) sec=${line#[}; sec=${sec%%]*}; sec=${sec,,}
            [[ $line == *]*[![:space:]]* ]] && return 1
            continue ;;
      ''|'#'*|';'*) continue ;;
    esac
    if [ "$sec" != commcoach ]; then [[ $sec == commcoach* ]] && return 1; continue; fi
    [[ $line == *=* ]] || return 1
    k=${line%%=*}; v=${line#*=}
    trail=${k##*[![:space:]]}; k=${k%"$trail"}; k=${k,,}
    lead=${v%%[![:space:]]*}; v=${v#"$lead"}; trail=${v##*[![:space:]]}; v=${v%"$trail"}
    [[ $v == *[\"\\\;#]* ]] && return 1
    case $k in mode) CC_G_MODE=$v ;; blocklistdir) CC_G_BLDIR=$v ;; esac
  done
  return 0
}

cc_speculate() {  # cc_speculate <scan source...>: start the content scan on the guessed settings
  local gd=${GIT_DIR:-.git}
  CC_SPEC=0
  [ -d "$gd" ] || return 0
  cc_guess_config "$gd/config" || return 0
  local saved_mode=$CC_MODE_CFG saved_dir=$CC_BLDIR_CFG
  CC_MODE_CFG=$CC_G_MODE; CC_BLDIR_CFG=$CC_G_BLDIR
  if ! cc_apply_mode; then CC_MODE_CFG=$saved_mode; CC_BLDIR_CFG=$saved_dir; return 0; fi
  cc_load_blocklists
  cc_scan_spawn "$@"
  CC_SPEC=1
}

cc_settle() {  # after cc_collect_config: keep the speculative scan only if git agrees
  if (( CC_SPEC )) && [ "$CC_MODE_CFG" = "$CC_G_MODE" ] && [ "$CC_BLDIR_CFG" = "$CC_G_BLDIR" ]; then
    cc_trace "settings guess confirmed"
    return 0
  fi
  if (( CC_SPEC )); then cc_discard bl; cc_discard pat; cc_trace "settings guess wrong - rescanning"; fi
  CC_SPEC=0
  cc_load_blocklists
  cc_scan_spawn "$@"
}

cc_native_path() {  # CC_R = <path> translated for this shell (Windows <-> WSL drive paths)
  local p=$1 d
  case ${OSTYPE:-} in
    msys*|cygwin*)
      if [[ $p == /mnt/[a-zA-Z]/* && ! -e $p ]]; then d=${p:5:1}; p="/${d,,}/${p:7}"; fi ;;
    *)
      if [[ $p =~ ^([A-Za-z]):[\\/](.*)$ ]]; then
        d=${BASH_REMATCH[1],,}
        [ -d "/mnt/$d" ] && p="/mnt/$d/${BASH_REMATCH[2]//\\//}"
      elif [[ $p =~ ^/([a-zA-Z])/(.*)$ && ! -e $p && -d /mnt/${BASH_REMATCH[1],,} ]]; then
        p="/mnt/${BASH_REMATCH[1],,}/${BASH_REMATCH[2]}"
      fi ;;
  esac
  CC_R=$p
}

cc_resolve_bldir() {  # commcoach.blocklistDir, else <top>/private, else <top>/../private
  local d
  if [ -n "$CC_BLDIR_CFG" ]; then
    CC_BLDIR_SHOW=$CC_BLDIR_CFG
    cc_native_path "$CC_BLDIR_CFG"; d=$CC_R
    case $d in /*|[A-Za-z]:[\\/]*) ;; *) d="$CC_TOP/$d" ;; esac
  elif [ -d "$CC_TOP/private" ]; then
    d="$CC_TOP/private"; CC_BLDIR_SHOW=private
  else
    d="$CC_TOP/../private"; CC_BLDIR_SHOW=../private
  fi
  CC_BLDIR=$d
}

# ---- blocklists --------------------------------------------------------------------------

cc_bl_block() { CC_BL_BLOCKS+=("(blocklist)"$'\t'"$1"); }
cc_bl_warn()  { CC_BL_WARNS+=("$1"); }

cc_utf8_ok() {  # is $1 valid UTF-8? (pure bash; terms are short)
  local LC_ALL=C s=$1 i=0 j need b n=${#1}
  [[ $s == *[$'\x80'-$'\xff']* ]] || return 0
  while (( i < n )); do
    printf -v b '%d' "'${s:i:1}"; (( b < 0 )) && b=$(( b + 256 ))
    if (( b < 128 )); then i=$(( i + 1 )); continue; fi
    if   (( b >= 194 && b <= 223 )); then need=1
    elif (( b >= 224 && b <= 239 )); then need=2
    elif (( b >= 240 && b <= 244 )); then need=3
    else return 1; fi
    for (( j = 1; j <= need; j++ )); do
      (( i + j < n )) || return 1
      printf -v b '%d' "'${s:i+j:1}"; (( b < 0 )) && b=$(( b + 256 ))
      (( b >= 128 && b <= 191 )) || return 1
    done
    i=$(( i + need + 1 ))
  done
  return 0
}

cc_read_terms() {  # cc_read_terms <file> <tag> -> appends terms; 0 ok, 1 unreadable, 2 bad encoding
  local LC_ALL=C f=$1 tag=$2 first2= text= raw t lead trail no=0
  local -a lines=()
  CC_READ_ERR=
  if [ ! -f "$f" ] || ! true 2>/dev/null <"$f"; then CC_READ_ERR="cannot be read"; return 1; fi
  if IFS= read -r -d '' _ <"$f"; then                  # a NUL byte: UTF-16, or mixed encodings
    IFS= read -r -n 2 -d '' first2 <"$f" || true
    if [[ $first2 != $'\xff\xfe' && $first2 != $'\xfe\xff' ]]; then
      CC_READ_ERR="mixes encodings (NUL bytes but no UTF-16 byte-order mark) - re-save it as UTF-8"
      return 2
    fi
    if ! text=$(iconv -f UTF-16 -t UTF-8 <"$f" 2>/dev/null); then
      CC_READ_ERR="is not valid UTF-16 (odd length or mixed encodings) - re-save it as UTF-8"
      return 2
    fi
    # UTF-8 lines appended to a UTF-16 file decode into U+0A00-0AFF / U+0D00-0DFF
    if [[ $text == *$'\xe0'[$'\xa8'$'\xa9'$'\xaa'$'\xab'$'\xb4'$'\xb5'$'\xb6'$'\xb7']* ]]; then
      CC_READ_ERR="looks like UTF-16 with UTF-8 lines appended - re-save it as UTF-8"
      return 2
    fi
    mapfile -t lines <<<"$text"
  else
    mapfile -t lines <"$f"
  fi
  for raw in "${lines[@]}"; do
    no=$(( no + 1 ))
    t=${raw//$'\xef\xbb\xbf'/}                          # byte-order marks
    lead=${t%%[![:space:]]*}; t=${t#"$lead"}            # CR, tabs, spaces at both ends
    trail=${t##*[![:space:]]}; t=${t%"$trail"}
    [ -z "$t" ] && continue
    [[ $t == '#'* ]] && continue
    if ! cc_utf8_ok "$t"; then
      CC_READ_ERR="line $no is not UTF-8 (ANSI or Latin-1?) - re-save the file as UTF-8"; return 2
    fi
    if [[ $t =~ ^[?]+$ ]]; then
      CC_READ_ERR="line $no is only '?' characters (an encoding lost the term) - re-save as UTF-8"; return 2
    fi
    [[ $t =~ ^[[:alnum:]]{1,2}$ ]] &&
      cc_bl_warn "$tag line $no: a term of 1-2 letters also matches inside longer words"
    CC_TERMS+=("$t"); CC_TERM_REF+=("$tag line $no")
  done
  return 0
}

cc_load_blocklists() {  # the fail-closed rules for the current mode (results in CC_BL_*)
  local allow=${ALLOW_NO_BLOCKLIST:-0} f rc t
  CC_BL_BLOCKS=(); CC_BL_WARNS=(); CC_TERMS=(); CC_TERM_REF=(); CC_TERMS_LC=()
  cc_resolve_bldir
  if [ ! -d "$CC_BLDIR" ]; then
    if [ "$allow" = 1 ]; then
      cc_bl_warn "blocklist folder '$CC_BLDIR_SHOW' not found; ALLOW_NO_BLOCKLIST=1, so blocklist.txt is skipped"
    else
      cc_bl_block "blocklist folder '$CC_BLDIR_SHOW' not found (commcoach.blocklistDir) - restore it, or set ALLOW_NO_BLOCKLIST=1 for one commit"
    fi
  else
    f="$CC_BLDIR/blocklist.txt"
    if [ ! -e "$f" ]; then
      if [ "$allow" = 1 ]; then cc_bl_warn "blocklist.txt not found; ALLOW_NO_BLOCKLIST=1, so it is skipped"
      else cc_bl_block "blocklist.txt not found in '$CC_BLDIR_SHOW' - create it, or set ALLOW_NO_BLOCKLIST=1 for one commit"; fi
    else
      cc_read_terms "$f" blocklist.txt; rc=$?
      if (( rc == 1 )) && [ "$allow" = 1 ]; then
        cc_bl_warn "blocklist.txt $CC_READ_ERR; ALLOW_NO_BLOCKLIST=1, so it is skipped"
      elif (( rc != 0 )); then
        cc_bl_block "blocklist.txt $CC_READ_ERR"
      fi
    fi
  fi
  if [ "$CC_MODE" = public ]; then
    f="$CC_BLDIR/blocklist-public.txt"
    if [ ! -e "$f" ]; then
      cc_bl_block "public mode needs blocklist-public.txt in '$CC_BLDIR_SHOW'"
    elif ! cc_read_terms "$f" blocklist-public.txt; then
      cc_bl_block "blocklist-public.txt $CC_READ_ERR"
    fi
    (( ${#CC_TERMS[@]} )) || cc_bl_block "public mode needs at least one blocklist term; the lists are empty"
  elif (( ${#CC_TERMS[@]} == 0 )); then
    cc_bl_warn "!!! blocklist.txt has NO terms - the real-name check is OFF. Add the names that must never be committed. !!!"
  fi
  for t in "${CC_TERMS[@]}"; do CC_TERMS_LC+=("${t,,}"); done
}

cc_term_ref() {  # CC_R = which blocklist line a matched line hit (never the term itself)
  local lc=${1,,} i
  CC_R="a blocklist term"
  for i in "${!CC_TERMS_LC[@]}"; do
    if [[ $lc == *"${CC_TERMS_LC[i]}"* ]]; then CC_R="term from ${CC_TERM_REF[i]}"; return 0; fi
  done
}

# ---- patterns ----------------------------------------------------------------------------
# POSIX ERE, matched with -i. No \b or \s (locale-dependent), and no multibyte character inside
# [...]: Git for Windows matches bytes, so [¥] would match any byte of it. Multibyte text is
# only used in alternations.
CC_CUR='RM|MYR|USD|SGD|CNY|RMB|EUR|GBP|HKD|JPY|THB|IDR|VND|AUD|NZD|CHF|INR|KRW|TWD|NTD'
CC_ZH_CUR='元|块钱|美元|令吉|人民币|港币|港元|马币|新币|新元|日元|欧元|英镑|台币'
CC_ZH_NUM='一|二|三|四|五|六|七|八|九|十|百|千|万|两|亿'
CC_RE_MONEY='(^|[^A-Za-z])('"$CC_CUR"')[[:space:]]?[0-9]'\
'|(^|[^A-Za-z])(US|S|HK|NT|A|NZ|C)\$[[:space:]]?[0-9]'\
'|\$[[:space:]]?[0-9]([0-9]|[.,][0-9]|[kKmMbB])'\
'|(€|£|¥|￥|＄|₹|₩|฿)[[:space:]]?[0-9]'\
'|(人民币|港币|港元|美元|令吉|马币|新币|新元|日元|欧元|英镑|台币)[[:space:]]?[0-9]'\
'|[0-9][0-9.,]*[[:space:]]?(k|m|b|mn|bn|mil|million|billion|juta|jt|ribu)?[[:space:]]?('"$CC_CUR"'|ringgit|dollars?|yuan|euros?|baht|rupiah|rupees?|yen|pesos?)($|[^A-Za-z])'\
'|[0-9][0-9.,]*[[:space:]]?(万|千|百万|亿)?('"$CC_ZH_CUR"')'\
'|('"$CC_ZH_NUM"')('"$CC_ZH_NUM"')+(元|块钱|令吉|美元|欧元|日元|英镑|港币)'
CC_RE_EMAIL='[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}'
# The bare-digit phone forms (leading 0, or a CN mobile 1[3-9]...) must not touch an ASCII letter
# or digit on either side: inside a hex hash or commit id ("...b057562589b...") they are not phone
# numbers, and eval run files carry dozens of sha256 values (about 1 in 20 matched before).
CC_RE_PHONE='\+[0-9]{1,3}[ .-]?(\(?[0-9]{1,4}\)?[ .-]?)?[0-9]{3,4}[ .-]?[0-9]{4}([^0-9]|$)'\
'|(^|[^0-9A-Za-z])\(?0[0-9]{1,3}\)?[ .-]?[0-9]{3,4}[ .-]?[0-9]{3,4}([^0-9A-Za-z]|$)'\
'|(^|[^0-9A-Za-z])1[3-9][0-9][ -]?[0-9]{4}[ -]?[0-9]{4}([^0-9A-Za-z]|$)'\
'|(^|[^0-9])(\([0-9]{3}\) ?|[0-9]{3}[.-])[0-9]{3}[.-][0-9]{4}([^0-9]|$)'

cc_pattern_label() {  # CC_R = which pattern(s) a reported line matches (same ERE in bash)
  local s=$1
  CC_R=
  shopt -s nocasematch
  [[ $s =~ $CC_RE_MONEY ]] && CC_R+="money amount, "
  [[ $s =~ $CC_RE_EMAIL ]] && CC_R+="email address, "
  [[ $s =~ $CC_RE_PHONE ]] && CC_R+="phone number, "
  shopt -u nocasematch
  if [ -n "$CC_R" ]; then CC_R=${CC_R%, }; else CC_R="money/email/phone pattern"; fi
}

cc_pattern_spec() {  # pathspec of the money/email/phone check in this mode
  if [ "$CC_MODE" = private ]; then
    CC_PAT_SPEC=(':(icase)knowledge/' ':(icase)eval/')
  else
    CC_PAT_SPEC=(. ':(exclude)*.sh' ':(exclude)*.py' ':(exclude)scripts/hooks/*')
  fi
}
cc_in_pattern_scope() {  # the same scope, for one path
  local lp=${1,,}
  if [ "$CC_MODE" = private ]; then
    [[ $lp == knowledge/* || $lp == eval/* ]]
  else
    [[ $1 != *.sh && $1 != *.py && $1 != scripts/hooks/* ]]
  fi
}

# ---- paths -------------------------------------------------------------------------------
CC_ALLOW_TEXT='README.md LICENSE .gitignore .gitattributes requirements.txt kernel/** starter/** eval/*.py eval/*.md scripts/** docs/**'

cc_path_problem() {  # CC_R = what is wrong with committing <path>; empty when allowed
  local p=$1 lp=${1,,}
  CC_R=
  if [[ $p == *[[:cntrl:]]* ]]; then CC_R='file name contains a control character (tab, line break)'; return; fi
  case $lp in
    private/readme.md) ;;
    private/*) CC_R='private/ never leaves this machine (only private/README.md may be committed)'; return ;;
  esac
  case $lp in
    .resource/*|*/.resource/*) CC_R='.resource/ is local-only'; return ;;
    *.local.md) CC_R='*.local.md files are local-only'; return ;;
  esac
  [ "$CC_MODE" = public ] || return 0
  case $p in
    README.md|LICENSE|.gitignore|.gitattributes|requirements.txt) return ;;
    kernel/*|starter/*|scripts/*|docs/*) return ;;
    eval/*) [[ ${p#eval/} != */* && ( $p == *.py || $p == *.md ) ]] && return ;;
  esac
  CC_R="not in the public allowlist: $CC_ALLOW_TEXT"
}

cc_needs_exec() {  # public repo: files git or the user must be able to execute
  case $1 in
    scripts/hooks/pre-commit|scripts/hooks/pre-push) return 0 ;;
    scripts/*.sh) [[ ${1#scripts/} != */* ]] && return 0 ;;          # not scripts/hooks/lib.sh
  esac
  return 1
}
cc_text_name() {  # a name that says "text": binary content there is UTF-16 or a mistake
  case ${1,,} in
    *.md|*.txt|*.yaml|*.yml|*.json|*.csv|*.tsv|*.ini|*.cfg|*.toml|*.xml|*.html|*.htm) return 0 ;;
  esac
  return 1
}

# ---- listing: git diff --raw --numstat -z ------------------------------------------------
# CC_P = paths in git's order; CC_P_MODE / CC_P_OLD / CC_P_NEW = new mode, old and new blob;
# CC_P_BIN[path]=1 for files git treats as binary (exactly what `git grep -I` skips).
cc_parse_listing() {  # <array name>
  local -n _cc_tok=$1
  local n=${#_cc_tok[@]} i=0 t p a rest
  CC_P=(); CC_P_MODE=(); CC_P_OLD=(); CC_P_NEW=(); CC_P_BIN=()
  while (( i < n )); do
    t=${_cc_tok[i]}
    if [[ $t == :* ]]; then
      p=${_cc_tok[i+1]-}; i=$(( i + 2 ))
      [ -z "$p" ] && continue
      CC_P+=("$p"); CC_P_MODE[$p]=${t:8:6}
      rest=${t#:* * }; CC_P_OLD[$p]=${rest%% *}; rest=${rest#* }; CC_P_NEW[$p]=${rest%% *}
    else
      i=$(( i + 1 )); [ -z "$t" ] && continue
      a=${t%%$'\t'*}; rest=${t#*$'\t'}; p=${rest#*$'\t'}
      [ "$a" = - ] && [ -n "$p" ] && CC_P_BIN[$p]=1
    fi
  done
}

CC_LOC_SFX=      # appended to each location (pre-push: the commit an older path came from)
CC_NO_EXEC=0     # 1 = skip the exec-bit rule (older commits: a later commit may have fixed it)
cc_check_paths() {  # <how to list the paths> : path, binary, exec-bit and file-name checks on CC_P
  local hint=$1 p lp k=0 i loc
  for p in "${CC_P[@]}"; do
    k=$(( k + 1 ))
    loc=$p$CC_LOC_SFX
    lp=${p,,}
    for i in "${!CC_TERMS_LC[@]}"; do            # a name holding a term is never printed
      if [[ $lp == *"${CC_TERMS_LC[i]}"* ]]; then
        loc="(path #$k of: $hint)"
        cc_block "$loc" "file name contains a term from ${CC_TERM_REF[i]}"
        break
      fi
    done
    cc_path_problem "$p"
    [ -n "$CC_R" ] && cc_block "$loc" "$CC_R"
    if [ -n "${CC_P_BIN[$p]-}" ]; then
      if [ "$CC_MODE" = public ]; then
        cc_block "$loc" "binary file (the public repo takes text only; binary content cannot be scanned)"
      elif cc_in_pattern_scope "$p"; then
        cc_block "$loc" "binary or UTF-16 file in a scanned folder cannot be checked - save it as UTF-8 text"
      elif cc_text_name "$p"; then
        cc_block "$loc" "UTF-16 or binary content in a text file cannot be checked for blocklist terms - save it as UTF-8"
      fi
    fi
    case ${CC_P_MODE[$p]-} in
      120000) cc_block "$loc" "symlink: git grep never reads a link's target, so it cannot be checked - commit a regular file" ;;
      160000) [ "$CC_MODE" = public ] && cc_block "$loc" "submodule (gitlink): the public repo takes plain files only" ;;
    esac
    if [ "$CC_MODE" = public ] && (( ! CC_NO_EXEC )) && cc_needs_exec "$p" && [ "${CC_P_MODE[$p]-}" = 100644 ]; then
      if [ "$loc" = "$p" ]; then cc_block "$loc" "not executable in git - run: git update-index --chmod=+x -- $p"
      else cc_block "$loc" "not executable in git - run git update-index --chmod=+x on it"; fi
    fi
  done
}

# ---- content scan ------------------------------------------------------------------------

cc_scan_spawn() {  # cc_scan_spawn --cached | <commit>...   (after cc_load_blocklists)
  local t; local -a e=()
  for t in "${CC_TERMS[@]}"; do e+=(-e "$t"); done
  (( ${#e[@]} )) && cc_spawn bl "${CC_GREP[@]}" -i -F "${e[@]}" "$@" --
  cc_pattern_spec
  cc_spawn pat "${CC_GREP[@]}" -i -E -e "$CC_RE_MONEY" -e "$CC_RE_EMAIL" -e "$CC_RE_PHONE" "$@" -- "${CC_PAT_SPEC[@]}"
}

cc_grep_hits() {  # <array name>: `git grep -z -n` output -> CC_H_PATH / CC_H_LINE / CC_H_TEXT
  local -n _cc_g=$1
  local n=${#_cc_g[@]} k=2 path line chunk
  CC_H_PATH=(); CC_H_LINE=(); CC_H_TEXT=()
  (( n >= 3 )) || return 0
  path=${_cc_g[0]}; line=${_cc_g[1]}
  while (( k < n )); do
    chunk=${_cc_g[k]}
    CC_H_PATH+=("$path"); CC_H_LINE+=("$line"); CC_H_TEXT+=("${chunk%%$'\n'*}")
    [[ $chunk == *$'\n'?* ]] || break
    path=${chunk#*$'\n'}; line=${_cc_g[k+1]-?}; k=$(( k + 2 ))
  done
}

cc_safe_path() {  # CC_R = <path>, or a placeholder when the path itself holds a blocklist term
  local lp=${1,,} i
  CC_R=$1
  for i in "${!CC_TERMS_LC[@]}"; do
    if [[ $lp == *"${CC_TERMS_LC[i]}"* ]]; then CC_R="[path hidden: its name holds a blocklist term]"; return 0; fi
  done
}

cc_where() {  # CC_R = display location: path:line, plus the commit for tree scans
  if [ "$CC_SCAN_SRC" = tree ] && [[ $1 == *:* ]]; then
    cc_safe_path "${1#*:}"; CC_R="$CC_R:$2 (commit ${1:0:10})"
  else
    cc_safe_path "$1"; CC_R="$CC_R:$2"
  fi
}

cc_scan_collect() {
  local -a out=(); local rc i loc
  if cc_spawned bl; then
    cc_collect bl out nul; rc=$?
    if (( rc == 0 )); then
      cc_grep_hits out
      for i in "${!CC_H_PATH[@]}"; do
        cc_where "${CC_H_PATH[i]}" "${CC_H_LINE[i]}"; loc=$CC_R
        cc_term_ref "${CC_H_TEXT[i]}"; cc_block "$loc" "$CC_R"
      done
    elif (( rc != 1 )); then
      cc_block "(blocklist check)" "git grep failed (status $rc)"
    fi
  fi
  out=()
  cc_collect pat out nul; rc=$?
  if (( rc == 0 )); then
    cc_grep_hits out
    for i in "${!CC_H_PATH[@]}"; do
      cc_where "${CC_H_PATH[i]}" "${CC_H_LINE[i]}"; loc=$CC_R
      cc_pattern_label "${CC_H_TEXT[i]}"; cc_block "$loc" "$CC_R"
    done
  elif (( rc != 1 )); then
    cc_block "(money/email/phone check)" "git grep failed (status $rc)"
  fi
}

cc_init() {  # cc_init <hook name>
  CC_HOOK=$1
  if (( BASH_VERSINFO[0] < 4 || ( BASH_VERSINFO[0] == 4 && BASH_VERSINFO[1] < 4 ) )); then
    printf 'commcoach %s: BLOCKED - needs bash >= 4.4 (found %s)\n' "$CC_HOOK" "$BASH_VERSION" >&2
    exit 1
  fi
  # git runs hooks from the top of the work tree; ask git only when that is not the case
  if [ ! -e "$PWD/.git" ]; then
    CC_TOP=$(git rev-parse --show-toplevel) && cd "$CC_TOP" || exit 1
  fi
  CC_TOP=$PWD
}
