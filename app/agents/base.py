"""Base común para los 8 agentes de ViveroOnline.

Todos los agentes heredan de Agent y comparten:
- Contexto del usuario (rol, municipio, vivero_id, etc.)
- Cliente Gemini
- Acceso a Supabase
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional

from app.services.gemini import get_gemini, GeminiService
from app.services.supabase import admin


@dataclass
class AgentContext:
    """Todo lo que un agente necesita saber del usuario que lo invoca."""
    user_id: Optional[str] = None
    whatsapp: Optional[str] = None
    rol: Optional[str] = None
    vivero_id: Optional[int] = None
    cliente_id: Optional[int] = None
    municipio: str = "Cajicá"
    lat: float = 4.9195
    lon: float = -74.0270
    historial: list[dict] = field(default_factory=list)


class Agent:
    """Base class. Cada agente implementa run()."""

    name: str = "base"
    description: str = "agent base class"

    def __init__(self) -> None:
        self.gemini: GeminiService = get_gemini()
        self.db = admin()

    def run(self, mensaje: str, ctx: AgentContext) -> dict:
        """Override en cada agente. Retorna {respuesta, metadata}."""
        raise NotImplementedError
