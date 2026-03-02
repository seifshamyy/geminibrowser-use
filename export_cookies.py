"""
Run this script ONCE on your local machine to export your browser session.
It opens a real browser window — log in manually, then press Enter.
The session (cookies + localStorage) is saved to storage_state.json.

Usage:
    pip install playwright
    playwright install chromium
    python export_cookies.py
    
Then POST the file contents to your deployed API:
    curl -X POST https://<your-url>/set-cookies \
      -H "Content-Type: application/json" \
      -d @storage_state_wrapped.json
"""

import json
from playwright.sync_api import sync_playwright

TARGET_URL = "https://your-target-site.com"   # <-- change this

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()
    page.goto(TARGET_URL)
    input(f"\nLog in to {TARGET_URL} in the browser window, then press Enter here...\n")
    state = context.storage_state()
    browser.close()

# Save raw state
with open("storage_state.json", "w") as f:
    json.dump(state, f, indent=2)

# Also save the wrapped version ready to POST to /set-cookies
wrapped = {"storage_state": state}
with open("storage_state_wrapped.json", "w") as f:
    json.dump(wrapped, f, indent=2)

print(f"\nDone! Exported {len(state.get('cookies', []))} cookies.")
print("To upload to your API run:")
print('  curl -X POST https://<your-url>/set-cookies -H "Content-Type: application/json" -d @storage_state_wrapped.json')
