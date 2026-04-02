import httpx
import json
import re

OLLAMA_URL = "http://localhost:11434/api/generate"


class IntentSystem:
    def __init__(self, config_path: str):
        with open(config_path, "r", encoding="utf-8") as f:
            self.cfg = json.load(f)

        # Pre-normalize companion header keys once at startup
        self._companions: dict[str, list[str]] = {
            self._norm(k): [self._norm(c) for c in v]
            for k, v in self.cfg.get("companion_headers", {}).items()
        }
        self._always_norm: list[str] = [
            self._norm(h) for h in self.cfg["always_headers"]
        ]

    # ------------------------------------------------------------------ helpers

    def _norm(self, h: str) -> str:
        return h.strip("#").strip().lower()

    @property
    def always_headers_norm(self) -> list[str]:
        """Normalized list of always-included headers (for use in tree filtering)."""
        return self._always_norm

    @property
    def include_unmatched(self) -> bool:
        """Whether unmatched sections should be appended at the end."""
        return self.cfg.get("include_unmatched_sections", False)

    # ------------------------------------------------------------------ public API

    async def extract(self, client: httpx.AsyncClient, model: str, text: str) -> dict:
        """Small Ollama call to classify player input into intents + entities."""
        intent_list = ", ".join(self.cfg["intent_types"])
        prompt = (
            "Classify Input into intents.\n "
            "Return ONLY valid JSON format. NEVER add new fields (no markdown, no explanation):\n"
            '{"intents": [{"type": "<intent1>", "score": <0.0 to 1.0>}, {"type": "<intent2>", "score": <0.0 to 1.0>}], '
            '"entities": {"npc": null, "topic": null, "item": null}, '
            '"locations": []}\n\n'
            f"- Valid intent types: [{intent_list}]\n"
            "- 'parley' is for war,peace,negotiation talks; 'kingdom_actions' for Trade agreement between kingdoms, declaring war,Taking vassals,mercenaries,alliances.\n"
            f'Input: "{text[:500]}"'
        )
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "num_predict": self.cfg.get("max_tokens", 300),
            "temperature": 0.1,
        }
        try:
            resp = await client.post(OLLAMA_URL, json=payload, timeout=30.0)
            resp.raise_for_status()
            raw = resp.json().get("response", "").strip()
            if "```" in raw:
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            return json.loads(raw)
        except Exception:
            return {"intents": [{"type": "greet", "score": 1.0}], "entities": {}, "locations": []}

    def select(self, data: dict) -> list[str]:
        """Pick the top intents above the score threshold; default to 'greet'."""
        ranked = sorted(data.get("intents", []), key=lambda x: x.get("score", 0), reverse=True)
        threshold = self.cfg.get("threshold", 0.4)
        max_count = self.cfg.get("max_intents", 2)
        return [
            i["type"] for i in ranked
            if i.get("score", 0) >= threshold and i.get("type") in self.cfg["intent_types"]
        ][:max_count] or ["greet"]

    def resolve_headers(self, intents: list[str]) -> set[str]:
        """Collect all normalized section headers needed for the given intents,
        including companion headers for sections that lose parent context after flattening."""
        headers: set[str] = set(self._always_norm)
        for intent in intents:
            for h in self.cfg["intent_to_headers"].get(intent, []):
                headers.add(self._norm(h))
            for h in self.cfg["intent_to_bullets"].get(intent, {}):
                headers.add(self._norm(h))
        # Expand companion headers
        companions: set[str] = set()
        for h in headers:
            for key, companion_list in self._companions.items():
                if key in h:
                    companions.update(companion_list)
        headers.update(companions)
        return headers

    def resolve_bullets(self, intents: list[str]) -> dict[str, list[str]]:
        """Merge bullet-point regex patterns across all active intents."""
        merged: dict[str, list[str]] = {}
        for intent in intents:
            for h, patterns in self.cfg["intent_to_bullets"].get(intent, {}).items():
                key = self._norm(h)
                merged.setdefault(key, []).extend(patterns)
        return merged

    def filter_content_by_bullets(self, content: str, patterns: list[str]) -> str:
        """Keep only bullet lines matching ANY pattern; non-bullet lines are always kept."""
        _BULLET_START = re.compile(r'^[ \t]*[-*]')
        lines = content.split('\n')
        groups: list[tuple[bool, list[str]]] = []
        cur: list[str] = []
        cur_is_bullet = False

        for line in lines:
            is_bullet = bool(_BULLET_START.match(line))
            if is_bullet:
                if cur:
                    groups.append((cur_is_bullet, cur))
                cur = [line]
                cur_is_bullet = True
            elif cur_is_bullet and (line.startswith(' ') or line.startswith('\t') or line == ''):
                cur.append(line)
            else:
                if cur:
                    groups.append((cur_is_bullet, cur))
                cur = [line]
                cur_is_bullet = False
        if cur:
            groups.append((cur_is_bullet, cur))

        result: list[str] = []
        for is_bullet, grp in groups:
            if not is_bullet:
                result.extend(grp)
                continue
            text = '\n'.join(grp)
            if any(re.search(p, text, re.IGNORECASE) for p in patterns):
                result.extend(grp)

        return '\n'.join(result).strip()