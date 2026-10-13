"""Adjoint design advice and shape morphing for OpenCFD v2606 adjointOptimisationFoam.

Dictionaries follow the v2512/v2606 tutorials `sensitivityMaps` (one face-sensitivity map)
and `shapeOptimisation` (volumetric B-splines, steadyOptimisation). The primal solve is left
as it finished: sensitivity fields are copied back onto that time directory and any later
adjoint time is removed, so the results package still reads the steady solution.

Turbulence is laminar when the flow is laminar, and frozen k–ω SST otherwise (the adjoint
does not differentiate the turbulence model). Morphing is a separate attempt: it copies the
mesh, runs the optimisation, and keeps the cycle before the objective rises.

This module does not run a solver and does not claim that a map or a morphed shape matches
an experiment. The `adjoint_fd` and `morph_verification` gates stay not-executed until a
real comparison is recorded.
"""
import hashlib
import re
import shutil
from pathlib import Path

from . import foam

SENSITIVITY_SUFFIX = 'gustsim'
OBJECTIVE_LINE = re.compile(r'\b(drag|lift)\b\s*[:=]\s*([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)')


def objective_patches(spec):
    from .validation import objective_patches as patches
    return patches(spec)


def turbulence_treatment(spec):
    """How the adjoint treats turbulence. Frozen means the primal viscosity is held fixed."""
    if spec.flow.turbulence == 'laminar':
        return 'laminar'
    return 'frozen-kOmegaSST'


def direction(spec):
    axis = spec.references.drag_axis if spec.design.objective == 'drag' else spec.references.lift_axis
    return foam.vector(axis)


def patch_list(names):
    return ' '.join(f'"{name}"' for name in names)


def turbulence_dict(spec):
    """adjointTurbulence on only for laminar flow. SST keeps the frozen-viscosity assumption."""
    frozen = spec.flow.turbulence != 'laminar'
    return f'''adjointRASModel adjointLaminar;
adjointTurbulence {'off' if frozen else 'on'};
printCoeffs off;
'''


def optimisation_dict(spec, patches, morph=False):
    """Body of system/optimisationDict. `morph` selects the shape-update manager."""
    names = patch_list(patches)
    objective = spec.design.objective
    primal = f'''optimisationManager {'steadyOptimisation' if morph else 'singleRun'};

primalSolvers
{{
    op1
    {{
        active true;
        type incompressible;
        solver simple;
        solutionControls
        {{
            consistent yes;
            nIters {spec.design.iterations};
            residualControl
            {{
                "p.*" 1e-5;
                "U.*" 1e-5;
            }}
        }}
    }}
}}

adjointManagers
{{
    adjManager1
    {{
        primalSolver op1;
        adjointSolvers
        {{
            adjS1
            {{
                active true;
                type incompressible;
                solver adjointSimple;
                computeSensitivities true;
                objectives
                {{
                    type incompressible;
                    objectiveNames
                    {{
                        {objective}
                        {{
                            weight 1;
                            type force;
                            patches ({names});
                            direction {direction(spec)};
                            Aref {spec.references.area:.12g};
                            rhoInf {spec.fluid.density:.12g};
                            UInf {spec.flow.speed:.12g};
                        }}
                    }}
                }}
                ATCModel
                {{
                    ATCModel standard;
                    extraConvection 0;
                    nSmooth 0;
                    zeroATCPatchTypes (wall patch);
                    maskType pointCells;
                }}
                solutionControls
                {{
                    consistent yes;
                    nIters {spec.design.iterations};
                    residualControl
                    {{
                        "pa.*" 1e-5;
                        "Ua.*" 1e-5;
                    }}
                }}
            }}
        }}
    }}
}}
'''
    if not morph:
        return primal + f'''
optimisation
{{
    designVariables
    {{
        sensitivityType multiple;
        sensitivityTypes ( faceMap );
        patches ({names});
        faceMap
        {{
            sensitivityType surface;
            patches ({names});
            includeSurfaceArea true;
            smoothSensitivities true;
            meanRadiusMultiplier 2;
            suffix {SENSITIVITY_SUFFIX};
            iters 300;
        }}
    }}
}}
'''
    step = min(box.max_displacement_m for box in spec.elements.design_boxes)
    return primal + f'''
optimisation
{{
    designVariables
    {{
        type shape;
        shapeType volumetricBSplines;
        sensitivityType shapeFI;
        patches ({names});
        maxInitChange {step:.12g};
    }}
    updateMethod
    {{
        method BFGS;
        BFGS
        {{
            nSteepestDescent 1;
            etaHessian 1;
            scaleFirstHessian true;
        }}
        lineSearch
        {{
            type ArmijoConditions;
        }}
    }}
}}
'''


def _degree(count):
    return max(1, min(3, count - 1))


def dynamic_mesh_dict(spec):
    """Cartesian volumetric B-spline boxes. Boundary control points stay fixed."""
    blocks = []
    for box in spec.elements.design_boxes:
        lower = tuple(c - d / 2 for c, d in zip(box.center, box.dimensions))
        upper = tuple(c + d / 2 for c, d in zip(box.center, box.dimensions))
        nu, nv, nw = box.control_points
        confine = tuple('false' if free else 'true' for free in box.free_axes)
        blocks.append(f'''    {box.name}
    {{
        type cartesian;
        nCPsU {nu};
        nCPsV {nv};
        nCPsW {nw};
        degreeU {_degree(nu)};
        degreeV {_degree(nv)};
        degreeW {_degree(nw)};
        controlPointsDefinition axisAligned;
        lowerCpBounds {foam.vector(lower)};
        upperCpBounds {foam.vector(upper)};
        confineUMovement {confine[0]};
        confineVMovement {confine[1]};
        confineWMovement {confine[2]};
        confineBoundaryControlPoints true;
    }}''')
    return 'solver volumetricBSplinesMotionSolver;\n\nvolumetricBSplinesMotionSolverCoeffs\n{\n' + '\n'.join(blocks) + '\n}\n'


def morph_surface_function(patches):
    names = patch_list(patches)
    return (f'\nmorphSurface {{ type surfaces; libs (sampling); writeControl timeStep; writeInterval 1; '
            f'surfaceFormat stl; fields (); surfaces {{ model {{ type patch; patches ({names}); '
            f'interpolate false; triangulate true; }} }} errors warn; }}')


def extend_numerics(folder):
    """Add adjoint discretisation the primal solve does not use. Called after the primal has finished."""
    schemes = folder / 'system' / 'fvSchemes'
    text = schemes.read_text(encoding='utf-8')
    if 'div(-phi,Ua)' not in text:
        # The adjoint equations convect with -phi (fvm::div(-phi, Ua)), so the v2606 scheme names
        # are div(-phi,Ua), div(-phi,ka), div(-phi,wa) as in the adjointOptimisationFoam
        # tutorials. Unused names are harmless; the +phi spellings and both adjoint stress
        # spellings are kept so a solver variant never finds `default none` without an entry.
        extra = (' div(-phi,Ua) bounded Gauss linearUpwind grad(Ua); div(phi,Ua) bounded Gauss linearUpwind grad(Ua);'
                 ' div(-phi,ka) bounded Gauss upwind; div(-phi,wa) bounded Gauss upwind;'
                 ' div(phi,ka) bounded Gauss upwind; div(phi,wa) bounded Gauss upwind;'
                 ' div((nuEff*dev(grad(Ua).T()))) Gauss linear; div((nuEff*dev(T(grad(Ua))))) Gauss linear;'
                 ' div((nuEff*dev2(T(grad(Ua))))) Gauss linear;')
        needle = 'div((nuEff*dev2(T(grad(U))))) Gauss linear;'
        if needle not in text:
            raise RuntimeError('fvSchemes has no momentum divergence term to extend for the adjoint')
        schemes.write_text(text.replace(needle, needle + extra, 1), encoding='utf-8', newline='\n')
    solution = folder / 'system' / 'fvSolution'
    text = solution.read_text(encoding='utf-8')
    if '"(pa|Ua|ka|wa)' not in text:
        # ma (adjoint mesh movement) and da (adjoint eikonal) are solved while the surface
        # sensitivities are computed.
        extra = ' "(pa|Ua|ka|wa|nua|ma|da).*" { solver smoothSolver; smoother symGaussSeidel; tolerance 1e-8; relTol 0.1; }'
        solution.write_text(text.replace('solvers {', 'solvers {' + extra, 1), encoding='utf-8', newline='\n')


def continue_control(folder, extra_iterations):
    """Keep the finished primal fields and give the adjoint room to advance time."""
    path = folder / 'system' / 'controlDict'
    text = path.read_text(encoding='utf-8')
    times = [float(p.name) for p in folder.iterdir() if p.is_dir() and _is_time(p.name)]
    latest = max(times) if times else 0
    text = re.sub(r'startFrom\s+\w+;', 'startFrom latestTime;', text, count=1)
    text = re.sub(r'endTime\s+[^;]+;', f'endTime {latest + extra_iterations:g};', text, count=1)
    path.write_text(text, encoding='utf-8', newline='\n')
    stop = folder / 'gustsim.stop'
    if stop.is_file():
        stop.unlink()


def adjoint_boundary(boundary):
    """(Ua, pa) boundary entries for one compiled boundary (v2606 adjoint conditions)."""
    kind = boundary.kind
    if kind == 'symmetry':
        return 'type symmetryPlane;', 'type symmetryPlane;'
    if kind in ('wall', 'moving_wall', 'rotating_wall'):
        return 'type adjointWallVelocity; value uniform (0 0 0);', 'type zeroGradient;'
    if kind == 'slip':
        return 'type slip;', 'type zeroGradient;'
    if kind in ('velocity_inlet', 'flow_inlet'):
        return 'type adjointInletVelocity; value uniform (0 0 0);', 'type zeroGradient;'
    if kind in ('pressure_outlet', 'flow_outlet', 'pressure_inlet'):
        return 'type adjointOutletVelocity; value uniform (0 0 0);', 'type adjointOutletPressure; value uniform 0;'
    return 'type adjointFarFieldVelocity; value uniform (0 0 0);', 'type adjointFarFieldPressure; value uniform 0;'


def write_adjoint_fields(spec, folder, time_name):
    """Zero initial adjoint fields Ua and pa in the primal time the adjoint starts from."""
    target = Path(folder) / time_name
    velocity, pressure = '', ''
    for boundary in spec.boundaries:
        ua, pa = adjoint_boundary(boundary)
        velocity += f'{boundary.patch} {{ {ua} }}\n'
        pressure += f'{boundary.patch} {{ {pa} }}\n'
    foam.write(target, 'Ua', f'dimensions [0 1 -1 0 0 0 0];\ninternalField uniform (0 0 0);\nboundaryField\n{{\n{velocity}}}', 'volVectorField')
    foam.write(target, 'pa', f'dimensions [0 2 -2 0 0 0 0];\ninternalField uniform 0;\nboundaryField\n{{\n{pressure}}}', 'volScalarField')


def write_sensitivity(spec, folder):
    patches = objective_patches(spec)
    if not patches:
        raise ValueError('Design advice has no model wall to differentiate')
    folder = Path(folder)
    primal = latest_time_name(folder)
    if primal is not None:
        write_adjoint_fields(spec, folder, primal)
    foam.write(folder, 'system/optimisationDict', optimisation_dict(spec, patches, morph=False))
    # smoothSensitivities solves on a finite-area mesh of the sensitivity patches.
    foam.write(folder, 'system/finite-area/faSchemes',
               'ddtSchemes { default steadyState; }\ngradSchemes { default Gauss linear; }\ndivSchemes { default none; }\n'
               'laplacianSchemes { default Gauss linear corrected; }\ninterpolationSchemes { default linear; }\n'
               'snGradSchemes { default corrected; }')
    foam.write(folder, 'system/finite-area/faSolution',
               'solvers { ".*" { solver PCG; preconditioner DIC; tolerance 1e-8; relTol 0; } }')
    body = turbulence_dict(spec)
    foam.write(folder, 'constant/adjointTurbulenceProperties', body)
    foam.write(folder, 'constant/adjointRASProperties', body)
    extend_numerics(folder)
    continue_control(folder, spec.design.iterations)
    return patches


def write_morph(spec, folder):
    patches = objective_patches(spec)
    if not spec.elements.design_boxes:
        raise ValueError('Morphing needs at least one design box')
    folder = Path(folder)
    foam.write(folder, 'system/optimisationDict', optimisation_dict(spec, patches, morph=True))
    foam.write(folder, 'constant/dynamicMeshDict', dynamic_mesh_dict(spec))
    body = turbulence_dict(spec)
    foam.write(folder, 'constant/adjointTurbulenceProperties', body)
    foam.write(folder, 'constant/adjointRASProperties', body)
    extend_numerics(folder)
    control = folder / 'system' / 'controlDict'
    text = control.read_text(encoding='utf-8')
    text = re.sub(r'application\s+\w+;', 'application adjointOptimisationFoam;', text, count=1)
    text = re.sub(r'startFrom\s+\w+;', 'startFrom startTime;', text, count=1)
    text = re.sub(r'endTime\s+[^;]+;', f'endTime {spec.design.cycles:g};', text, count=1)
    text = text.replace('functions {', 'functions {' + morph_surface_function(patches), 1)
    control.write_text(text, encoding='utf-8', newline='\n')
    stop = folder / 'gustsim.stop'
    if stop.is_file():
        stop.unlink()
    return patches


def _is_time(name):
    try:
        float(name)
        return True
    except ValueError:
        return False


def discard_later_times(case, primal_name):
    """Drop adjoint time directories so a failed adjoint cannot hide the steady solution."""
    if not primal_name:
        return
    case = Path(case)
    for time_dir in list(case.iterdir()):
        if time_dir.is_dir() and _is_time(time_dir.name) and time_dir.name != primal_name:
            if float(time_dir.name) != float(primal_name):
                shutil.rmtree(time_dir, ignore_errors=True)


def latest_time_name(case):
    times = [p.name for p in Path(case).iterdir() if p.is_dir() and _is_time(p.name)]
    return max(times, key=float) if times else None


def park_sensitivity(case, primal_name):
    """Copy adjoint sensitivity fields onto the primal time and drop later adjoint times.

    The steady solution stays the latest time the results package reads. Returns the field
    names that were kept.
    """
    case = Path(case)
    copied = []
    primal = case / primal_name
    if not primal.is_dir():
        raise RuntimeError(f'Primal time {primal_name} is missing; the adjoint output was left untouched')
    for time_dir in list(case.iterdir()):
        if not time_dir.is_dir() or not _is_time(time_dir.name) or time_dir.name == primal_name:
            continue
        for field in time_dir.iterdir():
            if field.is_file() and ('Sens' in field.name or SENSITIVITY_SUFFIX in field.name):
                shutil.copy2(field, primal / field.name)
                copied.append(field.name)
        if float(time_dir.name) != float(primal_name):
            shutil.rmtree(time_dir)
    # A field written into the primal time directly is kept too.
    for field in primal.iterdir():
        if field.is_file() and ('Sens' in field.name or SENSITIVITY_SUFFIX in field.name):
            copied.append(field.name)
    return sorted(set(copied))


def objective_history(log_text, name):
    """Objective value at the end of each `Time =` block. The last match in the block wins."""
    values = []
    current = None
    seen_time = False
    for line in log_text.splitlines():
        if line.startswith('Time ='):
            seen_time = True
            if current is not None:
                values.append(current)
            current = None
            continue
        match = OBJECTIVE_LINE.search(line)
        if match and match.group(1).lower() == name.lower():
            current = float(match.group(2))
    if current is not None:
        values.append(current)
    if not seen_time:
        values = [float(match.group(2)) for match in OBJECTIVE_LINE.finditer(log_text) if match.group(1).lower() == name.lower()]
    return values


def best_cycle(values):
    """Index of the cycle to keep: the lowest objective before the first rise after an improvement.

    A later rise stops the series, matching the morphing rule. An empty history has no cycle.
    """
    if not values:
        return None
    best = 0
    for index in range(1, len(values)):
        if values[index] > values[best]:
            return best
        if values[index] < values[best]:
            best = index
    return best


def morph_surfaces(case):
    """(time, stl paths) written by the morphSurface function object, in time order."""
    root = Path(case) / 'postProcessing' / 'morphSurface'
    found = []
    if not root.is_dir():
        return found
    for time_dir in root.iterdir():
        if not time_dir.is_dir() or not _is_time(time_dir.name):
            continue
        stls = sorted(time_dir.rglob('*.stl'))
        if stls:
            found.append((float(time_dir.name), stls))
    return sorted(found, key=lambda item: item[0])


def summarise(faces, spec, adjoint_run=None):
    """design.json. The map itself lives on the surface as the `sensitivity` array.

    `sensitivity` is the solver's face sensitivity, dJ/dn in N/m of inward normal displacement
    (OpenFOAM boundary normal, into the body). Negative (blue) predicts a lower objective when
    the surface moves inward; positive (red) when it moves outward. A 1 mm estimate is the
    value times 0.001 and is a linear adjoint prediction, not a measured change.
    """
    import numpy as np
    treatment = turbulence_treatment(spec)
    values = np.asarray(faces.get('sensitivity', []), float) if faces else np.asarray([])
    finite = values[np.isfinite(values)] if values.size else values
    summary = None
    if finite.size:
        inward = finite[finite < 0]
        summary = {
            'faces': int(finite.size),
            'min_n_per_m': float(finite.min()),
            'max_n_per_m': float(finite.max()),
            'mean_inward_delta_n_per_mm': float(inward.mean() * 0.001) if inward.size else None,
        }
    limitations = (
        f'{treatment} adjoint, objective {spec.design.objective}. Blue is a predicted decrease for a 1 mm inward '
        'move and red for a 1 mm outward move. The 1 mm figure is a linear estimate from the face sensitivity '
        '(N/m times 0.001). It is not a finite-difference check and it is not an experimental result.'
    )
    return {
        'schema': 'gustsim.design/1',
        'objective': spec.design.objective,
        'turbulence': treatment,
        'iterations': spec.design.iterations,
        'field': 'sensitivity',
        'source_fields': (adjoint_run or {}).get('fields') or [],
        'convention': 'negative (blue) = move inward to reduce the objective; positive (red) = move outward',
        'summary': summary,
        'morph': (adjoint_run or {}).get('morph'),
        'limitations': limitations,
        'available': summary is not None,
    }


def enqueue_id(solve_id):
    return hashlib.sha256(f'{solve_id}:optimise:1'.encode()).hexdigest()[:32]
