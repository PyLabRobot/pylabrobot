// Where the viewer's own code stops and three's begins.
//
// three and its addons are loaded through the browser's import map, from the minified bundle in
// vendor/. That bundle carries no type definitions, so this file says the modules exist and leaves
// their surface as `any`. Our code is checked; three's is not.
//
// This is a deliberate boundary, not an oversight. Installing @types/three would type the other
// side of it, and is the natural first step of a TypeScript conversion.

declare module "three" {
  const THREE: any;
  export = THREE;
}

declare module "three/addons/OrbitControls.js" {
  export const OrbitControls: any;
}
declare module "three/addons/ViewHelper.js" {
  export const ViewHelper: any;
}
declare module "three/addons/RoomEnvironment.js" {
  export const RoomEnvironment: any;
}
declare module "three/addons/lines/LineSegments2.js" {
  export const LineSegments2: any;
}
declare module "three/addons/GLTFLoader.js" {
  export const GLTFLoader: any;
}
declare module "three/addons/DRACOLoader.js" {
  export const DRACOLoader: any;
}
declare module "three/addons/lines/LineSegmentsGeometry.js" {
  export const LineSegmentsGeometry: any;
}

// gif.js attaches itself to the window from a plain script tag.
declare const GIF: any;

interface Window {
  WS_PORT: string;
  WS_URL: string;
  plrCapability?: { webgl2: boolean; renderer: string | null; software: boolean };
  __plrBooted?: boolean;
}
