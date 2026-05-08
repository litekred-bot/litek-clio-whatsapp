# agent/transcription.py — Transcripción de notas de voz con OpenAI Whisper
# Generado por AgentKit para LiTek

"""
Convierte notas de voz de WhatsApp a texto usando OpenAI Whisper.
Se activa automáticamente cuando un cliente manda un audio.
"""

import os
import logging
import tempfile
import httpx
from openai import AsyncOpenAI

logger = logging.getLogger("agentkit")

# Cliente de OpenAI para Whisper
openai_client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))


async def transcribir_audio(media_url: str, whapi_token: str) -> str:
    """
    Descarga un audio de Whapi y lo transcribe con OpenAI Whisper.

    Args:
        media_url: URL del archivo de audio en Whapi
        whapi_token: Token de Whapi para autenticar la descarga

    Returns:
        Texto transcrito del audio, o mensaje de error
    """
    if not os.getenv("OPENAI_API_KEY"):
        logger.warning("OPENAI_API_KEY no configurado — no se puede transcribir")
        return "[Nota de voz recibida — transcripción no disponible]"

    try:
        # Descargar el audio desde Whapi
        headers = {"Authorization": f"Bearer {whapi_token}"}
        async with httpx.AsyncClient(timeout=30.0) as client:
            respuesta = await client.get(media_url, headers=headers)
            if respuesta.status_code != 200:
                logger.error(f"Error descargando audio: {respuesta.status_code}")
                return "[No pude escuchar la nota de voz. ¿Puedes escribirlo?]"
            audio_bytes = respuesta.content

        # Guardar en archivo temporal y transcribir
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
            tmp.write(audio_bytes)
            tmp_path = tmp.name

        try:
            with open(tmp_path, "rb") as audio_file:
                resultado = await openai_client.audio.transcriptions.create(
                    model="whisper-1",
                    file=audio_file,
                    language="es",  # Español
                )
            transcripcion = resultado.text
            logger.info(f"Audio transcrito: {transcripcion[:80]}...")
            return transcripcion

        finally:
            # Limpiar archivo temporal
            os.unlink(tmp_path)

    except Exception as e:
        logger.error(f"Error transcribiendo audio: {e}")
        return "[No pude procesar la nota de voz. ¿Puedes escribirlo?]"
