"""Unit tests for PractiGen backend helper functions.

Pure Python — no Flask, no network, no API keys. Fast.
Run: pytest tests/test_utils.py -v
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import app


# --- Bug 1: Response parsing / validation -----------------------------------
def test_extract_section_valid_tags():
    text = "[CONCEPT]\nA concept.\n[CODE]\nprint(1)\n[OUTPUT]\n1\n[CAPTION]\nHello"
    assert app.extract_section("CONCEPT", text) == "A concept."
    assert app.extract_section("CODE", text) == "print(1)"
    assert app.extract_section("CAPTION", text) == "Hello"


def test_extract_section_missing_tag_returns_none():
    text = "[CONCEPT]\nonly concept here"
    assert app.extract_section("CODE", text) is None


def test_extract_section_malformed_response():
    # LLM used markdown headers instead of [TAG]; fallback pattern should still catch CODE.
    text = "CONCEPT:\nSome theory\n\nCODE:\ndef f():\n    return 2\n\nOUTPUT:\n2\n"
    assert app.extract_section("CODE", text) is not None
    assert "def f" in app.extract_section("CODE", text)


def test_validate_response_rejects_too_short_code():
    with pytest.raises(ValueError):
        app.validate_generation_result("general", "x = 1", "A valid concept.")


def test_validate_response_accepts_valid_code():
    # Should not raise.
    app.validate_generation_result(
        "general",
        "def bubble_sort(a):\n    return sorted(a)\n",
        "Sorting concept.",
    )


def test_validate_response_os_mode_skips_code_length():
    # OS mode has no [CODE]; only concept is required.
    app.validate_generation_result("os", "", "OS theory present.")


# --- Bug 5: Language validation ---------------------------------------------
def test_validate_code_language_python_correct():
    code = "import sys\n\ndef main():\n    print('hi')\n"
    assert app.validate_code_language(code, "python") is True


def test_validate_code_language_python_wrong():
    code = "#include <stdio.h>\nint main(){ printf(\"hi\"); return 0; }"
    assert app.validate_code_language(code, "python") is False


def test_validate_code_language_c_correct():
    code = "#include <stdio.h>\nint main(){ printf(\"hi\\n\"); return 0; }"
    assert app.validate_code_language(code, "c") is True


def test_validate_code_language_java_correct():
    code = "public class Main { public static void main(String[] a){ System.out.println(1);} }"
    assert app.validate_code_language(code, "java") is True


def test_validate_code_language_unknown_skips():
    assert app.validate_code_language("fn main() {}", "rust") is True


def test_validate_code_language_empty_is_false():
    assert app.validate_code_language("", "python") is False


# --- OS mode step parsing ----------------------------------------------------
def test_parse_steps_standard_format():
    text = (
        "Step 1: List files\n$ ls -la\nOutput:\nstudent@kali:~$ ls -la\ntotal 4\n\n"
        "Step 2: Print working dir\n$ pwd\nOutput:\nstudent@kali:~$ pwd\n/home/student\n"
    )
    steps = app.parse_steps(text)
    assert len(steps) == 2
    assert steps[0]["num"] == 1
    assert "List files" in steps[0]["explanation"]
    assert "total 4" in steps[0]["output"]


def test_parse_steps_numbered_list_format():
    text = "1. Do the first thing\n$ echo a\nOutput:\na\n\n2. Do the second\n$ echo b\nOutput:\nb\n"
    steps = app.parse_steps(text)
    assert len(steps) == 2
    assert steps[1]["num"] == 2


def test_parse_steps_empty_returns_fallback():
    steps = app.parse_steps("no recognizable steps here")
    assert len(steps) == 1
    assert steps[0]["num"] == 1


def test_parse_steps_multiline_code():
    text = (
        "Step 1: Write the program\n"
        "#include <stdio.h>\nint main(){\n  printf(\"x\");\n  return 0;\n}\n"
        "Output:\nstudent@kali:~$ gcc a.c\n"
    )
    steps = app.parse_steps(text)
    assert steps[0]["command"].count("\n") >= 2


# --- Caption helpers ---------------------------------------------------------
def test_make_step_caption_known_command():
    assert app.make_step_caption("ls -la") == "Directory Listing Output"


def test_make_step_caption_program_execution():
    assert app.make_step_caption("./a.out") == "Program Execution Output"


def test_make_caption_text_strips_step_prefix():
    assert app.make_caption_text("Step 3: Check permissions") == "Check permissions"


# --- Download payload helpers -----------------------------------------------
def test_get_terminal_lines_truncates_long_output():
    text = "\n".join(str(i) for i in range(100))
    lines = app.get_terminal_lines(text, max_lines=10)
    assert any("shortened" in ln.lower() for ln in lines)
    assert len(lines) <= 12


def test_get_terminal_lines_truncates_wide_lines():
    text = "x" * 500
    lines = app.get_terminal_lines(text, max_line_chars=50)
    assert lines[0].endswith("...")
    assert len(lines[0]) <= 50


def test_count_output_units_os_steps():
    experiments = [
        {"steps": [{}, {}, {}]},
        {"steps": []},      # min 1
        {"steps": [{}, {}]},
    ]
    assert app.count_output_units(experiments) == 3 + 1 + 2


# --- Auto-detect mode (Phase 3) ---------------------------------------------
def test_auto_detect_os_mode():
    d = app.detect_mode_and_language("Explore ls, cat, grep commands in Linux")
    assert d["mode"] == "os"


def test_auto_detect_general_mode_with_language():
    d = app.detect_mode_and_language("Write a Python program for binary search")
    assert d["mode"] == "general"
    assert d["code_language"] == "python"


def test_auto_detect_shell_script_is_os():
    d = app.detect_mode_and_language("Write a shell script to calculate factorial")
    assert d["mode"] == "os"


def test_auto_detect_ambiguous_defaults_general():
    d = app.detect_mode_and_language("Implement a sorting algorithm")
    assert d["mode"] == "general"
    assert d["code_language"] is None


def test_auto_detect_java():
    d = app.detect_mode_and_language("Implement a linked list in Java")
    assert d["mode"] == "general"
    assert d["code_language"] == "java"


# --- Message builder (Bug 2 / Enh 2 / Bug 5) --------------------------------
def test_build_messages_has_system_and_user():
    msgs = app.build_messages("general", "Sort an array", "python")
    assert len(msgs) == 2
    assert msgs[0]["role"] == "system"
    assert msgs[1]["role"] == "user"


def test_build_messages_language_lock_in_system():
    msgs = app.build_messages("general", "Write a C program", "python")
    assert "python" in msgs[0]["content"].lower()


def test_build_messages_variation_seed_injected():
    msgs = app.build_messages("general", "Sort", "python", variation_seed="ab12cd")
    assert "ab12cd" in msgs[1]["content"]


def test_build_messages_no_seed_no_injection():
    msgs = app.build_messages("general", "Sort", "python")
    assert "Variation reference" not in msgs[1]["content"]


# --- Jev advisory quality gate ----------------------------------------------
# Shape below mirrors a real response from POST https://openrouter.ai/api/alpha/decisions
LIVE_JEV_ANSWERS = {
    "decision": {
        "type": "choice",
        "choice": "needsRegeneration",
        "probabilities": {"needsRegeneration": 0.98, "readyForReview": 0.02},
        "confidence": 0.97,
    },
    "primaryConcern": {"type": "choice", "choice": "wrongLanguage", "confidence": 0.9},
    "needsHumanReview": {"type": "noul", "noul": 0.88},
    "submissionReadiness": {
        "type": "score",
        "score": 0.22,
        "legend": {"0": "1 - Major problems.", "1": "3 - Usable.", "2": "5 - Ready."},
        "probabilities": {"0": 0.81, "1": 0.16, "2": 0.03},
        "confidence": 0.67,
    },
}


def test_summarize_jev_answers_flattens_live_shape():
    s = app.summarize_jev_answers(LIVE_JEV_ANSWERS)
    assert s["decision"] == "needsRegeneration"
    assert s["decisionConfidence"] == 0.97
    assert s["primaryConcern"] == "wrongLanguage"
    assert s["reviewProbability"] == 0.88
    assert s["needsHumanReview"] is True
    assert s["clean"] is False
    assert s["decisionLabel"] == "Worth regenerating"


def test_summarize_jev_answers_clean_when_ready_and_low_noul():
    s = app.summarize_jev_answers({
        "decision": {"type": "choice", "choice": "readyForReview", "confidence": 0.9},
        "needsHumanReview": {"type": "noul", "noul": 0.07},
    })
    assert s["needsHumanReview"] is False
    assert s["clean"] is True


def test_summarize_jev_answers_noul_threshold_is_half():
    low = app.summarize_jev_answers({"needsHumanReview": {"noul": 0.49}})
    high = app.summarize_jev_answers({"needsHumanReview": {"noul": 0.5}})
    assert low["needsHumanReview"] is False
    assert high["needsHumanReview"] is True


@pytest.mark.parametrize("garbage", [None, [], "text", 42, {}, {"decision": "not-a-dict"},
                                     {"needsHumanReview": {"noul": "high"}}])
def test_summarize_jev_answers_never_raises(garbage):
    """A malformed gate response must degrade, not explode — generation already succeeded."""
    s = app.summarize_jev_answers(garbage)
    assert s["clean"] is False
    assert s["needsHumanReview"] is False


def test_normalize_score_uses_legend_size():
    # score 0.22 across a 3-point legend -> 0.11 on a 0..1 scale
    assert app._normalize_score(LIVE_JEV_ANSWERS["submissionReadiness"]) == pytest.approx(0.11)


def test_normalize_score_missing_or_degenerate_returns_none():
    assert app._normalize_score({"legend": {"0": "a", "1": "b"}}) is None   # no score
    assert app._normalize_score({"score": 1.0, "legend": {"0": "a"}}) is None  # single point


def test_build_jev_state_reports_language_mismatch():
    result = {"concept": "Theory here", "code": "def f():\n    return 1", "output": "1", "steps": []}
    state = app.build_jev_state("Write a C program", "general", "c", result)
    assert state["checks"]["languageValidationPassed"] is False
    assert state["checks"]["hasConcept"] is True
    assert state["targetLanguage"] == "c"


def test_build_jev_state_counts_os_steps():
    result = {"concept": "Linux", "code": "", "output": "", "steps": [{"num": 1}, {"num": 2}]}
    state = app.build_jev_state("Explore ls", "os", "", result)
    assert state["checks"]["stepCount"] == 2
    assert state["checks"]["languageValidationPassed"] is True  # no target language to check


def test_jev_gate_without_key_is_disabled(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    gate = app.run_jev_quality_gate("aim", "general", "python", {"code": "x", "concept": "y"})
    assert gate["enabled"] is False
    assert gate["status"] == "unavailable"


def test_jev_gate_swallows_any_transport_error(monkeypatch):
    """Any exception from the HTTP call must become 'unavailable', never propagate."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")

    def boom(*_args, **_kwargs):
        raise RuntimeError("connection reset")

    monkeypatch.setattr(app, "call_jev_decisions", boom)
    gate = app.run_jev_quality_gate("aim", "general", "python", {"code": "x", "concept": "y"})
    assert gate["status"] == "unavailable"
    assert "connection reset" in gate["reason"]
