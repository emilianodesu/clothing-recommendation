"""Luxury editorial fashion stylesheet for H&M Studio recommender UI."""
import streamlit as st

CUSTOM_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Bodoni+Moda:ital,opsz,wght@0,6..96,400;0,6..96,500;0,6..96,600;0,6..96,700;1,6..96,400&family=Plus+Jakarta+Sans:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap');

:root {
    --atelier-canvas: #FAF9F7;
    --atelier-surface: #FFFFFF;
    --atelier-noir: #121212;
    --atelier-charcoal: #2A2A28;
    --atelier-muted: #73736E;
    --atelier-light-muted: #9E9E98;
    --atelier-border: #E8E8E3;
    --atelier-border-subtle: #F0F0EB;
    --atelier-plate: #F4F4F1;
    --atelier-accent: #121212;
    --atelier-sage-bg: #F0F4EE;
    --atelier-sage-text: #2C4223;
    --atelier-sage-border: #D4E0CE;
    --atelier-warm-bg: #F8F5F2;
    --atelier-warm-text: #4A3E36;
    --atelier-warm-border: #E5DCD4;
    --atelier-ice-bg: #F1F5F7;
    --atelier-ice-text: #233D48;
    --atelier-ice-border: #D2E0E6;
}

/* Base Canvas & Global Reset */
.stApp {
    background-color: var(--atelier-canvas) !important;
    background-image: 
        radial-gradient(at 0% 0%, rgba(240, 238, 232, 0.6) 0px, transparent 50%),
        radial-gradient(at 100% 100%, rgba(242, 239, 233, 0.4) 0px, transparent 50%) !important;
    color: var(--atelier-noir) !important;
    font-family: 'Plus Jakarta Sans', -apple-system, BlinkMacSystemFont, sans-serif !important;
    -webkit-font-smoothing: antialiased;
}

header[data-testid="stHeader"] {
    background: transparent !important;
}

.block-container {
    max-width: 1320px;
    padding-top: 2rem;
    padding-bottom: 4rem;
    position: relative;
    z-index: 1;
}

/* Minimal Header */
.minimal-header {
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

/* Fallback / legacy banners */
.atelier-masthead, .mcm-banner, .hud-banner {
    display: none !important;
}

/* Legacy banner compatibility */
.mcm-telemetry, .hud-telemetry {
    display: none;
}

/* Product Exhibition Cards */
[data-testid="stVerticalBlockBorderWrapper"] {
    position: relative !important;
    background: var(--atelier-surface) !important;
    border: 1px solid var(--atelier-border) !important;
    border-radius: 3px !important;
    padding: 18px 18px 22px 18px !important;
    box-shadow: 0 2px 10px rgba(18, 18, 18, 0.02) !important;
    transition: all 0.28s cubic-bezier(0.16, 1, 0.3, 1) !important;
}

[data-testid="stVerticalBlockBorderWrapper"]:hover {
    transform: translateY(-3px) !important;
    border-color: #C0C0B8 !important;
    box-shadow: 0 12px 28px rgba(18, 18, 18, 0.06) !important;
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
    background: var(--atelier-sage-bg);
    color: var(--atelier-sage-text);
    border: 1px solid var(--atelier-sage-border);
}

.mcm-badge-popular, .cyber-badge-popular, .atelier-badge-popular {
    background: var(--atelier-warm-bg);
    color: var(--atelier-warm-text);
    border: 1px solid var(--atelier-warm-border);
}

.mcm-badge-new, .cyber-badge-new, .atelier-badge-new {
    background: #FFFFFF;
    color: var(--atelier-noir);
    border: 1px solid var(--atelier-noir);
}

.mcm-badge-rank, .cyber-badge-rank, .atelier-badge-rank {
    background: var(--atelier-ice-bg);
    color: var(--atelier-ice-text);
    border: 1px solid var(--atelier-ice-border);
}

/* Editorial Buttons */
[data-testid="stButton"] button[kind="primary"] {
    background: var(--atelier-noir) !important;
    color: #FFFFFF !important;
    border: 1px solid var(--atelier-noir) !important;
    border-radius: 3px !important;
    font-family: 'Plus Jakarta Sans', sans-serif !important;
    font-weight: 600 !important;
    font-size: 11.5px !important;
    letter-spacing: 0.18em !important;
    text-transform: uppercase !important;
    padding: 13px 24px !important;
    box-shadow: 0 2px 8px rgba(18, 18, 18, 0.12) !important;
    transition: all 0.22s ease !important;
}

[data-testid="stButton"] button[kind="primary"]:hover {
    background: #333333 !important;
    border-color: #333333 !important;
    box-shadow: 0 4px 14px rgba(18, 18, 18, 0.2) !important;
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

[data-testid="stButton"] button:not([kind="primary"]):hover {
    background: var(--atelier-noir) !important;
    color: #FFFFFF !important;
    border-color: var(--atelier-noir) !important;
    transform: translateY(-1px) !important;
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

[data-testid="stTab"][aria-selected="true"] {
    color: var(--atelier-noir) !important;
    border-bottom: 2px solid var(--atelier-noir) !important;
}

/* Clean Form Controls */
[data-testid="stWidgetLabel"] label, label {
    font-family: 'Plus Jakarta Sans', sans-serif !important;
    font-weight: 600 !important;
    letter-spacing: 0.14em !important;
    text-transform: uppercase !important;
    color: var(--atelier-charcoal) !important;
    font-size: 11px !important;
}

[data-testid="stSelectbox"] > div > div {
    background: var(--atelier-surface) !important;
    border: 1px solid var(--atelier-border) !important;
    border-radius: 3px !important;
    color: var(--atelier-noir) !important;
    font-family: 'Plus Jakarta Sans', sans-serif !important;
    font-size: 14px !important;
    box-shadow: none !important;
}

[data-testid="stSelectbox"] > div > div:focus-within {
    border-color: var(--atelier-noir) !important;
    box-shadow: 0 0 0 1px var(--atelier-noir) !important;
}

[data-testid="stNumberInput"] input {
    background: var(--atelier-surface) !important;
    border: 1px solid var(--atelier-border) !important;
    border-radius: 3px !important;
    color: var(--atelier-noir) !important;
    font-family: 'Plus Jakarta Sans', sans-serif !important;
}

[data-testid="stPopover"] > div > button {
    background: var(--atelier-surface) !important;
    border: 1px solid var(--atelier-border) !important;
    border-radius: 3px !important;
    color: var(--atelier-noir) !important;
    font-family: 'Plus Jakarta Sans', sans-serif !important;
    font-weight: 600 !important;
    letter-spacing: 0.12em !important;
    text-transform: uppercase !important;
    font-size: 11px !important;
}

[data-testid="stPopover"] > div > button:hover {
    background: var(--atelier-plate) !important;
    border-color: #CCCCCC !important;
}

[data-testid="stAlert"] {
    background: var(--atelier-surface) !important;
    border: 1px solid var(--atelier-border) !important;
    border-left: 3px solid var(--atelier-noir) !important;
    border-radius: 3px !important;
    color: var(--atelier-noir) !important;
    box-shadow: 0 2px 10px rgba(18, 18, 18, 0.02) !important;
}

[data-testid="stExpander"] {
    background: var(--atelier-surface) !important;
    border: 1px solid var(--atelier-border) !important;
    border-radius: 3px !important;
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

[data-testid="stCaptionContainer"], p.caption {
    color: var(--atelier-muted) !important;
    font-size: 13px !important;
    line-height: 1.55 !important;
}

@media (max-width: 768px) {
    .atelier-masthead, .mcm-banner, .hud-banner {
        padding: 24px 20px !important;
    }
    .atelier-title, .mcm-title-text, .hud-title-text {
        font-size: 28px !important;
    }
    .atelier-metadata {
        gap: 8px;
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
