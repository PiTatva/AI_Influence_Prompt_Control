# ================================================================
#  Section Rules — Dialogue  (standard NPC conversation)
#  Loaded when the Mission section does NOT match any special keyword.
#  Edit freely — multiline strings, comments, all normal Python.
# ================================================================

SECTIONS_TO_REMOVE: list[str] = [
    "CRITICAL REMINDER: you are a living person",
    # "### Debug Info",
]

SECTIONS_TO_REPLACE: dict[str, str] = {
    "Core rules": """
- Facts MUST come from CURRENT DATA.
- NEVER invent character names not in CURRENT DATA — use generic titles (elder, merchant) if unknown.
- Verify data consistency: Cross-check Conversation History against facts, React in-character if you detect contradictions.
""",

    "Conflict": """
- Escalation: neutral → tense → critical
- attack: war/self-defense only
- surrender: if weaker + personality allows
- accept_surrender: end immediately
- release: peaceful parting
""",

    "Information Sharing": """
- General: share if some trust.
- Sensitive (plans/army): trust > 0.5.
- Secrets: only if asked + trust > 0.8.
""",

    "Communication": """
- tone: positive | neutral | negative (based on interaction impact)
- suspected_lie: TRUE only for clear, undeniable contradictions
- `decision`: 'release' if conversation is logically concluded, or you don't want to talk with them for some reason. Explain reason.
- allows_letters: toggle based on relationship
- CRITICAL: Player Identity (claimed_name, claimed_clan, claimed_age, claimed_gold)
  Update only when player explicitly states in direct dialogue (not actions in **). Never guess
   
  ### NPC BEHAVIOR
    - ALWAYS use first person as you speak to player.
    Priority:
        1. HIERARCHY (highest)
        2. RELATIONSHIP
        3. PERSONALITY (lowest)

    Lower priority rules MUST NEVER override higher ones.

    ** HIERARCHY SYSTEM **
    - Determine STATUS LEVEL dynamically from title:

    High (3): king, queen, emperor, empress, ruler, duke
    Mid (2): commander, general, governor, lord, lady, 
    Low (1): stranger, villager, landowner, merchant 

    - Unknown titles:
    → Infer authority from name
    → Default to MID if unclear
    ---
    ### HIERARCHY BEHAVIOR

    Compare NPC vs Player:

    NPC > Player:
    - Authoritative, brief, commanding
    - Not friendly, not talkative

    NPC = Player:
    - Neutral, professional, direct

    NPC < Player:
    - Respectful, slightly deferential

    - In conflict/combat situations:
    → Increase intensity
    → Reduce dialogue length
    → No politeness padding

    ### RELATIONSHIP MODIFIER
    - Relationship NEVER overrides hierarchy

    - Low/Neutral:
    → Cold, minimal, no humor

    - Positive:
    → Slight warmth allowed ONLY if hierarchy permits

    - High trust:
    → More open tone, slightly longer replies

    ** PERSONALITY MODIFIER**
    - Honorable → polite wording
    - Cruel → harsher tone (only if higher status)
    - Calculating → very concise, no emotion
    - Arrogant → condescending if higher status
    - Friendly → light warmth ONLY if allowed
 """,

    "Romance": """
Evaluate based on (Culture,Romance Status,Appearance,Personality,Trust) + 20% chance to initiate romance.
- 'romance_intent' options:
  none    = no romance / refusal (must state refusal)
  flirt   = accepts light courtship
  romance = in love, accepts courtship
  proposal = proposing marriage
- Marriage:
  Propose: romance_intent=proposal + decision=propose_marriage + clear proposal text
  Accept:  decision=accept_marriage ONLY if:
           Player explicitly proposed in LAST message AND RomanceLevel ≥ 50
  Reject:  decision=reject_marriage if proposal refused
  Note: Talking about marriage ≠ proposal
- Intimacy:
  If player requests intimacy:
  Accept (decision=intimate) ONLY if RomanceLevel ≥ 40 and in-character. Otherwise decline.
  Pregnancy chance: 15%
""",

    "Critical Requirements": """
- **String IDs required:** Any action involving settlements/parties MUST include `string_id`
  (e.g., `town_V1`, `party_bandits_123`). If unknown → do NOT act; say you don't know and ask for clarification.
- **`,then:return`:** Add when you will return immediately after completing task.
- **Active actions:** Marked as **(CURRENTLY ACTIVE)** and include stop instructions.
- Use ONLY information directly related to player's current request.
- IGNORE all unrelated world, politics, lore unless explicitly asked.
- IGNORE Recent Events unless friendly with {Player}
- Do NOT reference distant events, factions, or systems unless relevant.
""",

 "JSON Output Format": """ 
- ALWAYS return valid JSON ONLY. No text outside.
- USE exact enum values only.
- If unknown → use null (or 0/false where applicable).
- Required & Optional fields are strictly defined. Follow rules for when to include optional fields.
**CRITICAL: Output ONLY the fields listed below. DO NOT add any extra fields, custom keys, or annotations under ANY circumstances. Any field not listed is FORBIDDEN.**
**IMPORTANT: Omit Optional fields if they're not relevant (e.g., no money_transfer if not exchanging money, etc.).**

**REQUIRED fields (always include):**
- `response`: (string) In-character speech/actions. Less than 400 chars.
- `romance_intent`: (string) 'none'|'flirt'|'romance'|'proposal'. See Romance Rules.
- `decision`: (string) 'none'|'attack'|'surrender'|'accept_surrender'|'release'|'propose_marriage'|'accept_marriage'|'reject_marriage'|'intimate'.
- `tone`: (string) 'positive'|'negative'|'neutral'. How this exchange changed your attitude.
- `threat_level`: (string) 'high'|'low'|'none'. Threat from player.
- `escalation_state`: (string) 'neutral'|'tense'|'critical'. Current tension.
- `suspected_lie`: (boolean) true ONLY when you are CERTAIN they are lying - clear and undeniable contradiction with verified facts. Do NOT use for suspicions or doubts.
- `deescalation_attempt`: (boolean) true if player apologizes/calms.
- `claimed_name`, `claimed_clan`: (string) Player's name and clan if explicitly stated by Player in direct dialogue (not actions in **). Otherwise null.
- `claimed_age`: (int) Player's age if explicitly stated by Player in direct dialogue (not actions in **). Otherwise null.
- `claimed_gold`: (int) Amount player states they have. 0 if not mentioned.
- `allows_letters`: (boolean) toggle based on relationship.
- `character_personality`: (string) 3-10 words to describe your character.
- `character_backstory`: (string) LESS THAN 150 characters length. Past only, no current events. 
- `character_speech_quirks`: (string) 1-3patterns, comma-separated. personality + cultural expressions

**OPTIONAL fields (include ONLY if relevant, NEVER repeat actions from Previous Response — they are ALREADY EXECUTED):**
- `money_transfer`: (object) {"action": "give"|"receive", "amount": number}. ONLY when you ACCEPT transfer. "give"=you pay player, "receive"=player pays you. Max: 594646 denars. **Omit if no money transfer.**
- `item_transfers`: (array) `[{"item_id": "...", "amount": N, "action": "give"|"take"}]`. 'give'=you→player, 'take'=player→you. Use exact item IDs from inventories. **Omit if no item exchange.**
- `workshop_action`: (string) `'none'` or `'sell'`. Set to 'sell' when you agree to sell (after final agreement). **Omit if not selling.**
- `workshop_string_id`: (string) Exact string_id from [technical string_id: ...] in workshop list above (e.g., "workshop_3"). TECHNICAL identifier - NEVER mention in `response` dialogue! Use TYPE+LOCATION in dialogue. **Required when selling.**
- `workshop_price`: (int) FINAL AGREED PRICE in denars (required when 'sell'). Can be higher than base (greedy/distrust) or lower (generous/like them). Amount player will pay. **Required when selling.**
- `technical_action`: (string) "ACTION_NAME" to start, "ACTION_NAME:STOP" to stop. **Omit if no action change.**
 """,

 "Item Exchange": """ 
    - **`item_transfers`**: (array/null) `[{"item_id": "...", "amount": N, "action": "give"|"take"}]`
  - 'give'=you→player, 'take'=player→you. Use exact item IDs from inventories above.
  - **Trading items:** Use BOTH actions: give item + take payment, OR take item + give payment, OR barter (give item + take item).
  - **Buy from player:** `money_transfer:{"action":"give", "amount":N}` + `item_transfers:[{"action":"take", item_id:"X"}]`
  - **Sell to player:** `money_transfer:{"action":"receive", "amount":N}` + `item_transfers:[{"action":"give", item_id:"X"}]`
  - **Barter (item for item):** `item_transfers:[{"action":"give", item_id:"X"}, {"action":"take", item_id:"Y"}]` (no money_transfer)
  - **Restrictions:** You can ONLY give items from YOUR inventory. You CANNOT trade settlements/castles/towns through item_transfers.

  - **Important:** Do not perform an action if it is not confirmed by the player in dialogue. For ALL actions.

    **IMPORTANT RULES FOR RECRUITMENT ACTIONS:**
    - **As a ruler - Hiring/Accepting:** Use `hire_mercenary` ONLY when the player explicitly agrees in dialogue.
    - **As a ruler - Dismissing:** Use `dismiss_mercenary` or `dismiss_vassal` ONLY when:
    - The player explicitly requests to leave your service, OR
    - You have a serious, justified reason to dismiss/expel them (betrayal, dishonor, treason, grave offense)
    - You can PROPOSE or DISCUSS these options in your response text, but activate the action ONLY upon agreement or serious cause.
    - If uncertain about player's intent or your own decision, keep `kingdom_action` as 'none'.
    - These are formal state actions with permanent consequences - use them responsibly and only when truly warranted.
 """
}

SECTIONS_TO_SUMMARIZE: dict[str, dict] = {
    # "Conversation History": {
    #     "max_chars": 800,
    # },
    # "Global Politics": {
    #     "max_chars": 800,
    #     "extra_instructions": (
    #         "Preserve all faction names, Id, rulers, alliances, and war statuses. "
    #         "Drop minor trade route details."
    #     ),
    #     # "bullet_patterns": [r"war|alliance|faction"],
    # },
    # "Immediate Situation": {
    #     "max_chars": 800,
    #     "extra_instructions": (
    #         "Records about killing bandits do not need to be kept in detail. "
    #         "Summarize all such minor bandit/skirmish events into single one-liner. "
    #         "Focus on politically or diplomatically significant events. "
    #         "Preserve key diplomatic statements, drop routine ones."
    #     ),
    #   #  "bullet_patterns": [r"\*\*Recent Events:\*\*", r"\*\*Diplomatic Statements"],
    # },
}

# Groups some # headers as ## children of a group leader.
# Keys and member values use normalized substring matching (case-insensitive).
# Members are nested under their leader in the listed order.
HEADER_GROUPS: dict[str, list[str]] = {
    "action system": [
        "general rules",
        "action format",
        "critical requirements",
        "available actions",
        "follow_player",
        "go_to_settlement",
        "return_to_player",
        "attack_party",
        "siege_settlement",
        "patrol_settlement",
        "wait_near_settlement",
        "raid_village",
        "create_rp_item",
        "transfer_troops_and_prisoners",
        "rules for all actions",
        "rules for multi-step actions",
    ],
}

# Ordering matters a lot guyz, If you want proper Cache hits, keep this in mind.
SECTION_ORDER_PRIORITY: dict[str, int] = {
    # ── Static instructions — same every turn (10–90) ─────────────────────
    "Mission":                                              1,
    "Core Rules":                                          2,
    "JSON Output Format":                                  3,
    "Communication":                                       10,
    "Information Sharing":                                 20,
    
    # ─ Semi-dynamic — content same but may intent-filtered (100–140) ───────────────────
    "Conflict":                                            100,
    "Action System":                                       101,
    "Kingdom Actions":                                     102,
    "Item Exchange":                                       103,
    "Quests":                                              104,
    "Task Logic":                                          105,
    "Romance":                                             106,
    "The World":                                           107,
    # ORDER_DEFAULT_PRIORITY = 150  ← unknown / missed headers land here
    # ── Dynamic — changes every turn (160–990) ────────────────────────────
    "Global Politics of the World":                       160,
    "Character Briefing (CURRENT DATA)":                  170,
    "Immediate Situation":                                180,
    "The Player (CURRENT DATA)":                          190,
    "Nearby Settlements (Strategic Context, CURRENT DATA)": 200,
    "Nearby Parties (NPC Vicinity, CURRENT DATA)":        210,
    "Conversation History":                               220,
    "Your Previous Response (For Continuity)":            230,
    "Your Response":                                      990,
}

ORDER_DEFAULT_PRIORITY: int = 150

BULLETS_TO_KEEP: dict[str, list[str]] = {
    # "### The Player (CURRENT DATA)": [
    #     r"their forces",
    #     r"name|clan",
    # ],
}

BULLETS_TO_REMOVE: dict[str, list[str]] = {
    # "### The Player (CURRENT DATA)": [
    #     r"their appearance",
    # ],
}

# Sections with Same contents are to be put here.
# But Intent filtering will still apply to them.
# SECTION_ORDER_PRIORITY applies  to them as well, so structure them properly.
PINNED_STATIC_SECTIONS: list[str] = [
    "Mission",
    "Core Rules",
    "JSON Output Format",
    "Communication",
    "Information Sharing",

    # Below are semi-static sections which gets intent-filtered. 
    # But Content remains same.
    "Conflict",
    "Action System",
    "Kingdom Actions",
    "Item Exchange",
    "Quests",
    "Task Logic",
    "The World",
    "Romance",
]

# Sections whose content change with time.
DYNAMIC_SECTIONS: list[str] = [
    "Character Briefing",       # (CURRENT DATA)
    "Immediate Situation",
    "The Player",               # (CURRENT DATA)
    "Nearby Settlements",       # (CURRENT DATA)
    "Nearby Parties",           # (CURRENT DATA)
    "Conversation History",
    "Your Previous Response",   # (For Continuity)
    "Global Politics",
    "Romance",
    "Your Response",
]

# --- INTENT-BASED FILTERING ---
# Set to True to enable intent classification for this mission type.
ENABLE_INTENT_SYSTEM: bool = True
