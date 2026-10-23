import { generateCyberMaskPoints } from "./cyberMask";
import { uiLog } from "../../lib/log";
import type { FrameParams } from "./animator";
import {
  GLOW_FRAGMENT,
  GLOW_VERTEX,
  POINTS_FRAGMENT,
  POINTS_VERTEX,
  RINGS_FRAGMENT,
  RINGS_VERTEX,
} from "./shaders";

const FOV_RAD = (40 * Math.PI) / 180;
const FOCAL = 1 / Math.tan(FOV_RAD / 2);
/** Rayon apparent de la sphère, en fraction de la demi-largeur du canevas. */
const SPHERE_RADIUS_NDC = 0.56;
const CAMERA_DISTANCE = FOCAL / SPHERE_RADIUS_NDC;
const TILT_RAD = 0.38;
const RING_POINTS = 260;

interface Program {
  program: WebGLProgram;
  uniforms: Record<string, WebGLUniformLocation | null>;
}

function compile(gl: WebGL2RenderingContext, type: number, source: string): WebGLShader {
  const shader = gl.createShader(type);
  if (!shader) throw new Error("création du shader impossible");
  gl.shaderSource(shader, source);
  gl.compileShader(shader);
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
    const log = gl.getShaderInfoLog(shader) ?? "";
    gl.deleteShader(shader);
    throw new Error(`shader : ${log}`);
  }
  return shader;
}

function link(
  gl: WebGL2RenderingContext,
  vertex: string,
  fragment: string,
  uniformNames: string[],
): Program {
  const program = gl.createProgram();
  const vs = compile(gl, gl.VERTEX_SHADER, vertex);
  const fs = compile(gl, gl.FRAGMENT_SHADER, fragment);
  gl.attachShader(program, vs);
  gl.attachShader(program, fs);
  gl.linkProgram(program);
  gl.deleteShader(vs);
  gl.deleteShader(fs);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
    throw new Error(`programme : ${gl.getProgramInfoLog(program) ?? ""}`);
  }
  const uniforms: Program["uniforms"] = {};
  for (const name of uniformNames) uniforms[name] = gl.getUniformLocation(program, name);
  return { program, uniforms };
}

/** Points répartis régulièrement sur la sphère (spirale de Fibonacci). */
export function fibonacciSphere(count: number): {
  positions: Float32Array;
  seeds: Float32Array;
  kinds: Float32Array;
} {
  const positions = new Float32Array(count * 3);
  const seeds = new Float32Array(count);
  const kinds = new Float32Array(count); // 0 = générique
  const golden = Math.PI * (3 - Math.sqrt(5));
  for (let i = 0; i < count; i += 1) {
    const y = 1 - ((i + 0.5) * 2) / count;
    const radius = Math.sqrt(1 - y * y);
    const theta = golden * i;
    positions[i * 3] = Math.cos(theta) * radius;
    positions[i * 3 + 1] = y;
    positions[i * 3 + 2] = Math.sin(theta) * radius;
    const s = Math.sin(i * 12.9898) * 43758.5453;
    seeds[i] = s - Math.floor(s);
    kinds[i] = 0;
  }
  return { positions, seeds, kinds };
}

/** Vert "code" de l'image, mélangé à la couleur d'état pour garder un retour visuel. */
const MASK_GREEN = [0.3, 1.0, 0.62] as const;
const MASK_GREEN_HOT = [0.85, 1.0, 0.92] as const;

function tintInto(
  out: Float32Array,
  base: ArrayLike<number>,
  target: readonly number[],
  k: number,
): void {
  for (let i = 0; i < 3; i += 1) {
    const b = base[i] ?? 0;
    const t = target[i] ?? 0;
    out[i] = b * (1 - k) + t * k;
  }
}

function perspective(aspect = 1): Float32Array {
  const near = 0.1;
  const far = 50;
  const m = new Float32Array(16);
  // Pour le masque en plein écran, on conserve la hauteur du masque bien présente
  // et on ouvre le champ horizontal sans déformer ni éloigner le masque.
  m[0] = FOCAL / aspect;
  m[5] = FOCAL;
  m[10] = (far + near) / (near - far);
  m[11] = -1;
  m[14] = (2 * far * near) / (near - far);
  return m;
}

/** Rotation autour de Y (lacet), après une légère inclinaison autour de X. Colonne d'abord. */
export function rotation(yaw: number, tilt = TILT_RAD): Float32Array {
  const cy = Math.cos(yaw);
  const sy = Math.sin(yaw);
  const cx = Math.cos(tilt);
  const sx = Math.sin(tilt);
  // R = Rx(tilt) * Ry(yaw)
  return new Float32Array([cy, sx * sy, -cx * sy, 0, cx, sx, sy, -sx * cy, cx * cy]);
}

export type AvatarModel = "sphere" | "cyberMask";

export interface RendererOptions {
  pointCount: number;
  avatarModel?: AvatarModel;
}

export class SphereRenderer {
  private readonly gl: WebGL2RenderingContext;
  private readonly points: Program;
  private readonly rings: Program;
  private readonly glow: Program;
  private readonly pointsVao: WebGLVertexArrayObject;
  private readonly ringsVao: WebGLVertexArrayObject;
  private readonly glowVao: WebGLVertexArrayObject;
  private readonly buffers: WebGLBuffer[] = [];
  private readonly projection = perspective();
  private readonly pointCount: number;
  private readonly avatarModel: AvatarModel;
  private readonly tint = new Float32Array(3);
  private readonly tintHot = new Float32Array(3);
  private flowTime = 0;
  private ringTime = 0;
  private yaw = 0;
  private time = 0;
  private dpr = 1;
  private extentX = 1;
  private extentY = 1;

  /** Garde le carré [-1,1]² de l'image entièrement visible, quel que soit le ratio. */
  private setAspect(aspect: number): void {
    const m = this.projection;
    m[0] = aspect >= 1 ? FOCAL / aspect : FOCAL;
    m[5] = aspect >= 1 ? FOCAL : FOCAL * aspect;
    this.extentX = Math.max(aspect, 1);
    this.extentY = Math.max(1 / aspect, 1);
  }

  private constructor(gl: WebGL2RenderingContext, options: RendererOptions) {
    this.gl = gl;
    this.pointCount = options.pointCount;
    this.avatarModel = options.avatarModel ?? "cyberMask";
    this.points = link(gl, POINTS_VERTEX, POINTS_FRAGMENT, [
      "u_proj",
      "u_rot",
      "u_camera",
      "u_flowTime",
      "u_time",
      "u_amp",
      "u_freq",
      "u_audio",
      "u_scale",
      "u_jitter",
      "u_pointSize",
      "u_extent",
      "u_color",
      "u_colorHot",
      "u_intensity",
    ]);
    this.rings = link(gl, RINGS_VERTEX, RINGS_FRAGMENT, [
      "u_proj",
      "u_rot",
      "u_camera",
      "u_ringTime",
      "u_scale",
      "u_pointSize",
      "u_color",
      "u_intensity",
    ]);
    this.glow = link(gl, GLOW_VERTEX, GLOW_FRAGMENT, [
      "u_resolution",
      "u_color",
      "u_core",
      "u_halo",
      "u_radius",
    ]);

    const { positions, seeds, kinds } =
      this.avatarModel === "cyberMask"
        ? generateCyberMaskPoints(this.pointCount)
        : fibonacciSphere(this.pointCount);
    this.pointsVao = this.vao(this.points.program, [
      { name: "a_pos", data: positions, size: 3 },
      { name: "a_seed", data: seeds, size: 1 },
      { name: "a_kind", data: kinds, size: 1 },
    ]);

    const angles = new Float32Array(RING_POINTS * 2);
    const ringIds = new Float32Array(RING_POINTS * 2);
    for (let i = 0; i < RING_POINTS * 2; i += 1) {
      angles[i] = ((i % RING_POINTS) / RING_POINTS) * Math.PI * 2;
      ringIds[i] = i < RING_POINTS ? 0 : 1;
    }
    this.ringsVao = this.vao(this.rings.program, [
      { name: "a_angle", data: angles, size: 1 },
      { name: "a_ring", data: ringIds, size: 1 },
    ]);
    this.glowVao = this.vao(this.glow.program, [
      { name: "a_corner", data: new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), size: 2 },
    ]);

    gl.disable(gl.DEPTH_TEST);
    gl.enable(gl.BLEND);
    // Mélange additif en couleurs prémultipliées : les lumières s'ajoutent, sur fond transparent.
    gl.blendFunc(gl.ONE, gl.ONE);
    gl.clearColor(0, 0, 0, 0);
  }

  static create(canvas: HTMLCanvasElement, options: RendererOptions): SphereRenderer | null {
    const gl = canvas.getContext("webgl2", {
      alpha: true,
      antialias: false,
      premultipliedAlpha: true,
      powerPreference: "low-power",
      preserveDrawingBuffer: false,
    });
    if (!gl) return null;
    try {
      return new SphereRenderer(gl, options);
    } catch (error) {
      uiLog("error", `sphère : ${error instanceof Error ? error.message : String(error)}`);
      return null;
    }
  }

  private vao(
    program: WebGLProgram,
    attributes: { name: string; data: Float32Array; size: number }[],
  ): WebGLVertexArrayObject {
    const gl = this.gl;
    const vao = gl.createVertexArray();
    gl.bindVertexArray(vao);
    for (const attribute of attributes) {
      const location = gl.getAttribLocation(program, attribute.name);
      const buffer = gl.createBuffer();
      this.buffers.push(buffer);
      gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
      gl.bufferData(gl.ARRAY_BUFFER, attribute.data, gl.STATIC_DRAW);
      if (location >= 0) {
        gl.enableVertexAttribArray(location);
        gl.vertexAttribPointer(location, attribute.size, gl.FLOAT, false, 0, 0);
      }
    }
    gl.bindVertexArray(null);
    return vao;
  }

  resize(width: number, height: number, dpr: number): void {
    const canvas = this.gl.canvas as HTMLCanvasElement;
    const w = Math.max(1, Math.round(width * dpr));
    const h = Math.max(1, Math.round(height * dpr));
    if (canvas.width !== w || canvas.height !== h) {
      canvas.width = w;
      canvas.height = h;
    }
    this.dpr = dpr;
    this.setAspect(w / h);
    this.gl.viewport(0, 0, w, h);
  }

  render(frame: FrameParams, dtSeconds: number): void {
    const gl = this.gl;
    const v = frame.values;
    const dt = Math.min(dtSeconds, 0.1);
    this.time += dt;
    this.flowTime += dt * v.flow;
    this.ringTime += dt * v.ringSpeed;
    const mask = this.avatarModel === "cyberMask";
    if (mask) {
      this.yaw = Math.sin(this.time * 0.4) * 0.05; // léger balancement
    } else {
      this.yaw += dt * v.spin;
    }
    const rot = rotation(this.yaw, mask ? 0 : TILT_RAD);

    let color: Float32List = frame.color;
    let colorHot: Float32List = frame.colorHot;
    if (mask) {
      // Si la couleur est une couleur d'alerte (rouge danger ou ambre warning), on la laisse dominer
      // En veille/écoute/réflexion, on garde l'ADN vert matrix subtilement mélangé au cyan
      const isAlert = frame.color[0] > 0.6 && (frame.color[1] < 0.8 || frame.color[2] < 0.6);
      const matrixWeight = isAlert ? 0.15 : 0.65;
      tintInto(this.tint, frame.color, MASK_GREEN, matrixWeight);
      tintInto(this.tintHot, frame.colorHot, MASK_GREEN_HOT, matrixWeight);
      color = this.tint;
      colorHot = this.tintHot;
    }

    const canvas = gl.canvas as HTMLCanvasElement;
    const sizeScale = Math.min(canvas.width, canvas.height) / (this.dpr * 440);
    const pointPx = Math.max(
      1.5,
      2.6 * v.pointSize * this.dpr * Math.min(Math.max(sizeScale, 0.6), 1.4),
    );

    gl.clear(gl.COLOR_BUFFER_BIT);

    // Pour le masque cybernétique, on n'affiche pas la boule rougeoyante circulaire de fond
    if (!mask) {
      gl.useProgram(this.glow.program);
      gl.uniform2f(this.glow.uniforms.u_resolution ?? null, canvas.width, canvas.height);
      gl.uniform3fv(this.glow.uniforms.u_color ?? null, frame.color);
      gl.uniform1f(this.glow.uniforms.u_core ?? null, frame.coreNow);
      gl.uniform1f(this.glow.uniforms.u_halo ?? null, v.halo);
      gl.uniform1f(this.glow.uniforms.u_radius ?? null, SPHERE_RADIUS_NDC * frame.scale);
      gl.bindVertexArray(this.glowVao);
      gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
    }

    const p = this.points.uniforms;
    gl.useProgram(this.points.program);
    gl.uniformMatrix4fv(p.u_proj ?? null, false, this.projection);
    gl.uniformMatrix3fv(p.u_rot ?? null, false, rot);
    gl.uniform1f(p.u_camera ?? null, CAMERA_DISTANCE);
    gl.uniform1f(p.u_flowTime ?? null, this.flowTime);
    gl.uniform1f(p.u_time ?? null, this.time);
    gl.uniform1f(p.u_amp ?? null, v.amplitude);
    gl.uniform1f(p.u_freq ?? null, v.frequency);
    gl.uniform1f(p.u_audio ?? null, frame.audio);
    gl.uniform1f(p.u_scale ?? null, frame.scale);
    gl.uniform1f(p.u_jitter ?? null, v.jitter);
    gl.uniform1f(p.u_pointSize ?? null, pointPx);
    gl.uniform2f(p.u_extent ?? null, this.extentX, this.extentY);
    gl.uniform3fv(p.u_color ?? null, color);
    gl.uniform3fv(p.u_colorHot ?? null, colorHot);
    gl.uniform1f(p.u_intensity ?? null, 0.85);
    gl.bindVertexArray(this.pointsVao);
    gl.drawArrays(gl.POINTS, 0, this.pointCount);

    if (!mask && v.rings > 0.01) {
      const r = this.rings.uniforms;
      gl.useProgram(this.rings.program);
      gl.uniformMatrix4fv(r.u_proj ?? null, false, this.projection);
      gl.uniformMatrix3fv(r.u_rot ?? null, false, rotation(0));
      gl.uniform1f(r.u_camera ?? null, CAMERA_DISTANCE);
      gl.uniform1f(r.u_ringTime ?? null, this.ringTime);
      gl.uniform1f(r.u_scale ?? null, frame.scale);
      gl.uniform1f(r.u_pointSize ?? null, pointPx);
      gl.uniform3fv(r.u_color ?? null, frame.color);
      gl.uniform1f(r.u_intensity ?? null, v.rings * 0.9);
      gl.bindVertexArray(this.ringsVao);
      gl.drawArrays(gl.POINTS, 0, RING_POINTS * 2);
    }
    gl.bindVertexArray(null);
  }

  dispose(): void {
    const gl = this.gl;
    for (const buffer of this.buffers) gl.deleteBuffer(buffer);
    gl.deleteVertexArray(this.pointsVao);
    gl.deleteVertexArray(this.ringsVao);
    gl.deleteVertexArray(this.glowVao);
    gl.deleteProgram(this.points.program);
    gl.deleteProgram(this.rings.program);
    gl.deleteProgram(this.glow.program);
  }
}
