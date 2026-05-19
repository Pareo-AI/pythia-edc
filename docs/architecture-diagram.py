# Render the architecture diagram on a fixed character grid so every box closes
# and connectors land on real coordinates.

W, H = 152, 35
canvas = [[" "] * W for _ in range(H)]

def put(x, y, s):
    if y < 0 or y >= H:
        return
    for i, ch in enumerate(s):
        cx = x + i
        if 0 <= cx < W:
            canvas[y][cx] = ch

def box(x, y, w, h, title="", lines=None):
    lines = lines or []
    # top border with optional title
    if title:
        head = f"┌─ {title} "
        top = head + "─" * max(0, w - len(head) - 1) + "┐"
    else:
        top = "┌" + "─" * (w - 2) + "┐"
    put(x, y, top[:w])
    bottom = "└" + "─" * (w - 2) + "┘"
    put(x, y + h - 1, bottom)
    for r in range(1, h - 1):
        put(x, y + r, "│")
        put(x + w - 1, y + r, "│")
    for i, ln in enumerate(lines):
        if 1 + i < h - 1:
            put(x + 2, y + 1 + i, ln[: w - 4])

# ---- Title -------------------------------------------------------------------
put(6, 0, 'PYTHIA "ASK YOUR DATASPACE" — GAIA-X TRUST-GATED DATASPACE CLIENT')

# ---- CLIENT (pipeline) -------------------------------------------------------
box(0, 2, 152, 4,
    title='PYTHIA CLIENT — ask a natural-language question across providers (trust-verified)',
    lines=[
        "① CATALOG fan-out ─► ② RANK (semantic relevance) ─► [ GAIA-X TRUST GATES ] ─► ③ NEGOTIATE ─► ④ TRANSFER+EDR ─► ⑤ FETCH",
        "trust gates run per candidate in rank order · verdicts cached per provider",
    ])

# client -> gates
put(66, 6, "│")
put(66, 7, "▼")

# ---- GATES -------------------------------------------------------------------
box(0, 8, 100, 8,
    title="GAIA-X TRUST GATES — per candidate · BEFORE negotiation",
    lines=[
        "ⓐ VERIFY PROVIDER VC",
        "   structure → schema (SHACL) → trusted issuer → validity → cryptographic signature",
        "   (signing key bound to the issuer's identity)",
        "ⓑ VERIFY ODRL OFFER",
        "   policy shape valid · usage rules well-formed (machine-readable ODRL)",
        "✗ fail → SKIP candidate (with reason)                     ✓ pass → ③ NEGOTIATE",
    ])

# ---- TRUST ANCHORS (feeds gates) --------------------------------------------
box(108, 8, 44, 8,
    title="GAIA-X TRUST ANCHORS",
    lines=[
        "Credential source",
        "  resolves a provider's signed VC",
        "  → roadmap: GXDCH / Notary / did:web",
        "Trust list = accepted issuer DIDs",
        "  did:web:registry.gaia-x.eu",
        "  did:key:z6Mk…",
    ])
# anchors -> gates arrow (resolve VC + accepted issuer DIDs)
put(100, 11, "◄──────")
put(108, 16, "feeds VC + accepted issuer DIDs → gate ⓐ")

# gates -> consumer (mgmt api)
put(4, 16, "│")
put(10, 16, "①③④ Management API (TLS/mTLS optional)")
put(4, 17, "▼")

# ---- Section headers ---------------------------------------------------------
put(0, 18, "DATASPACE PROTOCOL — DSP 2025-1 (catalog / negotiate / transfer)")
put(86, 18, "DATA-PLANE PULL — EDR ⑤ (token-authorized fetch)")

# ---- CONSUMER ----------------------------------------------------------------
box(0, 19, 30, 6,
    title="CONSUMER Connector",
    lines=["EDC", "Mgmt :29193", "DSP  :29194"])

# ---- PROVIDER ----------------------------------------------------------------
box(44, 19, 30, 7,
    title="PROVIDER Connector",
    lines=["EDC", "Mgmt :19193", "DSP  :19194", "data-plane :19191"])

# consumer <-> provider (DSP control-plane)
put(30, 20, ":29194↔:19194")
put(30, 22, "◄═══════════►")

# provider -> data-plane (EDR pull proxied)
put(74, 21, "EDR pull")
put(74, 22, "──proxied──►")

# ---- DATA-PLANE --------------------------------------------------------------
box(88, 19, 28, 4,
    title="Provider data-plane",
    lines=[":19191/public/..."])

# data-plane -> mock (HTTP plain)
put(116, 20, "HTTP")
put(116, 21, "─plain►")

# ---- BACKEND DATA SOURCE -----------------------------------------------------
box(123, 19, 29, 5,
    title="Backend Data Source",
    lines=["dataset endpoints", "(e.g. CO₂ datasets)"])

# ---- PROVISIONING ------------------------------------------------------------
box(44, 27, 54, 4,
    title="Provider provisioning",
    lines=[
        "registers assets · policies · contracts",
        "asset data URL → backend source",
    ])
# seeder -> provider
put(50, 26, "▲")

# provider publishes VC note (feeds anchors)
put(104, 27, "Provider publishes signed VC + ODRL offers")
put(104, 28, "→ consumed by TRUST ANCHORS ▲")

# ---- Divider + standards -----------------------------------------------------
put(0, 32, "─" * 152)
put(0, 33, "STANDARDS  W3C Verifiable Credentials · DIDs · Gaia-X trust framework · "
           "ODRL 2.0 · SHACL · Dataspace Protocol (DSP 2025-1) · DCAT")

out = "\n".join("".join(row).rstrip() for row in canvas)
print(out)
