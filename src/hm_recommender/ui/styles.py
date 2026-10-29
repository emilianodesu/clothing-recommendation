"""Custom stylesheet and layout enhancements for the Streamlit UI."""
import streamlit as st

CUSTOM_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Orbitron:wght@600;700;800;900&family=Rajdhani:wght@500;600;700&family=JetBrains+Mono:wght@400;500;600;700&family=Plus+Jakarta+Sans:wght@400;500;600;700&display=swap');

:root {
    --cyber-bg: #07090e;
    --cyber-neon-cyan: #00f0ff;
    --cyber-neon-red: #ff003c;
    --cyber-neon-green: #00ff9d;
    --cyber-neon-purple: #c084fc;
    --cyber-neon-amber: #fbbf24;
    --cyber-border: rgba(0, 240, 255, 0.22);
    --cyber-glow-cyan: rgba(0, 240, 255, 0.35);
}

/* Background Cyber Grid & Void Styling */
.stApp {
    background: radial-gradient(circle at 50% 0%, #0d1527 0%, #06080e 75%, #030408 100%) !important;
    color: #e2e8f0 !important;
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
        linear-gradient(to right, rgba(0, 240, 255, 0.035) 1px, transparent 1px),
        linear-gradient(to bottom, rgba(0, 240, 255, 0.035) 1px, transparent 1px);
    background-size: 36px 36px;
    pointer-events: none;
    z-index: 0;
}

header[data-testid="stHeader"] {
    background: transparent !important;
}

.block-container {
    max-width: 1280px;
    padding-top: 1.8rem;
    padding-bottom: 3.5rem;
    position: relative;
    z-index: 1;
}

/* Futuristic HUD Telemetry Banner */
.hud-banner {
    background: rgba(10, 17, 32, 0.82);
    backdrop-filter: blur(16px);
    -webkit-backdrop-filter: blur(16px);
    border: 1px solid rgba(0, 240, 255, 0.28);
    border-left: 4px solid #ff003c;
    border-radius: 14px;
    padding: 24px 28px;
    margin-bottom: 24px;
    box-shadow: 0 10px 30px rgba(0, 0, 0, 0.6), inset 0 0 20px rgba(0, 240, 255, 0.04);
    position: relative;
    overflow: hidden;
}

.hud-banner::after {
    content: "";
    position: absolute;
    top: 0;
    right: 0;
    width: 140px;
    height: 100%;
    background: linear-gradient(90deg, transparent, rgba(0, 240, 255, 0.05));
    pointer-events: none;
}

.hud-telemetry {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 10px;
    margin-bottom: 12px;
}

.hud-status-badge {
    display: inline-flex;
    align-items: center;
    gap: 7px;
    font-family: 'JetBrains Mono', monospace;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 1px;
    text-transform: uppercase;
    color: #00ff9d;
    background: rgba(0, 255, 157, 0.12);
    border: 1px solid rgba(0, 255, 157, 0.35);
    padding: 4px 11px;
    border-radius: 20px;
    box-shadow: 0 0 12px rgba(0, 255, 157, 0.2);
}

.hud-pulse {
    width: 7px;
    height: 7px;
    background-color: #00ff9d;
    border-radius: 50%;
    box-shadow: 0 0 8px #00ff9d;
    animation: pulse-glow 2s infinite ease-in-out;
}

@keyframes pulse-glow {
    0%, 100% { opacity: 1; transform: scale(1); box-shadow: 0 0 8px #00ff9d; }
    50% { opacity: 0.4; transform: scale(0.85); box-shadow: 0 0 2px #00ff9d; }
}

.hud-chip {
    font-family: 'JetBrains Mono', monospace;
    font-size: 11px;
    font-weight: 500;
    color: #94a3b8;
    background: rgba(255, 255, 255, 0.04);
    border: 1px solid rgba(255, 255, 255, 0.08);
    padding: 4px 10px;
    border-radius: 6px;
    letter-spacing: 0.5px;
}

.hud-title-text {
    font-family: 'Orbitron', sans-serif;
    font-size: 26px;
    font-weight: 800;
    letter-spacing: 1px;
    background: linear-gradient(135deg, #ffffff 0%, #a5f3fc 45%, #ff0055 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    margin: 0 0 6px 0;
}

.hud-subtitle-text {
    font-size: 14px;
    color: #94a3b8;
    margin: 0;
    line-height: 1.5;
}

/* Fashion Item Cards (Glassmorphism & HUD Glow) */
[data-testid="stVerticalBlockBorderWrapper"] {
    position: relative !important;
    background: rgba(10, 16, 30, 0.8) !important;
    backdrop-filter: blur(14px) !important;
    -webkit-backdrop-filter: blur(14px) !important;
    border: 1px solid rgba(0, 240, 255, 0.18) !important;
    border-radius: 12px !important;
    padding: 12px !important;
    box-shadow: 0 6px 24px rgba(0, 0, 0, 0.5), inset 0 0 16px rgba(0, 240, 255, 0.03) !important;
    transition: all 0.28s cubic-bezier(0.16, 1, 0.3, 1) !important;
}

[data-testid="stVerticalBlockBorderWrapper"]:hover {
    transform: translateY(-4px) !important;
    border-color: rgba(0, 240, 255, 0.6) !important;
    box-shadow: 0 12px 36px rgba(0, 240, 255, 0.22), inset 0 0 20px rgba(0, 240, 255, 0.08) !important;
}

/* Image Holographic Frame */
[data-testid="stImage"] img {
    height: 245px !important;
    object-fit: contain !important;
    background: radial-gradient(circle at center, #172138 0%, #070a14 100%) !important;
    border: 1px solid rgba(255, 255, 255, 0.08) !important;
    border-radius: 8px !important;
    transition: transform 0.3s cubic-bezier(0.16, 1, 0.3, 1) !important;
}

[data-testid="stVerticalBlockBorderWrapper"]:hover [data-testid="stImage"] img {
    transform: scale(1.03) !important;
}

/* Cyber Badges */
.cyber-badge {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    font-family: 'JetBrains Mono', monospace;
    font-size: 10.5px;
    font-weight: 600;
    letter-spacing: 0.6px;
    text-transform: uppercase;
    padding: 3px 8px;
    border-radius: 5px;
    margin: 3px 0;
}

.cyber-badge-bought {
    background: rgba(0, 255, 157, 0.12);
    color: #00ff9d;
    border: 1px solid rgba(0, 255, 157, 0.35);
    box-shadow: 0 0 8px rgba(0, 255, 157, 0.15);
}

.cyber-badge-popular {
    background: rgba(0, 240, 255, 0.12);
    color: #00f0ff;
    border: 1px solid rgba(0, 240, 255, 0.35);
    box-shadow: 0 0 8px rgba(0, 240, 255, 0.15);
}

.cyber-badge-new {
    background: rgba(217, 70, 239, 0.14);
    color: #f472b6;
    border: 1px solid rgba(217, 70, 239, 0.4);
    box-shadow: 0 0 8px rgba(217, 70, 239, 0.15);
}

.cyber-badge-rank {
    background: rgba(251, 191, 36, 0.14);
    color: #fbbf24;
    border: 1px solid rgba(251, 191, 36, 0.4);
    box-shadow: 0 0 8px rgba(251, 191, 36, 0.15);
}

/* Futuristic Action Buttons */
[data-testid="stButton"] button[kind="primary"] {
    background: linear-gradient(135deg, #ff0844 0%, #ff1744 50%, #e50010 100%) !important;
    color: #ffffff !important;
    border: 1px solid rgba(255, 255, 255, 0.25) !important;
    border-radius: 8px !important;
    font-family: 'Rajdhani', sans-serif !important;
    font-weight: 700 !important;
    font-size: 15px !important;
    letter-spacing: 1.5px !important;
    text-transform: uppercase !important;
    box-shadow: 0 0 20px rgba(255, 8, 68, 0.45) !important;
    transition: all 0.2s cubic-bezier(0.16, 1, 0.3, 1) !important;
}

[data-testid="stButton"] button[kind="primary"]:hover {
    box-shadow: 0 0 35px rgba(255, 8, 68, 0.8), 0 0 10px #ffffff !important;
    transform: translateY(-2px) scale(1.01) !important;
}

[data-testid="stButton"] button:not([kind="primary"]) {
    background: rgba(0, 240, 255, 0.07) !important;
    color: #00f0ff !important;
    border: 1px solid rgba(0, 240, 255, 0.4) !important;
    border-radius: 8px !important;
    font-family: 'Rajdhani', sans-serif !important;
    font-weight: 600 !important;
    font-size: 13.5px !important;
    letter-spacing: 1.2px !important;
    text-transform: uppercase !important;
    transition: all 0.2s cubic-bezier(0.16, 1, 0.3, 1) !important;
}

[data-testid="stButton"] button:not([kind="primary"]):hover {
    background: #00f0ff !important;
    color: #07090e !important;
    border-color: #00f0ff !important;
    box-shadow: 0 0 22px rgba(0, 240, 255, 0.6) !important;
    transform: translateY(-2px) !important;
}

/* Tabs & Navigation */
[data-testid="stTabs"] {
    border-bottom: 1px solid rgba(0, 240, 255, 0.15) !important;
    margin-bottom: 20px !important;
}

[data-testid="stTab"] {
    font-family: 'Rajdhani', sans-serif !important;
    font-weight: 700 !important;
    letter-spacing: 1.5px !important;
    text-transform: uppercase !important;
    color: #94a3b8 !important;
    padding: 12px 24px !important;
    border-bottom: 2px solid transparent !important;
    transition: all 0.2s ease !important;
}

[data-testid="stTab"][aria-selected="true"] {
    color: #00f0ff !important;
    border-bottom: 2px solid #00f0ff !important;
    text-shadow: 0 0 12px rgba(0, 240, 255, 0.5) !important;
}

/* Form Controls & Inputs */
[data-testid="stSelectbox"] label, [data-testid="stNumberInput"] label {
    font-family: 'Rajdhani', sans-serif !important;
    font-weight: 600 !important;
    letter-spacing: 1px !important;
    text-transform: uppercase !important;
    color: #94a3b8 !important;
    font-size: 13px !important;
}

[data-testid="stSelectbox"] > div > div {
    background: rgba(13, 20, 36, 0.85) !important;
    border: 1px solid rgba(0, 240, 255, 0.25) !important;
    border-radius: 8px !important;
    color: #f1f5f9 !important;
    font-family: 'Rajdhani', sans-serif !important;
    font-size: 15px !important;
}

[data-testid="stSelectbox"] > div > div:focus-within {
    border-color: #00f0ff !important;
    box-shadow: 0 0 16px rgba(0, 240, 255, 0.35) !important;
}

[data-testid="stNumberInput"] input {
    background: rgba(13, 20, 36, 0.85) !important;
    border: 1px solid rgba(0, 240, 255, 0.25) !important;
    color: #00f0ff !important;
    font-family: 'JetBrains Mono', monospace !important;
}

[data-testid="stPopover"] > div > button {
    background: rgba(13, 20, 36, 0.85) !important;
    border: 1px solid rgba(0, 240, 255, 0.3) !important;
    border-radius: 8px !important;
    color: #00f0ff !important;
    font-family: 'Rajdhani', sans-serif !important;
    font-weight: 600 !important;
    letter-spacing: 1px !important;
    text-transform: uppercase !important;
}

[data-testid="stAlert"] {
    background: rgba(13, 20, 36, 0.85) !important;
    border: 1px solid rgba(0, 240, 255, 0.25) !important;
    border-radius: 10px !important;
    backdrop-filter: blur(10px) !important;
}

[data-testid="stExpander"] {
    background: rgba(10, 16, 29, 0.6) !important;
    border: 1px solid rgba(0, 240, 255, 0.15) !important;
    border-radius: 10px !important;
}

h1, h2, h3, h4 {
    font-family: 'Orbitron', 'Rajdhani', sans-serif !important;
    color: #f1f5f9 !important;
    letter-spacing: 0.5px !important;
}

@media (max-width: 640px) {
    [data-testid="stImage"] img {
        height: 210px !important;
    }
    .hud-title-text {
        font-size: 20px !important;
    }
}
</style>
"""


def inject_custom_css():
    """Inject responsive styling into the current Streamlit page."""
    st.html(CUSTOM_CSS)
