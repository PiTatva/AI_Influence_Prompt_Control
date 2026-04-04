# AI Influence Prompt Filter

A local FastAPI proxy that sits between the **AI Influence** mod for **Mount & Blade II: Bannerlord** and your local AI backend — either **Ollama** or the **Player2 App**.

Instead of forwarding the mod's raw prompts directly to the AI backend, this script intercepts each request, parses the structured markdown prompt the mod generates, and applies a set of configurable rules before forwarding the trimmed prompt onward. This keeps prompts lean, reduces token usage, and lets you fine-tune AI behavior per mission type without touching the mod itself.

**What it can do:**

- **Filter** — remove prompt sections you don't need (e.g. redundant reminder blocks)
- **Replace** — swap verbose sections with your own shorter versions
- **Summarize** — condense large sections via a secondary Ollama call
- **Reorder** — control section priority for KV-cache-friendly static/dynamic prompt splitting
- **Intent classification** — detect what the player is asking (greeting, trade, romance, parley, etc.) and include only the sections relevant to that intent
- **Per-mission config** — dialogue, diplomatic analysis, diplomatic statements, and dynamic world events each have their own rule file (`config_*.py`)

---

## Requirements

- Python 3.10+
- **One of:**
  - [Ollama](https://ollama.com) running locally *(default)*
  - [Player2 App](https://player2.game) running locally
- The **AI Influence** Bannerlord mod installed

---

## Installation

1. **Clone or download** this folder somewhere on your machine.

2. **Create a virtual environment** (optional but recommended):
   ```bash
   python -m venv myenv
   myenv\Scripts\activate
   ```

3. **Install dependencies:**
   ```bash
   pip install fastapi uvicorn httpx
   ```

4. **Start the proxy server:**
   ```bash
   uvicorn ai_influence_prompt_filter:app --host 0.0.0.0 --port 8000
   ```
   The proxy will now listen on `http://localhost:8000` and forward processed prompts to your configured backend.

5. Make sure your **AI backend is running**:
   - **Ollama** (default): `ollama run mistral`
   - **Player2**: launch the Player2 App and sign in before starting the proxy.

---

## Bannerlord MCM Settings (AI Influence Mod)

In-game, open the **Mod Configuration Menu (MCM)** and find the **AI Influence** settings. You need to point the mod at this proxy instead of directly at Ollama:

| Setting | Value |
|---|---|
| **API URL / Endpoint** | `http://localhost:8000/api/generate` |
| **Model** | Your Ollama model name (e.g. `mistral`) |

> **Note:** The proxy passes the model name through to the backend. The default in the script is `mistral` — change `DEFAULT_MODEL` in `ai_influence_prompt_filter.py` if you want a different fallback. When using Player2 the model selection is controlled by the Player2 App itself, so the MCM model field is ignored.

Once set, every AI request from the mod will go through the filter automatically. No other changes to the mod are needed.

---

## Backend Configuration

Open `ai_influence_prompt_filter.py` and set the `BACKEND` variable near the top:

```python
# "ollama" (default) or "player2"
BACKEND = "ollama"
```

### Ollama (default)

No extra settings required. The proxy connects to `http://localhost:11434` and uses the KV-cache warm-up pipeline.

### Player2

```python
BACKEND          = "player2"
PLAYER2_BASE_URL = "http://127.0.0.1:4315"   # Player2 App must be running
PLAYER2_GAME_KEY = "your-game-client-id"      # from player2.game/profile/developer
```

- The Player2 App must be running and you must be signed in before starting the proxy.
- `PLAYER2_GAME_KEY` is your **Game Client Id** from the [Developer Dashboard](https://player2.game/profile/developer). You can leave it empty (`""`) while developing.
- Summarization calls and main generation both go through `POST /v1/chat/completions`.
- KV-cache warm-up is **not** used with Player2 (the App manages its own caching).

---

## Mission Config Files

Each mission type maps to a config file you can edit freely:

| File | When it's used |
|---|---|
| `config_dialogue.py` | Standard NPC conversation |
| `config_analyze_diplomacy.py` | "Analyze diplomatic situation" prompts |
| `config_diplomatic_statement.py` | Diplomatic statement generation |
| `config_event.py` | Dynamic world event generation |

Inside each file you can set:

- `SECTIONS_TO_REMOVE` — list of section headers to drop entirely
- `SECTIONS_TO_REPLACE` — dict of header → replacement text
- `SECTIONS_TO_SUMMARIZE` — dict of header → summarization settings
- `BULLETS_TO_KEEP` / `BULLETS_TO_REMOVE` — fine-grained bullet filtering per section
- `PINNED_STATIC_SECTIONS` / `DYNAMIC_SECTIONS` — KV-cache prompt splitting hints
- `ENABLE_INTENT_SYSTEM` — toggle intent-based section filtering on/off

Intent types and their associated headers are configured in `intent_config.json`.

Changes to config files take effect on the **next request** — no server restart needed.

---

## Logs

Diagnostic logs are written per mission type:

- `log_dialogue.txt`
- `log_unknown.txt`

Set `LOGGING = False` in `ai_influence_prompt_filter.py` to disable them.
