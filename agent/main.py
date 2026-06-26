# agent/main.py — Servidor FastAPI + Webhook de WhatsApp
# Generado por AgentKit para LiTek

import os
import re
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from contextlib import asynccontextmanager
from collections import OrderedDict
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from zoneinfo import ZoneInfo

# Cache de IDs procesados para evitar duplicados (Whapi reintenta si tardamos)
# OrderedDict para poder limpiar los más viejos y no crecer infinitamente
_ids_procesados: OrderedDict[str, bool] = OrderedDict()
MAX_IDS_CACHE = 500

# Archivo de diseño/arte que el cliente mandó para imprimir (telefono → media_url).
# Se guarda cuando manda el diseño y se reenvía al asesor cuando confirma el pago.
_archivo_diseno: dict[str, str] = {}

# Clientes que YA pagaron pero NO han mandado su archivo de impresión (telefono → nombre).
# Cuando el archivo llegue después, se reenvía al asesor de inmediato.
_pago_sin_archivo: dict[str, str] = {}

# Pedidos ya confirmados al grupo (telefono → datetime), para no reenviar el mismo
# pedido varias veces cuando el cliente manda detalles del pago en mensajes seguidos.
_pedido_confirmado_ts: dict[str, datetime] = {}
VENTANA_PEDIDO_MIN = 60  # no reenviar el pedido confirmado del mismo cliente en 60 min

# Última imagen/documento que mandó el cliente (telefono → "tipo|url").
# Red de seguridad: si Clio confirma el pago en un turno sin imagen, usamos esta
# como comprobante (el cliente la mandó un mensaje antes).
_ultima_imagen: dict[str, str] = {}

from agent.brain import generar_respuesta
from agent.memory import (
    inicializar_db, guardar_mensaje, obtener_historial, registrar_ruleta, verificar_ruleta,
    registrar_crm, registrar_o_actualizar_crm, listar_crm, actualizar_crm, verificar_usuario_crm,
    minutos_desde_ultimo_mensaje, asignar_ruletas_sin_avanzar,
    carga_por_asesor, consolidar_duplicados_crm, obtener_conversacion_crm,
    tomar_control, devolver_clio, estado_control, esta_en_modo_humano,
    marcar_alerta_crm, listar_usuarios_crm, cambiar_password_crm,
    reactivar_cliente_no_contesto, marcar_no_contesto_automatico,
    marcar_no_concretado_automatico, marcar_esperando_pago_automatico, marcar_expres_crm,
    guardar_calificacion_crm, guardar_monto_crm, total_vendido_crm, backfill_montos_crm,
    analisis_mensajeria,
    guardar_sucursal_crm, sucursal_crm_por_telefono, asesor_crm_por_telefono,
    guardar_entrega_crm, entrega_crm_por_telefono, info_pedido_por_telefono,
    pedidos_listos_para_entregar, marcar_factura_crm, forzar_asesor_crm,
    canalizar_diseno_disenador, marcar_diseno_crm, marcar_diseno_aprobado_crm, rutear_asesor_merida,
)
from zoneinfo import ZoneInfo as _ZI


def es_horario_atencion(sucursal: str = "") -> bool:
    """True si estamos dentro del horario de atención de la sucursal del cliente.
    Campeche: L-V 9-19h, Sáb 9-16h. Carmen y Mérida: L-V 9-18h, Sáb 9-14h. Dom cerrado."""
    ahora = datetime.now(_ZI("America/Merida"))
    dia = ahora.weekday()  # 0=Lun ... 6=Dom
    h = ahora.hour
    if dia == 6:            # Domingo (cerrado en todas)
        return False
    if sucursal in ("Carmen", "Mérida"):
        if dia == 5:        # Sábado hasta las 2pm
            return 9 <= h < 14
        return 9 <= h < 18  # Lunes a Viernes hasta las 6pm
    if dia == 5:            # Sábado hasta las 4pm (Campeche)
        return 9 <= h < 16
    return 9 <= h < 19      # Lunes a Viernes hasta las 7pm (Campeche)

# ── Reparto POR PRODUCTO (no por turnos) ──────────────────────────────────────
# El primer producto que identifica el cliente define su asesor; luego no cambia.
PRODUCTO_A_ASESOR = {
    # Anna — lonas, vinil impreso e impresión general
    "lona": "Anna", "vinil_impreso": "Anna", "microperforado": "Anna",
    "tabloide_laser": "Anna", "corte_vinil": "Anna",
    # Brayan — letreros/etiquetas + vinil rígido (PVC/coroplast) y papel gran formato
    "vinil_pvc": "Brayan", "coroplast": "Brayan",
    "papel_couche": "Brayan", "papel_bond": "Brayan",
    "etiqueta_5x5": "Brayan", "etiqueta_personalizada": "Brayan",
}


def asesor_por_producto(producto: str) -> str:
    """Asesor según el producto. Por defecto Anna (impresión general)."""
    return PRODUCTO_A_ASESOR.get(producto, "Anna")
from agent.providers import obtener_proveedor
from agent.transcription import transcribir_audio
from agent.document_reader import leer_documento
from agent.escalation import enviar_alerta_asesor, AREAS
from agent.reporte_diario import generar_reporte_diario
from agent.seguimiento import enviar_seguimientos, cargar_cache_desde_db, enviar_seguimiento_ruleta, cargar_cache_ruleta
from agent.agradecimiento import enviar_agradecimientos, cargar_cache_agradecidos
from agent.crm import HTML_PANEL, crear_token, verificar_token

# Número del dueño para recibir copia de cotizaciones de productos que no son lonas
ADMIN_WHATSAPP = os.getenv("ADMIN_WHATSAPP", "529812710000")
# Grupo de alertas de Clio (equipo LiTek) — recibe pedidos confirmados, comprobantes y archivos
ASESOR_WHATSAPP = os.getenv("ASESOR_WHATSAPP", "120363425558631008@g.us")  # Campeche/principal
# Grupo de alertas de la sucursal CARMEN (Alan, Jadiel, Leo)
ASESOR_WHATSAPP_CARMEN = os.getenv("ASESOR_WHATSAPP_CARMEN", "120363408250148529@g.us")
# Grupo de alertas de la sucursal MÉRIDA (Edith, Jadiel, Leo)
ASESOR_WHATSAPP_MERIDA = os.getenv("ASESOR_WHATSAPP_MERIDA", "120363410269425019@g.us")
# Mapa sucursal → grupo de alertas
GRUPO_ALERTA_SUCURSAL = {"Carmen": ASESOR_WHATSAPP_CARMEN, "Mérida": ASESOR_WHATSAPP_MERIDA}
# Diseñador de LiTek (Erick): recibe aviso de cada diseño PAGADO para realizarlo
ERICK_WHATSAPP = os.getenv("ERICK_WHATSAPP", "529811388507")
# Responsables de FACTURACIÓN por sucursal: Campeche→Tere, Carmen y Mérida→Leo
TERE_WHATSAPP = os.getenv("TERE_WHATSAPP", "529811388508")
LEO_WHATSAPP = os.getenv("LEO_WHATSAPP", "529818299794")
FACTURA_WHATSAPP = {"Campeche": TERE_WHATSAPP, "Carmen": LEO_WHATSAPP, "Mérida": LEO_WHATSAPP}
# Chino (director): recibe directo las quejas de Mérida (además del grupo)
CHINO_WHATSAPP = os.getenv("CHINO_WHATSAPP", "529812710000")
# WhatsApp PERSONAL de cada asesor → para mandarle la alerta también en privado
# (además del grupo, que el director usa como seguimiento).
ASESOR_PERSONAL = {
    "Anna": "529818290272", "Brayan": "529811670283", "Tere": "529811388508",
    "Leo": "529818299794", "Alan": "529381881109", "Jadiel": "529901017233",
    "Edith": "529811068908", "Erick": "529811388507", "Chino": "529812710000",
}


async def _avisar_personal(asesor: str, mensaje: str):
    """Manda la alerta en privado al asesor (además del grupo). Silencioso si falla."""
    num = ASESOR_PERSONAL.get((asesor or "").strip())
    if not num:
        return
    try:
        await proveedor.enviar_mensaje(num, mensaje)
    except Exception as e:
        logger.error(f"Error avisando en privado a {asesor}: {e}")


async def enviar_avisos_listos_para_entregar(*_):
    """Job: avisa al grupo + asesor cuando un pedido llega a su hora de entrega."""
    try:
        listos = await pedidos_listos_para_entregar()
    except Exception as e:
        logger.error(f"Error buscando pedidos listos: {e}")
        return
    for p in listos:
        wa = "".join(c for c in (p["telefono"] or "") if c.isdigit())
        msg = (
            f"📦 *LISTO PARA ENTREGAR — CLIO*\n\n"
            f"👤 *Cliente:* {p['nombre']}\n"
            f"📱 wa.me/{wa}\n"
            f"🏢 *Sucursal:* {p['sucursal']}\n"
            f"📋 *Pedido:* {p['descripcion'][:160]}\n\n"
            f"Ya llegó su hora de entrega. Cuando lo entreguen, márquenlo "
            f"como *✅ Entregado* en el panel para quitarlo de pendientes. 🙌"
        )
        grupo = GRUPO_ALERTA_SUCURSAL.get(p["sucursal"], ASESOR_WHATSAPP)
        try:
            await proveedor.enviar_mensaje(grupo, msg)
        except Exception as e:
            logger.error(f"Error aviso listo (grupo): {e}")
        await _avisar_personal(p["asesor"], msg)
        # Avisar también al CLIENTE que su pedido ya está listo para recoger
        try:
            nom = (p["nombre"] or "").split(" ")[0]
            msg_cli = (
                f"¡Hola {nom}! 📦 ¡Buenas noticias! Tu pedido ya está listo. "
                f"Puedes pasar a recogerlo cuando gustes 😊"
            )
            await proveedor.enviar_mensaje(p["telefono"], msg_cli)
        except Exception as e:
            logger.error(f"Error avisando al cliente listo: {e}")
    if listos:
        logger.info(f"Listos para entregar: {len(listos)} aviso(s) enviado(s)")


async def _grupo_alerta_de(telefono: str) -> str:
    """Grupo de WhatsApp al que van las alertas, según la sucursal del cliente."""
    try:
        suc = await sucursal_crm_por_telefono(telefono)
    except Exception:
        suc = "Campeche"
    return GRUPO_ALERTA_SUCURSAL.get(suc, ASESOR_WHATSAPP)

load_dotenv()

ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
log_level = logging.DEBUG if ENVIRONMENT == "development" else logging.INFO
logging.basicConfig(level=log_level)
logger = logging.getLogger("agentkit")

proveedor = obtener_proveedor()
PORT = int(os.getenv("PORT", 8000))

# Canal SECUNDARIO Meta (solo para la revisión de Meta / migración futura).
# Aislado: vive en /webhook-meta y NO toca el flujo de Whapi (producción).
# Se activa solo si están las credenciales de Meta en el entorno.
prov_meta = None
try:
    if os.getenv("META_ACCESS_TOKEN") and os.getenv("META_PHONE_NUMBER_ID"):
        from agent.providers.meta import ProveedorMeta
        prov_meta = ProveedorMeta()
        logger.info("Canal Meta ACTIVO en /webhook-meta")
except Exception as e:
    logger.error(f"No se pudo activar el canal Meta: {e}")
    prov_meta = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Inicializa la base de datos y el scheduler al arrancar el servidor."""
    await inicializar_db()
    logger.info("Base de datos inicializada")
    await cargar_cache_desde_db()  # Carga seguimientos recientes para no reenviar tras reinicio
    await cargar_cache_agradecidos()  # Carga a quién ya se agradeció (no reenviar tras reinicio)
    await cargar_cache_ruleta()  # Carga a qué ganador ya se le recordó su premio (no reenviar)
    # Recuperar montos de pedidos viejos desde el texto (una sola vez por pedido,
    # porque solo toca los que aún no tienen fecha de pago).
    try:
        bf = await backfill_montos_crm()
        if bf["actualizados"]:
            logger.info(f"Backfill montos: {bf['actualizados']} pedidos, ${bf['total']:.0f} recuperados")
    except Exception as e:
        logger.error(f"Error en backfill de montos: {e}")
    logger.info(f"Servidor AgentKit — LiTek corriendo en puerto {PORT}")
    logger.info(f"Proveedor de WhatsApp: {proveedor.__class__.__name__}")

    # Scheduler: reporte diario a las 6:00 AM hora Campeche
    scheduler = AsyncIOScheduler(timezone=ZoneInfo("America/Merida"))
    scheduler.add_job(
        generar_reporte_diario,
        CronTrigger(hour=5, minute=0),
        args=[proveedor.token],
        id="reporte_diario",
        name="Reporte diario de clientes pendientes",
        replace_existing=True,
    )
    # Seguimiento automático: 1 mensaje a clientes inactivos por más de 1 hora.
    # Seguro con PostgreSQL persistente: el marcador "ya envié" sobrevive reinicios.
    scheduler.add_job(
        enviar_seguimientos,
        "interval",
        minutes=30,
        args=[proveedor.token],
        id="seguimiento_inactivos",
        name="Seguimiento clientes inactivos cada 30 minutos",
        replace_existing=True,
    )
    # Agradecimiento post-venta: a los que pagaron hace +12h, gracias + pedir
    # calificación (una sola vez). A estos NO les llega el "seguimos pendientes".
    scheduler.add_job(
        enviar_agradecimientos,
        "interval",
        minutes=30,
        args=[proveedor.token],
        id="agradecimiento_postventa",
        name="Agradecer y pedir calificación a clientes que ya pagaron (+12h)",
        replace_existing=True,
    )
    # Recordatorio de premio (Mensaje 2): a las ~2h al ganador que no contestó.
    scheduler.add_job(
        enviar_seguimiento_ruleta,
        "interval",
        minutes=30,
        args=[proveedor.token],
        id="seguimiento_ruleta",
        name="Aviso a Erick de ganadores de ruleta que no contestan (+2h)",
        replace_existing=True,
    )
    # Reparto de ganadores de ruleta que llevan +2h sin avanzar (sin dueño).
    # Si compraron antes, ya tienen dueño y se ignoran (no se duplica).
    scheduler.add_job(
        asignar_ruletas_sin_avanzar,
        "interval",
        minutes=30,
        id="reparto_ruleta",
        name="Asignar ganadores de ruleta sin avanzar (+2h) a Erick",
        replace_existing=True,
    )
    # Marcar 'No contestó' los leads sin actividad en 2 días (cada 3 horas).
    scheduler.add_job(
        marcar_no_contesto_automatico,
        "interval",
        hours=3,
        id="marcar_no_contesto",
        name="Mover a 'No contestó' leads sin actividad en 2 días",
        replace_existing=True,
    )
    # Marcar 'No concretado' los leads cotizados que callaron +10h (cada 30 min).
    scheduler.add_job(
        marcar_no_concretado_automatico,
        "interval",
        minutes=30,
        id="marcar_no_concretado",
        name="Mover a 'No concretado' leads cotizados sin respuesta en 10h",
        replace_existing=True,
    )
    # Marcar 'Esperando pago' los leads que recibieron la cuenta y callaron +2h (cada 15 min).
    scheduler.add_job(
        marcar_esperando_pago_automatico,
        "interval",
        minutes=15,
        id="marcar_esperando_pago",
        name="Mover a 'Esperando pago' leads con cuenta dada sin pagar en 2h",
        replace_existing=True,
    )
    # Avisar 'Listo para entregar' cuando un pedido llega a su hora de entrega (cada 15 min).
    scheduler.add_job(
        enviar_avisos_listos_para_entregar,
        "interval",
        minutes=15,
        id="listos_para_entregar",
        name="Avisar pedidos listos para entregar (hora de entrega cumplida)",
        replace_existing=True,
    )
    scheduler.start()
    logger.info("Scheduler iniciado — reporte 5AM + seguimiento + reparto ruleta + no-contestó (Campeche)")

    yield

    scheduler.shutdown()
    logger.info("Scheduler detenido")


app = FastAPI(
    title="Clio — Agente de Ventas LiTek",
    version="1.0.0",
    lifespan=lifespan
)

# CORS — permite llamadas desde litek.mx a los endpoints de ruleta
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def health_check():
    """Endpoint de salud para Railway/monitoreo."""
    return {"status": "ok", "service": "agentkit-litek", "agente": "Clio"}


# ─────────────────────────────────────────────────────────────────────────────
# CRM — panel de seguimiento del equipo
# ─────────────────────────────────────────────────────────────────────────────
from fastapi.responses import HTMLResponse


def _crm_usuario_de_request(request: Request) -> dict | None:
    """Extrae y valida el token del header Authorization."""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return verificar_token(auth[7:])
    return None


# Quién ve qué: director (Chino) y administradora (Tere) ven TODO;
# cada asesor (Anna, Brayan) ve SOLO lo asignado a él.
CRM_VEN_TODO = {"chino", "tere"}
CRM_USUARIO_A_ASESOR = {"anna": "Anna", "brayan": "Brayan", "alan": "Alan", "jadiel": "Jadiel", "erick": "Erick"}
# Logins COMPARTIDOS: un usuario que ve los clientes de VARIOS asesores (se apoyan).
CRM_USUARIO_MULTI = {"taller": ("Brayan", "Erick")}
# Administradores de UNA sola sucursal: ven todo lo de SU sucursal (clientes + dinero),
# pero nada de las demás. Ej: Leo ve solo Carmen.
CRM_ADMIN_SUCURSAL = {"leo": "Carmen", "edith": "Mérida"}
# Asesores que pertenecen a cada sucursal (para la carga "Clientes por atender").
ASESORES_POR_SUCURSAL = {
    # Erick (diseñador) aparece en las 3 porque atiende diseños de todas.
    "Campeche": ("Anna", "Brayan", "Tere", "Erick"),
    "Carmen": ("Alan", "Jadiel", "Erick"),
    "Mérida": ("Edith", "Jadiel", "Erick"),
}


def _es_admin(usuario: str) -> bool:
    """¿El usuario ve dinero/ventas? (director global o admin de sucursal)."""
    return usuario in CRM_VEN_TODO or usuario in CRM_ADMIN_SUCURSAL


def _asesor_filtro_de(usuario: str) -> str:
    """Asesor por el que se filtra. '' = ve todo (director / admin de sucursal)."""
    if usuario in CRM_VEN_TODO or usuario in CRM_ADMIN_SUCURSAL:
        return ""
    return CRM_USUARIO_A_ASESOR.get(usuario, usuario.capitalize())


@app.get("/crm")
async def crm_panel():
    """Página del panel CRM (login + tablero)."""
    return HTMLResponse(HTML_PANEL)


@app.get("/demo")
async def crm_demo():
    """Espejo del CRM con datos de EJEMPLO (sin info real) — para mostrar/compartir."""
    try:
        with open("web/crm_demo.html", "r", encoding="utf-8") as f:
            return HTMLResponse(f.read())
    except Exception:
        return HTMLResponse("<h1>Demo no disponible</h1>", status_code=404)


@app.post("/crm/login")
async def crm_login(request: Request):
    """Autenticación del CRM. Retorna token de sesión."""
    data = await request.json()
    usuario = data.get("usuario", "")
    password = data.get("password", "")
    u = await verificar_usuario_crm(usuario, password)
    if u:
        return {"ok": True, "token": crear_token(u["usuario"], u["nombre"]), "nombre": u["nombre"]}
    return {"ok": False}


_TZ_CAMP = ZoneInfo("America/Merida")


def _mes_inicio_utc(mes: str):
    """'YYYY-MM' → primer día de ese mes 00:00 hora Campeche, como UTC naive. None si inválido."""
    try:
        y, m = [int(x) for x in mes.split("-")[:2]]
        local = datetime(y, m, 1, tzinfo=_TZ_CAMP)
        return local.astimezone(timezone.utc).replace(tzinfo=None)
    except Exception:
        return None


def _mes_siguiente_utc(mes: str):
    """Primer día del mes SIGUIENTE al dado (límite superior exclusivo), UTC naive."""
    try:
        y, m = [int(x) for x in mes.split("-")[:2]]
        if m == 12:
            y, m = y + 1, 1
        else:
            m += 1
        local = datetime(y, m, 1, tzinfo=_TZ_CAMP)
        return local.astimezone(timezone.utc).replace(tzinfo=None)
    except Exception:
        return None


@app.get("/crm/api/registros")
async def crm_registros(request: Request, estado: str = "", tipo: str = "",
                        desde: str = "", hasta: str = "", sucursal: str = ""):
    """Lista registros del CRM (requiere token). Cada asesor ve solo lo suyo."""
    u = _crm_usuario_de_request(request)
    if not u:
        raise HTTPException(status_code=401, detail="No autorizado")
    # Filtro por persona: director/admin ven todo, cada asesor solo lo asignado a él
    asesor_filtro = _asesor_filtro_de(u["usuario"])
    # Login COMPARTIDO (ej. 'taller' = Brayan + Erick): ve los clientes de VARIOS asesores.
    asesores_multi = CRM_USUARIO_MULTI.get(u["usuario"], ())
    if asesores_multi:
        asesor_filtro = ""  # el filtro real lo hace 'asesores'
    # Admin de UNA sucursal (ej. Leo→Carmen): se le FUERZA su sucursal y ve dinero.
    suc_forzada = CRM_ADMIN_SUCURSAL.get(u["usuario"], "")
    if suc_forzada:
        sucursal = suc_forzada
    ve_todo = _es_admin(u["usuario"])
    # Erick (diseñador) ve, además de lo suyo, TODA tarjeta con diseño 🎨 (disena pedidos
    # de cualquier asesor sin ser su dueño). Igual el login compartido 'taller' (Brayan+Erick).
    incluir_diseno = (asesor_filtro == "Erick") or ("Erick" in asesores_multi)
    registros = await listar_crm(estado=estado, tipo=tipo, asesor=asesor_filtro, sucursal=sucursal, asesores=asesores_multi, incluir_diseno=incluir_diseno)
    # Stats por estado (respetando el filtro de persona y sucursal, sin filtro de estado)
    todos = await listar_crm(tipo=tipo, asesor=asesor_filtro, sucursal=sucursal, limite=1000, asesores=asesores_multi, incluir_diseno=incluir_diseno)
    stats = {"nuevo": 0, "asignado": 0, "proceso": 0, "vendido": 0, "no_contesto": 0}
    for r in todos:
        stats[r["estado"]] = stats.get(r["estado"], 0) + 1
    # Ventas por rango de meses — SOLO el administrador (Tere/Chino) ve el dinero.
    # Los asesores NO reciben ningún total de ventas.
    ventas = None
    if ve_todo:
        # 'desde'/'hasta' llegan como 'YYYY-MM'. 'todo' = sin límites de fecha.
        es_todo = (desde == "todo" or hasta == "todo")
        if es_todo:
            d_ini = d_fin = None
            rango_label = "Todo"
        else:
            hoy = datetime.now(_TZ_CAMP)
            mes_actual = hoy.strftime("%Y-%m")
            mes_desde = desde or mes_actual
            mes_hasta = hasta or mes_desde
            d_ini = _mes_inicio_utc(mes_desde)
            d_fin = _mes_siguiente_utc(mes_hasta)
            rango_label = mes_desde if mes_desde == mes_hasta else (mes_desde + " a " + mes_hasta)
        ventas = await total_vendido_crm(desde=d_ini, hasta=d_fin, asesor="", sucursal=sucursal)
        ventas["rango"] = rango_label
        # Total de HOY (siempre el día de hoy, sin importar el rango elegido)
        hoy_camp = datetime.now(_TZ_CAMP)
        inicio_hoy = datetime(hoy_camp.year, hoy_camp.month, hoy_camp.day, tzinfo=_TZ_CAMP)
        hoy_ini = inicio_hoy.astimezone(timezone.utc).replace(tzinfo=None)
        hoy_fin = (inicio_hoy + timedelta(days=1)).astimezone(timezone.utc).replace(tzinfo=None)
        vh = await total_vendido_crm(desde=hoy_ini, hasta=hoy_fin, asesor="", sucursal=sucursal)
        ventas["hoy"] = vh["total"]
        ventas["hoy_num"] = vh["num"]
        # Total ACUMULADO (todo lo vendido hasta hoy, sin límite de fecha)
        vac = await total_vendido_crm(asesor="", sucursal=sucursal)
        ventas["acumulado"] = vac["total"]
        ventas["acumulado_num"] = vac["num"]
    # Carga por asesor (solo para quien ve todo). Si hay sucursal elegida, solo
    # muestra los asesores de ESA sucursal (Carmen → Alan/Jadiel; Campeche → Anna/Brayan/Tere).
    carga = None
    if ve_todo:
        aseq = ASESORES_POR_SUCURSAL.get(sucursal)
        carga = await carga_por_asesor(asesores=aseq, sucursal=sucursal) if aseq \
            else await carga_por_asesor(sucursal=sucursal)
    return {
        "registros": registros,
        "stats": stats,
        "ventas": ventas,
        "nombre": u["nombre"],
        "es_director": ve_todo,
        "solo_sucursal": suc_forzada,
        "carga": carga,
    }


@app.get("/crm/api/analisis")
async def crm_analisis(request: Request, desde: str = "", hasta: str = ""):
    """
    Análisis de costo de mensajería (entraron / vendido / mensajes) por sucursal y total.
    SOLO para el director/administrador (es información de dinero). Los costos los pone
    el panel en pesos; aquí solo damos los conteos para repartirlos.
    """
    u = _crm_usuario_de_request(request)
    if not u:
        raise HTTPException(status_code=401, detail="No autorizado")
    if not _es_admin(u["usuario"]):
        raise HTTPException(status_code=403, detail="Solo administradores")

    es_todo = (desde == "todo" or hasta == "todo")
    if es_todo:
        d_ini = d_fin = None
        rango_label = "Todo"
    else:
        mes_actual = datetime.now(_TZ_CAMP).strftime("%Y-%m")
        mes_desde = desde or mes_actual
        mes_hasta = hasta or mes_desde
        d_ini = _mes_inicio_utc(mes_desde)
        d_fin = _mes_siguiente_utc(mes_hasta)
        rango_label = mes_desde if mes_desde == mes_hasta else (mes_desde + " a " + mes_hasta)

    data = await analisis_mensajeria(desde=d_ini, hasta=d_fin)
    data["rango"] = rango_label
    return data


@app.get("/crm/api/chat")
async def crm_chat(request: Request, telefono: str = ""):
    """Devuelve la conversación de un cliente + estado de control (requiere token)."""
    u = _crm_usuario_de_request(request)
    if not u:
        raise HTTPException(status_code=401, detail="No autorizado")
    mensajes = await obtener_conversacion_crm(telefono)
    control = await estado_control(telefono)
    return {"mensajes": mensajes, "control": control}


@app.post("/crm/api/control")
async def crm_control(request: Request):
    """Toma o devuelve el control de una conversación (requiere token)."""
    u = _crm_usuario_de_request(request)
    if not u:
        raise HTTPException(status_code=401, detail="No autorizado")
    data = await request.json()
    telefono = data.get("telefono", "")
    accion = data.get("accion", "")  # "tomar" | "devolver"
    if accion == "tomar":
        await tomar_control(telefono, u["nombre"])
    elif accion == "devolver":
        await devolver_clio(telefono)
    return {"ok": True, "control": await estado_control(telefono)}


def _wa_destino(telefono: str) -> str:
    """Normaliza a formato WhatsApp México: 521 + últimos 10 dígitos."""
    d = "".join(c for c in (telefono or "") if c.isdigit())
    return ("521" + d[-10:]) if len(d) >= 10 else d


def _es_lona_gratis(premio: str) -> bool:
    """La lona gratis es el ÚNICO premio que se entrega sin compra."""
    pl = (premio or "").lower()
    return ("lona" in pl and "gratis" in pl)


def _mensaje_premio(nombre: str, premio: str, es_gol: bool) -> str:
    """
    Mensaje 1 — entrega del premio. La lona gratis se regala (sin compra);
    cualquier otro premio se valida haciendo un pedido (cualquier producto).
    """
    gancho = "⚽ *¡GOOOL!* Metiste el gol y" if es_gol else "🎡 Giraste la ruleta y"
    if _es_lona_gratis(premio):
        return (
            f"¡Felicidades {nombre}! {gancho} ganaste *{premio}* 🎉\n\n"
            f"Es tuya sin compra 🎁 Pasa a recogerla a Av. Colosio No. 414, Col. Pensiones "
            f"y muestra este chat. ¿Te late aprovechar y mandar a imprimir algo más?"
        )
    return (
        f"¡Felicidades {nombre}! {gancho} ganaste *{premio}* 🎉\n\n"
        f"Para hacerlo válido solo necesitas hacer un pedido con nosotros — cualquier producto cuenta."
    )


@app.post("/crm/api/enviar")
async def crm_enviar(request: Request):
    """Envía un mensaje al cliente desde el número de Clio (requiere token)."""
    u = _crm_usuario_de_request(request)
    if not u:
        raise HTTPException(status_code=401, detail="No autorizado")
    data = await request.json()
    telefono = data.get("telefono", "")
    mensaje = (data.get("mensaje", "") or "").strip()
    if not telefono or not mensaje:
        return {"ok": False, "error": "Falta teléfono o mensaje"}
    destino = _wa_destino(telefono)  # 521 + 10 dígitos (si no, WhatsApp no entrega)
    # Al responder, el asesor toma el control automáticamente (refresca el timer)
    await tomar_control(destino, u["nombre"])
    ok = await proveedor.enviar_mensaje(destino, mensaje)
    # Guardar en el historial marcado como asesor (para distinguirlo de Clio)
    await guardar_mensaje(destino, "assistant", f"[Asesor {u['nombre']}] {mensaje}")
    if not ok:
        logger.error(f"Envío desde panel falló a {destino}")
    return {"ok": ok}


@app.get("/crm/api/usuarios")
async def crm_usuarios(request: Request):
    """Lista usuarios del equipo (solo director/admin)."""
    u = _crm_usuario_de_request(request)
    if not u or u["usuario"] not in CRM_VEN_TODO:
        raise HTTPException(status_code=401, detail="No autorizado")
    return {"usuarios": await listar_usuarios_crm()}


@app.post("/crm/api/usuario/password")
async def crm_cambiar_password(request: Request):
    """Cambia la contraseña de un usuario del equipo (solo director/admin)."""
    u = _crm_usuario_de_request(request)
    if not u or u["usuario"] not in CRM_VEN_TODO:
        raise HTTPException(status_code=401, detail="No autorizado")
    data = await request.json()
    usuario = (data.get("usuario", "") or "").strip()
    nueva = (data.get("password", "") or "").strip()
    if not usuario or len(nueva) < 4:
        return {"ok": False, "error": "La contraseña debe tener al menos 4 caracteres"}
    ok = await cambiar_password_crm(usuario, nueva)
    return {"ok": ok}


@app.post("/crm/api/consolidar")
async def crm_consolidar(request: Request):
    """Une tarjetas duplicadas del mismo cliente (solo director/admin)."""
    u = _crm_usuario_de_request(request)
    if not u or u["usuario"] not in CRM_VEN_TODO:
        raise HTTPException(status_code=401, detail="No autorizado")
    resultado = await consolidar_duplicados_crm()
    return {"ok": True, **resultado}


@app.post("/crm/api/registro/{registro_id}")
async def crm_actualizar(registro_id: int, request: Request):
    """Actualiza estado, asesor o notas de un registro (requiere token)."""
    u = _crm_usuario_de_request(request)
    if not u:
        raise HTTPException(status_code=401, detail="No autorizado")
    data = await request.json()
    # El monto ($) solo lo toca un administrador (Chino/Tere o admin de sucursal), no los asesores.
    monto = data.get("monto") if _es_admin(u["usuario"]) else None
    ok = await actualizar_crm(
        registro_id,
        estado=data.get("estado"),
        asesor=data.get("asesor"),
        notas=data.get("notas"),
        alerta=data.get("alerta"),
        expres=data.get("expres"),
        monto=monto,
        sucursal=data.get("sucursal"),
        factura=data.get("factura"),
        facturado=data.get("facturado"),
        diseno=data.get("diseno"),
        diseno_aprobado=data.get("diseno_aprobado"),
    )
    return {"ok": ok}


@app.post("/crm/api/backfill-montos")
async def crm_backfill_montos(request: Request):
    """Recupera montos de pedidos viejos desde el texto (solo administrador)."""
    u = _crm_usuario_de_request(request)
    if not u:
        raise HTTPException(status_code=401, detail="No autorizado")
    if u["usuario"] not in CRM_VEN_TODO:
        raise HTTPException(status_code=403, detail="Solo administrador")
    res = await backfill_montos_crm()
    return {"ok": True, **res}


@app.get("/ruleta/ping")
async def ruleta_ping(nombre: str = "", telefono: str = "", premio: str = "", descripcion: str = "", juego: str = "ruleta"):
    """
    Endpoint GET sin preflight CORS — llamado via Image pixel desde la ruleta o el juego de gol.
    Registra al ganador y manda WhatsApp automáticamente. `juego`: "ruleta" o "gol".
    """
    import httpx as _httpx
    if not all([nombre, telefono, premio]):
        return {"ok": False}

    # Promo SOLO de Campeche: si el número ya es cliente conocido de Carmen/Mérida,
    # la ruleta/gol NO aplica → no registrar ni mandar premio.
    if await sucursal_crm_por_telefono(telefono) in ("Carmen", "Mérida"):
        logger.info(f"Ruleta/gol ignorada — {telefono} no es de Campeche (promo solo Campeche)")
        return {"ok": False, "razon": "solo_campeche"}

    tel = telefono.replace("+", "").replace(" ", "").replace("-", "")
    if len(tel) == 10:
        tel_wa = f"521{tel}"
    elif tel.startswith("52") and len(tel) == 12:
        tel_wa = f"521{tel[2:]}"
    elif tel.startswith("521") and len(tel) == 13:
        tel_wa = tel
    else:
        tel_wa = f"521{tel[-10:]}"

    ok = await registrar_ruleta(telefono, nombre, premio, descripcion)
    if not ok:
        return {"ok": False, "razon": "ya_jugo"}

    # Registrar en el CRM SIN dueño todavía. Si el cliente avanza (cotiza/compra),
    # se le asigna por el flujo normal. Si NO avanza en 2h, un job lo asigna a Erick
    # (ver asignar_ruletas_sin_avanzar). Así no se duplica si compra rápido.
    es_gol = juego == "gol"
    try:
        origen = "gol" if es_gol else "ruleta"
        await registrar_o_actualizar_crm(
            telefono=telefono,
            nombre=nombre,
            descripcion=f"🎁 Ganó en {origen}: {premio}. {descripcion}",
            tipo="ruleta",
            estado_minimo="nuevo",
        )
    except Exception as e:
        logger.error(f"Error registrando ruleta en CRM: {e}")

    msg_wa = _mensaje_premio(nombre, premio, es_gol)
    await guardar_mensaje(tel_wa, "assistant", msg_wa)

    # Mensaje al cliente ganador
    try:
        async with _httpx.AsyncClient(timeout=10.0) as client:
            await client.post(
                "https://gate.whapi.cloud/messages/text",
                json={"to": tel_wa, "body": msg_wa},
                headers={"Authorization": f"Bearer {proveedor.token}", "Content-Type": "application/json"},
            )
            logger.info(f"WhatsApp ruleta enviado a ganador {tel_wa}")
    except Exception as e:
        logger.error(f"Error WhatsApp ruleta ganador: {e}")

    # Copia al admin — registro de cupón con datos del ganador
    if ADMIN_WHATSAPP:
        msg_admin = (
            f"🎡 *RULETA — NUEVO GANADOR*\n\n"
            f"👤 *Nombre:* {nombre}\n"
            f"📱 *Teléfono:* {telefono}\n"
            f"🎁 *Premio:* {premio}\n"
            f"📝 {descripcion}"
        )
        try:
            async with _httpx.AsyncClient(timeout=10.0) as client:
                await client.post(
                    "https://gate.whapi.cloud/messages/text",
                    json={"to": ADMIN_WHATSAPP, "body": msg_admin},
                    headers={"Authorization": f"Bearer {proveedor.token}", "Content-Type": "application/json"},
                )
                logger.info(f"Copia ruleta enviada al admin: {nombre} / {telefono} / {premio}")
        except Exception as e:
            logger.error(f"Error copia ruleta admin: {e}")

    return {"ok": True}


@app.get("/anuncio")
async def redirigir_anuncio():
    """
    Redirige al WhatsApp de Clio desde anuncios de Facebook/Instagram.
    Usamos esta URL en los anuncios para evitar que Facebook bloquee el link directo de wa.me.
    El cliente llega con el mensaje 'vi su anuncio en Facebook' y Clio lo atiende automáticamente.
    """
    url_whatsapp = "https://wa.me/5219845576964?text=Hola%20LiTek%2C%20vi%20su%20anuncio%20en%20Facebook"
    logger.info("Redirigiendo visita de anuncio Facebook → WhatsApp Clio")
    return RedirectResponse(url=url_whatsapp, status_code=302)


@app.post("/reporte")
async def disparar_reporte(request: Request):
    """Dispara el reporte diario manualmente (para pruebas o uso bajo demanda)."""
    ok = await generar_reporte_diario(proveedor.token)
    return {"status": "ok" if ok else "error", "mensaje": "Reporte enviado al grupo" if ok else "Error al enviar"}


@app.post("/ruleta/verificar")
async def ruleta_verificar(request: Request):
    """Verifica si un número de teléfono ya participó en la ruleta."""
    data = await request.json()
    telefono = data.get("telefono", "").strip().replace("+", "").replace(" ", "")
    if not telefono:
        return {"ya_jugo": False}
    resultado = await verificar_ruleta(telefono)
    if resultado:
        return {
            "ya_jugo": True,
            "premio": resultado["premio"],
            "dias_faltantes": resultado.get("dias_faltantes", 0),
        }
    return {"ya_jugo": False}


@app.post("/ruleta/ganador")
async def ruleta_ganador(request: Request):
    """Registra al ganador de la ruleta y le manda WhatsApp con su premio."""
    import httpx as _httpx
    data = await request.json()
    nombre     = data.get("nombre", "").strip()
    telefono   = data.get("telefono", "").strip()
    premio     = data.get("premio", "").strip()
    descripcion = data.get("descripcion", "").strip()

    if not all([nombre, telefono, premio]):
        raise HTTPException(status_code=400, detail="Faltan datos")

    # Promo SOLO de Campeche: clientes ya conocidos de Carmen/Mérida no aplican.
    if await sucursal_crm_por_telefono(telefono) in ("Carmen", "Mérida"):
        logger.info(f"Ruleta ganador ignorado — {telefono} no es de Campeche")
        return {"ok": False, "mensaje": "La ruleta es solo para la sucursal de Campeche"}

    # Normalizar teléfono a formato Whapi México (521XXXXXXXXXX)
    tel = telefono.replace("+", "").replace(" ", "").replace("-", "")
    if len(tel) == 10:
        tel_wa = f"521{tel}"
    elif tel.startswith("52") and len(tel) == 12:
        tel_wa = f"521{tel[2:]}"
    elif tel.startswith("521") and len(tel) == 13:
        tel_wa = tel
    else:
        tel_wa = f"521{tel[-10:]}"

    # Registrar en DB (retorna False si ya participó)
    ok = await registrar_ruleta(telefono, nombre, premio, descripcion)
    if not ok:
        return {"ok": False, "mensaje": "Este número ya participó"}

    # Guardar mensaje en historial para que Clio tenga contexto
    msg_wa = _mensaje_premio(nombre, premio, False)
    await guardar_mensaje(tel_wa, "assistant", msg_wa)

    # Enviar WhatsApp via Whapi
    try:
        async with _httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(
                "https://gate.whapi.cloud/messages/text",
                json={"to": tel_wa, "body": msg_wa},
                headers={
                    "Authorization": f"Bearer {proveedor.token}",
                    "Content-Type": "application/json",
                },
            )
            logger.info(f"WhatsApp ruleta enviado a {tel_wa}: {r.status_code}")
    except Exception as e:
        logger.error(f"Error enviando WhatsApp ruleta: {e}")

    return {"ok": True}


@app.get("/webhook")
async def webhook_verificacion(request: Request):
    """Verificación GET del webhook (requerido por Meta Cloud API, no-op para Whapi)."""
    resultado = await proveedor.validar_webhook(request)
    if resultado is not None:
        return PlainTextResponse(str(resultado))
    return {"status": "ok"}


async def _reenviar_media(destino: str, tipo: str, media_url: str, media_id: str, caption: str = "") -> bool:
    """
    Reenvía una imagen/documento a otro número. Compatible con ambos proveedores:
    Meta usa media_id (preferido, no expira); Whapi usa media_url directa.
    """
    if tipo == "image":
        if media_id and hasattr(proveedor, "enviar_imagen_id"):
            return await proveedor.enviar_imagen_id(destino, media_id, caption=caption)
        if media_url and hasattr(proveedor, "enviar_imagen_url"):
            return await proveedor.enviar_imagen_url(destino, media_url, caption=caption)
    elif tipo == "document":
        if media_id and hasattr(proveedor, "enviar_documento_id"):
            return await proveedor.enviar_documento_id(destino, media_id, caption=caption)
        if media_url and hasattr(proveedor, "enviar_documento_url"):
            return await proveedor.enviar_documento_url(destino, media_url, caption=caption)
    return False


async def _procesar_mensaje(msg):
    """Procesa un mensaje en segundo plano (responder rápido evita reintentos de Whapi)."""
    try:
        # Grupo de alertas según la sucursal del cliente (Carmen → su grupo; resto → Campeche).
        # Esta variable LOCAL sustituye al grupo global para todas las alertas de este cliente.
        ASESOR_WHATSAPP = await _grupo_alerta_de(msg.telefono)

        # Guardar media y tipo ORIGINALES antes de modificarlos (para reenviar comprobantes)
        media_original = msg.media_url
        tipo_original = msg.tipo
        media_id_original = getattr(msg, "media_id", "")  # vacío en Whapi, lleno en Meta

        # Si es nota de voz, transcribir primero
        if msg.tipo in ("audio", "voice", "ptt") and msg.media_url:
            logger.info(f"Nota de voz de {msg.telefono} — transcribiendo...")
            msg.texto = await transcribir_audio(msg.media_url, proveedor.token)
            logger.info(f"Transcripción: {msg.texto}")

        # Si es documento, extraer texto
        elif msg.tipo == "document" and msg.media_url:
            logger.info(f"Documento de {msg.telefono} — extrayendo texto...")
            texto_doc = await leer_documento(msg.media_url, proveedor.token, msg.texto)
            msg.texto = texto_doc
            msg.tipo = "text"  # Procesar como texto normal con el contenido extraído

        if not msg.texto:
            return

        logger.info(f"Mensaje de {msg.telefono} [{msg.tipo}]: {msg.texto}")

        # Recordar la última imagen/documento del cliente (red de seguridad para comprobantes)
        if media_original and tipo_original in ("image", "document"):
            _ultima_imagen[msg.telefono] = f"{tipo_original}|{media_original}|{media_id_original}"

        # Obtener historial ANTES de guardar el mensaje actual
        historial = await obtener_historial(msg.telefono)
        es_primer_mensaje = len(historial) == 0

        # Registrar en el CRM SIN dueño todavía (un "hola" no se reparte):
        #  • Cliente NUEVO → en su primer mensaje
        #  • Cliente RECURRENTE → si regresa tras +6h de silencio
        # El reparto por turnos se hace MÁS ABAJO, solo cuando Clio da un precio
        # (producto identificado = interés real). Así no se reparten saludos vacíos.
        SILENCIO_RECURRENTE_MIN = 360  # 6 horas
        tel_limpio = msg.telefono.replace("@s.whatsapp.net", "")
        # Si estaba marcado 'no contestó' y vuelve a escribir → reactivar
        try:
            await reactivar_cliente_no_contesto(tel_limpio)
        except Exception as e:
            logger.error(f"Error reactivando cliente: {e}")
        try:
            if es_primer_mensaje:
                await registrar_o_actualizar_crm(
                    telefono=tel_limpio,
                    nombre=msg.nombre_perfil or "Cliente nuevo",
                    descripcion=f"🆕 Cliente nuevo. Primer mensaje: {msg.texto[:200]}",
                    tipo="cliente",
                    estado_minimo="nuevo",
                )
            else:
                mins = await minutos_desde_ultimo_mensaje(msg.telefono)
                if mins is not None and mins >= SILENCIO_RECURRENTE_MIN:
                    horas = int(mins // 60)
                    await registrar_o_actualizar_crm(
                        telefono=tel_limpio,
                        nombre=msg.nombre_perfil or "Cliente",
                        descripcion=f"🔄 Cliente regresó (tras {horas}h sin escribir): {msg.texto[:200]}",
                        tipo="cliente",
                        estado_minimo="nuevo",
                    )
        except Exception as e:
            logger.error(f"Error registrando cliente en CRM: {e}")

        # ── BANDEJA: si un asesor tomó el control, Clio NO responde ──────────
        # Guardamos el mensaje del cliente (para que el asesor lo vea) y salimos.
        # Defensivo: si el check falla, Clio responde igual (nunca se queda muda).
        try:
            en_modo_humano = await esta_en_modo_humano(msg.telefono)
        except Exception as e:
            logger.error(f"Error checando modo humano (Clio responde igual): {e}")
            en_modo_humano = False
        if en_modo_humano:
            await guardar_mensaje(msg.telefono, "user", msg.texto)
            logger.info(f"Modo humano activo para {msg.telefono} — Clio no responde")
            return

        # Contexto de ENTREGA: si el cliente tiene un pedido con hora prometida, le
        # decimos a Clio si YA PASÓ (para "ya puedes pasar") o si aún falta.
        _dias_sem = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
        ahora_local = datetime.now(_TZ_CAMP)
        ctx_entrega = f"Ahora es {_dias_sem[ahora_local.weekday()]} {ahora_local.strftime('%d/%m/%Y %H:%M')} (hora local)."
        try:
            _info = await info_pedido_por_telefono(tel_limpio)
            if _info and _info.get("entrega_en"):
                ent_local = _info["entrega_en"].replace(tzinfo=timezone.utc).astimezone(_TZ_CAMP)
                if ahora_local >= ent_local:
                    ctx_entrega += (
                        f"\n⚠️ ENTREGA: el cliente tiene un pedido cuya hora de entrega "
                        f"({ent_local.strftime('%d/%m a las %H:%M')}) YA PASÓ. Si pregunta por su "
                        f"pedido, dile con gusto que YA ESTÁ LISTO y puede pasar a recogerlo, y "
                        f"agrega al final la etiqueta [VA_EN_CAMINO] (invisible) para avisar al asesor."
                    )
                else:
                    ctx_entrega += (
                        f"\nENTREGA: el pedido del cliente estará listo el "
                        f"{ent_local.strftime('%d/%m a las %H:%M')} (aún no es la hora). Si pregunta, "
                        f"dile que estará listo a esa hora."
                    )
            elif _info and _info.get("estado") == "proceso" and _info.get("pagado_en"):
                # Pedido viejo: pagó pero no tiene hora calculada → estimar y guardar.
                pago_local = _info["pagado_en"].replace(tzinfo=timezone.utc).astimezone(_TZ_CAMP)
                ctx_entrega += (
                    f"\nENTREGA (estimar): el cliente YA PAGÓ su pedido el "
                    f"{pago_local.strftime('%d/%m a las %H:%M')}"
                    + (" (es EXPRÉS)" if _info.get("expres") else "")
                    + f". Producto/pedido: \"{_info['descripcion'][:120]}\". AÚN no tiene hora de entrega "
                    f"calculada. Si pregunta por su pedido: CALCULA la entrega (políticas + exprés + "
                    f"horas hábiles desde que pagó) y dale el estatus CONCRETO (si ya pasó la hora, dile "
                    f"que ya está listo + [VA_EN_CAMINO]; si no, dile a qué hora estará). Agrega "
                    f"[ENTREGA:AAAA-MM-DD HH:MM] al final para guardarla. NO escales si puedes darle el estatus."
                )
        except Exception as e:
            logger.error(f"Error contexto entrega: {e}")

        # Generar respuesta con Claude (con soporte de imagen si aplica).
        # `señales` nos dice si Clio cotizó en este turno (producto identificado).
        señales: dict = {}
        respuesta = await generar_respuesta(
            msg.texto,
            historial,
            media_url=msg.media_url,
            whapi_token=proveedor.token,
            tipo=msg.tipo,
            nombre_perfil=msg.nombre_perfil,
            señales=señales,
            contexto_extra=ctx_entrega,
        )

        # Si Clio dio un precio → producto identificado → repartir por turnos
        # (Anna/Brayan). Solo asigna dueño si la tarjeta aún no tiene uno.
        if señales.get("cotizo"):
            try:
                producto = señales.get("producto", "")
                asesor_prod = asesor_por_producto(producto)
                await registrar_o_actualizar_crm(
                    telefono=tel_limpio,
                    nombre=msg.nombre_perfil or "Cliente",
                    descripcion=f"💲 Cotización ({producto or 'producto'}): {msg.texto[:140]}",
                    tipo="cliente",
                    estado_minimo="asignado",  # cotizó → ya tiene dueño → pasa a Asignados
                    asesor_si_nuevo=asesor_prod,  # dueño fijo según el producto
                    cotizo=True,  # sella la hora de cotización → 'no concretado' si calla 10h
                )
                # Mérida: reparto por producto (lonas/viniles→Edith, otros→Jadiel).
                if await sucursal_crm_por_telefono(tel_limpio) == "Mérida":
                    await rutear_asesor_merida(tel_limpio, producto)
            except Exception as e:
                logger.error(f"Error asignando lead cotizado en CRM: {e}")

        # Si Clio mandó la CUENTA de pago en este turno → sella la hora.
        # Si el cliente calla +2h sin pagar, pasará a 'Esperando pago' (lead caliente).
        # Se detecta por los marcadores que SIEMPRE acompañan los datos bancarios.
        dio_cuenta = bool(re.search(r'CLABE|Cuenta:\s*\d|Tarjeta:\s*\d', respuesta, re.IGNORECASE))
        if dio_cuenta:
            try:
                await registrar_o_actualizar_crm(
                    telefono=tel_limpio,
                    nombre=msg.nombre_perfil or "Cliente",
                    descripcion="💳 Se le dio la cuenta de pago.",
                    tipo="cliente",
                    estado_minimo="asignado",
                    dio_cuenta=True,  # sella la hora → 'esperando pago' si calla 2h
                )
            except Exception as e:
                logger.error(f"Error sellando cuenta dada en CRM: {e}")

        # Guardar usuario y respuesta en memoria
        # Si era imagen, guardar con contexto para que no se pierda en turnos siguientes
        if msg.tipo == "image" and msg.media_url:
            texto_guardado = f"[El cliente envió una imagen. Descripción/caption: '{msg.texto}'. Clio analizó la imagen con visión.]"
        else:
            texto_guardado = msg.texto
        await guardar_mensaje(msg.telefono, "user", texto_guardado)
        await guardar_mensaje(msg.telefono, "assistant", respuesta)

        # Detectar escalación [ESCALAR:area]
        area_escalar = None
        if "[ESCALAR:" in respuesta:
            match = re.search(r'\[ESCALAR:(\w+)\]', respuesta)
            if match:
                area_escalar = match.group(1)
                respuesta = re.sub(r'\[ESCALAR:\w+\]', '', respuesta).strip()

        # Detectar [ALERTA:motivo] — Clio prende la alerta ⚠️ en el panel
        # (cliente frustrado, comprobante no cuadra, falta archivo, etc.)
        match_alerta = re.search(r'\[ALERTA:([^\]]+)\]', respuesta)
        if match_alerta:
            motivo_alerta = match_alerta.group(1).strip()
            respuesta = re.sub(r'\[ALERTA:[^\]]+\]', '', respuesta).strip()
            try:
                await marcar_alerta_crm(msg.telefono, f"⚠️ {motivo_alerta}")
            except Exception as e:
                logger.error(f"Error marcando alerta CRM: {e}")

        # Detectar [ENTREGA:YYYY-MM-DD HH:MM] — Clio calculó la fecha/hora de entrega
        # (con pago + archivo listos). La guardamos para comparar después.
        match_entrega = re.search(r'\[ENTREGA:\s*(\d{4}-\d{2}-\d{2})[ T](\d{1,2}):(\d{2})\]', respuesta)
        if match_entrega:
            respuesta = re.sub(r'\[ENTREGA:[^\]]*\]', '', respuesta).strip()
            try:
                y, mo, d = [int(x) for x in match_entrega.group(1).split("-")]
                hh, mm = int(match_entrega.group(2)), int(match_entrega.group(3))
                ent_local = datetime(y, mo, d, hh, mm, tzinfo=_TZ_CAMP)
                ent_utc = ent_local.astimezone(timezone.utc).replace(tzinfo=None)
                await guardar_entrega_crm(tel_limpio, ent_utc)
                logger.info(f"Entrega guardada para {tel_limpio}: {ent_local.strftime('%d/%m %H:%M')}")
            except Exception as e:
                logger.error(f"Error guardando entrega: {e}")

        # Detectar [VA_EN_CAMINO] — el cliente va a recoger su pedido ya listo → avisar al asesor
        if "[VA_EN_CAMINO]" in respuesta:
            respuesta = respuesta.replace("[VA_EN_CAMINO]", "").strip()
            try:
                _suc_vc = await sucursal_crm_por_telefono(tel_limpio)
                _grupo_vc = await _grupo_alerta_de(msg.telefono)
                aviso_vc = (
                    f"🚶 *CLIENTE VA EN CAMINO — CLIO*\n\n"
                    f"👤 {msg.nombre_perfil or 'Cliente'}\n"
                    f"📱 wa.me/{''.join(c for c in tel_limpio if c.isdigit())}\n"
                    f"🏢 {_suc_vc}\n\n"
                    f"Su pedido ya está listo y va en camino a recogerlo. 🙌"
                )
                await proveedor.enviar_mensaje(_grupo_vc, aviso_vc)
                _dueno_vc = await asesor_crm_por_telefono(tel_limpio)
                await _avisar_personal(_dueno_vc, aviso_vc)
            except Exception as e:
                logger.error(f"Error avisando va en camino: {e}")

        # Detectar [EXPRES] — Clio marca el pedido como exprés (prioritario) en el panel
        if "[EXPRES]" in respuesta:
            respuesta = respuesta.replace("[EXPRES]", "").strip()
            try:
                await marcar_expres_crm(msg.telefono, True)
            except Exception as e:
                logger.error(f"Error marcando exprés CRM: {e}")

        # Detectar [DISENO_APROBADO] — el CLIENTE aprobó el diseño en el chat. Marca ✅
        # en la tarjeta (sigue con 🎨) y avisa al asesor dueño que ya está listo a producir.
        if "[DISENO_APROBADO]" in respuesta:
            respuesta = respuesta.replace("[DISENO_APROBADO]", "").strip()
            try:
                tel_apro = msg.telefono.replace("@s.whatsapp.net", "")
                await marcar_diseno_aprobado_crm(msg.telefono, True)
                _dueno_apro = await asesor_crm_por_telefono(tel_apro)
                _suc_apro = await sucursal_crm_por_telefono(msg.telefono)
                aviso_apro = (
                    f"✅ *DISEÑO APROBADO — CLIO*\n\n"
                    f"👤 *Cliente:* {msg.nombre_perfil or 'Cliente'}\n"
                    f"🙋 *Atender:* {_dueno_apro or 'sin asignar'}\n"
                    f"🏢 *Sucursal:* {_suc_apro}\n"
                    f"📱 wa.me/{''.join(c for c in tel_apro if c.isdigit())}\n\n"
                    f"El cliente APROBÓ su diseño. Ya está listo para producir/entregar. 🙌"
                )
                if ASESOR_WHATSAPP:
                    await proveedor.enviar_mensaje(ASESOR_WHATSAPP, aviso_apro)
                await _avisar_personal(_dueno_apro, aviso_apro)
            except Exception as e:
                logger.error(f"Error en diseño aprobado CRM: {e}")

        # Detectar [DISENO] — el pedido incluye que NOSOTROS hagamos/modifiquemos el
        # diseño (no trae arte listo). Marca 🎨 en la tarjeta para que el equipo lo
        # identifique; al confirmarse el pago, si es de Carmen, pasa a Brayan (ver pago).
        if "[DISENO]" in respuesta:
            respuesta = respuesta.replace("[DISENO]", "").strip()
            try:
                await marcar_diseno_crm(msg.telefono, True)
            except Exception as e:
                logger.error(f"Error marcando diseño CRM: {e}")

        # Detectar [SUCURSAL:Campeche|Mérida|Carmen] — la sucursal del cliente.
        # En Carmen, además asigna a Jadiel (dueño por defecto del equipo Carmen).
        match_suc = re.search(r'\[SUCURSAL:\s*([^\]]+)\]', respuesta, re.IGNORECASE)
        if match_suc:
            respuesta = re.sub(r'\[SUCURSAL:[^\]]*\]', '', respuesta, flags=re.IGNORECASE).strip()
            suc = match_suc.group(1).strip().capitalize()
            suc = {"Merida": "Mérida", "Mérida": "Mérida", "Campeche": "Campeche", "Carmen": "Carmen"}.get(suc, suc)
            try:
                await guardar_sucursal_crm(msg.telefono, suc)
                logger.info(f"Sucursal de {msg.telefono}: {suc}")
            except Exception as e:
                logger.error(f"Error guardando sucursal CRM: {e}")

        # Detectar [MONTO:numero] — total $ del pedido confirmado (para totalizar ventas)
        match_monto = re.search(r'\[MONTO:\s*\$?([0-9][0-9.,]*)\]', respuesta)
        if match_monto:
            respuesta = re.sub(r'\[MONTO:[^\]]*\]', '', respuesta).strip()
            try:
                # Quitar separadores de miles (comas) antes de convertir
                monto_val = float(match_monto.group(1).replace(",", ""))
                await guardar_monto_crm(msg.telefono, monto_val)
                logger.info(f"Monto de pedido de {msg.telefono}: ${monto_val}")
            except Exception as e:
                logger.error(f"Error guardando monto CRM: {e}")

        # Detectar [CALIFICACION:nivel|comentario] — el cliente calificó el servicio
        # nivel = bueno|regular|malo. El comentario es opcional (tras el "|").
        # Se guarda en el CRM; si es malo/regular, prende la alerta ⚠️ (queja).
        match_calif = re.search(r'\[CALIFICACION:([^\]]+)\]', respuesta, re.IGNORECASE)
        if match_calif:
            contenido_calif = match_calif.group(1).strip()
            respuesta = re.sub(r'\[CALIFICACION:[^\]]+\]', '', respuesta, flags=re.IGNORECASE).strip()
            partes = contenido_calif.split("|", 1)
            nivel = partes[0].strip().lower()
            comentario = partes[1].strip() if len(partes) > 1 else ""
            try:
                await guardar_calificacion_crm(msg.telefono, nivel, comentario)
                logger.info(f"Calificación de {msg.telefono}: {nivel} — {comentario}")
            except Exception as e:
                logger.error(f"Error guardando calificación CRM: {e}")

        # Detectar [MOTIVO] — resumen conciso del motivo de escalación (lo genera Clio)
        motivo_escalacion = None
        match_motivo = re.search(r'\[MOTIVO\](.*?)\[/MOTIVO\]', respuesta, re.DOTALL)
        if match_motivo:
            motivo_escalacion = match_motivo.group(1).strip()
            respuesta = re.sub(r'\[MOTIVO\].*?\[/MOTIVO\]', '', respuesta, flags=re.DOTALL).strip()

        # Detectar [COPIA_ADMIN] — copia de cotización al dueño
        enviar_copia_admin = False
        if "[COPIA_ADMIN]" in respuesta:
            enviar_copia_admin = True
            respuesta = respuesta.replace("[COPIA_ADMIN]", "").strip()

        # Si este turno es de PAGO (tiene comprobante), NO tratar el archivo como diseño
        hay_comprobante_en_turno = "[COMPROBANTE]" in respuesta

        # Detectar [GUARDAR_ARCHIVO] — el cliente mandó su diseño/arte para imprimir
        # (solo si NO es un turno de pago — un comprobante no es un diseño)
        if "[GUARDAR_ARCHIVO]" in respuesta:
            respuesta = respuesta.replace("[GUARDAR_ARCHIVO]", "").strip()
            if media_original and tipo_original in ("image", "document") and not hay_comprobante_en_turno:
                # ¿El cliente ya pagó y solo faltaba el archivo? → reenviar al asesor YA
                if msg.telefono in _pago_sin_archivo:
                    nombre_cli = _pago_sin_archivo.pop(msg.telefono) or (msg.nombre_perfil or "Cliente")
                    cap = f"🎨 Archivo para imprimir (el que faltaba) — {nombre_cli}"
                    try:
                        await _reenviar_media(ASESOR_WHATSAPP, tipo_original, media_original, media_id_original, caption=cap)
                        await proveedor.enviar_mensaje(
                            ASESOR_WHATSAPP,
                            f"✅ {nombre_cli} ya mandó su archivo para imprimir. Pedido completo, listo para producción."
                        )
                        # Quitar la alerta del panel: ya mandó el archivo
                        try:
                            await marcar_alerta_crm(msg.telefono, "")
                        except Exception:
                            pass
                        logger.info(f"Archivo tardío reenviado al asesor para {msg.telefono}")
                    except Exception as e:
                        logger.error(f"Error reenviando archivo tardío: {e}")
                else:
                    # Aún no paga — guardar para reenviar cuando confirme el pago
                    _archivo_diseno[msg.telefono] = f"{tipo_original}|{media_original}|{media_id_original}"
                    logger.info(f"Archivo de diseño guardado para {msg.telefono}")

        # Detectar [COMPROBANTE] — pedido confirmado + comprobante para el asesor
        # Formato: [COMPROBANTE]resumen del pedido[/COMPROBANTE]
        resumen_pedido = None
        match_comp = re.search(r'\[COMPROBANTE\](.*?)\[/COMPROBANTE\]', respuesta, re.DOTALL)
        if match_comp:
            resumen_pedido = match_comp.group(1).strip()
            respuesta = re.sub(r'\[COMPROBANTE\].*?\[/COMPROBANTE\]', '', respuesta, flags=re.DOTALL).strip()

        # Detectar comando de imagen [IMAGEN:nombre]
        imagen_nombre = None
        if "[IMAGEN:" in respuesta:
            match = re.search(r'\[IMAGEN:(\w+)\]', respuesta)
            if match:
                imagen_nombre = match.group(1)
                respuesta = re.sub(r'\[IMAGEN:\w+\]', '', respuesta).strip()

        # Detectar comando de sticker explícito [STICKER:nombre]
        sticker_nombre = None
        if "[STICKER:" in respuesta:
            match = re.search(r'\[STICKER:(\w+)\]', respuesta)
            if match:
                sticker_nombre = match.group(1)
                respuesta = re.sub(r'\[STICKER:\w+\]', '', respuesta).strip()

        # Auto-detectar despedida y asignar sticker automáticamente
        if not sticker_nombre and hasattr(proveedor, 'enviar_sticker'):
            FRASES_CIERRE = [
                "aquí estaremos", "con gusto te cotizamos", "hasta luego",
                "que tengas", "buen día", "buenas noches", "buenas tardes",
                "nos vemos", "cuídate", "cualquier duda aquí", "estamos para",
                "cuando gustes regresa", "con mucho gusto", "fue un placer",
            ]
            resp_lower = respuesta.lower()
            if any(frase in resp_lower for frase in FRASES_CIERRE):
                sticker_nombre = "logo"

        # Enviar respuesta por WhatsApp
        await proveedor.enviar_mensaje(msg.telefono, respuesta)

        # Enviar imagen de catálogo si aplica
        if imagen_nombre and hasattr(proveedor, 'enviar_imagen'):
            await proveedor.enviar_imagen(msg.telefono, imagen_nombre)
            logger.info(f"Imagen enviada: {imagen_nombre}")

        # Enviar sticker si aplica
        if sticker_nombre and hasattr(proveedor, 'enviar_sticker'):
            await proveedor.enviar_sticker(msg.telefono, sticker_nombre)
            logger.info(f"Sticker enviado: {sticker_nombre}")

        # Sticker de bienvenida — primer mensaje del cliente → el LOGO de LiTek
        elif es_primer_mensaje and hasattr(proveedor, 'enviar_sticker'):
            await proveedor.enviar_sticker(msg.telefono, "logo")
            logger.info("Sticker de bienvenida enviado: logo")

        # Sticker CALIDAD PERRONA — cuando el cliente señaló producto (hubo cotización)
        elif señales.get("cotizo") and hasattr(proveedor, 'enviar_sticker'):
            await proveedor.enviar_sticker(msg.telefono, "calidad")
            logger.info("Sticker enviado: calidad (cotización)")

        logger.info(f"Respuesta a {msg.telefono}: {respuesta}")

        # Enviar copia de cotización al dueño (productos que no son lonas)
        if enviar_copia_admin and ADMIN_WHATSAPP:
            numero_limpio = msg.telefono.replace('@s.whatsapp.net', '')
            if numero_limpio.startswith("521") and len(numero_limpio) == 13:
                area_tel = numero_limpio[3:6]
                num_tel = numero_limpio[6:]
                numero_display = f"+52 {area_tel} {num_tel[:3]} {num_tel[3:]}"
            else:
                numero_display = f"+{numero_limpio}"
            msg_copia = (
                f"📋 *COTIZACIÓN — CLIO*\n\n"
                f"📱 *Cliente:* {numero_display}\n"
                f"💬 *Pidió:* {msg.texto[:300]}\n\n"
                f"📤 *Clio respondió:*\n{respuesta}"
            )
            try:
                import httpx as _httpx
                async with _httpx.AsyncClient(timeout=10.0) as _client:
                    await _client.post(
                        "https://gate.whapi.cloud/messages/text",
                        json={"to": ADMIN_WHATSAPP, "body": msg_copia},
                        headers={
                            "Authorization": f"Bearer {proveedor.token}",
                            "Content-Type": "application/json",
                        },
                    )
                logger.info(f"Copia de cotización enviada al admin ({ADMIN_WHATSAPP})")
            except Exception as e:
                logger.error(f"Error enviando copia al admin: {e}")

        # Dedup: si ya se confirmó un pedido de este cliente hace poco, no reenviar
        # (Clio a veces genera [COMPROBANTE] en varios mensajes seguidos)
        if resumen_pedido and ASESOR_WHATSAPP:
            ultimo = _pedido_confirmado_ts.get(msg.telefono)
            if ultimo and (datetime.utcnow() - ultimo) < timedelta(minutes=VENTANA_PEDIDO_MIN):
                logger.info(f"Pedido confirmado duplicado ignorado para {msg.telefono}")
                resumen_pedido = None
            else:
                _pedido_confirmado_ts[msg.telefono] = datetime.utcnow()

        # Pedido confirmado + comprobante → reenviar al asesor (Anna)
        if resumen_pedido and ASESOR_WHATSAPP:
            numero_limpio = msg.telefono.replace('@s.whatsapp.net', '')
            if numero_limpio.startswith("521") and len(numero_limpio) == 13:
                num_display = f"+52 {numero_limpio[3:6]} {numero_limpio[6:9]} {numero_limpio[9:]}"
            else:
                num_display = f"+{numero_limpio}"
            nombre_cli = msg.nombre_perfil or "Cliente"
            # Registrar el pedido en el CRM → la tarjeta del cliente pasa a "En proceso".
            # Si la tarjeta ya tenía dueño (por el producto que cotizó/escaló), lo conserva.
            # Si llegó directo a pedido sin dueño, default Anna (impresión general).
            try:
                await registrar_o_actualizar_crm(
                    telefono=numero_limpio,
                    nombre=nombre_cli,
                    descripcion=resumen_pedido,
                    tipo="pedido",
                    estado_minimo="proceso",
                    asesor_si_nuevo="Anna",
                )
                # Alerta automática si el pedido entró FUERA de horario (según su sucursal)
                suc_cli = await sucursal_crm_por_telefono(numero_limpio)
                if not es_horario_atencion(suc_cli):
                    await marcar_alerta_crm(numero_limpio, "⚠️ Pidió fuera de horario")
                # ¿El pedido es CON factura? → marcar 🧾 para que el equipo de la
                # sucursal la haga (el resumen trae "Factura: Sí/No").
                rl = (resumen_pedido or "").lower()
                if re.search(r'factura:\s*s[ií]\b', rl) or "con factura" in rl:
                    await marcar_factura_crm(numero_limpio)
                    # Aviso de FACTURA al responsable de la sucursal (Campeche→Tere, Carmen/Mérida→Leo)
                    destino_fact = FACTURA_WHATSAPP.get(suc_cli, TERE_WHATSAPP)
                    aviso_fact = (
                        f"🧾 *FACTURA POR HACER — CLIO*\n\n"
                        f"👤 *Cliente:* {nombre_cli}\n"
                        f"📱 *WhatsApp:* {num_display}\n"
                        f"🏢 *Sucursal:* {suc_cli}\n"
                        f"📋 *Pedido:* {resumen_pedido[:200]}\n\n"
                        f"El cliente pidió FACTURA. Por favor genérala. 🙌"
                    )
                    try:
                        await proveedor.enviar_mensaje(destino_fact, aviso_fact)  # privado a Tere/Leo
                        if ASESOR_WHATSAPP:                                        # + al grupo de la sucursal
                            await proveedor.enviar_mensaje(ASESOR_WHATSAPP, aviso_fact)
                    except Exception as e:
                        logger.error(f"Error avisando factura a {suc_cli}: {e}")
            except Exception as e:
                logger.error(f"Error registrando pedido en CRM: {e}")

            # Pedido PAGADO marcado con diseño (🎨) → se le AVISA a ERICK (diseñador de todo
            # LiTek) para que lo haga. El pedido SIGUE siendo del asesor (Erick nunca es dueño).
            try:
                info_dis = await canalizar_diseno_disenador(numero_limpio)
                if info_dis:
                    logger.info(f"Pedido pagado con diseño → aviso a Erick: {numero_limpio}")
                    _asesor_dis = info_dis['asesor'] or 'su asesor'
                    aviso_erick = (
                        f"🎨 *DISEÑO POR HACER — CLIO*\n\n"
                        f"👤 *Cliente:* {info_dis['nombre']}\n"
                        f"📱 *WhatsApp:* {num_display}\n"
                        f"📋 *Pedido:* {info_dis['descripcion'][:200]}\n"
                        f"🏢 *Sucursal:* {info_dis['sucursal']}\n"
                        f"🙋 *Asesor del pedido:* {info_dis['asesor'] or 'sin asignar'}\n\n"
                        f"Favor de realizar el diseño. El pedido es de *{_asesor_dis}* "
                        f"(tú solo lo diseñas); cuando esté listo, avísale. 🙌"
                    )
                    await proveedor.enviar_mensaje(ERICK_WHATSAPP, aviso_erick)  # privado a Erick
                    if ASESOR_WHATSAPP:                                          # + al grupo de la sucursal
                        await proveedor.enviar_mensaje(ASESOR_WHATSAPP, aviso_erick)
            except Exception as e:
                logger.error(f"Error avisando diseño a Erick: {e}")
            # Dueño de la tarjeta (a quién le toca atender el pedido).
            _dueno = await asesor_crm_por_telefono(numero_limpio)
            msg_asesor = (
                f"✅ *PEDIDO CONFIRMADO — CLIO*\n\n"
                f"👤 *Cliente:* {nombre_cli}\n"
                f"🙋 *Atender:* {_dueno or 'sin asignar'}\n"
                f"🏢 *Sucursal:* {suc_cli}\n"
                f"📱 *WhatsApp:* {num_display}\n\n"
                f"{resumen_pedido}\n\n"
                f"👉 Contactar: wa.me/{numero_limpio}"
            )
            try:
                await proveedor.enviar_mensaje(ASESOR_WHATSAPP, msg_asesor)
                logger.info(f"Pedido confirmado enviado al asesor ({ASESOR_WHATSAPP})")
                # Además, en privado al asesor dueño de la tarjeta (el grupo queda de seguimiento).
                try:
                    await _avisar_personal(_dueno, msg_asesor)
                except Exception as e:
                    logger.error(f"Error aviso privado pedido: {e}")

                # 1) Reenviar el COMPROBANTE de pago.
                # Si el turno actual trae imagen/doc, ese es el comprobante.
                # Si no (Clio confirmó el pago en un turno de texto), usar la última
                # imagen que mandó el cliente (la mandó un mensaje antes).
                comp_tipo, comp_url, comp_id = "", "", ""
                if media_original and tipo_original in ("image", "document"):
                    comp_tipo, comp_url, comp_id = tipo_original, media_original, media_id_original
                else:
                    ult = _ultima_imagen.get(msg.telefono, "")
                    if ult:
                        partes = ult.split("|", 2)
                        comp_tipo = partes[0] if len(partes) > 0 else ""
                        comp_url = partes[1] if len(partes) > 1 else ""
                        comp_id = partes[2] if len(partes) > 2 else ""

                if (comp_url or comp_id) and comp_tipo in ("image", "document"):
                    ok_comp = await _reenviar_media(ASESOR_WHATSAPP, comp_tipo, comp_url, comp_id, caption=f"🧾 Comprobante de pago — {nombre_cli}")
                    logger.info(f"Comprobante reenviado al asesor: {ok_comp}")
                else:
                    logger.warning(f"No se encontró comprobante para {msg.telefono}")

                # 2) Reenviar el ARCHIVO DE DISEÑO para imprimir (guardado de un turno anterior)
                archivo_guardado = _archivo_diseno.pop(msg.telefono, None)
                partes_a = (archivo_guardado or "").split("|", 2)
                tipo_arch = partes_a[0] if len(partes_a) > 0 else ""
                url_arch = partes_a[1] if len(partes_a) > 1 else ""
                id_arch = partes_a[2] if len(partes_a) > 2 else ""
                # Red de seguridad: si el "diseño" guardado es el MISMO archivo que el
                # comprobante, es un comprobante mal etiquetado — no reenviar duplicado
                if archivo_guardado and ((url_arch and url_arch == comp_url) or (id_arch and id_arch == comp_id)):
                    logger.info("Archivo guardado == comprobante — se omite (evita duplicado)")
                    archivo_guardado = None
                if archivo_guardado:
                    ok_arch = await _reenviar_media(ASESOR_WHATSAPP, tipo_arch, url_arch, id_arch, caption=f"🎨 Archivo para imprimir — {nombre_cli}")
                    logger.info(f"Archivo de diseño reenviado al asesor: {ok_arch}")
                else:
                    # El cliente pagó pero NO ha mandado su archivo de impresión
                    _pago_sin_archivo[msg.telefono] = nombre_cli
                    aviso_falta = (
                        f"⚠️ *{nombre_cli} YA PAGÓ pero falta su archivo para imprimir.*\n"
                        f"🏢 Sucursal: {suc_cli} · 🙋 Atender: {_dueno or 'sin asignar'}\n"
                        f"Clio se lo está pidiendo. Si no lo manda pronto, contáctalo: wa.me/{numero_limpio}"
                    )
                    await proveedor.enviar_mensaje(ASESOR_WHATSAPP, aviso_falta)  # grupo
                    await _avisar_personal(_dueno, aviso_falta)                   # + privado al dueño
                    # Prender alerta ⚠️ en el panel para que el asesor lo revise
                    try:
                        await marcar_alerta_crm(msg.telefono, "⚠️ Pagó pero falta archivo")
                    except Exception as e:
                        logger.error(f"Error marcando alerta pago-sin-archivo: {e}")
                    logger.info(f"Pago sin archivo — avisado al asesor, pendiente {msg.telefono}")

                # Limpiar la última imagen ya usada como comprobante
                _ultima_imagen.pop(msg.telefono, None)
            except Exception as e:
                logger.error(f"Error enviando pedido al asesor: {e}")

        # Enviar alerta al asesor si hay escalación
        if area_escalar:
            # Construir resumen limpio del historial (sin duplicados ni texto de imágenes)
            def _limpiar_contenido(texto: str) -> str:
                """Simplifica el texto de imágenes y recorta."""
                if "[El cliente envió una imagen" in texto:
                    return "[imagen]"
                if "[ESCALAR:" in texto or "[IMAGEN:" in texto or "[STICKER:" in texto:
                    import re as _re
                    texto = _re.sub(r'\[\w+:[^\]]*\]', '', texto).strip()
                return texto[:120]

            resumen_lineas = []
            mensajes_vistos = set()
            for h in historial[-8:]:
                contenido = _limpiar_contenido(h['content'])
                if not contenido or contenido in mensajes_vistos:
                    continue
                mensajes_vistos.add(contenido)
                rol = "👤 Cliente" if h["role"] == "user" else "🤖 Clio"
                resumen_lineas.append(f"{rol}: {contenido}")

            # Agregar el mensaje actual solo si no es una imagen ya registrada
            texto_actual = _limpiar_contenido(msg.texto)
            if texto_actual and texto_actual not in mensajes_vistos:
                resumen_lineas.append(f"👤 Cliente: {texto_actual}")

            # Usar el motivo conciso de Clio si existe; si no, el historial resumido
            if motivo_escalacion:
                resumen_completo = motivo_escalacion
            else:
                resumen_completo = "\n".join(resumen_lineas) if resumen_lineas else "(sin historial)"

            # ¿De qué sucursal es el cliente? Cada sucursal se atiende con su propio equipo.
            suc_cliente = await sucursal_crm_por_telefono(msg.telefono)
            es_carmen = (suc_cliente == "Carmen")
            es_merida = (suc_cliente == "Mérida")
            _tel_esc = msg.telefono.replace("@s.whatsapp.net", "")

            # ¿Quién atiende? El default del área (letreros→Erick, director→Chino, asesor→Anna,
            # administracion→Tere). Excepción: el "asesor" depende de la sucursal —
            # Mérida lo atiende Edith; Carmen, su dueño actual (Alan/Jadiel).
            _atender = AREAS.get(area_escalar, {}).get("nombre", "")
            if area_escalar in ("asesor", "letreros"):
                # 'asesor' y 'letreros' (diseño) los atiende el equipo de la sucursal.
                # Erick NO entra en la escalación (pre-venta): solo se le avisa cuando ya PAGÓ.
                if es_merida:
                    _atender = "Edith"
                elif es_carmen:
                    _atender = await asesor_crm_por_telefono(_tel_esc) or "el equipo de Carmen"
                elif area_escalar == "letreros":
                    _atender = "Brayan"   # Campeche: letreros lo ve Brayan
                else:
                    _atender = "Anna"

            # La alerta va al grupo de SU sucursal (ASESOR_WHATSAPP local ya es el correcto).
            await enviar_alerta_asesor(
                area=area_escalar,
                telefono_cliente=msg.telefono,
                resumen=resumen_completo,
                whapi_token=proveedor.token,
                nombre_cliente=msg.nombre_perfil,
                grupo=ASESOR_WHATSAPP,
                atender=_atender,
                sucursal=suc_cliente,
            )
            logger.info(f"Escalación enviada a área: {area_escalar} (sucursal: {suc_cliente})")

            # Quejas de MÉRIDA (escalación al director) → también directo a Chino.
            if suc_cliente == "Mérida" and area_escalar == "director":
                try:
                    tel_q = msg.telefono.replace("@s.whatsapp.net", "")
                    aviso_chino = (
                        f"😠 *QUEJA / MÉRIDA — CLIO*\n\n"
                        f"👤 {msg.nombre_perfil or 'Cliente'}\n"
                        f"📱 wa.me/{''.join(c for c in tel_q if c.isdigit())}\n\n"
                        f"{resumen_completo[:300]}"
                    )
                    await proveedor.enviar_mensaje(CHINO_WHATSAPP, aviso_chino)
                except Exception as e:
                    logger.error(f"Error avisando queja Mérida a Chino: {e}")

            # Registrar la escalación en el CRM, asignada al asesor correspondiente.
            # En Carmen NO se reasigna: el dueño por turnos (Alan/Jadiel) se queda.
            _asesor_area = {
                "asesor": "Anna", "director": "Chino",
                "letreros": "Brayan", "administracion": "Tere",
            }.get(area_escalar, "")
            # Mérida: el asesor general Y los letreros/diseño los lleva Edith (no los de Campeche).
            if area_escalar in ("asesor", "letreros") and es_merida:
                _asesor_area = "Edith"
            if es_carmen:
                _asesor_area = ""
            try:
                await registrar_o_actualizar_crm(
                    telefono=msg.telefono.replace("@s.whatsapp.net", ""),
                    nombre=msg.nombre_perfil or "Cliente",
                    descripcion=resumen_completo,
                    tipo="escalacion",
                    estado_minimo="asignado",
                    asesor=_asesor_area,
                )
                # Diseño/letreros: marcar 🎨 para que Erick lo VEA en su panel (3 sucursales).
                # OJO: a Erick NO se le avisa aquí — solo cuando el pedido esté PAGADO.
                if area_escalar == "letreros":
                    await marcar_diseno_crm(msg.telefono, True)
                # Aviso en PRIVADO al asesor dueño (además del grupo, que es seguimiento).
                _tel_esc = msg.telefono.replace("@s.whatsapp.net", "")
                _dueno_esc = await asesor_crm_por_telefono(_tel_esc)
                aviso_esc = (
                    f"🔔 *ESCALACIÓN — CLIO*\n\n"
                    f"👤 {msg.nombre_perfil or 'Cliente'}\n"
                    f"🏢 *Sucursal:* {suc_cliente} · 🙋 *Atender:* {_atender or _dueno_esc or 'equipo'}\n"
                    f"📱 wa.me/{''.join(c for c in _tel_esc if c.isdigit())}\n\n"
                    f"{resumen_completo[:300]}"
                )
                await _avisar_personal(_dueno_esc, aviso_esc)
            except Exception as e:
                logger.error(f"Error registrando escalación en CRM: {e}")

            # Foto del asesor al cliente según el área. En Carmen y Mérida NO mandamos foto
            # (los asesores de Campeche no aplican ahí). Solo si Clio no la envió ya.
            if hasattr(proveedor, 'enviar_imagen') and not imagen_nombre and not es_carmen and not es_merida:
                foto_asesor = {
                    "asesor":         "asesor_ana",
                    "director":       "asesor_ana",
                    "administracion": "asesor_tere",
                }.get(area_escalar)
                if foto_asesor:
                    await proveedor.enviar_imagen(msg.telefono, foto_asesor)
                    logger.info(f"Foto de asesor enviada al cliente: {foto_asesor}")

    except Exception as e:
        import traceback as _tb
        logger.error(f"Error procesando mensaje en background: {e}")
        _diag_webhook["ultimo_error"] = _tb.format_exc()[-1200:]


@app.get("/crm/recuperar-acceso")
async def crm_recuperar_acceso(usuario: str = "chino", clave: str = ""):
    """Rescate de contraseña con clave secreta (cuando alguien queda fuera del CRM)."""
    if clave != "litek-rescate-2026":
        raise HTTPException(status_code=404, detail="No encontrado")
    if usuario not in ("chino", "tere", "leo", "anna", "brayan", "alan", "jadiel"):
        return {"ok": False, "error": "usuario no válido"}
    nueva = {
        "chino": "litek2026", "tere": "tere2026", "leo": "carmen-leo-86",
        "anna": "anna2026", "brayan": "brayan2026",
        "alan": "carmen-alan-71", "jadiel": "carmen-jadiel-39",
    }[usuario]
    ok = await cambiar_password_crm(usuario, nueva)
    return {"ok": ok, "usuario": usuario, "nueva_password": nueva,
            "nota": "Inicia sesión y cámbiala en la pestaña Equipo."}


@app.get("/diag/brain")
async def diag_brain():
    """Muestra el último error del cerebro (Claude API / tool_use) para diagnóstico."""
    import agent.brain as _brain
    return {"ultimo_error_brain": getattr(_brain, "ULTIMO_ERROR_BRAIN", None)}


@app.get("/diag/test-alerta")
async def diag_test_alerta(sucursal: str = "Campeche", asesor: str = ""):
    """Envía un mensaje de PRUEBA al grupo de la sucursal y, si se indica, al privado del asesor.
    Ej: /diag/test-alerta?sucursal=Campeche&asesor=Anna — sirve para verificar los envíos."""
    msg = "🔔 *PRUEBA DE CLIO* — si ves esto, los envíos de alerta funcionan. ✅"
    grupo = GRUPO_ALERTA_SUCURSAL.get(sucursal, ASESOR_WHATSAPP)
    res = {}
    try:
        ok = await proveedor.enviar_mensaje(grupo, msg)
        res["grupo"] = {"sucursal": sucursal, "destino": grupo, "enviado": ok}
    except Exception as e:
        res["grupo"] = {"sucursal": sucursal, "destino": grupo, "error": str(e)}
    if asesor:
        num = ASESOR_PERSONAL.get(asesor)
        if not num:
            res["privado"] = {"asesor": asesor, "error": "ese asesor no está en ASESOR_PERSONAL"}
        else:
            try:
                ok = await proveedor.enviar_mensaje(num, msg)
                res["privado"] = {"asesor": asesor, "destino": num, "enviado": ok}
            except Exception as e:
                res["privado"] = {"asesor": asesor, "destino": num, "error": str(e)}
    return res


@app.get("/diag/grupos")
async def diag_grupos():
    """Lista los grupos de WhatsApp con su ID (para configurar alertas por sucursal)."""
    import httpx
    token = getattr(proveedor, "token", None)
    if not token:
        return {"error": "No hay token de Whapi configurado"}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(
                "https://gate.whapi.cloud/groups",
                params={"count": 200},
                headers={"Authorization": f"Bearer {token}"},
            )
        data = r.json()
        grupos = [
            {"nombre": g.get("name", "(sin nombre)"), "id": g.get("id", "")}
            for g in data.get("groups", [])
        ]
        grupos.sort(key=lambda x: x["nombre"].lower())
        return {"total": len(grupos), "grupos": grupos}
    except Exception as e:
        return {"error": str(e)}


@app.get("/diag/webhook")
async def diag_webhook():
    """Diagnóstico: cuántos webhooks ha recibido y cuándo el último."""
    from datetime import datetime as _dt
    return {
        "total_webhooks_recibidos": _diag_webhook["total"],
        "mensajes_extraidos": _diag_webhook["mensajes"],
        "ultimo_recibido": _diag_webhook["ultimo"].isoformat() if _diag_webhook["ultimo"] else None,
        "ultimo_error": _diag_webhook.get("ultimo_error"),
        "ahora": _dt.utcnow().isoformat(),
    }


_diag_webhook = {"total": 0, "mensajes": 0, "ultimo": None, "ultimo_error": None}


@app.post("/webhook")
async def webhook_handler(request: Request):
    """
    Recibe mensajes de WhatsApp. Responde 200 de inmediato y procesa en
    segundo plano, para que Whapi no reintente (evita alertas duplicadas).
    """
    from datetime import datetime as _dt
    _diag_webhook["total"] += 1
    _diag_webhook["ultimo"] = _dt.utcnow()
    try:
        mensajes = await proveedor.parsear_webhook(request)
    except Exception as e:
        logger.error(f"Error parseando webhook: {e}")
        return {"status": "ok"}
    _diag_webhook["mensajes"] += len(mensajes)

    for msg in mensajes:
        if msg.es_propio:
            continue
        if msg.mensaje_id and msg.mensaje_id in _ids_procesados:
            logger.info(f"Mensaje duplicado ignorado: {msg.mensaje_id}")
            continue
        if msg.mensaje_id:
            _ids_procesados[msg.mensaje_id] = True
            if len(_ids_procesados) > MAX_IDS_CACHE:
                _ids_procesados.popitem(last=False)
        asyncio.create_task(_procesar_mensaje(msg))

    return {"status": "ok"}


# ─────────────────────────────────────────────────────────────────────────────
# Canal Meta (AISLADO) — /webhook-meta. No toca el flujo de Whapi (producción).
# Para la revisión de Meta y la migración futura. Flujo esencial: texto→respuesta.
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/webhook-meta")
async def webhook_meta_verif(request: Request):
    """Verificación GET del webhook de Meta (hub.challenge)."""
    if prov_meta:
        resultado = await prov_meta.validar_webhook(request)
        if resultado is not None:
            return PlainTextResponse(str(resultado))
    return PlainTextResponse("error", status_code=403)


_diag_meta = {"total": 0, "mensajes": 0, "ultimo_texto": None, "respuesta_ok": None, "ultimo_error": None}


@app.get("/diag/meta")
async def diag_meta():
    """Diagnóstico del canal Meta (sin auth) para ver el flujo end-to-end."""
    return {"meta_activo": prov_meta is not None, **_diag_meta}


@app.post("/webhook-meta")
async def webhook_meta_handler(request: Request):
    """Recibe mensajes del canal Meta (número de prueba). Responde en background."""
    _diag_meta["total"] += 1
    if not prov_meta:
        return {"status": "meta-inactivo"}
    try:
        mensajes = await prov_meta.parsear_webhook(request)
    except Exception as e:
        logger.error(f"[META] Error parseando webhook: {e}")
        _diag_meta["ultimo_error"] = f"parseo: {e}"
        return {"status": "ok"}
    _diag_meta["mensajes"] += len(mensajes)
    for msg in mensajes:
        if msg.es_propio:
            continue
        if msg.mensaje_id and msg.mensaje_id in _ids_procesados:
            continue
        if msg.mensaje_id:
            _ids_procesados[msg.mensaje_id] = True
            if len(_ids_procesados) > MAX_IDS_CACHE:
                _ids_procesados.popitem(last=False)
        asyncio.create_task(_procesar_mensaje_meta(msg))
    return {"status": "ok"}


async def _procesar_mensaje_meta(msg):
    """Flujo esencial del canal Meta: recibe texto y responde con Clio vía Meta."""
    try:
        if not msg.texto:
            return
        logger.info(f"[META] Mensaje de {msg.telefono}: {msg.texto}")
        _diag_meta["ultimo_texto"] = msg.texto[:80]
        historial = await obtener_historial(msg.telefono)
        respuesta = await generar_respuesta(
            msg.texto, historial, nombre_perfil=msg.nombre_perfil,
        )
        await guardar_mensaje(msg.telefono, "user", msg.texto)
        await guardar_mensaje(msg.telefono, "assistant", respuesta)
        ok = await prov_meta.enviar_mensaje(msg.telefono, respuesta)
        _diag_meta["respuesta_ok"] = ok
        if not ok:
            _diag_meta["ultimo_error"] = "enviar_mensaje devolvió False (token/permisos)"
        logger.info(f"[META] Respondido a {msg.telefono} (ok={ok})")
    except Exception as e:
        logger.error(f"[META] Error procesando mensaje: {e}")
        _diag_meta["ultimo_error"] = f"procesar: {e}"
