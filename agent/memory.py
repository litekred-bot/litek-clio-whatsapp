# agent/memory.py — Memoria de conversaciones con SQLite
# Generado por AgentKit para LiTek

import os
from datetime import datetime, timedelta
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import String, Text, DateTime, select, Integer, func, Boolean
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./agentkit.db")

if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

engine = create_async_engine(DATABASE_URL, echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class Mensaje(Base):
    """Modelo de mensaje en la base de datos."""
    __tablename__ = "mensajes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telefono: Mapped[str] = mapped_column(String(50), index=True)
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class RuletaParticipacion(Base):
    """Registro de participantes en la ruleta de premios."""
    __tablename__ = "ruleta_participaciones"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telefono: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    nombre: Mapped[str] = mapped_column(String(200))
    premio: Mapped[str] = mapped_column(String(200))
    descripcion: Mapped[str] = mapped_column(Text)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    vence_en: Mapped[datetime] = mapped_column(DateTime)


class CrmRegistro(Base):
    """Registro del CRM: leads, escalaciones, pedidos, ganadores de ruleta."""
    __tablename__ = "crm_registros"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tipo: Mapped[str] = mapped_column(String(30), index=True)   # escalacion | pedido | cliente | ruleta
    nombre: Mapped[str] = mapped_column(String(200), default="")
    telefono: Mapped[str] = mapped_column(String(50), index=True)
    descripcion: Mapped[str] = mapped_column(Text, default="")
    asesor: Mapped[str] = mapped_column(String(100), default="")     # a quién se asignó
    estado: Mapped[str] = mapped_column(String(20), default="nuevo", index=True)  # nuevo|asignado|proceso|cerrado
    notas: Mapped[str] = mapped_column(Text, default="")
    creado: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    actualizado: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ControlHumano(Base):
    """Conversaciones donde un asesor tomó el control (Clio en pausa)."""
    __tablename__ = "control_humano"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telefono: Mapped[str] = mapped_column(String(20), unique=True, index=True)  # últimos 10 dígitos
    asesor: Mapped[str] = mapped_column(String(100), default="")
    activo: Mapped[bool] = mapped_column(Boolean, default=True)
    actualizado: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class CrmUsuario(Base):
    """Usuarios del panel CRM (login por persona)."""
    __tablename__ = "crm_usuarios"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    usuario: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(200))
    nombre: Mapped[str] = mapped_column(String(100))
    creado: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


async def inicializar_db():
    """Crea las tablas si no existen."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await _crear_usuarios_crm_iniciales()


async def guardar_mensaje(telefono: str, role: str, content: str):
    """Guarda un mensaje en el historial de conversación."""
    async with async_session() as session:
        mensaje = Mensaje(
            telefono=telefono,
            role=role,
            content=content,
            timestamp=datetime.utcnow()
        )
        session.add(mensaje)
        await session.commit()


async def obtener_historial(telefono: str, limite: int = 20) -> list[dict]:
    """
    Recupera los últimos N mensajes de una conversación.

    Args:
        telefono: Número de teléfono del cliente
        limite: Máximo de mensajes a recuperar (default: 20)

    Returns:
        Lista de diccionarios con role y content en orden cronológico
    """
    async with async_session() as session:
        query = (
            select(Mensaje)
            .where(Mensaje.telefono == telefono)
            .order_by(Mensaje.timestamp.desc())
            .limit(limite)
        )
        result = await session.execute(query)
        mensajes = result.scalars().all()
        mensajes.reverse()
        return [
            {"role": msg.role, "content": msg.content}
            for msg in mensajes
        ]


async def obtener_conversacion_crm(telefono: str, limite: int = 150) -> list[dict]:
    """
    Historial completo de la conversación de un cliente para verlo en el CRM.
    Empareja por los últimos 10 dígitos (ignora prefijos/@s.whatsapp.net).
    Devuelve mensajes en orden cronológico con rol y hora (Campeche).
    """
    sufijo = _sufijo_tel(telefono)
    if not sufijo:
        return []
    async with async_session() as session:
        query = (
            select(Mensaje)
            .where(Mensaje.telefono.like(f"%{sufijo}%"))
            .order_by(Mensaje.timestamp.asc())
            .limit(limite)
        )
        result = await session.execute(query)
        msgs = result.scalars().all()
        return [{
            "role": m.role,
            "content": m.content,
            "hora": (m.timestamp - timedelta(hours=6)).strftime("%d/%m %H:%M"),  # UTC→Campeche
        } for m in msgs]


# ─────────────────────────────────────────────────────────────────────────────
# Control humano (bandeja): un asesor pausa a Clio y atiende él
# ─────────────────────────────────────────────────────────────────────────────
EXPIRA_CONTROL_MIN = 30  # Clio retoma sola tras 30 min sin actividad del humano


async def tomar_control(telefono: str, asesor: str):
    """Un asesor toma el control de la conversación (Clio se pausa)."""
    suf = _sufijo_tel(telefono)
    async with async_session() as session:
        r = (await session.execute(
            select(ControlHumano).where(ControlHumano.telefono == suf)
        )).scalar_one_or_none()
        if r:
            r.activo = True
            r.asesor = asesor
            r.actualizado = datetime.utcnow()
        else:
            session.add(ControlHumano(telefono=suf, asesor=asesor, activo=True))
        await session.commit()


async def devolver_clio(telefono: str):
    """Devuelve la conversación a Clio (quita la pausa)."""
    suf = _sufijo_tel(telefono)
    async with async_session() as session:
        r = (await session.execute(
            select(ControlHumano).where(ControlHumano.telefono == suf)
        )).scalar_one_or_none()
        if r:
            r.activo = False
            r.actualizado = datetime.utcnow()
            await session.commit()


async def estado_control(telefono: str) -> dict:
    """
    Estado del control de la conversación, aplicando expiración automática.
    Si el humano lleva +30 min sin actividad, Clio retoma sola.
    Retorna {activo, asesor}.
    """
    suf = _sufijo_tel(telefono)
    async with async_session() as session:
        r = (await session.execute(
            select(ControlHumano).where(ControlHumano.telefono == suf)
        )).scalar_one_or_none()
        if not r or not r.activo:
            return {"activo": False, "asesor": ""}
        if datetime.utcnow() - r.actualizado > timedelta(minutes=EXPIRA_CONTROL_MIN):
            r.activo = False  # expiró → Clio retoma
            await session.commit()
            return {"activo": False, "asesor": ""}
        return {"activo": True, "asesor": r.asesor}


async def esta_en_modo_humano(telefono: str) -> bool:
    """True si un asesor tiene el control (Clio debe quedarse callada)."""
    return (await estado_control(telefono))["activo"]


async def minutos_desde_ultimo_mensaje(telefono: str) -> float | None:
    """
    Minutos transcurridos desde el último mensaje de esta conversación.
    Retorna None si no hay mensajes previos. Sirve para detectar clientes
    que regresan tras un silencio largo.
    """
    async with async_session() as session:
        query = (
            select(Mensaje.timestamp)
            .where(Mensaje.telefono == telefono)
            .order_by(Mensaje.timestamp.desc())
            .limit(1)
        )
        result = await session.execute(query)
        ts = result.scalar_one_or_none()
        if ts is None:
            return None
        return (datetime.utcnow() - ts).total_seconds() / 60.0


# Números de prueba — pueden jugar la ruleta infinitas veces (sin bloqueo).
# Se comparan solo los últimos 10 dígitos para ignorar prefijos (52, 521, +).
NUMEROS_PRUEBA_RULETA = {"9812710000", "9811125510"}


def _es_numero_prueba(telefono: str) -> bool:
    """True si el teléfono está en la lista de prueba (compara últimos 10 dígitos)."""
    solo_digitos = "".join(c for c in telefono if c.isdigit())
    return solo_digitos[-10:] in NUMEROS_PRUEBA_RULETA


# Días que un cliente debe esperar para volver a jugar la ruleta
DIAS_ESPERA_RULETA = 15


async def registrar_ruleta(telefono: str, nombre: str, premio: str, descripcion: str) -> bool:
    """
    Registra una participación en la ruleta. Retorna False si jugó hace menos
    de DIAS_ESPERA_RULETA días. Si ya pasaron, actualiza el registro y permite jugar.
    Los números de prueba siempre pueden jugar.
    """
    # Números de prueba: no se registran, siempre permiten jugar
    if _es_numero_prueba(telefono):
        return True

    ahora = datetime.utcnow()
    async with async_session() as session:
        resultado = await session.execute(
            select(RuletaParticipacion).where(RuletaParticipacion.telefono == telefono)
        )
        existente = resultado.scalar_one_or_none()

        if existente:
            # ¿Ya pasaron los días de espera?
            dias_transcurridos = (ahora - existente.timestamp).days
            if dias_transcurridos < DIAS_ESPERA_RULETA:
                return False  # todavía bloqueado
            # Ya pasó el tiempo — actualizar registro y permitir jugar de nuevo
            existente.nombre = nombre
            existente.premio = premio
            existente.descripcion = descripcion
            existente.timestamp = ahora
            existente.vence_en = ahora + timedelta(days=30)
            await session.commit()
            return True

        # Primera vez que juega
        participacion = RuletaParticipacion(
            telefono=telefono,
            nombre=nombre,
            premio=premio,
            descripcion=descripcion,
            timestamp=ahora,
            vence_en=ahora + timedelta(days=30),
        )
        session.add(participacion)
        await session.commit()
        return True


async def verificar_ruleta(telefono: str) -> dict | None:
    """
    Retorna datos si el teléfono jugó hace menos de DIAS_ESPERA_RULETA días.
    Incluye 'dias_faltantes' para avisar cuándo puede volver a jugar.
    None si nunca jugó o si ya pasó el tiempo de espera.
    """
    # Números de prueba: siempre aparecen como "no han jugado"
    if _es_numero_prueba(telefono):
        return None

    async with async_session() as session:
        resultado = await session.execute(
            select(RuletaParticipacion).where(RuletaParticipacion.telefono == telefono)
        )
        p = resultado.scalar_one_or_none()
        if not p:
            return None
        dias_transcurridos = (datetime.utcnow() - p.timestamp).days
        if dias_transcurridos >= DIAS_ESPERA_RULETA:
            return None  # ya puede volver a jugar
        dias_faltantes = DIAS_ESPERA_RULETA - dias_transcurridos
        return {
            "premio": p.premio,
            "descripcion": p.descripcion,
            "dias_faltantes": dias_faltantes,
        }


async def limpiar_historial(telefono: str):
    """Borra todo el historial de una conversación."""
    async with async_session() as session:
        query = select(Mensaje).where(Mensaje.telefono == telefono)
        result = await session.execute(query)
        mensajes = result.scalars().all()
        for msg in mensajes:
            await session.delete(msg)
        await session.commit()


# ─────────────────────────────────────────────────────────────────────────────
# CRM — registros y usuarios
# ─────────────────────────────────────────────────────────────────────────────
import hashlib
import hmac as _hmac

_CRM_SALT = os.getenv("CRM_SALT", "litek-clio-crm-2026")


def _hash_password(password: str) -> str:
    """Hashea una contraseña con PBKDF2."""
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), _CRM_SALT.encode(), 100_000)
    return dk.hex()


async def _crear_usuarios_crm_iniciales():
    """Crea los usuarios del equipo si no existen (solo la primera vez)."""
    # usuario → (contraseña, nombre). Cambiar contraseñas después.
    iniciales = {
        "chino":  ("litek2026", "Chino (Director)"),
        "anna":   ("anna2026",  "Anna (Asesora)"),
        "brayan": ("brayan2026", "Brayan (Letreros)"),
        "tere":   ("tere2026",  "Tere (Administración)"),
    }
    async with async_session() as session:
        for usuario, (pwd, nombre) in iniciales.items():
            existe = await session.execute(
                select(CrmUsuario).where(CrmUsuario.usuario == usuario)
            )
            if existe.scalar_one_or_none() is None:
                session.add(CrmUsuario(
                    usuario=usuario,
                    password_hash=_hash_password(pwd),
                    nombre=nombre,
                ))
        await session.commit()


async def verificar_usuario_crm(usuario: str, password: str) -> dict | None:
    """Verifica credenciales del CRM. Retorna {usuario, nombre} si son correctas."""
    async with async_session() as session:
        resultado = await session.execute(
            select(CrmUsuario).where(CrmUsuario.usuario == usuario.lower().strip())
        )
        u = resultado.scalar_one_or_none()
        if u and _hmac.compare_digest(u.password_hash, _hash_password(password)):
            return {"usuario": u.usuario, "nombre": u.nombre}
    return None


async def registrar_crm(tipo: str, nombre: str, telefono: str, descripcion: str, asesor: str = "") -> int:
    """Agrega un registro al CRM. Retorna el id creado."""
    async with async_session() as session:
        reg = CrmRegistro(
            tipo=tipo,
            nombre=nombre or "",
            telefono=telefono or "",
            descripcion=descripcion or "",
            asesor=asesor or "",
            estado="asignado" if asesor else "nuevo",
        )
        session.add(reg)
        await session.commit()
        await session.refresh(reg)
        return reg.id


async def listar_crm(estado: str = "", tipo: str = "", asesor: str = "", limite: int = 200) -> list[dict]:
    """Lista registros del CRM, opcionalmente filtrados por estado, tipo y asesor."""
    async with async_session() as session:
        query = select(CrmRegistro)
        if estado:
            query = query.where(CrmRegistro.estado == estado)
        if tipo:
            query = query.where(CrmRegistro.tipo == tipo)
        if asesor:
            query = query.where(CrmRegistro.asesor == asesor)
        query = query.order_by(CrmRegistro.creado.desc()).limit(limite)
        result = await session.execute(query)
        registros = result.scalars().all()
        return [{
            "id": r.id,
            "tipo": r.tipo,
            "nombre": r.nombre,
            "telefono": r.telefono,
            "descripcion": r.descripcion,
            "asesor": r.asesor,
            "estado": r.estado,
            "notas": r.notas,
            "creado": r.creado.strftime("%d/%m/%Y %H:%M"),
        } for r in registros]


async def actualizar_crm(registro_id: int, estado: str = None, asesor: str = None, notas: str = None) -> bool:
    """Actualiza estado, asesor o notas de un registro del CRM."""
    async with async_session() as session:
        resultado = await session.execute(
            select(CrmRegistro).where(CrmRegistro.id == registro_id)
        )
        r = resultado.scalar_one_or_none()
        if not r:
            return False
        if estado is not None:
            r.estado = estado
        if asesor is not None:
            r.asesor = asesor
        if notas is not None:
            r.notas = notas
        r.actualizado = datetime.utcnow()
        await session.commit()
        return True


# Ciclo de vida de un cliente: el estado solo AVANZA, nunca retrocede.
_ORDEN_ESTADO = {"nuevo": 0, "asignado": 1, "proceso": 2, "cerrado": 3}
# Importancia del tipo: se muestra la etiqueta del evento más avanzado alcanzado.
_ORDEN_TIPO = {"cliente": 0, "ruleta": 1, "escalacion": 2, "pedido": 3}


def _sufijo_tel(telefono: str) -> str:
    """Últimos 10 dígitos del teléfono (para emparejar al mismo cliente sin importar prefijo)."""
    return "".join(c for c in (telefono or "") if c.isdigit())[-10:]


async def siguiente_asesor_rueda(candidatos: tuple = ("Anna", "Brayan")) -> str:
    """
    Devuelve el asesor que debe recibir el siguiente lead, repartiendo por
    turnos (uno y uno). Asigna al que tenga MENOS clientes/ruleta acumulados,
    así queda balanceado. En empate, el primero de la lista.
    """
    async with async_session() as session:
        conteos = {}
        for a in candidatos:
            res = await session.execute(
                select(func.count(CrmRegistro.id)).where(
                    CrmRegistro.asesor == a,
                    CrmRegistro.tipo.in_(["cliente", "ruleta"]),
                )
            )
            conteos[a] = res.scalar_one() or 0
        return min(candidatos, key=lambda a: conteos[a])


async def carga_por_asesor(asesores: tuple = ("Anna", "Brayan", "Tere")) -> dict:
    """
    Cuenta los clientes PENDIENTES (no cerrados) que tiene cada asesor por atender.
    Sirve para el resumen de carga del panel (ej. Anna 10, Brayan 15).
    """
    out = {}
    async with async_session() as session:
        for a in asesores:
            res = await session.execute(
                select(func.count(CrmRegistro.id)).where(
                    CrmRegistro.asesor == a,
                    CrmRegistro.estado != "cerrado",
                )
            )
            out[a] = res.scalar_one() or 0
    return out


async def registrar_o_actualizar_crm(
    telefono: str,
    nombre: str = "",
    descripcion: str = "",
    tipo: str = "cliente",
    estado_minimo: str = "nuevo",
    asesor: str = "",
    asesor_si_nuevo: str = "",
) -> int:
    """
    Una tarjeta por cliente que AVANZA de estado (nuevo→asignado→proceso→cerrado).

    Si ya existe una tarjeta NO cerrada de ese teléfono, la actualiza (sube de
    estado solo si corresponde, refresca descripción, nombre y tipo).
    Si no existe (o la última está cerrada), crea una nueva. Retorna el id.

    Asesor:
    - `asesor`: dueño explícito. Se aplica SIEMPRE (sobrescribe al existente).
      Lo usa la escalación, que manda al área correspondiente.
    - `asesor_si_nuevo`: dueño SOLO si se crea tarjeta nueva (no reasigna a quien
      ya tiene dueño). Lo usa el reparto por turnos de clientes nuevos / ruleta.
    """
    sufijo = _sufijo_tel(telefono)
    tel_guardar = (telefono or "").replace("@s.whatsapp.net", "")

    async with async_session() as session:
        # Buscar la tarjeta abierta (no cerrada) más reciente de este cliente
        result = await session.execute(
            select(CrmRegistro)
            .where(CrmRegistro.estado != "cerrado")
            .order_by(CrmRegistro.creado.desc())
        )
        existente = None
        for r in result.scalars().all():
            if _sufijo_tel(r.telefono) == sufijo:
                existente = r
                break

        if existente is None:
            # Crear tarjeta nueva
            reg = CrmRegistro(
                tipo=tipo,
                nombre=nombre or "",
                telefono=tel_guardar,
                descripcion=descripcion or "",
                asesor=asesor or asesor_si_nuevo or "",
                estado=estado_minimo,
            )
            session.add(reg)
            await session.commit()
            await session.refresh(reg)
            return reg.id

        # Actualizar la tarjeta existente — el cliente avanza
        # Estado: solo sube
        if _ORDEN_ESTADO.get(estado_minimo, 0) > _ORDEN_ESTADO.get(existente.estado, 0):
            existente.estado = estado_minimo
        # Tipo: muestra la etiqueta del evento más avanzado
        if _ORDEN_TIPO.get(tipo, 0) >= _ORDEN_TIPO.get(existente.tipo, 0):
            existente.tipo = tipo
        # Descripción: refresca con lo más reciente relevante
        if descripcion:
            existente.descripcion = descripcion
        # Nombre: completa si estaba vacío o genérico
        if nombre and existente.nombre.strip().lower() in ("", "cliente", "cliente nuevo"):
            existente.nombre = nombre
        # Asesor: solo el explícito reasigna; asesor_si_nuevo respeta al dueño actual
        if asesor:
            existente.asesor = asesor
        elif asesor_si_nuevo and not existente.asesor:
            existente.asesor = asesor_si_nuevo
        existente.actualizado = datetime.utcnow()
        await session.commit()
        return existente.id


async def consolidar_duplicados_crm() -> dict:
    """
    Une tarjetas duplicadas del MISMO cliente (mismo teléfono) en una sola.
    Necesario para limpiar data vieja creada antes del sistema de upsert.

    Por cada grupo de duplicados conserva UNA tarjeta:
    - estado = el más avanzado (proceso > asignado > nuevo)
    - tipo = el más avanzado (pedido > escalacion > ruleta > cliente)
    - asesor = el de cualquiera que tenga dueño (prioriza el más avanzado)
    - nombre = el más largo/completo
    - descripcion = la del registro más avanzado
    Borra las tarjetas sobrantes. Retorna {grupos, borrados}.
    """
    grupos_borrados = 0
    total_borrados = 0
    async with async_session() as session:
        result = await session.execute(select(CrmRegistro))
        todos = result.scalars().all()

        # Agrupar por sufijo de teléfono
        grupos: dict = {}
        for r in todos:
            grupos.setdefault(_sufijo_tel(r.telefono), []).append(r)

        for sufijo, registros in grupos.items():
            if len(registros) < 2:
                continue
            # Elegir el "ganador": más avanzado en estado, luego en tipo, luego más reciente
            ganador = max(registros, key=lambda r: (
                _ORDEN_ESTADO.get(r.estado, 0),
                _ORDEN_TIPO.get(r.tipo, 0),
                r.creado,
            ))
            # Fusionar datos de todo el grupo en el ganador
            mejor_asesor = ""
            for r in sorted(registros, key=lambda r: _ORDEN_ESTADO.get(r.estado, 0), reverse=True):
                if r.asesor:
                    mejor_asesor = r.asesor
                    break
            if mejor_asesor:
                ganador.asesor = mejor_asesor
            mejor_nombre = max((r.nombre or "" for r in registros), key=len)
            if mejor_nombre and len(mejor_nombre) > len(ganador.nombre or ""):
                ganador.nombre = mejor_nombre
            ganador.actualizado = datetime.utcnow()

            # Borrar los demás
            for r in registros:
                if r.id != ganador.id:
                    await session.delete(r)
                    total_borrados += 1
            grupos_borrados += 1

        if total_borrados:
            await session.commit()
    return {"grupos": grupos_borrados, "borrados": total_borrados}


async def asignar_ruletas_sin_avanzar(horas: int = 2, asesor_default: str = "Anna") -> int:
    """
    Asigna los ganadores de ruleta que llevan +N horas SIN dueño y sin avanzar
    (siguen en 'nuevo'). Como aún no identifican producto, van al asesor por
    defecto (Anna, impresión general) para que alguien les dé seguimiento.
    Si ya cotizaron/compraron, ya tienen dueño por su producto y se ignoran.
    Retorna cuántos se asignaron.
    """
    limite = datetime.utcnow() - timedelta(hours=horas)
    asignados = 0
    async with async_session() as session:
        result = await session.execute(
            select(CrmRegistro).where(
                CrmRegistro.tipo == "ruleta",
                CrmRegistro.estado == "nuevo",
                (CrmRegistro.asesor == "") | (CrmRegistro.asesor.is_(None)),
                CrmRegistro.creado <= limite,
            )
        )
        pendientes = result.scalars().all()
        for r in pendientes:
            r.asesor = asesor_default
            r.estado = "asignado"  # ya tiene dueño → pasa a Asignados
            r.actualizado = datetime.utcnow()
            asignados += 1
        if asignados:
            await session.commit()
    return asignados
