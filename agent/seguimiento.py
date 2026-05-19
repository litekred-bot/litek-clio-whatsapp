# agent/seguimiento.py — Seguimiento automático de clientes inactivos
# Generado por AgentKit para LiTek

"""
Envía un mensaje cálido a clientes que no han respondido en más de 1 hora.
Reglas:
- Solo si el último mensaje fue de Clio (assistant) — el cliente no respondió
- Solo si la conversación tuvo al menos 2 mensajes (interacción real)
- Solo se envía UNA vez por conversación inactiva (no spam)
- Solo en horario 9am - 9pm hora Campeche
- El mensaje incluye invitación a la ruleta
"""

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from sqlalchemy import select, func
from agent.memory import async_session, Mensaje

logger = logging.getLogger("agentkit")

ZONA_CAMPECHE = ZoneInfo("America/Merida")
INACTIVIDAD_MINUTOS = 60       # 1 hora sin respuesta
HORA_INICIO = 9                # No enviar antes de las 9am
HORA_FIN = 21                  # No enviar después de las 9pm

MENSAJE_SEGUIMIENTO = (
    "Hola, soy Clio de LiTek 👋 Solo quería asegurarme de que no te quedaron dudas "
    "sobre tu cotización. ¡Estoy aquí cuando me necesites!\n\n"
    "Y recuerda: puedes girar nuestra ruleta 🎡 y ganarte un descuento para tu pedido. "
    "Es gratis y tarda 10 segundos → *litek.mx/ruleta*"
)

# Marcador sin corchetes para evitar problemas con LIKE en SQLite
MARCA_SEGUIMIENTO = "SEGUIMIENTO_AUTO"

# Cache en memoria: teléfonos que ya recibieron seguimiento en esta sesión del servidor
# Esto evita reenvíos incluso si la DB falla — se limpia al reiniciar el servidor
_enviados_sesion: set[str] = set()


async def obtener_conversaciones_inactivas() -> list[str]:
    """
    Retorna lista de teléfonos con conversaciones inactivas:
    - Último mensaje es de 'assistant'
    - Hace más de INACTIVIDAD_MINUTOS minutos
    - Al menos 2 mensajes en total
    - No se ha enviado ya un seguimiento reciente
    """
    ahora_utc = datetime.utcnow()
    limite_tiempo = ahora_utc - timedelta(minutes=INACTIVIDAD_MINUTOS)
    limite_seguimiento = ahora_utc - timedelta(hours=24)  # No reenviar en 24h

    telefonos_inactivos = []

    async with async_session() as session:
        # Obtener todos los teléfonos únicos con conversaciones
        query_telefonos = select(Mensaje.telefono).distinct()
        result = await session.execute(query_telefonos)
        telefonos = [row[0] for row in result.fetchall()]

        for telefono in telefonos:
            # Ignorar números de prueba o internos
            if "test" in telefono.lower() or telefono.startswith("alerta"):
                continue

            # Cache en memoria: si ya se envió en esta sesión, saltar
            if telefono in _enviados_sesion:
                continue

            # Obtener el último mensaje de esta conversación
            query_ultimo = (
                select(Mensaje)
                .where(Mensaje.telefono == telefono)
                .order_by(Mensaje.timestamp.desc())
                .limit(1)
            )
            result = await session.execute(query_ultimo)
            ultimo = result.scalar_one_or_none()

            if not ultimo:
                continue

            # El último mensaje debe ser de assistant (Clio ya respondió, cliente no contesta)
            if ultimo.role != "assistant":
                continue

            # Debe haber pasado al menos INACTIVIDAD_MINUTOS
            if ultimo.timestamp > limite_tiempo:
                continue

            # Verificar que el último mensaje no sea un seguimiento previo
            if MARCA_SEGUIMIENTO in ultimo.content:
                continue

            # Contar total de mensajes (conversación real, no solo saludo)
            query_count = (
                select(func.count(Mensaje.id))
                .where(Mensaje.telefono == telefono)
            )
            result = await session.execute(query_count)
            total = result.scalar_one()

            if total < 2:
                continue

            # Verificar en DB que no se haya enviado seguimiento en las últimas 24h
            query_seguimiento = (
                select(Mensaje)
                .where(
                    Mensaje.telefono == telefono,
                    Mensaje.role == "assistant",
                    Mensaje.content.like(f"%{MARCA_SEGUIMIENTO}%"),
                    Mensaje.timestamp > limite_seguimiento,
                )
            )
            result = await session.execute(query_seguimiento)
            seguimiento_reciente = result.scalar_one_or_none()

            if seguimiento_reciente:
                _enviados_sesion.add(telefono)  # sincronizar cache
                continue

            telefonos_inactivos.append(telefono)

    return telefonos_inactivos


def es_horario_permitido() -> bool:
    """Verifica que sea horario permitido para enviar seguimientos."""
    ahora = datetime.now(ZONA_CAMPECHE)
    return HORA_INICIO <= ahora.hour < HORA_FIN


async def enviar_seguimientos(token: str):
    """
    Job principal: detecta inactivos y les envía mensaje de seguimiento.
    Se ejecuta cada 30 minutos via APScheduler.
    """
    if not es_horario_permitido():
        logger.info("Seguimiento: fuera de horario, no se envían mensajes")
        return

    telefonos = await obtener_conversaciones_inactivas()

    if not telefonos:
        logger.info("Seguimiento: ningún cliente inactivo detectado")
        return

    logger.info(f"Seguimiento: {len(telefonos)} cliente(s) inactivo(s) detectado(s)")

    import httpx
    for telefono in telefonos:
        try:
            # Agregar al cache ANTES de enviar para evitar condición de carrera
            _enviados_sesion.add(telefono)

            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            }
            async with httpx.AsyncClient() as client:
                r = await client.post(
                    "https://gate.whapi.cloud/messages/text",
                    json={"to": telefono, "body": MENSAJE_SEGUIMIENTO},
                    headers=headers,
                )
                if r.status_code == 200:
                    logger.info(f"Seguimiento enviado a {telefono}")
                    # Guardar en DB con marca para persistir entre reinicios
                    from agent.memory import guardar_mensaje
                    await guardar_mensaje(
                        telefono, "assistant",
                        MENSAJE_SEGUIMIENTO + f" {MARCA_SEGUIMIENTO}"
                    )
                else:
                    logger.error(f"Error enviando seguimiento a {telefono}: {r.text}")
                    # Quitar del cache si falló el envío para reintentar en próxima vuelta
                    _enviados_sesion.discard(telefono)
        except Exception as e:
            logger.error(f"Error en seguimiento para {telefono}: {e}")
            _enviados_sesion.discard(telefono)
