import feedparser
import requests
import re
import json
import os
from datetime import datetime
import html
from transformers import pipeline
from dotenv import load_dotenv

# Load .env file
load_dotenv()

# Slack Webhook URL from .env
slack_webhook_url = os.getenv("SLACK_MARKETWATCH_WEBHOOK_URL")


# List to accumulate posts throughout the week
weekly_posts = []

# Cache file path
CACHE_FILE = "cached_productupdates.json"

def load_cache():
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, "r") as f:
            return json.load(f)
    return {}

def save_cache(cache_data):
    with open(CACHE_FILE, "w") as f:
        json.dump(cache_data, f, indent=4)

# Load the cache at the start
cached_articles = load_cache()

def clean_html(raw_html):
    cleanr = re.compile('<.*?>')
    cleaned = re.sub(cleanr, '', raw_html)
    return html.unescape(cleaned).replace('\xa0', ' ').strip()

def format_date(published_str):
    try:
        dt = datetime.strptime(published_str, "%a, %d %b %Y %H:%M:%S %Z")
        return dt.strftime("%a, %d %b %Y")
    except Exception:
        return published_str.split(" ")[0:4]  # fallback: just chop time part

def detect_theme(summary):
    lowered = summary.lower()
    if any(word in lowered for word in ["attack", "breach", "malware", "zero trust", "ransomware", "exploit", "threat"]):
        return "🔐 Security"
    elif any(word in lowered for word in ["performance", "latency", "speed", "optimization", "edge"]):
        return "🚀 Performance"
    elif any(word in lowered for word in ["cloud", "aws", "azure", "gcp"]):
        return "☁️ Cloud"
    elif any(word in lowered for word in ["api", "architecture", "design", "load balancer"]):
        return "🧱 Architecture"
    elif any(word in lowered for word in ["vulnerability", "cve", "patch", "mitigation"]):
        return "🧪 Vulnerability"
    else:
        return "📰 General"

def estimate_read_time(text):
    word_count = len(text.split())
    minutes = max(1, round(word_count / 200))
    return f"⏱️ Read Time: {minutes} min"

summarizer = pipeline("summarization")

def truncate_text(text, max_token_length=1024):
    """ Truncate text to fit model token length """
    if len(text.split()) > max_token_length:
        text = ' '.join(text.split()[:max_token_length])  # Truncate to fit model limits
    return text

def generate_why_it_matters(text):
    max_len = 100  # Set a reasonable maximum length
    min_len = 30   # Set a minimum length

    # Ensure max_len is greater than min_len and also checks for a safe length
    if max_len < min_len:
        max_len = min_len + 10

    # Prevent issues with very long text by truncating it if necessary
    if len(text.split()) > 1024:  # Max tokens for DistilBART
        text = ' '.join(text.split()[:1024])

    summary = summarizer(text, max_length=max_len, min_length=min_len, do_sample=False)
    return summary[0]['summary_text']


def format_slack_message(item, why_it_matters):
    summary_cleaned = clean_html(item['summary'])
    formatted_date = format_date(item['published'])
    theme = detect_theme(summary_cleaned)
    read_time = estimate_read_time(summary_cleaned)
    formatted_link = f"<{item['link']}|Click here for the full article>"
    return (
        f"*{theme} {item['company']}*\n"
        f"*📰 {item['title']}*\n"
        f"📅 {formatted_date} | {read_time}\n"
        f"🔗 {formatted_link}\n"
        f"📝 {summary_cleaned[:500]}...\n\n"
        f"*Why It Matters:*\n>{why_it_matters}\n\n"
        f"\n───────────────────────────────────────────────\n\n"
    )

# RSS feed URLs for company blogs in the cybersecurity space
rss_feeds = {
    "Cloudflare": "https://blog.cloudflare.com/rss/",
    "Fastly": "https://investors.fastly.com/rss/pressrelease.aspx",
    "Akamai": "https://feeds.feedburner.com/akamai/blog",
    "Radware": "https://www.radware.com/blog/rss/",
    "F5": "https://www.f5.com/labs/rss-feeds/all.xml",
    "Imperva": "https://www.imperva.com/blog/feed/",
    "Fortinet": "https://investor.fortinet.com/rss/news-releases.xml",
    "AWS": "https://aws.amazon.com/blogs/security/feed/",
}

# Microsoft Azure blog feeds in security space
azure_feeds = [
    "https://techcommunity.microsoft.com/t5/s/gxcuf89792/rss/board?board.id=AzureNetworkSecurityBlog",
    "https://techcommunity.microsoft.com/t5/s/gxcuf89792/rss/board?board.id=MicrosoftThreatProtectionBlog",
    "https://techcommunity.microsoft.com/t5/s/gxcuf89792/rss/board?board.id=MicrosoftDefenderCloudBlog",
    # More Azure RSS Feeds...
]

def fetch_rss_with_timeout(url, timeout=5):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3'
    }

    try:
        response = requests.get(url, headers=headers, timeout=timeout)
        if response.status_code != 200:
            print(f"❌ Failed to fetch {url} (Status code: {response.status_code})")
            return None
        return feedparser.parse(response.content)
    except requests.exceptions.Timeout:
        print(f"⏰ Timeout while fetching: {url}")
        return None
    except Exception as e:
        print(f"⚠️ Error fetching {url}: {e}")
        return None

def send_to_slack(message: str):
    payload = {"text": message}
    try:
        response = requests.post(slack_webhook_url, json=payload)
        if response.status_code != 200:
            print(f"❌ Failed to send to Slack: {response.text}")
    except Exception as e:
        print(f"⚠️ Slack error: {e}")

# Fetch and parse the feeds
def fetch_feeds():
    aggregated = []

    for company, url in rss_feeds.items():
        print(f"🔄 Fetching {company} feed...")
        feed = fetch_rss_with_timeout(url)
        if not feed:
            continue
        for entry in feed.entries[:1]:
            # Check if the article is already cached
            if entry.link in cached_articles:
                print(f"📄 Skipping {entry.title} as it was already posted")
                continue
            
            item = {
                "company": company,
                "title": entry.title,
                "link": entry.link,
                "published": entry.get("published", "N/A"),
                "summary": entry.get("summary", "No summary available"),
                "read_time": estimate_read_time(entry.get("summary", "No summary available"))  # Estimate read time based on summary
            }
            
            # Generate why it matters section
            why_it_matters = generate_why_it_matters(item['summary'])
            
            # Send to Slack with updated message including why it matters
            send_to_slack(format_slack_message(item, why_it_matters))

            # Add to weekly posts and update cache
            weekly_posts.append(item)
            cached_articles[entry.link] = True

    # Save updated cache after processing
    save_cache(cached_articles)

    for url in azure_feeds:
        print(f"🔄 Fetching Azure feed: {url}")
        feed = fetch_rss_with_timeout(url)
        if not feed:
            continue
        for entry in feed.entries[:5]:
            # Check if the article is already cached
            if entry.link in cached_articles:
                print(f"📄 Skipping {entry.title} as it was already posted")
                continue

            item = {
                "company": "Azure",
                "title": entry.title,
                "link": entry.link,
                "published": entry.get("published", "N/A"),
                "summary": entry.get("summary", "No summary available"),
                "read_time": estimate_read_time(entry.get("summary", "No summary available"))  # Estimate read time based on summary
            }
            
            # Generate why it matters section
            why_it_matters = generate_why_it_matters(item['summary'])
            
            # Send to Slack with updated message including why it matters
            send_to_slack(format_slack_message(item, why_it_matters))

            # Add to weekly posts and update cache
            weekly_posts.append(item)
            cached_articles[entry.link] = True

    # Save updated cache after processing
    save_cache(cached_articles)

    return aggregated

def send_weekly_digest():
    """Send a weekly summary of posts collected throughout the week"""
    today = datetime.today().date()
    
    # Check if today is Friday
    if today.weekday() == 4:  # 4 is Friday
        if weekly_posts:
            # Format the weekly digest
            digest_message = "*Weekly Digest: This Week in Industry Trends*\n"
            for post in weekly_posts:
                digest_message += format_slack_message(post, generate_why_it_matters(post['summary']))

            # Send the weekly digest to Slack
            send_to_slack(digest_message)
            
            # Clear the list for the next week
            weekly_posts.clear()

# Display results locally too
if __name__ == "__main__":
    fetch_feeds()  # Fetch today's posts
    #send_weekly_digest()  # Send weekly digest on Friday
    #print(f"\n✅ Fetched {len(weekly_posts)} articles this week.")
