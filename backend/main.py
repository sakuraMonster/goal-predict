from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="竞彩足球预测系统", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/api/health")
async def health():
    return {"status": "ok", "version": "0.1.0"}

from app.api import matches, predictions, reports, admin, mappings, teams

app.include_router(matches.router)
app.include_router(predictions.router)
app.include_router(reports.router)
app.include_router(admin.router)
app.include_router(mappings.router)
app.include_router(teams.router)
