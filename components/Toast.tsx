'use client'
import { useEffect, useState } from 'react'

export default function Toast({ msg, onDone }: { msg: string; onDone: () => void }) {
  useEffect(() => {
    const t = setTimeout(onDone, 2500)
    return () => clearTimeout(t)
  }, [onDone])

  return (
    <div className="fixed top-4 left-1/2 z-[100] bg-[#325926] text-white px-5 py-3 rounded-full text-sm font-semibold shadow-lg toast-enter pointer-events-none">
      {msg}
    </div>
  )
}
