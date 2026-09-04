"""
Módulo: Cálculo dinámico de tier con huella de piso.

Implementa especificación de Elena:
  PASO 1: Auditoría de restricciones físicas (altura)
  PASO 2: Sumatoria de área = ∑ (cantidad × huella_piso)
  PASO 3: Validación contra límites de piso útil
  PASO 4: Escalado automático

Archivo: backend/logistica_huella_piso.py
Fecha: 2026-09-03
"""

from typing import List, Dict, Optional
import logging

logger = logging.getLogger(__name__)

# ============================================================================
# CONSTANTES: Límites de piso útil y altura por tier (ESPECIFICACIÓN ELENA)
# ============================================================================

LIMITES_PISO = {
    'S': 0.24,    # Moto/Bicicleta: máx 0.24 m²
    'M': 2.0,     # Van utilitaria: máx 2.0 m²
    'L': 4.0,     # Camioneta platón: máx 4.0 m²
    'XL': 14.0,   # Camión turbo: máx 14.0 m²
}

LIMITES_ALTURA = {
    'S': 40,      # 0.40m (moto)
    'M': 120,     # 1.20m (van)
    'L': 200,     # 2.0m (camioneta) - EXCLUSIVO B2B
    'XL': 300,    # 3.0m (camión)
}

# Orden de escalado (para comparaciones)
TIER_ORDEN = {'S': 1, 'M': 2, 'L': 3, 'XL': 4}


# ============================================================================
# FUNCIÓN PRINCIPAL
# ============================================================================

def calcular_tier_viaje_con_huella_piso(db, items: List[Dict], es_b2b: bool = False) -> Dict:
    """
    ✅ FUNCIÓN PRINCIPAL: Calcula tier de viaje con lógica COMPLETA.
    
    Implementa especificación de Elena:
      PASO 1: Auditoría restricciones físicas (altura)
      PASO 2: Sumatoria de área (obtiene huella_piso_m2 via JOIN a formatos_comerciales)
      PASO 3: Validación contra límites (con márgenes configurables)
      PASO 4: Escalado automático
      PASO 5: Restricción de canal (B2B vs B2C)
    
    Args:
        db: Cliente Supabase (con método .table())
        items: Lista de items en carrito
               Formato: [{'inventario_id': X, 'cantidad': Y}, ...]
        es_b2b: bool
               - True: Cliente B2B registrado (puede ver L/XL)
               - False: Guest B2C (solo S/M)
    
    Returns:
        {
            'ok': bool,
            'tier_final': str,            # S, M, L, o XL
            'area_total': float,          # m² totales
            'altura_max': int,            # cm máximos
            'tier_por_restriccion': str,  # Tier mínimo por altura
            'tier_por_area': str,         # Tier mínimo por área
            'razon': str,                 # Explicación del resultado
            'advertencias': [str],        # Avisos si existen
            'limites': {                  # Límites del tier final
                'area_m2': float,
                'altura_cm': int,
            },
            'error': str,                 # Si ok=False, aquí el error
        }
    """
    
    advertencias = []
    
    try:
        # PASO 1: Auditoría restricciones físicas (altura)
        altura_max = obtener_altura_maxima_carrito(db, items)
        tier_por_restriccion = determinar_tier_minimo_restriccion(altura_max)
        
        # PASO 2: Sumatoria de área
        area_total = calcular_area_total_carrito(db, items)
        
        # PASO 3: Validación contra límites (ahora con margen de tolerancia)
        tier_por_area, area_info = determinar_tier_por_area(area_total, db=db, usar_margen=True)
        
        # PASO 4: Tomar el MÁXIMO (más restrictivo)
        if TIER_ORDEN[tier_por_restriccion] > TIER_ORDEN[tier_por_area]:
            tier_final = tier_por_restriccion
            razon = f"Altura {altura_max}cm descalifica; Tier mín = {tier_por_restriccion}"
        else:
            tier_final = tier_por_area
            razon = f"Área {area_total:.2f}m² requiere mín Tier {tier_por_area}"
        
        # PASO 5: Validación especial si supera 14 m²
        if area_total > 14.0:
            advertencias.append(
                f"⚠️ Área {area_total:.2f}m² > 14.0m²: Requiere múltiples camiones o cotización personalizada."
            )
            tier_final = 'XL'
        
        # PASO 6: Restricción de canal B2C (solo S/M)
        if not es_b2b and TIER_ORDEN[tier_final] > TIER_ORDEN['M']:
            advertencias.append(
                f"⚠️ Canal B2C solo soporta hasta Tier M. {tier_final} requiere canal B2B."
            )
            tier_final = 'M'
        
        return {
            'ok': True,
            'tier_final': tier_final,
            'area_total': round(area_total, 2),
            'altura_max': altura_max,
            'tier_por_restriccion': tier_por_restriccion,
            'tier_por_area': tier_por_area,
            'razon': razon,
            'advertencias': advertencias,
            'limites': {
                'area_m2': LIMITES_PISO[tier_final],
                'altura_cm': LIMITES_ALTURA[tier_final],
            },
        }
    
    except Exception as e:
        logger.exception(f"Error calculando tier con huella: {e}")
        
        # Fallback seguro
        return {
            'ok': False,
            'tier_final': 'M',
            'area_total': 0.0,
            'altura_max': 0,
            'tier_por_restriccion': 'M',
            'tier_por_area': 'M',
            'razon': 'Error en cálculo, fallback a M',
            'advertencias': [f'Error: {str(e)}'],
            'limites': {'area_m2': 2.0, 'altura_cm': 120},
            'error': str(e),
        }


# ============================================================================
# FUNCIONES HELPER: Auditoría de restricciones
# ============================================================================

def obtener_altura_maxima_carrito(db, items: List[Dict]) -> int:
    """
    Obtiene la altura máxima de todos los items en el carrito.
    
    Returns:
        int: Altura máxima en cm (0 si no hay items)
    """
    if not items:
        return 0
    
    try:
        inv_ids = [it.get('inventario_id') for it in items if it.get('inventario_id')]
        if not inv_ids:
            return 0
        
        # Obtener altura máxima de todos los productos (CORREGIDO: altura_cm, no altura_cm_max)
        resp = db.table('inventario').select('altura_cm').in_('inventario_id', inv_ids).execute()
        
        if resp.data:
            alturas = [r.get('altura_cm', 0) for r in resp.data]
            return max(alturas) if alturas else 0
    
    except Exception as e:
        logger.warning(f"Error obteniendo altura: {e}")
    
    return 0


def determinar_tier_minimo_restriccion(altura_max_cm: int) -> str:
    """
    PASO 1: Auditoría de restricciones físicas.
    
    Determina el TIER MÍNIMO basado en altura máxima del carrito.
    
    Lógica (ESPECIFICACIÓN ELENA):
      if altura > 2.0m (200cm) → tier_min = XL
      elif altura > 1.20m (120cm) → tier_min = L
      elif altura > 0.40m (40cm) → tier_min = M (descalifica S)
      else → tier_min = S
    
    Args:
        altura_max_cm: Altura máxima en cm
    
    Returns:
        str: Tier mínimo (S, M, L, o XL)
    """
    if altura_max_cm > 200:  # > 2.0m
        return 'XL'
    elif altura_max_cm > 120:  # > 1.20m
        return 'L'
    elif altura_max_cm > 40:  # > 0.40m
        return 'M'
    else:
        return 'S'


# ============================================================================
# FUNCIONES HELPER: Cálculo de área
# ============================================================================

def calcular_area_total_carrito(db, items: List[Dict]) -> float:
    """
    PASO 2: Sumatoria de área = ∑ (cantidad × huella_piso).
    
    Ejemplo:
      - 40 P12 (40 × 0.020 = 0.80m²)
      - 6 MEDIANA (6 × 0.065 = 0.39m²)
      - Total: 1.19m²
    
    CAMBIO (Sep 4, 2026):
      - Ahora obtiene huella_piso_m2 desde formatos_comerciales
      - Lee via FK formato_id (normalización correcta)
      - Elimina redundancia: huella_piso_m2 fue removida de inventario
    
    Args:
        db: Cliente Supabase
        items: [{inventario_id, cantidad}, ...]
    
    Returns:
        float: Área total en m²
    """
    if not items:
        return 0.0
    
    try:
        area_total = 0.0
        
        for item in items:
            inv_id = item.get('inventario_id')
            cantidad = item.get('cantidad', 1)
            
            if not inv_id or cantidad <= 0:
                continue
            
            # CAMBIO: Obtener huella_piso del producto via JOIN a formatos_comerciales
            # Query: inventario (con formato_id FK) → formatos_comerciales (con huella_piso_m2)
            resp = db.table('inventario').select(
                'formato_id, formatos_comerciales(huella_piso_m2)'
            ).eq('inventario_id', inv_id).limit(1).execute()
            
            if resp.data:
                row = resp.data[0]
                # El JOIN devuelve formatos_comerciales como objeto anidado
                formato = row.get('formatos_comerciales')
                if formato and formato.get('huella_piso_m2'):
                    huella = float(formato['huella_piso_m2'])
                    area_item = cantidad * huella
                    area_total += area_item
                    
                    logger.debug(f"Item {inv_id}: {cantidad} × {huella}m² = {area_item:.3f}m²")
        
        return round(area_total, 3)
    
    except Exception as e:
        logger.error(f"Error calculando área (JOIN formatos_comerciales): {e}")
        return 0.0


# ============================================================================
# FUNCIONES HELPER: Validación contra límites
# ============================================================================

def determinar_tier_por_area(area_m2: float, db=None, usar_margen: bool = True) -> tuple[str, dict]:
    """
    PASO 3: Validación contra límites de piso CON MARGEN DE TOLERANCIA.
    
    Determina el TIER MÍNIMO que puede soportar el área.
    
    MEJORÍA (Sep 4, 2026):
      - Ahora soporta margen de tolerancia configurable
      - Si área está cerca del límite, intenta escalad hacia abajo
      - Ejemplo: 4.5m² cabe en Tier L (límite 4.0 + margen 0.5)
    
    Lógica:
      if área ≤ 0.24 + margen_S → TIER S
      elif área ≤ 2.0 + margen_M → TIER M
      elif área ≤ 4.0 + margen_L → TIER L
      elif área ≤ 14.0 + margen_XL → TIER XL
      else → ERROR (múltiples camiones)
    
    Args:
        area_m2: Área total en m²
        db: Cliente Supabase (opcional, para leer márgenes de BD)
        usar_margen: bool (True = usar márgenes, False = usar límites base)
    
    Returns:
        tuple: (tier_final, info_dict)
        info_dict contiene: {
            'tier': str,
            'area_m2': float,
            'limite_base': float,
            'margen': float,
            'limite_con_margen': float,
            'entra_con_margen': bool,
            'razon': str,
        }
    """
    info = {
        'area_m2': area_m2,
        'entra_con_margen': False,
        'razon': '',
    }
    
    # Obtener márgenes desde BD si está disponible y se solicita
    margenes = {}
    if usar_margen and db:
        try:
            resp = db.table('limites_tier_con_margen').select(
                'tier, limite_base_m2, margen_m2, limite_con_margen'
            ).eq('activo', True).execute()
            for row in resp.data:
                margenes[row['tier']] = {
                    'base': float(row['limite_base_m2']),
                    'margen': float(row['margen_m2']),
                    'con_margen': float(row['limite_con_margen']),
                }
        except Exception as e:
            logger.warning(f"Error leyendo márgenes de BD: {e}. Usando defaults.")
            usar_margen = False
    
    # Iterar tiers en orden (S → M → L → XL)
    for tier in ['S', 'M', 'L', 'XL']:
        if usar_margen and margenes.get(tier):
            # Usar límite CON margen
            limite = margenes[tier]['con_margen']
            base = margenes[tier]['base']
            margen = margenes[tier]['margen']
        else:
            # Usar límite BASE (sin margen)
            limite = LIMITES_PISO[tier]
            base = LIMITES_PISO[tier]
            margen = 0
        
        if area_m2 <= limite:
            # ✅ Entra en este tier
            info['tier'] = tier
            info['limite_base'] = base
            info['margen'] = margen
            info['limite_con_margen'] = limite
            info['entra_con_margen'] = (margen > 0)
            
            if margen > 0 and area_m2 > base:
                info['razon'] = f"Área {area_m2:.2f}m² supera base {base}m², pero entra con margen +{margen}m² = {limite}m²"
            else:
                info['razon'] = f"Área {area_m2:.2f}m² ≤ límite {limite}m²"
            
            return (tier, info)
    
    # Si supera XL (14 + margen)
    info['tier'] = 'XL'
    info['limite_base'] = LIMITES_PISO['XL']
    info['margen'] = margenes.get('XL', {}).get('margen', 1.0) if margenes else 1.0
    info['limite_con_margen'] = info['limite_base'] + info['margen']
    info['razon'] = f"Área {area_m2:.2f}m² requiere múltiples camiones o cotización personalizada"
    
    return ('XL', info)


# ============================================================================
# FUNCIÓN: Cálculo completo de flete con tier dinámico
# ============================================================================

def calcular_flete_correcto(db, ciudad: str, items: List[Dict], es_b2b: bool = False) -> Dict:
    """
    ✅ FUNCIÓN COMPLETA: Calcula flete usando tier dinámico CORRECTO.
    
    Fórmula (ESPECIFICACIÓN ELENA):
      Total = Base + Fee + Recargo
      
      Fee = 9% (solo L/XL), 0% (S/M)
      Recargo = 12% × (viveros - 1) × Base
    
    Args:
        db: Cliente Supabase
        ciudad: Ciudad de entrega (ej: "Bogotá")
        items: [{inventario_id, cantidad}, ...]
        es_b2b: bool (B2B vs B2C)
    
    Returns:
        {
            'ok': bool,
            'tier_viaje': str,
            'zona': str,
            'costo_base': int,
            'fee_9pct': int,
            'recargo_12pct': int,
            'total_flete': int,
            'num_viveros': int,
            'area_total': float,
            'altura_max': int,
            'advertencias': [str],
            'desglose': {
                'base': int,
                'fee': int,
                'recargo': int,
                'razon_tier': str,
            },
            'error': str,  # Si ok=False
        }
    """
    
    # Obtener tier dinámico correcto
    tier_resultado = calcular_tier_viaje_con_huella_piso(db, items, es_b2b)
    
    if not tier_resultado['ok']:
        return {
            'ok': False,
            'error': tier_resultado.get('error', 'Error calculando tier'),
            'total_flete': 55000,  # Fallback
        }
    
    tier_viaje = tier_resultado['tier_final']
    
    try:
        # Obtener zona y costo base de tarifas_logistica
        zona = obtener_zona_de_ciudad(db, ciudad)
        costo_base = obtener_costo_base_tarifa(db, zona, tier_viaje)
    except Exception as e:
        logger.error(f"Error obteniendo tarifas: {e}")
        # Fallback con tarifas hardcodeadas
        costo_base = {
            'S': 110000, 'M': 180000, 'L': 380000, 'XL': 950000
        }.get(tier_viaje, 180000)
    
    # Calcular fee (9% solo L/XL, 0% para S/M)
    if tier_viaje in ('L', 'XL'):
        fee = int(costo_base * 0.09)
    else:
        fee = 0
    
    # Contar viveros únicos y calcular recargo (12%)
    try:
        num_viveros = contar_viveros_unicos(db, items)
    except:
        num_viveros = 1
    
    # Recargo = 12% de BASE × (viveros - 1)
    recargo = int(costo_base * 0.12 * max(0, num_viveros - 1))
    
    # Total
    total_flete = costo_base + fee + recargo
    
    return {
        'ok': True,
        'tier_viaje': tier_viaje,
        'zona': zona,
        'costo_base': costo_base,
        'fee_9pct': fee,
        'recargo_12pct': recargo,
        'total_flete': total_flete,
        'num_viveros': num_viveros,
        'area_total': tier_resultado['area_total'],
        'altura_max': tier_resultado['altura_max'],
        'advertencias': tier_resultado['advertencias'],
        'desglose': {
            'base': costo_base,
            'fee': fee,
            'recargo': recargo,
            'razon_tier': tier_resultado['razon'],
        },
    }


# ============================================================================
# FUNCIONES HELPER: Obtener datos de BD
# ============================================================================

def obtener_zona_de_ciudad(db, ciudad: str) -> str:
    """Obtiene la zona logística de una ciudad."""
    try:
        resp = db.table('ciudades_zonas').select('zona').eq('ciudad', ciudad.lower()).limit(1).execute()
        if resp.data:
            return resp.data[0]['zona']
    except:
        pass
    
    # Fallback
    return 'sabana_entre_municipios'


def obtener_costo_base_tarifa(db, zona: str, tier: str) -> int:
    """Obtiene el costo base de tarifa_logistica."""
    try:
        resp = db.table('tarifas_logistica').select('precio_cop').eq('zona', zona).eq('tier_base', tier).limit(1).execute()
        if resp.data and resp.data[0].get('precio_cop'):
            return int(resp.data[0]['precio_cop'])
    except:
        pass
    
    # Fallback: tarifas hardcodeadas
    fallbacks = {'S': 110000, 'M': 180000, 'L': 380000, 'XL': 950000}
    return fallbacks.get(tier, 180000)


def contar_viveros_unicos(db, items: List[Dict]) -> int:
    """Cuenta cuántos viveros únicos hay en los items."""
    try:
        inv_ids = [it.get('inventario_id') for it in items if it.get('inventario_id')]
        if not inv_ids:
            return 1
        
        resp = db.table('inventario').select('vivero_id').in_('inventario_id', inv_ids).execute()
        
        if resp.data:
            viveros = set(r.get('vivero_id') for r in resp.data if r.get('vivero_id'))
            return len(viveros) if viveros else 1
    except:
        pass
    
    return 1
