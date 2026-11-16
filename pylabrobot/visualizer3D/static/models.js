// Resources drawn from model files: fetched and parsed once per file, instanced for what stands
// still, cloned for what rides a moving part, with joints the state drives. A model already on
// screen is kept across rebuilds while its resource and file are unchanged.

import * as THREE from "three";
import { DRACOLoader } from "three/addons/DRACOLoader.js";
import { GLTFLoader } from "three/addons/GLTFLoader.js";

import { modelIsDrawn } from "./appearance.js";
import { fitFilterDiscs } from "./boxes.js";
import { DEG, GLAZED_MAX_OPACITY } from "./constants.js";
import {
  clearModels,
  disposeOwned,
  meshRoots,
  modelMeshes,
  own,
  placeInstance,
  remember,
  stateOf,
  travels,
} from "./drawn.js";
import { view } from "./renderer.js";
import { modelOf, world } from "./world.js";

// Geometry a resource declared for itself, drawn in place of its box.
//
// The file is loaded once per model and shared by every instance of it, the same way one geometry
// serves every well of a plate. Loading is asynchronous and the scene is already on screen by the
// time it lands, so each mesh is added when it arrives rather than being waited for: the box shows
// until then, and nothing blocks.

export const gltfLoader = new GLTFLoader();

// Draco-compressed meshes are common in files exported for the web, and cannot be read without a
// decoder. It is fetched only when a compressed mesh actually turns up, so a viewer that never
// loads one pays nothing for it. Only the WebAssembly decoder is vendored; the much larger
// JavaScript fallback is for browsers that predate WebAssembly, which cannot run this viewer anyway.
export const dracoLoader = new DRACOLoader();

// Counted up per rebuild, so a file that lands late is placed only by the scene that asked for it.
let sceneGeneration = 0;

// Every file that has been fetched and parsed, by url. A tree that changes shape sends a whole
// scene, and an instanced mesh cannot be resized - so the meshes are built again, from this,
// without going back to the network.
const parsedByUrl = new Map();

// What identifies a drawn model across rebuilds: the resource it belongs to and the file it was
// drawn from. A scene arrives whole whenever the tree changes shape, and most of what it describes
// is what was already on screen.
function meshKey(index) {
  return `${world.names[index]}\n${modelOf(index).mesh?.url}`;
}

const AXIS_VECTOR = {
  x: new THREE.Vector3(1, 0, 0),
  y: new THREE.Vector3(0, 1, 0),
  z: new THREE.Vector3(0, 0, 1),
};

// Move a resource's mesh to the joint values it publishes.
//
// A revolute joint turns about its declared axis, a prismatic one slides along it. Both are applied
// as a displacement from the rest transform the file was authored in, so a value of zero puts the
// arm back exactly where the file drew it.
export function applyJoints(index) {
  const root = meshRoots.find((r) => r.userData.index === index);
  if (!root) return;
  const published = stateOf.get(index)?.joints;
  if (!published) return;

  for (const [key, joint] of root.userData.joints) {
    const value = published[key];
    if (value === undefined || value === null) continue;
    const axis = AXIS_VECTOR[joint.spec.axis ?? "z"];
    if (!axis) continue;

    if (joint.spec.type === "prismatic") {
      // Published in millimetres; the node lives in the file's own units.
      const travel = value / (root.userData.scale || 1);
      joint.node.position.copy(joint.restPosition).addScaledVector(axis, travel);
    } else {
      const turn = new THREE.Quaternion().setFromAxisAngle(axis, value * DEG);
      joint.node.quaternion.copy(joint.restQuaternion).multiply(turn);
    }
  }
}

// Put a model that is already on screen where the new scene says it is. Its geometry, its
// materials and its joints are the same objects; what changed is which instance it belongs to and
// the transform that places it.
function replaceInScene(root, index) {
  root.userData.index = index;
  root.matrix.copy(world.matrices[index]);
  root.matrixWorldNeedsUpdate = true;
  root.traverse((o) => {
    if (o.isMesh) o.userData.declaredBy = index;
  });
  applyJoints(index);
}

// glTF says metres and Y-up; a resource that means something else says so in its declaration.
const MESH_UNITS = { mm: 1, cm: 10, m: 1000 };

// A copy of the file's scene in our units and Z-up: Y-up is glTF's default, and a Z-up file is
// already in our own convention.
function orient(gltf, scale, up) {
  const scene = gltf.scene.clone(true);
  scene.scale.setScalar(scale);
  if (up === "Y") scene.rotation.x = Math.PI / 2;
  return scene;
}

// What the file said of a material, kept before a plan view writes over its flags: a material
// asked afterwards what it was modelled as would answer with whatever the plan view gave it.
function asModelled(material) {
  return {
    transparent: material.transparent,
    opacity: material.opacity,
    depthWrite: material.depthWrite,
    color: material.color.getHex(),
    glazed: material.transparent && material.opacity <= GLAZED_MAX_OPACITY,
  };
}

/**
 * One model for every resource standing on it, in one instanced mesh per mesh in its file.
 *
 * A cloned model is a draw call apiece: a tip carrier's five racks came to 1,544 of the 1,962 draws
 * a close view cost, for geometry that is the same tip ninety-six times over. Instanced, a model
 * costs one draw however many resources it is drawn for, and the geometry and the materials are
 * the ones the file was parsed into - shared, as the clones shared them.
 *
 * Made for a count of instances and nothing about which ones: `placeInstancedModel` puts in where
 * each stands and which resource it is, so the same meshes serve scene after scene.
 */
function makeInstancedModel(key, count, gltf, scale, up) {
  const carrier = new THREE.Group();
  const scene = orient(gltf, scale, up);
  carrier.add(scene);
  carrier.updateMatrixWorld(true);

  const built = { key, meshes: [] };
  scene.traverse((o) => {
    if (!o.isMesh) return;
    // One material for every instance: what rides a moving part is cloned instead, so that it can
    // be drawn see-through on its own.
    const material = o.material.clone();
    const mesh = new THREE.InstancedMesh(o.geometry, material, count);
    own(built, mesh, material);
    // Where this mesh sits inside the file, with the file's units and its up-axis already in it.
    mesh.userData.at = [o.matrixWorld.clone()];
    mesh.userData.lit = material;
    mesh.userData.asModelled = asModelled(o.material);
    view.add(mesh);
    built.meshes.push(mesh);
  });
  return built;
}

/**
 * Put every instance of a model where the tree has it, registered the way a box's own parts are,
 * so a resource that moves or is switched off takes its geometry with it.
 */
function placeInstancedModel(built, modelIndex, instances) {
  built.modelIndex = modelIndex;
  built.instances = instances;
  for (const mesh of built.meshes) {
    mesh.userData.instances = instances;
    instances.forEach((index, slot) => {
      placeInstance(mesh, slot, world.matrices[index], mesh.userData.at[0]);
      remember(index, mesh, slot, mesh.userData.at);
    });
    mesh.instanceMatrix.needsUpdate = true;
    mesh.boundingSphere = null;
  }
  modelMeshes.push(built);
}

// What identifies an instanced model across rebuilds: the file it is drawn from and the resources
// standing on it, in order. Any other set needs new meshes, built from the parsed file in the same turn.
function instancedKey(modelIndex, instances) {
  const names = instances.map((index) => world.names[index]).join("\n");
  return `${JSON.stringify(world.models[modelIndex].mesh)}\n${names}`;
}

export function buildDeclaredMeshes() {
  // What is on screen already, by the resource and file it was drawn for. A tree that changes
  // shape - a tip picked up, a plate moved - sends a whole scene, and rebuilding the geometry for
  // it would take every model off screen and put the boxes back until the files had been fetched
  // and parsed again. That flash is what this is here to stop.
  sceneGeneration++;
  const onScreen = new Map();
  for (const root of meshRoots) onScreen.set(root.userData.key, root);
  const instanced = new Map();
  for (const built of modelMeshes) instanced.set(built.key, built);
  clearModels();

  // One load per distinct model, however many instances stand on it. A file of several hundred
  // thousand triangles is expensive to fetch and parse, and cloning shares both geometry and
  // materials, so the cost is paid once no matter how many arms are in the facility.
  const byModel = new Map();
  const kept = new Set();
  for (let index = 0; index < world.names.length; index++) {
    const declared = modelOf(index).mesh;
    if (!declared?.url) continue;
    const modelIndex = world.modelOf[index];
    const root = onScreen.get(meshKey(index));
    if (root !== undefined) {
      onScreen.delete(meshKey(index));
      replaceInScene(root, index);
      meshRoots.push(root);
      kept.add(modelIndex);
      continue;
    }
    if (!byModel.has(modelIndex)) byModel.set(modelIndex, []);
    byModel.get(modelIndex).push(index);
  }
  // Whatever is left belongs to a resource this scene does not have, or to one that now declares a
  // different file.
  for (const root of onScreen.values()) {
    view.remove(root);
    disposeOwned(root);
  }
  for (const modelIndex of kept) modelIsDrawn(modelIndex);

  for (const [modelIndex, instances] of byModel) {
    const declared = world.models[modelIndex].mesh;
    const scale = MESH_UNITS[declared.units] ?? 1;
    const up = declared.up ?? "Y";
    const generation = sceneGeneration;

    const place = (gltf) => {
      // The scene may have been rebuilt while this was in flight; that scene issued its own load.
      // Placing this one too would draw the model twice, and the same names do not make it current.
      parsedByUrl.set(declared.url, gltf);
      if (generation !== sceneGeneration) return;

      fitFilterDiscs(modelIndex, gltf.scene, scale, up);

      // What rides something that travels keeps a copy of its own: it is drawn see-through and
      // in a layer of its own while it is being carried, and instances of one model share a
      // material, so a tip on a channel cannot be told from a tip in a rack through one. There
      // are never many of them - a head has eight channels, not ninety-six.
      const riding = instances.filter((index) => travels(index));
      const standing = instances.filter((index) => !travels(index));
      if (standing.length > 0) {
        const key = instancedKey(modelIndex, standing);
        const built =
          instanced.get(key) ?? makeInstancedModel(key, standing.length, gltf, scale, up);
        instanced.delete(key);
        placeInstancedModel(built, modelIndex, standing);
      }

      riding.forEach((index) => {
        const scene = orient(gltf, scale, up);

        const root = new THREE.Group();
        root.add(scene);
        root.matrixAutoUpdate = false;
        root.matrix.copy(world.matrices[index]);
        root.matrixWorldNeedsUpdate = true;
        root.traverse((o) => {
          if (o.isMesh) {
            o.userData.declaredBy = index;
            // Its own material: the view sets opacity per resource, and a copy can be let go.
            o.material = o.material.clone();
            own(root, o.material);
            o.userData.lit = o.material;
            o.userData.asModelled = asModelled(o.material);
          }
        });

        // A rigged file names the parts that move. The declaration says which node answers to
        // which joint, so the viewer drives what it is told and holds no knowledge of any arm's
        // geometry. Each node's rest transform is kept, because a joint value is a displacement
        // from where the file was authored, not an absolute pose.
        const joints = new Map();
        for (const [key, spec] of Object.entries(declared.joints ?? {})) {
          const node = scene.getObjectByName(spec.node);
          if (!node) {
            console.warn(
              `${world.names[index]} declares joint ${key} on node ${spec.node}, which the file does not have`,
            );
            continue;
          }
          joints.set(key, {
            node,
            spec,
            restPosition: node.position.clone(),
            restQuaternion: node.quaternion.clone(),
          });
        }
        root.userData.joints = joints;
        root.userData.scale = scale;
        root.userData.index = index;
        root.userData.key = meshKey(index);

        view.add(root);
        meshRoots.push(root);
        applyJoints(index);
      });

      modelIsDrawn(modelIndex);
    };

    const parsed = parsedByUrl.get(declared.url);
    if (parsed !== undefined) {
      place(parsed);
      continue;
    }
    gltfLoader.load(declared.url, place, undefined, (error) =>
      console.warn(`could not load the mesh declared by ${world.names[instances[0]]}`, error),
    );
  }
  // Whatever is left was drawn for a set of resources this scene does not have. Let go here, so a
  // file that lands later finds nothing to reuse and builds its meshes afresh.
  for (const built of instanced.values()) {
    for (const mesh of built.meshes) view.remove(mesh);
    disposeOwned(built);
  }
  instanced.clear();
}
