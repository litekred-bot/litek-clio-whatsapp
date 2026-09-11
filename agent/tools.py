# agent/tools.py — Calculadora de precios LiTek (matemáticas exactas, sin IA)
# Generado por AgentKit para LiTek

"""
Todas las fórmulas de pricing de LiTek en código Python puro.
Claude llama a calcular_precio() como herramienta — nunca calcula mentalmente.
Actualizar precios aquí = cambia en toda la app automáticamente.
"""

import logging

logger = logging.getLogger("agentkit")

# ─────────────────────────────────────────────────────────────────────────────
# Lonas promocionales — precio fijo por medida EXACTA
# ─────────────────────────────────────────────────────────────────────────────
# Lonas PROMOCIONALES — precio fijo por medida (oferta). Acepta medidas giradas.
# Si el cliente pide varias, se compara la promo×cantidad contra el precio por
# volumen y se le da el MÁS BAJO (lo que más le conviene).
_LONAS_PROMO: dict[tuple[int, int], float] = {
    (75, 50):   39,
    (75, 75):   80,
    (100, 75):  99,
    (150, 100): 198,
    (200, 100): 264,
    (150, 150): 297,
}

# Promos CHICAS que Clio anuncia — NO entran al descuento por VOLUMEN (se cobran a su
# precio de promo aparte). Las demás medidas fijas (150×100, 200×100, 150×150) SÍ suman
# al volumen cuando el cliente pide varias lonas.
_PROMOS_FUERA_VOLUMEN: set[tuple[int, int]] = {(75, 50), (75, 75), (100, 75)}


# Tabla OFICIAL de lona por m² — el precio/m² baja al aumentar el área total.
# Compartida por calcular_precio() y calcular_precio_lonas_volumen().
_TABLA_LONA: list[tuple[float, float]] = [
    (0.98,         185.0),
    (2.00,         166.0),
    (4.00,         100.0),
    (6.00,         95.0),
    (9.00,         90.0),
    (12.00,        90.0),
    (15.00,        90.0),
    (100.00,       90.0),
    (200.00,       80.0),
    (400.00,       75.0),
    (float("inf"), 65.0),
]


def _tasa(area: float, tabla: list[tuple[float, float]]) -> float:
    """Devuelve la tasa $/m² que corresponde al área según la tabla de rangos."""
    for limite, tasa in tabla:
        if area <= limite:
            return tasa
    return tabla[-1][1]


def calcular_precio(
    producto: str,
    base_cm: float,
    alto_cm: float,
    cantidad: int = 1,
    expres: bool = False,
) -> dict:
    """
    Calcula el precio exacto de cualquier producto LiTek.

    Args:
        producto: lona | vinil_impreso | vinil_pvc | coroplast | microperforado |
                  papel_couche | papel_bond | etiqueta_5x5 | etiqueta_personalizada |
                  tabloide_laser | corte_vinil
        base_cm : Ancho en cm. Para corte_vinil: longitud total en cm.
        alto_cm : Alto en cm.  Para corte_vinil: no se usa.
        cantidad: Número de piezas (default 1).
        expres  : True → aplica +35% al total.

    Returns:
        {"precio": float, ...}  o  {"accion": "escalar_asesor"|"error", "mensaje": str}
    """
    base_m     = base_cm / 100
    alto_m     = alto_cm / 100
    area_pieza = base_m * alto_m
    precio     = 0.0
    es_promocion = False   # True si se aplicó un precio promocional de lona
    promo_sugerida = None  # promo de lona cercana a ofrecer (cuando la medida no tiene promo exacta)

    # ── LONA ─────────────────────────────────────────────────────────────────
    if producto == "lona":
        tabla = _TABLA_LONA
        area_total = area_pieza * cantidad
        precio_vol = max(99.0, area_total * _tasa(area_total, tabla))

        # ¿La medida tiene promoción? (acepta giradas: 75×50 = 50×75)
        clave     = (int(base_cm), int(alto_cm))
        clave_inv = (int(alto_cm), int(base_cm))
        promo_unit = _LONAS_PROMO.get(clave) or _LONAS_PROMO.get(clave_inv)

        if promo_unit is not None:
            precio_promo = float(promo_unit) * cantidad
            # Le damos lo que MÁS le conviene: el más barato entre promo y volumen.
            if precio_promo <= precio_vol:
                precio = precio_promo
                es_promocion = True
            else:
                precio = precio_vol   # en volumen le sale más barato
        else:
            precio = precio_vol
            # No hay promo EXACTA para esa medida → sugiere la promo más cercana
            # (por área de pieza) para ofrecerla como mejor opción.
            mejor = None
            for (b, a), p in _LONAS_PROMO.items():
                area_promo = (b / 100) * (a / 100)
                diff = abs(area_promo - area_pieza)
                if mejor is None or diff < mejor[0]:
                    mejor = (diff, b, a, p)
            if mejor is not None:
                _, b, a, p = mejor
                promo_sugerida = {
                    "medida": f"{b}×{a} cm",
                    "precio_unit": float(p),
                    "mas_grande": (b / 100) * (a / 100) >= area_pieza,
                }

    # ── VINIL IMPRESO ────────────────────────────────────────────────────────
    elif producto == "vinil_impreso":
        tabla = [
            (1.00,        388.0),
            (2.00,        300.0),
            (4.00,        280.0),
            (6.00,        250.0),
            (8.00,        220.0),
            (float("inf"), 200.0),
        ]
        area_total = area_pieza * cantidad
        precio = max(188.0, area_total * _tasa(area_total, tabla))

    # ── VINIL SOBRE PVC 3mm ──────────────────────────────────────────────────
    elif producto == "vinil_pvc":
        area_total = area_pieza * cantidad
        if area_total <= 0.72:
            precio = 299.0
        else:
            tabla = [
                (1.40,        750.0),
                (2.88,        710.0),
                (5.76,        650.0),
                (float("inf"), 610.0),
            ]
            precio = area_total * _tasa(area_total, tabla)

    # ── COROPLAST — precio POR PIEZA individual ──────────────────────────────
    elif producto == "coroplast":
        def _pieza(area: float) -> float:
            if area <= 0.40:
                return 264.60
            elif area < 1.00:
                return area * 681.0
            else:
                # Redondeo estándar (no banker's): round(1.5)→2, round(2.5)→3
                redondeado = int(area + 0.5)
                return float(redondeado) * 735.0

        precio = sum(_pieza(area_pieza) for _ in range(cantidad))

    # ── MICROPERFORADO ───────────────────────────────────────────────────────
    elif producto == "microperforado":
        tabla = [
            (1.00,        560.0),
            (2.00,        300.0),
            (4.00,        250.0),
            (float("inf"), 210.0),
        ]
        area_total = area_pieza * cantidad
        precio = max(350.0, area_total * _tasa(area_total, tabla))

    # ── PAPEL COUCHÉ — lógica de 3 escalones ────────────────────────────────
    elif producto == "papel_couche":
        tabla = [
            (2.00,        310.00),
            (4.00,        227.95),
            (6.00,        217.95),
            (9.00,        196.76),
            (12.00,       189.20),
            (15.00,       174.08),
            (float("inf"), 143.81),
        ]
        area_total = area_pieza * cantidad
        calculado  = area_total * _tasa(area_total, tabla)
        if calculado < 110.0:
            precio = 110.0
        elif calculado <= 310.0:
            precio = 310.0
        else:
            precio = calculado

    # ── PAPEL BOND ───────────────────────────────────────────────────────────
    elif producto == "papel_bond":
        tabla = [
            (2.00,        299.00),
            (4.00,        242.46),
            (6.00,        232.74),
            (9.00,        210.11),
            (12.00,       202.03),
            (15.00,       185.89),
            (float("inf"), 153.57),
        ]
        area_total = area_pieza * cantidad
        precio = max(180.0, area_total * _tasa(area_total, tabla))

    # ── ETIQUETAS VINIL 5×5 cm (PROD-050) ───────────────────────────────────
    elif producto == "etiqueta_5x5":
        if cantidad < 40:
            return {"accion": "error", "mensaje": "Mínimo de pedido: 40 piezas ($150)"}
        if cantidad < 200:
            precio = max(150.0, cantidad * 3.75)
        elif cantidad < 400:
            precio = cantidad * 2.38
        else:
            precio = cantidad * 1.45

    # ── ETIQUETAS PERSONALIZADAS con suaje (PROD-051) ────────────────────────
    elif producto == "etiqueta_personalizada":
        if cantidad > 500:
            return {
                "accion": "escalar_asesor",
                "mensaje": "Pedido mayor a 500 etiquetas personalizadas — escalar a asesor"
            }
        tabla = [
            (0.24,        355.00),
            (0.49,       1500.00),
            (0.74,        952.00),
            (0.99,        710.00),
            (1.24,        581.57),
            (1.49,        489.21),
            (1.74,        460.00),
            (1.99,        430.00),
            (2.24,        410.00),
            (2.49,        380.00),
            (float("inf"), 370.00),
        ]
        area_total = area_pieza * cantidad
        precio = max(85.20, area_total * _tasa(area_total, tabla))

    # ── TABLOIDE LÁSER (por pieza) ───────────────────────────────────────────
    elif producto == "tabloide_laser":
        if cantidad <= 10:
            precio = cantidad * 27.0
        elif cantidad <= 30:
            precio = cantidad * 20.0
        elif cantidad <= 50:
            precio = cantidad * 17.0
        else:
            precio = cantidad * 13.50

    # ── CORTE VINIL (metros lineales) ────────────────────────────────────────
    # base_cm = longitud total en cm  (ej. 150 cm = 1.5 m lineales)
    elif producto == "corte_vinil":
        ml    = base_cm / 100
        tabla = [
            (1.00,        420.0),
            (2.00,        350.0),
            (4.00,        300.0),
            (8.00,        200.0),
            (float("inf"), 180.0),
        ]
        precio = max(120.0, ml * _tasa(ml, tabla))

    else:
        return {
            "accion": "error",
            "mensaje": (
                f"Producto '{producto}' no reconocido. "
                "Opciones válidas: lona, vinil_impreso, vinil_pvc, coroplast, microperforado, "
                "papel_couche, papel_bond, etiqueta_5x5, etiqueta_personalizada, "
                "tabloide_laser, corte_vinil"
            )
        }

    # Servicio exprés: +55% SOLO en lona; +35% en los demás productos.
    if expres:
        precio *= 1.55 if producto == "lona" else 1.35

    return {
        "precio":     round(precio, 2),
        "producto":   producto,
        "base_cm":    base_cm,
        "alto_cm":    alto_cm,
        "cantidad":   cantidad,
        "expres":     expres,
        "promocion":  es_promocion,   # True → dile al cliente que es precio de promoción
        "promo_sugerida": promo_sugerida,  # promo de lona cercana para ofrecer (o None)
    }


def calcular_precio_lonas_volumen(lonas: list[dict], expres: bool = False) -> dict:
    """
    Descuento por VOLUMEN cuando el cliente pide VARIAS lonas de medidas distintas.

    Suma los m² de TODAS las lonas por medida y les aplica la tarifa $/m² del total
    (mientras más metros, más barato el m²). El ahorro vs. cotizar cada lona suelta se
    devuelve como DESCUENTO EN PESOS ligado al número de lonas (para decírselo así al
    cliente, SIN mencionar el $/m²).

    ⚠️ Las lonas PROMOCIONALES (medida exacta con precio fijo, ej. 75×50=$39) NO entran
    al volumen: se cobran aparte con su precio de promo.

    Args:
        lonas: lista de {"base_cm": float, "alto_cm": float, "cantidad": int}.
        expres: True → +55% (solo lona) sobre el total.

    Returns:
        {
          "precio": total final a cobrar (todas las lonas),
          "descuento": pesos ahorrados por volumen (0 si no hay ahorro),
          "precio_sin_descuento": lo que costaría cotizando cada lona suelta,
          "n_lonas": número total de lonas del pedido,
          "n_lonas_volumen": lonas por medida que entraron al volumen,
          "m2_total": m² sumados que entraron al volumen,
          "promos": [{"medida","cantidad","precio"}...] lonas de promo cobradas aparte,
          "expres": bool,
        }
        o {"accion": "error", "mensaje": str} si la lista es inválida.
    """
    if not lonas or not isinstance(lonas, list):
        return {"accion": "error", "mensaje": "Se requiere una lista de lonas."}

    tabla = _TABLA_LONA
    regulares_m2   = 0.0
    n_regulares    = 0
    precio_individual = 0.0   # suma de cada lona regular cotizada SUELTA (referencia)
    promos         = []
    precio_promos  = 0.0
    n_promos       = 0

    for item in lonas:
        try:
            base_cm  = float(item.get("base_cm", 0))
            alto_cm  = float(item.get("alto_cm", 0))
            cantidad = int(item.get("cantidad", 1) or 1)
        except (TypeError, ValueError):
            return {"accion": "error", "mensaje": f"Lona inválida: {item}"}
        if base_cm <= 0 or alto_cm <= 0 or cantidad <= 0:
            return {"accion": "error", "mensaje": f"Medida/cantidad inválida: {item}"}

        area_pieza = (base_cm / 100) * (alto_cm / 100)
        clave      = (int(base_cm), int(alto_cm))
        clave_inv  = (int(alto_cm), int(base_cm))
        # Solo las promos CHICAS anunciadas (75×50, 75×75, 100×75) quedan FUERA del
        # volumen. Las demás medidas (incl. 150×100, 200×100, 150×150) SÍ suman.
        es_promo_chica = clave in _PROMOS_FUERA_VOLUMEN or clave_inv in _PROMOS_FUERA_VOLUMEN
        promo_unit = _LONAS_PROMO.get(clave) or _LONAS_PROMO.get(clave_inv)

        if es_promo_chica and promo_unit is not None:
            # Promo chica: se cobra aparte, NO entra al volumen.
            p = float(promo_unit) * cantidad
            precio_promos += p
            n_promos += cantidad
            promos.append({
                "medida":   f"{int(base_cm)}×{int(alto_cm)} cm",
                "cantidad": cantidad,
                "precio":   round(p, 2),
            })
        else:
            # Regular: entra al volumen.
            n_regulares  += cantidad
            regulares_m2 += area_pieza * cantidad
            area_line = area_pieza * cantidad
            precio_individual += max(99.0, area_line * _tasa(area_line, tabla))

    # Precio por volumen (todas las lonas regulares juntas en un solo escalón).
    if n_regulares > 0:
        precio_volumen = max(99.0, regulares_m2 * _tasa(regulares_m2, tabla))
    else:
        precio_volumen = 0.0

    # Exprés: +55% solo en lona (aplica a regulares y promos).
    if expres:
        precio_volumen    *= 1.55
        precio_individual *= 1.55
        precio_promos     *= 1.55
        for pr in promos:
            pr["precio"] = round(pr["precio"] * 1.55, 2)

    descuento = round(max(0.0, precio_individual - precio_volumen), 2)
    total     = round(precio_volumen + precio_promos, 2)

    return {
        "precio":               total,
        "descuento":            descuento,
        "precio_sin_descuento": round(precio_individual + precio_promos, 2),
        "n_lonas":              n_regulares + n_promos,
        "n_lonas_volumen":      n_regulares,
        "m2_total":             round(regulares_m2, 2),
        "promos":               promos,
        "expres":               expres,
    }
