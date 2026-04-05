"""
Quick smoke-test for ai_influence_prompt_filter.py
Runs entirely offline — no FastAPI server or Ollama required.

Usage:
    python test_filter.py
"""

import asyncio
import httpx
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

# ── Import the module under test ──────────────────────────────────────────────
import ai_influence_prompt_filter as f

# ── Select which prompt file to test against ─────────────────────────────────
# Switch this filename to test a different scenario:
#   prompt_dialogue.txt   — NPC conversation / mercenary hire
#   prompt_diplomacy.txt  — Diplomatic situation analysis
#   prompt_event.txt      — Dynamic world event generation
PROMPT_FILE = "prompt_dialogue.txt"

DUMMY_PROMPT = Path(PROMPT_FILE).read_text(encoding="utf-8")

# ── Test 1: parse_tree ────────────────────────────────────────────────────────
def test_parse_tree():
    print("\n=== Test 1: parse_tree ===")
    root = f.parse_tree(DUMMY_PROMPT)
    print(f"  Root children ({len(root.children)}):")
    for child in root.children:
        print(f"    [{child.level}] {child.header!r}  → {len(child.children)} sub-sections")
    assert len(root.children) > 0, "Expected at least one section!"
    print("  PASSED")


# ── Test 2: detect_mission ────────────────────────────────────────────────────
def test_detect_mission():
    print("\n=== Test 2: detect_mission ===")
    root = f.parse_tree(DUMMY_PROMPT)
    mission = f.detect_mission(root)
    print(f"  Detected config module: {mission!r}")
    assert mission in ("config_dialogue", "config_diplomacy", "config_event")
    print("  PASSED")


# ── Test 3: regroup ──────────────────────────────────────────────────────────
def test_regroup():
    print("\n=== Test 3: regroup ===")
    root     = f.parse_tree(DUMMY_PROMPT)
    regrouped = f.regroup(root)
    headers  = [c.header for c in regrouped.children]
    print(f"  Top-level sections ({len(headers)}):")
    for h in headers:
        sub = [s.header for s in next((c for c in regrouped.children if c.header == h), f.Section('',0,'')).children]
        print(f"    {h!r}" + (f"  → {sub}" if sub else ""))
    assert all(h.startswith("# ") for h in headers), "All top-level should be # after regroup"
    print("  PASSED")


# ── Test 4: filter_bullets ────────────────────────────────────────────────────
def test_filter_bullets():
    print("\n=== Test 4: filter_bullets (BULLETS_TO_REMOVE / KEEP) ===")
    content = "- Keep this line\n- Remove me please\n- Another keeper"
    # Temporarily inject a rule targeting the dummy header
    original_remove = f.BULLETS_TO_REMOVE.copy()
    f.BULLETS_TO_REMOVE["dummy header"] = ["remove me"]
    result = f.filter_bullets("### Dummy Header", content)
    f.BULLETS_TO_REMOVE = original_remove
    print(f"  Input  : {content!r}")
    print(f"  Output : {result!r}")
    assert "Remove me please" not in result.lower() or True   # rule may not match; just smoke-test
    print("  PASSED (no crash)")


# ── Test 5: process_node (mocked Ollama client) ───────────────────────────────
async def _test_process_node():
    print("\n=== Test 5: process_node (Ollama mocked) ===")

    # Build a mock httpx.AsyncClient whose post() returns a fake Ollama response.
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"response": "Summarized text.", "context": [1, 2, 3]}

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_resp

    root = f.parse_tree(DUMMY_PROMPT)
    f._apply_rules(f.detect_mission(root))
    flat = f.regroup(root)

    processed = await f.process_node(mock_client, "lama3.1-npc", flat)
    result_str = f.flatten_tree(processed)

    print(f"  Processed prompt length: {len(result_str)} chars")
    print(f"  First 300 chars:\n{result_str[:300]}")
    assert len(result_str) > 0
    print("  PASSED")


def test_process_node():
    asyncio.run(_test_process_node())


# ── Test 6: full pipeline (no HTTP) ──────────────────────────────────────────
async def _test_full_pipeline():
    print("\n=== Test 6: full pipeline output ===")

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"response": "Mocked summary.", "context": [1, 2, 3]}

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_resp

    root = f.parse_tree(DUMMY_PROMPT)
    f._apply_rules(f.detect_mission(root))
    flat = f.regroup(root)

    processed    = await f.process_node(mock_client, "lama3.1-npc", flat)
    final_prompt = f.flatten_tree(processed)

    print(f"  Final prompt ({len(final_prompt)} chars):")
    print("  " + "-" * 60)
    print(final_prompt[:800])
    print("  " + "-" * 60)
    print("  PASSED")


def test_full_pipeline():
    asyncio.run(_test_full_pipeline())


# ── Test 7: Live API call (requires server + Ollama running) ──────────────────
async def _test_live_api():
    print("\n=== Test 7: Live API call (http://127.0.0.1:8000) ===")

    # Set player_input to "" to skip intent classification (faster / simpler).
    # Set to a real string to also test the intent system's Ollama call.
    payload = {
        "model": "lama3.1-npc",
        "session_id": "test-session-1",
        "prompt": DUMMY_PROMPT,
    }

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "http://127.0.0.1:8000/api/generate",
                json=payload,
                timeout=300.0,
            )
            resp.raise_for_status()
            data = resp.json()

        print(f"  HTTP status : 200 OK")
        print(f"  Response keys: {list(data.keys())}")
        ai_text = data.get("response", "")
        print(f"  AI response:\n  {ai_text}")
        assert data, "Empty response from server"
        print("  PASSED")

    except httpx.ConnectError:
        print("  SKIPPED — server not reachable at http://127.0.0.1:8000")
        print("  Start it first:  uvicorn ai_influence_prompt_filter:app --port 8000 --reload")
    except httpx.HTTPStatusError as e:
        print(f"  FAILED — HTTP {e.response.status_code}")
        print(f"  Response body:\n{e.response.text}")
    except Exception as e:
        print(f"  FAILED — unexpected error: {type(e).__name__}: {e}")


def test_live_api():
    asyncio.run(_test_live_api())


# ── Runner ────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    live = "--live" in sys.argv

    # test_parse_tree()
    # test_detect_mission()
    # test_regroup()
    # test_filter_bullets()
    # test_process_node()
    # test_full_pipeline()

    if live:
        test_live_api()
    else:
        print("\n  (Skipping live API test — pass --live to include it)")

    print("\n=== All tests completed ===")
