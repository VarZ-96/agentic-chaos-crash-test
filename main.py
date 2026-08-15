from fastapi import FastAPI
from contextlib import asynccontextmanager

from app.core.config import settings

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup logic
    print("Agentic Chaos Engine starting up...")
    print(f"Target Agent Proxy URL: {settings.chaos_config.target_agent.base_proxy_url if settings.chaos_config else 'Not loaded'}")
    yield
    # Shutdown logic
    print("Agentic Chaos Engine shutting down...")

app = FastAPI(
    title="Agentic Chaos Crash Test Engine",
    description="Asynchronous reverse-proxy fuzzing engine and resiliency evaluation platform.",
    version="1.0.0",
    lifespan=lifespan,
)

@app.get("/health")
async def health_check():
    return {"status": "ok"}
