"use client";

import { useEffect, useRef } from "react";
import type * as React from "react";

/**
 * The dot field on the right of the hero.
 *
 * The reference animates this in WebGL. We deliberately do not. A redrawing
 * particle field is a permanent main-thread tax on a page whose Core Web Vitals
 * are the point, so this draws once on mount and once more per resize. The only
 * requestAnimationFrame in this file is the resize debounce.
 *
 * The field is fully deterministic: no Math.random, no Date.now, no
 * performance.now. The same viewport always produces the same dots.
 */

/** CSS px between grid centres on both axes. 9px yields about 2000 dots on a 1568px hero. */
const SPACING = 9;

/** Backing store scale. Capped at 2 so dots stay crisp without quadrupling fill cost. */
const MAX_DPR = 2;

/** Half-width of the crest band in normalised units. Outside it a dot is skipped. */
const BAND_HALF_WIDTH = 0.34;

/** Fallbacks, used only when the theme custom properties are missing. */
const FALLBACK_ACCENT = "#f84fcc";
const FALLBACK_ACCENT_2 = "#00dbe4";

/**
 * Colour zone boundaries along `band`, read from the dim edge inwards.
 *
 * The crest core is magenta and only the band immediately behind it is cyan.
 * The reference puts the accent on the leading edge; mapping cyan to the
 * highest band instead turns the whole wave teal and loses that.
 */
const ZONE_CYAN = 0.4;
const ZONE_MAGENTA = 0.72;

type Rgb = readonly [number, number, number];

/**
 * Parses a colour the canvas understands into RGB. The tokens are hex today;
 * anything else is treated as absent so the caller falls back rather than
 * drawing a colour the page never uses.
 */
function parseColor(value: string): Rgb | null {
  const hex = /^#([0-9a-f]{6})$/i.exec(value.trim());
  if (hex) {
    const n = Number.parseInt(hex[1], 16);
    return [(n >> 16) & 0xff, (n >> 8) & 0xff, n & 0xff];
  }
  const short = /^#([0-9a-f]{3})$/i.exec(value.trim());
  if (short) {
    const [r, g, b] = short[1].toLowerCase();
    return [
      Number.parseInt(r + r, 16),
      Number.parseInt(g + g, 16),
      Number.parseInt(b + b, 16),
    ];
  }
  return null;
}

function clamp01(value: number): number {
  if (value < 0) return 0;
  if (value > 1) return 1;
  return value;
}

function mix(from: Rgb, to: Rgb, t: number): string {
  const r = Math.round(from[0] + (to[0] - from[0]) * t);
  const g = Math.round(from[1] + (to[1] - from[1]) * t);
  const b = Math.round(from[2] + (to[2] - from[2]) * t);
  return `rgb(${r}, ${g}, ${b})`;
}

/**
 * Three zones along one `band` value, interpolated rather than snapped, so a
 * single dot transitions through them.
 *
 * The crest core is magenta and only the band immediately behind it is cyan.
 * Mapping cyan to the highest band instead turns the whole wave teal, which is
 * the opposite of the reference, where the accent rides the leading edge.
 */
function zoneColor(band: number, dim: Rgb, magenta: Rgb, cyan: Rgb): string {
  if (band < ZONE_CYAN) return mix(dim, cyan, clamp01(band / ZONE_CYAN));
  if (band < ZONE_MAGENTA)
    return mix(cyan, magenta, clamp01((band - ZONE_CYAN) / (ZONE_MAGENTA - ZONE_CYAN)));
  return `rgb(${magenta[0]}, ${magenta[1]}, ${magenta[2]})`;
}

export function DotWave(): React.JSX.Element {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    // A null context means the canvas is unavailable or blocklisted. Render
    // nothing rather than throwing and taking the hero down with it.
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const root = getComputedStyle(document.documentElement);
    const magenta = parseColor(root.getPropertyValue("--color-accent")) ??
      parseColor(FALLBACK_ACCENT);
    const cyan = parseColor(root.getPropertyValue("--color-accent-2")) ??
      parseColor(FALLBACK_ACCENT_2);
    if (!magenta || !cyan) return;
    const dim: Rgb = [120, 121, 128];

    const draw = (): void => {
      const parent = canvas.parentElement;
      if (!parent) return;
      const cssWidth = parent.clientWidth;
      const cssHeight = parent.clientHeight;
      if (cssWidth <= 0 || cssHeight <= 0) return;

      const dpr = Math.min(window.devicePixelRatio || 1, MAX_DPR);
      canvas.width = Math.round(cssWidth * dpr);
      canvas.height = Math.round(cssHeight * dpr);
      canvas.style.width = `${cssWidth}px`;
      canvas.style.height = `${cssHeight}px`;

      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

      const cols = Math.floor(cssWidth / SPACING);
      const rows = Math.floor(cssHeight / SPACING);

      for (let row = 0; row < rows; row += 1) {
        const ny = 1 - row / (rows - 1 || 1);
        const y = cssHeight - row * SPACING;

        for (let col = 0; col < cols; col += 1) {
          const nx = 1 - col / (cols - 1 || 1);
          const crest = 0.42 + 0.5 * Math.pow(1 - nx, 2.2) - 0.22 * ny;
          const band = Math.max(0, 1 - Math.abs(ny - crest) / BAND_HALF_WIDTH);
          if (band <= 0) continue;

          // Dissolve toward the left edge rather than ending abruptly.
          const edge = Math.pow(nx, 0.9);
          const alpha = 0.95 * band * edge;
          if (alpha <= 0.004) continue;

          const x = cssWidth - col * SPACING;
          const radius = 0.5 + 1.7 * band;

          ctx.globalAlpha = alpha;
          ctx.fillStyle = zoneColor(band, dim, magenta, cyan);
          ctx.beginPath();
          ctx.arc(x, y, radius, 0, Math.PI * 2);
          ctx.fill();
        }
      }

      ctx.globalAlpha = 1;
    };

    draw();

    // Resizes are collapsed to a single redraw per frame via a dirty flag, so a
    // drag-resize does not queue one draw per observer callback.
    let dirty = false;
    let frame: number | null = null;
    const schedule = (): void => {
      if (dirty) return;
      dirty = true;
      frame = requestAnimationFrame(() => {
        dirty = false;
        frame = null;
        draw();
      });
    };

    const observer = new ResizeObserver(schedule);
    if (canvas.parentElement) observer.observe(canvas.parentElement);

    return () => {
      observer.disconnect();
      if (frame !== null) cancelAnimationFrame(frame);
      frame = null;
      dirty = false;
    };
  }, []);

  return (
    <canvas
      ref={canvasRef}
      aria-hidden="true"
      className="pointer-events-none absolute inset-0 h-full w-full"
    />
  );
}
