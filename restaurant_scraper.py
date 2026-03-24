#!/usr/bin/env python3
"""
Restaurant Email Scraper
Finds restaurant emails for a given area using web search + site scraping.

Usage:
    pip install requests beautifulsoup4 googlesearch-python lxml
    python restaurant_scraper.py --area "Melbourne CBD" --limit 30
    python restaurant_scraper.py --area "Fitzroy Melbourne" --limit 20 --output fitzroy.json
"""

import argparse
import json
import re
import time
import random
import sys
from urllib.parse import urlparse, urljoin
from datetime import datetime

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    print("Missing dependencies. Run:\n  pip install requests beautifulsoup4 lxml")
    sys.exit(1)

# ── Constants ──────────────────────────────────────────────────────────────────

EMAIL_RE = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-AU,en;q=0.9",
}

# Pages to check for contact emails
CONTACT_PATHS = [
    "/contact", "/contact-us", "/about", "/about-us",
    "/reservations", "/book", "/enquire", "/info",
]

# Domains to skip (booking platforms, social media, etc.)
SKIP_DOMAINS = {
    "google.com", "facebook.com", "instagram.com", "tripadvisor.com",
    "yelp.com", "zomato.com", "opentable.com", "dimmi.com.au",
    "menulog.com.au", "ubereats.com", "deliveroo.com.au", "doordash.com",
    "twitter.com", "tiktok.com", "youtube.com", "linkedin.com",
    "bing.com", "yahoo.com", "wix.com", "squarespace.com",
}

# ── Helpers ────────────────────────────────────────────────────────────────────

def log(msg, level="INFO"):
    ts = datetime.now().strftime("%H:%M:%S")
    colors = {"INFO": "\033[36m", "OK": "\033[32m", "WARN": "\033[33m", "ERR": "\033[31m"}
    reset = "\033[0m"
    c = colors.get(level, "")
    print(f"{c}[{ts}] [{level}]{reset} {msg}")


def safe_get(url, timeout=10):
    """GET request with error handling."""
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
        if r.status_code == 200:
            return r
    except Exception:
        pass
    return None


def extract_emails_from_text(text):
    """Find all emails in a block of text, filtering out junk."""
    raw = EMAIL_RE.findall(text)
    clean = set()
    for e in raw:
        e = e.lower().rstrip(".")
        # Skip image/asset emails, example domains, etc.
        if any(skip in e for skip in ["@example", "@test", "@domain", "@your", ".png", ".jpg", ".gif"]):
            continue
        clean.add(e)
    return clean


def extract_emails_from_page(url):
    """Fetch a page and extract emails from text + mailto links."""
    r = safe_get(url)
    if not r:
        return set()

    soup = BeautifulSoup(r.text, "lxml")

    # Remove script/style noise
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    emails = set()

    # mailto: links (most reliable)
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.lower().startswith("mailto:"):
            email = href[7:].split("?")[0].strip().lower()
            if EMAIL_RE.match(email):
                emails.add(email)

    # Plain text scan
    emails |= extract_emails_from_text(soup.get_text(" "))

    return emails


def find_emails_for_site(base_url, max_pages=5):
    """Try homepage + common contact paths, return all found emails."""
    parsed = urlparse(base_url)
    base = f"{parsed.scheme}://{parsed.netloc}"

    all_emails = set()

    # 1. Homepage
    all_emails |= extract_emails_from_page(base_url)
    if all_emails:
        return all_emails  # Found on homepage, done

    # 2. Contact / about pages
    for path in CONTACT_PATHS[:max_pages]:
        url = base + path
        found = extract_emails_from_page(url)
        all_emails |= found
        if found:
            break
        time.sleep(random.uniform(0.3, 0.8))

    return all_emails


def search_restaurants(area, limit):
    """
    Search for restaurant websites using a Google-style scrape of search results.
    Falls back to a DuckDuckGo HTML search (no API key needed).
    """
    query = f'restaurants "{area}" site:* -site:tripadvisor.com -site:zomato.com -site:yelp.com'
    search_url = f"https://html.duckduckgo.com/html/?q={requests.utils.quote(query)}"

    log(f"Searching: {area}")
    r = safe_get(search_url, timeout=15)
    if not r:
        log("Search request failed. Try again or use --urls flag.", "ERR")
        return []

    soup = BeautifulSoup(r.text, "lxml")
    results = []
    seen_domains = set()

    for a in soup.find_all("a", class_="result__url"):
        href = a.get("href", "")
        if not href.startswith("http"):
            href = "https://" + href.strip()

        parsed = urlparse(href)
        domain = parsed.netloc.replace("www.", "")

        if domain in SKIP_DOMAINS or domain in seen_domains:
            continue
        if not domain:
            continue

        seen_domains.add(domain)
        results.append(href)

        if len(results) >= limit:
            break

    # Also try a second page
    if len(results) < limit:
        r2 = safe_get(search_url + "&s=30", timeout=15)
        if r2:
            soup2 = BeautifulSoup(r2.text, "lxml")
            for a in soup2.find_all("a", class_="result__url"):
                href = a.get("href", "")
                if not href.startswith("http"):
                    href = "https://" + href.strip()
                parsed = urlparse(href)
                domain = parsed.netloc.replace("www.", "")
                if domain in SKIP_DOMAINS or domain in seen_domains:
                    continue
                if not domain:
                    continue
                seen_domains.add(domain)
                results.append(href)
                if len(results) >= limit:
                    break

    log(f"Found {len(results)} candidate sites", "OK")
    return results


def guess_restaurant_name(url, soup=None):
    """Try to guess the restaurant name from the page title or domain."""
    if soup:
        title = soup.find("title")
        if title:
            name = title.get_text().strip().split("|")[0].split("–")[0].split("-")[0].strip()
            if name:
                return name

    # Fallback: humanize domain
    domain = urlparse(url).netloc.replace("www.", "").split(".")[0]
    return domain.replace("-", " ").replace("_", " ").title()


# ── Main ───────────────────────────────────────────────────────────────────────

def scrape(area, urls=None, limit=30, delay=1.5, output=None):
    results = []
    failed = []

    # Either use provided URLs or search for them
    sites = urls if urls else search_restaurants(area, limit)

    if not sites:
        log("No sites to scrape.", "ERR")
        return

    log(f"Scraping {len(sites)} sites for emails...")

    for i, url in enumerate(sites, 1):
        if not url.startswith("http"):
            url = "https://" + url

        parsed = urlparse(url)
        domain = parsed.netloc.replace("www.", "")
        log(f"[{i}/{len(sites)}] {domain}")

        # Fetch homepage to get name
        r = safe_get(url)
        soup = BeautifulSoup(r.text, "lxml") if r else None
        name = guess_restaurant_name(url, soup)

        # Extract emails
        emails = find_emails_for_site(url)

        if emails:
            for email in emails:
                entry = {
                    "name": name,
                    "email": email,
                    "website": url,
                    "domain": domain,
                    "area": area,
                    "scraped_at": datetime.now().isoformat(),
                }
                results.append(entry)
                log(f"  ✓ {email}", "OK")
        else:
            failed.append({"website": url, "domain": domain, "name": name})
            log(f"  ✗ No email found", "WARN")

        time.sleep(random.uniform(delay * 0.7, delay * 1.3))

    # Summary
    print()
    log(f"Done. Found emails: {len(results)} | No email: {len(failed)}", "OK")

    out_data = {
        "area": area,
        "scraped_at": datetime.now().isoformat(),
        "total_with_email": len(results),
        "total_no_email": len(failed),
        "restaurants": results,
        "no_email": failed,
    }

    out_file = output or f"restaurants_{area.replace(' ', '_').lower()}.json"
    with open(out_file, "w") as f:
        json.dump(out_data, f, indent=2)

    log(f"Saved to: {out_file}", "OK")

    # Also save a simple CSV
    csv_file = out_file.replace(".json", ".csv")
    with open(csv_file, "w") as f:
        f.write("Name,Email,Website,Area\n")
        for r in results:
            f.write(f'"{r["name"]}","{r["email"]}","{r["website"]}","{r["area"]}"\n')
    log(f"CSV saved to: {csv_file}", "OK")

    return out_data


def main():
    parser = argparse.ArgumentParser(
        description="Scrape restaurant emails for a given area.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python restaurant_scraper.py --area "Melbourne CBD"
  python restaurant_scraper.py --area "Fitzroy Melbourne" --limit 20
  python restaurant_scraper.py --area "Sydney CBD" --output sydney.json
  python restaurant_scraper.py --area "Richmond Melbourne" --delay 2.0
  python restaurant_scraper.py --urls restaurant_sites.txt --area "Custom List"

restaurant_sites.txt format (one URL per line):
  https://restaurant1.com.au
  https://restaurant2.com.au
        """,
    )
    parser.add_argument("--area", default="Melbourne CBD", help="Area to search (e.g. 'Melbourne CBD')")
    parser.add_argument("--limit", type=int, default=30, help="Max number of sites to scrape (default: 30)")
    parser.add_argument("--delay", type=float, default=1.5, help="Delay between requests in seconds (default: 1.5)")
    parser.add_argument("--output", help="Output JSON filename (default: auto-named)")
    parser.add_argument("--urls", help="Text file with one URL per line (skips search step)")

    args = parser.parse_args()

    urls = None
    if args.urls:
        try:
            with open(args.urls) as f:
                urls = [line.strip() for line in f if line.strip()]
            log(f"Loaded {len(urls)} URLs from {args.urls}")
        except FileNotFoundError:
            log(f"URL file not found: {args.urls}", "ERR")
            sys.exit(1)

    scrape(
        area=args.area,
        urls=urls,
        limit=args.limit,
        delay=args.delay,
        output=args.output,
    )


if __name__ == "__main__":
    main()
