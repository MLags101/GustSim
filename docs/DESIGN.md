# Design reference

GustSim's look and workflow follow AirShaper's public results pages. These values were taken on 2026-10-12 from the Perrinn 424 sample simulation (https://app.airshaper.com/simulations/perrinn-424-06e288), its stylesheet and viewer controllers, and https://airshaper.com/sample-simulations. The upload and setup screens need a login, so that flow is reconstructed from front-end controllers and press articles (Monoist, September/October 2025).

## Visual language

**Layout**
- The 3D viewer fills the page and the controls float above it as cards.
- A slim icon rail on the left (40px wide, labels on hover) holds the results sections.
- An 18rem panel of white `rounded-xl` cards on the right holds numbers and options.
- Surface and volume field "pills" sit at the bottom centre, with their legends above them.
- The camera buttons (reset, perspective/orthographic, maximise) and an axis gizmo sit in a corner.

**Colour**

| Role | Value |
|---|---|
| Brand cyan | `#02BBEF` |
| Text | `#222529` |
| Light surfaces | `#F6FAF9`, `#F5F5F1`, `#F2F5FA` |
| Hover | `sky-200` background, `sky-600` text |
| Active | `sky-400` |
| Sliders | `#0EA5E9` |

- Light mode is the default. Dark mode uses slate-800/700 surfaces.
- Force axes: X red `rgb(239,68,68)`, Y green `rgb(34,197,94)`, Z blue `rgb(59,130,246)`.

**Type and icons**
- Figtree (variable), font features `cv02 cv03 cv04 cv11`. Weights: labels 500, card titles 600, body mostly `text-sm`/`text-xs`.
- Thin outline icons (Tabler/Lucide), 1.5–2 px stroke.

**Viewer**
- Light vertical gradient: `#F2F2F2` at the top to `#F9F9F9` at the bottom.
- A soft ground shadow.
- Z up. The default camera is a three-quarter view from front-left and above.

**Legends**
- About 10rem wide, with a 6px rounded gradient bar, five ticks and tiny values.
- Rainbow (`#00F,#0FF,#0F0,#FF0,#F00`) for most fields.
- Cool-to-warm (`#3B4CC0→#B6112C`) for design advice.
- Pressure-loss clouds are plain red.

**Field pills**
- A rounded `gray-300/70` bar holds the pills.
- The active pill is white with `sky-400` text and a 1px ring.
- One surface field and one volume field are shown at a time.

## Workflow

**1. Upload**
- Drag-and-drop a file, or pick a sample card.
- Name the simulation.

**2. Setup**
- The model is visible throughout.
- Choices, in order:
  - on the ground or above it
  - fluid
  - moving or static
  - units and scale
  - roll, pitch and yaw, typed or with the 3D gizmo
  - velocity
- Element tools: wheels (click parts), propellers (pick parts, then RPM and direction), radiators, openings, ride height, water surface.

**3. Order**
- Accuracy tiers: Basic, Regular, Advanced.
- Type: analysis, design advice, morphing, transient.
- An estimated run time, then start.

**Run**
- Live progress, with drag and lift converging.
- Convergence is detected automatically.

**Results**
- **Flow:** coefficients card, Cd scale, speed curves, surface fields (pressure, friction, friction lines) and volume fields (streamlines, loss clouds, noise, slices, vortices). Streamlines come from 11 seed planes, horizontal or vertical, starting at the middle plane.
- **Forces:** click parts to see their loads.
- **Elements:** radiator and propeller tables.
- **Design advice** and **morphing**.
- **AI analysis:** numbered 3D markers that move the camera to each point.
- **Info**, **3D compare**, **downloads**, **share**.

## Where GustSim deliberately differs

- **Everything is local, and evidence states are always visible.** Execution, numerical quality, experimental validation and physics maturity are separate badges. Experimental features need acknowledgement.
- **Insights are rule-based**, not AI, and say so.
- **GustSim adds a hover probe, named camera presets, and particle playback along steady streamlines.** AirShaper shows static lines and has neither a probe nor presets.
- **The Velocity surface pill is a near-wall sample.** The wall itself has zero velocity, so GustSim probes |U| about 2 % of the reference length off the surface (the mean of samples at 1.5, 2 and 2.5 %) and says so in the field note.
- **Early stopping is opt-in.** AirShaper stops on detected convergence by default; GustSim labels it experimental until its consistency gate passes.
