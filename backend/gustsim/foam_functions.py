"""Function objects that produce the results package fields and the stop mechanism.

Output-only function objects use `errors warn;` so a keyword mismatch in the pinned OpenCFD
release degrades to a missing field (reported by the results manifest) instead of aborting the
solve. The runtime probe (scripts/probe_runtime.py) confirms each one on the real image.
"""
import numpy as np


def vector(v):
    return "(" + " ".join(f"{x:.12g}" for x in v) + ")"


def coefficient_reference(spec):
    """Freestream velocity used for Cp/Cpt. Internal flow uses the inlet speed; a still
    freestream (static rotor) has no dynamic pressure, so no coefficients are written."""
    if spec.flow.speed <= 0:
        return None
    return spec.flow.velocity()


def stop_function(folder):
    # The worker's convergence monitor creates this file; the solver then writes the current
    # iteration and exits normally, so nothing is killed mid-write.
    return (f'\ngustsimStop {{ type abort; libs (utilityFunctionObjects); file "{(folder/"gustsim.stop").as_posix()}"; '
            'action writeNow; }')


def output_functions(spec, wall_patches):
    """Surface and volume fields for the results package (written at write times only)."""
    rho = spec.fluid.density
    out = ''
    if wall_patches:
        out += (f'\nwallShearStress {{ type wallShearStress; libs (fieldFunctionObjects); patches ({" ".join(wall_patches)}); '
                'writeControl writeTime; log false; errors warn; }')
    reference = coefficient_reference(spec)
    if reference is not None:
        out += (f'\npressureCoefficient {{ type pressure; libs (fieldFunctionObjects); mode staticCoeff; result Cp; '
                f'rho rhoInf; rhoInf {rho}; pRef 0; pInf 0; UInf {vector(reference)}; writeControl writeTime; errors warn; }}')
        out += (f'\ntotalPressureCoefficient {{ type pressure; libs (fieldFunctionObjects); mode totalCoeff; result Cpt; '
                f'rho rhoInf; rhoInf {rho}; pRef 0; pInf 0; UInf {vector(reference)}; writeControl writeTime; errors warn; }}')
    out += '\nQcriterion { type Q; libs (fieldFunctionObjects); writeControl writeTime; errors warn; }'
    out += '\nvorticityField { type vorticity; libs (fieldFunctionObjects); writeControl writeTime; errors warn; }'
    if spec.flow.turbulence != 'laminar' and spec.outputs.noise:
        out += (f'\nnoiseEstimate {{ type proudmanAcousticPower; libs (fieldFunctionObjects); alphaEps 0.1; '
                f'rhoInf {rho}; aRef {spec.fluid.speed_of_sound}; writeControl writeTime; errors warn; }}')
    return out


def part_force_functions(spec, meta, patches, force_function):
    """Loads per named group: user groups, else one per CAD component (leaf parts)."""
    groups = [(g.name, [p for p in g.patches if p in patches]) for g in spec.references.force_groups]
    if not groups:
        parts = meta.get('parts') or []
        parents = {p.get('parent_id') for p in parts}
        for index, part in enumerate(p for p in parts if p['id'] not in parents):
            selected = [p for p in part['patches'] if p in patches]
            if selected:
                groups.append((f'part_{index}', selected))
    return ''.join('\n' + force_function('group_' + name, selection) for name, selection in groups if selection), groups


def solver_fields(spec):
    fields = ['U', 'p']
    if spec.flow.turbulence != 'laminar':
        fields += ['k', 'omega']
    return fields
