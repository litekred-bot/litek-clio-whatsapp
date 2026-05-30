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


async def inicializar_db():
    """Crea las tablas si no existen."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


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


# Números de prueba — pueden jugar la ruleta infinitas veces (sin bloqueo).
# Se comparan solo los últimos 10 dígitos para ignorar prefijos (52, 521, +).
NUMEROS_PRUEBA_RULETA = {"9812710000", "9811125510"}


def _es_numero_prueba(telefono: str) -> bool:
    """True si el teléfono está en la lista de prueba (compara últimos 10 dígitos)."""
    solo_digitos = "".join(c for c in telefono if c.isdigit())
    return solo_digitos[-10:] in NUMEROS_PRUEBA_RULETA


async def registrar_ruleta(telefono: str, nombre: str, premio: str, descripcion: str) -> bool:
    """
    Registra una participación en la ruleta. Retorna False si ya participó.
    Premio válido por 30 días. Los números de prueba siempre pueden jugar.
    """
    # Números de prueba: no se registran, siempre permiten jugar
    if _es_numero_prueba(telefono):
        return True

    async with async_session() as session:
        existente = await session.execute(
            select(RuletaParticipacion).where(RuletaParticipacion.telefono == telefono)
        )
        if existente.scalar_one_or_none():
            return False
        participacion = RuletaParticipacion(
            telefono=telefono,
            nombre=nombre,
            premio=premio,
            descripcion=descripcion,
            vence_en=datetime.utcnow() + timedelta(days=30),
        )
        session.add(participacion)
        await session.commit()
        return True


async def verificar_ruleta(telefono: str) -> dict | None:
    """Retorna los datos del premio si el teléfono ya participó, None si no."""
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
        return {"premio": p.premio, "descripcion": p.descripcion, "vence_en": str(p.vence_en)}


async def limpiar_historial(telefono: str):
    """Borra todo el historial de una conversación."""
    async with async_session() as session:
        query = select(Mensaje).where(Mensaje.telefono == telefono)
        result = await session.execute(query)
        mensajes = result.scalars().all()
        for msg in mensajes:
            await session.delete(msg)
        await session.commit()
