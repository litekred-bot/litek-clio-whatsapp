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

    # URLs de stickers en GitHub (raw)
    STICKERS = {
        "calidad": "https://raw.githubusercontent.com/litekred-bot/litek-clio-whatsapp/main/knowledge/sticker_calidad.webp",
        "logo":    "https://raw.githubusercontent.com/litekred-bot/litek-clio-whatsapp/main/knowledge/sticker_logo.webp",
        "lona":    "https://raw.githubusercontent.com/litekred-bot/litek-clio-whatsapp/main/knowledge/sticker_lona.webp",
    }

    def __init__(self):
        self.token = os.getenv("WHAPI_TOKEN")
        self.url_envio = "https://gate.whapi.cloud/messages/text"
        self.url_sticker = "https://gate.whapi.cloud/messages/sticker"

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

            elif tipo_msg == "document":
                # Documento (PDF, Word, etc.)
                doc_data = msg.get("document", {})
                media_url = doc_data.get("link", "")
                filename = doc_data.get("filename", "documento")
                mime_type = doc_data.get("mime_type", "")
                caption = doc_data.get("caption", "")
                logger.info(f"Documento recibido — archivo: {filename} | URL: {media_url!r} | tipo: {mime_type}")
                if not media_url:
                    media_id = doc_data.get("id", "")
                    if media_id:
                        media_url = f"https://gate.whapi.cloud/media/{media_id}"
                mensajes.append(MensajeEntrante(
                    telefono=telefono,
                    texto=caption or f"[El cliente envió un documento: {filename}]",
                    mensaje_id=mensaje_id,
                    es_propio=es_propio,
                    tipo="document",
                    media_url=media_url,
                ))

            elif tipo_msg == "sticker":
                # Sticker — responder amigablemente
                mensajes.append(MensajeEntrante(
                    telefono=telefono,
                    texto="[El cliente envió un sticker]",
                    mensaje_id=mensaje_id,
                    es_propio=es_propio,
                    tipo="sticker",
                ))

        return mensajes

    async def enviar_sticker(self, telefono: str, nombre: str = "calidad") -> bool:
        """Envía un sticker de LiTek. nombre: 'calidad' | 'logo' | 'lona'"""
        if not self.token:
            return False
        url_sticker = self.STICKERS.get(nombre, self.STICKERS["calidad"])
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient() as client:
            r = await client.post(
                self.url_sticker,
                json={"to": telefono, "sticker": {"url": url_sticker}},
                headers=headers,
            )
            logger.info(f"Sticker '{nombre}' a {telefono}: {r.status_code}")
            return r.status_code == 200

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
