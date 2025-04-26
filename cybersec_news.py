import feedparser
import requests
import json
import os
import re
from datetime import datetime
from dotenv import load_dotenv

# Load .env file
load_dotenv()

# Slack Webhook URL from .env
slack_webhook_url = os.getenv("SLACK_CYBERNEWS_WEBHOOK_URL")

# RSS Feeds
rss_feeds = {
    'The Hacker News': 'https://feeds.feedburner.com/TheHackersNews?format=xml',
    'Krebs on Security': 'https://krebsonsecurity.com/feed/',
    'CSO Online': 'https://www.csoonline.com/feed/',
    'Dark Reading': 'https://www.darkreading.com/rss.xml',
    'Bleeping Computer': 'https://www.bleepingcomputer.com/feed/',
    'SecurityWeek': 'https://feeds.securityweek.com/securityweek',
}

# Cache file to avoid duplicates
CACHE_FILE = 'sent_cybersenews.json'
ITEMS_PER_FEED = 3

# Load cache or create a new one
def load_cache():
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, 'r') as f:
            return json.load(f)
    return {}

def save_cache(cache):
    with open(CACHE_FILE, 'w') as f:
        json.dump(cache, f, indent=2)

# Estimate read time in minutes
def estimate_read_time(entry):
    text = entry.get("summary", entry.get("title", ""))
    words = len(re.findall(r'\w+', text))
    minutes = max(1, round(words / 200))
    return f"📖 ~{minutes} min read"

# Send message to Slack using blocks
def send_to_slack(blocks):
    if not slack_webhook_url:
        print("❌ SLACK_WEBHOOK_URL is not set in the .env file.")
        return

    payload = {"blocks": blocks}
    response = requests.post(slack_webhook_url, json=payload)
    if response.status_code != 200:
        print(f"[!] Slack Error {response.status_code}: {response.text}")
    else:
        print("✅ Successfully sent to Slack")

# Format and send updates
def fetch_cyber_news():
    cache = load_cache()
    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "🛡️ Cybersecurity News Updates", "emoji": True}
        },
        {"type": "divider"}
    ]

    for name, url in rss_feeds.items():
        feed = feedparser.parse(url)
        if not feed.entries:
            continue

        new_entries = []
        for entry in feed.entries[:ITEMS_PER_FEED]:
            entry_id = entry.get("id", entry.link)
            if entry_id in cache:
                continue

            title = entry.title
            link = entry.link
            published = datetime(*entry.published_parsed[:6]).strftime("%a, %d %b %Y")
            read_time = estimate_read_time(entry)

            new_entries.append({
                "title": title,
                "link": link,
                "published": published,
                "read_time": read_time,
                "id": entry_id
            })

        if new_entries:
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*{name}*:"
                }
            })

            for entry in new_entries:
                blocks.append({
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"• *<{entry['link']}|{entry['title']}>*\n  🕒 {entry['published']} | {entry['read_time']}"
                    }
                })

            for entry in new_entries:
                cache[entry['id']] = entry['published']

            blocks.append({"type": "divider"})

    if len(blocks) > 2:
        send_to_slack(blocks)
        save_cache(cache)
    else:
        print("🔁 No new updates to send.")

if __name__ == "__main__":
    fetch_cyber_news()
