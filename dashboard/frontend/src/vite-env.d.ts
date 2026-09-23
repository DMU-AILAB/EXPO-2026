/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** 백엔드 주소. 비우면 http://localhost:8000 */
  readonly VITE_API_BASE?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
