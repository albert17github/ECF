#!/usr/bin/env bash
set -eu
ecf_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
ecf_platform="$(uname -s | tr '[:upper:]' '[:lower:]')-$(uname -m)"
ecf_output="$ecf_dir/bin/$ecf_platform"
ecf_compiler="${CC:-cc}"
mkdir -p "$ecf_output"
"$ecf_compiler" -O2 -pthread -ffile-prefix-map="$ecf_dir=/ecf/tools/blackbox" -fdebug-prefix-map="$ecf_dir=/ecf/tools/blackbox" -I"$ecf_dir/source/src" -DBLACKBOX_VERSION=9.0.0 -DBLACKBOX_COMMIT=1918a752 "$ecf_dir/source/src/parser.c" "$ecf_dir/source/src/tools.c" "$ecf_dir/source/src/platform.c" "$ecf_dir/source/src/stream.c" "$ecf_dir/source/src/decoders.c" "$ecf_dir/source/src/units.c" "$ecf_dir/source/src/blackbox_fielddefs.c" "$ecf_dir/source/src/blackbox_decode.c" "$ecf_dir/source/src/gpxwriter.c" "$ecf_dir/source/src/imu.c" "$ecf_dir/source/src/battery.c" "$ecf_dir/source/src/stats.c" -lm -o "$ecf_output/blackbox_decode"
"$ecf_compiler" -O2 -pthread -ffile-prefix-map="$ecf_dir=/ecf/tools/blackbox" -fdebug-prefix-map="$ecf_dir=/ecf/tools/blackbox" -I"$ecf_dir/source" -I"$ecf_dir/source/src" "$ecf_dir/check_frames.c" "$ecf_dir/source/src/parser.c" "$ecf_dir/source/src/tools.c" "$ecf_dir/source/src/platform.c" "$ecf_dir/source/src/stream.c" "$ecf_dir/source/src/decoders.c" "$ecf_dir/source/src/units.c" "$ecf_dir/source/src/blackbox_fielddefs.c" -lm -o "$ecf_output/check_frames"
"$ecf_output/blackbox_decode" --version
