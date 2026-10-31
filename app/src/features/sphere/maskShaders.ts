import { COLUMN_W, HOOD, WORLD } from "./cyberMask";
import { NOISE } from "./shaders";

const f = (n: number): string => n.toFixed(4);

/** Silhouette de la capuche : même formule que hoodHalfWidth() dans cyberMask.ts. */
const HOOD_GLSL = /* glsl */ `
float hoodHalfWidth(float y) {
  if (y >= ${f(HOOD.widestY)}) {
    float k = (y - ${f(HOOD.widestY)}) / ${f(HOOD.topY - HOOD.widestY)};
    if (k >= 1.0) return -1.0;
    return ${f(HOOD.halfW)} * pow(1.0 - k * k, ${f(HOOD.domeExp)});
  }
  if (y >= ${f(HOOD.neckY)}) {
    float t = (${f(HOOD.widestY)} - y) / ${f(HOOD.widestY - HOOD.neckY)};
    return ${f(HOOD.halfW)} - ${f(HOOD.halfW - HOOD.neckHalfW)} * pow(t, 1.5);
  }
  return ${f(HOOD.neckHalfW)} + ${f(HOOD.shoulderSlope)} * (${f(HOOD.neckY)} - y);
}

// 1 à l'intérieur de la capuche, 0 dehors (bord adouci)
float hoodInside(vec2 w) {
  vec2 n = w * ${f(1 / WORLD)};
  float hw = hoodHalfWidth(n.y);
  return 1.0 - smoothstep(hw - 0.02, hw + 0.02, abs(n.x));
}
`;

/** Masque cybernétique : visage, liseré de la capuche et pluie Matrix, selon `a_kind`. */
export const MASK_POINTS_VERTEX = /* glsl */ `#version 300 es
precision highp float;
in vec3 a_pos;
in float a_seed;
in float a_kind; // 0 générique, 1 visage, 2 liseré, 3 pluie
uniform mat4 u_proj;
uniform mat3 u_rot;
uniform float u_camera;
uniform float u_flowTime;
uniform float u_time;
uniform float u_amp;
uniform float u_freq;
uniform float u_audio;
uniform float u_scale;
uniform float u_jitter;
uniform float u_pointSize;
uniform vec2 u_extent;
out float v_alpha;
out float v_heat;
out float v_kind;
out float v_size;
${NOISE}
${HOOD_GLSL}
void main() {
  bool isFace = a_kind > 0.5 && a_kind < 1.5;
  bool isRim = a_kind > 1.5 && a_kind < 2.5;
  bool isRain = a_kind > 2.5;

  vec3 p = a_pos;
  float ptSize = u_pointSize;
  float gain = 1.0;

  if (isRain) {
    float w = ${f(WORLD)};
    // Dans thinking, la pluie accélère (flux massif de données)
    float rainSpeedMult = 1.0 + clamp((u_flowTime - u_time * 0.12) * 1.5, 0.0, 2.5);
    float speed = (0.45 + a_seed * 1.1) * rainSpeedMult;
    float span = 2.0 * u_extent.y * w + 0.8;
    float y0 = (a_pos.y / w * 0.5 + 0.5) * span;
    p.x = a_pos.x * u_extent.x;
    p.y = mod(y0 - u_time * speed, span) - span * 0.5;
    ptSize = min(u_pointSize * (14.0 + a_seed * 30.0), 120.0);
    // Dans listening/speaking, la pluie réagit légèrement aux fréquences audio
    gain = mix(1.0, 0.14, hoodInside(p.xy)) * (1.0 + u_audio * 0.35);
  } else if (isFace) {
    // flux de code : chaque colonne a sa vitesse et sa phase
    float col = floor(p.x / ${f(COLUMN_W * WORLD)} + 0.5);
    float h = hash3(vec3(col, 1.7, 4.2));
    // La vague descend en rythme avec flowTime (accélération spectaculaire en réflexion)
    float flowSpeed = 2.0 + h * 2.5 + (u_flowTime - u_time * 0.12) * 4.0;
    float wave = 0.5 + 0.5 * sin(u_time * flowSpeed + p.y * 11.0 + h * 6.2831);
    
    // Balayage lumineux de scan (dans awaiting : faisceau de sécurité lent de haut en bas)
    float scanBeam = exp(-pow((p.y - sin(u_time * 1.5) * 0.8) * 4.0, 2.0)) * 0.7;
    
    gain = 0.35 + 0.65 * wave + scanBeam;
    ptSize *= 0.85 + 0.3 * u_audio;
    // Vibration 3D des lèvres et traits au son de la voix (audio)
    p.z += u_audio * 0.14 * sin(p.y * 16.0 + u_time * 8.0);
  } else if (isRim) {
    gain = 0.75 + 0.25 * sin(u_time * 3.0 + a_seed * 50.0) + u_audio * 0.4;
    ptSize *= 0.7 + 0.2 * u_audio;
  } else {
    float pulse = sin(u_time * 1.8 + p.y * 3.0) * 0.015;
    p.z += pulse + u_audio * 0.08 * sin(p.y * 12.0);
  }

  // Effet de glitch saccadé pour l'état error (u_jitter > 0.05)
  float glitchLine = step(0.92, hash3(vec3(floor(p.y * 18.0), floor(u_time * 14.0), 0.0)));
  float glitchOffset = (hash3(vec3(floor(u_time * 24.0), p.y, 0.0)) - 0.5) * u_jitter * glitchLine * 0.35;
  float shake = (hash3(p * 91.7 + floor(u_time * 24.0)) - 0.5) * u_jitter * 0.08;

  vec3 world = u_rot * (p + vec3(shake + glitchOffset, 0.0, 0.0)) * u_scale;
  gl_Position = u_proj * vec4(world - vec3(0.0, 0.0, u_camera), 1.0);

  float front = smoothstep(-0.8, 0.8, world.z);
  if (isRim) front = 1.0;
  float sparkle = step(0.92, a_seed) * (0.6 + 0.4 * sin(u_time * 5.0 + a_seed * 50.0));

  v_alpha = (mix(0.15, 1.0, front) + sparkle * 0.8) * gain;
  v_heat = clamp(u_audio * 2.2 + sparkle * 0.6 + (glitchLine * u_jitter), 0.0, 1.0);
  v_kind = a_kind;
  v_size = ptSize;
  gl_PointSize = isRain
    ? ptSize
    : ptSize * mix(0.7, 1.4, front) * (0.8 + a_seed * 0.5) * (1.0 + sparkle * 0.5);
}
`;

export const MASK_POINTS_FRAGMENT = /* glsl */ `#version 300 es
precision mediump float;
in float v_alpha;
in float v_heat;
in float v_kind;
in float v_size;
uniform vec3 u_color;
uniform vec3 u_colorHot;
uniform float u_intensity;
out vec4 outColor;
void main() {
  vec2 c = gl_PointCoord - 0.5;
  vec3 color = mix(u_color, u_colorHot, v_heat);
  float a;

  if (v_kind > 2.5) {
    // traînée de pluie : fin trait vertical, queue vers le haut, tête brillante au centre
    float px = 1.0 / v_size;
    float line = 1.0 - smoothstep(0.0, 1.2 * px, abs(c.x));
    float t = clamp(-c.y * 2.0, 0.0, 1.0);
    float tail = (1.0 - t) * (1.0 - t) * step(c.y, 0.0);
    float head = 1.0 - smoothstep(0.0, 2.6, length(c * v_size));
    a = clamp(line * tail * 0.6 + head, 0.0, 1.0);
    // La pluie prend la couleur d'état (u_color) avec tête lumineuse (u_colorHot)
    color = mix(u_color * 1.1, u_colorHot, head * 0.85);
  } else {
    float d = length(c);
    a = 1.0 - smoothstep(0.05, 0.5, d);
    a *= a;
    // liseré de capuche : surbrillance de contour basée sur u_colorHot
    if (v_kind > 1.5) color = mix(color, u_colorHot, 0.4);
  }

  a *= v_alpha * u_intensity;
  outColor = vec4(color * a, a);
}
`;
