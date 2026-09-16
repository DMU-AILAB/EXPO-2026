export default function GlowBlobs() {
  return (
    <div className="fixed inset-0 overflow-hidden pointer-events-none z-0">
      <div className="glow-blob w-[680px] h-[680px] bg-blue-200/70 top-[-140px] left-[-80px] animate-float-slow" />
      <div className="glow-blob w-[620px] h-[620px] bg-emerald-200/60 bottom-[-100px] right-[-60px] animate-pulse-glow" />
      <div className="glow-blob w-[500px] h-[500px] bg-violet-200/50 top-[30%] left-[45%]" />
      <div className="glow-blob w-[460px] h-[460px] bg-amber-100/40 top-[15%] right-[12%]" />
    </div>
  )
}
