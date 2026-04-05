import httpx
import json
import re
from typing import Optional

OLLAMA_URL = "http://localhost:11434/api/generate"

# Bullet keywords to extract from Character Briefing for Pass 1
_CB_FIELDS = [
    "identity", "culture", "wealth", "relationship with player",
    "trust in player", "your character", "your story",
]

# Bullet keywords to extract from The Player for Pass 1
_TP_FIELDS = ["your knowledge"]

# Regex patterns for structured data
_ID_RE       = re.compile(r'\(id:([^)]+)\)',        re.IGNORECASE)
_SID_RE      = re.compile(r'string_id:([^\s,)]+)',  re.IGNORECASE)
# Matches inventory lines:  Name (id:xxx): qty (approx. price gold
_INV_LINE_RE = re.compile(
    r'([^(\n]+?)\s*\(id:([^)]+)\)\s*:\s*(\d+)[^(]*\(approx\.\s*(\d+)\s*gold',
    re.IGNORECASE,
)
# Matches settlement/party lines:  - Name (id:xxx):
_SETTLE_RE   = re.compile(r'^-\s*([^(]+?)\s*\(id:([^)]+)\)', re.MULTILINE)
# Matches bare  id:X  references (e.g. "Leader: Emperor Lucon id:lord_1_1")
_BARE_ID_RE  = re.compile(r'\bid:([^\s,);]+)', re.IGNORECASE)

# Sections + bullet field keywords searched when resolving NPC IDs.
# Top-level bullets only (lines starting with '- **' at column 0);
# matching blocks include all indented sub-lines.
_NPC_ID_SEARCH_FIELDS: dict[str, list[str]] = {
    "character briefing": ["identity", "relationships", "your captives"],
    "the player":         ["their forces", "other lords in player"],
    "immediate situation": ["recent events"],
    "global politics":    ["kingdoms and leaders"],
}


def _norm(h: str) -> str:
    return h.strip("#").strip().lower()


def _extract_bullet_block(content: str, fields: list[str]) -> str:
    """
    Extract top-level bullet blocks whose header matches any of `fields`,
    including all indented sub-lines beneath them.

    Top-level bullets are lines starting at column 0 with '- **' or '* **'.
    Indented lines (sub-bullets, continuation) are kept while their parent
    is captured and stop when the next top-level bullet is reached.
    """
    lines     = content.split("\n")
    result:   list[str] = []
    capturing = False
    for line in lines:
        # Only treat unindented '- **' lines as top-level bullets
        is_top = line.startswith("- **") or line.startswith("* **")
        if is_top:
            capturing = any(f in line.lower() for f in fields)
        if capturing:
            result.append(line)
    return "\n".join(result)


def _find_id_near_name(line: str, name_lower: str) -> Optional[str]:
    """
    Search for an id starting from the name's position in the line.
    Tries '(id:X)' format first, then bare 'id:X' (used in Global Politics).
    """
    pos = line.lower().find(name_lower)
    if pos == -1:
        return None
    segment = line[pos:]
    m = _ID_RE.search(segment)
    if m:
        return m.group(1)
    m = _BARE_ID_RE.search(segment)
    if m:
        return m.group(1)
    return None


class ContextAnalyzer:
    """
    Pass 1 — heavy analysis LLM call + programmatic entity-resolution code layer.

    Pass 1 receives:
      - Character Briefing: identity, culture, wealth, relationship, trust (bullets only)
      - The Player: "Your Knowledge" bullet only (NOT inventory)
      - Immediate Situation: full content if present
      - Last N conversation messages (configurable, default 6)
      - Current player input

    Pass 1 returns structured JSON:
      intent, conversation_state, entities{items,npcs,locations}, lying_score, topic

    Code layer then fuzzy-matches entity names → IDs from section data.
    """

    def __init__(self, config_path: str):
        with open(config_path, "r", encoding="utf-8") as f:
            self.cfg = json.load(f)

        self._intent_types: list[str]       = self.cfg["intent_types"]
        self._history_n:    int             = self.cfg.get("pass1_history_messages", 6)
        self._max_tokens:   int             = self.cfg.get("pass1_max_tokens", 600)

    # ------------------------------------------------------------------ helpers

    def _extract_bullet_fields(self, content: str, fields: list[str]) -> str:
        """Keep only bullet lines whose text contains one of the field keywords."""
        lines     = content.split("\n")
        result:   list[str] = []
        capturing = False

        for line in lines:
            stripped = line.strip()
            is_bullet = stripped.startswith("- **") or stripped.startswith("* **")
            if is_bullet:
                capturing = any(f in stripped.lower() for f in fields)
            if capturing:
                result.append(line)

        return "\n".join(result).strip()

    def _extract_history_messages(self, conv_content: str, n: int) -> str:
        """Return the last n dialogue lines from Conversation History content."""
        skip_prefixes = ("(last ", "last interaction:", "- player input:", "respond to")
        messages: list[str] = []

        for line in conv_content.split("\n"):
            s = line.strip()
            if not s:
                continue
            if any(s.lower().startswith(p) for p in skip_prefixes):
                continue
            # Treat lines with a colon as dialogue (e.g. "Player: ...", "Npc: ...")
            if ":" in s and not s.startswith("-") and not s.startswith("*"):
                messages.append(s)

        return "\n".join(messages[-n:])

    def _extract_pass1_context(self, root) -> tuple[str, str, str, str]:
        """
        Pull the four pieces needed for Pass 1 from the section tree.
        Returns: (npc_status, player_status, immediate_situation, history)
        """
        npc_status = player_status = immediate_situation = history = ""

        for child in root.children:
            hn = _norm(child.header)
            if "character briefing" in hn:
                npc_status = self._extract_bullet_fields(child.content, _CB_FIELDS)
            elif "the player" in hn:
                player_status = self._extract_bullet_fields(child.content, _TP_FIELDS)
            elif "immediate situation" in hn:
                immediate_situation = child.content[:2000]
            elif "conversation history" in hn:
                history = self._extract_history_messages(child.content, self._history_n)

        return npc_status, player_status, immediate_situation, history

    # ------------------------------------------------------------------ Pass 1 LLM call

    async def analyze(
        self,
        client: httpx.AsyncClient,
        model: str,
        root,          # Section root — read-only, not modified
        player_input: str,
    ) -> dict:
        """
        Pass 1: heavy LLM call that understands intent + context together.
        Returns a dict with: intent, conversation_state, entities, lying_score, topic
        """
        npc_status, player_status, immediate, history = self._extract_pass1_context(root)
        intent_list = ", ".join(self._intent_types)

        sections: list[str] = [
            "Analyze this Mount & Blade II: Bannerlord NPC conversation. "
            "Return ONLY valid JSON. No markdown, no explanation.\n",
        ]
        if npc_status:
            sections.append(f"## NPC Status\n{npc_status}\n")
        if player_status:
            sections.append(f"## Player Known Info\n{player_status}\n")
        if immediate:
            sections.append(f"## Immediate Situation\n{immediate}\n")
        if history:
            sections.append(f"## Recent Conversation (last {self._history_n})\n{history}\n")
        sections.append(f"## Current Player Input\nPlayer: {player_input[:500]}\n")

        sections.append(
            f"\nValid intents: [{intent_list}]\n"
            "- parley: war/peace/negotiations\n"
            "- kingdom_action: alliances, declaring war, vassals, mercenaries\n\n"
            "Return ONLY this JSON (no extra fields, no markdown):\n"
            "{\n"
            '  "intent": "<intent_type>",\n'
            '  "conversation_state": "<new_request|continuation|confirmation|withdrawal|challenge>",\n'
            '  "entities": {\n'
            '    "items": [{"name": "...", "qty": null, "price_per_unit": null, "direction": "give|take|unknown"}],\n'
            '    "npcs": [{"name": "..."}],\n'
            '    "locations": [{"name": "..."}]\n'
            "  },\n"
            '  "lying_score": 0.0,\n'
            '  "topic": "..."\n'
            "}\n"
            "Rules:\n"
            "- lying_score: 0.0=honest → 1.0=clear provable lie. Use contradictions with known facts only.\n"
            "- topic: 2-3 sentences. What player truly wants based on full context. "
            "Note intentions, history patterns, concerns, or suspicious claims.\n"
            "- direction: 'give'=player gives to NPC, 'take'=NPC gives to player.\n"
            "- qty/price_per_unit: null if not stated.\n"
            "- If no entities mentioned, use empty arrays [].\n"
        )

        prompt = "".join(sections)

        payload = {
            "model":       model,
            "prompt":      prompt,
            "stream":      False,
            "num_predict": self._max_tokens,
            "temperature": 0.1,
        }

        try:
            resp = await client.post(OLLAMA_URL, json=payload, timeout=45.0)
            resp.raise_for_status()
            raw = resp.json().get("response", "").strip()
            if "```" in raw:
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            return json.loads(raw)
        except Exception:
            return {
                "intent":             "greet",
                "conversation_state": "new_request",
                "entities":           {"items": [], "npcs": [], "locations": []},
                "lying_score":        0.0,
                "topic":              "",
            }

    # ------------------------------------------------------------------ Code layer

    def _build_inventory_lookup(self, root) -> dict[str, dict]:
        """
        Scan Character Briefing + The Player sections → build
        { normalized_name: {id, qty, price, owner, line} }
        Also indexed by item_id for fast lookup.
        """
        lookup: dict[str, dict] = {}
        for child in root.children:
            hn = _norm(child.header)
            if "character briefing" in hn:
                self._parse_inventory_lines(child.content, "npc", lookup)
            elif "the player" in hn:
                self._parse_inventory_lines(child.content, "player", lookup)
        return lookup

    def _parse_inventory_lines(self, content: str, owner: str, lookup: dict) -> None:
        for line in content.split("\n"):
            m = _INV_LINE_RE.search(line)
            if not m:
                continue
            name     = m.group(1).strip().lower()
            item_id  = m.group(2).strip()
            qty      = int(m.group(3))
            price    = int(m.group(4))
            entry    = {"id": item_id, "qty": qty, "price": price,
                        "owner": owner, "line": line.strip()}
            lookup[name]             = entry
            lookup[item_id.lower()]  = entry   # also index by id

    def _fuzzy_match(self, name: str, lookup: dict) -> Optional[dict]:
        """Exact match first; then word-by-word (words > 3 chars only)."""
        key = name.lower().strip()
        if key in lookup:
            return lookup[key]
        for word in key.split():
            if len(word) > 3:
                for lk in lookup:
                    if word in lk or lk in word:
                        return lookup[lk]
        return None

    def _build_settlement_lookup(self, root) -> dict[str, dict]:
        lookup: dict[str, dict] = {}
        for child in root.children:
            hn = _norm(child.header)
            if "nearby settlements" in hn or "nearby parties" in hn:
                for m in _SETTLE_RE.finditer(child.content):
                    name = m.group(1).strip().lower()
                    sid  = m.group(2).strip()
                    entry = {"id": sid, "line": m.group(0).strip()}
                    lookup[name]        = entry
                    lookup[sid.lower()] = entry
        return lookup

    def _find_npc_id(self, name: str, root) -> Optional[str]:
        """
        Search for an NPC id across specific bullet fields in multiple sections:
          - Character Briefing  : Identity, Relationships, Your Captives
          - The Player          : Their Forces, Other Lords in Player's Captivity
          - Immediate Situation : Recent Events
          - Global Politics     : Kingdoms and Leaders

        Uses _extract_bullet_block so indented sub-lines (e.g. under
        Relationships) are included, and _find_id_near_name so the correct
        id is extracted even on dense lines like Global Politics leaders.
        """
        name_lower = name.lower().strip()
        for child in root.children:
            hn = _norm(child.header)
            for section_key, fields in _NPC_ID_SEARCH_FIELDS.items():
                if section_key in hn:
                    block = _extract_bullet_block(child.content, fields)
                    for line in block.split("\n"):
                        npc_id = _find_id_near_name(line, name_lower)
                        if npc_id:
                            return npc_id
                    break
        return None

    def resolve_entities(self, entities: dict, root) -> dict:
        """
        Code layer: fuzzy-match entity names from Pass 1 → IDs from section data.
        Items searched only in inventories.
        Locations searched only in Nearby Settlements / Nearby Parties.
        NPCs searched only in Character Briefing.
        """
        inv_lookup    = self._build_inventory_lookup(root)
        settle_lookup = self._build_settlement_lookup(root)

        resolved: dict[str, list] = {"items": [], "locations": [], "npcs": []}

        for item in entities.get("items", []):
            name  = item.get("name", "")
            match = self._fuzzy_match(name, inv_lookup)
            resolved["items"].append({
                "name":           name,
                "id":             match["id"]    if match else None,
                "qty_available":  match["qty"]   if match else None,
                "market_price":   match["price"] if match else None,
                "owner":          match["owner"] if match else None,
                "qty_requested":  item.get("qty"),
                "price_offered":  item.get("price_per_unit"),
                "direction":      item.get("direction", "unknown"),
                "found":          match is not None,
            })

        for loc in entities.get("locations", []):
            name      = loc.get("name", "")
            match     = self._fuzzy_match(name, settle_lookup)
            resolved["locations"].append({
                "name":  name,
                "id":    match["id"] if match else None,
                "found": match is not None,
            })

        for npc in entities.get("npcs", []):
            name   = npc.get("name", "")
            npc_id = self._find_npc_id(name, root)
            resolved["npcs"].append({
                "name":  name,
                "id":    npc_id,
                "found": npc_id is not None,
            })

        return resolved

    # ------------------------------------------------------------------ Context block builder

    def build_context_block(self, analysis: dict, resolved: dict) -> str:
        """
        Build the compact '# Context Analysis' section injected into Pass 3's
        dynamic prompt, replacing Conversation History + Immediate Situation.
        """
        parts: list[str] = []

        topic = analysis.get("topic", "")
        if topic:
            parts.append(f"**Situation:** {topic}")

        intent     = analysis.get("intent", "")
        conv_state = analysis.get("conversation_state", "")
        if intent:
            parts.append(f"**Intent:** {intent} ({conv_state})")

        lying = analysis.get("lying_score", 0.0)
        if lying >= 0.5:
            parts.append(
                f"**Deception warning:** lying_score={lying:.2f} — "
                "player claims contradict known facts."
            )

        # Resolved items
        if resolved.get("items"):
            item_lines: list[str] = []
            for it in resolved["items"]:
                if it["found"]:
                    line = (
                        f"  - {it['name']} "
                        f"(id:{it['id']}, "
                        f"available:{it['qty_available']}, "
                        f"market:{it['market_price']}g, "
                        f"direction:{it['direction']}"
                    )
                    if it["qty_requested"] is not None:
                        line += f", requested:{it['qty_requested']}"
                    if it["price_offered"] is not None:
                        line += f", offered_price:{it['price_offered']}"
                    line += ")"
                else:
                    line = f"  - {it['name']}: NOT FOUND in any inventory"
                item_lines.append(line)
            parts.append("**Resolved Items:**\n" + "\n".join(item_lines))

        # Resolved locations
        if resolved.get("locations"):
            loc_lines = [
                f"  - {l['name']} (id:{l['id']})" if l["found"]
                else f"  - {l['name']}: NOT FOUND"
                for l in resolved["locations"]
            ]
            parts.append("**Resolved Locations:**\n" + "\n".join(loc_lines))

        # Resolved NPCs
        if resolved.get("npcs"):
            npc_lines = [
                f"  - {n['name']} (id:{n['id']})" if n["found"]
                else f"  - {n['name']}: NOT FOUND"
                for n in resolved["npcs"]
            ]
            parts.append("**Resolved NPCs:**\n" + "\n".join(npc_lines))

        return "\n".join(parts)
