# agent/providers/whapi.py — Adaptador para Whapi.cloud
# Generado por AgentKit para LiTek

import os
import logging
import httpx
from fastapi import Request
from agent.providers.base import ProveedorWhatsApp, MensajeEntrante

logger = logging.getLogger("agentkit")


class ProveedorWhapi(ProveedorWhatsApp):
    """Proveedor de WhatsApp usando Whapi.cloud (REST API simple)."""

    def __init__(self):
        self.token = os.getenv("WHAPI_TOKEN")
        self.url_envio = "https://gate.whapi.cloud/messages/text"

    async def parsear_webhook(self, request: Request) -> list[MensajeEntrante]:
        """Parsea el payload de Whapi.cloud — soporta texto, audio e imagen."""
        body = await request.json()
        mensajes = []
        for msg in body.get("messages", []):
            tipo_msg = msg.get("type", "text")
            telefono = msg.get("chat_id", "")
            mensaje_id = msg.get("id", "")
            es_propio = msg.get("from_me", False)

            if tipo_msg == "text":
                # Mensaje de texto normal
                mensajes.append(MensajeEntrante(
                    telefono=telefono,
                    texto=msg.get("text", {}).get("body", ""),
                    mensaje_id=mensaje_id,
                    es_propio=es_propio,
                    tipo="text",
                ))

            elif tipo_msg in ("audio", "voice", "ptt"):
                # Nota de voz — extraer URL del media
                audio_data = msg.get("audio") or msg.get("voice") or msg.get("ptt") or {}
                media_url = audio_data.get("link", "")
                if not media_url:
                    # Intentar construir URL desde el ID del media
                    media_id = audio_data.get("id", "")
                    media_url = f"https://gate.whapi.cloud/media/{media_id}"

                mensajes.append(MensajeEntrante(
                    telefono=telefono,
                    texto="",  # Se llenará con la transcripción en main.py
                    mensaje_id=mensaje_id,
                    es_propio=es_propio,
                    tipo="audio",
                    media_url=media_url,
                ))

            elif tipo_msg == "image":
                # Imagen — extraer URL
                imagen_data = msg.get("image", {})
                media_url = imagen_data.get("link", "")
                caption = imagen_data.get("caption", "")
                # Log para diagnóstico
                logger.info(f"Imagen recibida — URL: {media_url!r} | caption: {caption!r} | datos: {imagen_data}")
                # Si no hay link, construir URL con el ID del media
                if not media_url:
                    media_id = imagen_data.get("id", "")
                    if media_id:
                        media_url = f"https://gate.whapi.cloud/media/{media_id}"
                        logger.info(f"URL construida desde ID: {media_url}")
                mensajes.append(MensajeEntrante(
                    telefono=telefono,
                    texto=caption or "[El cliente envió una imagen]",
                    mensaje_id=mensaje_id,
                    es_propio=es_propio,
                    tipo="image",
                    media_url=media_url,
                ))

        return mensajes

    async def enviar_mensaje(self, telefono: str, mensaje: str) -> bool:
        """Envía mensaje via Whapi.cloud."""
        if not self.token:
            logger.warning("WHAPI_TOKEN no configurado — mensaje no enviado")
            return False
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient() as client:
            r = await client.post(
                self.url_envio,
                json={"to": telefono, "body": mensaje},
                headers=headers,
            )
            if r.status_code != 200:
                logger.error(f"Error Whapi: {r.status_code} — {r.text}")
            return r.status_code == 200
