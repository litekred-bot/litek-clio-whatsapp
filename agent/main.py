# agent/main.py — Servidor FastAPI + Webhook de WhatsApp
# Generado por AgentKit para LiTek

import os
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse
from dotenv import load_dotenv

from agent.brain import generar_respuesta
from agent.memory import inicializar_db, guardar_mensaje, obtener_historial
from agent.providers import obtener_proveedor
from agent.transcription import transcribir_audio
from agent.document_reader import leer_documento
from agent.escalation import enviar_alerta_asesor, AREAS

load_dotenv()

ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
log_level = logging.DEBUG if ENVIRONMENT == "development" else logging.INFO
logging.basicConfig(level=log_level)
logger = logging.getLogger("agentkit")

proveedor = obtener_proveedor()
PORT = int(os.getenv("PORT", 8000))


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Inicializa la base de datos al arrancar el servidor."""
    await inicializar_db()
    logger.info("Base de datos inicializada")
    logger.info(f"Servidor AgentKit — LiTek corriendo en puerto {PORT}")
    logger.info(f"Proveedor de WhatsApp: {proveedor.__class__.__name__}")
    yield


app = FastAPI(
    title="Clio — Agente de Ventas LiTek",
    version="1.0.0",
    lifespan=lifespan
)


@app.get("/")
async def health_check():
    """Endpoint de salud para Railway/monitoreo."""
    return {"status": "ok", "service": "agentkit-litek", "agente": "Clio"}


@app.get("/webhook")
async def webhook_verificacion(request: Request):
    """Verificación GET del webhook (requerido por Meta Cloud API, no-op para Whapi)."""
    resultado = await proveedor.validar_webhook(request)
    if resultado is not None:
        return PlainTextResponse(str(resultado))
    return {"status": "ok"}


@app.post("/webhook")
async def webhook_handler(request: Request):
    """
    Recibe mensajes de WhatsApp via el proveedor configurado.
    Procesa el mensaje, genera respuesta con Claude y la envía de vuelta.
    """
    try:
        mensajes = await proveedor.parsear_webhook(request)

        for msg in mensajes:
            if msg.es_propio:
                continue

            # Si es nota de voz, transcribir primero
            if msg.tipo in ("audio", "voice", "ptt") and msg.media_url:
                logger.info(f"Nota de voz de {msg.telefono} — transcribiendo...")
                msg.texto = await transcribir_audio(msg.media_url, proveedor.token)
                logger.info(f"Transcripción: {msg.texto}")

            # Si es documento, extraer texto
            elif msg.tipo == "document" and msg.media_url:
                logger.info(f"Documento de {msg.telefono} — extrayendo texto...")
                texto_doc = await leer_documento(msg.media_url, proveedor.token, msg.texto)
                msg.texto = texto_doc
                msg.tipo = "text"  # Procesar como texto normal con el contenido extraído

            if not msg.texto:
                continue

            logger.info(f"Mensaje de {msg.telefono} [{msg.tipo}]: {msg.texto}")

            # Obtener historial ANTES de guardar el mensaje actual
            historial = await obtener_historial(msg.telefono)
            es_primer_mensaje = len(historial) == 0

            # Generar respuesta con Claude (con soporte de imagen si aplica)
            respuesta = await generar_respuesta(
                msg.texto,
                historial,
                media_url=msg.media_url,
                whapi_token=proveedor.token,
                tipo=msg.tipo,
            )

            # Guardar usuario y respuesta en memoria
            await guardar_mensaje(msg.telefono, "user", msg.texto)
            await guardar_mensaje(msg.telefono, "assistant", respuesta)

            import re

            # Detectar escalación [ESCALAR:area]
            area_escalar = None
            if "[ESCALAR:" in respuesta:
                match = re.search(r'\[ESCALAR:(\w+)\]', respuesta)
                if match:
                    area_escalar = match.group(1)
                    respuesta = re.sub(r'\[ESCALAR:\w+\]', '', respuesta).strip()

            # Detectar comando de sticker explícito [STICKER:nombre]
            sticker_nombre = None
            if "[STICKER:" in respuesta:
                match = re.search(r'\[STICKER:(\w+)\]', respuesta)
                if match:
                    sticker_nombre = match.group(1)
                    respuesta = re.sub(r'\[STICKER:\w+\]', '', respuesta).strip()

            # Auto-detectar despedida y asignar sticker automáticamente
            if not sticker_nombre and hasattr(proveedor, 'enviar_sticker'):
                FRASES_CIERRE = [
                    "aquí estaremos", "con gusto te cotizamos", "hasta luego",
                    "que tengas", "buen día", "buenas noches", "buenas tardes",
                    "nos vemos", "cuídate", "cualquier duda aquí", "estamos para",
                    "cuando gustes regresa", "con mucho gusto", "fue un placer",
                ]
                resp_lower = respuesta.lower()
                if any(frase in resp_lower for frase in FRASES_CIERRE):
                    sticker_nombre = "logo"

            # Enviar respuesta por WhatsApp
            await proveedor.enviar_mensaje(msg.telefono, respuesta)

            # Enviar sticker si aplica
            if sticker_nombre and hasattr(proveedor, 'enviar_sticker'):
                await proveedor.enviar_sticker(msg.telefono, sticker_nombre)
                logger.info(f"Sticker enviado: {sticker_nombre}")

            # Sticker de bienvenida — primer mensaje del cliente
            elif es_primer_mensaje and hasattr(proveedor, 'enviar_sticker'):
                await proveedor.enviar_sticker(msg.telefono, "calidad")
                logger.info("Sticker de bienvenida enviado: calidad")

            logger.info(f"Respuesta a {msg.telefono}: {respuesta}")

            # Enviar alerta al asesor si hay escalación
            if area_escalar:
                await enviar_alerta_asesor(
                    area=area_escalar,
                    telefono_cliente=msg.telefono,
                    resumen=msg.texto[:200],
                    whapi_token=proveedor.token,
                )
                logger.info(f"Escalación enviada a área: {area_escalar}")

        return {"status": "ok"}

    except Exception as e:
        logger.error(f"Error en webhook: {e}")
        raise HTTPException(status_code=500, detail=str(e))
