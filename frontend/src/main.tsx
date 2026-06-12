import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import { installSafeAreaInsets } from './safeArea'
import App from './App.tsx'

installSafeAreaInsets()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
