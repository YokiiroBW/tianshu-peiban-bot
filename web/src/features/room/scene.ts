import * as THREE from "three";
import { daylight, type RoomValues, type PreviewInput } from "./environment";

// Scene asset colors are material albedos, separate from the shared UI tokens.
export function buildRoom() {
  const scene = new THREE.Scene();
  const materials = new Set<THREE.Material>();
  const geometries = new Set<THREE.BufferGeometry>();
  const material = (color: number, roughness = 0.85) => {
    const value = new THREE.MeshStandardMaterial({ color, roughness });
    materials.add(value);
    return value;
  };
  const wood = material(0xc5aa8e);
  const trim = material(0xe9e0d6);
  const wall = material(0xbfc5d8);
  const cloth = material(0xe1dce9);
  const lilac = material(0x9e9bb9);
  const dark = material(0x585567);
  const metal = material(0x7d7b88, 0.45);
  const leaf = material(0x7c997f);
  const mesh = (
    geometry: THREE.BufferGeometry,
    mat: THREE.Material,
    parent: THREE.Object3D = scene,
  ) => {
    geometries.add(geometry);
    const object = new THREE.Mesh(geometry, mat);
    object.castShadow = true;
    object.receiveShadow = true;
    parent.add(object);
    return object;
  };
  const box = (
    w: number,
    h: number,
    d: number,
    x: number,
    y: number,
    z: number,
    mat: THREE.Material = wood,
    parent: THREE.Object3D = scene,
  ) => {
    const object = mesh(new THREE.BoxGeometry(w, h, d), mat, parent);
    object.position.set(x, y, z);
    return object;
  };
  const sphere = (
    r: number,
    x: number,
    y: number,
    z: number,
    mat: THREE.Material,
    parent: THREE.Object3D = scene,
  ) => {
    const object = mesh(new THREE.SphereGeometry(r, 16, 12), mat, parent);
    object.position.set(x, y, z);
    return object;
  };
  const rod = (
    a: number[],
    b: number[],
    radius: number,
    mat: THREE.Material,
    parent: THREE.Object3D = scene,
  ) => {
    const start = new THREE.Vector3(...a);
    const end = new THREE.Vector3(...b);
    const object = mesh(
      new THREE.CylinderGeometry(radius, radius, start.distanceTo(end), 10),
      mat,
      parent,
    );
    object.position.copy(start.add(end).multiplyScalar(0.5));
    object.quaternion.setFromUnitVectors(
      new THREE.Vector3(0, 1, 0),
      new THREE.Vector3(...b).sub(new THREE.Vector3(...a)).normalize(),
    );
    return object;
  };

  // Open diorama: left wall has a genuine aperture; the back wall stays solid.
  box(6.2, 0.22, 5.8, 0, -0.16, 0, wall);
  for (let i = 0; i < 15; i++) {
    const board = material(
      i % 3 === 0 ? 0xd4bfa8 : i % 3 === 1 ? 0xcdb8a2 : 0xdac7b4,
    );
    box(0.397, 0.065, 5.6, -2.8 + i * 0.4, -0.025, 0, board);
  }
  box(6.1, 3.6, 0.16, 0, 1.75, -2.8, wall);
  box(0.16, 3.6, 0.6, -3, 1.75, -2.5, wall);
  box(0.16, 3.6, 1.9, -3, 1.75, 1.85, wall);
  box(0.16, 0.86, 3.1, -3, 0.38, -0.65, wall);
  box(0.16, 0.44, 3.1, -3, 3.33, -0.65, wall);
  box(6, 0.12, 0.06, 0, 0.1, -2.69, trim);
  box(0.06, 0.12, 5.6, -2.89, 0.1, 0, trim);
  box(0.35, 0.12, 3.4, -2.92, 0.86, -0.65, trim);

  const skyMat = new THREE.MeshBasicMaterial({
    vertexColors: true,
    side: THREE.DoubleSide,
  });
  materials.add(skyMat);
  const sky = mesh(new THREE.PlaneGeometry(3.15, 2.35), skyMat);
  sky.rotation.y = Math.PI / 2;
  sky.position.set(-3.12, 2.01, -0.65);
  sky.castShadow = false;
  sky.geometry.setAttribute(
    "color",
    new THREE.Float32BufferAttribute(new Float32Array(12), 3),
  );
  const skylineMat = new THREE.MeshBasicMaterial({ color: 0x8d9dac });
  materials.add(skylineMat);
  for (let i = 0; i < 10; i++) {
    const h = 0.18 + ((i * 7) % 5) * 0.065;
    const building = box(
      0.025,
      h,
      0.16,
      -3.09,
      0.95 + h / 2,
      -2.03 + i * 0.3,
      skylineMat,
    );
    building.castShadow = false;
  }

  const glass = new THREE.MeshStandardMaterial({
    color: 0xcbdfea,
    transparent: true,
    opacity: 0.12,
    roughness: 0.2,
    side: THREE.DoubleSide,
    depthWrite: false,
  });
  materials.add(glass);
  const sash = new THREE.Group();
  sash.position.set(-2.98, 0.94, -2.18);
  scene.add(sash);
  for (const z of [0, 1.5]) box(0.09, 2.14, 0.075, 0, 1.07, z, trim, sash);
  for (const y of [0, 2.14]) box(0.09, 0.075, 1.5, 0, y, 0.75, trim, sash);
  box(0.1, 0.035, 1.5, 0, 1.05, 0.75, trim, sash);
  box(0.13, 0.18, 0.035, 0.05, 0.95, 1.4, metal, sash);
  box(0.015, 2.07, 1.42, 0, 1.07, 0.75, glass, sash).castShadow = false;
  for (const z of [-0.65, 0.92]) box(0.11, 2.28, 0.09, -2.98, 2.02, z, trim);
  for (const y of [0.92, 3.13, 1.99])
    box(0.11, 0.07, 1.53, -2.98, y, 0.13, trim);
  box(0.015, 2.15, 1.45, -2.99, 2.02, 0.14, glass).castShadow = false;
  rod([-2.57, 3.35, -2.44], [-2.57, 3.35, 1.16], 0.035, metal);

  const curtains: {
    geometry: THREE.PlaneGeometry;
    side: number;
    sheer: boolean;
  }[] = [];
  const sheerMat = new THREE.MeshStandardMaterial({
    color: 0xf0ece7,
    transparent: true,
    opacity: 0.46,
    roughness: 1,
    side: THREE.DoubleSide,
    depthWrite: false,
  });
  const blackoutMat = material(0xa7a6c1);
  blackoutMat.side = THREE.DoubleSide;
  materials.add(sheerMat);
  for (const sheer of [true, false]) {
    for (const side of [-1, 1]) {
      const geometry = new THREE.PlaneGeometry(1, 1, 16, 20);
      const curtain = mesh(geometry, sheer ? sheerMat : blackoutMat);
      curtain.castShadow = !sheer;
      // Positions are rebuilt below; avoid a stale bounding sphere clipping moving cloth.
      curtain.frustumCulled = false;
      curtains.push({ geometry, side, sheer });
    }
  }

  // Quiet reference composition, with real table/chair occluders.
  box(2.45, 0.035, 1.65, -0.85, 0.025, 0.8, lilac);
  box(1.7, 0.27, 2.8, 1.65, 0.26, -0.75);
  box(1.7, 0.95, 0.12, 1.65, 0.78, -2.08);
  box(1.62, 0.23, 2.65, 1.65, 0.52, -0.7, cloth);
  box(1.66, 0.08, 1.95, 1.65, 0.69, -0.25, lilac);
  for (const x of [1.22, 2.03])
    box(0.65, 0.17, 0.47, x, 0.74, -1.63, cloth).rotation.x = 0.12;
  box(0.74, 0.55, 0.72, 0.3, 0.3, -1.98, trim);
  box(0.56, 0.19, 0.02, 0.3, 0.42, -1.606, wood);
  box(0.13, 0.025, 0.05, 0.3, 0.43, -1.57, metal);

  box(1.28, 2.4, 0.42, -1.03, 1.24, -2.5, wood);
  box(1.14, 2.24, 0.05, -1.03, 1.24, -2.255, dark);
  for (let row = 0; row < 4; row++) {
    const y = 0.32 + row * 0.56;
    box(1.2, 0.055, 0.43, -1.03, y, -2.24, wood);
    for (let j = 0; j < 7; j++) {
      const bookMat = [cloth, lilac, wood, wall][(j + row) % 4];
      const h = 0.28 + ((j * 3 + row) % 3) * 0.055;
      box(0.1, h, 0.22, -1.49 + j * 0.145, y + h / 2 + 0.03, -2.22, bookMat);
    }
  }
  // Reading armchair and deliberately stylized static mannequin.
  const reader = new THREE.Group();
  reader.position.set(-1.63, 0, 0.22);
  reader.rotation.y = 0.15;
  scene.add(reader);
  box(0.9, 0.25, 0.83, 0, 0.46, 0, trim, reader);
  box(0.9, 0.78, 0.17, 0, 0.85, -0.34, cloth, reader);
  for (const x of [-0.43, 0.43]) {
    box(0.17, 0.37, 0.89, x, 0.67, 0, cloth, reader);
    for (const z of [-0.3, 0.3])
      box(0.075, 0.3, 0.075, x, 0.17, z, wood, reader);
  }
  const skin = material(0xe3c1ac);
  const chair = reader;
  const person = new THREE.Group();
  chair.add(person);
  const garmentMaterial = cloth.clone();
  materials.add(garmentMaterial);
  let garmentSource: HTMLImageElement | null = null;
  let garmentTexture: THREE.Texture | null = null;
  const hair = material(0x574d50);
  sphere(0.24, 0, 1.48, -0.08, hair, person).scale.set(1, 1.2, 1);
  sphere(0.19, 0, 1.45, 0.02, skin, person).scale.set(0.92, 1.13, 0.85);
  const fringe = sphere(0.22, 0, 1.59, -0.02, hair, person);
  fringe.scale.set(1, 0.53, 0.94);
  for (const x of [-0.19, 0.19])
    sphere(0.11, x, 1.36, -0.05, hair, person).scale.set(0.6, 2.3, 1);
  const torso = sphere(0.26, 0, 1.05, -0.03, garmentMaterial, person);
  torso.scale.set(0.95, 1.23, 0.73);
  rod([-0.2, 1.15, 0], [-0.24, 0.95, 0.3], 0.077, garmentMaterial, person);
  rod([0.2, 1.15, 0], [0.24, 0.95, 0.3], 0.077, garmentMaterial, person);
  for (const x of [-0.23, 0.23]) sphere(0.068, x, 1.02, 0.39, skin, person);
  const skirt = mesh(new THREE.ConeGeometry(0.35, 0.68, 16), dark, person);
  skirt.position.set(0, 0.58, 0.25);
  skirt.scale.set(1, 1, 1.3);
  for (const x of [-0.14, 0.14]) {
    rod([x, 0.44, 0.45], [x, 0.14, 0.55], 0.065, skin, person);
    sphere(0.1, x, 0.1, 0.64, trim, person).scale.set(0.8, 0.65, 1.5);
  }
  for (const side of [-1, 1]) {
    const book = box(0.23, 0.035, 0.32, side * 0.12, 1.025, 0.34, trim, person);
    book.rotation.set(-0.4, 0, side * 0.17);
  }
  box(2.15, 0.11, 0.87, -0.05, 0.94, 1.83);
  for (const x of [-0.97, 0.87])
    for (const z of [1.51, 2.15]) box(0.075, 0.87, 0.075, x, 0.45, z);
  box(0.7, 0.1, 0.65, 0.25, 0.46, 2.4, trim);
  box(0.7, 0.64, 0.08, 0.25, 0.82, 2.65, lilac);
  for (const x of [-0.03, 0.53])
    for (const z of [2.15, 2.63]) box(0.06, 0.44, 0.06, x, 0.22, z, wood);
  box(0.52, 0.055, 0.34, 0.14, 1.03, 1.77, dark);
  box(0.47, 0.025, 0.31, 0.14, 1.07, 1.77, trim);
  const mug = mesh(new THREE.CylinderGeometry(0.085, 0.075, 0.15, 16), cloth);
  mug.position.set(0.58, 1.07, 1.7);

  function plant(x: number, z: number, y = 0) {
    const pot = mesh(new THREE.CylinderGeometry(0.19, 0.14, 0.3, 14), trim);
    pot.position.set(x, y + 0.17, z);
    for (let i = 0; i < 5; i++) {
      const angle = (i / 5) * Math.PI * 2;
      const end = [
        x + Math.sin(angle) * 0.24,
        y + 0.55 + (i % 2) * 0.17,
        z + Math.cos(angle) * 0.24,
      ];
      rod([x, y + 0.25, z], end, 0.018, leaf);
      const sprig = sphere(0.15, ...(end as [number, number, number]), leaf);
      sprig.scale.set(0.7, 1.3, 0.6);
      sprig.rotation.z = angle;
    }
  }
  plant(-2.45, 1.97);
  plant(-1.18, -2.46, 2.47);
  box(0.84, 1.03, 0.04, 1.56, 2.36, -2.68, wood);
  box(0.7, 0.89, 0.045, 1.56, 2.36, -2.65, trim);
  for (let i = 0; i < 4; i++) {
    const petal = sphere(
      0.1,
      1.56 + (i % 2 ? 0.1 : -0.1),
      2.08 + i * 0.17,
      -2.61,
      leaf,
    );
    petal.scale.set(1, 0.5, 0.1);
    petal.rotation.z = i % 2 ? 0.5 : -0.5;
  }

  const lamps: { light: THREE.SpotLight; bulb: THREE.MeshStandardMaterial }[] =
    [];
  function lamp(
    x: number,
    y: number,
    z: number,
    target: number[],
    power: number,
    reach: number,
  ) {
    const base = mesh(new THREE.CylinderGeometry(0.14, 0.18, 0.045, 16), metal);
    base.position.set(x, y + 0.025, z);
    rod([x, y, z], [x, y + 0.43, z], 0.025, metal);
    const bulb = material(0xffedd4);
    bulb.emissive.set(0xffd5a0);
    const shade = mesh(
      new THREE.CylinderGeometry(0.12, 0.25, 0.23, 20, 1, true),
      bulb,
    );
    shade.position.set(x, y + 0.46, z);
    bulb.side = THREE.DoubleSide;
    shade.castShadow = false;
    const light = new THREE.SpotLight(
      0xffd1a0,
      power,
      reach,
      Math.PI * 0.39,
      0.72,
      2,
    );
    light.position.set(x, y + 0.38, z);
    light.target.position.set(...(target as [number, number, number]));
    light.castShadow = true;
    light.shadow.bias = -0.0006;
    light.shadow.normalBias = 0.025;
    light.shadow.camera.near = 0.08;
    scene.add(light, light.target);
    lamps.push({ light, bulb });
  }
  lamp(-0.71, 1.0, 1.64, [0, 0.8, 1.95], 8, 3.5);
  lamp(0.3, 0.59, -1.98, [1.25, 0.45, -1.1], 10, 3.8);
  const ambient = new THREE.HemisphereLight(0xd6e6ff, 0xaba0ab, 1);
  const sun = new THREE.DirectionalLight(0xffe0bd, 3);
  sun.castShadow = true;
  sun.shadow.camera.left = -5;
  sun.shadow.camera.right = 5;
  sun.shadow.camera.top = 5;
  sun.shadow.camera.bottom = -5;
  sun.shadow.camera.near = 0.5;
  sun.shadow.camera.far = 30;
  sun.shadow.bias = -0.0003;
  sun.shadow.normalBias = 0.025;
  sun.target.position.set(-0.2, 0.3, 0);
  scene.add(ambient, sun, sun.target);
  const warm = new THREE.Color(0xffc894);
  const cool = new THREE.Color(0xe3edff);
  const skyTop = new THREE.Color();
  const skyBottom = new THREE.Color();

  function update(
    hour: number,
    state: RoomValues,
    wind: number,
    elapsed: number,
  ) {
    const day = daylight(hour);
    const glow = Math.pow(1 - day, 3) * Math.min(day * 8, 1);
    skyTop
      .set(0x17243f)
      .lerp(new THREE.Color(0x9cc6e4), day)
      .lerp(new THREE.Color(0xcf8f9b), glow * 0.45);
    skyBottom
      .set(0x3b4262)
      .lerp(new THREE.Color(0xe7e4d9), Math.min(1, day * 2))
      .lerp(new THREE.Color(0xf5ac79), glow);
    const colors = sky.geometry.attributes.color;
    for (let i = 0; i < 4; i++) {
      const color = i < 2 ? skyTop : skyBottom;
      colors.setXYZ(i, color.r, color.g, color.b);
    }
    colors.needsUpdate = true;
    skylineMat.color.copy(skyBottom).multiplyScalar(0.6);
    // Glass transmits at every opening. Sheer softens direct light; blackout also
    // occludes via its actual mesh. This is an art-directed diffuse approximation.
    sun.intensity = day * 3.2 * (0.56 + state.sheer * 0.44);
    sun.color.copy(warm).lerp(cool, day * 0.55);
    sun.position.set(-9, 3.1 + day * 5, -3.8 + ((hour - 6) / 13) * 7.6);
    ambient.intensity = 0.16 + day * 0.95 * (0.24 + state.blackout * 0.76);
    sash.rotation.y = -state.window * Math.PI * 0.36;
    lamps.forEach(({ light, bulb }, index) => {
      const brightness = index === 0 ? state.desk : state.bedside;
      const warmth = index === 0 ? state.deskWarmth : state.bedsideWarmth;
      light.intensity = brightness * (index === 0 ? 9 : 12);
      light.color.copy(cool).lerp(warm, warmth);
      bulb.emissive.copy(light.color);
      bulb.emissiveIntensity = brightness * 1.5;
    });
    for (const curtain of curtains) {
      const openness = curtain.sheer ? state.sheer : state.blackout;
      const width = 0.16 + (1 - openness) * 1.49;
      const positions = curtain.geometry.attributes.position;
      for (let i = 0; i < positions.count; i++) {
        const u = (i % 17) / 16;
        const v = Math.floor(i / 17) / 20; // 0 at the fixed rail
        const z = curtain.side < 0 ? -2.28 + u * width : 0.98 - u * width;
        const fold = Math.sin(u * Math.PI * 12) * 0.035;
        const sway =
          Math.sin(elapsed * 1.7 + u * 3 + curtain.side * 1.3) *
          wind *
          v *
          v *
          (curtain.sheer ? 0.065 : 0.026);
        positions.setXYZ(
          i,
          (curtain.sheer ? -2.77 : -2.57) + fold + sway,
          3.3 - v * 2.89,
          z,
        );
      }
      positions.needsUpdate = true;
      curtain.geometry.computeVertexNormals();
    }
  }
  function quality(simple: boolean) {
    [sun, ...lamps.map(({ light }) => light)].forEach((light, index) => {
      light.shadow.map?.dispose();
      light.shadow.map = null;
      light.shadow.mapSize.setScalar(
        index === 0 ? (simple ? 512 : 1024) : simple ? 256 : 512,
      );
    });
  }
  function appearance(input: PreviewInput) {
    const pose = input.pose ?? "reading";
    person.visible = pose !== "away";
    person.rotation.z = pose === "resting" ? -Math.PI / 2 : 0;
    person.position.set(
      pose === "resting" ? 2.5 : pose === "idle" ? 1.5 : 0,
      pose === "resting" ? 0.4 : 0,
      pose === "resting" ? -1.8 : pose === "idle" ? 0.8 : 0,
    );
    if ((input.garment ?? null) !== garmentSource) {
      garmentTexture?.dispose();
      garmentSource = input.garment ?? null;
      garmentTexture = garmentSource ? new THREE.Texture(garmentSource) : null;
      if (garmentTexture) {
        garmentTexture.colorSpace = THREE.SRGBColorSpace;
        garmentTexture.needsUpdate = true;
      }
      garmentMaterial.map = garmentTexture;
      garmentMaterial.needsUpdate = true;
    }
  }
  function dispose() {
    garmentTexture?.dispose();
    for (const geometry of geometries) geometry.dispose();
    for (const mat of materials) mat.dispose();
    for (const light of [sun, ...lamps.map(({ light }) => light)])
      light.dispose();
    scene.clear();
  }
  return { scene, update, quality, appearance, dispose };
}
