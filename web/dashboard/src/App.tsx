import { useEffect } from 'react'
import { Box, Typography, Button, Container, Grid, Paper } from '@mui/material'
import { useDispatch, useSelector } from 'react-redux'
import { RootState } from './store'
import { loginSuccess } from './features/auth/authSlice'
import { fetchTasks, addTask, completeTask } from './features/tasks/tasksSlice'
import WitnessCard from './components/WitnessCard'
import { AnimatePresence, motion } from 'framer-motion'
import { useTelegram } from './hooks/useTelegram'
import { TonConnectButton } from '@tonconnect/ui-react'

function App() {
  const dispatch = useDispatch()
  const { tg, user: tgUser } = useTelegram()
  const { isAuthenticated, user } = useSelector((state: RootState) => state.auth)
  const tasks = useSelector((state: RootState) => state.tasks.items)

  // --- Telegram Auto-Inoculation ---
  useEffect(() => {
    if (tgUser && !isAuthenticated) {
      console.log('🔮 Telegram Session Detected. Auto-Inoculating...')
      dispatch(loginSuccess({
        user: { 
          id: `user:${tgUser.id}`, 
          name: tgUser.username || tgUser.first_name, 
          role: 'Rider' 
        },
        token: tg?.initData || 'mock_jwt'
      }))
      dispatch(fetchTasks() as any)
    }
  }, [tgUser, isAuthenticated, dispatch, tg])

  // --- Real-time Nervous System Integration ---
  useEffect(() => {
    if (isAuthenticated && user) {
      // Use standard WebSocket but fallback if in non-secure environment
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
      const socket = new WebSocket(`${protocol}//${window.location.host}/ws/nervous-system/${user.id}`)

      socket.onopen = () => {
        console.log('🔌 Connected to SOS Nervous System')
      }

      socket.onmessage = (event) => {
        const message = JSON.parse(event.data)
        if (message.type === 'task_create') {
          dispatch(addTask({
            id: message.payload.task_id,
            title: message.payload.title,
            status: 'pending',
            bounty: { amount: 10, token: 'MIND' },
            imageUrl: `/assets/generated/default_mycelium.png`
          }))
          // Haptic feedback for new task
          tg?.HapticFeedback.notificationOccurred('success')
        }
      }

      return () => {
        socket.close()
      }
    }
  }, [isAuthenticated, user, dispatch, tg])

  const handleWitness = (taskId: string, vote: 1 | -1, latency: number) => {
    console.log(`👁️ WITNESS EVENT: Task=${taskId}, Vote=${vote}, Latency=${latency.toFixed(2)}ms`)
    
    // Trigger Telegram Haptics
    if (vote === 1) {
      tg?.HapticFeedback.impactOccurred('medium')
    } else {
      tg?.HapticFeedback.notificationOccurred('warning')
    }

    // Call API to complete (delete) task
    dispatch(completeTask(taskId) as any)
  }

  const handleManualInoculate = () => {
    dispatch(loginSuccess({
      user: { id: 'user_1', name: 'Kasra', role: 'Architect' },
      token: 'mock_jwt'
    }))
    dispatch(fetchTasks() as any)
  }

  if (!isAuthenticated) {
    return (
      <Container maxWidth="sm" sx={{ height: '100vh', display: 'flex', alignItems: 'center' }}>
        <Paper sx={{ p: 4, width: '100%', textAlign: 'center', position: 'relative', overflow: 'hidden' }}>
          <Box sx={{ 
            position: 'absolute', top: -50, right: -50, width: 200, height: 200, 
            background: 'radial-gradient(circle, rgba(191,0,255,0.2) 0%, rgba(0,0,0,0) 70%)',
            filter: 'blur(40px)', zIndex: 0
          }} />
          <Box sx={{ position: 'relative', zIndex: 1 }}>
            <Typography variant="h1" gutterBottom sx={{ color: 'primary.main', fontSize: '3.5rem', fontWeight: 900 }}>
              SOS
            </Typography>
            <Typography variant="subtitle1" sx={{ mb: 4, color: 'text.secondary', letterSpacing: 2 }}>
              {tgUser ? 'SESSING TELEGRAM DNA...' : 'SOVEREIGN OPERATING SYSTEM'}
            </Typography>
            <Button 
              variant="contained" 
              size="large" 
              sx={{ py: 2, px: 6 }} 
              onClick={handleManualInoculate}
            >
              {tgUser ? 'SYNCING...' : 'INOCULATE'}
            </Button>
          </Box>
        </Paper>
      </Container>
    )
  }

  return (
    <Box sx={{ minHeight: '100vh', bgcolor: 'background.default', p: 3 }}>
      <Grid container spacing={3}>
        {/* Header */}
        <Grid item xs={12}>
          <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', borderBottom: '1px solid #222', pb: 2 }}>
            <Typography variant="h4" sx={{ fontWeight: 900, color: 'primary.main' }}>
              EMPIRE OF THE MIND
            </Typography>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
              <TonConnectButton />
              <Box sx={{ textAlign: 'right' }}>
                <Typography variant="body1" sx={{ fontWeight: 600 }}>
                  {user?.name}
                </Typography>
                <Typography variant="caption" sx={{ color: 'text.secondary', display: 'block' }}>
                  {user?.role} | LVL 10
                </Typography>
              </Box>
            </Box>
          </Box>
        </Grid>

        {/* Task Board */}
        <Grid item xs={12} md={8}>
          <Typography variant="h6" sx={{ mb: 3, color: 'text.secondary', fontWeight: 300, letterSpacing: 1 }}>
            PENDING WITNESS REQUESTS
          </Typography>
          <Grid container spacing={3}>
            <AnimatePresence mode="popLayout">
              {tasks.map((task) => (
                <Grid item xs={12} sm={6} key={task.id} component={motion.div} layout exit={{ scale: 0.8, opacity: 0 }}>
                  <WitnessCard task={task} onWitness={handleWitness} />
                </Grid>
              ))}
            </AnimatePresence>
          </Grid>
        </Grid>

        {/* Sidebar / Wallet */}
        <Grid item xs={12} md={4}>
          <Paper sx={{ p: 4, height: '100%', display: 'flex', flexDirection: 'column', justifyContent: 'center' }}>
            <Typography variant="h6" align="center" sx={{ color: 'text.secondary', mb: 2 }}>
              THE FORGE
            </Typography>
            <Box sx={{ my: 4, textAlign: 'center' }}>
              <Typography variant="h2" sx={{ color: 'primary.main', fontWeight: 900 }}>
                1,250
              </Typography>
              <Typography variant="body1" sx={{ color: 'text.secondary', letterSpacing: 4 }}>
                $MIND
              </Typography>
            </Box>
            <Button variant="outlined" fullWidth color="secondary" sx={{ py: 2, borderWidth: 2, fontWeight: 900 }}>
              TRANSMUTE TO TON
            </Button>
          </Paper>
        </Grid>
      </Grid>
    </Box>
  )
}

export default App