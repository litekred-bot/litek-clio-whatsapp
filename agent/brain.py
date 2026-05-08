# agent/brain.py — Cerebro del agente: conexión con Claude API
# Generado por AgentKit para LiTek

import os
import base64
import yaml
import logging
import httpx
from anthropic import AsyncAnthropic
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("agentkit")

client = AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))


def cargar_config_prompts() -> dict:
    """Lee toda la configuración desde config/prompts.yaml."""
    try:
        with open("config/prompts.yaml", "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        logger.error("config/prompts.yaml no encontrado")
        return {}


def cargar_system_prompt() -> str:
    """Lee el system prompt desde config/prompts.yaml."""
    config = cargar_config_prompts()
    return config.get("system_prompt", "Eres Clio, asistente de ventas de LiTek. Responde en español.")


def obtener_mensaje_error() -> str:
    config = cargar_config_prompts()
    return config.get("error_message", "Ops, tuve un problema técnico. ¿Puedes intentarlo en un momento?")


def obtener_mensaje_fallback() -> str:
    config = cargar_config_prompts()
    return config.get("fallback_message", "Mmm, no entendí bien. ¿Me puedes decir qué producto necesitas y las medidas?")


async def descargar_imagen(media_url: str, whapi_token: str) -> tuple[str, str] | None:
    """
    Descarga una imagen de Whapi y la convierte a base64.
    Retorna (base64_data, media_type) o None si falla.
    """
    if not media_url:
        logger.error("URL de imagen vacía — no se puede descargar")
        return None
    try:
        headers = {"Authorization": f"Bearer {whapi_token}"}
        logger.info(f"Descargando imagen desde: {media_url}")
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client_http:
            r = await client_http.get(media_url, headers=headers)
            logger.info(f"Respuesta descarga imagen: {r.status_code} | content-type: {r.headers.get('content-type', 'desconocido')} | tamaño: {len(r.content)} bytes")
            if r.status_code != 200:
                logger.error(f"Error descargando imagen: HTTP {r.status_code} — {r.text[:200]}")
                return None
            # Detectar tipo de imagen
            content_type = r.headers.get("content-type", "image/jpeg")
            media_type = content_type.split(";")[0].strip()
            if media_type not in ("image/jpeg", "image/png", "image/gif", "image/webp"):
                media_type = "image/jpeg"
            imagen_b64 = base64.standard_b64encode(r.content).decode("utf-8")
            logger.info(f"Imagen codificada en base64 ({len(imagen_b64)} chars) tipo: {media_type}")
            return imagen_b64, media_type
    except Exception as e:
        logger.error(f"Excepción descargando imagen: {e}")
        return None


async def generar_respuesta(
    mensaje: str,
    historial: list[dict],
    media_url: str = "",
    whapi_token: str = "",
    tipo: str = "text",
) -> str:
    """
    Genera una respuesta usando Claude API.

    Args:
        mensaje: El mensaje nuevo del usuario (o transcripción de audio)
        historial: Lista de mensajes anteriores
        media_url: URL de imagen (si el mensaje tiene imagen)
        whapi_token: Token de Whapi para descargar la imagen
        tipo: "text" | "image"

    Returns:
        La respuesta generada por Claude
    """
    if not mensaje or len(mensaje.strip()) < 2:
        return obtener_mensaje_fallback()

    system_prompt = cargar_system_prompt()

    # Construir historial
    mensajes = []
    for msg in historial:
        mensajes.append({
            "role": msg["role"],
            "content": msg["content"]
        })

    # Construir el mensaje del usuario (con o sin imagen)
    if tipo == "image" and media_url and whapi_token:
        imagen = await descargar_imagen(media_url, whapi_token)
        if imagen:
            imagen_b64, media_type = imagen
            contenido_usuario = [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": imagen_b64,
                    }
                },
                {
                    "type": "text",
                    "text": mensaje or "¿Qué ves en esta imagen? Ayúdame a cotizarla si es un diseño o arte para imprimir."
                }
            ]
        else:
            # Si falla la descarga, solo texto
            contenido_usuario = mensaje
    else:
        contenido_usuario = mensaje

    mensajes.append({
        "role": "user",
        "content": contenido_usuario
    })

    try:
        response = await client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            system=system_prompt,
            messages=mensajes
        )

        respuesta = response.content[0].text
        logger.info(f"Respuesta generada ({response.usage.input_tokens} in / {response.usage.output_tokens} out)")
        return respuesta

    except Exception as e:
        logger.error(f"Error Claude API: {e}")
        return obtener_mensaje_error()
