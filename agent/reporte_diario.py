# agent/reporte_diario.py — Reporte diario de clientes pendientes
# Generado por AgentKit para LiTek

"""
Todos los días a las 6:00 AM envía al grupo "Aletas clio" un reporte
de los clientes que tuvieron actividad en las últimas 24 horas
pero NO confirmaron compra.
"""

import os
import logging
import httpx
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from sqlalchemy import select, distinct
from agent.memory import async_session, Mensaje

logger = logging.getLogger("agentkit")

# Número de Chino (director) — recibe el reporte diario
DESTINATARIO_REPORTE = "5219812710000@s.whatsapp.net"

# Zona horaria de Campeche (CST = UTC-6)
TZ_CAMPECHE = ZoneInfo("America/Merida")

# Palabras clave que indican que SÍ hubo compra confirmada
SEÑALES_COMPRA = [
    "número de folio",
    "folio:",
    "confirmamos tu pago",
    "pago recibido",
    "ya quedó registrado",
    "arrancar tu producción",
    "arrancamos tu pedido",
    "pedido confirmado",
    "recibimos tu pago",
    "tu pedido está en producción",
]

# Palabras clave que indican que el cliente se fue sin interés
SEÑALES_SIN_INTERES = [
    "gracias de todas formas",
    "no por el momento",
    "después lo veo",
    "ahorita no",
    "no gracias",
]


def detectar_estado(mensajes: list[dict]) -> str:
    """
    Analiza los mensajes de una conversación y retorna el estado:
    - 'compro'      → hubo confirmación de pago
    - 'pendiente'   → cotizó o preguntó pero no cerró
    - 'sin_interes' → se fue sin comprar y sin intención
    """
    todo_el_texto = " ".join(
        m["content"].lower() for m in mensajes
    )

    # ¿Hubo folio/confirmación de pago?
    for señal in SEÑALES_COMPRA:
        if señal in todo_el_texto:
            return "compro"

    # ¿Se fue sin interés?
    for señal in SEÑALES_SIN_INTERES:
        if señal in todo_el_texto:
            return "sin_interes"

    # Si hubo intercambio real (más de 2 mensajes) → pendiente
    if len(mensajes) >= 4:
        return "pendiente"

    return "sin_interes"


def extraer_resumen_cliente(mensajes: list[dict]) -> str:
    """Extrae de qué habló el cliente en máximo 2 líneas."""
    # Buscar el primer mensaje del usuario que tenga contenido útil
    for msg in mensajes:
        if msg["role"] == "user":
            texto = msg["content"]
            # Ignorar mensajes genéricos
            if len(texto) > 10 and not any(
                g in texto.lower()
                for g in ["hola", "buenos", "buenas", "hey"]
            ):
                return texto[:120]

    # Si todos son saludos, tomar el último mensaje del usuario
    usuarios = [m for m in mensajes if m["role"] == "user"]
    if usuarios:
        return usuarios[-1]["content"][:120]
    return "Sin detalle"


def formatear_telefono(telefono: str) -> str:
    """Formatea el número para que sea legible."""
    numero = telefono.replace("@s.whatsapp.net", "").replace("@g.us", "")
    if len(numero) == 12 and numero.startswith("52"):
        return f"+{numero[:2]} {numero[2:5]} {numero[5:8]} {numero[8:]}"
    return f"+{numero}"


async def generar_reporte_diario(whapi_token: str) -> bool:
    """
    Consulta la base de datos, identifica clientes pendientes
    de las últimas 24 horas y envía el reporte al grupo.
    """
    ahora = datetime.now(TZ_CAMPECHE)
    hace_24h = ahora - timedelta(hours=24)

    logger.info(f"Generando reporte diario — período: {hace_24h.strftime('%d/%m %H:%M')} a {ahora.strftime('%d/%m %H:%M')}")

    try:
        async with async_session() as session:
            # Obtener todos los teléfonos únicos con actividad en las últimas 24h
            # Solo conversaciones reales (no grupos, no el propio bot)
            resultado = await session.execute(
                select(distinct(Mensaje.telefono), Mensaje.timestamp)
                .where(Mensaje.timestamp >= hace_24h.replace(tzinfo=None))
                .where(~Mensaje.telefono.like("%@g.us"))  # excluir grupos
                .order_by(Mensaje.timestamp.desc())
            )
            telefonos_raw = resultado.fetchall()

        if not telefonos_raw:
            logger.info("Reporte: sin actividad en las últimas 24h")
            return True

        # Teléfonos únicos
        telefonos_unicos = list({row[0] for row in telefonos_raw})

        pendientes = []
        total_compraron = 0

        for telefono in telefonos_unicos:
            # Saltar el número de prueba local
            if "test-local" in telefono:
                continue

            async with async_session() as session:
                resultado = await session.execute(
                    select(Mensaje)
                    .where(Mensaje.telefono == telefono)
                    .where(Mensaje.timestamp >= hace_24h.replace(tzinfo=None))
                    .order_by(Mensaje.timestamp.asc())
                )
                msgs_db = resultado.scalars().all()

            if not msgs_db:
                continue

            mensajes = [{"role": m.role, "content": m.content} for m in msgs_db]
            ultimo_ts = msgs_db[-1].timestamp

            estado = detectar_estado(mensajes)

            if estado == "compro":
                total_compraron += 1
            elif estado == "pendiente":
                resumen = extraer_resumen_cliente(mensajes)
                pendientes.append({
                    "telefono": telefono,
                    "resumen": resumen,
                    "hora": ultimo_ts.strftime("%H:%M"),
                    "mensajes": len(mensajes),
                })

        # Construir mensaje del reporte
        fecha_reporte = ahora.strftime("%d/%m/%Y")
        total_contactos = len(telefonos_unicos) - sum(
            1 for t in telefonos_unicos if "test-local" in t
        )

        if not pendientes and total_compraron == 0:
            mensaje = (
                f"📊 *REPORTE CLIO — {fecha_reporte}*\n\n"
                f"Sin actividad de clientes en las últimas 24 hrs."
            )
        else:
            lineas = [
                f"📊 *REPORTE CLIO — {fecha_reporte}*",
                f"_Actividad de las últimas 24 hrs_\n",
                f"👥 Contactos atendidos: {total_contactos}",
                f"✅ Compraron: {total_compraron}",
                f"⏳ Pendientes de cerrar: {len(pendientes)}",
            ]

            if pendientes:
                lineas.append("\n─────────────────────")
                lineas.append("*PENDIENTES — dar seguimiento:*\n")
                for i, cliente in enumerate(pendientes, 1):
                    numero_display = formatear_telefono(cliente["telefono"])
                    numero_limpio = cliente["telefono"].replace("@s.whatsapp.net", "")
                    lineas.append(
                        f"*{i}.* 📱 {numero_display}\n"
                        f"   💬 _{cliente['resumen']}_\n"
                        f"   ⏰ Último contacto: {cliente['hora']} hrs\n"
                        f"   👉 wa.me/{numero_limpio}"
                    )
            else:
                lineas.append("\n✨ ¡Todos los contactos del día cerraron!")

            mensaje = "\n".join(lineas)

        # Enviar a Chino (director)
        headers = {
            "Authorization": f"Bearer {whapi_token}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(
                "https://gate.whapi.cloud/messages/text",
                json={"to": DESTINATARIO_REPORTE, "body": mensaje},
                headers=headers,
            )
            if r.status_code == 200:
                logger.info(f"Reporte diario enviado: {len(pendientes)} pendientes, {total_compraron} cierres")
                return True
            else:
                logger.error(f"Error enviando reporte: {r.status_code} — {r.text[:100]}")
                return False

    except Exception as e:
        logger.error(f"Error generando reporte diario: {e}")
        return False
