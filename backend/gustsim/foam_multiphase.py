"""Calm-water free surface: interFoam with local time stepping and a fixed waterline.

Trim and sinkage are not solved. The waterline is the value in the spec. Air is above it and
water below it. Experimental until hydrostatic_box and dtc_resistance pass.
"""
import math
import re

from .foam import vector, write


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


def prepare(spec, folder, boundaries):
    """Overwrite the single-phase transport and time scheme. Called after the base compiler."""
    surface = spec.free_surface
    water, air = surface.water, surface.air
    sigma = 0.07
    write(folder, 'constant/transportProperties',
          f'thermalPhysicalProperties off;\nphases (water air);\nwater {{ transportModel Newtonian; nu {water.dynamic_viscosity/water.density:.8g}; rho {water.density:.8g}; }}\n'
          f'air {{ transportModel Newtonian; nu {air.dynamic_viscosity/air.density:.8g}; rho {air.density:.8g}; }}\n'
          f'sigma {sigma:.8g};\n')
    write(folder, 'constant/g', f'dimensions [0 1 -2 0 0 0 0];\nvalue {vector(surface.gravity)};', 'uniformDimensionedVectorField')
    low, high = spec.domain.minimum, spec.domain.maximum
    write(folder, 'system/setFieldsDict',
          'defaultFieldValues ( volScalarFieldValue alpha.water 0 );\nregions ( boxToCell { box '
          f'{vector((low[0], low[1], low[2]))} {vector((high[0], high[1], surface.waterline_z))}; '
          'fieldValues ( volScalarFieldValue alpha.water 1 ); } );')
    alpha = 'dimensions [0 0 0 0 0 0 0];\ninternalField uniform 0;\nboundaryField\n{\n'
    for boundary in boundaries:
        if boundary.kind in ('wall', 'moving_wall', 'rotating_wall'):
            alpha += f'{boundary.patch} {{ type zeroGradient; }}\n'
        else:
            alpha += f'{boundary.patch} {{ type inletOutlet; inletValue uniform 0; value uniform 0; }}\n'
    write(folder, '0/alpha.water', alpha + '}', 'volScalarField')
    prgh = 'dimensions [0 2 -2 0 0 0 0];\ninternalField uniform 0;\nboundaryField\n{\n'
    for boundary in boundaries:
        if boundary.kind == 'pressure_outlet':
            prgh += f'{boundary.patch} {{ type fixedValue; value uniform 0; }}\n'
        else:
            prgh += f'{boundary.patch} {{ type fixedFluxPressure; value uniform 0; }}\n'
    write(folder, '0/p_rgh', prgh + '}', 'volScalarField')
    schemes = (folder / 'system' / 'fvSchemes').read_text(encoding='utf-8')
    schemes = schemes.replace('ddtSchemes { default steadyState; }', 'ddtSchemes { default localEuler; }', 1)
    if 'div(phi,alpha)' not in schemes:
        schemes = schemes.replace('divSchemes { default none;', 'divSchemes { default none; div(phi,alpha) Gauss vanLeer;', 1)
    (folder / 'system' / 'fvSchemes').write_text(schemes, encoding='utf-8', newline='\n')
    solution = (folder / 'system' / 'fvSolution').read_text(encoding='utf-8')
    if 'p_rgh' not in solution:
        solution = solution.replace(
            'solvers {',
            'solvers { p_rgh { solver GAMG; tolerance 1e-7; relTol 0.01; smoother GaussSeidel; } '
            'p_rghFinal { $p_rgh; relTol 0; } alpha.water { nAlphaCorr 1; nAlphaSubCycles 1; cAlpha 1; } ',
            1)
    (folder / 'system' / 'fvSolution').write_text(solution, encoding='utf-8', newline='\n')
    control = (folder / 'system' / 'controlDict').read_text(encoding='utf-8')
    control = re.sub(r'application\s+\w+;', 'application interFoam;', control, count=1)
    (folder / 'system' / 'controlDict').write_text(control, encoding='utf-8', newline='\n')
    return {'waterline_z': surface.waterline_z, 'sigma_n_per_m': sigma, 'solver': 'interFoam', 'time': 'localEuler'}
