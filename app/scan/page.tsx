'use client'
import { useRef, useState } from 'react'
import { useRouter } from 'next/navigation'
import { supabase } from '@/lib/supabase'

type Resultado = { nombre_comun: string; nombre_cientifico: string; confianza: number; luz: string; riego: string }

export default function ScanPage() {
  const router  = useRouter()
  const inputRef = useRef<HTMLInputElement>(null)
  const [preview,   setPreview]   = useState<string | null>(null)
  const [analizando, setAnalizando] = useState(false)
  const [progreso,  setProgreso]  = useState(0)
  const [resultado, setResultado] = useState<Resultado | null>(null)

  function handleFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    if (!file) return
    setPreview(URL.createObjectURL(file))
    setResultado(null)
    analizar(file)
  }

  async function analizar(file: File) {
    setAnalizando(true)
    setProgreso(0)
    const iv = setInterval(() => setProgreso(p => p >= 88 ? 88 : p + 12), 250)

    // Sube imagen a Supabase Storage
    const ext  = file.name.split('.').pop()
    const path = `scans/${Date.now()}.${ext}`
    await supabase.storage.from('plant-images').upload(path, file, { upsert: true })

    // Aquí llamas a tu endpoint real: POST /identificar-planta
    // Por ahora simulamos la respuesta de la IA
    await new Promise(r => setTimeout(r, 2200))
    clearInterval(iv)
    setProgreso(100)

    const res: Resultado = {
      nombre_comun: 'Monstera Deliciosa',
      nombre_cientifico: 'Monstera deliciosa',
      confianza: 97,
      luz: 'Luz indirecta',
      riego: 'Cada 7 días',
    }
    setResultado(res)

    // Guarda en identificaciones_ia
    const { data: { user } } = await supabase.auth.getUser()
    await supabase.from('identificaciones_ia').insert({
      imagen_url: path,
      resultado_json: res,
      confianza: res.confianza,
      ...(user ? {} : {}),
    })

    setAnalizando(false)
  }

  return (
    <div className="min-h-screen bg-[#f7f7f6] flex flex-col">
      <header className="flex items-center bg-[#f7f7f6]/80 backdrop-blur-md sticky top-0 z-20 p-4 justify-between border-b border-[#325926]/10">
        <button onClick={() => router.back()} className="flex size-10 items-center justify-center rounded-full hover:bg-[#325926]/10">
          <span className="material-symbols-outlined">arrow_back</span>
        </button>
        <h2 className="text-lg font-bold flex-1 text-center">Identificador IA</h2>
        <div className="w-10" />
      </header>

      <main className="flex-1 flex flex-col">
        {/* Visor cámara */}
        <div className="relative m-4 rounded-2xl overflow-hidden bg-slate-800 min-h-64 flex-1">
          {preview
            ? <img src={preview} className="w-full h-full object-cover absolute inset-0" alt="planta"/>
            : <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 text-slate-400">
                <span className="material-symbols-outlined text-7xl">photo_camera</span>
                <p className="text-sm font-medium">Toma o sube una foto de la planta</p>
              </div>
          }

          {/* Overlay */}
          {preview && (
            <div className="absolute inset-0">
              {/* Esquinas */}
              <div className="absolute top-6 left-6 w-10 h-10 border-t-4 border-l-4 border-white/80 rounded-tl-lg"/>
              <div className="absolute top-6 right-6 w-10 h-10 border-t-4 border-r-4 border-white/80 rounded-tr-lg"/>
              <div className="absolute bottom-20 left-6 w-10 h-10 border-b-4 border-l-4 border-white/80 rounded-bl-lg"/>
              <div className="absolute bottom-20 right-6 w-10 h-10 border-b-4 border-r-4 border-white/80 rounded-br-lg"/>
              {analizando && <div className="scan-line"/>}
              {!analizando && resultado && (
                <>
                  <div className="absolute top-1/3 left-1/4 size-3 bg-[#325926] rounded-full animate-pulse border-2 border-white"/>
                  <div className="absolute top-1/2 right-1/3 size-3 bg-[#325926] rounded-full animate-pulse border-2 border-white"/>
                </>
              )}
            </div>
          )}

          {/* Controles */}
          <div className="absolute bottom-5 left-0 right-0 flex items-center justify-center gap-6">
            <label className="flex items-center justify-center rounded-full size-12 bg-black/40 text-white backdrop-blur-sm border border-white/20 cursor-pointer">
              <span className="material-symbols-outlined">image</span>
              <input type="file" accept="image/*" className="hidden" onChange={handleFile}/>
            </label>
            <label className="flex items-center justify-center rounded-full size-20 bg-[#325926] text-white border-4 border-white/30 shadow-xl cursor-pointer">
              <span className="material-symbols-outlined text-4xl">photo_camera</span>
              <input type="file" accept="image/*" capture="environment" className="hidden" onChange={handleFile}/>
            </label>
            <button className="flex items-center justify-center rounded-full size-12 bg-black/40 text-white backdrop-blur-sm border border-white/20">
              <span className="material-symbols-outlined">flash_on</span>
            </button>
          </div>
        </div>

        {/* Panel resultado */}
        <div className="bg-white rounded-t-2xl px-5 pt-5 pb-4 border-t border-[#325926]/10 shadow-2xl">
          <div className="w-10 h-1.5 bg-slate-200 rounded-full mx-auto mb-5"/>

          {analizando && (
            <div className="space-y-3 mb-6">
              <div className="flex justify-between">
                <div className="flex items-center gap-2">
                  <div className="size-2 bg-[#325926] rounded-full animate-pulse"/>
                  <p className="font-semibold text-lg">Analizando planta...</p>
                </div>
                <p className="text-[#325926] font-bold text-lg">{progreso}%</p>
              </div>
              <div className="h-3 bg-[#325926]/10 rounded-full overflow-hidden">
                <div className="h-full bg-[#325926] rounded-full transition-all" style={{ width: `${progreso}%` }}/>
              </div>
              <p className="text-slate-400 text-sm italic">Identificando especie y salud del follaje...</p>
            </div>
          )}

          {resultado && !analizando && (
            <>
              <div className="bg-[#325926]/5 rounded-2xl p-4 border border-[#325926]/10 mb-4">
                <div className="flex gap-4">
                  {preview && <div className="size-20 rounded-xl overflow-hidden shrink-0">
                    <img src={preview} className="w-full h-full object-cover" alt=""/>
                  </div>}
                  <div className="flex-1">
                    <div className="flex justify-between items-start">
                      <div>
                        <h3 className="font-bold text-slate-900">{resultado.nombre_comun}</h3>
                        <p className="text-sm text-slate-500 italic">{resultado.nombre_cientifico}</p>
                      </div>
                      <span className="bg-[#325926]/20 text-[#325926] text-xs font-bold px-2 py-1 rounded-full">
                        {resultado.confianza}%
                      </span>
                    </div>
                    <div className="flex gap-2 mt-2 flex-wrap">
                      {[{ icon: 'water_drop', label: resultado.riego }, { icon: 'wb_sunny', label: resultado.luz }].map(c => (
                        <span key={c.label} className="flex items-center gap-1 text-[10px] bg-white px-2 py-1 rounded-lg border border-[#325926]/10">
                          <span className="material-symbols-outlined text-[#325926]" style={{ fontSize: '11px' }}>{c.icon}</span>
                          {c.label}
                        </span>
                      ))}
                    </div>
                  </div>
                </div>
              </div>
              <button onClick={() => router.push('/marketplace')}
                className="w-full bg-[#325926] text-white font-bold py-4 rounded-xl flex items-center justify-center gap-2 shadow-lg shadow-[#325926]/20">
                <span className="material-symbols-outlined">search</span>
                Buscar en Marketplace
              </button>
            </>
          )}

          {!preview && !analizando && (
            <p className="text-center text-slate-400 text-sm pb-2">Apunta la cámara a cualquier planta para identificarla con IA</p>
          )}
        </div>
      </main>
    </div>
  )
}
