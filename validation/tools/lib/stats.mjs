// Analytical confidence intervals. No resampling.

export function wilsonCI(successes, total) {
  if (total === 0) return { n: 0, p: NaN, lo: NaN, hi: NaN };
  const z = 1.96;
  const p = successes / total;
  const denom = 1 + (z * z) / total;
  const center = p + (z * z) / (2 * total);
  const margin = z * Math.sqrt((p * (1 - p) + (z * z) / (4 * total)) / total);
  // Clamped: at k = 0 or k = n rounding can leave a bound a hair outside [0, 1].
  const clamp = (x) => Math.min(1, Math.max(0, x));
  return { n: total, p, lo: clamp((center - margin) / denom), hi: clamp((center + margin) / denom) };
}

// Krippendorff's alpha for units rated by a varying number of raters. Chance
// corrected, so it is comparable across items with different numbers of
// categories and different marginal skew — raw percent agreement is not (an item
// where 84% of answers are "yes" scores high by luck alone).
//
// units: array of arrays, one inner array per unit, holding that unit's ratings
// (units with fewer than 2 ratings are dropped — they are not pairable).
// metric: 'nominal' (default) or 'ordinal'.
export function krippendorffAlpha(units, metric = 'nominal') {
  const pairable = units.filter((u) => u.filter((v) => v != null).length >= 2)
    .map((u) => u.filter((v) => v != null));
  if (!pairable.length) return NaN;

  const values = [...new Set(pairable.flat())];
  // Ordinal deltas need the categories ordered; nominal does not care.
  const cats = metric === 'ordinal'
    ? values.slice().sort((a, b) => a - b)
    : values;
  const idx = new Map(cats.map((c, i) => [c, i]));
  const K = cats.length;

  // Coincidence matrix: each unit contributes its pairs, weighted 1/(m-1).
  const o = Array.from({ length: K }, () => new Array(K).fill(0));
  for (const u of pairable) {
    const m = u.length;
    for (let i = 0; i < m; i++) {
      for (let j = 0; j < m; j++) {
        if (i === j) continue;
        o[idx.get(u[i])][idx.get(u[j])] += 1 / (m - 1);
      }
    }
  }
  const marg = o.map((row) => row.reduce((a, b) => a + b, 0));
  const n = marg.reduce((a, b) => a + b, 0);
  if (n < 2) return NaN;

  // Ordinal difference uses the marginals between the two categories.
  const delta2 = (a, b) => {
    if (a === b) return 0;
    if (metric !== 'ordinal') return 1;
    const [lo, hi] = a < b ? [a, b] : [b, a];
    let sum = 0;
    for (let g = lo; g <= hi; g++) sum += marg[g];
    return (sum - (marg[a] + marg[b]) / 2) ** 2;
  };

  let Do = 0, De = 0;
  for (let c = 0; c < K; c++) {
    for (let k = 0; k < K; k++) {
      const d = delta2(c, k);
      Do += o[c][k] * d;
      De += marg[c] * marg[k] * d;
    }
  }
  Do /= n;
  De /= n * (n - 1);
  return De === 0 ? NaN : 1 - Do / De;
}

// Percentile bootstrap CI for a mean. Preferred over the t-interval here because
// participant counts are small and the quantity (a share) is bounded.
export function bootstrapMeanCI(values, iters = 10000, seed = 12345) {
  const xs = values.filter(Number.isFinite);
  if (xs.length < 2) return { mean: xs[0] ?? NaN, lo: NaN, hi: NaN, n: xs.length };
  let s = seed;
  const rnd = () => ((s = (s * 1103515245 + 12345) >>> 0) / 4294967296);
  const means = [];
  for (let b = 0; b < iters; b++) {
    let sum = 0;
    for (let i = 0; i < xs.length; i++) sum += xs[Math.floor(rnd() * xs.length)];
    means.push(sum / xs.length);
  }
  means.sort((a, b) => a - b);
  return {
    mean: xs.reduce((a, b) => a + b, 0) / xs.length,
    lo: means[Math.floor(0.025 * iters)],
    hi: means[Math.floor(0.975 * iters)],
    n: xs.length,
  };
}

// Spearman rank correlation, average ranks for ties.
export function spearman(a, b) {
  const rank = (arr) => {
    const order = arr.map((v, i) => [v, i]).sort((x, y) => x[0] - y[0]);
    const out = new Array(arr.length);
    let i = 0;
    while (i < order.length) {
      let j = i;
      while (j + 1 < order.length && order[j + 1][0] === order[i][0]) j++;
      const r = (i + j) / 2 + 1;
      for (let k = i; k <= j; k++) out[order[k][1]] = r;
      i = j + 1;
    }
    return out;
  };
  const ra = rank(a), rb = rank(b), n = a.length;
  const ma = ra.reduce((x, y) => x + y, 0) / n;
  const mb = rb.reduce((x, y) => x + y, 0) / n;
  let num = 0, da = 0, db = 0;
  for (let i = 0; i < n; i++) {
    num += (ra[i] - ma) * (rb[i] - mb);
    da += (ra[i] - ma) ** 2;
    db += (rb[i] - mb) ** 2;
  }
  return da && db ? num / Math.sqrt(da * db) : NaN;
}

