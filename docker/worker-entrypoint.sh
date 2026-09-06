#!/bin/bash
gustsim_command=("$@")
set --
source /usr/lib/openfoam/openfoam2606/etc/bashrc
set -e
test "${WM_PROJECT_VERSION#v}" = "2606"
exec "${gustsim_command[@]}"
