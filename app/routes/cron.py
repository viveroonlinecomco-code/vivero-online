"""Entry point para Vercel Serverless Python."""
from app.main import app
from routes.cron import router as cron_router

app.include_router(cron_router)

handler = app
