#!/bin/sh
exec xvfb-run -a /usr/bin/pvbatch "$@"
