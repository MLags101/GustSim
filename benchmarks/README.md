# Engineering acceptance suite

The runtime smoke tests prove that the real mesh/solver/postprocessing pipeline runs; they do **not** satisfy these release gates. Keep experimental validation pending until versioned measurements, exact geometry, source citations, and sensitivity studies are committed here.

1. **Laminar pipe:** sample fully developed axial velocity at specified radial locations. Compare with `u(r)=2 Umean (1-r²/R²)` and `Δp=8 μ L Umean/R²` between two fully developed sections. Match inlet development, no-slip walls, viscosity, and measurement length. Target maximum profile error normalized by centerline speed and pressure-drop relative error ≤2%.
2. **Rotating cylinders:** compare tangential velocity with `uθ=A r+B/r`; match both wall speeds, radii, and end/periodic treatment. Avoid end effects or use the supplied OpenFOAM 2D verification geometry. Target normalized profile error ≤2%.
3. **NACA 0012:** use the [Turbulence Modeling Resource](https://tmbwg.github.io/turbmodels/naca0012_val.html) geometry and reference conditions. Record turbulence formulation, transition/trip assumptions, force reference area, moment origin, far-field treatment, and grid sequence. Do not compare an SST run to an SA verification result as if the turbulence models were identical.
4. **Potsdam VP1304:** download STEP geometry and non-cavitating open-water measurements from [SVA Potsdam](https://www.sva-potsdam.de/en/pptc-smp11-workshop/). Record file checksums, pitch, diameter, hub configuration, fluid properties, RPM and advance ratio. Compare thrust and torque coefficients at ≥3 published points away from zero coefficients; target ≤10% error at every selected point.
5. **Mesh/domain sensitivity:** run three systematically refined meshes. Target <5% change in primary loads between the two finest meshes; separately expand the domain to assess boundary interference. Layer behavior, y+, and solver convergence must be reviewed for each mesh.

`python -m gustsim.benchmarks measurements.json --output acceptance.json` evaluates pipe, rotating-cylinder, PPTC, or mesh-study measurements and exits nonzero when targets fail. The evaluator is unit-tested; no measured benchmark dataset is bundled yet. It never changes the application's validation label automatically. `backend/gustsim/benchmarks.py` defines the JSON fields and formulas. Put authentic measured data and source provenance alongside the resulting reports; never manufacture measurements to pass a gate.

## Gates for the experimental capabilities

`backend/gustsim/capabilities.py` lists each capability's gates. A gate counts as passed only when `benchmarks/results/<gate>.json` exists with `"status": "passed"`, written by the evaluator from authentic measurements:

```sh
python -m gustsim.benchmarks measurements.json --output benchmarks/results/<gate>.json
```

A passed gate never promotes a capability by itself. Promotion from `experimental` to `core` is a reviewed edit of `capabilities.py` that cites the result file. Gates that compare against published data (`pptc`, `reference`) are refused without `provenance.source`.

| Gate | Capability | Evaluator kind | Requirement |
|---|---|---|---|
| `early_stop_consistency` | Force-based early stop | `early_stop_consistency` | Coefficients at the early stop within 1% of the window average of the same case run to its full iteration budget |
| `flat_plate_cf` | Wall shear / Cf output | `flat_plate_cf` | Laminar local Cf within 5% of Blasius `0.664/√Re_x` at three or more stations away from the leading edge |
| `multi_zone_couette` | Multiple MRF zones | `multi_zone_couette` | Two or more concentric annuli, each driven by its own zone: tangential velocity within 2% of the Couette profile in every zone |
| `porous_duct` | Porous radiators | `porous_duct` | Slip-wall duct (uniform flow) through a porous block at two or more speeds: measured Δp within 2% of the two-point curve `a·v + b·v²`, with a three-mesh study of the cell layers across the block. The smoke case measured +21% at 6 layers, +8% at 11 and +2.4% at 21 (`smoke_linux.py --porous --mode internal`); expect about 20 layers or more |
| `fackrell_wheel` | Rotating wheels | `reference` | Isolated rotating wheel on a moving ground (Fackrell & Harvey): lift and drag coefficients within the tolerances recorded with the data |
| `drivaer_ground` | Moving ground | `reference` | DrivAer (fastback, smooth underbody, moving ground and rotating wheels): Cd within 5% of the cited wind-tunnel value at the cited Reynolds number |
| `adjoint_fd` | Design advice | `reference` | Sensitivity sign correct at every probe; magnitude within 10% (laminar) or 30% (frozen-turbulence SST) of central finite differences |
| `morph_verification` | Morphing | `reference` | The verification run of the morphed shape confirms the predicted objective change in sign and within 30% |
| `cylinder_re100` | Transient | `reference` | Circular cylinder, Re = 100: Strouhal within 3% and mean Cd within 5% |
| `square_cylinder` | Transient | `reference` | Square cylinder, Re = 22 000 (URANS/DES): Strouhal within 10% |
| `hydrostatic_box` | Free surface | `reference` | Still water in a box: hydrostatic pressure within 1% |
| `dtc_resistance` | Free surface | `reference` | DTC hull, calm water: total resistance coefficient within 7% of the towing-tank value |

The analytical evaluators (`pipe`, `rotating_cylinders`, `multi_zone_couette`, `porous_duct`, `early_stop_consistency`, `flat_plate_cf`) and the gate-status contract are unit-tested in `tests/test_benchmarks_backup.py`. `early_stop_consistency` passed on 2026-10-13 (see `benchmarks/measurements/` and `benchmarks/results/`). **No other gate has been executed.** Each still needs a reviewed case, a mesh study and measured outputs.
