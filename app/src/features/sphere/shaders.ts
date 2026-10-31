/** Bruit de valeur 3D, assez fin pour déformer la surface sans coûter cher au GPU. */
export const NOISE = /* glsl */ `
float hash3(vec3 p) {
  p = fract(p * vec3(0.1031, 0.1030, 0.0973));
  p += dot(p, p.yxz + 33.33);
  return fract((p.x + p.y) * p.z);
}

float valueNoise(vec3 x) {
  vec3 i = floor(x);
  vec3 f = fract(x);
  f = f * f * (3.0 - 2.0 * f);
  float n000 = hash3(i);
  float n100 = hash3(i + vec3(1.0, 0.0, 0.0));
  float n010 = hash3(i + vec3(0.0, 1.0, 0.0));
  float n110 = hash3(i + vec3(1.0, 1.0, 0.0));
  float n001 = hash3(i + vec3(0.0, 0.0, 1.0));
  float n101 = hash3(i + vec3(1.0, 0.0, 1.0));
  float n011 = hash3(i + vec3(0.0, 1.0, 1.0));
  float n111 = hash3(i + vec3(1.0, 1.0, 1.0));
  return mix(
    mix(mix(n000, n100, f.x), mix(n010, n110, f.x), f.y),
    mix(mix(n001, n101, f.x), mix(n011, n111, f.x), f.y),
    f.z
  );
}
`;

/** Nuage de points posé sur la sphère, déformé par le bruit et le niveau audio. */
export const POINTS_VERTEX = /* glsl */ `#version 300 es
precision highp float;
in vec3 a_pos;
in float a_seed;
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
out float v_alpha;
out float v_heat;
${NOISE}
void main() {
  vec3 p = a_pos;
  float n1 = valueNoise(p * u_freq + vec3(0.0, u_flowTime, u_flowTime * 0.5)) * 2.0 - 1.0;
  float n2 = valueNoise(p * u_freq * 2.3 - vec3(u_flowTime * 1.3)) * 2.0 - 1.0;
  float relief = 0.72 * n1 + 0.28 * n2;
  float disp = u_amp * relief + u_audio * (0.55 + 0.45 * n1);
  float shake = (hash3(p * 91.7 + floor(u_time * 24.0)) - 0.5) * u_jitter * 0.08;
  vec3 world = u_rot * (p * (1.0 + disp + shake)) * u_scale;
  gl_Position = u_proj * vec4(world - vec3(0.0, 0.0, u_camera), 1.0);
  float front = smoothstep(-1.0, 1.0, world.z);
  // Quelques points scintillent lentement : la sphère paraît vivante, même au repos.
  float sparkle = step(0.965, a_seed) * (0.5 + 0.5 * sin(u_time * (1.5 + a_seed * 2.0) + a_seed * 40.0));
  v_alpha = mix(0.08, 1.0, front * front) + sparkle * 0.6;
  v_heat = clamp(disp * 5.0 + 0.3 + sparkle * 0.7, 0.0, 1.0);
  gl_PointSize = u_pointSize * mix(0.55, 1.35, front) * (0.7 + a_seed * 0.6) * (1.0 + sparkle * 0.8);
}
`;

export const POINTS_FRAGMENT = /* glsl */ `#version 300 es
precision mediump float;
in float v_alpha;
in float v_heat;
uniform vec3 u_color;
uniform vec3 u_colorHot;
uniform float u_intensity;
out vec4 outColor;
void main() {
  float d = length(gl_PointCoord - 0.5);
  float a = smoothstep(0.5, 0.05, d);
  a *= a * v_alpha * u_intensity;
  vec3 color = mix(u_color, u_colorHot, v_heat);
  outColor = vec4(color * a, a);
}
`;

/** Anneaux en pointillés, inclinés, qui tournent autour de la sphère. */
export const RINGS_VERTEX = /* glsl */ `#version 300 es
precision highp float;
in float a_angle;
in float a_ring;
uniform mat4 u_proj;
uniform mat3 u_rot;
uniform float u_camera;
uniform float u_ringTime;
uniform float u_scale;
uniform float u_pointSize;
out float v_alpha;
void main() {
  float radius = mix(1.32, 1.5, a_ring);
  float direction = a_ring > 0.5 ? -1.0 : 1.0;
  float angle = a_angle + u_ringTime * direction;
  vec3 p = vec3(cos(angle), 0.0, sin(angle)) * radius;
  float tilt = mix(0.42, -0.28, a_ring);
  mat3 tiltX = mat3(1.0, 0.0, 0.0, 0.0, cos(tilt), sin(tilt), 0.0, -sin(tilt), cos(tilt));
  vec3 world = u_rot * (tiltX * p) * u_scale;
  gl_Position = u_proj * vec4(world - vec3(0.0, 0.0, u_camera), 1.0);
  float dash = step(0.35, fract(a_angle * 9.549));
  float front = smoothstep(-1.5, 1.5, world.z);
  v_alpha = dash * mix(0.2, 1.0, front);
  gl_PointSize = u_pointSize * 0.8;
}
`;

export const RINGS_FRAGMENT = /* glsl */ `#version 300 es
precision mediump float;
in float v_alpha;
uniform vec3 u_color;
uniform float u_intensity;
out vec4 outColor;
void main() {
  float d = length(gl_PointCoord - 0.5);
  float a = smoothstep(0.5, 0.1, d) * v_alpha * u_intensity;
  outColor = vec4(u_color * a, a);
}
`;

/** Cœur lumineux, halo et liseré, sur un quadrilatère plein cadre. */
export const GLOW_VERTEX = /* glsl */ `#version 300 es
in vec2 a_corner;
void main() {
  gl_Position = vec4(a_corner, 0.0, 1.0);
}
`;

export const GLOW_FRAGMENT = /* glsl */ `#version 300 es
precision mediump float;
uniform vec2 u_resolution;
uniform vec3 u_color;
uniform float u_core;
uniform float u_halo;
uniform float u_radius;
out vec4 outColor;
void main() {
  vec2 uv = (gl_FragCoord.xy / u_resolution) * 2.0 - 1.0;
  float r = length(uv) / u_radius;
  float core = exp(-r * r * 3.4) * u_core;
  float hot = exp(-r * r * 14.0) * u_core * 0.55;
  float halo = exp(-max(r - 0.9, 0.0) * 3.0) * smoothstep(2.0, 0.85, r) * u_halo * 0.6;
  float rim = smoothstep(0.05, 0.0, abs(r - 1.0)) * u_halo * 0.45;
  float energy = core + halo + rim;
  vec3 color = u_color * energy + vec3(1.0) * hot * 0.35;
  outColor = vec4(color, clamp(energy + hot * 0.35, 0.0, 1.0));
}
`;
