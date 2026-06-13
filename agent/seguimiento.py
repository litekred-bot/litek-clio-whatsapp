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
from agent.memory import async_session, Mensaje, estado_crm_por_telefono

logger = logging.getLogger("agentkit")

ZONA_CAMPECHE = ZoneInfo("America/Merida")
INACTIVIDAD_MINUTOS = 60       # 1 hora sin respuesta
HORA_INICIO = 9                # No enviar antes de las 9am
HORA_FIN = 21                  # No enviar después de las 9pm

MENSAJE_SEGUIMIENTO = (
    "¡Hola! 👋 Soy Clio de LiTek. Seguimos pendientes de tu producto — "
    "cuando quieras continuamos, aquí estoy para ayudarte 😊"
)

# Marcador sin caracteres especiales para evitar problemas en SQLite LIKE
MARCA_SEGUIMIENTO = "SEGUIMIENTO_AUTO"

# Cache en memoria: telefono → timestamp del último seguimiento enviado
# Se puebla desde la DB al arrancar el servidor (ver cargar_cache_desde_db)
_cache_enviados: dict[str, datetime] = {}
VENTANA_HORAS = 24  # No reenviar en 24 horas


async def cargar_cache_desde_db():
    """
    Carga desde la DB los teléfonos que ya recibieron seguimiento reciente.
    Se llama al arrancar el servidor para que el cache sobreviva reinicios.
    """
    limite = datetime.utcnow() - timedelta(hours=VENTANA_HORAS)
    async with async_session() as session:
        query = (
            select(Mensaje.telefono, Mensaje.timestamp)
            .where(
                Mensaje.role == "assistant",
                Mensaje.content.contains(MARCA_SEGUIMIENTO),
                Mensaje.timestamp > limite,
            )
            .order_by(Mensaje.timestamp.desc())
        )
        result = await session.execute(query)
        rows = result.fetchall()
        for telefono, timestamp in rows:
            if telefono not in _cache_enviados:
                _cache_enviados[telefono] = timestamp
    logger.info(f"Seguimiento: cache cargado desde DB — {len(_cache_enviados)} teléfono(s) bloqueado(s)")


def _ya_recibio_seguimiento(telefono: str) -> bool:
    """Verifica si el teléfono recibió seguimiento en las últimas 24h (cache en memoria)."""
    if telefono not in _cache_enviados:
        return False
    enviado_en = _cache_enviados[telefono]
    return datetime.utcnow() - enviado_en < timedelta(hours=VENTANA_HORAS)


async def obtener_conversaciones_inactivas() -> list[str]:
    """
    Retorna lista de teléfonos con conversaciones inactivas:
    - Último mensaje es de 'assistant'
    - Hace más de INACTIVIDAD_MINUTOS minutos
    - Al menos 2 mensajes en total
    - No se ha enviado ya un seguimiento en las últimas 24h
    """
    ahora_utc = datetime.utcnow()
    limite_tiempo = ahora_utc - timedelta(minutes=INACTIVIDAD_MINUTOS)

    telefonos_inactivos = []

    async with async_session() as session:
        query_telefonos = select(Mensaje.telefono).distinct()
        result = await session.execute(query_telefonos)
        telefonos = [row[0] for row in result.fetchall()]

        for telefono in telefonos:
            # Ignorar números de prueba o internos
            if "test" in telefono.lower() or telefono.startswith("alerta"):
                continue

            # ① Cache en memoria — barrera más rápida
            if _ya_recibio_seguimiento(telefono):
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

            # El último mensaje debe ser de assistant (Clio respondió, cliente no contesta)
            if ultimo.role != "assistant":
                continue

            # Debe haber pasado al menos INACTIVIDAD_MINUTOS
            if ultimo.timestamp > limite_tiempo:
                continue

            # ② Si el último mensaje ya es un seguimiento, no reenviar
            if MARCA_SEGUIMIENTO in ultimo.content:
                _cache_enviados[telefono] = ultimo.timestamp  # sincronizar cache
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

            # ③ Si el cliente YA pagó (proceso/vendido), NO le mandamos el
            # recordatorio de "seguimos pendientes" — a ellos les toca el
            # agradecimiento + calificación (ver agent/agradecimiento.py).
            try:
                estado = await estado_crm_por_telefono(telefono)
                if estado in ("proceso", "vendido"):
                    continue
            except Exception:
                pass  # si falla la consulta, no bloquear el recordatorio normal

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
            # ③ Marcar en cache ANTES de enviar para evitar condición de carrera
            _cache_enviados[telefono] = datetime.utcnow()

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
                    # Guardar en DB para persistir entre reinicios del servidor
                    from agent.memory import guardar_mensaje
                    await guardar_mensaje(
                        telefono, "assistant",
                        MENSAJE_SEGUIMIENTO + f" {MARCA_SEGUIMIENTO}"
                    )
                else:
                    logger.error(f"Error enviando seguimiento a {telefono}: {r.text}")
                    # Si falló el envío, quitar del cache para reintentar después
                    _cache_enviados.pop(telefono, None)
        except Exception as e:
            logger.error(f"Error en seguimiento para {telefono}: {e}")
            _cache_enviados.pop(telefono, None)
