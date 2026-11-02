"""Mid-Century Modern stylesheet and layout enhancements for the Streamlit UI."""
import streamlit as st

CUSTOM_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Serif+Display:ital@0;1&family=Outfit:wght@400;500;600;700;800&family=Plus+Jakarta+Sans:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap');

:root {
    --mcm-bg: #FAF7F2;
    --mcm-card: #FFFFFF;
    --mcm-walnut: #251B14;
    --mcm-espresso: #1E1610;
    --mcm-terracotta: #C85A32;
    --mcm-terracotta-dark: #B34820;
    --mcm-ochre: #DDA15E;
    --mcm-olive: #5B7546;
    --mcm-teal: #245864;
    --mcm-linen: #F5EFEB;
    --mcm-border: #E5DCCE;
    --mcm-border-subtle: #EFE8DD;
    --mcm-text: #281F19;
    --mcm-text-muted: #786C60;
}

/* Background Canvas & Architectural Grid */
.stApp {
    background: radial-gradient(circle at 50% 0%, #FCFBF9 0%, #F8F4EC 65%, #F0E9DE 100%) !important;
    color: var(--mcm-text) !important;
    font-family: 'Plus Jakarta Sans', -apple-system, BlinkMacSystemFont, sans-serif !important;
}

.stApp::before {
    content: "";
    position: fixed;
    top: 0;
    left: 0;
    width: 100vw;
    height: 100vh;
    background-image: 
        linear-gradient(to right, rgba(198, 166, 138, 0.08) 1px, transparent 1px),
        linear-gradient(to bottom, rgba(198, 166, 138, 0.08) 1px, transparent 1px);
    background-size: 38px 38px;
    pointer-events: none;
    z-index: 0;
}

header[data-testid="stHeader"] {
    background: transparent !important;
}

.block-container {
    max-width: 1260px;
    padding-top: 1.8rem;
    padding-bottom: 3.5rem;
    position: relative;
    z-index: 1;
}

/* Mid-Century Modern Atelier Pavilion Banner */
.mcm-banner, .hud-banner {
    background: #251B14 !important;
    border: 1px solid #3E2F24 !important;
    border-top: 4px solid var(--mcm-terracotta) !important;
    border-radius: 16px !important;
    padding: 26px 32px !important;
    margin-bottom: 24px !important;
    box-shadow: 0 14px 34px rgba(37, 27, 20, 0.18), inset 0 1px 0 rgba(255, 255, 255, 0.07) !important;
    position: relative !important;
    overflow: hidden !important;
    color: #FAF6EE !important;
}

.mcm-banner::after, .hud-banner::after {
    content: "";
    position: absolute;
    top: 0;
    right: 0;
    width: 260px;
    height: 100%;
    background: radial-gradient(circle at 100% 50%, rgba(200, 90, 50, 0.18) 0%, rgba(221, 161, 94, 0.08) 45%, transparent 75%);
    pointer-events: none;
}

.mcm-telemetry, .hud-telemetry {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 10px;
    margin-bottom: 14px;
}

.mcm-status-badge, .hud-status-badge {
    display: inline-flex;
    align-items: center;
    gap: 7px;
    font-family: 'Outfit', sans-serif;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 1px;
    text-transform: uppercase;
    color: #8EB872;
    background: rgba(142, 184, 114, 0.16);
    border: 1px solid rgba(142, 184, 114, 0.35);
    padding: 4px 12px;
    border-radius: 20px;
}

.mcm-pulse, .hud-pulse {
    width: 7px;
    height: 7px;
    background-color: #8EB872;
    border-radius: 50%;
    box-shadow: 0 0 6px #8EB872;
    animation: mcm-pulse-glow 2.5s infinite ease-in-out;
}

@keyframes mcm-pulse-glow {
    0%, 100% { opacity: 1; transform: scale(1); }
    50% { opacity: 0.4; transform: scale(0.85); }
}

.mcm-chip, .hud-chip {
    font-family: 'Outfit', sans-serif;
    font-size: 11px;
    font-weight: 500;
    color: #D3C9BD;
    background: rgba(255, 255, 255, 0.07);
    border: 1px solid rgba(255, 255, 255, 0.12);
    padding: 4px 11px;
    border-radius: 6px;
    letter-spacing: 0.6px;
    text-transform: uppercase;
}

.mcm-title-text, .hud-title-text {
    font-family: 'DM Serif Display', Georgia, serif;
    font-size: 32px;
    font-weight: 400;
    letter-spacing: 0.4px;
    color: #FFFDF9;
    margin: 0 0 8px 0;
    text-shadow: 0 2px 4px rgba(0, 0, 0, 0.25);
}

.mcm-subtitle-text, .hud-subtitle-text {
    font-size: 14.5px;
    color: #CFC3B3;
    margin: 0;
    line-height: 1.55;
    font-weight: 400;
}

/* Mid-Century Product Cards */
[data-testid="stVerticalBlockBorderWrapper"] {
    position: relative !important;
    background: var(--mcm-card) !important;
    border: 1px solid var(--mcm-border) !important;
    border-radius: 14px !important;
    padding: 16px !important;
    box-shadow: 0 4px 16px rgba(40, 31, 25, 0.05), 0 1px 3px rgba(40, 31, 25, 0.03) !important;
    transition: all 0.26s cubic-bezier(0.16, 1, 0.3, 1) !important;
}

[data-testid="stVerticalBlockBorderWrapper"]:hover {
    transform: translateY(-4px) !important;
    border-color: var(--mcm-terracotta) !important;
    box-shadow: 0 14px 30px rgba(200, 90, 50, 0.14), 0 3px 8px rgba(40, 31, 25, 0.04) !important;
}

/* Oatmeal Fabric Image Plate */
[data-testid="stImage"] img {
    height: 245px !important;
    object-fit: contain !important;
    background: var(--mcm-linen) !important;
    border: 1px solid #EAE2D5 !important;
    border-radius: 9px !important;
    transition: transform 0.3s cubic-bezier(0.16, 1, 0.3, 1) !important;
}

[data-testid="stVerticalBlockBorderWrapper"]:hover [data-testid="stImage"] img {
    transform: scale(1.025) !important;
}

/* Mid-Century Swatch Badges */
.mcm-badge, .cyber-badge {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    font-family: 'Outfit', sans-serif;
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.6px;
    text-transform: uppercase;
    padding: 4px 9px;
    border-radius: 6px;
    margin: 4px 0;
}

.mcm-badge-bought, .cyber-badge-bought {
    background: #EDF4E8;
    color: #385226;
    border: 1px solid #C4DAB8;
}

.mcm-badge-popular, .cyber-badge-popular {
    background: #FDF0EB;
    color: #A94323;
    border: 1px solid #F3C8BA;
}

.mcm-badge-new, .cyber-badge-new {
    background: #FAF3DE;
    color: #8C5C15;
    border: 1px solid #EED8A4;
}

.mcm-badge-rank, .cyber-badge-rank {
    background: #E8F3F5;
    color: #1A5460;
    border: 1px solid #B4D7DF;
}

/* Mid-Century Modern Buttons */
[data-testid="stButton"] button[kind="primary"] {
    background: var(--mcm-terracotta) !important;
    color: #FFFFFF !important;
    border: 1px solid var(--mcm-terracotta-dark) !important;
    border-radius: 8px !important;
    font-family: 'Outfit', sans-serif !important;
    font-weight: 600 !important;
    font-size: 14.5px !important;
    letter-spacing: 0.8px !important;
    text-transform: uppercase !important;
    box-shadow: 0 4px 14px rgba(200, 90, 50, 0.3) !important;
    transition: all 0.2s cubic-bezier(0.16, 1, 0.3, 1) !important;
}

[data-testid="stButton"] button[kind="primary"]:hover {
    background: var(--mcm-terracotta-dark) !important;
    box-shadow: 0 6px 20px rgba(200, 90, 50, 0.45) !important;
    transform: translateY(-2px) !important;
}

[data-testid="stButton"] button:not([kind="primary"]) {
    background: #FAF6EF !important;
    color: var(--mcm-walnut) !important;
    border: 1px solid #D6CAB8 !important;
    border-radius: 8px !important;
    font-family: 'Outfit', sans-serif !important;
    font-weight: 600 !important;
    font-size: 13px !important;
    letter-spacing: 0.6px !important;
    text-transform: uppercase !important;
    transition: all 0.2s cubic-bezier(0.16, 1, 0.3, 1) !important;
}

[data-testid="stButton"] button:not([kind="primary"]):hover {
    background: var(--mcm-walnut) !important;
    color: #FAF6EF !important;
    border-color: var(--mcm-walnut) !important;
    box-shadow: 0 4px 14px rgba(37, 27, 20, 0.2) !important;
    transform: translateY(-2px) !important;
}

/* Elegant Editorial Tabs */
[data-testid="stTabs"] {
    border-bottom: 1px solid var(--mcm-border) !important;
    margin-bottom: 24px !important;
}

[data-testid="stTab"] {
    font-family: 'DM Serif Display', Georgia, serif !important;
    font-weight: 400 !important;
    font-size: 18px !important;
    letter-spacing: 0.3px !important;
    color: var(--mcm-text-muted) !important;
    padding: 12px 24px !important;
    border-bottom: 2px solid transparent !important;
    transition: all 0.2s ease !important;
}

[data-testid="stTab"][aria-selected="true"] {
    color: var(--mcm-walnut) !important;
    border-bottom: 2px solid var(--mcm-terracotta) !important;
}

/* Form Controls & Inputs */
[data-testid="stWidgetLabel"] label, label {
    font-family: 'Outfit', sans-serif !important;
    font-weight: 600 !important;
    letter-spacing: 0.6px !important;
    text-transform: uppercase !important;
    color: var(--mcm-text) !important;
    font-size: 12.5px !important;
}

[data-testid="stSelectbox"] > div > div {
    background: #FFFFFF !important;
    border: 1px solid #D8CFC2 !important;
    border-radius: 8px !important;
    color: var(--mcm-text) !important;
    font-family: 'Plus Jakarta Sans', sans-serif !important;
    font-size: 14.5px !important;
}

[data-testid="stSelectbox"] > div > div:focus-within {
    border-color: var(--mcm-terracotta) !important;
    box-shadow: 0 0 0 2px rgba(200, 90, 50, 0.2) !important;
}

[data-testid="stNumberInput"] input {
    background: #FFFFFF !important;
    border: 1px solid #D8CFC2 !important;
    color: var(--mcm-text) !important;
    font-family: 'JetBrains Mono', monospace !important;
}

[data-testid="stPopover"] > div > button {
    background: #FAF6EF !important;
    border: 1px solid #D6CAB8 !important;
    border-radius: 8px !important;
    color: var(--mcm-walnut) !important;
    font-family: 'Outfit', sans-serif !important;
    font-weight: 600 !important;
    letter-spacing: 0.6px !important;
    text-transform: uppercase !important;
}

[data-testid="stPopover"] > div > button:hover {
    background: var(--mcm-walnut) !important;
    color: #FAF6EE !important;
    border-color: var(--mcm-walnut) !important;
}

[data-testid="stAlert"] {
    background: #FDF9F2 !important;
    border: 1px solid var(--mcm-border) !important;
    border-radius: 10px !important;
    color: var(--mcm-text) !important;
}

[data-testid="stExpander"] {
    background: #FAF6EF !important;
    border: 1px solid var(--mcm-border) !important;
    border-radius: 10px !important;
}

h1, h2, h3, h4 {
    font-family: 'DM Serif Display', Georgia, serif !important;
    color: var(--mcm-walnut) !important;
    font-weight: 400 !important;
    letter-spacing: 0.2px !important;
}

[data-testid="stCaptionContainer"], p.caption {
    color: var(--mcm-text-muted) !important;
    font-size: 13px !important;
}

@media (max-width: 640px) {
    [data-testid="stImage"] img {
        height: 210px !important;
    }
    .mcm-title-text, .hud-title-text {
        font-size: 24px !important;
    }
    .mcm-banner, .hud-banner {
        padding: 20px 22px !important;
    }
}
</style>
"""


def inject_custom_css():
    """Inject responsive Mid-Century Modern styling into the current Streamlit page."""
    st.html(CUSTOM_CSS)
