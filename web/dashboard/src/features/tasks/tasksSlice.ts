import { createSlice, createAsyncThunk, PayloadAction } from '@reduxjs/toolkit'
import axios from 'axios'

export interface Task {
  id: string
  title: string
  status: 'pending' | 'claimed' | 'done'
  bounty?: { amount: number; token: string }
  imageUrl?: string
}

interface TasksState {
  items: Task[]
  status: 'idle' | 'loading' | 'succeeded' | 'failed'
}

const initialState: TasksState = {
  items: [],
  status: 'idle',
}

export const fetchTasks = createAsyncThunk('tasks/fetchTasks', async () => {
  // Real API Call via Nginx Proxy -> Engine
  const response = await axios.get('/api/tasks')
  // The API returns { tasks: [...] }
  return response.data.tasks || []
})

export const completeTask = createAsyncThunk('tasks/completeTask', async (taskId: string) => {
  await axios.post(`/api/tasks/${taskId}/complete`)
  return taskId
})

const tasksSlice = createSlice({
  name: 'tasks',
  initialState,
  reducers: {
    addTask(state, action: PayloadAction<Task>) {
      state.items.unshift(action.payload)
    },
    removeTask(state, action: PayloadAction<string>) {
      state.items = state.items.filter(t => t.id !== action.payload)
    },
  },
  extraReducers: (builder) => {
    builder
      .addCase(fetchTasks.pending, (state) => {
        state.status = 'loading'
      })
      .addCase(fetchTasks.fulfilled, (state, action) => {
        state.status = 'succeeded'
        state.items = action.payload
      })
      .addCase(completeTask.fulfilled, (state, action) => {
        state.items = state.items.filter(t => t.id !== action.payload)
      })
  },
})

export const { addTask, removeTask } = tasksSlice.actions
export default tasksSlice.reducer