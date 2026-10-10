// How far along a move a drive is at a given moment: speed up, cruise, slow down.
//
// Without a jerk limit, the symmetric trapezoid the STAR simulator times moves by
// (`_get_travel_time`): a move too short to reach cruise speed speeds up for half its length and
// slows down for the other half. With one, an S-curve: acceleration itself ramps at the jerk, which
// is how the X-arm moves on a STAR (fitted from HxUsbComm traces; see MOTION_PROFILES.md).
// Pure: no three, no page, so it can be checked on its own.

/**
 * @typedef {object} Profile
 * @property {number} duration        seconds the move takes
 * @property {(t: number) => number} progress  share of the distance covered at `t` seconds, 0..1
 */

/**
 * @param {number} distance      mm, of either sign; only its size counts
 * @param {number} speed         mm/s the drive cruises at
 * @param {number} acceleration  mm/s^2 it speeds up and slows down at
 * @param {number} [jerk]        mm/s^3 its acceleration changes at; none for a trapezoid
 * @returns {Profile}
 */
export function motionProfile(distance, speed, acceleration, jerk) {
  const d = Math.abs(distance);
  if (!(d > 0) || !(speed > 0)) return { duration: 0, progress: () => 1 };
  if (!(acceleration > 0)) {
    const duration = d / speed;
    return { duration, progress: (t) => clamp(t / duration) };
  }
  if (jerk > 0 && Number.isFinite(jerk)) return sCurve(d, speed, acceleration, jerk);

  const tAccel = speed / acceleration;
  const dAccel = 0.5 * acceleration * tAccel * tAccel;

  if (d >= 2 * dAccel) {
    // Trapezoid: up to speed, cruise, and down again.
    const dCruise = d - 2 * dAccel;
    const tCruise = dCruise / speed;
    const duration = 2 * tAccel + tCruise;
    return {
      duration,
      progress: (t) => {
        if (t >= duration) return 1;
        if (t <= tAccel) return (0.5 * acceleration * t * t) / d;
        if (t <= tAccel + tCruise) return (dAccel + speed * (t - tAccel)) / d;
        const s = t - tAccel - tCruise;
        return clamp((dAccel + dCruise + speed * s - 0.5 * acceleration * s * s) / d);
      },
    };
  }

  // Triangle: never reaches cruise speed.
  const vPeak = Math.sqrt(acceleration * d);
  const tPeak = vPeak / acceleration;
  const duration = 2 * tPeak;
  return {
    duration,
    progress: (t) => {
      if (t >= duration) return 1;
      if (t <= tPeak) return (0.5 * acceleration * t * t) / d;
      const s = t - tPeak;
      return clamp((0.5 * d + vPeak * s - 0.5 * acceleration * s * s) / d);
    },
  };
}

// The phases of one ramp from rest to `v`: jerk up, hold the acceleration, jerk down - the middle
// phase only when `v` is high enough for the acceleration to reach its limit.
function ramp(v, a, j) {
  if (v >= (a * a) / j) return [a / j, v / a - a / j, a / j];
  const t1 = Math.sqrt(v / j);
  return [t1, 0, t1];
}

const rampTime = (v, a, j) => ramp(v, a, j).reduce((s, t) => s + t, 0);

/** Rest to rest over `d`, the speed, acceleration and jerk all limited: seven phases at most. */
function sCurve(d, vmax, a, j) {
  // The peak speed: the cruise speed if the move is long enough to reach it, else the speed at
  // which a ramp up and a ramp down cover the distance between them (found by halving).
  let v = vmax;
  if (d < vmax * rampTime(vmax, a, j)) {
    let lo = 0;
    let hi = vmax;
    for (let i = 0; i < 60; i++) {
      const mid = (lo + hi) / 2;
      if (mid * rampTime(mid, a, j) < d) lo = mid;
      else hi = mid;
    }
    v = hi;
  }
  const [t1, t2, t3] = ramp(v, a, j);
  const cruise = Math.max(0, (d - v * (t1 + t2 + t3)) / v);
  // Each phase as [duration, jerk]; the accelerations and speeds follow by integration.
  const phases = [
    [t1, j],
    [t2, 0],
    [t3, -j],
    [cruise, 0],
    [t3, -j],
    [t2, 0],
    [t1, j],
  ];
  const duration = phases.reduce((s, [t]) => s + t, 0);
  return {
    duration,
    progress: (t) => {
      if (t >= duration) return 1;
      let p = 0;
      let vel = 0;
      let acc = 0;
      let left = Math.max(0, t);
      for (const [dt, jk] of phases) {
        const s = Math.min(dt, left);
        p += vel * s + (acc * s * s) / 2 + (jk * s * s * s) / 6;
        vel += acc * s + (jk * s * s) / 2;
        acc += jk * s;
        left -= s;
        if (left <= 0) break;
      }
      return clamp(p / d);
    },
  };
}

function clamp(p) {
  return Math.max(0, Math.min(1, p));
}
