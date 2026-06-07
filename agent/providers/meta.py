# agent/providers/meta.py — Adaptador para Meta WhatsApp Cloud API
# Generado por AgentKit para LiTek
#
# Paridad completa con whapi.py: texto, imagen, audio, documento, sticker,
# nombre de perfil, envío de imágenes/documentos por URL o por media_id,
# y stickers. Maneja la descarga de media (Cloud API usa media_id, no URL directa).

import os
import logging
import httpx
from fastapi import Request
from agent.providers.base import ProveedorWhatsApp, MensajeEntrante

logger = logging.getLogger("agentkit")


class ProveedorMeta(ProveedorWhatsApp):
    """Proveedor de WhatsApp usando la API oficial de Meta (Cloud API)."""

    # URLs de stickers en GitHub (raw) — Meta acepta envío por link público
    STICKERS = {
        "calidad": "https://raw.githubusercontent.com/litekred-bot/litek-clio-whatsapp/main/knowledge/sticker_calidad.webp",
        "logo":    "https://raw.githubusercontent.com/litekred-bot/litek-clio-whatsapp/main/knowledge/sticker_logo.webp",
        "lona":    "https://raw.githubusercontent.com/litekred-bot/litek-clio-whatsapp/main/knowledge/sticker_lona.webp",
    }

    BASE = "https://raw.githubusercontent.com/litekred-bot/litek-clio-whatsapp/main/knowledge/"
    IMAGENES = {
        "vinil":              BASE + "img_vinil.JPG",
        "vinil2":             BASE + "img_vinil2.jpg",
        "vinil3":             BASE + "img_vinil3.jpg",
        "vinilsobrecoroplas": BASE + "img_vinilsobrecoroplas.JPG",
        "vinilsobrepvc":      BASE + "img_vinilsobrepvc.JPG",
        "lonas":              BASE + "img_lonas.JPG",
        "etiquetas":          BASE + "img_etiquetas.JPG",
        "etiquetasconsuaje":  BASE + "img_etiquetasconsuaje.JPG",
        "letras3d":           BASE + "img_letras3d.JPG",
        "sellos":             BASE + "img_sellos.JPG",
        "tarjetas":           BASE + "img_tarjetas.JPG",
        "tazas":              BASE + "img_tazas.JPG",
        "grabadolaser":       BASE + "img_grabadolaser.JPG",
        "rotulado":           BASE + "img_rotuladodevehiculo.JPG",
        "laser":              BASE + "img_laser.jpg",
        "playeras":           BASE + "img_playeras.jpg",
        "cortedevinil":       BASE + "img_cortedevinil.jpg",
        "offset":             BASE + "img_offset.jpg",
        "asesor_ana":         BASE + "asesor_ana.JPG",
        "asesor_brayan":      BASE + "asesor_brayan.JPG",
        "asesor_tere":        BASE + "asesor_tere.JPG",
        "catalogo":           BASE + "img_lonas.JPG",
    }

    def __init__(self):
        self.access_token = os.getenv("META_ACCESS_TOKEN")
        self.phone_number_id = os.getenv("META_PHONE_NUMBER_ID")
        self.verify_token = os.getenv("META_VERIFY_TOKEN", "agentkit-verify")
        self.api_version = "v21.0"
        # Compatibilidad con main.py que usa proveedor.token
        self.token = self.access_token
        self.base_url = f"https://graph.facebook.com/{self.api_version}"

    # ──────────────────────────────────────────────────────────────────
    # Verificación del webhook (GET)
    # ──────────────────────────────────────────────────────────────────
    async def validar_webhook(self, request: Request):
        """Meta requiere verificación GET con hub.verify_token."""
        params = request.query_params
        mode = params.get("hub.mode")
        token = params.get("hub.verify_token")
        challenge = params.get("hub.challenge")
        if mode == "subscribe" and token == self.verify_token:
            logger.info(f"Webhook Meta verificado. Challenge: {challenge}")
            return challenge
        logger.warning(f"Verificación webhook fallida. mode={mode}, token={token}")
        return None

    # ──────────────────────────────────────────────────────────────────
    # Resolver media_id → URL temporal de descarga
    # ──────────────────────────────────────────────────────────────────
    async def _resolver_media_url(self, media_id: str) -> str:
        """Obtiene la URL temporal de descarga de un media de Meta a partir de su ID."""
        if not media_id or not self.access_token:
            return ""
        try:
            headers = {"Authorization": f"Bearer {self.access_token}"}
            async with httpx.AsyncClient(timeout=20.0) as client:
                r = await client.get(f"{self.base_url}/{media_id}", headers=headers)
                if r.status_code == 200:
                    return r.json().get("url", "")
                logger.error(f"Error resolviendo media {media_id}: {r.status_code} — {r.text[:120]}")
        except Exception as e:
            logger.error(f"Excepción resolviendo media {media_id}: {e}")
        return ""

    # ──────────────────────────────────────────────────────────────────
    # Parseo del webhook entrante
    # ──────────────────────────────────────────────────────────────────
    async def parsear_webhook(self, request: Request) -> list[MensajeEntrante]:
        """Parsea el payload anidado de Meta Cloud API (texto, imagen, audio, doc, sticker)."""
        try:
            body = await request.json()
        except Exception:
            return []

        logger.info(f"Webhook Meta payload: {body}")
        mensajes: list[MensajeEntrante] = []

        for entry in body.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})

                # Mapear push name por número (viene en contacts[])
                nombres = {}
                for c in value.get("contacts", []):
                    wa_id = c.get("wa_id", "")
                    nombre = c.get("profile", {}).get("name", "")
                    if wa_id:
                        nombres[wa_id] = nombre

                for msg in value.get("messages", []):
                    telefono = msg.get("from", "")
                    mensaje_id = msg.get("id", "")
                    tipo = msg.get("type", "text")
                    nombre_perfil = nombres.get(telefono, "")

                    # ── Texto ──
                    if tipo == "text":
                        mensajes.append(MensajeEntrante(
                            telefono=telefono,
                            texto=msg.get("text", {}).get("body", ""),
                            mensaje_id=mensaje_id,
                            es_propio=False,
                            tipo="text",
                            nombre_perfil=nombre_perfil,
                        ))

                    # ── Imagen ──
                    elif tipo == "image":
                        img = msg.get("image", {})
                        media_id = img.get("id", "")
                        media_url = await self._resolver_media_url(media_id)
                        mensajes.append(MensajeEntrante(
                            telefono=telefono,
                            texto=img.get("caption", "") or "[El cliente envió una imagen]",
                            mensaje_id=mensaje_id,
                            es_propio=False,
                            tipo="image",
                            media_url=media_url,
                            media_id=media_id,
                            nombre_perfil=nombre_perfil,
                        ))

                    # ── Audio / nota de voz ──
                    elif tipo == "audio":
                        aud = msg.get("audio", {})
                        media_id = aud.get("id", "")
                        media_url = await self._resolver_media_url(media_id)
                        mensajes.append(MensajeEntrante(
                            telefono=telefono,
                            texto="",  # se llena con transcripción en main.py
                            mensaje_id=mensaje_id,
                            es_propio=False,
                            tipo="audio",
                            media_url=media_url,
                            media_id=media_id,
                            nombre_perfil=nombre_perfil,
                        ))

                    # ── Documento ──
                    elif tipo == "document":
                        doc = msg.get("document", {})
                        media_id = doc.get("id", "")
                        filename = doc.get("filename", "documento")
                        media_url = await self._resolver_media_url(media_id)
                        mensajes.append(MensajeEntrante(
                            telefono=telefono,
                            texto=doc.get("caption", "") or f"[El cliente envió un documento: {filename}]",
                            mensaje_id=mensaje_id,
                            es_propio=False,
                            tipo="document",
                            media_url=media_url,
                            media_id=media_id,
                            nombre_perfil=nombre_perfil,
                        ))

                    # ── Sticker ──
                    elif tipo == "sticker":
                        mensajes.append(MensajeEntrante(
                            telefono=telefono,
                            texto="[El cliente envió un sticker]",
                            mensaje_id=mensaje_id,
                            es_propio=False,
                            tipo="sticker",
                            nombre_perfil=nombre_perfil,
                        ))

                    else:
                        logger.info(f"Mensaje Meta tipo '{tipo}' no manejado")

        return mensajes

    # ──────────────────────────────────────────────────────────────────
    # Envío de mensajes
    # ──────────────────────────────────────────────────────────────────
    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

    async def _post(self, payload: dict) -> bool:
        """POST genérico al endpoint de mensajes."""
        if not self.access_token or not self.phone_number_id:
            logger.warning("META_ACCESS_TOKEN o META_PHONE_NUMBER_ID no configurados")
            return False
        url = f"{self.base_url}/{self.phone_number_id}/messages"
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.post(url, json=payload, headers=self._headers())
            if r.status_code != 200:
                logger.error(f"Error Meta API: {r.status_code} — {r.text[:200]}")
            return r.status_code == 200

    async def enviar_mensaje(self, telefono: str, mensaje: str) -> bool:
        """Envía un mensaje de texto."""
        return await self._post({
            "messaging_product": "whatsapp",
            "to": telefono,
            "type": "text",
            "text": {"body": mensaje},
        })

    async def enviar_imagen(self, telefono: str, nombre: str) -> bool:
        """Envía una imagen de catálogo (por link público de GitHub)."""
        url_imagen = self.IMAGENES.get(nombre)
        if not url_imagen:
            logger.warning(f"Imagen no encontrada: {nombre}")
            return False
        return await self._post({
            "messaging_product": "whatsapp",
            "to": telefono,
            "type": "image",
            "image": {"link": url_imagen},
        })

    async def enviar_imagen_url(self, telefono: str, url_imagen: str, caption: str = "") -> bool:
        """Reenvía una imagen por su URL pública (ej. catálogo)."""
        if not url_imagen:
            return False
        img = {"link": url_imagen}
        if caption:
            img["caption"] = caption
        return await self._post({
            "messaging_product": "whatsapp", "to": telefono, "type": "image", "image": img,
        })

    async def enviar_imagen_id(self, telefono: str, media_id: str, caption: str = "") -> bool:
        """Reenvía una imagen recibida usando su media_id de Meta (ej. comprobante/diseño)."""
        if not media_id:
            return False
        img = {"id": media_id}
        if caption:
            img["caption"] = caption
        return await self._post({
            "messaging_product": "whatsapp", "to": telefono, "type": "image", "image": img,
        })

    async def enviar_documento_url(self, telefono: str, url_doc: str, caption: str = "") -> bool:
        """Reenvía un documento por su URL pública."""
        if not url_doc:
            return False
        doc = {"link": url_doc}
        if caption:
            doc["caption"] = caption
        return await self._post({
            "messaging_product": "whatsapp", "to": telefono, "type": "document", "document": doc,
        })

    async def enviar_documento_id(self, telefono: str, media_id: str, caption: str = "") -> bool:
        """Reenvía un documento recibido usando su media_id de Meta."""
        if not media_id:
            return False
        doc = {"id": media_id}
        if caption:
            doc["caption"] = caption
        return await self._post({
            "messaging_product": "whatsapp", "to": telefono, "type": "document", "document": doc,
        })

    async def enviar_sticker(self, telefono: str, nombre: str = "calidad") -> bool:
        """Envía un sticker de LiTek (por link público .webp)."""
        url_sticker = self.STICKERS.get(nombre, self.STICKERS["calidad"])
        return await self._post({
            "messaging_product": "whatsapp",
            "to": telefono,
            "type": "sticker",
            "sticker": {"link": url_sticker},
        })
