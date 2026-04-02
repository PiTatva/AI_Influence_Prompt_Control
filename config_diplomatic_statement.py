# ================================================================
#  Section Rules — Diplomacy  (DIPLOMATIC STATEMENT GENERATION)
#  Loaded when the Mission section contains that exact phrase (case-insensitive).
#  Follow config_dialogue.py to see how to use these rules to customize the prompt for this mission type.
# ================================================================

SECTIONS_TO_REMOVE: list[str] = []

SECTIONS_TO_REPLACE: dict[str, str] = {

}

SECTIONS_TO_SUMMARIZE: dict[str, dict] = {
    

}

SECTION_ORDER_PRIORITY: dict[str, int] = {}

ORDER_DEFAULT_PRIORITY: int = 50

BULLETS_TO_KEEP: dict[str, list[str]] = {}

BULLETS_TO_REMOVE: dict[str, list[str]] = {}

PINNED_STATIC_SECTIONS: list[str] = ["Core Rules",]

DYNAMIC_SECTIONS: list[str] = []

# --- INTENT-BASED FILTERING ---
# Set to True to enable intent classification for this mission type.
ENABLE_INTENT_SYSTEM: bool = False
