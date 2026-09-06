"""OpenCFD v2606 case compiler. All emitted values come from typed models."""
import json
import importlib.metadata
import platform
import math
from pathlib import Path
import numpy as np
from . import config, db, geometry
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
    if b.kind=="pressure_outlet":return f"type inletOutlet; inletValue uniform {val}; value uniform {val};"
    return "type zeroGradient;"

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
    for patch in meta["patches"]:
        part=mesh.submesh([np.flatnonzero(groups==patch["group"])],append=True)
        part.export(folder/"constant"/"triSurface"/(patch["name"]+".stl"))
    low,high=spec.domain.minimum,spec.domain.maximum
    vertices=[(low[0],low[1],low[2]),(high[0],low[1],low[2]),(high[0],high[1],low[2]),(low[0],high[1],low[2]),
              (low[0],low[1],high[2]),(high[0],low[1],high[2]),(high[0],high[1],high[2]),(low[0],high[1],high[2])]
    extent=np.array(high)-np.array(low)
    counts=np.maximum(4,np.ceil(spec.mesh.base_cells*extent/max(extent))).astype(int)
    faces={"inlet":"(0 4 7 3)","outlet":"(1 2 6 5)","front":"(0 1 5 4)","back":"(3 7 6 2)","ground":"(0 3 2 1)","top":"(4 5 6 7)"}
    bounds={b.patch:b for b in spec.boundaries}
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
    direction=np.asarray(spec.flow.velocity()); direction=direction/max(np.linalg.norm(direction),1e-12)
    center=np.mean(meta["bounds"],axis=0)
    wake_end=center+direction*spec.references.length*5
    wake_min=np.minimum(center,wake_end)-spec.references.length*0.6
    wake_max=np.maximum(center,wake_end)+spec.references.length*0.6
    geo+=f"\nwake {{ type searchableBox; min {vector(wake_min)}; max {vector(wake_max)}; }}"
    refinements.append(f"wake {{ mode inside; levels ((1e15 {max(1,spec.mesh.surface_level-1)})); }}")
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
    if spec.rotation.enabled:
        r=spec.rotation
        a=np.asarray(r.origin)-np.asarray(r.axis)*r.length/2;b=np.asarray(r.origin)+np.asarray(r.axis)*r.length/2
        geo+=f"\nrotorRegion {{ type searchableCylinder; point1 {vector(a)}; point2 {vector(b)}; radius {r.radius}; }}"
        refinements.append(f"rotorRegion {{ mode inside; levels ((1e15 {spec.mesh.surface_level})); }}")
        write(folder,"system/topoSetDict",f"actions\n(\n{{name rotorCells;type cellSet;action new;source cylinderToCell;point1 {vector(a)};point2 {vector(b)};radius {r.radius};}}\n{{name rotor;type cellZoneSet;action new;source setToCellZone;set rotorCells;}}\n);")
        nonrotating=[b.patch for b in spec.boundaries if b.patch not in r.patches]
        write(folder,"constant/MRFProperties",f"rotor {{ active yes; cellZone rotor; nonRotatingPatches ({' '.join(nonrotating)}); origin {vector(r.origin)}; axis {vector(r.axis)}; omega {r.rpm*math.pi/30}; }}")
    surfaces="\n".join(f"{p} {{ level ({spec.mesh.surface_level} {spec.mesh.surface_level}); patchInfo {{ type {patch_type(p)}; }} }}" for p in patches)
    layer_patches="\n".join(f"{b.patch} {{ nSurfaceLayers {spec.mesh.layers}; }}" for b in spec.boundaries if b.patch in patches and b.kind in {"wall","moving_wall","rotating_wall"})
    features="\n".join(f'{{ file "{p}.eMesh"; level {spec.mesh.feature_level}; }}' for p in patches)
    write(folder,"system/snappyHexMeshDict",f"""
castellatedMesh true;
snap true;
addLayers {'true' if spec.mesh.layers else 'false'};
geometry {{ {geo} }}
castellatedMeshControls
{{
 maxLocalCells {spec.mesh.max_cells}; maxGlobalCells {spec.mesh.max_cells}; minRefinementCells 0;
 maxLoadUnbalance 0.1; nCellsBetweenLevels 3;
 features ({features});
 refinementSurfaces {{ {surfaces} }}
 resolveFeatureAngle 30;
 refinementRegions {{ {' '.join(refinements)} }}
 locationInMesh {vector(spec.domain.fluid_point)};
 allowFreeStandingZoneFaces true;
}}
snapControls {{ nSmoothPatch 3; tolerance 2; nSolveIter 30; nRelaxIter 5; nFeatureSnapIter 10; implicitFeatureSnap false; explicitFeatureSnap true; multiRegionFeatureSnap true; }}
addLayersControls
{{
 relativeSizes false;
 layers {{ {layer_patches} }}
 expansionRatio {spec.mesh.expansion_ratio}; firstLayerThickness {spec.mesh.first_layer_m}; minThickness {spec.mesh.first_layer_m*0.1};
 nGrow 0; featureAngle 60; slipFeatureAngle 30; nRelaxIter 3; nSmoothSurfaceNormals 1;
 nSmoothNormals 3; nSmoothThickness 10; maxFaceThicknessRatio 0.5; maxThicknessToMedialRatio 0.3;
 minMedialAxisAngle 90; nBufferCellsNoExtrude 0; nLayerIter 50; nRelaxedIter 20;
}}
meshQualityControls
{{
 maxNonOrtho 65; maxBoundarySkewness 20; maxInternalSkewness 4; maxConcave 80;
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
        content+="\n".join(f"{b.patch} {{ {boundary_field(spec,b,field)} }}" for b in spec.boundaries)
        write(folder,"0/"+field,content+"\n}","volVectorField" if field=="U" else "volScalarField")
    turbulent_div="div(phi,k) bounded Gauss upwind; div(phi,omega) bounded Gauss upwind;" if spec.flow.turbulence!="laminar" else ""
    write(folder,"system/fvSchemes",f"ddtSchemes {{ default steadyState; }}\ngradSchemes {{ default cellLimited Gauss linear 1; }}\ndivSchemes {{ default none; div(phi,U) bounded Gauss linearUpwind grad(U); {turbulent_div} div((nuEff*dev2(T(grad(U))))) Gauss linear; }}\nlaplacianSchemes {{ default Gauss linear limited 0.5; }}\ninterpolationSchemes {{ default linear; }}\nsnGradSchemes {{ default limited 0.5; }}\nwallDist {{ method meshWave; }}")
    write(folder,"system/fvSolution",f'''solvers {{ p {{ solver GAMG; tolerance 1e-8; relTol 0.01; smoother GaussSeidel; }} "(U|k|omega)" {{ solver smoothSolver; smoother symGaussSeidel; tolerance 1e-8; relTol 0.1; }} }}
SIMPLE {{ nNonOrthogonalCorrectors 1; consistent yes; residualControl {{ p {spec.solver.residual_target}; U {spec.solver.residual_target}; "(k|omega)" {spec.solver.residual_target}; }} }}
relaxationFactors {{ fields {{ p 0.3; }} equations {{ U 0.7; k 0.7; omega 0.7; }} }}''')
    write(folder,"system/decomposeParDict",f"numberOfSubdomains {spec.solver.processes};\nmethod scotch;")
    force_patches=[b.patch for b in spec.boundaries if b.patch in patches and b.kind in {"wall","moving_wall","rotating_wall"}]
    def force_function(name,selection):
        return f'''{name} {{ type forces; libs ("libforces.so"); patches ({' '.join(selection)}); rho rhoInf; rhoInf {spec.fluid.density}; pRef 0; CofR {vector(spec.references.origin)}; log true; writeControl timeStep; writeInterval 1; }}'''
    load_selection=spec.use_case.force_patches if spec.use_case and spec.use_case.force_patches else force_patches
    functions=force_function("loads",load_selection)
    for p in force_patches:functions+="\n"+force_function("load_"+p,[p])
    if spec.rotation.enabled:functions+="\n"+force_function("rotorLoads",spec.rotation.patches)
    functions+='\nresiduals { type solverInfo; libs ("libutilityFunctionObjects.so"); fields (p U); writeControl timeStep; writeInterval 1; }'
    if spec.flow.turbulence!="laminar":functions+='\nyPlus { type yPlus; libs ("libfieldFunctionObjects.so"); writeControl writeTime; }'
    if spec.mode=='internal':
        functions+=f'\ntotalPressureField {{ type pressure; libs (fieldFunctionObjects); mode total; result gustsimTotalPressure; rho rhoInf; rhoInf {spec.fluid.density}; pRef 0; executeControl timeStep; executeInterval 1; writeControl timeStep; writeInterval 1; }}'
    for b in spec.boundaries:
        if b.kind in {"velocity_inlet","flow_inlet","pressure_inlet","pressure_outlet","freestream"}:
            functions+=f'\nflux_{b.patch} {{ type surfaceFieldValue; libs ("libfieldFunctionObjects.so"); regionType patch; name {b.patch}; operation sum; fields (phi); writeFields false; writeControl timeStep; writeInterval 1; }}'
            functions+=f'\npressure_{b.patch} {{ type surfaceFieldValue; libs ("libfieldFunctionObjects.so"); regionType patch; name {b.patch}; operation areaAverage; fields (p); writeFields false; writeControl timeStep; writeInterval 1; }}'
            if spec.mode=='internal':
                functions+=f'\ntotalpressure_{b.patch} {{ type surfaceFieldValue; libs (fieldFunctionObjects); regionType patch; name {b.patch}; operation weightedAverage; weightField phi; fields (gustsimTotalPressure); writeFields false; writeControl timeStep; writeInterval 1; }}'
                functions+=f'\nabsoluteFlux_{b.patch} {{ type surfaceFieldValue; libs (fieldFunctionObjects); regionType patch; name {b.patch}; operation sumMag; fields (phi); writeFields false; writeControl timeStep; writeInterval 1; }}'
    write(folder,"system/controlDict",f'''application simpleFoam;
startFrom startTime; startTime 0; stopAt endTime; endTime {spec.solver.iterations}; deltaT 1;
writeControl timeStep; writeInterval {spec.solver.write_interval}; purgeWrite 0;
writeFormat ascii; writePrecision 10; writeCompression off; timeFormat general; timePrecision 10;
runTimeModifiable false;
functions {{ {functions} }}''')
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
