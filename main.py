import os
import asyncio
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from browser_use import Agent, ChatGoogle
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

class TaskRequest(BaseModel):
    instruction: str

@app.get("/")
def home():
    return {"status": "Gemini Browser Agent is Awake"}

@app.post("/run")
async def run_agent(request: TaskRequest):
    try:
        llm = ChatGoogle(model="gemini-2.5-pro")

        final_task = (
            f"{request.instruction} "
            "IMPORTANT: Once you have completed the task or found the answer, "
            "you must use the 'finish' tool to output it clearly."
        )

        agent = Agent(
            task=final_task,
            llm=llm,
        )

        history = await agent.run()

        # Robust result extraction with fallback
        result = history.final_result()
        if not result:
            if history.is_done():
                result = "Agent finished but returned no specific result. Check logs."
            elif history.has_errors():
                result = f"Agent encountered errors: {history.errors()}"
            elif history.steps:
                last_step = history.steps[-1]
                if last_step.model_output:
                    result = str(last_step.model_output)
                else:
                    result = "No result produced."
            else:
                result = "No result produced."

        return {
            "status": "success",
            "result": result
        }

    except Exception as e:
        print(f"Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
