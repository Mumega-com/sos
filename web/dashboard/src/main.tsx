import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App.tsx'
import { CssBaseline, ThemeProvider, createTheme } from '@mui/material'
import { Provider } from 'react-redux'
import { store } from './store'
import { TonConnectUIProvider } from '@tonconnect/ui-react'

const darkTheme = createTheme({
  palette: {
    mode: 'dark',
    primary: {
      main: '#00ff9d', // Neon Green (Mycelium)
    },
    secondary: {
      main: '#bf00ff', // Neon Purple (Spore)
    },
    background: {
      default: '#0a0a0a', // Deep Void
      paper: '#111111',
    },
  },
  typography: {
    fontFamily: '"Roboto", "Helvetica", "Arial", sans-serif',
    h1: {
      fontWeight: 700,
      letterSpacing: '-0.02em',
    },
  },
  components: {
    MuiButton: {
      styleOverrides: {
        root: {
          borderRadius: 0, // Brutalist
          textTransform: 'none',
          fontWeight: 600,
        },
      },
    },
    MuiPaper: {
      styleOverrides: {
        root: {
          backgroundImage: 'none', // Remove gradients
          border: '1px solid #222',
        },
      },
    },
  },
})

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <TonConnectUIProvider manifestUrl="https://deck.mumega.com/tonconnect-manifest.json?v=2">
      <Provider store={store}>
        <ThemeProvider theme={darkTheme}>
          <CssBaseline />
          <App />
        </ThemeProvider>
      </Provider>
    </TonConnectUIProvider>
  </React.StrictMode>,
)