import plugin from 'tailwindcss/plugin'

export default plugin(function ({ addComponents }) {
  addComponents({
    // ── Panels ──────────────────────────────────────────────────────────────

    '.glass-panel': {
      background: 'rgba(255, 255, 255, 0.58)',
      backdropFilter: 'blur(28px) saturate(180%)',
      WebkitBackdropFilter: 'blur(28px) saturate(180%)',
      border: '1px solid rgba(255, 255, 255, 0.9)',
      boxShadow: [
        '0 12px 32px -4px rgba(20, 35, 90, 0.06)',
        '0 4px 12px -2px rgba(20, 35, 90, 0.03)',
        'inset 0 1px 0 rgba(255, 255, 255, 0.95)',
      ].join(', '),
      borderRadius: '20px',
    },

    '.glass-panel-subtle': {
      background: 'rgba(255, 255, 255, 0.48)',
      backdropFilter: 'blur(20px) saturate(170%)',
      WebkitBackdropFilter: 'blur(20px) saturate(170%)',
      border: '1px solid rgba(255, 255, 255, 0.85)',
      boxShadow: [
        '0 8px 24px -4px rgba(20, 35, 90, 0.04)',
        'inset 0 1px 0 rgba(255, 255, 255, 0.9)',
      ].join(', '),
      borderRadius: '16px',
    },

    '.glass-device-card': {
      background: 'rgba(255, 255, 255, 0.58)',
      backdropFilter: 'blur(24px) saturate(180%)',
      WebkitBackdropFilter: 'blur(24px) saturate(180%)',
      border: '1px solid rgba(255, 255, 255, 0.95)',
      borderRadius: '20px',
      boxShadow: [
        '0 10px 30px -4px rgba(30, 41, 75, 0.05)',
        'inset 0 1px 1px rgba(255, 255, 255, 1)',
      ].join(', '),
      transition: [
        'transform 0.45s cubic-bezier(0.16, 1, 0.3, 1)',
        'box-shadow 0.45s cubic-bezier(0.16, 1, 0.3, 1)',
        'border-color 0.3s ease',
      ].join(', '),
      willChange: 'transform',
      position: 'relative',
      '&:hover': {
        transform: 'translateY(-5px)',
        boxShadow: [
          '0 24px 48px -8px rgba(44, 75, 224, 0.13)',
          '0 4px 12px 0 rgba(44, 75, 224, 0.07)',
          'inset 0 1px 1px rgba(255, 255, 255, 1)',
        ].join(', '),
        borderColor: 'rgba(44, 75, 224, 0.32)',
      },
    },

    '.light-glass-panel': {
      background: 'rgba(255, 255, 255, 0.58)',
      backdropFilter: 'blur(24px) saturate(180%)',
      WebkitBackdropFilter: 'blur(24px) saturate(180%)',
      border: '1px solid rgba(255, 255, 255, 0.95)',
      boxShadow: [
        '0 14px 38px rgba(30, 45, 90, 0.05)',
        '0 2px 6px rgba(0, 0, 0, 0.02)',
      ].join(', '),
      borderRadius: '20px',
    },

    '.glass-device-card.offline-card': {
      background: 'rgba(254, 242, 242, 0.82)',
      borderColor: 'rgba(252, 165, 165, 0.5)',
      '&:hover': {
        borderColor: 'rgba(239, 68, 68, 0.5)',
        boxShadow: '0 20px 40px -8px rgba(239, 68, 68, 0.12)',
      },
    },

    // ── Buttons ─────────────────────────────────────────────────────────────

    '.glass-btn': {
      display: 'inline-flex',
      alignItems: 'center',
      background: 'rgba(255, 255, 255, 0.80)',
      backdropFilter: 'blur(16px) saturate(160%)',
      WebkitBackdropFilter: 'blur(16px) saturate(160%)',
      border: '1px solid rgba(255, 255, 255, 0.85)',
      boxShadow: [
        '0 2px 8px rgba(20, 35, 90, 0.06)',
        'inset 0 1px 0 rgba(255, 255, 255, 0.9)',
      ].join(', '),
      color: '#475569',
      fontWeight: '600',
      transition: [
        'background 0.28s cubic-bezier(0.16, 1, 0.3, 1)',
        'box-shadow 0.28s cubic-bezier(0.16, 1, 0.3, 1)',
        'border-color 0.2s ease',
        'color 0.15s ease',
      ].join(', '),
      '&:hover': {
        background: 'rgba(255, 255, 255, 0.96)',
        boxShadow: [
          '0 4px 16px rgba(20, 35, 90, 0.10)',
          'inset 0 1px 0 rgba(255, 255, 255, 1)',
        ].join(', '),
        borderColor: 'rgba(203, 213, 225, 0.8)',
        color: '#0f172a',
      },
    },

    '.glass-btn-brand': {
      display: 'inline-flex',
      alignItems: 'center',
      background: 'rgba(44, 75, 224, 0.08)',
      backdropFilter: 'blur(16px) saturate(160%)',
      WebkitBackdropFilter: 'blur(16px) saturate(160%)',
      border: '1px solid rgba(44, 75, 224, 0.28)',
      boxShadow: [
        '0 2px 8px rgba(44, 75, 224, 0.08)',
        'inset 0 1px 0 rgba(255, 255, 255, 0.6)',
      ].join(', '),
      color: '#2c4be0',
      fontWeight: '600',
      transition: [
        'background 0.28s cubic-bezier(0.16, 1, 0.3, 1)',
        'box-shadow 0.28s cubic-bezier(0.16, 1, 0.3, 1)',
        'border-color 0.2s ease',
      ].join(', '),
      '&:hover': {
        background: 'rgba(44, 75, 224, 0.14)',
        boxShadow: [
          '0 4px 16px rgba(44, 75, 224, 0.14)',
          'inset 0 1px 0 rgba(255, 255, 255, 0.7)',
        ].join(', '),
        borderColor: 'rgba(44, 75, 224, 0.40)',
      },
    },

    // ── Toggle group container ───────────────────────────────────────────────

    '.glass-toggle': {
      background: 'rgba(255, 255, 255, 0.55)',
      backdropFilter: 'blur(12px) saturate(150%)',
      WebkitBackdropFilter: 'blur(12px) saturate(150%)',
      border: '1px solid rgba(255, 255, 255, 0.80)',
      boxShadow: 'inset 0 1px 0 rgba(255, 255, 255, 0.9)',
    },

    // ── Badges ───────────────────────────────────────────────────────────────

    '.glass-badge': {
      display: 'inline-flex',
      alignItems: 'center',
      backdropFilter: 'blur(12px) saturate(180%)',
      WebkitBackdropFilter: 'blur(12px) saturate(180%)',
      boxShadow: 'inset 0 1px 0 rgba(255, 255, 255, 0.85), 0 1px 4px rgba(20, 35, 90, 0.06)',
    },

    // ── Misc ─────────────────────────────────────────────────────────────────

    '.micro-pill': {
      background: 'rgba(255, 255, 255, 0.32)',
      border: '1px solid rgba(255, 255, 255, 0.75)',
      backdropFilter: 'blur(14px) saturate(170%)',
      WebkitBackdropFilter: 'blur(14px) saturate(170%)',
      boxShadow: [
        'inset 0 1px 0 rgba(255, 255, 255, 0.90)',
        'inset 0 -1px 0 rgba(0, 0, 0, 0.04)',
        '0 2px 6px rgba(20, 35, 90, 0.07)',
      ].join(', '),
      borderRadius: '9999px',
      padding: '4px 10px',
      fontSize: '11px',
      lineHeight: '14px',
      fontWeight: '500',
      color: '#334155',
      display: 'inline-flex',
      alignItems: 'center',
      gap: '5px',
      transition: [
        'background 0.22s cubic-bezier(0.16, 1, 0.3, 1)',
        'border-color 0.18s ease',
        'color 0.15s ease',
      ].join(', '),
      '&:hover': {
        background: 'rgba(255, 255, 255, 0.95)',
        borderColor: 'rgba(148, 163, 184, 0.8)',
        color: '#0f172a',
      },
    },

    '.glow-blob': {
      position: 'absolute',
      borderRadius: '9999px',
      filter: 'blur(90px)',
      pointerEvents: 'none',
      zIndex: '0',
    },

    '.stream-scanlines': {
      background: [
        'linear-gradient(rgba(15, 23, 42, 0.02) 50%, rgba(15, 23, 42, 0.25) 50%)',
        'linear-gradient(90deg, rgba(44, 75, 224, 0.03), rgba(16, 185, 129, 0.02), rgba(44, 75, 224, 0.03))',
      ].join(', '),
      backgroundSize: '100% 3px, 6px 100%',
    },
  })
})
