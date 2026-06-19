# agent/agradecimiento.py — Agradecimiento post-venta + pedir calificación
# Generado por AgentKit para LiTek

"""
A los clientes que YA pagaron/compraron (estado 'proceso' o 'vendido' en el CRM),
12 horas después les manda UN mensaje de agradecimiento y les pide calificar el
servicio (Bueno / Regular / Malo + comentario). La respuesta del cliente la
captura Clio con la etiqueta [CALIFICACION:...] y se guarda en el CRM.

Reglas:
- Solo clientes que ya pagaron (no a los que apenas cotizan).
- Una sola vez por cliente (marcador GRACIAS_AUTO, sobrevive reinicios).
- Solo en horario 9am - 9pm hora Campeche.
- Estos clientes NO reciben el recordatorio de "seguimos pendientes".
"""

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from sqlalchemy import select
from agent.memory import async_session, Mensaje, clientes_para_agradecer, telefono_conversacion

logger = logging.getLogger("agentkit")

ZONA_CAMPECHE = ZoneInfo("America/Merida")
HORA_INICIO = 9
HORA_FIN = 21
HORAS_DESPUES_DE_PAGAR = 9    # esperar 9h tras confirmar el pago
DIAS_MAX = 7                   # no agradecer pedidos de hace más de 7 días

# Marcador para saber a quién ya se le agradeció (sin caracteres especiales)
MARCA_GRACIAS = "GRACIAS_AUTO"

MENSAJE_GRACIAS = (
    "¡Hola! 🙏 Aquí Clio de LiTek. Mil gracias por confiar en nosotros para tu pedido. "
    "¿Cómo calificarías nuestro servicio? Responde solo con el número:\n\n"
    "1️⃣ Bueno 😀\n"
    "2️⃣ Regular 😐\n"
    "3️⃣ Malo 😞\n\n"
    "Y si quieres, déjanos un comentario — nos ayuda muchísimo a mejorar. 💪"
)

# Cache en memoria: telefono → timestamp del agradecimiento enviado
_cache_agradecidos: dict[str, datetime] = {}
VENTANA_DIAS = 30  # no reagradecer en 30 días


def _wa_destino(telefono: str) -> str:
    """Normaliza a formato WhatsApp México: 521 + últimos 10 dígitos."""
    d = "".join(c for c in (telefono or "") if c.isdigit())
    return ("521" + d[-10:]) if len(d) >= 10 else d


async def cargar_cache_agradecidos():
    """Carga de la DB los teléfonos que ya recibieron el agradecimiento (sobrevive reinicios)."""
    limite = datetime.utcnow() - timedelta(days=VENTANA_DIAS)
    async with async_session() as session:
        query = (
            select(Mensaje.telefono, Mensaje.timestamp)
            .where(
                Mensaje.role == "assistant",
                Mensaje.content.contains(MARCA_GRACIAS),
                Mensaje.timestamp > limite,
            )
            .order_by(Mensaje.timestamp.desc())
        )
        result = await session.execute(query)
        for telefono, timestamp in result.fetchall():
            if telefono not in _cache_agradecidos:
                _cache_agradecidos[telefono] = timestamp
    logger.info(f"Agradecimiento: cache cargado — {len(_cache_agradecidos)} cliente(s) ya agradecido(s)")


def _ya_agradecido(telefono: str) -> bool:
    enviado = _cache_agradecidos.get(telefono)
    if not enviado:
        return False
    return datetime.utcnow() - enviado < timedelta(days=VENTANA_DIAS)


def es_horario_permitido() -> bool:
    ahora = datetime.now(ZONA_CAMPECHE)
    return HORA_INICIO <= ahora.hour < HORA_FIN


async def enviar_agradecimientos(token: str):
    """
    Job: detecta clientes que pagaron hace >=12h y les manda el agradecimiento
    + pedido de calificación. Una sola vez por cliente. Cada 30 min via APScheduler.
    """
    if not es_horario_permitido():
        logger.info("Agradecimiento: fuera de horario")
        return

    candidatos = await clientes_para_agradecer(horas_min=HORAS_DESPUES_DE_PAGAR, dias_max=DIAS_MAX)
    if not candidatos:
        logger.info("Agradecimiento: ningún cliente por agradecer")
        return

    import httpx
    from agent.memory import guardar_mensaje

    enviados = 0
    for cli in candidatos:
        telefono_crm = cli["telefono"]
        # Ignorar internos/pruebas
        if "test" in telefono_crm.lower() or telefono_crm.startswith("alerta"):
            continue
        # Teléfono EXACTO de la conversación (para que el gracias caiga en el
        # mismo historial que ve Clio y entienda la respuesta del cliente).
        telefono = await telefono_conversacion(telefono_crm)
        if _ya_agradecido(telefono):
            continue
        try:
            # Marcar ANTES de enviar para evitar duplicados por carrera
            _cache_agradecidos[telefono] = datetime.utcnow()
            headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
            async with httpx.AsyncClient() as client:
                r = await client.post(
                    "https://gate.whapi.cloud/messages/text",
                    json={"to": _wa_destino(telefono), "body": MENSAJE_GRACIAS},
                    headers=headers,
                )
            if r.status_code == 200:
                enviados += 1
                logger.info(f"Agradecimiento enviado a {telefono}")
                # Guardar en DB con marcador: persiste el "ya envié" y deja el
                # gracias en el historial de Clio (para entender la calificación).
                await guardar_mensaje(telefono, "assistant", MENSAJE_GRACIAS + f" {MARCA_GRACIAS}")
            else:
                logger.error(f"Error enviando agradecimiento a {telefono}: {r.text}")
                _cache_agradecidos.pop(telefono, None)
        except Exception as e:
            logger.error(f"Error en agradecimiento para {telefono}: {e}")
            _cache_agradecidos.pop(telefono, None)

    if enviados:
        logger.info(f"Agradecimiento: {enviados} mensaje(s) enviado(s)")
