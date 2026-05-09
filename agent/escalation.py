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

# Números de cada área (formato internacional sin +)
AREAS = {
    "director":      {"numero": "529812710000",  "nombre": "Director"},
    "asesor":        {"numero": "529818290272",  "nombre": "Asesor General"},
    "letreros":      {"numero": "529811670283",  "nombre": "Asesor Letreros/Láser/Offset/Róuter"},
    "contabilidad":  {"numero": "529811388508",  "nombre": "Contabilidad"},
}


async def enviar_alerta_asesor(
    area: str,
    telefono_cliente: str,
    resumen: str,
    whapi_token: str,
) -> bool:
    """
    Envía un mensaje de alerta al asesor del área indicada.

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

    area_info = AREAS[area]
    numero_asesor = area_info["numero"]
    nombre_area = area_info["nombre"]

    # Limpiar número del cliente para mostrar
    numero_display = telefono_cliente.replace("@s.whatsapp.net", "").replace("529", "+52 9")

    mensaje = (
        f"🔔 *LEAD NUEVO — {nombre_area}*\n\n"
        f"📱 Cliente: +{telefono_cliente.replace('@s.whatsapp.net', '')}\n"
        f"📋 Solicita: {resumen}\n\n"
        f"👉 Responder: wa.me/{telefono_cliente.replace('@s.whatsapp.net', '')}"
    )

    headers = {
        "Authorization": f"Bearer {whapi_token}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(
                "https://gate.whapi.cloud/messages/text",
                json={"to": numero_asesor, "body": mensaje},
                headers=headers,
            )
            if r.status_code == 200:
                logger.info(f"Alerta enviada a {nombre_area} ({numero_asesor}): OK")
                return True
            else:
                logger.error(f"Error enviando alerta a {nombre_area}: {r.status_code} — {r.text[:100]}")
                return False
    except Exception as e:
        logger.error(f"Excepción enviando alerta: {e}")
        return False
