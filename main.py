import os
import json
import asyncio
from datetime import datetime
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from browser_use import Agent, ChatGoogle, Browser
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

COOKIES_FILE = "/data/storage_state.json"

# ── Models ────────────────────────────────────────────────────────────────────

class TaskRequest(BaseModel):
    instruction: str

class CookiesRequest(BaseModel):
    storage_state: dict  # Playwright storage_state format: {cookies: [...], origins: [...]}

# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/")
def home():
    return {"status": "Gemini Browser Agent is Awake"}


@app.post("/set-cookies")
def set_cookies(request: CookiesRequest):
    """
    Upload your browser session (cookies + localStorage) in Playwright storage_state format.
    Export from Chrome using Cookie-Editor extension or the export script in export_cookies.py.
    """
    os.makedirs(os.path.dirname(COOKIES_FILE), exist_ok=True)
    with open(COOKIES_FILE, "w") as f:
        json.dump(request.storage_state, f, indent=2)

    cookie_count = len(request.storage_state.get("cookies", []))
    return {
        "status": "success",
        "message": f"Stored {cookie_count} cookies. They will be injected into every agent run."
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

        # Inject cookies if available
        browser_kwargs = {}
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
