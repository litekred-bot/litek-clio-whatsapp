# agent/escalation.py — Sistema de escalación a asesores
# Generado por AgentKit para LiTek

"""
Cuando Clio no puede resolver algo, escala al área correcta.
Envía un resumen por WhatsApp al asesor correspondiente.
"""

import logging
import httpx
import os

logger = logging.getLogger("agentkit")

# Grupo de alertas del equipo LiTek (Aletas clio)
GRUPO_ALERTAS = "120363425558631008@g.us"

# Responsable de cada área (para mencionar en el mensaje del grupo)
AREAS = {
    "director":        {"nombre": "Chino LiTek (Fco Sánchez)"},
    "asesor":          {"nombre": "Anna"},
    "letreros":        {"nombre": "Brayan"},
    "administracion":  {"nombre": "Tere (Administración)"},
}


async def enviar_alerta_asesor(
    area: str,
    telefono_cliente: str,
    resumen: str,
    whapi_token: str,
) -> bool:
    """
    Envía un mensaje de alerta al grupo del equipo LiTek.

    Args:
        area: 'director' | 'asesor' | 'letreros' | 'contabilidad'
        telefono_cliente: Número del cliente (para que el asesor lo contacte)
        resumen: Resumen de lo que necesita el cliente
        whapi_token: Token de Whapi para enviar el mensaje

    Returns:
        True si el mensaje fue enviado exitosamente
    """
    if area not in AREAS:
        logger.warning(f"Área desconocida: {area}")
        return False

    nombre_area = AREAS[area]["nombre"]
    numero_limpio = telefono_cliente.replace('@s.whatsapp.net', '')
    # Números mexicanos en Whapi: 521XXXXXXXXXX (52 + 1 + 10 dígitos)
    # Formato legible: +52 981 105 0955
    if numero_limpio.startswith("521") and len(numero_limpio) == 13:
        # 52 + 1 + área(3) + número(7)
        area_tel = numero_limpio[3:6]
        num_tel = numero_limpio[6:]
        numero_display = f"+52 {area_tel} {num_tel[:3]} {num_tel[3:]}"
    elif numero_limpio.startswith("52") and len(numero_limpio) == 12:
        area_tel = numero_limpio[2:5]
        num_tel = numero_limpio[5:]
        numero_display = f"+52 {area_tel} {num_tel[:3]} {num_tel[3:]}"
    else:
        numero_display = f"+{numero_limpio}"

    mensaje = (
        f"🔔 *ALERTA CLIO — {area.upper()}*\n\n"
        f"👤 *Para:* {nombre_area}\n"
        f"📱 *Cliente:* {numero_display}\n"
        f"📋 *Solicita:* {resumen}\n\n"
        f"👉 *Contactar:* wa.me/{numero_limpio}"
    )

    headers = {
        "Authorization": f"Bearer {whapi_token}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(
                "https://gate.whapi.cloud/messages/text",
                json={"to": GRUPO_ALERTAS, "body": mensaje},
                headers=headers,
            )
            if r.status_code == 200:
                logger.info(f"Alerta enviada al grupo para {nombre_area}: OK")
                return True
            else:
                logger.error(f"Error enviando alerta al grupo: {r.status_code} — {r.text[:100]}")
                return False
    except Exception as e:
        logger.error(f"Excepción enviando alerta: {e}")
        return False
