"""Tests for eval/run_eval.py - stdlib unittest only.

No network except a fake Messages API on 127.0.0.1 (ports 18800-18849). The cli backend runs against a
fake `claude` written into a temp folder; no real claude or Anthropic API is ever called.

  python -m unittest eval/test_run_eval.py          (from the framework folder)
  python framework/eval/test_run_eval.py            (from anywhere)
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap
import threading
import unittest
from decimal import Decimal
from fractions import Fraction
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_eval as R  # noqa: E402

RUN_EVAL = HERE / "run_eval.py"
PORTS = range(18800, 18850)
HAVE_SDK = importlib.util.find_spec("anthropic") is not None
SCRUB = ("COMM_COACH_DATA", "EVAL_MODEL", "EVAL_EFFORT", "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CONFIG_DIR",
         "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN", "PYTHONIOENCODING", "PYTHONUTF8",
         "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")

KERNEL = textwrap.dedent("""\
    # Test kernel

    Version 9.1 · test

    ## 3. Modes
    Reply within 24 hours of any 1:1.

    ## 11. Calibration examples

    **LIVE - vendor**
    User: On a call now. The vendor says the 12% surcharge is final, take it or leave it.
    Coach:
    Say: "Help me understand what's driving the 12%."

    **DRAFT - chasing**
    **User:** Rewrite: "Hi, just checking on the invoices, we really need them, please send asap thanks."
    Coach:
    "Hi [confirm: name] - can you send the invoices by [confirm: time]?"

    ## 12. Memory
    Nothing here.
    """)

FAKE_CLAUDE = r'''
import json, os, sys, time
args = sys.argv[1:]
if args[:1] == ["--version"]:
    if os.environ.get("FAKE_VERSION_FAIL"):
        sys.stderr.write("broken install\n")
        sys.exit(5)
    sys.stdout.write("9.9.9 (Claude Code)\n")
    sys.exit(0)
prompt = sys.stdin.buffer.read().decode("utf-8")
def arg(name):
    return args[args.index(name) + 1] if name in args else None
sp = arg("--system-prompt-file")
system = open(sp, encoding="utf-8").read() if sp else None
cfg = os.environ.get("CLAUDE_CONFIG_DIR")
log = os.environ.get("COMMCOACH_FAKE_LOG")
if log:
    with open(log, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "argv": args, "cwd": os.getcwd(), "prompt": prompt, "system": system,
            "env": sorted(k for k in os.environ if k.upper().startswith(("CLAUDE", "ANTHROPIC"))),
            "token": os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"), "config_dir": cfg,
            "config_dir_empty": (os.path.isdir(cfg) and not os.listdir(cfg)) if cfg else None,
        }) + "\n")
model = arg("--model") or "unknown"
result = {
    "type": "result", "subtype": "success", "is_error": False, "duration_ms": 12, "num_turns": 1,
    "session_id": "fake", "total_cost_usd": 0.01, "stop_reason": "end_turn",
    "usage": {"input_tokens": 40, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 600,
              "output_tokens": 30},
    "modelUsage": {model: {"inputTokens": 40, "outputTokens": 30}},
    "result": 'Say: "Fair point. Can we pick this up at [confirm: time]?"\nWhy: buys time.',
}
code = 0
if "#auth" in prompt:
    result.update(is_error=True, result="Not logged in - Please run /login", total_cost_usd=0)
    code = 1
elif "#empty" in prompt:
    result["result"] = ""
elif "#truncate" in prompt:
    result.update(result='Say: "We could', stop_reason="max_tokens")
elif "#crash" in prompt:
    sys.stderr.write("boom\n")
    sys.exit(3)
elif "#slow" in prompt:
    time.sleep(60)
elif "#zh" in prompt:
    result["result"] = "Send: \"\u597d\u7684\uff0c\u6211\u4eec\u4eca\u5929\u786e\u8ba4\u3002\"\nWhy: \u5148\u7a33\u4f4f\u3002 \uff13\uff05"
elif "#bigcontext" in prompt:
    result["usage"]["cache_read_input_tokens"] = 90000
sys.stdout.buffer.write(json.dumps(result, ensure_ascii=False).encode("utf-8"))
sys.exit(code)
'''


def clean_env(**extra: str) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in SCRUB}
    env.update(extra)
    return env


def call(argv: list[str], env: dict | None = None) -> tuple[int, str, str]:
    """Run run_eval.main in-process with a clean environment."""
    out, err = io.StringIO(), io.StringIO()
    with mock.patch.dict(os.environ, env if env is not None else clean_env(), clear=True), \
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = R.main(argv)
        except SystemExit as exc:
            code = exc.code
    return code, out.getvalue(), err.getvalue()


def sit(sid: int, prefix: str = "L:", text: str | None = None, **extra) -> dict:
    entry = {"id": sid, "prefix": prefix,
             "situation": text if text is not None else f"Colleague number {sid} wants a quick answer on the rota."}
    entry.update(extra)
    return entry


def full_set() -> list[dict]:
    """20 synthetic situations that meet every coverage minimum."""
    plan = (["L:"] * 4 + ["P:"] * 5 + ["D:"] * 5 + ["X:"] * 3 + ["R:"] * 1)
    tag_cycle = [["up"], ["across"], ["out"], ["personal"], ["personal"], ["no-reply"], ["channel-switch"],
                 ["push-back"], ["push-back"], ["safety"], ["safety"], ["audience"], ["zh"], ["zh"], ["zh"],
                 ["down"], [], [], []]
    rows = [sit(i + 1, p, f"Topic {chr(65 + i)} needs handling with the {chr(97 + i)} team today.",
                tags=tag_cycle[i]) for i, p in enumerate(plan)]
    rows.append(sit(19, "", "They just asked me across the table whether the plan still holds.", expect="live"))
    rows.append(sit(20, "", "Next month I have to ask the committee for more time. How should I approach it?",
                    expect="prep"))
    return rows


class Workspace:
    """A temp data folder: knowledge/, eval/situations.yaml, eval/runs/, and a kernel file."""

    def __init__(self, testcase: unittest.TestCase, situations: list | str | None = None,
                 kernel: str = KERNEL, knowledge: dict | None = None):
        tmp = tempfile.TemporaryDirectory(prefix="rev-test-")
        testcase.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.data = self.root / "data"
        (self.data / "knowledge").mkdir(parents=True)
        (self.data / "eval" / "runs").mkdir(parents=True)
        self.kernel = self.root / "instructions.md"
        self.kernel.write_text(kernel, encoding="utf-8", newline="\n")
        for name, text in (knowledge if knowledge is not None else {
                "norms.md": "Replies within 2 days are normal here.\n",
                "secret.local.md": "LOCAL-ONLY-CANARY\n"}).items():
            (self.data / "knowledge" / name).write_text(text, encoding="utf-8", newline="\n")
        self.write_situations(situations if situations is not None else [sit(1)])
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.fake = self.bin / "fake_claude.py"
        self.fake.write_text(FAKE_CLAUDE, encoding="utf-8", newline="\n")
        self.log = self.root / "fake.log"

    def write_situations(self, situations: list | str) -> None:
        text = situations if isinstance(situations, str) else yaml.safe_dump(situations, allow_unicode=True,
                                                                              sort_keys=False)
        (self.data / "eval" / "situations.yaml").write_text(text, encoding="utf-8", newline="\n")

    def base(self) -> list[str]:
        return ["--data", str(self.data), "--kernel", str(self.kernel)]

    def calls(self) -> list[dict]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines() if line.strip()]

    def runs(self) -> list[Path]:
        return sorted((self.data / "eval" / "runs").glob("*.yaml"))

    def env(self, **extra: str) -> dict:
        return clean_env(COMMCOACH_FAKE_LOG=str(self.log), **extra)

    def path_shim(self) -> None:
        """A `claude` on PATH: an npm-style claude.cmd on Windows, an executable script elsewhere."""
        if os.name == "nt":
            (self.bin / "claude.cmd").write_text('@ECHO off\r\n"%dp0%\\fake_claude.py"   %*\r\n', encoding="utf-8")
        else:
            script = self.bin / "claude"
            script.write_text(f"#!{sys.executable}\n" + FAKE_CLAUDE, encoding="utf-8")
            script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    def run_file(self, rows: list[dict], name: str = "2026-01-01_0000_v9.1_cli.yaml", **top) -> Path:
        payload = {"status": "complete", "date": "2026-01-01", "backend": "cli", "model_requested": "m-1",
                   "kernel_version": "9.1", "kernel_sha256": R.sha256(R.read_text(self.kernel))}
        payload.update(top)
        payload["results"] = rows
        path = self.data / "eval" / "runs" / name
        path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
        return path


def scored(situations: list[dict], score: int = 5, truth: str = "pass") -> list[dict]:
    return [{"id": s["id"], "prefix": s["prefix"], "situation": s["situation"], "response": "Say: \"ok\"",
             "error": None, "do_not_score": False,
             "scores": {k: score for k in R.SCORED}, "truth": truth} for s in situations]


# --------------------------------------------------------------------------- loader

class LoaderTests(unittest.TestCase):
    def test_valid_entries_and_defaults(self):
        sits = R.validate_situations([sit(1), sit(2, None, "Where do we stand?", expect="debrief", tags=["zh"])], "t")
        self.assertEqual([s.mode for s in sits], ["live", "debrief"])
        self.assertEqual(sits[1].prefix, "")
        self.assertEqual(sits[1].prompt, "Where do we stand?")
        self.assertEqual(sits[0].prompt, sits[0].prefix + " " + sits[0].situation)

    def test_every_error_is_reported_with_entry_and_id(self):
        raw = [sit(1), sit(1), {"id": "3", "prefix": "L:", "situation": "x"}, {"id": True, "situation": "x"},
               {"id": 5, "prefix": "L:"}, sit(6, "Q:"), sit(7, "l:"), sit(8, tag=["up"]), sit(9, tags=["boss"]),
               sit(10, ""), sit(11, "L:", expect="draft"), sit(12, "", "L: on a call", expect="live"),
               sit(13, tags="up")]
        with self.assertRaises(R.InputError) as ctx:
            R.validate_situations(raw, "sits.yaml")
        msg = str(ctx.exception)
        for needle in ("entry #2 (id 1): duplicate id", "entry #3: id must be a positive integer (got '3')",
                       "entry #4: id must be a positive integer (got True)", "(id 5): situation must be non-empty",
                       "(id 6): prefix 'Q:'", "(id 7): prefix 'l:'", "(id 8): unknown key(s) tag",
                       "(id 9): unknown tag(s) 'boss'", "(id 10): an unprefixed situation", "(id 11): expect 'draft'",
                       "(id 12): the situation starts with a prefix", "(id 13): tags must be a list"):
            self.assertIn(needle, msg)

    def test_top_level_shapes(self):
        for raw, needle in ((None, "empty"), ({"id": 1}, "expected a list"), ([], "no situations"),
                            (["just text"], "expected a mapping")):
            with self.assertRaises(R.InputError) as ctx:
                R.validate_situations(raw, "t")
            self.assertIn(needle, str(ctx.exception))

    def test_yaml_error_names_line_and_hint(self):
        ws = Workspace(self, situations="- id: 1\n  prefix: L:\n  situation: x\n")
        code, out, err = call(ws.base() + ["check"])
        self.assertEqual(code, 2)
        self.assertIn("line 2", err)
        self.assertIn("quote prefixes", err)

    def test_select_ids(self):
        sits = R.validate_situations([sit(1), sit(2), sit(3)], "t")
        self.assertEqual([s.id for s in R.select_ids(sits, [3, 1, 3])], [1, 3])
        self.assertEqual(len(R.select_ids(sits, None)), 3)
        with self.assertRaises(R.InputError) as ctx:
            R.select_ids(sits, [1, 99])
        self.assertIn("no situation with id 99", str(ctx.exception))


# --------------------------------------------------------------------------- auto flags

class AutoFlagTests(unittest.TestCase):
    def S(self, prefix="L:", text="He wants an answer.", expect=None):
        entry = sit(1, prefix, text)
        if expect:
            entry["expect"] = expect
        return R.validate_situations([entry], "t")[0]

    def test_live_labels_say_or_send(self):
        self.assertEqual(R.auto_flags(self.S(), 'Say: "Sure."\nWhy: fine.'), [])
        self.assertEqual(R.auto_flags(self.S(), '**Send:** "Sure."'), [])
        self.assertIn("LIVE reply has no 'Say:' or 'Send:' line", R.auto_flags(self.S(), "Just agree."))

    def test_live_rules_apply_to_unprefixed_expect_live(self):
        flags = R.auto_flags(self.S("", "He just asked me.", "live"), "word " * 90)
        self.assertTrue(any("LIVE reply is ~90 words" in f for f in flags))
        self.assertTrue(any("no 'Say:'" in f for f in flags))

    def test_chinese_length_counts_characters(self):
        flags = R.auto_flags(self.S(), "Say: \"" + "\u597d" * 200 + "\"")
        self.assertTrue(any("~101 words" in f and "Chinese" in f for f in flags), flags)

    def test_empty_and_mode_announcement_and_prep_length(self):
        self.assertTrue(R.auto_flags(self.S(), "  ")[0].startswith("EMPTY reply"))
        self.assertTrue(any("announces the mode" in f for f in R.auto_flags(self.S(), "LIVE mode. Say: \"x\"")))
        self.assertTrue(any("PREP reply is ~260 words" in f
                            for f in R.auto_flags(self.S("P:"), "plan " * 260)))

    def test_numbers_quiet_on_lists_placeholders_and_formats(self):
        s = self.S("P:", "They want 8% more on a 1,000 unit order by 3pm.")
        reply = ("1. Open with the 8 percent.\n2. Anchor on 1000 units.\n3) Close by 3:00.\n"
                 "Deliver by [confirm: 5pm Friday]. Keep it 1:1, Q3 plan.")
        self.assertEqual(R.auto_flags(s, reply), [])

    def test_numbers_flag_inventions_including_fullwidth(self):
        s = self.S("D:", "Reply to the price email.")
        flags = R.auto_flags(s, 'Send: "We can do \uff13\uff05 by the 14th."')
        self.assertEqual(len(flags), 1)
        self.assertIn("3, 14", flags[0])

    def test_numbers_from_knowledge_are_supplied(self):
        s = self.S("D:", "Reply to the chaser.")
        self.assertEqual(R.auto_flags(s, 'Send: "Within 2 days."', supplied="Replies within 2 days."), [])


# --------------------------------------------------------------------------- leakage, check, coverage

class LeakageAndCheckTests(unittest.TestCase):
    def test_copy_and_paraphrase_are_leaks(self):
        sits = R.validate_situations([
            sit(1, "L:", "On a call now. The vendor says the 12% surcharge is final, take it or leave it."),
            sit(2, "D:", "Rewrite for chat: 'Hi, just checking on the receipts, we really need them, please send asap thanks.'"),
            sit(3, "L:", "My landlord wants the flat back early and I am on the phone with him."),
        ], "t")
        leaks, note = R.find_leakage(sits, KERNEL)
        self.assertEqual(sorted(x.situation_id for x in leaks), [1, 2])
        self.assertIn("2 example user line", note)

    def test_kernel_without_section_11_warns(self):
        sits = R.validate_situations([sit(1)], "t")
        leaks, note = R.find_leakage(sits, "# K\n\nVersion 1.0\n\n## 1. Mission\nx\n")
        self.assertEqual(leaks, [])
        self.assertIn("not checked", note)

    def test_section_is_read_at_run_time(self):
        line = "My sister keeps cancelling our lunch plans at the last minute and I'm annoyed."
        kernel = KERNEL.replace("On a call now. The vendor says the 12% surcharge is final, take it or leave it.", line)
        sits = R.validate_situations([sit(1, "L:", line)], "t")
        self.assertEqual(len(R.find_leakage(sits, kernel)[0]), 1)
        self.assertEqual(len(R.find_leakage(sits, KERNEL)[0]), 0)

    def test_check_exit_codes(self):
        ws = Workspace(self, situations=full_set())
        code, out, _ = call(ws.base() + ["check"])
        self.assertEqual(code, 0, out)
        self.assertIn("Coverage:   OK", out)
        ws.write_situations([sit(1), sit(2)])
        code, out, _ = call(ws.base() + ["check"])
        self.assertEqual(code, 1)
        self.assertIn("GAP total", out)
        self.assertEqual(call(ws.base() + ["check", "--schema-only"])[0], 0)
        ws.write_situations([sit(1, "L:", "On a call now. The vendor says the 12% surcharge is final, take it or leave it.")])
        code, out, _ = call(ws.base() + ["check"])
        self.assertEqual(code, 2)
        self.assertIn("matches a kernel calibration example", out)
        ws.write_situations([sit(1), sit(1)])
        self.assertEqual(call(ws.base() + ["check"])[0], 2)

    def test_coverage_counts(self):
        counts = R.coverage_counts(R.validate_situations(full_set(), "t"))
        self.assertEqual((counts["total"], counts["mode:live"], counts["mode:prep"], counts["unprefixed"]), (20, 5, 6, 2))
        self.assertEqual((counts["zh"], counts["tag:no-reply|channel-switch"]), (3, 2))
        zh = R.validate_situations([sit(1, "D:", "\u5e2e\u6211\u56de\u590d\u4ed6\u3002")], "t")
        self.assertEqual(R.coverage_counts(zh)["zh"], 1)

    def test_eval_set_md_table_matches_code(self):
        text = (HERE / "eval-set.md").read_text(encoding="utf-8")
        table = {m.group(1).replace("\\|", "|"): int(m.group(2))     # \| is a pipe escaped for the table
                 for m in re.finditer(r"^\|\s*`([^`]+)`\s*\|\s*(\d+)\s*\|", text, re.M)}
        self.assertEqual(table, {key: minimum for key, minimum, _ in R.COVERAGE})

    def test_shipped_kernel_and_starter_set(self):
        code, out, err = call(["check"])
        self.assertIn(code, (0, 1), out + err)
        self.assertIn("Schema:     OK", out)
        self.assertIn("Leakage:    OK", out)
        starter = R.load_situations(R.DEFAULT_DATA / "eval" / "situations.yaml")
        self.assertTrue(8 <= len(starter) <= 10)
        self.assertEqual({s.mode for s in starter}, set(R.MODES))
        self.assertGreaterEqual(sum(1 for s in starter if not s.prefix), 2)


# --------------------------------------------------------------------------- score gate

class ScoreGateTests(unittest.TestCase):
    def setUp(self):
        self.ws = Workspace(self, situations=full_set())
        self.log = self.ws.data / "eval" / "runs" / "LOG.md"

    def score(self, path: Path, *extra: str) -> tuple[int, str, str]:
        return call(self.ws.base() + list(extra) + ["score", str(path)])

    def test_pass_logs_and_prints_tag(self):
        path = self.ws.run_file(scored(full_set()))
        code, out, _ = self.score(path)
        self.assertEqual(code, 0, out)
        self.assertIn("git tag -a v9.1", out)
        raw = self.log.read_bytes()
        self.assertNotIn(b"\r", raw)
        text = raw.decode("utf-8")
        self.assertIn(R.LOG_HEADER, text)
        self.assertEqual(text.count(path.name), 1)
        self.assertIn("| 5.00 | 0 | PASS |", text)

    def test_rescore_replaces_the_row(self):
        path = self.ws.run_file(scored(full_set()))
        self.assertEqual(self.score(path)[0], 0)
        self.ws.run_file(scored(full_set(), score=3), name=path.name)
        code, out, _ = self.score(path)
        self.assertEqual(code, 1)
        text = self.log.read_text(encoding="utf-8")
        self.assertEqual(text.count(path.name), 1)
        self.assertIn("| 3.00 | 0 | BELOW |", text)

    def test_below_target_and_truth_fail(self):
        rows = scored(full_set())
        rows[0]["scores"] = {k: 1 for k in R.SCORED}    # 76 fives + 4 ones over 80 -> 4.80, still fine
        rows[0]["truth"] = "FAIL "
        code, out, _ = self.score(self.ws.run_file(rows))
        self.assertEqual(code, 1)
        self.assertIn("Below target", out)
        rows = scored(full_set(), score=4)
        rows[0]["scores"]["outcome"] = 3                 # 319/80 = 3.9875 -> 3.99
        code, out, _ = self.score(self.ws.run_file(rows, name="b.yaml"))
        self.assertEqual(code, 1)
        self.assertIn("overall 3.99", out)

    def test_threshold_uses_the_rounded_value(self):
        self.assertLess(sum([37 / 9, 37 / 9, 33 / 9, 37 / 9]) / 4, 4.0)          # the float trap
        self.assertEqual(R.round2(Fraction(37 + 37 + 33 + 37, 36)), Decimal("4.00"))
        self.assertEqual(R.round2(Fraction(799, 200)), Decimal("4.00"))           # 3.995 rounds up
        self.assertEqual(R.round2(Fraction(7988, 2000)), Decimal("3.99"))        # 3.994
        code, out, _ = self.score(self.ws.run_file(scored(full_set(), score=4)))
        self.assertEqual(code, 0, out)

    def test_invalid_inputs_exit_2_and_log_nothing(self):
        cases = {
            "bool": lambda r: r[0]["scores"].update(outcome=True),
            "float": lambda r: r[0]["scores"].update(outcome=4.5),
            "string": lambda r: r[0]["scores"].update(outcome="5"),
            "zero": lambda r: r[0]["scores"].update(outcome=0),
            "six": lambda r: r[0]["scores"].update(outcome=6),
            "nan": lambda r: r[0]["scores"].update(outcome=float("nan")),
            "unscored": lambda r: r[3]["scores"].update(length=None),
            "no_scores": lambda r: r[0].pop("scores"),
            "truth_null": lambda r: r[0].update(truth=None),
            "truth_no": lambda r: r[0].update(truth=False),
            "truth_failed": lambda r: r[0].update(truth="failed"),
            "error_row": lambda r: r[0].update(error="API 500"),
            "do_not_score": lambda r: r[0].update(do_not_score="truncated"),
            "dup_id": lambda r: r[1].update(id=1),
            "id_list": lambda r: r[0].update(id=[1]),          # unhashable: must not crash the validator
            "id_map": lambda r: r[0].update(id={"n": 1}),
        }
        for name, mutate in cases.items():
            with self.subTest(name):
                rows = scored(full_set())
                mutate(rows)
                code, _, err = self.score(self.ws.run_file(rows, name=f"{name}.yaml"))
                self.assertEqual(code, 2, err)
                self.assertIn("INVALID", err)
                self.assertFalse(self.log.exists())

    def test_malformed_files(self):
        runs = self.ws.data / "eval" / "runs"
        for name, text in (("empty.yaml", ""), ("list.yaml", "- 1\n"), ("nores.yaml", "date: x\n"),
                           ("bad.yaml", "results: [\n")):
            with self.subTest(name):
                (runs / name).write_text(text, encoding="utf-8")
                self.assertEqual(self.score(runs / name)[0], 2)
        self.assertEqual(self.score(runs / "missing.yaml")[0], 2)
        self.assertFalse(self.log.exists())

    def test_partial_runs_are_not_taggable(self):
        code, out, _ = self.score(self.ws.run_file(scored(full_set()[:19])))
        self.assertEqual(code, 1)
        self.assertIn("NOT TAGGABLE", out)
        self.assertIn("1 of 20 situations are not in this run", out)
        self.assertIn("| PARTIAL |", self.log.read_text(encoding="utf-8"))

    def test_small_set_changed_text_and_changed_kernel(self):
        sits = full_set()
        rows = scored(sits)
        rows[4]["situation"] = "An older wording."
        code, out, _ = self.score(self.ws.run_file(rows, kernel_sha256="0" * 64))
        self.assertEqual(code, 1)
        self.assertIn("situation text changed since this run: ids 5", out)
        self.assertIn("the kernel changed since this run", out)
        self.ws.write_situations(sits[:4])
        code, out, _ = self.score(self.ws.run_file(scored(sits[:4]), name="small.yaml"))
        self.assertEqual(code, 1)
        self.assertIn("a tag needs at least 20", out)

    def test_kernel_provenance_gates_the_tag(self):
        kernel_hash = R.sha256(R.read_text(self.ws.kernel))
        path = self.ws.run_file(scored(full_set()), name="nohash.yaml")
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        data.pop("kernel_sha256")
        path.write_text(yaml.safe_dump(data), encoding="utf-8")
        code, out, _ = self.score(path)
        self.assertEqual(code, 1, out)
        self.assertIn("no kernel_sha256", out)
        rows = scored(full_set())
        for r in rows:
            r["meta"] = {"kernel_sha256": kernel_hash}
        rows[3]["meta"]["kernel_sha256"] = "f" * 64                  # a row pasted in from another kernel's run
        code, out, _ = self.score(self.ws.run_file(rows, name="mixed.yaml"))
        self.assertEqual(code, 1, out)
        self.assertIn("different kernel than the file header: ids 4", out)
        code, out, _ = self.score(self.ws.run_file(scored(full_set()), name="unk.yaml", kernel_version="unknown"))
        self.assertEqual(code, 1, out)
        self.assertNotIn("git tag", out)
        head = "a" * 40
        # Scored before the kernel is committed: still a pass, but the tag must not land on today's HEAD.
        with mock.patch.object(R, "committed_kernel", return_value={"head": head, "sha256": "0" * 64}):
            code, out, _ = self.score(self.ws.run_file(scored(full_set()), name="dirty.yaml"))
        self.assertEqual(code, 0, out)
        self.assertIn("does not hold this kernel yet: commit kernel/ first", out)
        self.assertNotIn(head[:12], out)
        with mock.patch.object(R, "committed_kernel", return_value={"head": head, "sha256": kernel_hash}):
            code, out, _ = self.score(self.ws.run_file(scored(full_set()), name="clean.yaml"))
        self.assertEqual(code, 0, out)
        self.assertIn(f"git tag -a v9.1 {head[:12]} -m", out)

    def test_committed_kernel_reads_head(self):
        if not shutil.which("git"):
            self.skipTest("git not installed")
        repo = self.ws.root / "fw"
        (repo / "kernel").mkdir(parents=True)
        kernel = repo / "kernel" / "instructions.md"
        kernel.write_text("# K\n\nVersion 1.0\n", encoding="utf-8", newline="\n")
        hooks = self.ws.root / "no-hooks"
        hooks.mkdir()
        git = ["git", "-C", str(repo), "-c", f"core.hooksPath={hooks}", "-c", "user.name=test",
               "-c", "user.email=test@example.invalid"]
        steps = (["init", "-q"], ["add", "kernel/instructions.md"], ["commit", "-q", "-m", "k"])
        if any(subprocess.run(git + s, capture_output=True).returncode for s in steps):
            self.skipTest("cannot make a scratch git commit here")
        with mock.patch.object(R, "FRAMEWORK", repo.resolve()):
            info = R.committed_kernel(kernel)
            self.assertEqual(info["sha256"], R.sha256(R.read_text(kernel)))
            kernel.write_text("# K\n\nVersion 1.1\n", encoding="utf-8", newline="\n")   # uncommitted edit
            self.assertNotEqual(R.committed_kernel(kernel)["sha256"], R.sha256(R.read_text(kernel)))
            self.assertIsNone(R.committed_kernel(self.ws.kernel))                      # outside the repo

    def test_log_keeps_a_preamble_and_adds_the_header(self):
        self.log.write_text("# My own notes\r\n\r\nSomething I wrote.\r\n", encoding="utf-8", newline="")
        self.score(self.ws.run_file(scored(full_set())))
        raw = self.log.read_bytes()
        self.assertNotIn(b"\r", raw)
        text = raw.decode("utf-8")
        self.assertTrue(text.startswith("# My own notes\n\nSomething I wrote.\n\n" + R.LOG_HEADER))

    def test_runfile_resolution_and_data_inference(self):
        path = self.ws.run_file(scored(full_set()))
        self.assertEqual(call(self.ws.base() + ["score", path.name])[0], 0)
        code, out, _ = call(["--kernel", str(self.ws.kernel), "score", str(path)])   # data from the path
        self.assertEqual(code, 0, out)


# --------------------------------------------------------------------------- cli backend

class CliBackendTests(unittest.TestCase):
    def test_end_to_end_with_fake_claude(self):
        ws = Workspace(self, situations=[
            sit(1), sit(2, "D:", "Reply in Chinese please #zh \u8c22\u8c22"), sit(3, "P:", "Plan it #empty"),
            sit(4, "X:", "How did it go #truncate")])
        env = ws.env(CLAUDE_CODE_OAUTH_TOKEN="tok-test", ANTHROPIC_API_KEY="must-not-leak",
                     CLAUDE_EFFORT="xhigh", ANTHROPIC_BASE_URL="http://example.invalid")
        code, out, err = call(ws.base() + ["run", "--claude-bin", str(ws.fake)], env)
        self.assertEqual(code, 1, out + err)       # rows 3 and 4 cannot be scored
        calls = ws.calls()
        self.assertEqual(len(calls), 4)
        for c in calls:
            argv = c["argv"]
            self.assertEqual(argv[argv.index("--tools") + 1], "")
            self.assertEqual(argv[argv.index("--model") + 1], "claude-sonnet-5-5")
            self.assertEqual(argv[argv.index("--effort") + 1], "high")
            self.assertEqual(argv[argv.index("--output-format") + 1], "json")
            for flag in ("-p", "--system-prompt-file", "--strict-mcp-config", "--no-session-persistence",
                         "--disable-slash-commands"):
                self.assertIn(flag, argv)
            for flag in ("--bare", "--fallback-model", "--append-system-prompt"):
                self.assertNotIn(flag, argv)
            self.assertEqual(c["env"], ["CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CONFIG_DIR"])
            self.assertEqual(c["token"], "tok-test")
            self.assertTrue(c["config_dir_empty"])
            self.assertTrue(R.outside_ch(Path(c["cwd"])))
            self.assertNotIn(str(ws.data.resolve()), str(Path(c["cwd"]).resolve()))
            self.assertIn("## 11. Calibration examples", c["system"])
            self.assertIn('<file name="norms.md">', c["system"])
            self.assertNotIn("LOCAL-ONLY-CANARY", c["system"])
        self.assertEqual(calls[1]["prompt"], "D: Reply in Chinese please #zh \u8c22\u8c22")
        [path] = ws.runs()
        self.assertNotIn(b"\r", path.read_bytes())
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        self.assertEqual((data["status"], data["backend"], data["cli_auth"]), ("complete", "cli", "oauth-token"))
        self.assertEqual(data["backend_version"], "9.9.9 (Claude Code)")
        self.assertEqual(data["kernel_version"], "9.1")
        self.assertEqual(data["knowledge_skipped"], ["secret.local.md"])
        r1, r2, r3, r4 = data["results"]
        self.assertEqual(r1["meta"]["model_served"], ["claude-sonnet-5-5"])
        self.assertEqual(r1["meta"]["stop_reason"], "end_turn")
        self.assertEqual(r1["meta"]["usage"]["cache_read_input_tokens"], 600)
        self.assertEqual(r1["meta"]["kernel_sha256"], data["kernel_sha256"])
        self.assertFalse(r1["do_not_score"])
        self.assertIn("\u597d\u7684", r2["response"])
        self.assertTrue(any("3" in f for f in r2["auto_flags"]))        # fullwidth 3% not in the prompt
        self.assertEqual(r3["do_not_score"], "empty reply")
        self.assertTrue(r4["auto_flags"][0].startswith("TRUNCATED"))
        self.assertEqual(r4["do_not_score"], "truncated")
        self.assertEqual(r1["scores"], {k: None for k in R.SCORED})

    def test_claude_found_on_path(self):
        ws = Workspace(self)
        ws.path_shim()
        env = ws.env(PATH=str(ws.bin) + os.pathsep + os.environ.get("PATH", ""))
        code, out, err = call(ws.base() + ["run"], env)
        self.assertEqual(code, 0, out + err)
        self.assertEqual(len(ws.calls()), 1)

    def test_auth_error_aborts_after_one_call(self):
        ws = Workspace(self, situations=[sit(1, "L:", "first #auth"), sit(2), sit(3)])
        code, out, err = call(ws.base() + ["run", "--claude-bin", str(ws.fake)], ws.env(CLAUDE_CODE_OAUTH_TOKEN="t"))
        self.assertEqual(code, 1)
        self.assertEqual(len(ws.calls()), 1)
        self.assertIn("claude setup-token", err)
        data = yaml.safe_load(ws.runs()[0].read_text(encoding="utf-8"))
        self.assertTrue(data["status"].startswith("aborted"))
        self.assertIn("Not logged in", data["results"][0]["error"])
        self.assertTrue(data["results"][0]["meta"]["is_error"])

    def test_without_token_uses_the_terminal_login(self):
        ws = Workspace(self)
        cfg = ws.root / "cfg"
        cfg.mkdir()
        code, out, err = call(ws.base() + ["run", "--claude-bin", str(ws.fake)], ws.env(CLAUDE_CONFIG_DIR=str(cfg)))
        self.assertEqual(code, 0, out + err)
        self.assertIn("CLAUDE_CODE_OAUTH_TOKEN is not set", err)
        [c] = ws.calls()
        self.assertEqual(c["config_dir"], str(cfg))
        self.assertIsNone(c["token"])
        self.assertEqual(yaml.safe_load(ws.runs()[0].read_text(encoding="utf-8"))["cli_auth"], "inherited-login")

    def test_token_with_a_line_break_is_refused(self):
        ws = Workspace(self)
        code, _, err = call(ws.base() + ["run", "--claude-bin", str(ws.fake)],
                            ws.env(CLAUDE_CODE_OAUTH_TOKEN="abc\ndef"))
        self.assertEqual(code, 2)
        self.assertIn("copy button", err)
        self.assertEqual(ws.calls(), [])

    def test_unknown_or_empty_ids_send_nothing(self):
        ws = Workspace(self)
        code, _, err = call(ws.base() + ["run", "--ids", "99", "--claude-bin", str(ws.fake)], ws.env())
        self.assertEqual(code, 2)
        self.assertIn("no situation with id 99", err)
        self.assertEqual(call(ws.base() + ["run", "--ids"], ws.env())[0], 2)
        self.assertEqual(ws.calls(), [])
        self.assertEqual(ws.runs(), [])

    def test_leaked_situation_blocks_the_run(self):
        ws = Workspace(self, situations=[
            sit(1, "L:", "On a call now. The vendor says the 12% surcharge is final, take it or leave it.")])
        code, out, err = call(ws.base() + ["run", "--claude-bin", str(ws.fake)], ws.env())
        self.assertEqual(code, 2)
        self.assertEqual(ws.calls(), [])

    def test_timeout_crash_and_error_streak(self):
        ws = Workspace(self, situations=[sit(1, "L:", "a #slow"), sit(2, "L:", "b #crash"), sit(3)])
        # 8 s: the other rows are a cold Python start each, which can pass 1-2 s on a busy Windows machine.
        code, out, err = call(ws.base() + ["run", "--timeout", "8", "--claude-bin", str(ws.fake)], ws.env())
        self.assertEqual(code, 1)
        rows = yaml.safe_load(ws.runs()[0].read_text(encoding="utf-8"))["results"]
        self.assertIn("timed out after 8s", rows[0]["error"])
        self.assertIn("exited 3 without JSON output: boom", rows[1]["error"])
        self.assertIsNone(rows[2]["error"])
        ws2 = Workspace(self, situations=[sit(i, "L:", f"x{i} #crash") for i in (1, 2, 3, 4)])
        code, _, _ = call(ws2.base() + ["run", "--claude-bin", str(ws2.fake)], ws2.env())
        self.assertEqual(code, 1)
        self.assertEqual(len(ws2.calls()), 3)
        self.assertIn("3 errors in a row", yaml.safe_load(ws2.runs()[0].read_text(encoding="utf-8"))["status"])

    def test_context_canary(self):
        ws = Workspace(self, situations=[sit(1, "L:", "hello #bigcontext")])
        code, _, _ = call(ws.base() + ["run", "--claude-bin", str(ws.fake)], ws.env())
        row = yaml.safe_load(ws.runs()[0].read_text(encoding="utf-8"))["results"][0]
        self.assertTrue(any(f.startswith("context canary") for f in row["auto_flags"]))

    def test_interrupt_keeps_completed_rows(self):
        ws = Workspace(self, situations=[sit(1), sit(2), sit(3)])
        replies = iter([R.Reply(text='Say: "one"', stop_reason="end_turn")])

        def fake_ask(self, prompt):
            try:
                return next(replies)
            except StopIteration:
                raise KeyboardInterrupt from None

        with mock.patch.object(R.CliBackend, "ask", fake_ask):
            code, _, err = call(ws.base() + ["run", "--claude-bin", str(ws.fake)], ws.env())
        self.assertEqual(code, 130)
        data = yaml.safe_load(ws.runs()[0].read_text(encoding="utf-8"))
        self.assertEqual(data["status"], "aborted: interrupted")
        self.assertEqual([r["id"] for r in data["results"]], [1])

    def test_broken_claude_stops_before_any_situation(self):
        ws = Workspace(self)
        code, _, err = call(ws.base() + ["run", "--claude-bin", str(ws.fake)], ws.env(FAKE_VERSION_FAIL="1"))
        self.assertEqual(code, 2)
        self.assertIn("claude --version` failed (exit 5): broken install", err)
        self.assertEqual(ws.calls(), [])
        self.assertEqual(ws.runs(), [])

    def test_setup_failure_cleans_the_temp_folder(self):
        ws = Workspace(self, knowledge={"k.md": "PRIVATE-KNOWLEDGE-CANARY\n"})
        runs = ws.data / "eval" / "runs"
        runs.rmdir()
        runs.write_text("a file where the runs folder should be\n", encoding="utf-8")
        tmp = ws.root / "tmp"
        tmp.mkdir()
        with mock.patch.object(tempfile, "tempdir", str(tmp)):
            code, _, err = call(ws.base() + ["run", "--claude-bin", str(ws.fake)], ws.env())
        self.assertEqual(code, 2, err)
        self.assertIn("cannot write a run file", err)
        self.assertEqual(list(tmp.iterdir()), [])          # kernel + knowledge never left behind
        self.assertEqual(ws.calls(), [])

    def test_replies_stay_readable_blocks(self):
        s = R.validate_situations([sit(1)], "t")[0]
        backend = mock.Mock(name="cli")
        backend.name = "cli"
        backend.canary.return_value = None
        row = R.build_row(s, R.Reply(text='Say: "Fine."  \nWhy:\tbuys time. \n', stop_reason="end_turn"),
                          backend, "m", {"kernel": "k", "knowledge": "n"}, "")
        dumped = R.dump_yaml(row)
        self.assertIn("response: |", dumped)
        self.assertEqual(yaml.safe_load(dumped)["response"], 'Say: "Fine."\nWhy:    buys time.')

    def test_dry_run_sends_nothing(self):
        ws = Workspace(self)
        code, out, _ = call(ws.base() + ["run", "--dry-run", "--claude-bin", str(ws.fake)], ws.env())
        self.assertEqual(code, 0)
        self.assertIn("Nothing sent", out)
        self.assertEqual(ws.calls(), [])
        self.assertEqual(ws.runs(), [])

    def test_shim_resolution(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            exe = base / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
            exe.parent.mkdir(parents=True)
            exe.write_bytes(b"")
            shim = base / "claude.cmd"
            shim.write_text('@ECHO off\r\n"%dp0%\\node_modules\\@anthropic-ai\\claude-code\\bin\\claude.exe"   %*\r\n')
            self.assertEqual(R._resolve_shim(shim), [str(exe)])
            js = base / "old" / "cli.js"
            js.parent.mkdir()
            js.write_bytes(b"")
            (base / "old" / "node.exe").write_bytes(b"")
            old = base / "old" / "claude.cmd"
            old.write_text('"%_prog%"  "%dp0%\\cli.js" %*\r\nIF EXIST "%dp0%\\node.exe" (\r\n')
            self.assertEqual(R._resolve_shim(old), [str(base / "old" / "node.exe"), str(js)])
            broken = base / "broken.cmd"
            broken.write_text("@ECHO off\r\nclaude-real %*\r\n")
            with self.assertRaises(R.InputError):
                R._resolve_shim(broken)
        self.assertFalse(R.outside_ch(Path("C:/work/.CH-Thing/tmp")))
        self.assertTrue(R.outside_ch(Path(tempfile.gettempdir())))


class SubprocessTests(unittest.TestCase):
    """The runner as a separate process: import hygiene and piped output on Windows."""

    def run_cli(self, ws: Workspace, *argv: str, cwd: Path | None = None, **env: str) -> subprocess.CompletedProcess:
        full_env = ws.env(PYTHONUTF8="0", **env)
        return subprocess.run([sys.executable, str(RUN_EVAL), *ws.base(), *argv], capture_output=True,
                              env=full_env, timeout=120, cwd=cwd)

    def test_relative_claude_bin(self):
        ws = Workspace(self)
        proc = self.run_cli(ws, "run", "--claude-bin", "fake_claude.py", cwd=ws.bin)
        self.assertEqual(proc.returncode, 0, proc.stderr.decode("utf-8", "replace"))
        self.assertEqual(len(ws.calls()), 1)

    def test_piped_stdout_is_utf8(self):
        ws = Workspace(self, situations=[sit(1, "D:", "\u8bf7\u5e2e\u6211\u56de\u590d\u4f9b\u5e94\u5546 #zh")])
        proc = self.run_cli(ws, "run", "--claude-bin", str(ws.fake))
        self.assertEqual(proc.returncode, 0, proc.stderr.decode("utf-8", "replace"))
        text = proc.stdout.decode("utf-8")               # raises if the output is not UTF-8
        self.assertIn("\u8bf7\u5e2e\u6211", text)
        self.assertIn("number(s) not in the situation or knowledge: 3", text)

    def test_cli_backend_never_imports_anthropic(self):
        ws = Workspace(self)
        poison = ws.root / "poison" / "anthropic"
        poison.mkdir(parents=True)
        (poison / "__init__.py").write_text("raise ImportError('poisoned: the cli backend must not import me')\n")
        env = {"PYTHONPATH": os.pathsep.join(p for p in (str(poison.parent), os.environ.get("PYTHONPATH")) if p)}
        proc = self.run_cli(ws, "run", "--claude-bin", str(ws.fake), **env)
        self.assertEqual(proc.returncode, 0, proc.stderr.decode("utf-8", "replace"))
        # Base URL on loopback: even if the poison failed to load, nothing could reach the real API.
        proc = self.run_cli(ws, "run", "--backend", "api", "--metered", **env, ANTHROPIC_API_KEY="k",
                            ANTHROPIC_BASE_URL=f"http://127.0.0.1:{PORTS[-1]}")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("needs the anthropic SDK", proc.stderr.decode("utf-8"))


# --------------------------------------------------------------------------- api backend (fake Messages API)

class _FakeAPIHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # keep test output clean
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("content-length", "0"))) or b"{}")
        self.server.requests.append({"path": self.path, "body": body})
        user = body["messages"][0]["content"]
        status, payload = 200, None
        text = [{"type": "text", "text": 'Say: "Let me check and come back to you."\nWhy: buys time.'}]
        stop, details = "end_turn", None
        if "#404" in user:
            status, payload = 404, {"type": "error", "error": {"type": "not_found_error", "message": "model: nope"}}
        elif "#truncate" in user:
            text, stop = [{"type": "thinking", "thinking": "", "signature": "sig"}], "max_tokens"
        elif "#refusal" in user:
            text, stop = [{"type": "text", "text": "I can"}], "refusal"
            details = {"type": "refusal", "category": None, "explanation": "declined"}
        if payload is None:
            payload = {"id": "msg_test", "type": "message", "role": "assistant", "model": body["model"],
                       "content": text, "stop_reason": stop, "stop_sequence": None, "stop_details": details,
                       "usage": {"input_tokens": 25, "output_tokens": 30, "cache_creation_input_tokens": 900,
                                 "cache_read_input_tokens": 0}}
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("request-id", "req_test")
        self.end_headers()
        self.wfile.write(raw)


class _FakeAPI(ThreadingHTTPServer):
    allow_reuse_address = False       # never share a port with another process's listener
    daemon_threads = True


def start_fake_api(testcase: unittest.TestCase) -> _FakeAPI:
    for port in PORTS:
        try:
            server = _FakeAPI(("127.0.0.1", port), _FakeAPIHandler)
        except OSError:
            continue
        server.requests = []
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        testcase.addCleanup(server.server_close)
        testcase.addCleanup(server.shutdown)
        return server
    raise unittest.SkipTest("no free port in 18800-18849")


@unittest.skipUnless(HAVE_SDK, "anthropic SDK not installed (only the optional api backend needs it)")
class ApiBackendTests(unittest.TestCase):
    def setUp(self):
        self.api = start_fake_api(self)
        self.url = f"http://127.0.0.1:{self.api.server_address[1]}"

    def env(self, ws: Workspace, **extra: str) -> dict:
        return ws.env(ANTHROPIC_BASE_URL=self.url, NO_PROXY="127.0.0.1,localhost", **extra)

    def test_refuses_without_metered_or_key(self):
        ws = Workspace(self)
        code, out, err = call(ws.base() + ["run", "--backend", "api"], self.env(ws, ANTHROPIC_API_KEY="k"))
        self.assertEqual(code, 2)
        self.assertIn("METERED RUN", out)
        self.assertIn("add --metered", err)
        code, _, err = call(ws.base() + ["run", "--backend", "api", "--metered"], self.env(ws))
        self.assertEqual(code, 2)
        self.assertIn("ANTHROPIC_API_KEY", err)
        self.assertEqual(self.api.requests, [])
        code, out, _ = call(ws.base() + ["run", "--backend", "api", "--dry-run"], self.env(ws))
        self.assertEqual(code, 0)
        self.assertIn("Lowest-cost alternative", out)
        self.assertEqual(self.api.requests, [])

    def test_metered_run_against_the_fake(self):
        ws = Workspace(self, situations=[sit(1), sit(2, "P:", "Plan #truncate"), sit(3, "D:", "Draft #refusal"),
                                         sit(4, "X:", "Debrief #404"), sit(5)])
        code, out, err = call(ws.base() + ["run", "--backend", "api", "--metered"],
                              self.env(ws, ANTHROPIC_API_KEY="test-key-not-real"))
        self.assertEqual(code, 1, out + err)
        for needle in ("Capability the subscription path lacks", "Incremental cost: yes", "Lowest-cost alternative",
                       f"127.0.0.1:{self.api.server_address[1]}"):
            self.assertIn(needle, out)
        self.assertEqual(len(self.api.requests), 4)          # the 404 aborts before id 5
        body = self.api.requests[0]["body"]
        self.assertEqual(body["model"], "claude-sonnet-5-5")
        self.assertEqual(body["max_tokens"], R.API_MAX_TOKENS)
        self.assertEqual(body["output_config"], {"effort": "high"})
        self.assertNotIn("thinking", body)
        self.assertEqual(len(body["system"]), 2)
        self.assertEqual(body["system"][1]["cache_control"], {"type": "ephemeral"})
        self.assertIn("Project knowledge:", body["system"][1]["text"])
        self.assertNotIn("LOCAL-ONLY-CANARY", json.dumps(body))
        data = yaml.safe_load(ws.runs()[0].read_text(encoding="utf-8"))
        self.assertTrue(data["status"].startswith("aborted"))
        self.assertTrue(data["backend_version"].startswith("anthropic "))
        r1, r2, r3, r4 = data["results"]
        self.assertEqual((r1["meta"]["stop_reason"], r1["meta"]["model_served"]), ("end_turn", ["claude-sonnet-5-5"]))
        self.assertEqual(r1["meta"]["usage"]["cache_creation_input_tokens"], 900)
        self.assertEqual(r2["do_not_score"], "empty reply, truncated")
        self.assertEqual(r3["do_not_score"], "refusal")
        self.assertIn("API 404", r4["error"])


if __name__ == "__main__":
    unittest.main()
