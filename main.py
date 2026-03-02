import os
import json
import asyncio
from datetime import datetime
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from browser_use import Agent, ChatGoogle, Browser, Controller
from browser_use.browser.browser import Browser
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

COOKIES_FILE = "/data/storage_state.json"

# ── Image extraction custom tool ──────────────────────────────────────────────

controller = Controller()

_JS_EXTRACT_IMAGES = """
() => {
    const seen = new Set();
    const results = [];

    const add = (url) => {
        if (!url || seen.has(url)) return;
        if (url.startsWith('data:')) return;          // skip base64
        if (url.startsWith('blob:')) return;          // skip blobs
        seen.add(url);
        results.push(url);
    };

    // 1. Standard <img> — src, currentSrc, data-src variants
    document.querySelectorAll('img').forEach(img => {
        add(img.currentSrc || img.src);
        ['data-src','data-lazy','data-original','data-lazy-src',
         'data-hi-res-src','data-full-src'].forEach(attr => {
            if (img.dataset) add(img.getAttribute(attr));
        });
        // Pick highest-res from srcset
        if (img.srcset) {
            const best = img.srcset.split(',').map(s => {
                const [url, w] = s.trim().split(/\\s+/);
                return { url, w: parseFloat(w) || 0 };
            }).sort((a, b) => b.w - a.w)[0];
            if (best) add(best.url);
        }
    });

    // 2. CSS background-image on any element
    document.querySelectorAll('*').forEach(el => {
        const bg = getComputedStyle(el).backgroundImage;
        const match = bg && bg.match(/url\\(["']?([^"')]+)["']?\\)/);
        if (match) add(match[1]);
    });

    // 3. Filter tiny tracking pixels (natural size < 10px)
    return results.filter(u => u && u.startsWith('http'));
}
"""

@controller.action("Get all image URLs on the current page")
async def get_image_urls(browser: Browser):
    """Extracts every image URL via JavaScript — no right-clicking needed."""
    page = await browser.get_current_page()
    urls = await page.evaluate(_JS_EXTRACT_IMAGES)
    return {"image_urls": urls, "count": len(urls)}

# ── Models ────────────────────────────────────────────────────────────────────

class TaskRequest(BaseModel):
    instruction: str

class CookiesRequest(BaseModel):
    storage_state: dict  # Playwright storage_state format: {cookies: [...], origins: [...]}

class RawCookiesRequest(BaseModel):
    cookies: list  # Chrome extension format (Cookie-Editor, EditThisCookie, etc.)

# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/")
def home():
    return {"status": "Gemini Browser Agent is Awake"}


def _convert_samesite(value):
    """Map Chrome extension sameSite strings to Playwright format."""
    mapping = {"no_restriction": "None", "lax": "Lax", "strict": "Strict"}
    if value is None:
        return "None"
    return mapping.get(value.lower(), "None")


@app.post("/set-cookies")
def set_cookies(request: CookiesRequest):
    """Upload session in Playwright storage_state format: {cookies: [...], origins: [...]}"""
    os.makedirs(os.path.dirname(COOKIES_FILE), exist_ok=True)
    with open(COOKIES_FILE, "w") as f:
        json.dump(request.storage_state, f, indent=2)

    cookie_count = len(request.storage_state.get("cookies", []))
    return {
        "status": "success",
        "message": f"Stored {cookie_count} cookies. They will be injected into every agent run."
    }


@app.post("/set-cookies-raw")
def set_cookies_raw(request: RawCookiesRequest):
    """
    Upload cookies in Chrome Extension format (Cookie-Editor, EditThisCookie, etc.).
    Auto-converts to Playwright storage_state — paste the exported array directly.
    """
    playwright_cookies = [
        {
            "name": c["name"],
            "value": c["value"],
            "domain": c["domain"],
            "path": c.get("path", "/"),
            "expires": c.get("expirationDate", -1) or -1,
            "httpOnly": c.get("httpOnly", False),
            "secure": c.get("secure", False),
            "sameSite": _convert_samesite(c.get("sameSite")),
        }
        for c in request.cookies
    ]

    storage_state = {"cookies": playwright_cookies, "origins": []}
    os.makedirs(os.path.dirname(COOKIES_FILE), exist_ok=True)
    with open(COOKIES_FILE, "w") as f:
        json.dump(storage_state, f, indent=2)

    return {
        "status": "success",
        "message": f"Converted and stored {len(playwright_cookies)} cookies."
    }


@app.get("/cookies-status")
def cookies_status():
    """Check whether cookies are loaded and show a summary."""
    if not os.path.exists(COOKIES_FILE):
        return {"loaded": False, "message": "No cookies found. POST to /set-cookies to upload."}

    with open(COOKIES_FILE, "r") as f:
        state = json.load(f)

    cookies = state.get("cookies", [])
    # Find the soonest expiry
    expires = [c.get("expires", -1) for c in cookies if c.get("expires", -1) > 0]
    soonest = min(expires) if expires else None
    soonest_str = datetime.utcfromtimestamp(soonest).isoformat() + "Z" if soonest else "session cookie (no expiry)"

    return {
        "loaded": True,
        "cookie_count": len(cookies),
        "soonest_expiry": soonest_str,
        "domains": list({c.get("domain", "") for c in cookies}),
    }


@app.delete("/cookies")
def delete_cookies():
    """Clear stored cookies."""
    if os.path.exists(COOKIES_FILE):
        os.remove(COOKIES_FILE)
    return {"status": "success", "message": "Cookies cleared."}


@app.post("/run")
async def run_agent(request: TaskRequest):
    try:
        llm = ChatGoogle(model="gemini-2.5-pro")

        # Cloud stealth browser — residential IP, no automation fingerprint
        browser_kwargs = {"use_cloud": True}
        if os.path.exists(COOKIES_FILE):
            browser_kwargs["storage_state"] = COOKIES_FILE

        browser = Browser(**browser_kwargs)

        final_task = (
            f"{request.instruction} "
            "IMPORTANT: Once you have completed the task or found the answer, "
            "you must use the 'finish' tool to output it clearly."
        )

        agent = Agent(
            task=final_task,
            llm=llm,
            browser=browser,
            controller=controller,
            max_steps=int(os.getenv("MAX_AGENT_STEPS", "20")),
        )

        history = await agent.run()

        result = history.final_result()
        if not result:
            if history.is_done():
                result = "Agent finished but returned no specific result. Check logs."
            elif history.has_errors():
                result = f"Agent encountered errors: {history.errors()}"
            elif history.steps:
                last_step = history.steps[-1]
                result = str(last_step.model_output) if last_step.model_output else "No result produced."
            else:
                result = "No result produced."

        return {"status": "success", "result": result}

    except Exception as e:
        print(f"Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
