# ================================================================
#  Section Rules — Diplomacy  (MISSION: Analyze Diplomatic Situation)
#  Loaded when the Mission section contains that exact phrase (case-insensitive).
# ================================================================

SECTIONS_TO_REMOVE: list[str] = []

SECTIONS_TO_REPLACE: dict[str, str] = {
    "Core Rules":""" 
    * event_update: **50 - 400 chars**
    * Must include:
        * Reaction (shock/anger/etc.)
        * Outcome (what actually happened)
        * Atmosphere (tension/change)
""",
    "DATA INTERPRETATION PRIORITY": """1. **CURRENT DIPLOMATIC SITUATION = TRUE STATE**
2. NEGOTIATION HISTORY = attempts/statements only
3. If conflict → TRUST CURRENT STATE """ ,    
}

SECTIONS_TO_SUMMARIZE: dict[str, dict] = {
    "CURRENT DIPLOMATIC EVENT": {
        "max_chars": 1200,
        "extra_instructions": (
            "Summarize days to its own paragraph not more than 100 words."
            "Keep info helpful for diplomacy only."
        ),
    },
    "PARTICIPATING KINGDOMS":    {
        "max_chars": 1000,
        "extra_instructions": (
            "- Keep Id's with their names intact like (string_id:empire_w)"
        ),
    },
    "COMPLETE NEGOTIATION HISTORY":    {
        "max_chars": 300,
        "extra_instructions": (
            "- HEAVILY SUMMARIZE with comma-separated key events only" 
            "- For ex. Western Empire: Demands investigation, reinforces borders."     
        )
    }
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
