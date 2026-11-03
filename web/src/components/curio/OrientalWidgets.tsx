import React from "react";

/**
 * 方案 8-07 东方器物与天象微动效 SVG 组件集
 * 1:1 提炼自 design-proposals-v3/index.html
 */

/** 品牌宋韵星轨罗盘徽标 SVG */
export function BrandAstrolabeSvg() {
  return (
    <svg
      width="20"
      height="20"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      aria-hidden="true"
    >
      <circle cx="12" cy="12" r="8" strokeDasharray="3 3" />
      <path d="M12 2v4M12 18v4M2 12h4M18 12h4" />
      <circle cx="12" cy="12" r="2.5" fill="currentColor" />
    </svg>
  );
}

/** 格壹 · 节气气象：远山淡墨 + 呼吸日/月晕 + 双层宋式流云水平缓漂 SVG */
export function WeatherLandscapeSvg() {
  return (
    <svg
      className="oriental-decor-svg"
      viewBox="0 0 150 120"
      fill="none"
      aria-hidden="true"
    >
      <circle
        cx="108"
        cy="34"
        r="18"
        fill="var(--seal)"
        opacity="0.18"
        className="lantern-pulse"
      />
      <path
        d="M40 95 Q75 55 110 95 T150 90 L150 120 L40 120 Z"
        fill="var(--accent)"
        opacity="0.09"
      />
      <g className="cloud-drift-1" opacity="0.5">
        <path
          d="M55 46 H125 M70 54 H135"
          stroke="var(--accent)"
          strokeWidth="3.5"
          strokeLinecap="round"
        />
      </g>
      <g className="cloud-drift-2" opacity="0.35">
        <path
          d="M45 64 H115"
          stroke="var(--seal)"
          strokeWidth="3"
          strokeLinecap="round"
        />
      </g>
    </svg>
  );
}

/** 格贰 · 昼夜圭表：缓慢自转的十二时辰星轨日晷罗盘 SVG */
export function SundialCompassSvg({ branch = "丑" }: { branch?: string }) {
  return (
    <svg
      width="70"
      height="70"
      viewBox="0 0 80 80"
      fill="none"
      className="curio-sundial-svg"
      aria-hidden="true"
    >
      <circle
        cx="40"
        cy="40"
        r="34"
        stroke="var(--border-outer)"
        strokeWidth="1.4"
      />
      <g className="sundial-ring">
        <circle
          cx="40"
          cy="40"
          r="27"
          stroke="var(--accent)"
          strokeWidth="1.6"
          strokeDasharray="4 6"
        />
        <circle cx="40" cy="13" r="3.2" fill="var(--seal)" />
        <circle cx="67" cy="40" r="2.2" fill="var(--accent)" />
      </g>
      <circle
        cx="40"
        cy="40"
        r="15"
        stroke="var(--border-outer)"
        strokeWidth="1.2"
      />
      <text
        x="40"
        y="44"
        textAnchor="middle"
        fill="var(--accent)"
        fontSize="12"
        fontFamily="var(--font-serif)"
        fontWeight="bold"
      >
        {branch.slice(0, 1)}
      </text>
    </svg>
  );
}

/** 格叁 · 此刻感受：古琴五弦水波微颤 SVG */
export function GuqinStringsSvg() {
  return (
    <svg
      width="46"
      height="18"
      viewBox="0 0 46 18"
      fill="none"
      className="curio-guqin-svg"
      aria-hidden="true"
    >
      <rect
        className="qin-string"
        x="4"
        y="4"
        width="2.5"
        height="10"
        rx="1"
        fill="var(--accent)"
      />
      <rect
        className="qin-string"
        x="12"
        y="2"
        width="2.5"
        height="14"
        rx="1"
        fill="var(--seal)"
        style={{ animationDelay: "0.3s" }}
      />
      <rect
        className="qin-string"
        x="20"
        y="5"
        width="2.5"
        height="8"
        rx="1"
        fill="var(--accent)"
        style={{ animationDelay: "0.6s" }}
      />
      <rect
        className="qin-string"
        x="28"
        y="1"
        width="2.5"
        height="16"
        rx="1"
        fill="var(--accent)"
        style={{ animationDelay: "0.2s" }}
      />
      <rect
        className="qin-string"
        x="36"
        y="4"
        width="2.5"
        height="10"
        rx="1"
        fill="var(--seal)"
        style={{ animationDelay: "0.5s" }}
      />
    </svg>
  );
}

/** 格肆 · 角色生活地点：呼吸暖光宣纸灯笼 SVG */
export function LanternGlowSvg() {
  return (
    <svg
      width="44"
      height="44"
      viewBox="0 0 48 48"
      fill="none"
      className="lantern-pulse curio-lantern-svg"
      aria-hidden="true"
    >
      <circle cx="24" cy="24" r="20" fill="var(--seal)" opacity="0.14" />
      <rect
        x="15"
        y="12"
        width="18"
        height="24"
        rx="5"
        stroke="var(--accent)"
        strokeWidth="1.8"
        fill="var(--bg-subtle)"
      />
      <circle cx="24" cy="24" r="4.5" fill="var(--seal)" opacity="0.85" />
      <line
        x1="24"
        y1="6"
        x2="24"
        y2="12"
        stroke="var(--accent)"
        strokeWidth="1.8"
      />
      <line
        x1="24"
        y1="36"
        x2="24"
        y2="42"
        stroke="var(--seal)"
        strokeWidth="1.8"
      />
    </svg>
  );
}

/** 媒体订阅环形进度盘 SVG */
export function CurioDonutRing({
  total,
  active,
  caption,
  tone = "accent",
}: {
  total: number;
  active: number;
  caption: string;
  tone?: "accent" | "seal";
}) {
  const circumference = 2 * Math.PI * 52; // ~326.7
  const ratio = total > 0 ? Math.min(1, Math.max(0.06, active / total)) : 0;
  const dash = Math.round(ratio * circumference);
  const strokeColor = tone === "seal" ? "var(--seal)" : "var(--accent)";

  return (
    <div className="donut-wrap">
      <svg
        width="132"
        height="132"
        viewBox="0 0 132 132"
        className="curio-donut-svg"
        aria-hidden="true"
      >
        <circle
          cx="66"
          cy="66"
          r="52"
          fill="none"
          stroke="var(--border-outer)"
          strokeWidth="10"
        />
        <circle
          cx="66"
          cy="66"
          r="52"
          fill="none"
          stroke={strokeColor}
          strokeWidth="10"
          strokeLinecap="round"
          strokeDasharray={`${dash} 330`}
          transform="rotate(-90 66 66)"
        />
      </svg>
      <div className="donut-center">
        <strong>{total}</strong>
        <span>{caption}</span>
      </div>
    </div>
  );
}

/** 通用博古卡片头部（带方形宋韵篆字印章图标与虚线底栏） */
export function CurioSectionHead({
  seal,
  title,
  subtitle,
  action,
  level = 3,
}: {
  seal: string;
  title: React.ReactNode;
  subtitle?: React.ReactNode;
  action?: React.ReactNode;
  level?: 2 | 3;
}) {
  const Heading = level === 2 ? "h2" : "h3";
  return (
    <div className="section-head">
      <div className="section-title-group">
        <div className="section-seal-icon" aria-hidden="true">
          {seal.slice(0, 1)}
        </div>
        <div>
          <Heading className="section-title">{title}</Heading>
          {subtitle && <div className="section-sub">{subtitle}</div>}
        </div>
      </div>
      {action && <div className="section-head-action">{action}</div>}
    </div>
  );
}
