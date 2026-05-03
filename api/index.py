"""Entry point para Vercel Serverless Python.

Vercel detecta automáticamente la variable `app` de este archivo
y la expone como función serverless.
"""
from app.main import app

# Vercel espera la variable llamada `app` o `handler`
handler = app
