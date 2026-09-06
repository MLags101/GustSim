"""SI configuration contract. No raw solver text is accepted by the API."""
import math
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Vec3 = tuple[float, float, float]
Name = str

class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

class Fluid(Model):
    name: Literal["air", "water", "custom"] = "air"
    density: float = Field(default=1.204, gt=0)
    dynamic_viscosity: float = Field(default=1.825e-5, gt=0)
    temperature_k: float = Field(default=293.15, gt=0)
    reference_pressure_pa: float = Field(default=101325, gt=0)
    vapor_pressure_pa: float = Field(default=2339, ge=0)
    speed_of_sound: float = Field(default=343, gt=0)

class Flow(Model):
    speed: float = Field(default=20, ge=0, le=2000)
    alpha_deg: float = Field(default=0, ge=-180, le=180)
    beta_deg: float = Field(default=0, ge=-90, le=90)
    turbulence: Literal["kOmegaSST", "laminar"] = "kOmegaSST"
    intensity: float = Field(default=0.01, gt=0, le=1)
    length_scale: float = Field(default=0.05, gt=0)

    def velocity(self) -> Vec3:
        a, b = math.radians(self.alpha_deg), math.radians(self.beta_deg)
        return (self.speed * math.cos(a) * math.cos(b), self.speed * math.sin(b), self.speed * math.sin(a) * math.cos(b))

class Domain(Model):
    minimum: Vec3 = (-3, -3, -3)
    maximum: Vec3 = (8, 3, 3)
    fluid_point: Vec3 = (-2, 0, 0)

    @model_validator(mode="after")
    def valid_bounds(self):
        if any(a >= b for a, b in zip(self.minimum, self.maximum)):
            raise ValueError("Each domain maximum must exceed its minimum")
        if any(not a < p < b for a, p, b in zip(self.minimum, self.fluid_point, self.maximum)):
            raise ValueError("Fluid point must lie strictly inside the domain")
        return self

class Boundary(Model):
    patch: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,80}$")
    kind: Literal["velocity_inlet", "flow_inlet", "pressure_inlet", "pressure_outlet", "wall", "moving_wall", "rotating_wall", "slip", "symmetry", "freestream"]
    velocity: Vec3 | None = None
    pressure_pa: float = 0
    flow_rate: float = Field(default=0.01, gt=0)
    origin: Vec3 = (0, 0, 0)
    axis: Vec3 = (1, 0, 0)
    rpm: float = 0

    @model_validator(mode="after")
    def axis_valid(self):
        if self.kind == "rotating_wall" and sum(x*x for x in self.axis) < 1e-12:
            raise ValueError("Rotating wall axis must be nonzero")
        return self

class Rotation(Model):
    enabled: bool = False
    origin: Vec3 = (0, 0, 0)
    axis: Vec3 = (1, 0, 0)
    rpm: float = Field(default=2000, ge=-100000, le=100000)
    radius: float = Field(default=0.6, gt=0)
    length: float = Field(default=0.5, gt=0)
    blade_radius: float = Field(default=0.5, gt=0)
    patches: list[str] = Field(default_factory=list)
    stationary_patches: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def valid_rotation(self):
        mag = math.sqrt(sum(v*v for v in self.axis))
        if mag < 1e-12:
            raise ValueError("Rotation axis must be nonzero")
        self.axis = tuple(v / mag for v in self.axis)
        if self.enabled and (not self.patches or self.rpm == 0):
            raise ValueError("Select rotor surfaces and a nonzero RPM")
        if self.radius <= self.blade_radius:
            raise ValueError("Rotating region must extend beyond the blade tips")
        if set(self.patches) & set(self.stationary_patches):
            raise ValueError("Rotor and stationary surfaces cannot overlap")
        return self

class Mesh(Model):
    body_level: int = Field(default=0, ge=0, le=10)
    preset: Literal["draft", "standard", "refined"] = "standard"
    base_cells: int = Field(default=24, ge=8, le=200)
    surface_level: int = Field(default=2, ge=1, le=6)
    feature_level: int = Field(default=3, ge=1, le=7)
    layers: int = Field(default=3, ge=0, le=15)
    first_layer_m: float = Field(default=0.001, gt=0)
    expansion_ratio: float = Field(default=1.2, ge=1, le=2)
    max_cells: int = Field(default=2000000, ge=10000, le=30000000)
    wall_treatment: Literal["wall_function", "resolved"] = "wall_function"

class Solver(Model):
    processes: int = Field(default=4, ge=1, le=64)
    iterations: int = Field(default=1000, ge=10, le=100000)
    write_interval: int = Field(default=100, ge=1, le=10000)
    residual_target: float = Field(default=1e-5, gt=0, le=0.1)
    timeout_minutes: int = Field(default=120, ge=1, le=10080)

class References(Model):
    area: float = Field(default=1, gt=0)
    length: float = Field(default=1, gt=0)
    origin: Vec3 = (0, 0, 0)
    drag_axis: Vec3 = (1, 0, 0)
    lift_axis: Vec3 = (0, 0, 1)
    thrust_axis: Vec3 = (-1, 0, 0)

    @model_validator(mode="after")
    def axes(self):
        for field in ("drag_axis", "lift_axis", "thrust_axis"):
            vec = getattr(self, field)
            length = math.sqrt(sum(x*x for x in vec))
            if length < 1e-12:
                raise ValueError("Reference axes must be nonzero")
            setattr(self, field, tuple(x/length for x in vec))
        if abs(sum(a*b for a,b in zip(self.drag_axis, self.lift_axis))) > 1e-6:
            raise ValueError("Lift and drag axes must be orthogonal")
        return self

class Primitive(Model):
    shape: Literal["box", "cylinder", "tube", "sphere"] = "box"
    role: Literal["solid", "enclosure", "refinement", "rotation"] = "solid"
    name: str = Field(default="primitive", pattern=r"^[A-Za-z][A-Za-z0-9_]{0,40}$")
    center: Vec3 = (0, 0, 0)
    dimensions: Vec3 = (1, 1, 1)
    radius: float = Field(default=0.5, gt=0)
    inner_radius: float = Field(default=0.4, gt=0)
    length: float = Field(default=1, gt=0)
    axis: Vec3 = (1, 0, 0)

    @model_validator(mode="after")
    def valid_shape(self):
        if min(self.dimensions) <= 0 or sum(x*x for x in self.axis) < 1e-12:
            raise ValueError("Dimensions must be positive and axis nonzero")
        if self.shape == "tube" and self.inner_radius >= self.radius:
            raise ValueError("Tube inner radius must be smaller than outer radius")
        if self.role == "rotation" and self.shape != "cylinder":
            raise ValueError("Rotating regions must be cylinders")
        return self

class UseCase(Model):
    kind: Literal['pipe', 'aerodynamics', 'propeller']
    version: Literal[1] = 1
    confirmed: bool = False
    force_patches: list[str] = Field(default_factory=list)
    references_confirmed: bool = False

class SimulationSpec(Model):
    project_id: str | None = Field(default=None, pattern=r'^[a-f0-9]{32}$')
    schema_version: Literal[1] = 1
    name: str = Field(default="Untitled study", min_length=1, max_length=120)
    geometry_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    mode: Literal["external", "internal", "rotor"] = "external"
    fluid: Fluid = Field(default_factory=Fluid)
    flow: Flow = Field(default_factory=Flow)
    domain: Domain = Field(default_factory=Domain)
    boundaries: list[Boundary] = Field(default_factory=list)
    rotation: Rotation = Field(default_factory=Rotation)
    mesh: Mesh = Field(default_factory=Mesh)
    solver: Solver = Field(default_factory=Solver)
    references: References = Field(default_factory=References)
    regions: list[Primitive] = Field(default_factory=list)
    use_case: UseCase | None = None

    @model_validator(mode="after")
    def unique_patches(self):
        names = [b.patch for b in self.boundaries]
        if len(set(names)) != len(names):
            raise ValueError("Each patch must have exactly one boundary condition")
        if self.mode == "rotor" and not self.rotation.enabled:
            raise ValueError("Rotor mode requires an enabled MRF region")
        if any(r.role != "refinement" for r in self.regions):
            raise ValueError("Only refinement objects belong in regions; add solids to geometry and configure domain/rotation separately")
        return self

class Prepare(Model):
    component_surfaces: bool = False
    units: Literal["m", "mm", "cm", "in"] = "m"
    scale: float = Field(default=1, gt=0, le=1e6)
    rotation_deg: Vec3 = (0, 0, 0)
    translation: Vec3 = (0, 0, 0)
    repair: bool = False
    group_angle_deg: float = Field(default=35, gt=0, lt=180)
    merge_patches: list[str] = Field(default_factory=list)
    keep_patches: list[str] = Field(default_factory=list)
    merged_name: str = Field(default="selected", pattern=r"^[A-Za-z][A-Za-z0-9_]{0,40}$")
    cap_loops: list[int] = Field(default_factory=list)

class ViewSpec(Model):
    kind: Literal["surface", "slice", "clip", "streamlines", "glyphs", "line", "probe"] = "surface"
    field: Literal["pressure_pa", "U", "vorticity", "yPlus"] = "pressure_pa"
    origin: Vec3 = (0, 0, 0)
    normal: Vec3 = (0, 0, 1)
    end: Vec3 = (1, 0, 0)
    seed_radius: float = Field(default=0.2, gt=0)
    resolution: int = Field(default=100, ge=2, le=2000)
    color_range: tuple[float, float] | None = None
    patches: list[str] = Field(default_factory=list)

    @field_validator('normal', check_fields=False)
    @classmethod
    def nonzero_normal(cls, v):
        if sum(x*x for x in v) < 1e-12: raise ValueError('Normal must be nonzero')
        return v

class PresetRequest(Model):
    geometry_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    kind: Literal['pipe', 'aerodynamics', 'propeller']
    fluid: Literal['air', 'water'] = 'air'
    turbulence: Literal['kOmegaSST', 'laminar'] = 'kOmegaSST'
    speed: float = Field(default=10, ge=0)
    direction: Vec3 = (1, 0, 0)
    lift_axis: Vec3 = (0, 0, 1)
    inlet: str = ''
    outlet: str = ''
    driving: Literal['flow', 'speed', 'pressure'] = 'flow'
    flow_rate: float = Field(default=.01, gt=0)
    inlet_pressure_pa: float = 100
    outlet_pressure_pa: float = 0
    patches: list[str] = Field(default_factory=list)
    origin: Vec3 = (0, 0, 0)
    axis: Vec3 = (1, 0, 0)
    rpm: float = 2000

class RotorSuggestion(Model):
    patches: list[str] = Field(min_length=1)
    origin: Vec3
    axis: Vec3

    @field_validator("axis")
    @classmethod
    def nonzero(cls, v):
        if sum(x*x for x in v) < 1e-12:
            raise ValueError("Normal must be nonzero")
        return v

class Sweep(Model):
    spec: SimulationSpec
    parameter: Literal["speed", "alpha_deg", "rpm", "surface_level"]
    values: list[float] = Field(min_length=1, max_length=30)
