# agent/reporte_diario.py — Reporte diario basado en el CRM
# Generado por AgentKit para LiTek

"""
Todos los días envía a Chino (director) un reporte de la actividad de las
últimas 24 horas, SACADO DEL CRM (fuente de verdad). Sin duplicados y siempre
cuadra con el panel.
"""

import logging
import httpx
from datetime import datetime
from zoneinfo import ZoneInfo
from agent.memory import datos_reporte_crm

logger = logging.getLogger("agentkit")

# Número de Chino (director) — recibe el reporte diario
DESTINATARIO_REPORTE = "5219812710000@s.whatsapp.net"

# Zona horaria de Campeche (CST = UTC-6)
TZ_CAMPECHE = ZoneInfo("America/Merida")


def formatear_telefono(telefono: str) -> str:
    """Formatea el número para que sea legible."""
    numero = telefono.replace("@s.whatsapp.net", "").replace("@g.us", "")
    if len(numero) == 13 and numero.startswith("521"):
        return f"+52 {numero[3:6]} {numero[6:9]} {numero[9:]}"
    if len(numero) == 12 and numero.startswith("52"):
        return f"+{numero[:2]} {numero[2:5]} {numero[5:8]} {numero[8:]}"
    return f"+{numero}"


async def generar_reporte_diario(whapi_token: str) -> bool:
    """Genera el reporte diario desde el CRM y lo envía a Chino."""
    ahora = datetime.now(TZ_CAMPECHE)
    fecha_reporte = ahora.strftime("%d/%m/%Y")
    logger.info("Generando reporte diario desde el CRM")

    try:
        datos = await datos_reporte_crm(horas=24)
        pendientes = datos["pendientes"]

        if datos["atendidos"] == 0:
            mensaje = (
                f"📊 *REPORTE CLIO — {fecha_reporte}*\n\n"
                f"✨ Sin actividad de clientes en las últimas 24 hrs."
            )
        else:
            lineas = [
                f"📊 *REPORTE CLIO — {fecha_reporte}*",
                f"_Actividad de las últimas 24 hrs (del CRM)_\n",
                f"👥 Contactos atendidos: {datos['atendidos']}",
                f"✅ Compraron / en proceso: {datos['compraron']}",
                f"⏳ Pendientes de cerrar: {len(pendientes)}",
                f"⚫ No contestaron: {datos['no_contesto']}",
            ]

            if pendientes:
                lineas.append("\n─────────────────────")
                lineas.append("*PENDIENTES — dar seguimiento:*\n")
                for i, c in enumerate(pendientes, 1):
                    numero_display = formatear_telefono(c["telefono"])
                    numero_limpio = c["telefono"].replace("@s.whatsapp.net", "")
                    asesor = f" · 👤 {c['asesor']}" if c["asesor"] and c["asesor"] != "—" else ""
                    lineas.append(
                        f"*{i}.* {c['nombre']}{asesor}\n"
                        f"   📱 {numero_display}\n"
                        f"   💬 _{c['resumen']}_\n"
                        f"   ⏰ Último contacto: {c['hora']} hrs\n"
                        f"   👉 wa.me/{numero_limpio}"
                    )
            else:
                lineas.append("\n✨ ¡Todos los contactos del día cerraron!")

            mensaje = "\n".join(lineas)

        headers = {"Authorization": f"Bearer {whapi_token}", "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(
                "https://gate.whapi.cloud/messages/text",
                json={"to": DESTINATARIO_REPORTE, "body": mensaje},
                headers=headers,
            )
            if r.status_code == 200:
                logger.info(f"Reporte enviado: {len(pendientes)} pendientes, {datos['compraron']} compraron")
                return True
            logger.error(f"Error enviando reporte: {r.status_code} — {r.text[:100]}")
            return False

    except Exception as e:
        logger.error(f"Error generando reporte diario: {e}")
        return False
