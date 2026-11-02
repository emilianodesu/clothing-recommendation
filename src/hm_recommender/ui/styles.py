"""Luxury editorial fashion stylesheet with full Light & Dark mode support."""
import streamlit as st

CUSTOM_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Bodoni+Moda:ital,opsz,wght@0,6..96,400;0,6..96,500;0,6..96,600;0,6..96,700;1,6..96,400&family=Plus+Jakarta+Sans:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap');

:root, body, .stApp {
    color-scheme: light dark;
    --atelier-canvas: light-dark(#FAF9F7, #0E0E0D);
    --atelier-surface: light-dark(#FFFFFF, #171716);
    --atelier-surface-elevated: light-dark(#FFFFFF, #20201E);
    --atelier-noir: light-dark(#121212, #F2F0EC);
    --atelier-charcoal: light-dark(#2A2A28, #D6D3CD);
    --atelier-muted: light-dark(#6E6E68, #9A968E);
    --atelier-light-muted: light-dark(#9E9E98, #706D66);
    --atelier-border: light-dark(#E6E6E1, #2A2A27);
    --atelier-border-hover: light-dark(#B8B8B0, #4A4A44);
    --atelier-border-subtle: light-dark(#F0F0EB, #222220);
    --atelier-plate: light-dark(#F4F4F1, #F2F1ED);
    --atelier-primary-bg: light-dark(#121212, #F2F0EC);
    --atelier-primary-fg: light-dark(#FFFFFF, #121212);
    --atelier-primary-hover: light-dark(#2E2E2C, #DCD9D2);
    --atelier-sage-bg: light-dark(#EFF4ED, #192418);
    --atelier-sage-text: light-dark(#283D20, #A8C99F);
    --atelier-sage-border: light-dark(#D2DFCC, #2B3E29);
    --atelier-warm-bg: light-dark(#F6F3F0, #24201C);
    --atelier-warm-text: light-dark(#4A3E35, #D4C5B9);
    --atelier-warm-border: light-dark(#E3DAD2, #38312B);
    --atelier-ice-bg: light-dark(#EFF4F6, #172226);
    --atelier-ice-text: light-dark(#203B46, #9EC5D4);
    --atelier-ice-border: light-dark(#CFE0E6, #273A42);
}

@supports not (color: light-dark(#000, #fff)) {
    :root, body, .stApp {
        --atelier-canvas: #FAF9F7;
        --atelier-surface: #FFFFFF;
        --atelier-surface-elevated: #FFFFFF;
        --atelier-noir: #121212;
        --atelier-charcoal: #2A2A28;
        --atelier-muted: #6E6E68;
        --atelier-light-muted: #9E9E98;
        --atelier-border: #E6E6E1;
        --atelier-border-hover: #B8B8B0;
        --atelier-border-subtle: #F0F0EB;
        --atelier-plate: #F4F4F1;
        --atelier-primary-bg: #121212;
        --atelier-primary-fg: #FFFFFF;
        --atelier-primary-hover: #2E2E2C;
        --atelier-sage-bg: #EFF4ED;
        --atelier-sage-text: #283D20;
        --atelier-sage-border: #D2DFCC;
        --atelier-warm-bg: #F6F3F0;
        --atelier-warm-text: #4A3E35;
        --atelier-warm-border: #E3DAD2;
        --atelier-ice-bg: #EFF4F6;
        --atelier-ice-text: #203B46;
        --atelier-ice-border: #CFE0E6;
    }
    @media (prefers-color-scheme: dark) {
        :root, body, .stApp {
            --atelier-canvas: #0E0E0D;
            --atelier-surface: #171716;
            --atelier-surface-elevated: #20201E;
            --atelier-noir: #F2F0EC;
            --atelier-charcoal: #D6D3CD;
            --atelier-muted: #9A968E;
            --atelier-light-muted: #706D66;
            --atelier-border: #2A2A27;
            --atelier-border-hover: #4A4A44;
            --atelier-border-subtle: #222220;
            --atelier-plate: #F2F1ED;
            --atelier-primary-bg: #F2F0EC;
            --atelier-primary-fg: #121212;
            --atelier-primary-hover: #DCD9D2;
            --atelier-sage-bg: #192418;
            --atelier-sage-text: #A8C99F;
            --atelier-sage-border: #2B3E29;
            --atelier-warm-bg: #24201C;
            --atelier-warm-text: #D4C5B9;
            --atelier-warm-border: #38312B;
            --atelier-ice-bg: #172226;
            --atelier-ice-text: #9EC5D4;
            --atelier-ice-border: #273A42;
        }
    }
}

/* Base Canvas & Global Typography */
.stApp, [data-testid="stAppViewContainer"], [data-testid="stMain"] {
    background-color: var(--atelier-canvas) !important;
    background-image: none !important;
    color: var(--atelier-noir) !important;
    font-family: 'Plus Jakarta Sans', -apple-system, BlinkMacSystemFont, sans-serif !important;
    -webkit-font-smoothing: antialiased;
}

header[data-testid="stHeader"] {
    background: transparent !important;
}

[data-testid="stSidebar"] {
    background-color: var(--atelier-surface) !important;
    border-right: 1px solid var(--atelier-border) !important;
}

.block-container {
    max-width: 1320px;
    padding-top: 2rem;
    padding-bottom: 4rem;
    position: relative;
    z-index: 1;
}

/* Ensure all Markdown & Body text respects theme contrast */
[data-testid="stMarkdownContainer"],
[data-testid="stMarkdownContainer"] p,
[data-testid="stMarkdownContainer"] strong,
[data-testid="stMarkdownContainer"] li,
[data-testid="stMarkdownContainer"] span:not(.atelier-badge):not(.mcm-badge):not(.cyber-badge) {
    color: var(--atelier-noir) !important;
}

/* Minimal Header */
.minimal-header {
    display: flex !important;
    justify-content: space-between !important;
    align-items: center !important;
    gap: 16px !important;
    flex-wrap: wrap !important;
    background: transparent !important;
    padding: 0 0 20px 0 !important;
    margin-bottom: 24px !important;
    border-bottom: 1px solid var(--atelier-border) !important;
}

.minimal-title {
    font-family: 'Bodoni Moda', 'Playfair Display', Georgia, serif !important;
    font-size: 34px !important;
    font-weight: 500 !important;
    letter-spacing: -0.01em !important;
    color: var(--atelier-noir) !important;
    margin: 0 0 6px 0 !important;
    line-height: 1.2 !important;
}

.minimal-subtitle {
    font-size: 14.5px !important;
    color: var(--atelier-muted) !important;
    margin: 0 !important;
    line-height: 1.5 !important;
    font-weight: 400 !important;
}

.logout-btn, a.logout-btn:link, a.logout-btn:visited {
    display: inline-flex !important;
    align-items: center !important;
    background: var(--atelier-surface) !important;
    color: var(--atelier-noir) !important;
    border: 1px solid var(--atelier-border) !important;
    border-radius: 3px !important;
    font-family: 'Plus Jakarta Sans', sans-serif !important;
    font-weight: 600 !important;
    font-size: 10.5px !important;
    letter-spacing: 0.14em !important;
    text-transform: uppercase !important;
    text-decoration: none !important;
    padding: 9px 16px !important;
    transition: all 0.2s ease !important;
    white-space: nowrap !important;
}

.logout-btn:hover, a.logout-btn:hover {
    background: var(--atelier-primary-bg) !important;
    color: var(--atelier-primary-fg) !important;
    border-color: var(--atelier-primary-bg) !important;
    text-decoration: none !important;
}

/* Fallback / legacy banners */
.atelier-masthead, .mcm-banner, .hud-banner, .mcm-telemetry, .hud-telemetry {
    display: none !important;
}

/* Product Exhibition Cards */
[data-testid="stVerticalBlockBorderWrapper"] {
    position: relative !important;
    background: var(--atelier-surface) !important;
    border: 1px solid var(--atelier-border) !important;
    border-radius: 3px !important;
    padding: 18px 18px 22px 18px !important;
    box-shadow: 0 2px 10px rgba(0, 0, 0, 0.04) !important;
    transition: all 0.28s cubic-bezier(0.16, 1, 0.3, 1) !important;
}

[data-testid="stVerticalBlockBorderWrapper"]:hover {
    transform: translateY(-3px) !important;
    border-color: var(--atelier-border-hover) !important;
    box-shadow: 0 12px 28px rgba(0, 0, 0, 0.12) !important;
}

/* Gallery Photo Plate */
[data-testid="stImage"] img {
    height: 255px !important;
    object-fit: contain !important;
    background: var(--atelier-plate) !important;
    border: 1px solid var(--atelier-border-subtle) !important;
    border-radius: 2px !important;
    padding: 10px !important;
    transition: transform 0.35s cubic-bezier(0.16, 1, 0.3, 1) !important;
}

[data-testid="stVerticalBlockBorderWrapper"]:hover [data-testid="stImage"] img {
    transform: scale(1.02) !important;
}

/* Minimalist Editorial Swing-Tag Badges */
.mcm-badge, .cyber-badge, .atelier-badge {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    font-family: 'Plus Jakarta Sans', sans-serif;
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    padding: 3px 8px;
    border-radius: 2px;
    margin: 5px 0 2px 0;
}

.mcm-badge-bought, .cyber-badge-bought, .atelier-badge-recent {
    background: var(--atelier-sage-bg) !important;
    color: var(--atelier-sage-text) !important;
    border: 1px solid var(--atelier-sage-border) !important;
}

.mcm-badge-popular, .cyber-badge-popular, .atelier-badge-popular {
    background: var(--atelier-warm-bg) !important;
    color: var(--atelier-warm-text) !important;
    border: 1px solid var(--atelier-warm-border) !important;
}

.mcm-badge-new, .cyber-badge-new, .atelier-badge-new {
    background: var(--atelier-surface) !important;
    color: var(--atelier-noir) !important;
    border: 1px solid var(--atelier-noir) !important;
}

.mcm-badge-rank, .cyber-badge-rank, .atelier-badge-rank {
    background: var(--atelier-ice-bg) !important;
    color: var(--atelier-ice-text) !important;
    border: 1px solid var(--atelier-ice-border) !important;
}

/* Editorial Buttons */
[data-testid="stButton"] button[kind="primary"] {
    background: var(--atelier-primary-bg) !important;
    color: var(--atelier-primary-fg) !important;
    border: 1px solid var(--atelier-primary-bg) !important;
    border-radius: 3px !important;
    font-family: 'Plus Jakarta Sans', sans-serif !important;
    font-weight: 600 !important;
    font-size: 11.5px !important;
    letter-spacing: 0.18em !important;
    text-transform: uppercase !important;
    padding: 13px 24px !important;
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.12) !important;
    transition: all 0.22s ease !important;
}

[data-testid="stButton"] button[kind="primary"] *,
[data-testid="stButton"] button[kind="primary"] p,
[data-testid="stButton"] button[kind="primary"] span {
    color: var(--atelier-primary-fg) !important;
}

[data-testid="stButton"] button[kind="primary"]:hover {
    background: var(--atelier-primary-hover) !important;
    border-color: var(--atelier-primary-hover) !important;
    transform: translateY(-1px) !important;
}

[data-testid="stButton"] button:not([kind="primary"]) {
    background: var(--atelier-surface) !important;
    color: var(--atelier-noir) !important;
    border: 1px solid var(--atelier-border) !important;
    border-radius: 3px !important;
    font-family: 'Plus Jakarta Sans', sans-serif !important;
    font-weight: 600 !important;
    font-size: 10.5px !important;
    letter-spacing: 0.14em !important;
    text-transform: uppercase !important;
    padding: 10px 18px !important;
    transition: all 0.22s ease !important;
}

[data-testid="stButton"] button:not([kind="primary"]) *,
[data-testid="stButton"] button:not([kind="primary"]) p,
[data-testid="stButton"] button:not([kind="primary"]) span {
    color: var(--atelier-noir) !important;
}

[data-testid="stButton"] button:not([kind="primary"]):hover {
    background: var(--atelier-primary-bg) !important;
    color: var(--atelier-primary-fg) !important;
    border-color: var(--atelier-primary-bg) !important;
    transform: translateY(-1px) !important;
}

[data-testid="stButton"] button:not([kind="primary"]):hover *,
[data-testid="stButton"] button:not([kind="primary"]):hover p,
[data-testid="stButton"] button:not([kind="primary"]):hover span {
    color: var(--atelier-primary-fg) !important;
}

/* Minimalist Editorial Tabs */
[data-testid="stTabs"] {
    border-bottom: 1px solid var(--atelier-border) !important;
    margin-bottom: 28px !important;
}

[data-testid="stTab"] {
    font-family: 'Bodoni Moda', 'Playfair Display', Georgia, serif !important;
    font-weight: 500 !important;
    font-size: 18px !important;
    letter-spacing: 0.02em !important;
    color: var(--atelier-muted) !important;
    padding: 14px 28px !important;
    border-bottom: 2px solid transparent !important;
    transition: all 0.2s ease !important;
}

[data-testid="stTab"] *, [data-testid="stTab"] p {
    color: inherit !important;
    font-family: inherit !important;
    font-size: inherit !important;
}

[data-testid="stTab"][aria-selected="true"] {
    color: var(--atelier-noir) !important;
    border-bottom: 2px solid var(--atelier-noir) !important;
}

/* Clean Form Controls (Selectbox, Dropdown Portals, NumberInput, Popover) */
[data-testid="stWidgetLabel"] label,
[data-testid="stWidgetLabel"] p,
label {
    font-family: 'Plus Jakarta Sans', sans-serif !important;
    font-weight: 600 !important;
    letter-spacing: 0.14em !important;
    text-transform: uppercase !important;
    color: var(--atelier-charcoal) !important;
    font-size: 11px !important;
}

[data-testid="stSelectbox"] > div > div,
[data-baseweb="select"] > div {
    background-color: var(--atelier-surface) !important;
    border: 1px solid var(--atelier-border) !important;
    border-radius: 3px !important;
    color: var(--atelier-noir) !important;
    font-family: 'Plus Jakarta Sans', sans-serif !important;
    font-size: 14px !important;
    box-shadow: none !important;
}

[data-testid="stSelectbox"] [data-baseweb="select"] *,
[data-testid="stSelectbox"] svg {
    color: var(--atelier-noir) !important;
    fill: var(--atelier-noir) !important;
}

[data-testid="stSelectbox"] > div > div:focus-within {
    border-color: var(--atelier-noir) !important;
    box-shadow: 0 0 0 1px var(--atelier-noir) !important;
}

/* Dropdown Menus & Popover Portals */
[data-baseweb="popover"],
[data-baseweb="menu"],
ul[role="listbox"],
[data-testid="stSelectboxVirtualDropdown"] {
    background-color: var(--atelier-surface-elevated) !important;
    border: 1px solid var(--atelier-border) !important;
    border-radius: 3px !important;
    color: var(--atelier-noir) !important;
}

li[role="option"],
[data-baseweb="menu"] li {
    background-color: transparent !important;
    color: var(--atelier-noir) !important;
    font-family: 'Plus Jakarta Sans', sans-serif !important;
    font-size: 13.5px !important;
}

li[role="option"]:hover,
li[role="option"][aria-selected="true"] {
    background-color: var(--atelier-border-subtle) !important;
    color: var(--atelier-noir) !important;
}

/* Number Input & Stepper Buttons */
[data-testid="stNumberInput"] > div > div,
[data-testid="stNumberInputContainer"],
[data-testid="stNumberInput"] [data-baseweb="base-input"],
[data-testid="stNumberInput"] input {
    background-color: var(--atelier-surface) !important;
    color: var(--atelier-noir) !important;
    font-family: 'Plus Jakarta Sans', sans-serif !important;
}

[data-testid="stNumberInputContainer"] {
    border: 1px solid var(--atelier-border) !important;
    border-radius: 3px !important;
}

[data-testid="stNumberInputStepDown"],
[data-testid="stNumberInputStepUp"] {
    background-color: var(--atelier-surface) !important;
    color: var(--atelier-noir) !important;
    border: none !important;
}

[data-testid="stNumberInputStepDown"]:hover,
[data-testid="stNumberInputStepUp"]:hover {
    background-color: var(--atelier-border-subtle) !important;
}

[data-testid="stNumberInputStepDown"] svg,
[data-testid="stNumberInputStepUp"] svg {
    fill: var(--atelier-noir) !important;
}

/* Popover Trigger & Body */
[data-testid="stPopover"] > div > button {
    background-color: var(--atelier-surface) !important;
    border: 1px solid var(--atelier-border) !important;
    border-radius: 3px !important;
    color: var(--atelier-noir) !important;
    font-family: 'Plus Jakarta Sans', sans-serif !important;
    font-weight: 600 !important;
    letter-spacing: 0.12em !important;
    text-transform: uppercase !important;
    font-size: 11px !important;
}

[data-testid="stPopover"] > div > button *,
[data-testid="stPopover"] > div > button svg {
    color: var(--atelier-noir) !important;
    fill: var(--atelier-noir) !important;
}

[data-testid="stPopover"] > div > button:hover {
    border-color: var(--atelier-border-hover) !important;
}

[data-testid="stPopoverBody"] {
    background-color: var(--atelier-surface-elevated) !important;
    border: 1px solid var(--atelier-border) !important;
    border-radius: 3px !important;
    color: var(--atelier-noir) !important;
}

/* Alerts & Expanders */
[data-testid="stAlert"] {
    background-color: var(--atelier-surface) !important;
    border: 1px solid var(--atelier-border) !important;
    border-left: 3px solid var(--atelier-noir) !important;
    border-radius: 3px !important;
    color: var(--atelier-noir) !important;
}

[data-testid="stExpander"],
[data-testid="stExpander"] details,
[data-testid="stExpander"] summary,
[data-testid="stExpanderDetails"] {
    background-color: var(--atelier-surface) !important;
    color: var(--atelier-noir) !important;
    border-color: var(--atelier-border) !important;
    border-radius: 3px !important;
}

[data-testid="stExpander"] summary *,
[data-testid="stExpander"] summary svg {
    color: var(--atelier-noir) !important;
    fill: var(--atelier-noir) !important;
}

h1, h2, h3, h4 {
    font-family: 'Bodoni Moda', 'Playfair Display', Georgia, serif !important;
    color: var(--atelier-noir) !important;
    font-weight: 400 !important;
    letter-spacing: -0.01em !important;
}

h2 {
    font-size: 24px !important;
}

h3 {
    font-size: 18px !important;
}

[data-testid="stCaptionContainer"],
[data-testid="stCaptionContainer"] *,
p.caption {
    color: var(--atelier-muted) !important;
    font-size: 13px !important;
    line-height: 1.55 !important;
}

@media (max-width: 768px) {
    .minimal-title {
        font-size: 26px !important;
    }
    [data-testid="stImage"] img {
        height: 220px !important;
    }
}
</style>
"""


def inject_custom_css():
    """Inject responsive luxury fashion styling into the current Streamlit page."""
    st.html(CUSTOM_CSS)
