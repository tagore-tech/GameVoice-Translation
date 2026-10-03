/**
 * Push-to-talk configuration.
 *
 * All values here are defaults that can be overridden later through
 * a user-settings panel (M10).  No PTT logic should hard-code these values —
 * always read them from PTT_CONFIG or accept them as props/parameters.
 *
 * Key format: KeyboardEvent.code strings (layout-independent).
 * Reference: https://developer.mozilla.org/en-US/docs/Web/API/KeyboardEvent/code/code_values
 */
export const PTT_CONFIG = {
  /** Default keyboard key for push-to-talk. */
  defaultKey: 'Space',

  /**
   * Minimum recording duration to consider a chunk valid.
   * Chunks shorter than this are discarded (e.g. accidental key tap).
   */
  minDurationMs: 300,

  /**
   * Maximum recording duration before auto-stop.
   * Prevents the mic being held open indefinitely if the user forgets to release.
   */
  maxDurationMs: 30_000,
} as const

export type PTTConfig = typeof PTT_CONFIG
