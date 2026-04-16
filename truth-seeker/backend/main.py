"""
Truth Seeker — Meta Search Engine
FastAPI entry point. Starts the API server and wires up routes + CORS.
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from routes.search import router as search_router

app = FastAPI(
    title="Truth Seeker",
    description="A truth-seeking meta search engine that prioritizes signal over noise.",
    version="1.0.0",
)

# Allow local frontend dev server; tighten in production
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(search_router, prefix="/api")


@app.get("/health")
def health():
    return {"status": "ok", "engine": "truth-seeker v1.0"}
