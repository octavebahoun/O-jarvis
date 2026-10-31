/**
 * Nuage de points "Cyber Mask" : masque en colonnes de code, capuche à liseré lumineux,
 * pluie matricielle. La géométrie est décrite en coordonnées "image" n ∈ [-1, 1]
 * (x vers la droite, y vers le haut), puis convertie en unités monde via WORLD.
 */

export interface CyberPointCloud {
  positions: Float32Array;
  seeds: Float32Array;
  /** 0 = générique (sphère), 1 = visage, 2 = liseré de capuche, 3 = pluie */
  kinds: Float32Array;
}

export const KIND = { generic: 0, face: 1, rim: 2, rain: 3 } as const;

/** Doit rester cohérent avec SPHERE_RADIUS_NDC (0.56) de renderer.ts. */
export const WORLD = 1 / 0.56;
/** Écart entre colonnes de code, en coordonnées image. */
export const COLUMN_W = 0.011;

/** Silhouette de la capuche (partagée avec le shader pour masquer la pluie). */
export const HOOD = {
  halfW: 0.69, // demi-largeur maximale
  widestY: 0.1, // hauteur où la capuche est la plus large
  topY: 1.15, // sommet théorique du dôme (hors cadre)
  domeExp: 0.8, // < 1 = dôme plus arrondi, > 1 = plus pointu
  neckY: -0.25, // hauteur du coin capuche/épaule
  neckHalfW: 0.52,
  shoulderSlope: 1.85, // pente des épaules
} as const;

export function hoodHalfWidth(y: number): number {
  if (y >= HOOD.widestY) {
    const k = (y - HOOD.widestY) / (HOOD.topY - HOOD.widestY);
    return k >= 1 ? -1 : HOOD.halfW * Math.pow(1 - k * k, HOOD.domeExp);
  }
  if (y >= HOOD.neckY) {
    const t = (HOOD.widestY - y) / (HOOD.widestY - HOOD.neckY);
    return HOOD.halfW - (HOOD.halfW - HOOD.neckHalfW) * Math.pow(t, 1.5);
  }
  return HOOD.neckHalfW + HOOD.shoulderSlope * (HOOD.neckY - y);
}

const FACE = { halfW: 0.395, topY: 0.61, chinY: -0.36 } as const;

function faceHalfWidth(y: number): number {
  const t = (y - FACE.chinY) / (FACE.topY - FACE.chinY);
  if (t <= 0 || t >= 1) return 0;
  if (t > 0.62) {
    const k = (t - 0.62) / 0.38;
    return FACE.halfW * Math.sqrt(1 - 0.85 * k * k); // front arrondi
  }
  return FACE.halfW * (0.36 + 0.64 * Math.pow(t / 0.62, 0.6)); // joues -> menton
}

/**
 * Traits sombres du masque (demi-visage droit, x >= 0 ; le gauche est symétrique).
 * [ax, ay, bx, by, rayon au départ, rayon à l'arrivée]
 */
const INK: readonly (readonly [number, number, number, number, number, number])[] = [
  // sourcil épais, oblique, plus bas vers le nez
  [0.34, 0.46, 0.07, 0.36, 0.03, 0.046],
  // moustache relevée
  [0.0, -0.1, 0.12, -0.115, 0.024, 0.022],
  [0.12, -0.115, 0.22, -0.07, 0.022, 0.016],
  [0.22, -0.07, 0.29, 0.05, 0.016, 0.008],
  // sourire
  [0.0, -0.17, 0.1, -0.159, 0.012, 0.012],
  [0.1, -0.159, 0.2, -0.126, 0.012, 0.012],
  [0.2, -0.126, 0.27, -0.09, 0.012, 0.012],
];

let segT = 0; // paramètre le long du segment, rempli par distSegment

function distSegment(
  px: number,
  py: number,
  ax: number,
  ay: number,
  bx: number,
  by: number,
): number {
  const dx = bx - ax;
  const dy = by - ay;
  const len2 = dx * dx + dy * dy;
  segT = len2 > 0 ? Math.min(1, Math.max(0, ((px - ax) * dx + (py - ay) * dy) / len2)) : 0;
  return Math.hypot(px - (ax + dx * segT), py - (ay + dy * segT));
}

function isInk(x: number, y: number): boolean {
  const sx = Math.abs(x);
  for (const [ax, ay, bx, by, r0, r1] of INK) {
    const d = distSegment(sx, y, ax, ay, bx, by);
    if (d < r0 + (r1 - r0) * segT) return true;
  }
  // yeux : fentes en amande, bout intérieur plus bas
  {
    const dx = sx - 0.2;
    const dy = y - 0.295;
    const c = Math.cos(0.22);
    const s = Math.sin(0.22);
    const u = dx * c + dy * s;
    const v = -dx * s + dy * c;
    if ((u / 0.105) ** 2 + (v / 0.03) ** 2 < 1) return true;
  }
  // narines
  if ((sx / 0.055) ** 2 + ((y + 0.045) / 0.016) ** 2 < 1) return true;
  // bouc : bande verticale sous la bouche
  if (sx < 0.03 && y < -0.2) return true;
  return false;
}

function hash1(n: number): number {
  const s = Math.sin(n * 91.3458) * 47453.5453;
  return s - Math.floor(s);
}

/** Pseudo-gaussienne centrée, écart-type ~0.5. */
function gauss(): number {
  return Math.random() + Math.random() + Math.random() - 1.5;
}

/** Contour droit de la capuche + épaules, du haut du cadre vers le bas. */
function buildHoodOutline() {
  const xs: number[] = [];
  const ys: number[] = [];
  const cum: number[] = [0];
  const steps = 240;
  const yTop = 1.04;
  const yBottom = -1.0; // les épaules sortent du cadre, même en 16:9
  for (let i = 0; i <= steps; i += 1) {
    const y = yTop + (yBottom - yTop) * (i / steps);
    xs.push(hoodHalfWidth(y));
    ys.push(y);
    if (i > 0) {
      const prevCum = cum[i - 1] ?? 0;
      const x0 = xs[i - 1] ?? 0;
      const x1 = xs[i] ?? 0;
      const y0 = ys[i - 1] ?? 0;
      const y1 = ys[i] ?? 0;
      cum.push(prevCum + Math.hypot(x1 - x0, y1 - y0));
    }
  }
  return { xs, ys, cum };
}

export function generateCyberMaskPoints(count: number): CyberPointCloud {
  const positions = new Float32Array(count * 3);
  const seeds = new Float32Array(count);
  const kinds = new Float32Array(count);

  const faceCount = Math.floor(count * 0.6);
  const rimCount = Math.floor(count * 0.24);
  const rainCount = count - faceCount - rimCount;

  let idx = 0;
  const put = (x: number, y: number, z: number, kind: number): void => {
    positions[idx * 3] = x * WORLD;
    positions[idx * 3 + 1] = y * WORLD;
    positions[idx * 3 + 2] = z * WORLD;
    const s = Math.sin(idx * 12.9898 + kind * 78.233) * 43758.5453;
    seeds[idx] = s - Math.floor(s);
    kinds[idx] = kind;
    idx += 1;
  };

  // 1. VISAGE : colonnes verticales de "code", traits sombres creusés par omission
  for (let i = 0; i < faceCount; i += 1) {
    let placed = false;
    while (!placed) {
      const y = FACE.chinY + Math.random() * (FACE.topY - FACE.chinY);
      const hw = faceHalfWidth(y);
      if (hw <= 0) continue;
      const col = Math.round(((Math.random() * 2 - 1) * hw) / COLUMN_W);
      const x = col * COLUMN_W;
      if (Math.abs(x) > hw || isInk(x, y)) continue;

      const nose = Math.abs(x) < 0.045 && y > -0.03 && y < 0.4;
      // certaines colonnes sont plus denses que d'autres ; l'arête du nez est pleine
      const keep = nose ? 1 : (0.4 + 0.6 * hash1(col)) * 0.85;
      if (Math.random() > keep) continue;

      const bulge = Math.max(0, 1 - (x * x) / (FACE.halfW * FACE.halfW));
      put(x, y, 0.1 + 0.18 * bulge + (nose ? 0.07 : 0), KIND.face);
      placed = true;
    }
  }

  // 2. LISERÉ DE LA CAPUCHE : contour fibreux lumineux (dôme + épaules)
  const outline = buildHoodOutline();
  const total = outline.cum[outline.cum.length - 1] ?? 1;
  for (let i = 0; i < rimCount; i += 1) {
    const s = Math.random() * total;
    let lo = 0;
    let hi = outline.cum.length - 1;
    while (hi - lo > 1) {
      const mid = (lo + hi) >> 1;
      if ((outline.cum[mid] ?? 0) < s) lo = mid;
      else hi = mid;
    }
    const cumLo = outline.cum[lo] ?? 0;
    const cumHi = outline.cum[hi] ?? cumLo;
    const seg = cumHi - cumLo;
    const f = seg > 0 ? (s - cumLo) / seg : 0;
    const xLo = outline.xs[lo] ?? 0;
    const xHi = outline.xs[hi] ?? xLo;
    const yLo = outline.ys[lo] ?? 0;
    const yHi = outline.ys[hi] ?? yLo;
    const ox = xLo + (xHi - xLo) * f;
    const oy = yLo + (yHi - yLo) * f;
    const tx = xHi - xLo;
    const ty = yHi - yLo;
    const len = Math.hypot(tx, ty) || 1;
    const nx = -ty / len; // normale sortante (côté droit)
    const ny = tx / len;

    // flou gaussien + quelques "poils" qui dépassent + lueur intérieure
    let off = gauss() * 0.014;
    if (Math.random() < 0.14) off += Math.random() * 0.05;
    else if (Math.random() < 0.25) off -= Math.random() * 0.08;

    const side = Math.random() < 0.5 ? -1 : 1;
    put((ox + nx * off) * side, oy + ny * off, (Math.random() - 0.5) * 0.12, KIND.rim);
  }

  // 3. PLUIE : traînées verticales sur tout le cadre, à profondeurs variées
  for (let i = 0; i < rainCount; i += 1) {
    put(Math.random() * 2 - 1, Math.random() * 2 - 1, (Math.random() - 0.5) * 0.5, KIND.rain);
  }

  return { positions, seeds, kinds };
}
