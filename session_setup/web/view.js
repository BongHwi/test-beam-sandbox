// SPDX-License-Identifier: LGPL-3.0-or-later
// Experiment view: the session's devices in 3D at the surveyed positions of its setup, the beam
// and run state read from the session, and decorative particles while the beam is on.
// It uses three.js r155 (MIT), which ships inside the image's JSROOT 7.5.2; nothing comes from the network.
import * as THREE from '/jsroot/modules/three.mjs';
import { controlLive, operate, canOperate } from './control.js';


// Scene axes: three.js x = beam z, y = device y, z = -device x (right-handed, beam to the right).
const at = (x, y, z) => new THREE.Vector3(z, y, -x);
const COLORS = { reference: 0x4f8fdc, dut: 0xe8912d, off: 0x7d838c, aligned: 0x37d6c4, beam: 0xffd54a, axis: 0xc9d1dc };
// Translucent faces write no depth and are drawn in this order, so they never cut each other out.
const ORDER = { devices: 1, beam: 2, particles: 3 };
// The first view can be chosen in the address: view=reset, side, top or beam.
const FIRST_VIEW = ['reset', 'side', 'top', 'beam'].includes(new URLSearchParams(location.search).get('view'))
  ? new URLSearchParams(location.search).get('view') : 'reset';
let firstBuild = true;
const SVG_FRAME_MS = 66, GL_FRAME_MS = 16, LIVE_MS = 2000, LAYOUT_MS = 2000, PARTICLES = 36;

let ctx = null;          // { session, t, el }
let layout = null, layoutKey = null, live = null, main = null;
let active = false, timer = null, liveBusy = false, frameRequest = null;

const fmt = (v, d = null) => (v === null || v === undefined) ? '-' :
  (Array.isArray(v) ? v.map(x => fmt(x, d)).join(', ') : (d === null ? String(v) : Number(v).toFixed(d)));
// Solve the x component of cubic-bezier(.22, 1, .36, 1) before evaluating y.
function easeView(x) {
  let lo = 0, hi = 1;
  for (let i = 0; i < 20; i++) {
    const t = (lo + hi) / 2, a = 1 - t;
    if (3*a*a*t*.22 + 3*a*t*t*.36 + t*t*t < x) lo = t; else hi = t;
  }
  const t = (lo + hi) / 2, a = 1 - t;
  return 3*a*a*t + 3*a*t*t + t*t*t;
}

const count = v => v === null || v === undefined ? '-' : Number(v).toLocaleString('en-US');
const body = document.body.dataset;

function webglAvailable() {
  try {
    const c = document.createElement('canvas');
    return !!(window.WebGLRenderingContext && (c.getContext('webgl2') || c.getContext('webgl')));
  } catch (err) { return false; }
}

function line(points, color, opacity = 1) {
  const g = new THREE.BufferGeometry().setFromPoints(points);
  return new THREE.Line(g, new THREE.LineBasicMaterial({ color, transparent: opacity < 1, opacity, depthWrite: opacity >= 1 }));
}

// One 3D drawing of devices and beam: the experiment view of a session, or the preview of a setup being edited.
export class Scene3D {
  constructor(box, labelBox, reasonBox, options) {
    Object.assign(this, { box, labelBox, reasonBox, el: options.el, t: options.t, report: !!options.report });
    Object.assign(this, { renderer: null, kind: 'none', scene: null, camera: null, controls: null, three: null,
                          particles: [], envelope: null, labels: [], alignedGroup: null, home: null, span: 100,
                          lastFrame: 0, view: 'reset', beamOn: false, autoFit: true, userGesture: false });
  }

  create() {
    // The image's JSROOT ships three.js r155 with WebGLRenderer and SVGRenderer; WebGL first.
    const reason = this.reasonBox;
    reason.replaceChildren();
    if (webglAvailable()) {
      try {
        this.renderer = new THREE.WebGLRenderer({ antialias: true });
        this.renderer.setPixelRatio(Math.min(2, window.devicePixelRatio || 1));
        this.kind = 'webgl';
      } catch (err) { this.renderer = null; }
    }
    if (!this.renderer) {
      try {
        this.renderer = new THREE.SVGRenderer();
        this.renderer.setQuality('high');
        this.kind = 'svg';
        reason.append(this.el('p', { class: 'note', 'data-t': 'no_webgl' }, this.t('no_webgl')));
      } catch (err) {
        this.renderer = null;
        this.kind = 'none';
        reason.append(this.el('p', this.report ? { class: 'alert warn', id: 'no-3d' } : { class: 'alert warn' }, this.el('span', { 'data-t': 'no_3d' }, this.t('no_3d')),
                              ' ' + String(err && err.message || err)));
      }
    }
    if (this.report) body.renderer = this.kind;
    if (!this.renderer) return false;
    this.renderer.setClearColor(0x111723, 1);
    this.box.prepend(this.renderer.domElement);
    this.resizeObserver = new ResizeObserver(() => {
      const width = this.box.clientWidth, height = this.box.clientHeight;
      if (!width || !height || !this.camera) return;
      if (this.observedSize && this.observedSize[0] === width && this.observedSize[1] === height) return;
      this.observedSize = [width, height];
      const [w,h] = this.size();
      this.camera.aspect = w / h;
      this.camera.updateProjectionMatrix();
      if (this.autoFit) this.setView(this.view, false);
      else this.render(true);
    });
    this.resizeObserver.observe(this.box);
    return true;
  }

  size() {
    return [Math.max(320, this.box.clientWidth), Math.max(240, this.box.clientHeight)];
  }

  label(text, where, cls, priority) {
    const node = this.el('div', { class: 'scene-label ' + (cls || '') }, text);
    this.labelBox.append(node);
    this.labels.push({ node, where, priority });
    return node;
  }

  device(d, group, below) {
    const [x, y, z] = d.position_mm, [w, h] = d.size_mm, thick = 0.6;
    const role = d.role === 'dut' ? 'dut' : 'reference';
    const edges = (geometry, color) => new THREE.LineSegments(new THREE.EdgesGeometry(geometry), new THREE.LineBasicMaterial({ color }));
    let tilesOff = 0, tiles = 0;
    if (d.kind === 'msensor' && d.tiles && d.tile_pixels) {
      // One box per tile, tile 0 at column 0, row 0, numbered along x first (as in the setup file).
      const [nx, ny] = d.tiles, tw = d.tile_pixels[0] * d.pitch_um[0] / 1000, th = d.tile_pixels[1] * d.pitch_um[1] / 1000;
      const off = new Set(d.tiles_off || []);
      for (let i = 0; i < nx * ny; i++) {
        const tx = i % nx, ty = Math.floor(i / nx), isOff = off.has(i);
        const cx = x - w / 2 + (tx + 0.5) * tw, cy = y - h / 2 + (ty + 0.5) * th;
        const geometry = new THREE.BoxGeometry(thick, th * 0.94, tw * 0.94);
        const material = this.kind === 'webgl' ? new THREE.MeshStandardMaterial({ color: isOff ? 0x5d6470 : 0xd98a2b, roughness: 0.42, metalness: 0.25 }) :
          new THREE.MeshBasicMaterial({ color: isOff ? COLORS.off : COLORS.dut, transparent: true, opacity: isOff ? 0.35 : 0.8, depthWrite: false });
        const mesh = new THREE.Mesh(geometry, material);
        mesh.renderOrder = ORDER.devices;
        mesh.position.copy(at(cx, cy, z));
        mesh.userData = { tile: i, off: isOff };
        const outline = edges(geometry, isOff ? 0xa9b0b9 : 0xffc27a);
        outline.position.copy(mesh.position);
        group.add(mesh, outline);
        tiles += 1; tilesOff += isOff ? 1 : 0;
      }
    } else {
      const geometry = new THREE.BoxGeometry(thick, h, w);
      const material = this.kind === 'webgl' ? new THREE.MeshStandardMaterial(d.kind === 'msensor' ?
        { color: 0xd98a2b, roughness: 0.42, metalness: 0.25 } : { color: 0x46628a, roughness: 0.35, metalness: 0.35 }) :
        new THREE.MeshBasicMaterial({ color: COLORS[role], transparent: true, opacity: 0.55, depthWrite: false });
      const mesh = new THREE.Mesh(geometry, material);
      mesh.renderOrder = ORDER.devices;
      mesh.position.copy(at(x, y, z));
      const outline = edges(geometry, role === 'dut' ? 0xffc27a : (this.kind === 'webgl' ? 0x8fb4f5 : 0xa8cdf5));
      outline.position.copy(mesh.position);
      group.add(mesh, outline);
    }
    const bar = 1.4;
    if (this.kind === 'webgl') {
      const metal = new THREE.MeshStandardMaterial({ color: 0x7a8594, roughness: 0.4, metalness: 0.6 });
      for (const [bw,bh,dy,dx] of [[w+2*bar,bar,h/2+bar/2,0],[w+2*bar,bar,-h/2-bar/2,0],[bar,h,0,w/2+bar/2],[bar,h,0,-w/2-bar/2]]) {
        const frame = new THREE.Mesh(new THREE.BoxGeometry(1, bh, bw), metal);
        frame.position.copy(at(x+dx, y+dy, z+0.3)); frame.userData.scenePart = 'frame'; group.add(frame);
      }
    }
    this.mounts.push(y - h/2 - (this.kind === 'webgl' ? bar : 0));
    const lift = below ? 16 : 7;
    const leader = line([at(x,y+h/2+2,z),at(x,y+h/2+lift,z)],0x6b7689,0.9);
    leader.userData.scenePart = 'leader'; group.add(leader);
    const text = this.el('span', { class:'name' }, d.name + ' ');
    const roleText = this.el('small', { 'data-t': role === 'dut' ? 'dut_role' : 'reference' }, this.t(role === 'dut' ? 'dut_role' : 'reference'));
    const node = this.label('', at(x,y+h/2+lift,z), role + ' device', 0);
    node.append(text, roleText, this.el('span', { class:'z' }, ` z ${z}`));
    return { tiles, tilesOff };
  }

  scintillator(d, group, below) {
    const [x, y, z] = d.position_mm, shape = new THREE.Shape();
    const disc = d.shape === 'disc', w = d.size_mm[0], h = disc ? w : d.size_mm[1];
    if (disc) shape.absarc(0, 0, w / 2, 0, Math.PI * 2, false);
    else { shape.moveTo(-w/2, -h/2); shape.lineTo(w/2, -h/2); shape.lineTo(w/2, h/2); shape.lineTo(-w/2, h/2); shape.closePath(); }
    const hole = d.hole_mm || [];
    if (disc && hole[0] > 0) {
      const cut = new THREE.Path(); cut.absarc(0, 0, hole[0]/2, 0, Math.PI * 2, true); shape.holes.push(cut);
    } else if (!disc && hole.length === 2 && hole[0] > 0 && hole[1] > 0) {
      const [a, b] = hole.map(v => v/2), cut = new THREE.Path();
      cut.moveTo(-a, -b); cut.lineTo(-a, b); cut.lineTo(a, b); cut.lineTo(a, -b); cut.closePath(); shape.holes.push(cut);
    }
    const geometry = new THREE.ExtrudeGeometry(shape, { depth: 2, bevelEnabled: false, curveSegments: 48 });
    const material = this.kind === 'webgl' ? new THREE.MeshStandardMaterial({ color:0x3b82f6, roughness:0.25, metalness:0, transparent:true, opacity:0.6, depthWrite:false, side:THREE.DoubleSide }) :
      new THREE.MeshBasicMaterial({ color:0x3b82f6, transparent:true, opacity:0.35, depthWrite:false, side:THREE.DoubleSide });
    const mesh = new THREE.Mesh(geometry, material);
    mesh.rotation.y = Math.PI / 2; mesh.position.copy(at(x, y, z - 1)); mesh.renderOrder = ORDER.devices; group.add(mesh);
    const lift = below ? 16 : 7;
    const leader = line([at(x,y+h/2+2,z),at(x,y+h/2+lift,z)],0x6b7689,0.9);
    leader.userData.scenePart = 'leader'; group.add(leader);
    const node = this.label('',at(x,y+h/2+lift,z),'device plate',0);
    node.append(this.el('span',{class:'name'},`S${d.number} \u00b7 trg${d.channel}`),this.el('span',{class:'z'},` z ${z}`));
  }

  build(layout, firstView) {
    for (const l of this.labels) l.node.remove();
    this.labels = [];
    this.particles = []; this.mounts = []; this.flashes = [];
    if (this.viewFrame) cancelAnimationFrame(this.viewFrame);
    const scene = this.scene = new THREE.Scene();
    if (this.kind === 'webgl') {
      scene.add(new THREE.HemisphereLight(0xe6ecf5,0x1b2232,1.6));
      const key = new THREE.DirectionalLight(0xffffff,2.2); key.position.set(-60,140,90); scene.add(key);
      const fill = new THREE.DirectionalLight(0x9fb4d8,0.6); fill.position.set(90,-30,-70); scene.add(fill);
    }
    const devices = layout.devices || [];
    const plates = layout.scintillators || [];
    // Corners of all sensors and plates, used to fit every predefined view.
    this.bounds = [];
    for (const d of [...devices, ...plates]) {
      const size = d.size_mm || [0, 0], w = size[0], h = size.length > 1 ? size[1] : size[0];
      const [x,y,z] = d.position_mm;
      for (const dx of [-w/2,w/2]) for (const dy of [-h/2,h/2]) for (const dz of [-1,1]) this.bounds.push(at(x+dx,y+dy,z+dz));
    }
    this.deviceBounds = this.bounds.slice();
    this.deviceTarget = new THREE.Box3().setFromPoints(this.deviceBounds).getCenter(new THREE.Vector3());
    [...devices, ...plates].forEach((d,i) => {
      const [x,y,z] = d.position_mm, h = d.size_mm.length > 1 ? d.size_mm[1] : d.size_mm[0];
      this.bounds.push(at(x,y+h/2+(i%2 ? 16 : 7),z));
    });
    const zs = [...devices, ...plates].map(d => d.position_mm[2]);
    const beam = layout.beam || {};
    const centre = beam.position_mm ? beam.position_mm : [devices.reduce((a, d) => a + d.position_mm[0], 0) / Math.max(1, devices.length),
      devices.reduce((a, d) => a + d.position_mm[1], 0) / Math.max(1, devices.length), Math.min(...zs) - 20];
    for (const dx of [-1.5,1.5]) for (const dy of [-1.5,1.5]) for (const dz of [-2,0])
      this.bounds.push(at(centre[0]+dx,centre[1]+dy,centre[2]+dz));
    const z0 = Math.min(centre[2], ...zs), z1 = Math.max(...zs) + 15;
    this.span = Math.max(40, z1 - z0, ...plates.map(p => Math.max(...p.size_mm) * 1.5));
    const group = new THREE.Group();
    let tiles = 0, tilesOff = 0;
    devices.forEach((d, i) => { const r = this.device(d, group, i % 2 === 1); tiles += r.tiles; tilesOff += r.tilesOff; });
    plates.forEach((d, i) => this.scintillator(d, group, (devices.length + i) % 2 === 1));
    scene.add(group);
    // Beam axis with an arrow and z ticks every 10 mm.
    const [bx, by] = centre;
    const low = Math.floor(z0 / 10) * 10, high = Math.ceil(z1 / 10) * 10;
    scene.add(line([at(bx, by, low), at(bx, by, high)], COLORS.axis, 0.5));
    const arrow = new THREE.Mesh(new THREE.ConeGeometry(1.2, 4, 12), new THREE.MeshBasicMaterial({ color: COLORS.axis }));
    arrow.rotation.z = -Math.PI / 2;
    arrow.position.copy(at(bx, by, high + 2));
    scene.add(arrow);
    this.label(this.t('beam_axis'), at(bx, by - 3, high + 2), 'axis', 1).dataset.t = 'beam_axis';
    const ymin = this.kind === 'webgl' ? (this.mounts.length ? Math.min(...this.mounts) : by) - 7 :
      Math.min(...devices.map(d => d.position_mm[1] - d.size_mm[1] / 2), by) - 9;
    const axisOpacity = this.kind === 'webgl' ? 0.55 : 0.8;
    const zAxis = line([at(bx, ymin, low), at(bx, ymin, high)], COLORS.axis, axisOpacity);
    zAxis.userData.scenePart = 'z-axis'; scene.add(zAxis);
    for (let z = low; z <= high; z += 10) {
      scene.add(line([at(bx, ymin, z), at(bx, ymin - (z % 50 === 0 ? 2.5 : 1.2), z)], COLORS.axis, axisOpacity));
      if (z % 20 === 0) this.label(String(z), at(bx, ymin - 4.5, z), 'tick', 2);
    }
    this.label(this.t('z_axis'), at(bx, ymin - 8, (low + high) / 2), 'tick', 2).dataset.t = 'z_axis';
    // x and y directions of the devices at the beam source.
    const s = 6;
    scene.add(line([at(bx, by, centre[2]), at(bx + s, by, centre[2])], 0xff6b6b));
    scene.add(line([at(bx, by, centre[2]), at(bx, by + s, centre[2])], 0x6bdc7b));
    this.label('x', at(bx + s + 1, by, centre[2]), 'axis', 1); this.label('y', at(bx, by + s + 1, centre[2]), 'axis', 1);
    // Beam source and the drawn beam (illustration of the setup values).
    const source = new THREE.Mesh(new THREE.BoxGeometry(2, 3, 3), new THREE.MeshBasicMaterial({ color: 0x9aa4b2 }));
    source.userData.scenePart = 'beam-source';
    source.position.copy(at(bx, by, centre[2] - 1));
    scene.add(source);
    const sigma = beam.spread_mm2 ? beam.spread_mm2.map(v => Math.sqrt(v)) : [0.3, 0.3];
    const radius = Math.max(0.3, 1.5 * Math.max(...sigma)), length = z1 - centre[2];
    if (this.kind === 'webgl') {
      this.envelope = new THREE.Group();
      for (let z = centre[2]; z <= z1; z += 40) {
        const points = [];
        for (let k=0; k<=48; k++) { const a=k/48*2*Math.PI; points.push(at(bx+sigma[0]*Math.cos(a),by+sigma[1]*Math.sin(a),z)); }
        const ring=line(points,COLORS.beam,0.7); ring.userData.scenePart='beam-ring'; this.envelope.add(ring);
      }
    } else {
      this.envelope = new THREE.Mesh(new THREE.CylinderGeometry(radius, radius, length, 16, 1, true),
        new THREE.MeshBasicMaterial({ color:COLORS.beam, transparent:true, opacity:0.05, side:THREE.DoubleSide, depthWrite:false }));
      this.envelope.rotation.z=-Math.PI/2; this.envelope.position.copy(at(bx,by,centre[2]+length/2));
    }
    this.envelope.renderOrder = ORDER.beam;
    scene.add(this.envelope);
    for (let i = 0; i < PARTICLES; i++) {
      let p;
      if (this.kind === 'webgl') {
        const g=new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(0,0,0),new THREE.Vector3(-7,0,0)]);
        g.setAttribute('color',new THREE.Float32BufferAttribute([1,0.835,0.29,17/255,23/255,35/255],3));
        p=new THREE.Line(g,new THREE.LineBasicMaterial({vertexColors:true}));
      } else p=line([new THREE.Vector3(0,0,0),new THREE.Vector3(3.5,0,0)],COLORS.beam);
      p.userData = { phase:i/PARTICLES, x:0, y:0, cycle:-1, last:null };
      p.renderOrder = ORDER.particles;
      this.particles.push(p);
      scene.add(p);
    }
    this.three = { centre, z0, z1, sigma, length };
    this.planes=devices.map(d=>({x:d.position_mm[0],y:d.position_mm[1],z:d.position_mm[2],w:d.size_mm[0],h:d.size_mm[1]}));
    if (this.kind === 'webgl') for (let i=0; i<160; i++) {
      const flash=new THREE.Mesh(new THREE.CircleGeometry(0.55,16),new THREE.MeshBasicMaterial({color:0xfff3b0,transparent:true,opacity:0,depthWrite:false,side:THREE.DoubleSide}));
      flash.rotation.y=Math.PI/2; flash.visible=false; flash.userData={time:-Infinity,scenePart:'hit'}; flash.renderOrder=ORDER.particles; scene.add(flash); this.flashes.push(flash);
    }
    this.flashNext=0;
    // Positions found by alignment, as outlines (only values of the alignment result file).
    this.alignedGroup = new THREE.Group();
    const corrections = (layout.alignment || {}).corrections_um || {};
    for (const d of devices) {
      const c = corrections[d.name];
      if (!c) continue;
      const g = new THREE.BoxGeometry(0.8, d.size_mm[1], d.size_mm[0]);
      const o = new THREE.LineSegments(new THREE.EdgesGeometry(g), new THREE.LineBasicMaterial({ color: COLORS.aligned }));
      o.position.copy(at(d.position_mm[0] + c[0] / 1000, d.position_mm[1] + c[1] / 1000, d.position_mm[2]));
      this.alignedGroup.add(o);
    }
    scene.add(this.alignedGroup);
    const [width, height] = this.size();
    this.camera = new THREE.PerspectiveCamera(26, width / height, 0.5, 5000);
    const target = new THREE.Box3().setFromPoints(this.bounds).getCenter(new THREE.Vector3());
    this.home = { target, position: target.clone().add(new THREE.Vector3(-0.35 * this.span, 0.45 * this.span, 1.05 * this.span)) };
    if (!this.controls) {
      this.controls = new THREE.OrbitControls(this.camera, this.renderer.domElement);
      this.controls.addEventListener('start', () => {
        this.userGesture = true;
        if (this.viewFrame) cancelAnimationFrame(this.viewFrame);
        this.viewFrame = null;
      });
      this.controls.addEventListener('change', () => {
        if (this.userGesture) this.autoFit = false;
        this.render(true);
      });
      this.controls.addEventListener('end', () => { this.userGesture = false; });
    } else this.controls.object = this.camera;
    this.controls.screenSpacePanning = true;
    this.setView(firstView || this.view, false);
    this.observedSize = [this.box.clientWidth, this.box.clientHeight];
    return { devices: devices.length, tiles, tilesOff, names: devices.map(d => d.name) };
  }

  setView(which, animate = true) {
    if (!this.camera || !this.home) return;
    this.autoFit = true;
    if (this.viewFrame) cancelAnimationFrame(this.viewFrame);
    const startPosition=this.camera.position.clone(), startTarget=this.controls.target.clone();
    const three = this.three, span = this.span;
    const target = (which === 'reset' ? this.home.target : this.deviceTarget).clone(), d = span * 1.25;
    const fitBounds = which === 'reset' ? this.bounds : this.deviceBounds;
    this.visibleBounds = fitBounds;
    const position = { reset: this.home.position.clone(), side: target.clone().add(new THREE.Vector3(0, 0, d)),
                       top: target.clone().add(new THREE.Vector3(0, d, 0.001)),
                       beam: target.clone().add(new THREE.Vector3(-d, 0, 0)) }[which];
    this.camera.position.copy(position);
    this.camera.up.set(0, 1, 0);
    // Fit the projected device envelope while keeping a small margin on all sides.
    const direction = position.clone().sub(target).normalize();
    let near = Math.max(1, span * .05), far = span * 20;
    for (let i = 0; i < 36; i++) {
      const distance = (near + far) / 2;
      this.camera.position.copy(target).addScaledVector(direction, distance);
      this.camera.lookAt(target); this.camera.updateMatrixWorld();
      const points = fitBounds.map(p => p.clone().project(this.camera));
      const minX = Math.min(...points.map(p=>p.x)), maxX = Math.max(...points.map(p=>p.x));
      const minY = Math.min(...points.map(p=>p.y)), maxY = Math.max(...points.map(p=>p.y));
      const tooLarge = points.some(p=>p.z < -1 || p.z > 1) || maxX-minX > 1.7 || maxY-minY > 1.76 || minX < -.92 || maxX > .92 || minY < -.92 || maxY > .92;
      if (tooLarge) near = distance; else far = distance;
    }
    const destination=target.clone().addScaledVector(direction,far);
    this.camera.position.copy(startPosition);
    this.view = which;
    const toolbar = this.box.closest('.stage, .setup-preview-card');
    for (const button of toolbar?.querySelectorAll('[data-view]') || []) {
      button.classList.toggle('active', button.dataset.view === which);
      button.setAttribute('aria-pressed', String(button.dataset.view === which));
    }
    if (this.report) body.cameraview = which;
    if (!animate) {
      this.camera.position.copy(destination); this.controls.target.copy(target); this.controls.update(); this.render(true);
      return;
    }
    const started=performance.now();
    const advance=now=>{
      const progress=Math.min(1,(now-started)/300), amount=easeView(progress);
      this.camera.position.lerpVectors(startPosition,destination,amount);
      this.controls.target.lerpVectors(startTarget,target,amount);
      this.controls.update(); this.render(true);
      this.viewFrame=progress<1 ? requestAnimationFrame(advance) : null;
    };
    this.viewFrame=requestAnimationFrame(advance);
  }

  placeLabels() {
    // Labels that would cover one another: device labels move up or down, axis and tick labels are hidden.
    const [width, height] = this.size();
    const placed = [];
    const overlaps = r => placed.some(q => r.x < q.x + q.w && q.x < r.x + r.w && r.y < q.y + q.h && q.y < r.y + r.h);
    const order = this.labels.map((l, i) => [l, i]).sort((a, b) => a[0].priority - b[0].priority || a[1] - b[1]);
    for (const [l] of order) {
      const p = l.where.clone().project(this.camera);
      if (p.z > 1 || p.z < -1) { l.node.style.display = 'none'; continue; }
      l.node.style.display = '';
      const w = l.node.offsetWidth, h = l.node.offsetHeight, below = l.node.classList.contains('below');
      const r = { x: (p.x + 1) / 2 * width - w / 2, y: (1 - p.y) / 2 * height - (below ? 0 : h), w, h };
      r.x = Math.max(0, Math.min(width - w, r.x));
      r.y = Math.max(0, Math.min(height - h, r.y));
      if (l.priority === 0) {
        const origin = r.y;
        for (let n = 1; n < 80 && overlaps(r); n++) {
          const direction = (n % 2 ? (below ? 1 : -1) : (below ? -1 : 1));
          r.y = Math.max(0, Math.min(height - h, origin + direction * Math.ceil(n / 2) * (h + 1)));
        }
      } else if (overlaps(r)) { l.node.style.display = 'none'; continue; }
      placed.push(r);
      l.node.style.transform = `translate(${r.x.toFixed(1)}px, ${r.y.toFixed(1)}px)`;
    }
  }

  moveParticles(now) {
    const on = this.beamOn;
    this.envelope.visible = on;
    let shown = 0;
    const { centre, sigma, length } = this.three, period = 1600;
    for (const p of this.particles) {
      const f = ((now / period) + p.userData.phase) % 1, cycle = Math.floor(now / period + p.userData.phase);
      if (cycle !== p.userData.cycle) {
        // A new decorative particle: Gaussian in x and y with the setup's variance.
        const g = () => Math.sqrt(-2 * Math.log(1 - Math.random())) * Math.cos(2 * Math.PI * Math.random());
        Object.assign(p.userData, { cycle, x: centre[0] + g() * sigma[0], y: centre[1] + g() * sigma[1] });
      }
      p.visible = on;
      if (on) {
        const z=centre[2]+f*length, last=p.userData.last;
        p.position.copy(at(p.userData.x,p.userData.y,z)); shown += 1;
        if (this.kind === 'webgl' && last !== null && z > last) for (const plane of this.planes) {
          if (plane.z > last && plane.z <= z && Math.abs(p.userData.x-plane.x) <= plane.w/2 && Math.abs(p.userData.y-plane.y) <= plane.h/2) {
            const hit=this.flashes[this.flashNext++ % this.flashes.length];
            hit.position.copy(at(p.userData.x,p.userData.y,plane.z-0.5)); hit.userData.time=now; hit.visible=true;
          }
        }
        p.userData.last=z;
      } else p.userData.last=null;
    }
    for (const hit of this.flashes) {
      const age=(now-hit.userData.time)/600;
      hit.visible=on && age>=0 && age<1; if (hit.visible) hit.material.opacity=1-age;
      if (!on) hit.userData.time=-Infinity;
    }
    if (this.report) body.particles = String(shown);
  }

  render(force) {
    if (!this.renderer || !this.scene || !this.camera) return;
    const [width, height] = this.size();
    const now = performance.now();
    if (!force && now - this.lastFrame < (this.kind === 'svg' ? SVG_FRAME_MS : GL_FRAME_MS)) return;
    this.lastFrame = now;
    this.renderer.setSize(width, height);
    this.camera.aspect = width / height;
    this.camera.updateProjectionMatrix();
    this.moveParticles(now);
    this.renderer.render(this.scene, this.camera);
    this.placeLabels();
    if (this.report && this.visibleBounds?.length) {
      const projected = this.visibleBounds.map(p => p.clone().project(this.camera));
      body.sceneBounds = JSON.stringify({ left:(Math.min(...projected.map(p=>p.x))+1)/2, right:(Math.max(...projected.map(p=>p.x))+1)/2,
        top:(1-Math.max(...projected.map(p=>p.y)))/2, bottom:(1-Math.min(...projected.map(p=>p.y)))/2 });
    }
  }

  relabel() {
    for (const l of this.labels) {
      if (l.node.dataset.t) l.node.textContent = this.t(l.node.dataset.t);
      for (const n of l.node.querySelectorAll('[data-t]')) n.textContent = this.t(n.dataset.t);
    }
  }
}

function buildScene() {
  const r = main.build(layout, firstBuild ? FIRST_VIEW : (body.cameraview || 'reset'));
  firstBuild = false;
  main.alignedGroup.visible = !!document.getElementById('show-aligned')?.checked;
  main.render(true);
  Object.assign(body, { devices: String(r.devices), tiles: String(r.tiles), tilesoff: String(r.tilesOff),
                        devicenames: r.names.join(',') });
}

function loop() {
  frameRequest = null;
  if (!active || document.hidden) return;
  const previousBeamOn = main.beamOn;
  main.beamOn = !!(live && live.running && live.beam_on);
  main.render(previousBeamOn !== main.beamOn);
  // Only a beam that is on needs frames; otherwise the scene changes with the controls only.
  if (main.beamOn) frameRequest = requestAnimationFrame(loop);
}

function kick() {
  if (!frameRequest && active) frameRequest = requestAnimationFrame(loop);
}

function row(label, value, id) { return ctx.el('tr', {}, ctx.el('th', { 'data-t': label }, ctx.t(label)), ctx.el('td', id ? { id } : {}, value)); }

function renderStatus() {
  const { t, el } = ctx;
  controlLive(live);
  const badge = document.getElementById('beam-badge'), note = document.getElementById('beam-note');
  const s = live || {};
  let text = t('beam_off_badge'), cls = 'off';
  if (live && !s.exists) { text = t('no_session_badge'); cls = 'none'; }
  else if (live && !s.running) { text = t('stopped_badge'); cls = 'none'; }
  else if (s.beam_on) { text = t('beam_on_badge'); cls = 'on'; }
  else if (!live || s.beam_on === null || s.beam_on === undefined) { text = t('beam_unknown_badge'); cls = 'none'; }
  badge.textContent = text;
  badge.className = 'beam-badge ' + cls;
  body.beam = s.running ? (s.beam_on ? 'on' : (s.beam_on === false ? 'off' : '-')) : '-';
  note.replaceChildren();
  if (live && !s.exists) note.append(el('p', { class: 'alert warn' }, t('missing')));
  else if (live && !s.running) note.append(el('p', { class: 'alert warn' }, t('stopped')));
  else if (s.beam_on) {
    note.append(el('p', { class: 'note', id: 'illustration' }, t('illustration')));
    if (s.rc_state !== 'RUNNING') note.append(el('p', { class: 'note' }, t('daq_idle')));
  } else if (live) note.append(el('p', { class: 'note' }, t('beam_off_note')));
  const rate = s.events_per_s ?? null;
  document.getElementById('v-session').textContent = ctx.session || '';
  document.getElementById('v-rc').replaceChildren(el('span', { class:'rc-badge' + (s.rc_state === 'RUNNING' ? ' running' : '') }, fmt(s.rc_state)));
  document.getElementById('v-run').textContent = fmt(s.run);
  document.getElementById('v-events').textContent = count(s.events);
  document.getElementById('v-rate').textContent = rate === null ? '-' : `${rate.toFixed(1)} ${t('per_second')}`;
  document.getElementById('read-every').textContent = `${t('updated')} ${s.read_utc ? s.read_utc.slice(11, 19) : '--:--:--'} UTC \u00b7 ${t('read_interval').replace('{n}', '2')}`;
  const targetEvents = layout?.settings?.current?.run?.events ?? layout?.run_events;
  const percent = targetEvents && s.events != null ? Math.min(100,100*s.events/targetEvents) : null;
  document.getElementById('v-target').textContent = targetEvents ? '/ ' + count(targetEvents) : '';
  document.getElementById('event-percent').textContent = percent === null ? '' : Math.round(percent)+' %';
  document.querySelector('#run-progress span').style.width = percent === null ? '0%' : percent+'%';
  Object.assign(body, { rc: fmt(s.rc_state), run: fmt(s.run), events: fmt(s.events) });
  // Nothing to draw: the scene says why instead of staying empty.
  const message = document.getElementById('scene-message');
  const drawn = layoutKey !== null && s.running;
  if (main.renderer && main.scene) main.renderer.domElement.style.visibility = drawn ? 'visible' : 'hidden';
  document.getElementById('scene-labels').style.visibility = drawn ? 'visible' : 'hidden';
  message.textContent = drawn || !live ? '' : t(!s.exists ? 'missing' : (!s.running ? 'stopped' : 'empty'));
  renderLayoutText();
}

function renderLayoutText() {
  const { t, el } = ctx;
  const beamTable = document.getElementById('beam-table'), devicesTable = document.getElementById('devices-table');
  const source = document.getElementById('layout-source');
  if (!layout || !layout.running) { beamTable.replaceChildren(); devicesTable.replaceChildren(); source.replaceChildren(); return; }
  const b = layout.beam || {};
  const nextSettings = layout.settings?.next, currentSettings = layout.settings?.current || nextSettings;
  const runValue = currentSettings?.run.events ?? layout.run_events;
  const nextRun = nextSettings?.run.events;
  const runText = nextRun !== undefined && nextRun !== runValue ? `${runValue} ${t('next_run_prefix')} ${nextRun}` : fmt(runValue);
  beamTable.replaceChildren(row('particle', fmt(b.particle)), row('energy', fmt(b.energy_gev)), row('beam_rate', fmt(b.rate_hz)),
    row('centre', b.position_mm ? fmt(b.position_mm.slice(0, 2)) : '-'), row('variance', fmt(b.spread_mm2)),
    row('source_z', b.position_mm ? fmt(b.position_mm[2]) : '-'), row('run_length', runText));
  source.replaceChildren(el('p', { class: 'note' }, layout.source === 'setup' ? t('surveyed') : t('layout_source')));
  if (layout.offset_max_mm) source.append(el('p', { class: 'note' }, `${t('hidden')} ${layout.offset_max_mm}`));
  const corrections = (layout.alignment || {}).corrections_um || {};
  const aligned = Object.keys(corrections).length > 0;
  const head = ['detector', 'role', 'plane', 'position_xyz', 'size', 'pixels', 'pitch', 'settings', 'tiles_col'].concat(aligned ? ['aligned_col'] : []);
  const rows = [el('tr', {}, ...head.map(k => el('th', {}, t(k))))];
  const summary = document.getElementById('device-summary'); summary.replaceChildren();
  for (const d of layout.devices || []) {
    const c = corrections[d.name];
    const current = currentSettings?.devices.find(x => x.producer === d.label)?.settings || d.settings || {};
    const next = nextSettings?.devices.find(x => x.producer === d.label)?.settings || current;
    const settings = Object.entries(current).map(([k, v]) => `${k} ${fmt(v)}${next[k] !== undefined && JSON.stringify(v) !== JSON.stringify(next[k]) ? ` ${t('next_run_prefix')} ${fmt(next[k])}` : ''}`).join(', ');
    const tilesText = d.tiles ? `${d.tiles[0]}x${d.tiles[1]} (${(d.tiles_off || []).length ? d.tiles_off.join(', ') : '-'})` : '-';
    const cells = [d.name, t(d.role === 'dut' ? 'dut_role' : 'reference'), fmt(d.plane), fmt(d.position_mm), fmt(d.size_mm, 2),
                   d.pixels ? d.pixels.join(' x ') : '-', fmt(d.pitch_um), settings || '-', tilesText];
    if (aligned) cells.push(c ? fmt([d.position_mm[0] + c[0] / 1000, d.position_mm[1] + c[1] / 1000], 3) : '-');
    rows.push(el('tr', { 'data-device': d.name }, ...cells.map(x => el('td', {}, x))));
    summary.append(el('div', { class:'device-summary-row' }, el('span', {}, d.name),
      el('span', { class:'role-badge '+d.role }, t(d.role === 'dut' ? 'dut_role' : 'role_reference_badge'))));
  }
  devicesTable.replaceChildren(...rows);
  const plateBox = document.getElementById('scintillators-table'), plates = layout.scintillators || [];
  if (!plates.length) plateBox.replaceChildren(el('p', {}, t('scint_none')));
  else {
    const table = el('table', {}, el('tr', {}, ...['#', 'scint_channel', 'scint_shape', 'scint_size', 'scint_hole', 'position_xyz'].map(k => el('th', {}, k === '#' ? k : t(k)))));
    for (const p of plates) table.append(el('tr', {}, ...[p.number, p.channel, p.shape, fmt(p.size_mm), fmt(p.hole_mm), fmt(p.position_mm)].map(v => el('td', {}, String(v)))));
    const trigger = currentSettings?.trigger || layout.trigger || {}, nextTrigger = nextSettings?.trigger || trigger;
    const values = ['trg','veto','count'].map(key => el('p', {}, key + ' = ', el('code', {}, trigger[key] || ''), nextTrigger[key] !== undefined && trigger[key] !== nextTrigger[key] ? ` ${t('next_run_prefix')} ${nextTrigger[key]}` : ''));
    plateBox.replaceChildren(table, el('p', {}, t('trigger_settings')), ...values);
  }
  renderSettings();
  document.getElementById('aligned-toggle').style.display = aligned ? '' : 'none';
}

const settingItems = {trigger: ['trg', 'veto', 'count'], run: ['events'], alpide: ['ithr', 'strobe_length'],
  msensor: ['threshold_dac', 'readout', 'window_length_ns', 'strobe_delay_ns', 'strobe_length_ns']};
function renderSettings() {
  const box = document.getElementById('next-settings'), data = layout?.settings?.next;
  if (!data) { box.replaceChildren(); return; }
  const {el, t} = ctx;
  const sections = ['run', ...data.devices.map(d => d.section), ...(data.trigger ? ['trigger'] : [])];
  let section = document.getElementById('setting-section');
  if (!section || section.dataset.sections !== JSON.stringify(sections)) {
    section = el('select', {id:'setting-section', 'data-sections':JSON.stringify(sections)}, ...sections.map(x=>el('option',{value:x},x)));
    const item = el('select',{id:'setting-item'}), value = el('input',{id:'setting-value',type:'text'});
    const command = el('code',{id:'setting-command',class:'cmd'}), button=el('button',{id:'setting-button',class:'op',type:'button','data-t':'settings_button'},t('settings_button'));
    const result=el('div',{id:'setting-result',class:'op-result'});
    const displayValue = () => section.value === 'trigger' && (!value.value || /[^0-9A-Za-z._-]/.test(value.value)) ? JSON.stringify(value.value) : value.value;
    const fillValue = () => {
      const chosen = section.value === 'run' ? layout.settings.next.run : section.value === 'trigger' ? layout.settings.next.trigger : layout.settings.next.devices.find(d=>d.section===section.value)?.settings || {};
      const fallback = layout.devices.find(d => `${d.kind} ${d.number}` === section.value)?.settings_all || {};
      const selected = chosen[item.value] ?? fallback[item.value];
      value.value = Array.isArray(selected) ? selected.join(', ') : String(selected ?? '');
      command.textContent = `scripts/session_control.sh ${ctx.session || 'NAME'} set ${section.value} ${item.value} ${displayValue()} --id page-set`;
    };
    const fillItems = () => { const keys=settingItems[section.value.split(' ')[0]]; item.replaceChildren(...keys.map(x=>el('option',{value:x},x))); fillValue(); };
    section.addEventListener('change',fillItems); item.addEventListener('change',fillValue);
    value.addEventListener('input',()=>{command.textContent=`scripts/session_control.sh ${ctx.session || 'NAME'} set ${section.value} ${item.value} ${displayValue()} --id page-set`;});
    button.addEventListener('click',async()=>{
      button.disabled=true;
      const {status,answer}=await operate('set',{session:ctx.session,section:section.value,item:item.value,value:value.value});
      result.textContent=status===200 ? `${answer.job.output}\nexit: ${answer.job.exit_code}` : String(answer.error || status);
      await pollLayout();
    });
    box.replaceChildren(el('h2',{'data-t':'settings_title'},t('settings_title')),
      el('label',{},el('span',{'data-t':'settings_section'},t('settings_section')),' ',section), ' ',
      el('label',{},el('span',{'data-t':'settings_item'},t('settings_item')),' ',item), ' ',
      el('label',{},el('span',{'data-t':'settings_value'},t('settings_value')),' ',value), ' ', button,
      el('p',{},command),el('p',{'data-t':'settings_note',class:'note'},t('settings_note')),result);
    fillItems();
  }
  const disabled=!canOperate() || !layout.running || !live || live.rc_state === 'TERMINATED';
  for (const e of box.querySelectorAll('select,input,button:not(.copy-command)')) e.disabled=disabled;
}

async function fetchJson(path) {
  if (!ctx.session) return { exists: false, running: false, none: true };
  const response = await fetch(`${path}?session=${encodeURIComponent(ctx.session)}`, { cache: 'no-store' });
  return response.json();
}

async function pollLive() {
  if (liveBusy) return;
  liveBusy = true;
  try {
    const now = await fetchJson('/api/live');
    live = now;
    renderStatus();
    if (now.running && (!layout || !layout.running)) await pollLayout();
    main.beamOn = !!(live && live.running && live.beam_on);
    kick();
    main.render(true);
  } catch (err) { /* The next reading tries again. */ }
  liveBusy = false;
}

async function pollLayout() {
  try {
    const next = await fetchJson('/api/view');
    const key = JSON.stringify([next.devices, next.scintillators, next.beam, next.alignment, next.running]);
    layout = next;
    if (key !== layoutKey && next.running && (next.devices || []).length && main.renderer) {
      layoutKey = key;
      buildScene();
    }
    renderStatus();
    body.viewready = 'yes';
    main.render(true);
  } catch (err) { /* The next reading tries again. */ }
}

export function refreshLive() { return pollLive(); }

export function viewLanguage() {
  if (!ctx) return;
  main.relabel();
  renderStatus();
}

export function viewActive(on) {
  active = on;
  clearInterval(timer);
  timer = null;
  if (!on) return;
  // Reading stops while the tab is hidden; the next reading comes at once on return.
  let tick = 0;
  pollLive().then(() => pollLayout());
  timer = setInterval(() => {
    tick += 1;
    pollLive();
    if (tick % (LAYOUT_MS / LIVE_MS) === 0) pollLayout();
  }, LIVE_MS);
  kick();
}

export function startView(options) {
  ctx = options;
  body.viewready = 'no';
  body.particles = '0';
  main = new Scene3D(document.getElementById('scene'), document.getElementById('scene-labels'),
                     document.getElementById('view-renderer'), { el: ctx.el, t: ctx.t, report: true });
  main.create();
  for (const b of document.querySelectorAll('#view [data-view]')) b.addEventListener('click', () => main.setView(b.dataset.view));
  document.getElementById('show-aligned').addEventListener('change', e => { if (main.alignedGroup) main.alignedGroup.visible = e.target.checked; main.render(true); });
  document.addEventListener('visibilitychange', () => { if (!document.hidden) kick(); });
  window.addEventListener('resize', () => main.render(true));
}
