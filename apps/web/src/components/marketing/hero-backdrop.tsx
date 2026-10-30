"use client";

import { useEffect, useRef } from "react";
import type * as React from "react";
import { motion, useAnimationFrame, useReducedMotion } from "motion/react";

import { DotWave } from "@/components/marketing/dot-wave";

/** CSS px between grid centres. Matches `DotWave`, so the two layers align dot for dot. */
const SPACING = 9;

/** Backing store scale. Capped at 2, exactly as `DotWave` does it. */
const MAX_DPR = 2;

/**
 * Half-width of the crest band in normalised units. `DotWave` uses 0.34 and
 * paints everything inside it; this layer uses a fraction of that, which is
 * what holds it to roughly 140 dots instead of the full 2000.
 */
const BAND_HALF_WIDTH = 0.1;

/** Fallbacks, used only when the theme custom properties are missing. Same pair `DotWave` inherits. */
const FALLBACK_ACCENT = "#f84fcc";
const FALLBACK_ACCENT_2 = "#00dbe4";

/** Colour zone boundaries along `band`, matching `DotWave` so both layers speak one language. */
const ZONE_CYAN = 0.4;
const ZONE_MAGENTA = 0.72;

/** Wall-clock floor between crest redraws, about 41.7ms. */
const CREST_FRAME_MS = 1000 / 24;

/** Seconds for one full breathe cycle of the crest offset. */
const BREATHE_PERIOD_S = 9;

/** Seconds for one pass of the travelling brightness wave along the crest. */
const SHIMMER_PERIOD_S = 5;

type Rgb = readonly [number, number, number];

/** Parses a colour the canvas understands into RGB. Anything else is absent, so the caller falls back. */
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

/** Three zones along one `band` value, interpolated rather than snapped. Magenta core, cyan behind it. */
function zoneColor(band: number, dim: Rgb, magenta: Rgb, cyan: Rgb): string {
  if (band < ZONE_CYAN) return mix(dim, cyan, clamp01(band / ZONE_CYAN));
  if (band < ZONE_MAGENTA) {
    return mix(cyan, magenta, clamp01((band - ZONE_CYAN) / (ZONE_MAGENTA - ZONE_CYAN)));
  }
  return `rgb(${magenta[0]}, ${magenta[1]}, ${magenta[2]})`;
}

/** The accents, read from the same custom properties `DotWave` reads. Null when neither resolves. */
function readAccents(): { magenta: Rgb; cyan: Rgb; dim: Rgb } | null {
  const root = getComputedStyle(document.documentElement);
  const magenta =
    parseColor(root.getPropertyValue("--color-accent")) ?? parseColor(FALLBACK_ACCENT);
  const cyan =
    parseColor(root.getPropertyValue("--color-accent-2")) ?? parseColor(FALLBACK_ACCENT_2);
  if (!magenta || !cyan) return null;
  return { magenta, cyan, dim: [120, 121, 128] };
}

/**
 * Paints the crest band onto a canvas already prepared for the given CSS size.
 *
 * `seconds` drives everything, so two machines at different refresh rates show
 * the same wave. A null `seconds` paints the base state: no breathe, no
 * shimmer, which is what reduced motion renders.
 */
function paintCrest(
  ctx: CanvasRenderingContext2D,
  cssWidth: number,
  cssHeight: number,
  seconds: number | null,
): void {
  const accents = readAccents();
  if (!accents) return;
  const { magenta, cyan, dim } = accents;

  const animated = seconds !== null;
  const breathe = animated ? 0.07 * Math.sin((seconds * Math.PI * 2) / BREATHE_PERIOD_S) : 0;

  const cols = Math.floor(cssWidth / SPACING);
  const rows = Math.floor(cssHeight / SPACING);

  for (let row = 0; row < rows; row += 1) {
    const ny = 1 - row / (rows - 1 || 1);
    const y = cssHeight - row * SPACING;

    for (let col = 0; col < cols; col += 1) {
      const nx = 1 - col / (cols - 1 || 1);
      // `DotWave`'s crest, plus the slow offset that makes it breathe.
      const crest = 0.42 + 0.5 * Math.pow(1 - nx, 2.2) - 0.22 * ny + breathe;
      const band = Math.max(0, 1 - Math.abs(ny - crest) / BAND_HALF_WIDTH);
      if (band <= 0) continue;

      // A brightness wave travelling along the crest, derived from time so it
      // moves at one speed no matter how often we redraw.
      const shimmer = animated
        ? 0.5 + 0.5 * Math.sin((seconds * Math.PI * 2) / SHIMMER_PERIOD_S - nx * 6.5)
        : 1;
      const edge = Math.pow(nx, 0.9);
      const alpha = 0.95 * band * edge * (animated ? 0.35 + 0.65 * shimmer : 0.75);
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
}

/**
 * Matches the backing store to the CSS size and clears the surface. Returns
 * null when the canvas has no usable box or no 2d context.
 *
 * The backing store is only reallocated when the size really changed, because
 * assigning width reallocates and clears it. Doing that every redraw would cost
 * 24 times a second for nothing.
 */
function prepareCanvas(
  canvas: HTMLCanvasElement,
): { w: number; h: number; ctx: CanvasRenderingContext2D } | null {
  const parent = canvas.parentElement;
  if (!parent) return null;

  const cssWidth = parent.clientWidth;
  const cssHeight = parent.clientHeight;
  if (cssWidth <= 0 || cssHeight <= 0) return null;

  const ctx = canvas.getContext("2d");
  if (!ctx) return null;

  const dpr = Math.min(window.devicePixelRatio || 1, MAX_DPR);
  const backingWidth = Math.round(cssWidth * dpr);
  const backingHeight = Math.round(cssHeight * dpr);

  if (canvas.width !== backingWidth || canvas.height !== backingHeight) {
    canvas.width = backingWidth;
    canvas.height = backingHeight;
    canvas.style.width = `${cssWidth}px`;
    canvas.style.height = `${cssHeight}px`;
  }

  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

  return { w: cssWidth, h: cssHeight, ctx };
}

const CANVAS_CLASS = "pointer-events-none absolute inset-0 h-full w-full";

/**
 * The breathing crest. Mounted only when the visitor has not asked for reduced
 * motion, so the animation frame loop is never started at all in that case.
 */
function AnimatedCrest(): React.JSX.Element {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const accumulatorRef = useRef(0);

  useAnimationFrame((time, delta) => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    // Wall-clock throttle on the delta the hook supplies. Counting frames
    // instead would run the wave at double speed on a 120Hz display.
    accumulatorRef.current += delta;
    if (accumulatorRef.current < CREST_FRAME_MS) return;
    accumulatorRef.current = 0;

    const prepared = prepareCanvas(canvas);
    if (!prepared) return;
    paintCrest(prepared.ctx, prepared.w, prepared.h, time / 1000);
  });

  useEffect(() => {
    const parent = canvasRef.current?.parentElement;
    if (!parent) return;

    // A resize is not a frame event. Saturating the accumulator makes the very
    // next frame redraw rather than waiting out the rest of the throttle.
    const observer = new ResizeObserver(() => {
      accumulatorRef.current = CREST_FRAME_MS;
    });
    observer.observe(parent);

    return () => observer.disconnect();
  }, []);

  return <canvas ref={canvasRef} aria-hidden="true" className={CANVAS_CLASS} />;
}

/** The static crest. One deterministic draw at the base crest, plus redraws on resize. */
function StaticCrest(): React.JSX.Element {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    const parent = canvas?.parentElement;
    if (!canvas || !parent) return;

    const draw = (): void => {
      const prepared = prepareCanvas(canvas);
      if (!prepared) return;
      paintCrest(prepared.ctx, prepared.w, prepared.h, null);
    };

    draw();

    // Resizes collapse to one redraw per frame via a dirty flag, so a
    // drag-resize does not queue a draw per observer callback.
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
    observer.observe(parent);

    return () => {
      observer.disconnect();
      if (frame !== null) cancelAnimationFrame(frame);
      frame = null;
      dirty = false;
    };
  }, []);

  return <canvas ref={canvasRef} aria-hidden="true" className={CANVAS_CLASS} />;
}

export function HeroBackdrop(): React.JSX.Element {
  const reduceMotion = useReducedMotion();

  return (
    <div aria-hidden="true" className="pointer-events-none absolute inset-0">
      {/* The 2000 dot field is painted once by `DotWave` and never redrawn. Motion
          only drifts its wrapper, which is a compositor transform. */}
      <motion.div
        className="absolute inset-0 overflow-hidden"
        animate={reduceMotion ? undefined : { x: "-1.6%", y: "-1.2%", scale: 1.04 }}
        transition={
          reduceMotion
            ? undefined
            : { duration: 22, repeat: Infinity, repeatType: "mirror", ease: "easeInOut" }
        }
      >
        <DotWave />
      </motion.div>

      {/* The crest layer. Roughly 140 dots, redrawn at most 24 times a second. */}
      {reduceMotion ? <StaticCrest /> : <AnimatedCrest />}
    </div>
  );
}

export default HeroBackdrop;
