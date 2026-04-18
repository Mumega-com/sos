/**
 * StatCard — 1080×1080 square (Instagram / X)
 * 15s · single stat with animated counter
 * Dark bg, gold/cyan accent, monospace label
 */

import {
  AbsoluteFill,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";

const BG = "#0D0D0D";
const TEXT = "#F0F0F0";
const TEXT_MUTED = "rgba(240,240,240,0.5)";
const MONO = "'Courier New', monospace";
const SANS = "'Helvetica Neue', Arial, sans-serif";

export interface StatCardProps {
  value: string;
  label: string;
  sublabel?: string;
  color: string;
}

export const StatCard: React.FC<StatCardProps> = ({
  value,
  label,
  sublabel,
  color,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const valueScale = spring({
    frame: frame - 15,
    fps,
    config: { damping: 10, stiffness: 60 },
  });

  const labelOpacity = interpolate(frame, [25, 45], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  const sublabelOpacity = interpolate(frame, [40, 60], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  const brandOpacity = interpolate(frame, [60, 80], [0, 0.4], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill
      style={{
        background: BG,
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: 24,
      }}
    >
      <div
        style={{
          fontSize: 160,
          fontFamily: SANS,
          fontWeight: 700,
          color,
          transform: `scale(${valueScale})`,
          lineHeight: 1,
        }}
      >
        {value}
      </div>
      <div
        style={{
          fontSize: 32,
          fontFamily: MONO,
          color: TEXT,
          opacity: labelOpacity,
          textTransform: "uppercase",
          letterSpacing: 4,
          textAlign: "center",
          padding: "0 40px",
        }}
      >
        {label}
      </div>
      {sublabel && (
        <div
          style={{
            fontSize: 24,
            fontFamily: MONO,
            color: TEXT_MUTED,
            opacity: sublabelOpacity,
            textAlign: "center",
          }}
        >
          {sublabel}
        </div>
      )}
      <div
        style={{
          position: "absolute",
          bottom: 40,
          fontFamily: MONO,
          fontSize: 18,
          color: TEXT,
          opacity: brandOpacity,
          letterSpacing: 6,
        }}
      >
        MUMEGA
      </div>
    </AbsoluteFill>
  );
};
