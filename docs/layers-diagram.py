W, H = 152, 33
canvas = [[" "] * W for _ in range(H)]

def put(x, y, s):
    if y < 0 or y >= H: return
    for i, ch in enumerate(s):
        cx = x + i
        if 0 <= cx < W: canvas[y][cx] = ch

def box(x, y, w, h, title="", lines=None):
    lines = lines or []
    if title:
        head = f"┌─ {title} "
        top = head + "─" * max(0, w - len(head) - 1) + "┐"
    else:
        top = "┌" + "─" * (w - 2) + "┐"
    put(x, y, top[:w])
    put(x, y + h - 1, "└" + "─" * (w - 2) + "┘")
    for r in range(1, h - 1):
        put(x, y + r, "│"); put(x + w - 1, y + r, "│")
    for i, ln in enumerate(lines):
        if 1 + i < h - 1: put(x + 2, y + 1 + i, ln[: w - 4])

# ---- Title -------------------------------------------------------------------
put(6, 0, "PYTHIA — UNDER THE HOOD")
put(6, 1, "Not a wrapper. Three real layers — with a trust layer across all of them.")

# ---- Inputs ------------------------------------------------------------------
put(0, 5,  "AI agent ──────►")
put(0, 12, "your sentence ─►")

CX = 60  # vertical call-chain column

# ---- LAYER 3 — MCP -----------------------------------------------------------
box(16, 4, 88, 4,
    title="LAYER 3 — MCP SERVER",
    lines=[
        "hands the whole thing to an AI agent — tools exposed over MCP",
        "→ Claude or any MCP-capable agent",
    ])
put(CX, 8, "▼")

# ---- LAYER 2 — NL ------------------------------------------------------------
box(16, 9, 88, 8,
    title="LAYER 2 — NATURAL-LANGUAGE INTERFACE",
    lines=[
        "turns a sentence into the right asset (semantic ranking)",
        "IBM Granite embeddings — granite-embedding-97m-multilingual-r2 (Apache-2.0, ~130 MB)",
        "offline · multilingual · no API key · no query leaves the machine",
        "",
        "relevance: exact query 0.98 · reworded query 0.93",
    ])
put(CX, 17, "▼")

# ---- LAYER 1 — EDC CORE ------------------------------------------------------
box(16, 18, 88, 7,
    title="LAYER 1 — EDC CORE",
    lines=[
        "Python client for the Dataspace Protocol (DSP 2025-1)",
        "full negotiation state machine: REQUESTED → AGREED → VERIFIED → FINALIZED",
        "DCAT catalogs · ODRL policies (correct JSON-LD contexts) · EDR token flow",
        "typed errors — a real message, never a silent 400",
        "no message broker · no callback server — just a library",
    ])

# ---- TRUST LAYER (spans all three) ------------------------------------------
box(107, 4, 45, 21,
    title="TRUST LAYER (cryptographic · offline)",
    lines=[
        "across all three layers",
        "",
        "ⓐ PROVIDER VC",
        "   Ed25519 signature",
        "   expiry window",
        "   issuer ↔ key binding",
        "   issuer on trust-list",
        "",
        "ⓑ ODRL OFFER",
        "   validated against",
        "   a SHACL shape",
        "",
        "either check fails →",
        "Pythia REFUSES to negotiate",
    ])
# ties from each layer into the trust band
for ry in (5, 12, 21):
    put(104, ry, "──►")

# ---- Output to dataspace -----------------------------------------------------
put(CX, 25, "▼")
box(16, 26, 88, 3, lines=["speaks to ANY EDC connector · Dataspace Protocol (DSP 2025-1)"])

# ---- Footer tagline ----------------------------------------------------------
put(0, 30, "─" * 152)
put(0, 31, "natural language  +  agent access  +  cryptographic credential verification "
           "—  one  `pip install`,  against any EDC connector")

print("\n".join("".join(r).rstrip() for r in canvas))
