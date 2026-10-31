import { useId } from "react";
import { dayPeriod } from "./dayPeriod";

export function DayPeriodArt({
  date,
  timezone,
}: {
  date: Date;
  timezone: string;
}) {
  const period = dayPeriod(date, timezone);
  const id = useId();
  const elevation =
    period.id === "noon"
      ? 23
      : period.id === "morning" || period.id === "afternoon"
        ? 31
        : 45;
  return (
    <span
      className="life-period-art"
      data-period={period.id}
      role="img"
      aria-label={`${period.label}时段`}
      title={`${period.label} · 当地时段`}
    >
      <svg viewBox="0 0 80 80" aria-hidden="true">
        <defs>
          <radialGradient id={id} cx="40%" cy="30%" r="75%">
            <stop offset="0" stopColor="var(--period-glow)" />
            <stop offset="1" stopColor="var(--period-sky)" stopOpacity="0" />
          </radialGradient>
        </defs>
        <circle cx="40" cy="40" r="38" fill={`url(#${id})`} />
        <g
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          {period.night ? (
            <>
              <path
                d="M46 18a13 13 0 1 0 15 18 14 14 0 0 1-15-18Z"
                fill="var(--period-orb)"
              />
              <path d="M25 20v5m-2.5-2.5h5m31 24v4m-2-2h4" />
              {period.id === "late-night" && (
                <path d="M23 38v3m-1.5-1.5h3m38-23v3m-1.5-1.5h3" />
              )}
            </>
          ) : (
            <>
              <circle cx="40" cy={elevation} r="10" fill="var(--period-orb)" />
              <path
                d={`M40 ${elevation - 16}v-3m-16 19h-3m38 0h-3m-27-11-2-2m24 0-2 2`}
              />
              {(period.id === "dawn" || period.id === "dusk") && (
                <path
                  d="M21 45h38"
                  stroke="var(--period-glow)"
                  strokeWidth="5"
                />
              )}
            </>
          )}
          <path
            d="M15 54c9-4 16-4 25 0s16 4 25 0M20 62c8-3 13-3 20 0s13 3 20 0"
            fill="none"
            opacity=".6"
          />
          {period.id === "morning" && (
            <path d="M17 39q4-5 8 0q4-5 8 0" fill="none" opacity=".7" />
          )}
          {period.id === "afternoon" && (
            <path
              d="M50 41q-1-5 4-5q5-7 10-1q7 0 5 6Z"
              fill="var(--period-glow)"
              opacity=".8"
            />
          )}
        </g>
      </svg>
    </span>
  );
}
