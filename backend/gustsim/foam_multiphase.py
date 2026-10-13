"""Calm-water free surface: interFoam with local time stepping and a fixed waterline.

Trim and sinkage are not solved. The waterline is the value in the spec. Air is above it and
water below it. Experimental until hydrostatic_box and dtc_resistance pass.

Unlike the single-phase solvers, interFoam's pressure is dynamic (Pa): p_rgh is solved and
p = p_rgh + rho*g*h is in pascals. Exports therefore must not multiply it by a density again
(the results package plan carries `pressure_is_kinematic: false`). Dictionaries follow the
pinned v2606 interFoam layout (DTCHull / damBreak): PIMPLE with p_rgh and *Final solvers,
MULES alpha controls and the mixture-momentum divergence terms.
"""
import re

from .foam import vector, write

OPEN = ('freestream', 'pressure_inlet', 'pressure_outlet', 'velocity_inlet', 'flow_inlet', 'flow_outlet')
WALLS = ('wall', 'moving_wall', 'rotating_wall')


def wetted_area(alpha, area, threshold=0.5):
    """Area of faces whose water fraction is at least `threshold`, in m²."""
    total = 0.0
    for value, face in zip(alpha, area):
        if value == value and value >= threshold:
            total += face
    return total


def resistance_coefficient(drag_n, speed, area, density):
    """C_T = drag / (½ ρ U² A). None when the denominator is zero."""
    dynamic = 0.5 * density * speed * speed * area
    if dynamic <= 0 or drag_n is None:
        return None
    return drag_n / dynamic


def atmosphere(boundary):
    """The top of the tank is the open atmosphere when it is an open boundary."""
    return boundary.patch == 'top' and boundary.kind in ('freestream', 'pressure_inlet', 'pressure_outlet')


def field_entries(boundary):
    """(alpha.water, p_rgh, p) boundary entries for one boundary."""
    if boundary.kind == 'symmetry':
        return ('type symmetryPlane;',) * 3
    p = 'type calculated; value uniform 0;'
    if atmosphere(boundary):
        return ('type inletOutlet; inletValue uniform 0; value uniform 0;',
                'type totalPressure; p0 uniform 0; value uniform 0;', p)
    if boundary.kind == 'pressure_outlet':
        return ('type zeroGradient;', f'type fixedValue; value uniform {boundary.pressure_pa:.12g};', p)
    # Walls, slip walls and the other open faces keep the hydrostatic pressure gradient. Open
    # faces other than the atmosphere carry the initial (setFields) water fraction outwards
    # and inwards; an inletOutlet with a uniform inlet value would feed air below the waterline.
    return 'type zeroGradient;', 'type fixedFluxPressure; value uniform 0;', p


def schemes(spec):
    turbulent = ' div(phi,k) Gauss upwind; div(phi,omega) Gauss upwind;' if spec.flow.turbulence != 'laminar' else ''
    return ('ddtSchemes { default localEuler; }\n'
            'gradSchemes { default Gauss linear; grad(U) cellLimited Gauss linear 1; }\n'
            'divSchemes { default none; div(rhoPhi,U) Gauss linearUpwind grad(U); div(phi,alpha) Gauss vanLeer; '
            f'div(phirb,alpha) Gauss linear;{turbulent} div(((rho*nuEff)*dev2(T(grad(U))))) Gauss linear; '
            'div((nuEff*dev2(T(grad(U))))) Gauss linear; }\n'
            'laplacianSchemes { default Gauss linear limited 0.5; }\ninterpolationSchemes { default linear; }\n'
            'snGradSchemes { default limited 0.5; }\nwallDist { method meshWave; }')


def solution(spec):
    return ('solvers {\n'
            ' "alpha.water.*" { nAlphaCorr 2; nAlphaSubCycles 1; cAlpha 1; MULESCorr yes; nLimiterIter 3; '
            'solver smoothSolver; smoother symGaussSeidel; tolerance 1e-8; relTol 0; }\n'
            ' "pcorr.*" { solver GAMG; smoother DIC; tolerance 1e-5; relTol 0; }\n'
            ' p_rgh { solver GAMG; smoother DIC; tolerance 1e-7; relTol 0.01; }\n'
            ' p_rghFinal { $p_rgh; relTol 0; }\n'
            ' "(U|k|omega).*" { solver smoothSolver; smoother symGaussSeidel; tolerance 1e-7; relTol 0.1; minIter 1; }\n'
            '}\n'
            'PIMPLE { momentumPredictor no; nOuterCorrectors 1; nCorrectors 2; nNonOrthogonalCorrectors 1; '
            'maxCo 10; maxAlphaCo 5; rDeltaTSmoothingCoeff 0.05; rDeltaTDampingCoeff 0.5; '
            'nAlphaSpreadIter 0; nAlphaSweepIter 0; maxDeltaT 1; '
            # Used only when no boundary fixes the pressure level (no open top face).
            f'pRefPoint {vector(spec.domain.fluid_point)}; pRefValue 0; }}\n'
            'relaxationFactors { equations { ".*" 1; } }')


def prepare(spec, folder, boundaries):
    """Overwrite the single-phase transport, fields and numerics. Called after the base compiler."""
    surface = spec.free_surface
    water, air = surface.water, surface.air
    sigma = 0.07
    write(folder, 'constant/transportProperties',
          f'phases (water air);\nwater {{ transportModel Newtonian; nu {water.dynamic_viscosity/water.density:.8g}; rho {water.density:.8g}; }}\n'
          f'air {{ transportModel Newtonian; nu {air.dynamic_viscosity/air.density:.8g}; rho {air.density:.8g}; }}\n'
          f'sigma {sigma:.8g};\n')
    write(folder, 'constant/g', f'dimensions [0 1 -2 0 0 0 0];\nvalue {vector(surface.gravity)};', 'uniformDimensionedVectorField')
    low, high = spec.domain.minimum, spec.domain.maximum
    write(folder, 'system/setFieldsDict',
          'defaultFieldValues ( volScalarFieldValue alpha.water 0 );\nregions ( boxToCell { box '
          f'{vector((low[0], low[1], low[2]))} {vector((high[0], high[1], surface.waterline_z))}; '
          'fieldValues ( volScalarFieldValue alpha.water 1 ); } );')
    fields = {'alpha.water': '[0 0 0 0 0 0 0]', 'p_rgh': '[1 -1 -2 0 0 0 0]', 'p': '[1 -1 -2 0 0 0 0]'}
    entries = {name: '' for name in fields}
    for boundary in boundaries:
        for name, entry in zip(fields, field_entries(boundary)):
            entries[name] += f'{boundary.patch} {{ {entry} }}\n'
    for name, dimensions in fields.items():
        write(folder, '0/' + name, f'dimensions {dimensions};\ninternalField uniform 0;\nboundaryField\n{{\n{entries[name]}}}', 'volScalarField')
    for boundary in boundaries:
        if atmosphere(boundary):
            from .foam import set_patch_field
            set_patch_field(folder, 'U', boundary.patch, 'type pressureInletOutletVelocity; value uniform (0 0 0);')
    write(folder, 'system/fvSchemes', schemes(spec))
    write(folder, 'system/fvSolution', solution(spec))
    control = (folder / 'system' / 'controlDict').read_text(encoding='utf-8')
    control = re.sub(r'application\s+\w+;', 'application interFoam;', control, count=1)
    # interFoam solves p_rgh and alpha, not p, and without a momentum predictor not U either;
    # residuals follow the solved fields.
    control = re.sub(r'(residuals \{[^}]*fields \()U p\b', r'\1p_rgh alpha.water', control, count=1)
    (folder / 'system' / 'controlDict').write_text(control, encoding='utf-8', newline='\n')
    return {'waterline_z': surface.waterline_z, 'sigma_n_per_m': sigma, 'solver': 'interFoam', 'time': 'localEuler'}
