/**
 * TopicHighlight — 1080×1920 vertical video (TikTok / Reels / Shorts)
 * 60s · 5 scenes · data from topic page
 *
 * Scene 1: Hook text (big, animated)
 * Scene 2: Topic title + "our take"
 * Scene 3: Stats (animated counters)
 * Scene 4: Key insight
 * Scene 5: CTA
 */

import {
  AbsoluteFill,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
  Sequence,
} from "remotion";

// ── Brand ────────────────────────────────────────────────────────
const GOLD = "#D4A017";
const CYAN = "#06B6D4";
const BG = "#0D0D0D";
const TEXT = "#F0F0F0";
const TEXT_MUTED = "rgba(240,240,240,0.5)";
const MONO = "'Courier New', monospace";
const SANS = "'Helvetica Neue', Arial, sans-serif";

// ── Types ────────────────────────────────────────────────────────
export interface TopicHighlightProps {
  topicTitle: string;
  hook: string;
  stats: Array<{ label: string; value: string; color: string }>;
  insight: string;
  cta: string;
}

// ── Animated Counter ─────────────────────────────────────────────
const AnimatedValue: React.FC<{
  value: string;
  color: string;
  delay: number;
}> = ({ value, color, delay }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const scale = spring({
    frame: frame - delay,
    fps,
    config: { damping: 12, stiffness: 80 },
  });

  const opacity = interpolate(frame - delay, [0, 15], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  return (
    <div
      style={{
        transform: `scale(${scale})`,
        opacity,
        fontSize: 96,
        fontFamily: SANS,
        fontWeight: 700,
        color,
        textAlign: "center",
        lineHeight: 1,
      }}
    >
      {value}
    </div>
  );
};

// ── Fade In Text ─────────────────────────────────────────────────
const FadeText: React.FC<{
  children: React.ReactNode;
  delay?: number;
  style?: React.CSSProperties;
}> = ({ children, delay = 0, style = {} }) => {
  const frame = useCurrentFrame();

  const opacity = interpolate(frame - delay, [0, 20], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  const y = interpolate(frame - delay, [0, 20], [30, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  return (
    <div
      style={{
        opacity,
        transform: `translateY(${y}px)`,
        ...style,
      }}
    >
      {children}
    </div>
  );
};

// ── Scene: Hook ──────────────────────────────────────────────────
const HookScene: React.FC<{ hook: string }> = ({ hook }) => {
  return (
    <AbsoluteFill
      style={{
        background: BG,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: 60,
      }}
    >
      <FadeText
        delay={10}
        style={{
          fontSize: 64,
          fontFamily: SANS,
          fontWeight: 700,
          color: TEXT,
          textAlign: "center",
          lineHeight: 1.3,
          whiteSpace: "pre-line",
        }}
      >
        {hook}
      </FadeText>
    </AbsoluteFill>
  );
};

// ── Scene: Topic Title ───────────────────────────────────────────
const TitleScene: React.FC<{ title: string }> = ({ title }) => {
  return (
    <AbsoluteFill
      style={{
        background: BG,
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        padding: 60,
        gap: 40,
      }}
    >
      <FadeText
        delay={5}
        style={{
          fontFamily: MONO,
          fontSize: 24,
          color: GOLD,
          textTransform: "uppercase",
          letterSpacing: 6,
        }}
      >
        MUMEGA TOPICS
      </FadeText>
      <FadeText
        delay={15}
        style={{
          fontSize: 72,
          fontFamily: SANS,
          fontWeight: 700,
          color: TEXT,
          textAlign: "center",
          lineHeight: 1.2,
        }}
      >
        {title}
      </FadeText>
      <FadeText
        delay={30}
        style={{
          width: 120,
          height: 3,
          background: GOLD,
        }}
      >
        <div />
      </FadeText>
    </AbsoluteFill>
  );
};

// ── Scene: Stats ─────────────────────────────────────────────────
const StatsScene: React.FC<{
  stats: Array<{ label: string; value: string; color: string }>;
}> = ({ stats }) => {
  return (
    <AbsoluteFill
      style={{
        background: BG,
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: 80,
        padding: 60,
      }}
    >
      {stats.map((stat, i) => (
        <div
          key={stat.label}
          style={{
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            gap: 8,
          }}
        >
          <AnimatedValue
            value={stat.value}
            color={stat.color}
            delay={i * 20 + 10}
          />
          <FadeText
            delay={i * 20 + 20}
            style={{
              fontFamily: MONO,
              fontSize: 24,
              color: TEXT_MUTED,
              textTransform: "uppercase",
              letterSpacing: 3,
            }}
          >
            {stat.label}
          </FadeText>
        </div>
      ))}
    </AbsoluteFill>
  );
};

// ── Scene: Insight ───────────────────────────────────────────────
const InsightScene: React.FC<{ insight: string }> = ({ insight }) => {
  return (
    <AbsoluteFill
      style={{
        background: BG,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: 80,
      }}
    >
      <FadeText
        delay={10}
        style={{
          fontSize: 48,
          fontFamily: SANS,
          fontWeight: 400,
          color: TEXT,
          textAlign: "center",
          lineHeight: 1.5,
          whiteSpace: "pre-line",
        }}
      >
        {insight}
      </FadeText>
    </AbsoluteFill>
  );
};

// ── Scene: CTA ───────────────────────────────────────────────────
const CTAScene: React.FC<{ cta: string }> = ({ cta }) => {
  return (
    <AbsoluteFill
      style={{
        background: BG,
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: 40,
        padding: 60,
      }}
    >
      <FadeText
        delay={5}
        style={{
          fontSize: 48,
          fontFamily: SANS,
          fontWeight: 700,
          color: TEXT,
          textAlign: "center",
        }}
      >
        the living page.
      </FadeText>
      <FadeText
        delay={20}
        style={{
          fontSize: 36,
          fontFamily: MONO,
          color: GOLD,
          textAlign: "center",
        }}
      >
        {cta}
      </FadeText>
    </AbsoluteFill>
  );
};

// ── Main Composition ─────────────────────────────────────────────
export const TopicHighlight: React.FC<TopicHighlightProps> = ({
  topicTitle,
  hook,
  stats,
  insight,
  cta,
}) => {
  // 60s at 30fps = 1800 frames
  // Scene durations: hook 300, title 300, stats 450, insight 450, cta 300
  return (
    <AbsoluteFill style={{ background: BG }}>
      <Sequence from={0} durationInFrames={300} name="Hook">
        <HookScene hook={hook} />
      </Sequence>
      <Sequence from={300} durationInFrames={300} name="Title">
        <TitleScene title={topicTitle} />
      </Sequence>
      <Sequence from={600} durationInFrames={450} name="Stats">
        <StatsScene stats={stats} />
      </Sequence>
      <Sequence from={1050} durationInFrames={450} name="Insight">
        <InsightScene insight={insight} />
      </Sequence>
      <Sequence from={1500} durationInFrames={300} name="CTA">
        <CTAScene cta={cta} />
      </Sequence>
    </AbsoluteFill>
  );
};
