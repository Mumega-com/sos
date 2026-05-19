
import React, { useRef } from 'react';
import { Paper, Box, Typography } from '@mui/material';
import { motion, useMotionValue, useTransform } from 'framer-motion';

interface Task {
  id: string;
  title: string;
  bounty?: { amount: number; token: string };
  imageUrl?: string;
}

interface WitnessCardProps {
  task: Task;
  onWitness: (taskId: string, vote: 1 | -1, latency: number) => void;
}

const WitnessCard: React.FC<WitnessCardProps> = ({ task, onWitness }) => {
  const startTime = useRef<number>(0);
  
  // Motion values for drag
  const x = useMotionValue(0);
  const rotate = useTransform(x, [-200, 200], [-25, 25]);
  const opacity = useTransform(x, [-200, -150, 0, 150, 200], [0, 1, 1, 1, 0]);
  
  // Background color based on drag direction
  const background = useTransform(
    x,
    [-100, 0, 100],
    ['rgba(255, 0, 0, 0.2)', 'rgba(0, 0, 0, 0)', 'rgba(0, 255, 157, 0.2)']
  );

  const handleDragStart = () => {
    startTime.current = performance.now();
  };

  const handleDragEnd = (_: any, info: any) => {
    const latency = performance.now() - startTime.current;
    
    if (info.offset.x > 100) {
      // Swipe Right: Approve (+1)
      onWitness(task.id, 1, latency);
    } else if (info.offset.x < -100) {
      // Swipe Left: Shred (-1)
      onWitness(task.id, -1, latency);
    }
  };

  return (
    <Box sx={{ perspective: 1000, width: '100%', height: 220 }}>
      <motion.div
        style={{ x, rotate, opacity, width: '100%', height: '100%' }}
        drag="x"
        dragConstraints={{ left: 0, right: 0 }}
        onDragStart={handleDragStart}
        onDragEnd={handleDragEnd}
        whileTap={{ scale: 0.95 }}
      >
        <motion.div style={{ background, height: '100%', borderRadius: 0 }}>
          <Paper sx={{ 
            height: '100%',
            position: 'relative', 
            overflow: 'hidden',
            background: `linear-gradient(rgba(0,0,0,0.7), rgba(0,0,0,0.9)), url(${task.imageUrl || 'https://images.unsplash.com/photo-1614850523296-d8c1af93d400?q=80&w=500&auto=format&fit=crop'})`,
            backgroundSize: 'cover',
            display: 'flex',
            flexDirection: 'column',
            justifyContent: 'space-between',
            border: '1px solid rgba(0,255,157,0.1)',
            cursor: 'grab',
            '&:active': { cursor: 'grabbing' }
          }}>
            <Box sx={{ p: 2 }}>
              <Typography variant="h6" sx={{ fontWeight: 700, lineHeight: 1.2, color: 'white' }}>
                {task.title}
              </Typography>
              <Typography variant="caption" sx={{ color: 'rgba(255,255,255,0.5)' }}>
                ID: {task.id}
              </Typography>
            </Box>
            
            <Box sx={{ 
              p: 2, 
              bgcolor: 'rgba(0,0,0,0.5)', 
              backdropFilter: 'blur(5px)', 
              display: 'flex', 
              justifyContent: 'space-between', 
              alignItems: 'center' 
            }}>
              <Typography variant="body2" sx={{ color: 'secondary.main', fontWeight: 900 }}>
                +{task.bounty?.amount || 0} {task.bounty?.token || 'MIND'}
              </Typography>
              <Box sx={{ display: 'flex', gap: 1 }}>
                <Typography variant="caption" sx={{ color: 'primary.main', opacity: 0.7 }}>
                  SHRED ⟵
                </Typography>
                <Typography variant="caption" sx={{ color: 'primary.main', opacity: 0.7 }}>
                  ⟶ WITNESS
                </Typography>
              </Box>
            </Box>
          </Paper>
        </motion.div>
      </motion.div>
    </Box>
  );
};

export default WitnessCard;
