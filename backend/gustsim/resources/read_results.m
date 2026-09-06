% MATLAB: h5read works directly; no OpenFOAM installation is required.
filename = 'fields.h5';
manifest = jsondecode(h5readatt(filename, '/', 'manifest_json'));
points = h5read(filename, '/mesh/points')';  % MATLAB reverses HDF5 dimensions
connectivity = double(h5read(filename, '/mesh/connectivity')) + 1;
offsets = double(h5read(filename, '/mesh/offsets'));
pressure = h5read(filename, '/fields/cell/pressure_pa');
velocity = h5read(filename, '/fields/cell/U')';
firstCell = connectivity(offsets(1)+1:offsets(2));
speed = vecnorm(velocity, 2, 2);
fprintf('Run %s: %d points, pressure [%g, %g] Pa gauge\n', ...
    manifest.run_id, size(points,1), min(pressure), max(pressure));
