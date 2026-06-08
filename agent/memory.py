# agent/memory.py — Memoria de conversaciones con SQLite
# Generado por AgentKit para LiTek

import os
from datetime import datetime, timedelta
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import String, Text, DateTime, select, Integer
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


async def listar_crm(estado: str = "", tipo: str = "", limite: int = 200) -> list[dict]:
    """Lista registros del CRM, opcionalmente filtrados por estado y tipo."""
    async with async_session() as session:
        query = select(CrmRegistro)
        if estado:
            query = query.where(CrmRegistro.estado == estado)
        if tipo:
            query = query.where(CrmRegistro.tipo == tipo)
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
