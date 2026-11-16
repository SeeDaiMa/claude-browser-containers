import React from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { App } from './app/App'
import './app/styles/tokens.css'
import './app/styles/components.css'
import './app/styles/app.css'
import './app/styles/sidebar.css'
import './app/styles/pages.css'

createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </React.StrictMode>,
)
