
# ================================================================
#  AI Influence Prompt Filter
#  Author : Dardbador
#  Description : FastAPI proxy that sits between a Bannerlord mod
#                and Ollama.  Parses the game's markdown prompt,
#                classifies player intent, filters / summarizes
#                sections, and forwards a trimmed prompt with
#                KV-cache-aware static/dynamic splitting.
# ================================================================

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import httpx
import re
import json
import asyncio
import dataclasses
import hashlib
import importlib
import traceback
from typing import Optional
from intent_system import IntentSystem

app = FastAPI()


@app.exception_handler(Exception)
async def _debug_exception_handler(request: Request, exc: Exception):
    """Return the full traceback in the response body during development."""
    tb = traceback.format_exc()
    return JSONResponse(status_code=500, content={"error": str(exc), "traceback": tb})

OLLAMA_URL    = "http://localhost:11434/api/generate"
DEFAULT_MODEL = "mistral"
TOTAL_BUDGET  = 60000   # hard character cap on the final combined prompt
LOGGING       = True    # write diagnostic logs to log_{mission}.txt


# Config lives in intent_config.json  (intent types, headers, bullets, thresholds)
# ENABLE_INTENT_SYSTEM is loaded from each config_*.py file via _apply_rules().

# ================================================================


def _norm_header(h: str) -> str:
    """Strip leading/trailing # markers, spaces, and lowercase — keys are case-insensitive."""
    return h.strip("#").strip().lower()


# ── Logging helpers ─────────────────────────────────────────────────────────
_log_buffer: list[str] = []   # accumulates lines for the current request
_log_path:   str       = "log_unknown.txt"


def _log(msg: str) -> None:
    """Buffer a log line. No-op when LOGGING is False."""
    if LOGGING:
        _log_buffer.append(msg)


def _flush_log() -> None:
    """Overwrite _log_path with all buffered lines, then clear the buffer."""
    if not LOGGING:
        return
    with open(_log_path, "w", encoding="utf-8") as _fh:
        _fh.write("\n".join(_log_buffer) + "\n")
    _log_buffer.clear()


# Maps substrings found in the Mission section content to config module names.
# Checked case-insensitively; first match wins;
# if no match, the prompt is passed through.
_MISSION_TO_CONFIG: dict[str, str] = {
    "analyze diplomatic situation":  "config_analyze_diplomacy",
    "DIPLOMATIC STATEMENT GENERATION" : "config_diplomatic_statement",
    "generate dynamic world events": "config_event",
    "Role-play as a character": "config_dialogue",
}

# These globals are set by _apply_rules() on every request.
# Declared here so the rest of the module can reference them before the first call.
ENABLE_INTENT_SYSTEM:   bool             = False   # overridden per config
SECTIONS_TO_REMOVE:     list[str]        = []
SECTIONS_TO_REPLACE:    dict[str, str]   = {}
SECTIONS_TO_SUMMARIZE:  dict[str, dict]  = {}
SECTION_ORDER_PRIORITY: dict[str, int]   = {}
ORDER_DEFAULT_PRIORITY: int              = 50
BULLETS_TO_KEEP:        dict[str, list]  = {}
BULLETS_TO_REMOVE:      dict[str, list]  = {}
PINNED_STATIC_SECTIONS: list[str]        = []
DYNAMIC_SECTIONS:       list[str]        = []
HEADER_GROUPS:          dict[str, list[str]] = {}


def detect_mission(root: "Section") -> Optional[str]:  # type: ignore[name-defined]
    """Return the config module name matching any section's header or content, or None."""
    for child in root.children:
        # Search both the header and the content — some prompts omit "Mission" in the header.
        text = (_norm_header(child.header) + "\n" + child.content).lower()
        for keyword, cfg_name in _MISSION_TO_CONFIG.items():
            if keyword.lower() in text:
                return cfg_name

    _log("[Warning] No mission keywords found; rules reset to defaults (pass-through mode).")
    return None


def _reset_rules() -> None:
    """Reset all section-rule globals to empty defaults (pass-through — no processing)."""
    global ENABLE_INTENT_SYSTEM
    global SECTIONS_TO_REMOVE, SECTIONS_TO_REPLACE, SECTIONS_TO_SUMMARIZE
    global SECTION_ORDER_PRIORITY, ORDER_DEFAULT_PRIORITY
    global BULLETS_TO_KEEP, BULLETS_TO_REMOVE
    global PINNED_STATIC_SECTIONS, DYNAMIC_SECTIONS, HEADER_GROUPS
    ENABLE_INTENT_SYSTEM   = False
    SECTIONS_TO_REMOVE     = []
    SECTIONS_TO_REPLACE    = {}
    SECTIONS_TO_SUMMARIZE  = {}
    SECTION_ORDER_PRIORITY = {}
    ORDER_DEFAULT_PRIORITY = 50
    BULLETS_TO_KEEP        = {}
    BULLETS_TO_REMOVE      = {}
    PINNED_STATIC_SECTIONS = []
    DYNAMIC_SECTIONS       = []
    HEADER_GROUPS          = {}


def _apply_rules(module_name: str) -> None:
    """Load a config_*.py module and rebind all section-rule globals (normalized)."""
    global ENABLE_INTENT_SYSTEM
    global SECTIONS_TO_REMOVE, SECTIONS_TO_REPLACE, SECTIONS_TO_SUMMARIZE
    global SECTION_ORDER_PRIORITY, ORDER_DEFAULT_PRIORITY
    global BULLETS_TO_KEEP, BULLETS_TO_REMOVE
    global PINNED_STATIC_SECTIONS, DYNAMIC_SECTIONS, HEADER_GROUPS

    m = importlib.import_module(module_name)
    # Reload the module each time so edits to config_*.py take effect without restart.
    importlib.reload(m)
    ENABLE_INTENT_SYSTEM   = getattr(m, "ENABLE_INTENT_SYSTEM", True)
    SECTIONS_TO_REMOVE     = [_norm_header(h) for h in m.SECTIONS_TO_REMOVE]
    SECTIONS_TO_REPLACE    = {_norm_header(k): v for k, v in m.SECTIONS_TO_REPLACE.items()}
    SECTIONS_TO_SUMMARIZE  = {_norm_header(k): v for k, v in m.SECTIONS_TO_SUMMARIZE.items()}
    SECTION_ORDER_PRIORITY = {_norm_header(k): v for k, v in m.SECTION_ORDER_PRIORITY.items()}
    ORDER_DEFAULT_PRIORITY = m.ORDER_DEFAULT_PRIORITY
    BULLETS_TO_KEEP        = {_norm_header(k): v for k, v in m.BULLETS_TO_KEEP.items()}
    BULLETS_TO_REMOVE      = {_norm_header(k): v for k, v in m.BULLETS_TO_REMOVE.items()}
    PINNED_STATIC_SECTIONS = [_norm_header(h) for h in m.PINNED_STATIC_SECTIONS]
    DYNAMIC_SECTIONS       = [_norm_header(s) for s in m.DYNAMIC_SECTIONS]
    HEADER_GROUPS          = {_norm_header(k): [_norm_header(mb) for mb in v]
                               for k, v in getattr(m, "HEADER_GROUPS", {}).items()}


# Initialize with the default (dialogue) rules at startup.
_apply_rules("config_dialogue")

# Intent system — loaded from intent_config.json
_intent_system = IntentSystem("intent_config.json")


def _find_in_list(header_norm: str, lst: list) -> bool:
    """True if any config key is a substring of the header (or matches exactly)."""
    return any(k in header_norm for k in lst)

# This method is so that you can match config keys by substring instead of exact match if desired.
def _find_in_dict(header_norm: str, mapping: dict):
    """Return value of the first config key that is a substring of the header, or None."""
    for k, v in mapping.items():
        if k in header_norm:
            return v
    return None


# ---------------------------------------------------------
# Intent-based tree filtering
# ---------------------------------------------------------

def filter_tree_by_intents(
    root: "Section",
    allowed_headers: set[str],
    bullet_filters: dict[str, list[str]],
    include_unmatched: bool = False,
    always_included: list[str] = [],
) -> "Section":
    """Keep only children matching allowed headers; apply bullet filters where specified.

    - Headers in always_included are never bullet-filtered by intent.
    - If include_unmatched is True, unmatched headers are appended at the end.
    """
    filtered = Section(header=root.header, level=root.level, content=root.content)
    matched_children: list[Section] = []
    unmatched_children: list[Section] = []
    for child in root.children:
        hn = _norm_header(child.header)
        if not any(ah in hn for ah in allowed_headers):
            if include_unmatched:
                unmatched_children.append(child)
            continue
        # Apply bullet filtering unless this is an always-included header
        bp = _find_in_dict(hn, bullet_filters)
        if bp and not _find_in_list(hn, always_included):
            child.content = _intent_system.filter_content_by_bullets(child.content, bp)
        matched_children.append(child)
    filtered.children = matched_children + unmatched_children
    return filtered


# ---------------------------------------------------------
# Bullet-level summarization helper
# ---------------------------------------------------------

def _split_bullets_for_summary(
    content: str, patterns: list[str]
) -> tuple[str, str]:
    """
    Split section content into (kept_verbatim, to_summarize) based on
    bullet_patterns.  Bullets matching ANY pattern go into to_summarize;
    everything else stays in kept_verbatim.
    """
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

    kept_parts: list[str] = []
    summarize_parts: list[str] = []
    for is_bullet, grp in groups:
        text = '\n'.join(grp)
        if not is_bullet:
            kept_parts.append(text)
        elif any(re.search(p, text, re.IGNORECASE) for p in patterns):
            summarize_parts.append(text)
        else:
            kept_parts.append(text)

    return '\n'.join(kept_parts).strip(), '\n'.join(summarize_parts).strip()


# ---------------------------------------------------------
# Section tree
# ---------------------------------------------------------

@dataclasses.dataclass
class Section:
    header:   str                        # e.g. "## GENERAL RULES"  (or "__root__")
    level:    int                        # 1-6, or 0 for root
    content:  str                        # raw text directly below this heading
    children: list["Section"] = dataclasses.field(default_factory=list)

    def full_text(self) -> str:
        """Flatten this node + all descendants into one string (for summarization)."""
        parts = []
        if self.content:
            parts.append(self.content)
        for child in self.children:
            parts.append(child.header)
            child_body = child.full_text()
            if child_body:
                parts.append(child_body)
        return "\n".join(parts)


# ---------------------------------------------------------
# Parser — builds a Section tree
# ---------------------------------------------------------

# Matches any Markdown heading at the start of a line:
#   # H1 Title
#   ## H2 Title
#   ### H3 Title ###   (trailing hashes are stripped from the captured key)
_HEADING_RE = re.compile(
    r"^(#{1,6})[ \t]+([^\n]+?)[ \t]*#*[ \t]*$",
    re.MULTILINE,
)


def parse_tree(prompt: str) -> Section:
    """
    Parse the raw prompt into a Section tree.
    Returns a virtual root node (level=0) whose children are the top-level headings.
    Text before the first heading is stored in root.content (preamble).
    """
    root = Section(header="__root__", level=0, content="")
    matches = list(_HEADING_RE.finditer(prompt))

    if not matches:
        root.content = prompt.strip()
        return root

    root.content = prompt[: matches[0].start()].strip()
    stack: list[Section] = [root]

    for idx, match in enumerate(matches):
        level  = len(match.group(1))
        header = match.group(1) + " " + match.group(2).strip()

        # Content = raw text between THIS heading and the NEXT heading (any level)
        start   = match.end()
        end     = matches[idx + 1].start() if idx + 1 < len(matches) else len(prompt)
        content = prompt[start:end].strip()

        node = Section(header=header, level=level, content=content)

        # Pop stack until we find a node with a strictly lower level (= the parent)
        while len(stack) > 1 and stack[-1].level >= level:
            stack.pop()

        stack[-1].children.append(node)
        stack.append(node)

    return root


# ---------------------------------------------------------
# Regroup — promote all headers to # and re-nest groups
# ---------------------------------------------------------

def regroup(root: Section) -> Section:
    """
    Builds a clean two-level # / ## tree:

    Step 1 — Promote: every node at any depth is extracted into a flat list
             as a standalone # (level=1) node with its direct content only.
             Original parent-child links are discarded (children become siblings).

    Step 2 — Re-nest: nodes whose normalised title contains a key from
             HEADER_GROUPS['<leader>'] member list are moved as ## (level=2)
             children of their group leader, in the order defined by HEADER_GROUPS.

    Step 3 — Non-members (including group leaders themselves) remain as
             top-level # siblings and are sorted later by _priority().

    Matching uses substring against normalised (lowercased, hash-stripped) titles.

    Why do this? Because the prompt's heading structure is often inconsistent and messy.
    """
    # Step 1: extract every node at any depth into a flat list (no children).
    flat: list[Section] = []

    def _extract(node: Section) -> None:
        title = node.header.lstrip("#").strip()
        flat.append(Section(header="# " + title, level=1, content=node.content.strip()))
        for child in node.children:
            _extract(child)

    for child in root.children:
        _extract(child)

    # Step 2a: locate the Section object for each group leader key.
    leader_nodes: dict[str, Section] = {}
    for node in flat:
        hn = _norm_header(node.header)
        for lk in HEADER_GROUPS:
            if lk in hn and lk not in leader_nodes:
                leader_nodes[lk] = node
                break

    # Step 2b: reverse map  member_key → leader_key (for fast lookup).
    member_to_leader: dict[str, str] = {
        mk: lk
        for lk, members in HEADER_GROUPS.items()
        for mk in members
    }

    def _find_member_key(hn: str) -> Optional[str]:
        for mk in member_to_leader:
            if mk in hn:
                return mk
        return None

    # Step 3: partition — non-members go to top level; members are stashed.
    new_root = Section(header="__root__", level=0, content=root.content)
    pending_members: dict[str, Section] = {}   # member_key → Section

    for node in flat:
        hn = _norm_header(node.header)
        mk = _find_member_key(hn)
        if mk is not None:
            pending_members[mk] = node          # will become ## under its leader
        else:
            new_root.children.append(node)      # stays as # top-level

    # Step 4: attach members as ## children in HEADER_GROUPS-defined order.
    for lk, members in HEADER_GROUPS.items():
        leader = leader_nodes.get(lk)
        if leader is None:
            continue
        for mk in members:
            member = pending_members.get(mk)
            if member is None:
                continue
            title = member.header.lstrip("#").strip()
            leader.children.append(Section(header="## " + title, level=2,
                                           content=member.content))

    return new_root


# ---------------------------------------------------------
# Bullet-level filtering helper
# ---------------------------------------------------------

def filter_bullets(header: str, content: str) -> str:
    """
    Apply BULLETS_TO_KEEP / BULLETS_TO_REMOVE to bullet lines inside a
    section's content string.

    A bullet group = a line starting with optional whitespace + '-' or '*',
    plus any immediately following continuation lines (indented or blank).
    Non-bullet lines (plain paragraphs) are always preserved.
    BULLETS_TO_KEEP takes priority over BULLETS_TO_REMOVE.
    """
    keep_patterns   = _find_in_dict(_norm_header(header), BULLETS_TO_KEEP) or []
    remove_patterns = _find_in_dict(_norm_header(header), BULLETS_TO_REMOVE) or []
    if not keep_patterns and not remove_patterns:
        return content

    _BULLET_START = re.compile(r'^[ \t]*[-*]')

    lines  = content.split('\n')
    groups: list[tuple[bool, list[str]]] = []
    cur:    list[str] = []
    cur_is_bullet     = False

    for line in lines:
        is_bullet = bool(_BULLET_START.match(line))
        if is_bullet:
            if cur:
                groups.append((cur_is_bullet, cur))
            cur           = [line]
            cur_is_bullet = True
        elif cur_is_bullet and (line.startswith(' ') or line.startswith('\t') or line == ''):
            cur.append(line)  # continuation of current bullet
        else:
            if cur:
                groups.append((cur_is_bullet, cur))
            cur           = [line]
            cur_is_bullet = False
    if cur:
        groups.append((cur_is_bullet, cur))

    result: list[str] = []
    for is_bullet, grp in groups:
        if not is_bullet:
            result.extend(grp)
            continue
        text = '\n'.join(grp)
        if keep_patterns:
            if any(re.search(p, text, re.IGNORECASE) for p in keep_patterns):
                result.extend(grp)
        else:
            if not any(re.search(p, text, re.IGNORECASE) for p in remove_patterns):
                result.extend(grp)

    return '\n'.join(result).strip()


# ---------------------------------------------------------
# Ollama summarisation helper
# ---------------------------------------------------------

async def summarize_section(
    client: httpx.AsyncClient,
    model: str,
    header: str,
    content: str,
    max_chars: int,
    extra_instructions: str = "",
) -> str:
    if len(content) <= max_chars:
        return content

    word_limit = max(20, max_chars // 6)
    summarize_prompt = (
        f"Section: {header}\n"
        f"{content[:3000]}\n"
        f"- Summarize Section in at most {word_limit} words. "
        "- Preserve faction names, rulers, key numbers, and diplomatic status. "
        "- Don't modify/forget id's in brackets like (kingdom_id:khuzait)."

    )
    if extra_instructions:
        summarize_prompt += f"\n{extra_instructions}"

    payload = {"model": model, "prompt": summarize_prompt, "stream": False}

    try:
        resp = await client.post(OLLAMA_URL, json=payload, timeout=90.0)
        resp.raise_for_status()
        summary = resp.json().get("response", "").strip()
        if summary:
            return summary[:max_chars]
    except Exception:
        pass

    return content[:max_chars]   # fallback: hard truncate


# ---------------------------------------------------------
# Process the tree — apply all rules recursively
# ---------------------------------------------------------

async def process_node(
    client: httpx.AsyncClient,
    model: str,
    node: Section,
) -> Optional[Section]:
    """
    Recursively applies REMOVE / REPLACE / SUMMARIZE / PASSTHROUGH rules.
    Returns None if the node (and its subtree) should be dropped.
    Children of the same node are processed concurrently via asyncio.gather.
    """
    if node.header != "__root__":

        # ## and deeper nodes are sub-content of their # group parent;
        # rule-matching is skipped — they pass through as-is.
        if node.level >= 2:
            return node

        # PINNED — skip summarization (replacement text is static, summary is not);
        #          but still allow REPLACE so overrides apply.
        if _find_in_list(_norm_header(node.header), PINNED_STATIC_SECTIONS):
            replace_val = _find_in_dict(_norm_header(node.header), SECTIONS_TO_REPLACE)
            if replace_val is not None:
                node.content  = replace_val
                node.children = []
            return node

        # BULLET FILTER — applied first so later rules see the trimmed content
        node.content = filter_bullets(node.header, node.content)

        # REMOVE — drop this node and all its children
        if _find_in_list(_norm_header(node.header), SECTIONS_TO_REMOVE):
            return None

        # REPLACE — swap content + drop children
        replace_val = _find_in_dict(_norm_header(node.header), SECTIONS_TO_REPLACE)
        if replace_val is not None:
            node.content  = replace_val
            node.children = []
            return node

        # SUMMARIZE — summarize full subtree text (or just matching bullets)
        cfg = _find_in_dict(_norm_header(node.header), SECTIONS_TO_SUMMARIZE)
        if cfg is not None:
            bullet_patterns = cfg.get("bullet_patterns")
            if bullet_patterns:
                # Bullet-level: only summarize matching bullets, keep the rest
                full = node.full_text()
                kept, to_summarize = _split_bullets_for_summary(full, bullet_patterns)
                if to_summarize:
                    summarized = await summarize_section(
                        client, model,
                        node.header,
                        to_summarize,
                        cfg["max_chars"],
                        cfg.get("extra_instructions", ""),
                    )
                    node.content = (kept + "\n" + summarized).strip()
                else:
                    node.content = kept
            else:
                # Whole-section summarization (original behaviour)
                node.content = await summarize_section(
                    client, model,
                    node.header,
                    node.full_text(),
                    cfg["max_chars"],
                    cfg.get("extra_instructions", ""),
                )
            node.children = []
            return node

    # PASSTHROUGH — recurse into children (concurrently at each level)
    if node.children:
        results = await asyncio.gather(
            *[process_node(client, model, child) for child in node.children]
        )
        node.children = [r for r in results if r is not None]

    return node


# ---------------------------------------------------------
# Flatten the processed tree back into a prompt string
# ---------------------------------------------------------

def _priority(node: Section) -> int:
    """Sort key for # top-level nodes. Option B scale: static 10–90,
    semi-dynamic 100–140, DEFAULT 150, dynamic 160–990."""
    hn = _norm_header(node.header)
    return _find_in_dict(hn, SECTION_ORDER_PRIORITY) or ORDER_DEFAULT_PRIORITY


def flatten_tree(node: Section) -> str:
    """
    Recursively flatten the tree into a string.
    Children at every level are sorted by SECTION_ORDER_PRIORITY before output.
    """
    parts: list[str] = []

    if node.header == "__root__":
        if node.content:
            parts.append(node.content[:500])   # preamble cap
    else:
        parts.append(node.header)
        if node.content:
            parts.append(node.content)

    # Sort only at root level (level=0); ## children of # groups keep the
    # insertion order set by regroup() so HEADER_GROUPS order is preserved.
    children_to_render = sorted(node.children, key=_priority) if node.level == 0 else node.children
    for child in children_to_render:
        child_text = flatten_tree(child)
        if child_text:
            parts.append(child_text)

    return "\n\n".join(parts)


# ---------------------------------------------------------
# FastAPI endpoint
# ---------------------------------------------------------

# Persistent static-prefix context cache.
# key   : "{session_key}:{static_hash}"  — one entry per unique prefix combination
# value : list[int]  (Ollama context tokens for that prefix)
_static_ctx_cache: dict[str, list[int]] = {}

OLLAMA_BASE_URL = "http://localhost:11434"


@app.get("/api/tags")
async def tags():
    """Proxy Ollama's /api/tags so mods can verify connectivity against this proxy."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=10.0)
        resp.raise_for_status()
        return resp.json()


async def _run_pipeline(
    original_prompt: str,
    model: str,
    session_key: str,
    player_input: str,
    forward_data: dict,
) -> dict:
    """
    Core filtering + KV-cache pipeline shared by /api/generate and /api/chat.
    Mutates forward_data to set prompt/context/stream/keep_alive.
    Returns the raw Ollama /api/generate JSON response dict.
    """
    global _log_buffer, _log_path
    _log_buffer = []
    _log_path   = "log_unknown.txt"

    root = parse_tree(original_prompt)
    root = regroup(root)

    # Extract player_input from the "Your Response" section if present.
    # That section is already included in the dynamic prompt (priority 99),
    # so we only use its content for intent classification — no separate append.
    _player_input_from_section = False
    if not player_input:
        for _child in root.children:
            if "your response" in _norm_header(_child.header):
                player_input = _child.content.strip()
                _player_input_from_section = True
                break

    _mission = detect_mission(root)
    if _mission:
        _apply_rules(_mission)
        _log_path = f"log_{_mission.removeprefix('config_')}.txt"
    else:
        _reset_rules()

    # ── Intent-based section filtering ────────────────────────────────────
    if ENABLE_INTENT_SYSTEM and player_input:
        async with httpx.AsyncClient() as client:
            intent_data     = await _intent_system.extract(client, model, player_input)
        _log(f"  Detected intents: {intent_data}")
        top_intents     = _intent_system.select(intent_data)
        allowed_headers = _intent_system.resolve_headers(top_intents)
        bullet_filters  = _intent_system.resolve_bullets(top_intents)
        root = filter_tree_by_intents(
            root, allowed_headers, bullet_filters,
            include_unmatched=_intent_system.include_unmatched,
            always_included=_intent_system.always_headers_norm,
        )
        _log(", ".join(child.header for child in root.children))

    async with httpx.AsyncClient() as client:
        processed_root = await process_node(client, model, root)

    # ── Split sections into static (rules/world) vs dynamic (current state) ───
    def _is_dynamic(node: Section) -> bool:
        hn = _norm_header(node.header)
        if any(p in hn for p in PINNED_STATIC_SECTIONS):
            return False
        return any(d in hn for d in DYNAMIC_SECTIONS)

    def _is_pinned(node: Section) -> bool:
        hn = _norm_header(node.header)
        return any(p in hn for p in PINNED_STATIC_SECTIONS)

    sorted_children = sorted(processed_root.children, key=_priority)
    if len(PINNED_STATIC_SECTIONS) > 0:
        static_children  = [c for c in sorted_children if     _is_pinned(c)]
        dynamic_children = [c for c in sorted_children if not _is_pinned(c)]
    else:
        static_children  = [c for c in sorted_children if not _is_dynamic(c)]
        dynamic_children = [c for c in sorted_children if     _is_dynamic(c)]

    static_root          = Section(header="__root__", level=0, content=processed_root.content)
    static_root.children = static_children
    dynamic_root          = Section(header="__root__", level=0, content="")
    dynamic_root.children = dynamic_children

    static_prompt  = flatten_tree(static_root) [:int(TOTAL_BUDGET * 0.75)]
    dynamic_prompt = flatten_tree(dynamic_root)[:TOTAL_BUDGET - len(static_prompt)]

    # Only append player_input explicitly when it was NOT already present as
    # the "Your Response" section in the prompt (which is included via DYNAMIC_SECTIONS).
    if player_input and not _player_input_from_section:
        dynamic_prompt = dynamic_prompt + f"\n\n- Player Input: \n{player_input}"

    static_hash = hashlib.sha256(static_prompt.encode()).hexdigest()[:16]
    cache_key   = f"{session_key}:{static_hash}"

    _log(f"[KV-cache] session_key={session_key!r}  static_hash={static_hash}")
    _log(f"[KV-cache] static_prompt={len(static_prompt)} chars  dynamic_prompt={len(dynamic_prompt)} chars")
    _log(f"[KV-cache] known prefixes: {list(_static_ctx_cache.keys())}")

    async with httpx.AsyncClient() as client:
        if cache_key not in _static_ctx_cache:
            _log(f"[KV-cache] MISS — warming new prefix combination ({cache_key!r})")
            warm_payload = {
                "model":       model,
                "prompt":      static_prompt,
                "num_predict": 0,
                "stream":      False,
                "keep_alive":  -1,
            }
            warm_resp = await client.post(OLLAMA_URL, json=warm_payload, timeout=120.0)
            warm_resp.raise_for_status()
            static_context = warm_resp.json().get("context", [])
            _log(f"[KV-cache] warm response — context tokens returned: {len(static_context)}")
            _static_ctx_cache[cache_key] = static_context
        else:
            static_context = _static_ctx_cache[cache_key]
            _log(f"[KV-cache] HIT — reusing cached context ({len(static_context)} tokens) for {cache_key!r}")

        forward_data["keep_alive"] = -1
        forward_data["stream"]     = False

        _log(f"[Prompt] Received from mod: {len(original_prompt)} chars")
        if static_context:
            _log(f"[KV-cache] Sending dynamic-only prompt ({len(dynamic_prompt)} chars) with context prefix")
            forward_data["prompt"]  = dynamic_prompt
            forward_data["context"] = static_context
            _log(f"[Prompt] After processing → sending to Ollama: {len(static_prompt) + len(dynamic_prompt)} chars , + cached static context)")
        else:
            full_prompt = (static_prompt + "\n------------\n" + dynamic_prompt)[:TOTAL_BUDGET]
            _log(f"[KV-cache] WARNING — no context returned by warm; falling back to full prompt ({len(full_prompt)} chars)")
            forward_data["prompt"] = full_prompt
            forward_data.pop("context", None)
            _log(f"[Prompt] After processing → sending to Ollama: {len(full_prompt)} chars (full prompt fallback)")

        resp = await client.post(OLLAMA_URL, json=forward_data, timeout=120.0)
        resp.raise_for_status()
        _log(f"[Ollama Response] {resp.json().get('response', '')}")
        _log(f"{static_prompt}\n------------\n{dynamic_prompt}")
        _flush_log()
        return resp.json()


@app.post("/api/generate")
async def proxy(request: Request):
    data = await request.json()

    original_prompt: str = data.pop("prompt", "")
    model: str           = data.get("model", DEFAULT_MODEL)
    session_key: str     = data.pop("session_id", model)

    return await _run_pipeline(original_prompt, model, session_key, "", data)


@app.post("/api/chat")
async def chat_proxy(request: Request):
    data = await request.json()

    messages: list     = data.get("messages", [])
    model: str         = data.get("model", DEFAULT_MODEL)
    session_key: str   = data.pop("session_id", model)

    # The mod puts the game-world context in the system message and the
    # player's line in the last user message.
    system_content = ""
    player_input   = ""
    for msg in messages:
        role = msg.get("role", "")
        if role == "system":
            system_content = msg.get("content", "")
        elif role == "user":
            player_input = msg.get("content", "")   # last user message wins

    # Connectivity / "hello" test — no game context present, proxy directly.
    if not system_content:
        data["stream"] = False
        async with httpx.AsyncClient() as client:
            resp = await client.post(f"{OLLAMA_BASE_URL}/api/chat", json=data, timeout=30.0)
            resp.raise_for_status()
            return resp.json()

    # Build forward payload without the messages key; pipeline produces a prompt.
    forward_data = {k: v for k, v in data.items() if k not in ("messages", "session_id")}
    gen_resp = await _run_pipeline(system_content, model, session_key, player_input, forward_data)

    # Convert /api/generate response → /api/chat response format expected by the mod.
    return {
        "model":      gen_resp.get("model", model),
        "created_at": gen_resp.get("created_at", ""),
        "message": {
            "role":    "assistant",
            "content": gen_resp.get("response", ""),
        },
        "done":              gen_resp.get("done", True),
        "done_reason":       gen_resp.get("done_reason", "stop"),
        "total_duration":    gen_resp.get("total_duration", 0),
        "load_duration":     gen_resp.get("load_duration", 0),
        "prompt_eval_count": gen_resp.get("prompt_eval_count", 0),
        "eval_count":        gen_resp.get("eval_count", 0),
        "eval_duration":     gen_resp.get("eval_duration", 0),
    }

