import streamlit as st
import json
import os
import subprocess
subprocess.run(["playwright", "install", "chromium"])
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright

STATE_FILE = "monitor_state.json"
URLS_FILE = "monitored_urls.json"

# -----------------------------
# Helpers for saving/loading
# -----------------------------
def load_json(path, default):
    if os.path.exists(path):
        with open(path, "r") as f:
            return json.load(f)
    return default

def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)

# -----------------------------
# Per-site configuration
# -----------------------------

# Domain -> list of CSS selectors that should work best for that site
SITE_SELECTORS = {
    # AP Oddities page
    "apnews.com": [
        "a[href^='https://apnews.com/article/']",
    ],
    # Sky News Offbeat
    "news.sky.com": [
        "a.sdc-site-tile__headline-link",
    ],
    # NPR Strange News
    "www.npr.org": [
        "h2.title a",
    ],
    # UPI Odd News
    "www.upi.com": [
        "a[href*='/Odd_News/']",
    ],
    # NY Post Weird but True
    "nypost.com": [
        "a[href*='/weird-but-true/']",
    ],
    # HuffPost Weird News
    "www.huffpost.com": [
        "a[href*='/entry/']",
        "a[href*='/weird-news/']",
    ],
    # SCMP Offbeat
    "www.scmp.com": [
        "a[href*='/offbeat/']",
    ],
}

# Generic fallback selectors used for any site
GENERIC_SELECTORS = [
    "a[href*='/article']",
    "a[href*='/story/']",
    "a[href*='/weird']",
    "a[href*='/Odd_News']",
    "a[href*='/offbeat']",
    "h3 a",
    "article a",
]

# -----------------------------
# Playwright helpers
# -----------------------------
def create_context(p, url: str):
    """
    Create a browser context, with special handling for sites
    that block headless browsers (e.g., UPI).
    """
    browser = p.chromium.launch(headless=True)

    parsed = urlparse(url)
    domain = parsed.netloc

    # UPI: spoof a real browser
    if "upi.com" in domain:
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
            locale="en-US",
            timezone_id="America/New_York",
        )
    else:
        context = browser.new_context()

    return browser, context


def prepare_page_for_site(page, url: str):
    """
    Do any per-site page preparation after goto:
    - NPR: wait for article titles to load
    - Sky: scroll to trigger lazy loading
    """
    parsed = urlparse(url)
    domain = parsed.netloc

    # NPR Strange News
    if "npr.org" in domain:
        try:
            page.wait_for_selector("h2.title a", timeout=5000)
        except:
            pass

    # Sky News Offbeat
    if "news.sky.com" in domain:
        try:
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(1500)
        except:
            pass


# -----------------------------
# Scraping functions
# -----------------------------
def fetch_links(url):
    """Extracts article links from a news page, with per-site logic."""
    try:
        with sync_playwright() as p:
            browser, context = create_context(p, url)
            page = context.new_page()

            page.goto(url, timeout=60000, wait_until="domcontentloaded")
            prepare_page_for_site(page, url)

            parsed = urlparse(url)
            domain = parsed.netloc

            # Build selector list: site-specific first, then generic fallback
            selectors = SITE_SELECTORS.get(domain, []) + GENERIC_SELECTORS

            articles = []
            for sel in selectors:
                try:
                    links = page.eval_on_selector_all(
                        sel,
                        """
                        nodes => nodes.map(a => ({
                            title: a.innerText.trim(),
                            url: a.href
                        })).filter(x => x.title && x.url)
                        """
                    )
                    if links:
                        articles = links
                        break
                except Exception:
                    continue

            browser.close()
            # Limit to 10 to keep state small
            articles = articles[:10]
            print(f"Scraped from {url}:", articles)
            return articles

    except Exception as e:
        st.error(f"Error scraping {url}: {e}")
        return []


from readability import Document
from bs4 import BeautifulSoup

def fetch_article_reader_mode(url):
    """Extracts clean readable article text using Readability."""
    try:
        with sync_playwright() as p:
            browser, context = create_context(p, url)
            page = context.new_page()

            page.goto(url, timeout=60000, wait_until="domcontentloaded")

            html = page.content()
            browser.close()

        doc = Document(html)
        cleaned_html = doc.summary()
        title = doc.title()

        soup = BeautifulSoup(cleaned_html, "html.parser")
        return f"<h1>{title}</h1>" + str(soup)

    except Exception as e:
        return f"<p>Error loading article: {e}</p>"


def fetch_article_html(url):
    """Fetch full HTML of the article (minus scripts)."""
    try:
        with sync_playwright() as p:
            browser, context = create_context(p, url)
            page = context.new_page()

            page.goto(url, timeout=60000, wait_until="domcontentloaded")

            page.evaluate("""
                document.querySelectorAll('script, iframe, noscript').forEach(e => e.remove());
            """)

            html = page.content()
            browser.close()
            return html

    except Exception as e:
        return f"<p>Error loading article: {e}</p>"


# -----------------------------
# Streamlit UI
# -----------------------------
st.title("📰 News Monitor (ISP‑Bypass Mode)")

urls = load_json(URLS_FILE, [])
state = load_json(STATE_FILE, {})

# --- Add new URL ---
st.subheader("Add a site to monitor")
new_url = st.text_input("Enter news URL")

if st.button("Add URL"):
    if new_url and new_url not in urls:
        urls.append(new_url)
        save_json(URLS_FILE, urls)
        st.success("Added!")
    else:
        st.warning("URL already exists or empty")

# --- List monitored URLs ---
st.subheader("Monitored sites")
for u in urls:
    st.write("•", u)

# --- Check for updates ---
st.subheader("Check for updates")
if st.button("Check now"):
    st.write("Checking…")

    new_articles_global = []

    for url in urls:
        st.write(f"🔍 {url}")
        articles = fetch_links(url)
        if not articles:
            continue

        current_urls = {a["url"] for a in articles}

        # First time seeing this site: treat all as new
        if url not in state:
            new_articles = articles
            new_articles_global.extend(new_articles)
            state[url] = list(current_urls)
            continue

        old_urls = set(state[url])
        new_urls = current_urls - old_urls

        new_articles = [a for a in articles if a["url"] in new_urls]
        new_articles_global.extend(new_articles)

        # Keep only the latest snapshot (max 10 URLs)
        state[url] = list(current_urls)

    save_json(STATE_FILE, state)

    st.session_state["new_articles"] = new_articles_global
    st.success("Done!")
    if new_articles_global:
        st.write("✅ New articles found:")
    else:
        st.write("ℹ️ No new articles found.")


# --- Show new articles ---
st.subheader("New articles")

if "new_articles" not in st.session_state:
    st.info("Press 'Check now' to scan for updates.")
else:
    for i, art in enumerate(st.session_state["new_articles"]):
        # Use index in key to avoid duplicates even if URL repeats
        btn_key = f"{art['url']}_{i}"
        if st.button(art["title"], key=btn_key):
            st.session_state["selected_article"] = art["url"]

# --- Manual URL reader ---
st.subheader("Read any URL (bypass ISP / region blocks)")

read_url = st.text_input("Enter URL to read")

mode_manual = st.radio("View mode:", ["HTML", "Reader Mode"], key="manual_mode")

if st.button("Read URL"):
    if read_url:
        st.write("Loading...")

        if mode_manual == "HTML":
            html = fetch_article_html(read_url)
        else:
            html = fetch_article_reader_mode(read_url)

        st.components.v1.html(html, height=800, scrolling=True)

# --- Article viewer ---
if "selected_article" in st.session_state:
    st.subheader("Article content")

    mode = st.radio("View mode:", ["HTML", "Reader Mode"], key="article_mode")

    url = st.session_state["selected_article"]

    if mode == "HTML":
        html = fetch_article_html(url)
    else:
        html = fetch_article_reader_mode(url)

    st.components.v1.html(html, height=800, scrolling=True)
