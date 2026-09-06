"""Immutable triangulated geometry revisions and conservative preparation."""
import hashlib
import io
import json
import math
from pathlib import Path
import numpy as np
import trimesh
from . import config, db
from .models import Prepare, Primitive

UNITS = {"m": 1.0, "mm": 0.001, "cm": 0.01, "in": 0.0254}

def revision_path(identifier):
    if len(identifier) != 32 or any(c not in "0123456789abcdef" for c in identifier):
        raise ValueError("Invalid geometry identifier")
    return config.DATA / "geometry" / identifier

def load(identifier):
    with np.load(revision_path(identifier) / "mesh.npz", allow_pickle=False) as a:
        return trimesh.Trimesh(a["vertices"], a["faces"], process=False), a["groups"].copy()

def connected_groups(mesh, angle=35):
    parent = np.arange(len(mesh.faces))
    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    if len(mesh.face_adjacency):
        for (a, b), theta in zip(mesh.face_adjacency, mesh.face_adjacency_angles):
            if theta < math.radians(angle):
                parent[root(int(a))] = root(int(b))
    roots = np.array([root(i) for i in range(len(parent))])
    _, labels = np.unique(roots, return_inverse=True)
    return labels

def boundary_loops(mesh):
    edges = mesh.edges_sorted
    unique, counts = np.unique(edges, axis=0, return_counts=True)
    opened = unique[counts == 1]
    neighbors = {}
    for a,b in opened:
        neighbors.setdefault(int(a), []).append(int(b))
        neighbors.setdefault(int(b), []).append(int(a))
    loops, seen = [], set()
    for start in neighbors:
        if start in seen or len(neighbors[start]) != 2:
            continue
        chain, cur, prev = [], start, None
        while cur not in seen and len(neighbors.get(cur, [])) == 2:
            seen.add(cur)
            chain.append(cur)
            nxt = [v for v in neighbors[cur] if v != prev][0]
            prev, cur = cur, nxt
        if cur == start and len(chain) >= 3:
            points = mesh.vertices[chain]
            _, _, vt = np.linalg.svd(points - points.mean(axis=0))
            error = float(np.max(np.abs((points-points.mean(axis=0)) @ vt[-1])))
            loops.append({"id": len(loops), "vertices": chain, "center": points.mean(axis=0).tolist(),
                          "normal": vt[-1].tolist(), "planar": error < max(mesh.scale, 1e-12)*1e-5})
    return loops, int(len(opened)), int(np.sum(counts > 2))

def diagnose(mesh):
    loops, open_edges, nonmanifold = boundary_loops(mesh)
    duplicates = len(mesh.faces) - len(np.unique(np.sort(mesh.faces, axis=1), axis=0))
    return {"watertight": bool(mesh.is_watertight), "winding_consistent": bool(mesh.is_winding_consistent),
            "open_edges": open_edges, "nonmanifold_edges": nonmanifold, "duplicate_faces": duplicates,
            "degenerate_faces": int(np.sum(mesh.area_faces < max(mesh.scale, 1e-12)**2*1e-14)),
            "small_faces": int(np.sum(mesh.area_faces < max(mesh.scale, 1e-12)**2*1e-8)),
            "components": int(len(np.unique(connected_groups(mesh, 179.999)))),
            "self_intersections": "not_evaluated", "open_loops": loops,
            "volume_m3": abs(float(mesh.volume)) if mesh.is_watertight else None}

def save(mesh, groups, names, filename, parent=None, operations=None, source=None, confirmed=False, extra=None):
    if not len(mesh.faces) or not np.isfinite(mesh.vertices).all():
        raise ValueError("The geometry contains no usable triangles or non-finite coordinates")
    if len(mesh.faces) > 2_000_000:
        raise ValueError("Geometry exceeds the two-million-triangle import limit; simplify or tessellate more coarsely")
    identifier = db.uid()
    folder = revision_path(identifier)
    folder.mkdir(parents=True)
    groups = np.asarray(groups, dtype=np.int32)
    np.savez_compressed(folder / "mesh.npz", vertices=mesh.vertices, faces=mesh.faces, groups=groups)
    mesh.export(folder / "preview.stl")
    if source is not None:
        (folder / ("source" + Path(filename).suffix.lower())).write_bytes(source)
    digest = hashlib.sha256(mesh.vertices.tobytes()+mesh.faces.tobytes()+groups.tobytes()).hexdigest()
    meta = {"id": identifier, "parent_id": parent, "filename": filename, "sha256": digest,
            "source_sha256": hashlib.sha256(source).hexdigest() if source is not None else (db.geometry(parent).get("source_sha256") if parent else None),
            "units": "m", "units_confirmed": confirmed, "bounds": mesh.bounds.tolist(),
            "dimensions": mesh.extents.tolist(), "triangles": len(mesh.faces), "vertices": len(mesh.vertices),
            "patches": [{"name": names[int(g)], "group": int(g), "triangles": int(np.sum(groups == g))} for g in np.unique(groups)],
            "operations": operations or [], "diagnostics": diagnose(mesh), **(extra or {})}
    (folder / "metadata.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return db.save_geometry(meta)

def import_stl(filename, content, units):
    try:
        mesh = trimesh.load(io.BytesIO(content), file_type="stl", force="mesh", process=True)
    except Exception as e:
        raise ValueError("Cannot read STL: " + str(e)) from e
    mesh.apply_scale(UNITS[units])
    groups = connected_groups(mesh)
    return save(mesh, groups, {int(g): f"surface_{g}" for g in np.unique(groups)}, filename,
                operations=[{"import_units": units}], source=content, confirmed=True)

def import_step(filename, content):
    try:
        from OCP.STEPCAFControl import STEPCAFControl_Reader
        from OCP.TDocStd import TDocStd_Document
        from OCP.TCollection import TCollection_ExtendedString
        from OCP.XCAFDoc import XCAFDoc_DocumentTool
        from OCP.TDF import TDF_LabelSequence, TDF_Label
        from OCP.TopExp import TopExp_Explorer
        from OCP.TopAbs import TopAbs_FACE, TopAbs_REVERSED
        from OCP.TopoDS import TopoDS
        from OCP.TopLoc import TopLoc_Location
        from OCP.BRep import BRep_Tool
        from OCP.BRepMesh import BRepMesh_IncrementalMesh
        from OCP.BRepCheck import BRepCheck_Analyzer
        from OCP.IFSelect import IFSelect_RetDone
        from OCP.Interface import Interface_Static
        from OCP.TDataStd import TDataStd_Name
    except ImportError as e:
        raise ValueError("STEP import requires the CAD extra: pip install '.[cad]'") from e
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "source.step"
        path.write_bytes(content)
        reader = STEPCAFControl_Reader()
        # OCCT exchange target is explicitly millimetres; normalize to metres below.
        Interface_Static.SetCVal_s("xstep.cascade.unit", "MM")
        doc = TDocStd_Document(TCollection_ExtendedString("GustSim"))
        if reader.ReadFile(str(path)) != IFSelect_RetDone or not reader.Transfer(doc):
            raise ValueError("Open CASCADE could not transfer this STEP file")
        tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
        labels = TDF_LabelSequence()
        tool.GetFreeShapes(labels)
        vertices, faces, groups, names, parts, validity = [], [], [], {}, [], []
        component_meshes, component_groups = [], []
        group = 0

        def label_name(label, fallback):
            name_attr = TDataStd_Name()
            return name_attr.Get().ToExtString() if label.FindAttribute(TDataStd_Name.GetID_s(), name_attr) else fallback

        def visit(label, parent_id, path, placement, depth=0):
            nonlocal group
            if depth > 64:
                raise ValueError('STEP assembly nesting exceeds 64 levels')
            definition = label
            local = tool.GetLocation_s(label)
            if tool.IsReference_s(label):
                definition = TDF_Label()
                if not tool.GetReferredShape_s(label, definition):
                    raise ValueError('STEP component reference could not be resolved')
            world = placement.Multiplied(local)
            transform = world.Transformation()
            instance_id = 'component_' + hashlib.sha256('.'.join(map(str,path)).encode()).hexdigest()[:16]
            part_name = label_name(label, label_name(definition, instance_id))
            node = {'id': instance_id, 'parent_id': parent_id, 'name': part_name, 'patches': [], 'assembly_path':path,
                    'placement': [[transform.Value(i,j) for j in range(1,5)] for i in range(1,4)]}
            # Translation in metadata follows the same SI convention as vertices.
            for row in node['placement']: row[3] /= 1000
            parts.append(node)
            children = TDF_LabelSequence()
            if tool.IsAssembly_s(definition) and tool.GetComponents_s(definition, children):
                for child in range(1, children.Length()+1):
                    visit(children.Value(child), instance_id, path+[child], world, depth+1)
                node['patches'] = [p for n in parts if n['parent_id'] == instance_id for p in n['patches']]
                return
            shape = tool.GetShape_s(definition).Located(TopLoc_Location())
            validity.append(bool(BRepCheck_Analyzer(shape).IsValid()))
            BRepMesh_IncrementalMesh(shape, 0.25, False, 0.35, True).Perform()
            explorer = TopExp_Explorer(shape, TopAbs_FACE)
            vertices, faces, groups = [], [], []
            part_patches = []
            while explorer.More():
                face = TopoDS.Face_s(explorer.Current())
                loc = TopLoc_Location()
                tri = BRep_Tool.Triangulation_s(face, loc)
                if tri is not None:
                    offset = len(vertices)
                    for n in range(1, tri.NbNodes()+1):
                        p = tri.Node(n).Transformed(world.Multiplied(loc).Transformation())
                        vertices.append([p.X()/1000, p.Y()/1000, p.Z()/1000])
                    for n in range(1, tri.NbTriangles()+1):
                        ids = list(tri.Triangle(n).Get())
                        if face.Orientation() == TopAbs_REVERSED:
                            ids.reverse()
                        faces.append([offset+j-1 for j in ids])
                        groups.append(group)
                    names[group] = f"{instance_id}_face_{group}"
                    part_patches.append(names[group])
                    group += 1
                explorer.Next()
            node['patches'] = part_patches
            if faces:
                part_mesh = trimesh.Trimesh(vertices, faces, process=False)
                part_mesh.merge_vertices()
                component_meshes.append(part_mesh)
                component_groups.extend(groups)
        for i in range(1, labels.Length()+1):
            visit(labels.Value(i), None, [i], TopLoc_Location())
        if not component_meshes: raise ValueError('STEP contains no triangulatable component bodies')
        mesh = trimesh.util.concatenate(component_meshes)
        groups = component_groups
        return save(mesh, groups, names, filename, source=content, confirmed=True,
                    operations=[{"import": "STEP", "target_units": "m"}],
                    extra={"parts": parts, "cad_valid": all(validity), 'component_schema_version': 1})

def cap(mesh, indices):
    """Triangulate a planar opening with VTK, including concave polygons."""
    import vtk
    from vtk.util.numpy_support import vtk_to_numpy
    pts = vtk.vtkPoints()
    for p in mesh.vertices[indices]:
        pts.InsertNextPoint(*p)
    lines = vtk.vtkCellArray()
    for i in range(len(indices)):
        lines.InsertNextCell(2)
        lines.InsertCellPoint(i)
        lines.InsertCellPoint((i+1)%len(indices))
    poly = vtk.vtkPolyData()
    poly.SetPoints(pts)
    poly.SetLines(lines)
    triangulator = vtk.vtkContourTriangulator()
    triangulator.SetInputData(poly)
    triangulator.Update()
    cells = vtk_to_numpy(triangulator.GetOutput().GetPolys().GetData()).reshape(-1,4)[:,1:]
    if not len(cells):
        raise ValueError("Opening could not be triangulated")
    return np.asarray(indices)[cells]

def prepare(identifier, request: Prepare):
    meta = db.geometry(identifier)
    mesh, groups = load(identifier)
    names = {p["group"]: p["name"] for p in meta["patches"]}
    if request.keep_patches:
        if not set(request.keep_patches) <= set(names.values()):
            raise ValueError("Unknown selected wall surface")
        keep = np.isin(groups, [g for g,n in names.items() if n in request.keep_patches])
        mesh.update_faces(keep)
        groups = groups[keep]
        mesh.remove_unreferenced_vertices()
    if request.repair:
        # Never weld vertices across STEP instance boundaries, including touching bodies.
        children = {p.get('parent_id') for p in meta.get('parts', [])}
        leaves = [p for p in meta.get('parts', []) if p.get('id') not in children]
        partitions = []
        assigned = np.zeros(len(groups), dtype=bool)
        for part in leaves:
            mask = np.isin(groups, [g for g,n in names.items() if n in part['patches']]) & ~assigned
            if mask.any(): partitions.append(mask); assigned |= mask
        if (~assigned).any(): partitions.append(~assigned)
        repaired, mapped = [], []
        for mask in partitions:
            piece = mesh.submesh([np.flatnonzero(mask)], append=True, repair=False)
            labels = groups[mask]
            piece.merge_vertices()
            keep = piece.unique_faces() & piece.nondegenerate_faces()
            piece.update_faces(keep); piece.remove_unreferenced_vertices()
            trimesh.repair.fix_normals(piece, multibody=True)
            repaired.append(piece); mapped.extend(labels[keep])
        mesh = trimesh.util.concatenate(repaired)
        groups = np.asarray(mapped, dtype=np.int32)
    if request.cap_loops:
        loops, _, _ = boundary_loops(mesh)
        for index in sorted(set(request.cap_loops)):
            if index < 0 or index >= len(loops) or not loops[index]["planar"]:
                raise ValueError("Only detected planar openings can be capped")
            faces = cap(mesh, loops[index]["vertices"])
            group = max(names)+1
            names[group] = f"opening_{index}"
            mesh.faces = np.concatenate([mesh.faces, faces])
            groups = np.concatenate([groups, np.full(len(faces),group)])
        trimesh.repair.fix_normals(mesh, multibody=True)
    if request.merge_patches:
        if not set(request.merge_patches) <= set(names.values()):
            raise ValueError("Unknown patch in merge selection")
        if request.merged_name in set(names.values()) - set(request.merge_patches):
            raise ValueError("Merged patch name is already in use")
        parents={p.get('parent_id') for p in meta.get('parts',[])}
        owners=[p for p in meta.get('parts',[]) if p.get('id') not in parents and set(p['patches']) & set(request.merge_patches)]
        if len(owners)>1:raise ValueError('Merge surfaces within one component; component instances must remain separate')
        chosen = [g for g,n in names.items() if n in request.merge_patches]
        group = min(chosen)
        groups[np.isin(groups, chosen)] = group
        names[group] = request.merged_name
    mesh.apply_scale(UNITS[request.units] * request.scale)
    transform = trimesh.transformations.euler_matrix(*np.radians(request.rotation_deg), axes="sxyz")
    mesh.apply_transform(transform)
    mesh.apply_translation(request.translation)
    present = {names[int(g)] for g in np.unique(groups)}
    mapping = {p['name']: ([request.merged_name] if p['name'] in request.merge_patches else [p['name']] if p['name'] in present else []) for p in meta['patches']}
    parts = [{**p, 'patches': list(dict.fromkeys(q for n in p['patches'] for q in mapping.get(n, [])))} for p in meta.get('parts', [])]
    covered = {n for p in parts for n in p['patches']}
    if parts and present - covered:
        parts.append({'id': 'prepared_'+db.uid()[:16], 'parent_id': None, 'name': 'Prepared openings', 'patches': sorted(present-covered)})
    affine = transform.copy();affine[:3,:3] *= UNITS[request.units]*request.scale;affine[:3,3] += request.translation
    for part in parts:
        if 'placement' in part:
            old = np.eye(4);old[:3,:] = part['placement']
            part['placement'] = (affine @ old)[:3,:].tolist()
    extra = {'parts': parts, 'patch_mapping': mapping, 'source_revision': identifier,
             'preparation_before': meta['diagnostics'], 'cad_valid': meta.get('cad_valid'),
             'component_schema_version': meta.get('component_schema_version', 1)}
    return save(mesh, groups, names, meta["filename"], parent=identifier,
                confirmed=True, operations=meta["operations"]+[request.model_dump()], extra=extra)

def regroup(identifier, angle):
    meta = db.geometry(identifier)
    mesh, old_groups = load(identifier)
    groups = connected_groups(mesh, angle)
    # Preserve an explicit many-to-many mapping; callers must reassign ambiguous patches.
    names={int(g):f'surface_{g}' for g in np.unique(groups)}
    mapping={p['name']:[names[int(g)] for g in np.unique(groups[old_groups==p['group']])] for p in meta['patches']}
    parts=[{**p,'patches':list(dict.fromkeys(q for n in p['patches'] for q in mapping.get(n,[])))} for p in meta.get('parts',[])]
    return save(mesh, groups, names, meta["filename"], parent=identifier, confirmed=True,
                operations=meta["operations"]+[{"regroup_angle": angle}],extra={'parts':parts,'patch_mapping':mapping,'cad_valid':meta.get('cad_valid')})

def primitive_mesh(p: Primitive):
    if p.shape == "box":
        mesh = trimesh.creation.box(extents=p.dimensions)
    elif p.shape == "sphere":
        mesh = trimesh.creation.icosphere(subdivisions=3, radius=p.radius)
    elif p.shape == "tube":
        mesh = trimesh.creation.annulus(r_min=p.inner_radius, r_max=p.radius, height=p.length, sections=64)
    else:
        mesh = trimesh.creation.cylinder(radius=p.radius, height=p.length, sections=64)
    if p.shape in ("cylinder", "tube"):
        mesh.apply_transform(trimesh.geometry.align_vectors([0,0,1], np.asarray(p.axis)/np.linalg.norm(p.axis)))
    mesh.apply_translation(p.center)
    return mesh

def add_primitive(p: Primitive, identifier=None):
    if p.role not in ("solid", "enclosure"):
        raise ValueError("Refinement and rotating regions belong in simulation setup, not solid geometry")
    effective = p.model_copy(update={"shape":"cylinder", "radius":p.inner_radius}) if p.shape == "tube" and p.role == "enclosure" else p
    mesh = primitive_mesh(effective)
    groups = connected_groups(mesh)
    names = {int(g): f"{p.name}_{g}" for g in np.unique(groups)}
    added_names=list(names.values())
    extra={}
    if identifier:
        base, base_groups = load(identifier)
        meta = db.geometry(identifier)
        base_parts=meta.get('parts') or [{'id':'original_'+identifier[:16],'parent_id':None,'name':meta['filename'],'patches':[q['name'] for q in meta['patches']]}]
        extra={'parts':base_parts+[{'id':'primitive_'+db.uid()[:16],'parent_id':None,'name':p.name,'patches':added_names}],
               'patch_mapping':{q['name']:[q['name']] for q in meta['patches']},'cad_valid':meta.get('cad_valid')}
        shift = int(max(base_groups))+1
        names = {**{q["group"]: q["name"] for q in meta["patches"]}, **{g+shift:n for g,n in names.items()}}
        if len(set(names.values())) != len(names):
            raise ValueError("Use a unique primitive name")
        groups = np.concatenate([base_groups, groups+shift])
        mesh = trimesh.util.concatenate([base, mesh])
    return save(mesh, groups, names, p.name+".stl", parent=identifier, confirmed=True,
                operations=(db.geometry(identifier)["operations"] if identifier else [])+[p.model_dump()],extra=extra)

def scene(identifier):
    mesh, groups = load(identifier)
    # This response is geometry only; it never contains synthetic CFD fields.
    return {"points": mesh.vertices.ravel().tolist(), "polys": np.column_stack([np.full(len(mesh.faces),3),mesh.faces]).ravel().tolist(),
            "groups": groups.tolist(), "patches": db.geometry(identifier)["patches"]}

def point_inside(mesh, point):
    import vtk
    from vtk.util.numpy_support import numpy_to_vtk, numpy_to_vtkIdTypeArray
    poly = vtk.vtkPolyData()
    pts = vtk.vtkPoints()
    pts.SetData(numpy_to_vtk(np.asarray(mesh.vertices), deep=True))
    cells = vtk.vtkCellArray()
    cells.SetCells(len(mesh.faces), numpy_to_vtkIdTypeArray(np.column_stack([np.full(len(mesh.faces),3),mesh.faces]).astype(np.int64).ravel(),deep=True))
    poly.SetPoints(pts)
    poly.SetPolys(cells)
    q = vtk.vtkPolyData()
    qp = vtk.vtkPoints()
    qp.InsertNextPoint(*point)
    q.SetPoints(qp)
    select = vtk.vtkSelectEnclosedPoints()
    select.SetInputData(q)
    select.SetSurfaceData(poly)
    select.Update()
    return bool(select.IsInside(0))
