# agent/document_reader.py — Lector de documentos PDF
# Generado por AgentKit para LiTek

"""
Descarga y extrae texto de documentos PDF enviados por clientes.
Útil para leer constancias fiscales, RFCs, y otros documentos de facturación.
"""

import os
import logging
import tempfile
import httpx
import pdfplumber

logger = logging.getLogger("agentkit")

MAX_CHARS = 3000  # Máximo de caracteres a extraer (para no saturar el contexto)


async def leer_documento(media_url: str, whapi_token: str, filename: str = "") -> str:
    """
    Descarga un documento de Whapi y extrae su texto.

    Args:
        media_url: URL del documento en Whapi
        whapi_token: Token de Whapi para autenticar
        filename: Nombre del archivo (para detectar el tipo)

    Returns:
        Texto extraído del documento, o mensaje descriptivo si no se puede leer
    """
    if not media_url:
        return "[Documento recibido pero no se pudo descargar]"

    try:
        # Descargar el documento
        headers = {"Authorization": f"Bearer {whapi_token}"}
        logger.info(f"Descargando documento desde: {media_url}")

        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            r = await client.get(media_url, headers=headers)
            if r.status_code != 200:
                logger.error(f"Error descargando documento: {r.status_code}")
                return "[No pude descargar el documento. ¿Puedes enviarlo de nuevo?]"

            contenido = r.content
            content_type = r.headers.get("content-type", "")
            logger.info(f"Documento descargado: {len(contenido)} bytes | tipo: {content_type}")

        # Detectar si es PDF
        es_pdf = (
            "pdf" in content_type.lower()
            or filename.lower().endswith(".pdf")
            or contenido[:4] == b"%PDF"
        )

        if es_pdf:
            return extraer_texto_pdf(contenido, filename)
        else:
            # Para otros formatos (Word, etc.), devolver descripción básica
            return f"[Documento recibido: {filename or 'archivo'} — {len(contenido)} bytes. Tipo: {content_type}]"

    except Exception as e:
        logger.error(f"Error leyendo documento: {e}")
        return "[Tuve un problema leyendo el documento. ¿Puedes describirme qué contiene?]"


def extraer_texto_pdf(contenido: bytes, filename: str = "") -> str:
    """Extrae texto de un PDF usando pdfplumber."""
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(contenido)
            tmp_path = tmp.name

        try:
            with pdfplumber.open(tmp_path) as pdf:
                texto_completo = []
                for i, pagina in enumerate(pdf.pages):
                    texto = pagina.extract_text()
                    if texto:
                        texto_completo.append(f"[Página {i+1}]\n{texto}")

                if texto_completo:
                    resultado = "\n\n".join(texto_completo)
                    # Limitar tamaño para no saturar el contexto de Claude
                    if len(resultado) > MAX_CHARS:
                        resultado = resultado[:MAX_CHARS] + "\n...[documento truncado]"
                    logger.info(f"PDF extraído: {len(resultado)} caracteres de {len(pdf.pages)} páginas")
                    return f"[Contenido del documento {filename}]:\n{resultado}"
                else:
                    return f"[El PDF {filename} no tiene texto extraíble — puede ser una imagen escaneada]"
        finally:
            os.unlink(tmp_path)

    except Exception as e:
        logger.error(f"Error extrayendo PDF: {e}")
        return f"[No pude leer el contenido del PDF {filename}]"
