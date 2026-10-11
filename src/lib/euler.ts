/** Rotation matching the backend's geometry.prepare: static-axis XYZ Euler angles in degrees
 *  (trimesh euler_matrix(..., 'sxyz')), i.e. R = Rz · Ry · Rx, applied about the origin. */
export type Matrix3 = [[number, number, number], [number, number, number], [number, number, number]];

export function rotation(deg: number[]): Matrix3 {
  const [x, y, z] = deg.map((d) => (d * Math.PI) / 180);
  const cx = Math.cos(x), sx = Math.sin(x), cy = Math.cos(y), sy = Math.sin(y), cz = Math.cos(z), sz = Math.sin(z);
  return [
    [cz * cy, cz * sy * sx - sz * cx, cz * sy * cx + sz * sx],
    [sz * cy, sz * sy * sx + cz * cx, sz * sy * cx - cz * sx],
    [-sy, cy * sx, cy * cx],
  ];
}

/** 4×4 transform translate · rotate · scale, column-major (vtk.js setUserMatrix / gl-matrix). */
export function transformMatrix(rotationDeg: number[], scale = 1, translation: number[] = [0, 0, 0]): number[] {
  const r = rotation(rotationDeg);
  const m = new Array(16).fill(0);
  for (let row = 0; row < 3; row++) for (let col = 0; col < 3; col++) m[col * 4 + row] = r[row][col] * scale;
  m[12] = translation[0]; m[13] = translation[1]; m[14] = translation[2]; m[15] = 1;
  return m;
}

export function apply(matrix: number[], point: number[]): number[] {
  return [0, 1, 2].map((row) => matrix[row] * point[0] + matrix[4 + row] * point[1] + matrix[8 + row] * point[2] + matrix[12 + row]);
}

/** Axis-aligned bounds of a transformed box. */
export function transformBounds(bounds: number[][], matrix: number[]): number[][] {
  const corners = [0, 1, 2, 3, 4, 5, 6, 7].map((i) => apply(matrix, [bounds[i & 1][0], bounds[(i >> 1) & 1][1], bounds[(i >> 2) & 1][2]]));
  return [0, 1].map((side) => [0, 1, 2].map((axis) => (side ? Math.max : Math.min)(...corners.map((c) => c[axis]))));
}
