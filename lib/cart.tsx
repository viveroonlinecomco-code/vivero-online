'use client'
import { createContext, useContext, useState, ReactNode } from 'react'

export type CartItem = {
  id: string
  nombre_comun: string
  precio_cop: number
  imagen_url: string
  cantidad: number
}

type CartCtx = {
  items: CartItem[]
  agregar: (p: Omit<CartItem, 'cantidad'>) => void
  quitar: (id: string) => void
  limpiar: () => void
  total: number
  cantidad: number
}

const Ctx = createContext<CartCtx>({} as CartCtx)

export function CartProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<CartItem[]>([])

  function agregar(p: Omit<CartItem, 'cantidad'>) {
    setItems(prev => {
      const ex = prev.find(i => i.id === p.id)
      if (ex) return prev.map(i => i.id === p.id ? { ...i, cantidad: i.cantidad + 1 } : i)
      return [...prev, { ...p, cantidad: 1 }]
    })
  }

  function quitar(id: string) {
    setItems(prev => prev.filter(i => i.id !== id))
  }

  const total = items.reduce((s, i) => s + i.precio_cop * i.cantidad, 0)
  const cantidad = items.reduce((s, i) => s + i.cantidad, 0)

  return (
    <Ctx.Provider value={{ items, agregar, quitar, limpiar: () => setItems([]), total, cantidad }}>
      {children}
    </Ctx.Provider>
  )
}

export const useCart = () => useContext(Ctx)
