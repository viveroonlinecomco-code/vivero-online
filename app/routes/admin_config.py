"""Configuración admin - ViveroOnline

NOTA: Los endpoints de tickets fueron movidos a admin_tickets.py
Este archivo mantiene el router para compatibilidad con main.py
"""

from fastapi import APIRouter

router = APIRouter(prefix="/api/admin", tags=["admin"])
