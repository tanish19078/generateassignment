"""API integration tests for PractiGen using the Flask test client.

LLM calls are mocked by monkeypatching app.create_chat_completion, so no network
or API key is required. A capture list records each outbound call so we can assert
on temperature, top_p and the system/user message split.

Run: pytest tests/test_api.py -v
"""
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import app as app_module


GENERAL_RESPONSE = (
    "[CONCEPT]\nThis uses Python lists and loops.\n"
    "[CODE]\ndef bubble_sort(arr):\n    for i in range(len(arr)):\n        pass\n    return arr\n"
    "[OUTPUT]\n[1, 2, 3]\n"
    "[CAPTION]\nSorted array output\n"
)

C_RESPONSE = (
    "[CONCEPT]\nThis uses C arrays.\n"
    "[CODE]\n#include <stdio.h>\nint main(){ printf(\"sorted\"); return 0; }\n"
    "[OUTPUT]\nsorted\n"
    "[CAPTION]\nSorted output\n"
)

OS_RESPONSE = (
    "[CONCEPT]\nLinux file commands.\n"
    "[PROCEDURE]\n"
    "Step 1: List files\n$ ls -la\nOutput:\nstudent@kali:~$ ls -la\ntotal 8\n\n"
    "Step 2: Show path\n$ pwd\nOutput:\nstudent@kali:~$ pwd\n/home/student\n"
    "[CAPTION]\nFile commands\n"
)


@pytest.fixture
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


@pytest.fixture
def capture(monkeypatch):
    """Replace create_chat_completion with a scripted fake that records calls."""
    calls = []
    responses = {"queue": [GENERAL_RESPONSE]}

    def fake_completion(provider_key, api_key, model, messages, temperature=0.7, top_p=0.9):
        calls.append({
            "provider": provider_key,
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "top_p": top_p,
        })
        queue = responses["queue"]
        if len(queue) > 1:
            return queue.pop(0)
        return queue[0]

    monkeypatch.setattr(app_module, "create_chat_completion", fake_completion)
    # Ensure a key is always available so the endpoint doesn't short-circuit.
    monkeypatch.setattr(app_module, "get_provider_keys", lambda cfg, sub: [sub or "test-key"])
    return {"calls": calls, "responses": responses}


# --- /api/parse --------------------------------------------------------------
def test_parse_splits_by_separator(client):
    r = client.post("/api/parse", json={"text": "Aim one\n---\nAim two\n---\nAim three"})
    assert r.status_code == 200
    assert r.get_json()["aims"] == ["Aim one", "Aim two", "Aim three"]


def test_parse_handles_empty_input(client):
    r = client.post("/api/parse", json={"text": ""})
    assert r.get_json()["aims"] == []


def test_parse_trims_whitespace(client):
    r = client.post("/api/parse", json={"text": "  Aim one  \n---\n  Aim two  "})
    assert r.get_json()["aims"] == ["Aim one", "Aim two"]


# --- /api/generate -----------------------------------------------------------
def test_generate_returns_all_sections(client, capture):
    r = client.post("/api/generate", json={"aim": "Sort", "mode": "general", "code_language": "python"})
    assert r.status_code == 200
    data = r.get_json()
    assert "bubble_sort" in data["code"]
    assert data["concept"]
    assert data["caption"]


def test_generate_includes_temperature(client, capture):
    client.post("/api/generate", json={"aim": "Sort", "mode": "general", "code_language": "python"})
    call = capture["calls"][0]
    assert call["temperature"] == 0.7
    assert call["top_p"] == 0.9


def test_generate_includes_variation_seed(client, capture):
    client.post("/api/generate", json={
        "aim": "Sort", "mode": "general", "code_language": "python", "variation_seed": "zzz999",
    })
    user_msg = capture["calls"][0]["messages"][-1]["content"]
    assert "zzz999" in user_msg


def test_generate_uses_system_message(client, capture):
    client.post("/api/generate", json={"aim": "Sort", "mode": "general", "code_language": "python"})
    roles = [m["role"] for m in capture["calls"][0]["messages"]]
    assert "system" in roles and "user" in roles


def test_generate_enforces_language_in_prompt(client, capture):
    client.post("/api/generate", json={"aim": "Write a C program", "mode": "coding", "code_language": "python"})
    system_msg = capture["calls"][0]["messages"][0]["content"]
    assert "python" in system_msg.lower()


def test_generate_validates_wrong_language_then_retries(client, capture):
    # First response returns C code for a Python request -> triggers a correction retry.
    capture["responses"]["queue"] = [C_RESPONSE, GENERAL_RESPONSE]
    r = client.post("/api/generate", json={
        "aim": "Write a C program for bubble sort", "mode": "coding", "code_language": "python",
    })
    assert r.status_code == 200
    assert "bubble_sort" in r.get_json()["code"]
    assert len(capture["calls"]) == 2  # original + correction retry


def test_generate_rejects_empty_code_response(client, capture):
    capture["responses"]["queue"] = ["[CONCEPT]\nok concept\n[CODE]\n\n[OUTPUT]\n\n[CAPTION]\nx\n"]
    r = client.post("/api/generate", json={"aim": "Sort", "mode": "general", "code_language": "python"})
    assert r.status_code >= 400
    assert "error" in r.get_json()


def test_generate_os_mode_returns_steps(client, capture):
    capture["responses"]["queue"] = [OS_RESPONSE]
    r = client.post("/api/generate", json={"aim": "Explore ls and pwd", "mode": "os"})
    assert r.status_code == 200
    steps = r.get_json()["steps"]
    assert len(steps) == 2
    assert steps[0]["num"] == 1


def test_generate_auto_detect_overrides_mode(client, capture):
    capture["responses"]["queue"] = [OS_RESPONSE]
    r = client.post("/api/generate", json={"aim": "Explore ls, cat, grep in Linux", "mode": "auto"})
    assert r.status_code == 200
    assert "steps" in r.get_json()


# --- /api/refine (Bug 3) -----------------------------------------------------
def test_refine_includes_existing_code(client, capture):
    client.post("/api/refine", json={
        "aim": "Sort an array", "change_request": "add comments",
        "existing_code": "def bubble_sort(a): return a",
        "existing_concept": "sorting", "existing_output": "[1,2,3]",
        "mode": "general", "code_language": "python",
    })
    user_msg = capture["calls"][0]["messages"][-1]["content"]
    assert "bubble_sort" in user_msg
    assert "add comments" in user_msg


def test_refine_uses_lower_temperature(client, capture):
    client.post("/api/refine", json={
        "aim": "Sort", "change_request": "add comments",
        "existing_code": "def bubble_sort(a): return a", "existing_concept": "c", "existing_output": "o",
        "mode": "general", "code_language": "python",
    })
    assert capture["calls"][0]["temperature"] == 0.3


def test_refine_preserves_aim(client, capture):
    r = client.post("/api/refine", json={
        "aim": "Original aim text", "change_request": "tweak it",
        "existing_code": "def bubble_sort(a): return a", "existing_concept": "c", "existing_output": "o",
        "mode": "general", "code_language": "python",
    })
    assert r.get_json()["aim"] == "Original aim text"


def test_refine_requires_change_request(client, capture):
    r = client.post("/api/refine", json={"aim": "x", "change_request": "  ", "mode": "general", "code_language": "python"})
    assert r.status_code >= 400


# --- /api/detect (Phase 3) ---------------------------------------------------
def test_detect_classifies_mixed_batch(client):
    r = client.post("/api/detect", json={"aims": [
        "Explore grep and ls in Linux",
        "Write a Java program for a linked list",
    ]})
    assert r.status_code == 200
    dets = r.get_json()["detections"]
    assert dets[0]["mode"] == "os"
    assert dets[1]["mode"] == "general"
    assert dets[1]["code_language"] == "java"


# --- /api/download (Bug 4) ---------------------------------------------------
def _download_body(experiments, mode="general"):
    return json.dumps({
        "experiments": experiments,
        "settings": {"outputFilename": "test.docx"},
        "mode": mode,
    })


def test_download_produces_valid_docx(client):
    body = _download_body([{
        "aim": "Sort an array", "concept": "sorting concept",
        "code": "def bubble_sort(a):\n    return sorted(a)", "output": "[1, 2, 3]",
        "caption": "Sorted",
    }])
    r = client.post("/api/download", data=body, headers={"Content-Type": "application/json"})
    assert r.status_code == 200
    assert r.data[:2] == b"PK"  # .docx is a zip container
    assert "wordprocessingml" in r.headers["Content-Type"]


def test_download_rejects_too_many_units(client):
    experiments = [{"aim": f"a{i}", "steps": [{"num": 1, "output": "x"}] * 11} for i in range(20)]
    r = client.post("/api/download", data=_download_body(experiments, mode="os"),
                    headers={"Content-Type": "application/json"})
    assert r.status_code == 400
    assert "Too many" in r.get_json()["error"]


def test_download_empty_experiments_rejected(client):
    r = client.post("/api/download", data=_download_body([]),
                    headers={"Content-Type": "application/json"})
    assert r.status_code == 400


# --- /api/extract-aims (Phase 3) ---------------------------------------------
def test_extract_aims_rejects_non_pdf(client):
    data = {"file": (io.BytesIO(b"not a pdf"), "notes.txt")}
    r = client.post("/api/extract-aims", data=data, content_type="multipart/form-data")
    assert r.status_code == 400


def test_extract_aims_requires_file(client):
    r = client.post("/api/extract-aims", data={}, content_type="multipart/form-data")
    assert r.status_code == 400


# --- Jev advisory quality gate (never blocks generation) ----------------------
JEV_OK = {
    "answers": {
        "decision": {"type": "choice", "choice": "readyForReview", "confidence": 0.94},
        "primaryConcern": {"type": "choice", "choice": "none", "confidence": 0.9},
        "needsHumanReview": {"type": "noul", "noul": 0.05},
        "submissionReadiness": {
            "type": "score", "score": 1.9,
            "legend": {"0": "1 - bad", "1": "3 - ok", "2": "5 - ready"},
        },
    }
}


def _generate(client, **overrides):
    body = {"aim": "Sort", "mode": "general", "code_language": "python"}
    body.update(overrides)
    return client.post("/api/generate", json=body)


def test_generate_gate_disabled_makes_no_call(client, capture, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    called = []
    monkeypatch.setattr(app_module, "call_jev_decisions",
                        lambda *a, **k: called.append(1) or JEV_OK)

    r = _generate(client, jev_quality_gate=False)
    assert r.status_code == 200
    assert "quality_gate" not in r.get_json()
    assert called == []


def test_generate_gate_runs_by_default(client, capture, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    monkeypatch.setattr(app_module, "call_jev_decisions", lambda *a, **k: JEV_OK)

    data = _generate(client).get_json()
    gate = data["quality_gate"]
    assert gate["enabled"] is True
    assert gate["status"] == "complete"
    assert gate["summary"]["decision"] == "readyForReview"
    assert gate["summary"]["clean"] is True


def test_generate_succeeds_when_gate_raises(client, capture, monkeypatch):
    """The whole point of the feature: a Jev outage must not cost the student a generation."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")

    def boom(*_a, **_k):
        raise RuntimeError("jev is down")

    monkeypatch.setattr(app_module, "call_jev_decisions", boom)

    r = _generate(client)
    assert r.status_code == 200
    data = r.get_json()
    assert "bubble_sort" in data["code"]          # content survived intact
    assert data["quality_gate"]["status"] == "unavailable"


def test_generate_gate_unavailable_without_server_key(client, capture):
    data = _generate(client).get_json()
    assert data["quality_gate"]["enabled"] is False
    assert data["quality_gate"]["status"] == "unavailable"


def test_generate_gate_does_not_alter_content(client, capture, monkeypatch):
    """A 'needsRegeneration' verdict is advice only — it must not re-run the LLM."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    monkeypatch.setattr(app_module, "call_jev_decisions", lambda *a, **k: {
        "answers": {
            "decision": {"type": "choice", "choice": "needsRegeneration", "confidence": 0.99},
            "needsHumanReview": {"type": "noul", "noul": 0.97},
        }
    })

    baseline = len(capture["calls"])
    data = _generate(client).get_json()
    assert "bubble_sort" in data["code"]
    assert data["quality_gate"]["summary"]["needsHumanReview"] is True
    # exactly one LLM call — the gate did not trigger a regeneration
    assert len(capture["calls"]) - baseline == 1
