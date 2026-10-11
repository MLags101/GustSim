"""OpenCFD v2606 case compiler. All emitted values come from typed models."""
import hashlib
import json
import importlib.metadata
import platform
import math
import shutil
from pathlib import Path
import numpy as np
from . import config, db, geometry
from . import foam_functions, foam_elements
from .models import SimulationSpec
from .validation import DOMAIN_PATCHES, validate

def vector(v):return "("+" ".join(f"{x:.12g}" for x in v)+")"
def header(name,cls="dictionary"):
    return f"/* GustSim · OpenCFD OpenFOAM v{config.FOAM_VERSION} */\nFoamFile\n{{\n version 2.0;\n format ascii;\n class {cls};\n object {name};\n}}\n"

def write(folder,name,text,cls="dictionary"):
    target=folder/name
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(header(target.name,cls)+text+"\n",encoding="utf-8",newline="\n")

def boundary_field(spec,b,field):
    velocity=vector(b.velocity if b.velocity is not None else spec.flow.velocity())
    ambient=b.kind=='freestream' and sum(v*v for v in (b.velocity if b.velocity is not None else spec.flow.velocity())) < 1e-24
    p=b.pressure_pa/spec.fluid.density
    k=max(1.5*(max(spec.flow.speed,0.01)*spec.flow.intensity)**2,1e-10)
    omega=max(math.sqrt(k)/(0.09**0.25*spec.flow.length_scale),1e-10)
    wall=b.kind in {"wall","moving_wall","rotating_wall"}
    if b.kind=="symmetry":return "type symmetryPlane;"
    if field=="U":
        if ambient:return 'type pressureInletOutletVelocity; value uniform (0 0 0);'
        if b.kind=="velocity_inlet":return f"type fixedValue; value uniform {velocity};"
        if b.kind=="flow_inlet":return f"type flowRateInletVelocity; volumetricFlowRate constant {b.flow_rate}; value uniform {velocity};"
        if b.kind in ("pressure_inlet","pressure_outlet"):return "type pressureInletOutletVelocity; value uniform (0 0 0);"
        if b.kind=="flow_outlet":return f"type flowRateOutletVelocity; volumetricFlowRate constant {b.flow_rate}; value uniform (0 0 0);"
        if b.kind=="wall":return "type fixedValue; value uniform (0 0 0);"
        if b.kind=="moving_wall":return f"type fixedValue; value uniform {velocity};"
        if b.kind=="rotating_wall":
            axis=np.asarray(b.axis)/np.linalg.norm(b.axis)
            return f"type rotatingWallVelocity; origin {vector(b.origin)}; axis {vector(axis)}; omega constant {b.rpm*2*math.pi/60}; value uniform (0 0 0);"
        if b.kind=="slip":return "type slip;"
        return f"type freestream; freestreamValue uniform {velocity}; value uniform {velocity};"
    if field=="p":
        # v2606 freestreamPressure normalizes the freestream velocity and is
        # undefined at rest. A quiescent reservoir is an open total-pressure BC.
        if ambient:return f'type totalPressure; p0 uniform {p}; value uniform {p};'
        if b.kind=="pressure_outlet":return f"type fixedValue; value uniform {p};"
        if b.kind=="pressure_inlet":return f"type totalPressure; p0 uniform {p}; value uniform {p};"
        if b.kind=="freestream":return f"type freestreamPressure; freestreamValue uniform {p}; value uniform {p};"
        return "type zeroGradient;"
    val={"k":k,"omega":omega,"nut":0}[field]
    if wall:
        if spec.mesh.wall_treatment=="resolved" and field in ("k","nut"):
            return f"type fixedValue; value uniform {1e-10 if field=='k' else 0};"
        return f"type {dict(k='kqRWallFunction',omega='omegaWallFunction',nut='nutkWallFunction')[field]}; value uniform {val};"
    if field=="nut":return "type calculated; value uniform 0;"
    if b.kind in {"velocity_inlet","flow_inlet","pressure_inlet","freestream"}:
        return f"type inletOutlet; inletValue uniform {val}; value uniform {val};" if b.kind in {"pressure_inlet","freestream"} else f"type fixedValue; value uniform {val};"
    if b.kind in ("pressure_outlet","flow_outlet"):return f"type inletOutlet; inletValue uniform {val}; value uniform {val};"
    return "type zeroGradient;"

def end_time(spec):
    if spec.convergence.enabled:
        return min(spec.solver.iterations,spec.convergence.cap)
    return spec.solver.iterations


def write_schemes(spec,folder,robust_start=False):
    """`robust_start` is the first stage of the robust numerics profile: first-order
    momentum for a stable start before the second-order solve continues from it."""
    momentum="bounded Gauss upwind" if robust_start else "bounded Gauss linearUpwind grad(U)"
    turbulent_div="div(phi,k) bounded Gauss upwind; div(phi,omega) bounded Gauss upwind;" if spec.flow.turbulence!="laminar" else ""
    write(folder,"system/fvSchemes",f"ddtSchemes {{ default steadyState; }}\ngradSchemes {{ default cellLimited Gauss linear 1; }}\ndivSchemes {{ default none; div(phi,U) {momentum}; {turbulent_div} div((nuEff*dev2(T(grad(U))))) Gauss linear; }}\nlaplacianSchemes {{ default Gauss linear limited 0.5; }}\ninterpolationSchemes {{ default linear; }}\nsnGradSchemes {{ default limited 0.5; }}\nwallDist {{ method meshWave; }}")


def write_solution(spec,folder):
    solvers=f'''solvers {{ p {{ solver GAMG; tolerance 1e-8; relTol 0.01; smoother GaussSeidel; }} "(U|k|omega)" {{ solver smoothSolver; smoother symGaussSeidel; tolerance 1e-8; relTol 0.1; }}'''
    if spec.schema_version>=2:
        # SIMPLEC (consistent yes) is formulated for an unrelaxed pressure with momentum
        # relaxation near 0.9; v1 cases keep their original factors for reproducibility.
        solvers+=" Phi { solver GAMG; smoother DIC; tolerance 1e-6; relTol 0.01; }"
        relaxation='relaxationFactors { equations { U 0.9; ".*" 0.7; } }'
        potential="\npotentialFlow { nNonOrthogonalCorrectors 3; }"
    else:
        relaxation="relaxationFactors { fields { p 0.3; } equations { U 0.7; k 0.7; omega 0.7; } }"
        potential=""
    write(folder,"system/fvSolution",f'''{solvers} }}
SIMPLE {{ nNonOrthogonalCorrectors 1; consistent yes; residualControl {{ p {spec.solver.residual_target}; U {spec.solver.residual_target}; "(k|omega)" {spec.solver.residual_target}; }} }}
{relaxation}{potential}''')


def write_control(spec,folder,functions,end,start_from="startTime"):
    (folder/"function_objects.json").write_text(json.dumps({"functions":functions}),encoding="utf-8")
    write(folder,"system/controlDict",f'''application simpleFoam;
startFrom {start_from}; startTime 0; stopAt endTime; endTime {end}; deltaT 1;
writeControl timeStep; writeInterval {spec.solver.write_interval}; purgeWrite 0;
writeFormat binary; writePrecision 10; writeCompression off; timeFormat general; timePrecision 10;
runTimeModifiable false;
functions {{ {functions} }}''')


def restart_control(spec,folder,end):
    """Rewrite controlDict to continue from the latest time (robust numerics, second stage)."""
    functions=json.loads((folder/"function_objects.json").read_text(encoding="utf-8"))["functions"]
    write_control(spec,folder,functions,end,start_from="latestTime")


def compile_case(spec:SimulationSpec,folder:Path):
    report=validate(spec)
    if not report["valid"]:raise ValueError("; ".join(report["errors"]))
    folder.mkdir(parents=True,exist_ok=True)
    meta=db.geometry(spec.geometry_id)
    (folder/"simulation.json").write_text(spec.model_dump_json(indent=2),encoding="utf-8")
    (folder/"geometry.json").write_text(json.dumps(meta,indent=2),encoding="utf-8")
    (folder/"preflight.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
    (folder/"gustsim.foam").touch()
    (folder/"constant"/"triSurface").mkdir(parents=True,exist_ok=True)
    mesh,groups=geometry.load(spec.geometry_id)
    # Environment and elements (ground, wheels, openings) change how surfaces are compiled.
    boundaries=foam_elements.effective_boundaries(spec,meta,mesh,groups)
    for patch in meta["patches"]:
        part=mesh.submesh([np.flatnonzero(groups==patch["group"])],append=True)
        part.export(folder/"constant"/"triSurface"/(patch["name"]+".stl"))
    low,high=spec.domain.minimum,spec.domain.maximum
    vertices=[(low[0],low[1],low[2]),(high[0],low[1],low[2]),(high[0],high[1],low[2]),(low[0],high[1],low[2]),
              (low[0],low[1],high[2]),(high[0],low[1],high[2]),(high[0],high[1],high[2]),(low[0],high[1],high[2])]
    extent=np.array(high)-np.array(low)
    counts=np.maximum(4,np.ceil(spec.mesh.base_cells*extent/max(extent))).astype(int)
    faces={"inlet":"(0 4 7 3)","outlet":"(1 2 6 5)","front":"(0 1 5 4)","back":"(3 7 6 2)","ground":"(0 3 2 1)","top":"(4 5 6 7)"}
    bounds={b.patch:b for b in boundaries}
    def patch_type(name):
        b=bounds.get(name)
        if b and b.kind=="symmetry":return "symmetryPlane"
        return "wall" if b and b.kind in ("wall","moving_wall","rotating_wall") else "patch"
    write(folder,"system/blockMeshDict", "scale 1;\nvertices\n(\n"+"\n".join(vector(v) for v in vertices)+"\n);\nblocks\n( hex (0 1 2 3 4 5 6 7) "+vector(counts)+" simpleGrading (1 1 1) );\nedges ();\nboundary\n(\n"+
          "\n".join(f"{name} {{ type {patch_type(name)}; faces ({faces[name]}); }}" for name in DOMAIN_PATCHES)+"\n);\nmergePatchPairs ();" )
    patches=[p["name"] for p in meta["patches"]]
    write(folder,"system/surfaceFeatureExtractDict","\n".join(f'{p}.stl {{ extractionMethod extractFromSurface; extractFromSurfaceCoeffs {{ includedAngle 150; }} writeObj no; }}' for p in patches))
    geo="\n".join(f"{p}.stl {{ type triSurfaceMesh; name {p}; }}" for p in patches)
    refinements=[]
    if spec.mesh.body_level:
        h=max(extent)/spec.mesh.base_cells/2**spec.mesh.body_level
        a=np.asarray(meta['bounds'][0])-2*h;b=np.asarray(meta['bounds'][1])+2*h
        geo+=f"\nbodyResolution {{ type searchableBox; min {vector(a)}; max {vector(b)}; }}"
        refinements.append(f"bodyResolution {{ mode inside; levels ((1e15 {spec.mesh.body_level})); }}")
    # Wake refinement follows the incident flow rather than assuming alpha=beta=0.
    # Only external flow has a wake: inside a duct this box covers the whole fluid
    # volume and inflates the cell count for nothing, with no way to switch it off.
    from .estimate import wake_boxes
    for name,wake_min,wake_max,level in wake_boxes(spec,meta):
        geo+=f"\n{name} {{ type searchableBox; min {vector(wake_min)}; max {vector(wake_max)}; }}"
        refinements.append(f"{name} {{ mode inside; levels ((1e15 {level})); }}")
    for i,region in enumerate(spec.regions):
        name=f"refine_{i}"
        if region.shape=="box":
            a=np.asarray(region.center)-np.asarray(region.dimensions)/2;b=np.asarray(region.center)+np.asarray(region.dimensions)/2
            geo+=f"\n{name} {{ type searchableBox; min {vector(a)}; max {vector(b)}; }}"
        elif region.shape=="sphere":geo+=f"\n{name} {{ type searchableSphere; centre {vector(region.center)}; radius {region.radius}; }}"
        elif region.shape=="cylinder":
            axis=np.asarray(region.axis)/np.linalg.norm(region.axis)
            a=np.asarray(region.center)-axis*region.length/2;b=np.asarray(region.center)+axis*region.length/2
            geo+=f"\n{name} {{ type searchableCylinder; point1 {vector(a)}; point2 {vector(b)}; radius {region.radius}; }}"
        else:raise ValueError("Use a cylinder, box or sphere for volume refinement")
        refinements.append(f"{name} {{ mode inside; levels ((1e15 {spec.mesh.surface_level})); }}")
    source = spec.mesh.adaptive_source
    if source is not None:
        indicator = config.DATA/'runs'/source.run_id/'results'/'amr'/'indicator.stl'
        if not indicator.is_file():
            raise ValueError('The adaptive indicator surface is missing from the solved attempt')
        digest = hashlib.sha256(indicator.read_bytes()).hexdigest()
        if digest != source.sha256:
            raise ValueError('The adaptive indicator no longer matches the recorded checksum')
        shutil.copyfile(indicator, folder/'constant'/'triSurface'/'amrIndicator.stl')
        geo += '\namrIndicator.stl { type triSurfaceMesh; name amrIndicator; }'
        refinements.append(f"amrIndicator {{ mode distance; levels (({source.distance_m:.8g} {source.level})); }}")
    # Rotating zones (the legacy region is the zone named 'rotor') and porous zones.
    zone_geo,zone_refine,actions,mrf=foam_elements.zones(spec,boundaries,spec.mesh.surface_level)
    if spec.time.sliding_mesh:
        # The cell zone stays, so the mesh check can see the rotor. Motion is the sliding mesh, not MRF.
        mrf = []
        from . import foam_ami
        actions += foam_ami.face_actions(spec)
    porous_geo,porous_refine,porous_actions,porous_options,porous_functions=foam_elements.porous(spec,spec.mesh.surface_level)
    for entry in zone_geo+porous_geo:geo+="\n"+entry
    refinements+=zone_refine+porous_refine
    actions+=porous_actions
    if actions:write(folder,"system/topoSetDict","actions\n(\n"+"\n".join(actions)+"\n);")
    if mrf:write(folder,"constant/MRFProperties","\n".join(mrf))
    if porous_options:write(folder,"constant/fvOptions","\n".join(porous_options))
    # The relaxed profile helps snapping and layering; the acceptance gate (meshQualityDict,
    # checkMesh) never changes.
    relaxed=spec.mesh.quality_profile=="relaxed"
    surfaces="\n".join(f"{p} {{ level ({spec.mesh.surface_level} {spec.mesh.surface_level}); patchInfo {{ type {patch_type(p)}; }} }}" for p in patches)
    layer_patches="\n".join(f"{b.patch} {{ nSurfaceLayers {spec.mesh.layers}; }}" for b in boundaries if b.patch in patches and b.kind in {"wall","moving_wall","rotating_wall"})
    features="\n".join(f'{{ file "{p}.eMesh"; level {spec.mesh.feature_level}; }}' for p in patches)
    write(folder,"system/snappyHexMeshDict",f"""
castellatedMesh true;
snap true;
addLayers {'true' if spec.mesh.layers else 'false'};
geometry {{ {geo} }}
castellatedMeshControls
{{
 maxLocalCells {spec.mesh.max_cells}; maxGlobalCells {spec.mesh.max_cells}; minRefinementCells 0;
 maxLoadUnbalance 0.1; nCellsBetweenLevels {spec.mesh.n_cells_between_levels};
 features ({features});
 refinementSurfaces {{ {surfaces} }}
 resolveFeatureAngle 30;
 refinementRegions {{ {' '.join(refinements)} }}
 locationInMesh {vector(spec.domain.fluid_point)};
 allowFreeStandingZoneFaces true;
}}
snapControls {{ nSmoothPatch {5 if relaxed else 3}; tolerance 2; nSolveIter 30; nRelaxIter 5; nFeatureSnapIter 10; implicitFeatureSnap false; explicitFeatureSnap true; multiRegionFeatureSnap true; }}
addLayersControls
{{
 relativeSizes false;
 layers {{ {layer_patches} }}
 expansionRatio {spec.mesh.expansion_ratio}; firstLayerThickness {spec.mesh.first_layer_m}; minThickness {spec.mesh.first_layer_m*0.1};
 nGrow 0; featureAngle {90 if relaxed else 60}; slipFeatureAngle 30; nRelaxIter 3; nSmoothSurfaceNormals 1;
 nSmoothNormals 3; nSmoothThickness 10; maxFaceThicknessRatio 0.5; maxThicknessToMedialRatio 0.3;
 minMedialAxisAngle 90; nBufferCellsNoExtrude 0; nLayerIter 50; nRelaxedIter {30 if relaxed else 20};
}}
meshQualityControls
{{
 maxNonOrtho {70 if relaxed else 65}; maxBoundarySkewness 20; maxInternalSkewness 4; maxConcave 80;
 minVol 1e-13; minTetQuality 1e-15; minArea -1; minTwist 0.02;
 minDeterminant 0.001; minFaceWeight 0.02; minVolRatio 0.01; minTriangleTwist -1;
 nSmoothScale 4; errorReduction 0.75; relaxed {{ maxNonOrtho 75; }}
}}
writeFlags (scalarLevels layerSets layerFields);
mergeTolerance 1e-6;
""")
    write(folder,"constant/transportProperties",f"transportModel Newtonian;\nnu [0 2 -1 0 0 0 0] {spec.fluid.dynamic_viscosity/spec.fluid.density};")
    write(folder,'system/meshQualityDict','maxNonOrtho 65; maxBoundarySkewness 20; maxInternalSkewness 4; maxConcave 80; minVol 1e-13; minTetQuality 1e-15; minArea -1; minTwist 0.02; minDeterminant 0.001; minFaceWeight 0.02; minVolRatio 0.01; minTriangleTwist -1;')
    turbulence="simulationType laminar;" if spec.flow.turbulence=="laminar" else "simulationType RAS;\nRAS { RASModel kOmegaSST; turbulence on; printCoeffs on; }"
    write(folder,"constant/turbulenceProperties",turbulence)
    values={"U":("[0 1 -1 0 0 0 0]",vector(spec.flow.velocity())),"p":("[0 2 -2 0 0 0 0]","0")}
    if spec.flow.turbulence!="laminar":
        k=max(1.5*(max(spec.flow.speed,0.01)*spec.flow.intensity)**2,1e-10)
        values.update({"k":("[0 2 -2 0 0 0 0]",str(k)),"omega":("[0 0 -1 0 0 0 0]",str(math.sqrt(k)/(0.09**0.25*spec.flow.length_scale))),"nut":("[0 2 -1 0 0 0 0]","0")})
    for field,(dimensions,value) in values.items():
        content=f"dimensions {dimensions};\ninternalField uniform {value};\nboundaryField\n{{\n"
        content+="\n".join(f"{b.patch} {{ {boundary_field(spec,b,field)} }}" for b in boundaries)
        write(folder,"0/"+field,content+"\n}","volVectorField" if field=="U" else "volScalarField")
    write_schemes(spec,folder)
    write_solution(spec,folder)
    write(folder,"system/decomposeParDict",f"numberOfSubdomains {spec.solver.processes};\nmethod scotch;")
    force_patches=[b.patch for b in boundaries if b.patch in patches and b.kind in {"wall","moving_wall","rotating_wall"}]
    # Porous resistance is a body force on the fluid; forces then also report it separately.
    porosity=" porosity yes;" if spec.elements.porous else ""
    def force_function(name,selection):
        return f'''{name} {{ type forces; libs ("libforces.so"); patches ({' '.join(selection)}); rho rhoInf; rhoInf {spec.fluid.density}; pRef 0; CofR {vector(spec.references.origin)}; log true;{porosity} writeControl timeStep; writeInterval 1; }}'''
    load_selection=spec.use_case.force_patches if spec.use_case and spec.use_case.force_patches else force_patches
    v2=spec.schema_version>=2
    functions=force_function("loads",load_selection)
    # Per-surface loads stay for v1. v2 caps them (a STEP model has one surface per CAD face)
    # and reports named part groups instead.
    if not v2 or len(force_patches)<=64:
        for p in force_patches:functions+="\n"+force_function("load_"+p,[p])
    for zone in spec.rotating_zones():functions+="\n"+force_function(foam_elements.zone_names(zone)[3],zone.patches)
    for wheel in spec.elements.wheels:functions+="\n"+force_function(f"wheel_{wheel.name}",wheel.patches)
    functions+=porous_functions
    residual_fields=" ".join(foam_functions.solver_fields(spec)) if v2 else "p U"
    functions+=f'\nresiduals {{ type solverInfo; libs ("libutilityFunctionObjects.so"); fields ({residual_fields}); writeControl timeStep; writeInterval 1; }}'
    if spec.flow.turbulence!="laminar":functions+='\nyPlus { type yPlus; libs ("libfieldFunctionObjects.so"); writeControl writeTime; }'
    if spec.mode=='internal':
        functions+=f'\ntotalPressureField {{ type pressure; libs (fieldFunctionObjects); mode total; result gustsimTotalPressure; rho rhoInf; rhoInf {spec.fluid.density}; pRef 0; executeControl timeStep; executeInterval 1; writeControl timeStep; writeInterval 1; }}'
    for b in boundaries:
        if b.kind in {"velocity_inlet","flow_inlet","pressure_inlet","pressure_outlet","flow_outlet","freestream"}:
            functions+=f'\nflux_{b.patch} {{ type surfaceFieldValue; libs ("libfieldFunctionObjects.so"); regionType patch; name {b.patch}; operation sum; fields (phi); writeFields false; writeControl timeStep; writeInterval 1; }}'
            functions+=f'\npressure_{b.patch} {{ type surfaceFieldValue; libs ("libfieldFunctionObjects.so"); regionType patch; name {b.patch}; operation areaAverage; fields (p); writeFields false; writeControl timeStep; writeInterval 1; }}'
            if spec.mode=='internal':
                functions+=f'\ntotalpressure_{b.patch} {{ type surfaceFieldValue; libs (fieldFunctionObjects); regionType patch; name {b.patch}; operation weightedAverage; weightField phi; fields (gustsimTotalPressure); writeFields false; writeControl timeStep; writeInterval 1; }}'
                functions+=f'\nabsoluteFlux_{b.patch} {{ type surfaceFieldValue; libs (fieldFunctionObjects); regionType patch; name {b.patch}; operation sumMag; fields (phi); writeFields false; writeControl timeStep; writeInterval 1; }}'
    if v2:
        group_functions,groups=foam_functions.part_force_functions(spec,meta,patches,force_function)
        functions+=group_functions
        functions+=foam_functions.output_functions(spec,force_patches)
        (folder/"force_groups.json").write_text(json.dumps([{"name":n,"patches":s} for n,s in groups],indent=2),encoding="utf-8")
    if spec.convergence.enabled:
        functions+=foam_functions.stop_function(folder)
    write_control(spec,folder,functions,end_time(spec))
    if spec.free_surface is not None:
        from . import foam_multiphase
        foam_multiphase.prepare(spec, folder, boundaries)
    elif spec.time.sliding_mesh:
        from . import foam_ami
        foam_ami.prepare(spec, folder)
    # Preserve an executable-free manifest; worker chooses the actual command sequence.
    libraries={}
    for name in ('gustsim','numpy','trimesh','pydantic','vtk','cadquery-ocp'):
        try:libraries[name]=importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:pass
    (folder/"provenance.json").write_text(json.dumps({"schema_version":1,"openfoam_version":config.FOAM_VERSION,
        "python":platform.python_version(),"libraries":libraries,"native_runtime":Path('/app/native-versions.txt').read_text() if Path('/app/native-versions.txt').exists() else None,
        "geometry_sha256":meta["sha256"],"units":"SI","pressure":"kinematic in raw case; Pa gauge in derived exports",
        "time_axis":"SIMPLE iteration, not physical seconds","validation_status":"experimental_validation_pending"},indent=2),encoding="utf-8")
    return folder
