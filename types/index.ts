export type Planta = {
  id: string
  viverista_id: string
  nombre_comun: string
  nombre_cientifico: string
  descripcion: string
  precio_cop: number
  stock_unidades: number
  imagen_url: string
  vision_metadata: Record<string, any> | null
  verificado: boolean
  created_at: string
  updated_at: string
}

export type Viverista = {
  id: string
  nombre: string
  email: string
  telefono: string
  municipio: string
  sabana_zone: string
  activo: boolean
  nombre_vivero: string
  onboarded_at: string
  created_at: string
}

export type Vivero = {
  vivero_id: number
  nombre_vivero: string
  propietario: string
  ciudad: string
  latitud: number
  longitud: number
  altitud_msnm: number
  telefono: string
  email: string
}

export type Inventario = {
  inventario_id: number
  vivero_id: number
  planta_id: number
  altura_cm: number
  precio_detal: number
  precio_mayorista: number
  stock: number
  fecha_actualizacion: string
}

export type Pedido = {
  id: string
  cliente_id: number
  viverista_id: string
  estado: 'pendiente' | 'confirmado' | 'preparando' | 'en_camino' | 'entregado' | 'cancelado'
  total_cop: number
  direccion_entrega: string
  notas: string
  created_at: string
  updated_at: string
}

export type Perfil = {
  id: string
  nombre: string
  rol: 'buyer' | 'nursery_owner' | 'landscaper' | 'admin'
  avatar_url: string | null
  created_at: string
}
