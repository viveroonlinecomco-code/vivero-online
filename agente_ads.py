import os
import json
from google import genai
from google.genai import types
from pydantic import BaseModel, Field
from dotenv import load_dotenv

load_dotenv()

# Estructura estricta para garantizar que la IA no rompa las reglas de Google Ads
class CampanaGoogleAds(BaseModel):
    keywords_positivas: list[str] = Field(description="Lista de 10-15 palabras clave de alta intención de compra.")
    keywords_negativas: list[str] = Field(description="Lista de 5-10 exclusiones críticas para evitar tráfico basura o cruces.")
    titulos: list[str] = Field(description="Mínimo 5 opciones de títulos persuasivos. Máximo 30 caracteres por título.")
    descripciones: list[str] = Field(description="Mínimo 3 opciones de descripciones. Máximo 90 caracteres por descripción.")

def generar_y_guardar_pauta(objetivo: str, modo: str, supabase_client) -> bool:
    """
    Genera la estrategia de Google Ads usando Gemini y la persiste en Supabase
    para que el agente SEO pueda alimentarse de ella.
    """
    if not supabase_client:
        return False

    client_ai = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
    
    # Contexto dinámico según el discurso de marca
    if modo == "B2B":
        contexto_marca = "Enfoque institucional B2B (constructoras, paisajistas). Tono profesional, enfocado en volumen, logística y rentabilidad en la Sabana de Bogotá."
    else:
        contexto_marca = "Enfoque minorista B2C (consumidor final, venta de materas y decoración). Tono cercano, emocional, enfocado en diseño y estética para el hogar."

    prompt = f"""
    Eres el Director de Performance y Growth Marketing de ViveroOnline. 
    Tu tarea es diseñar una campaña de Google Ads optimizada para el siguiente objetivo:
    "{objetivo}"

    Contexto operativo: {contexto_marca}

    Reglas estrictas de validación:
    1. Ningún título puede superar los 30 caracteres.
    2. Ninguna descripción puede superar los 90 caracteres.
    3. Las keywords negativas deben bloquear activamente el mercado contrario (si es B2B, bloquea palabras minoristas; si es B2C, bloquea palabras corporativas).
    """

    try:
        # Uso de Gemini con respuesta estructurada obligatoria (Pydantic)
        response = client_ai.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=CampanaGoogleAds,
                temperature=0.3
            ),
        )
        
        # Parsear la respuesta estructurada de la IA
        datos_campana = json.loads(response.text)
        
        # Preparar el registro para Supabase
        registro = {
            "modo": modo,
            "keyword_objetivo": objetivo,
            "keywords_positivas": datos_campana["keywords_positivas"],
            "keywords_negativas": datos_campana["keywords_negativas"],
            "titulos_anuncio": datos_campana["titulos"],
            "descripciones_anuncio": datos_campana["descripciones"],
            "estado": "pendiente_uso_seo"
        }
        
        # Insertar en la base de datos de la Agencia
        supabase_client.table("insights_pauta").insert(registro).execute()
        return True

    except Exception as e:
        print(f"Error en agente_ads: {e}")
        return False
