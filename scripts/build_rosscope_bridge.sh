#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
rosscope_source="${ROSSCOPE_SOURCE:-/home/zuci/RosScope}"
qt_root="${ROSSCOPE_QT_ROOT:-$rosscope_source/.deps/usr}"
mkdir -p build
if ! rg -q ROSSCOPE_COMMAND_TIMEOUT_MS "$rosscope_source/src/services/command_runner.cpp"; then
    git -C "$rosscope_source" apply "$PWD/patches/rosscope-command-timeout.patch"
fi
if pkg-config --exists Qt6Core; then
    read -ra qt_flags <<< "$(pkg-config --cflags --libs Qt6Core)"
else
    qt_flags=(-I"$qt_root/include/x86_64-linux-gnu/qt6" -I"$qt_root/include/x86_64-linux-gnu/qt6/QtCore" -L"$qt_root/lib/x86_64-linux-gnu" -Wl,-rpath,"$qt_root/lib/x86_64-linux-gnu" -lQt6Core)
fi
g++ -std=c++17 -O2 -fPIC -I"$rosscope_source/include" \
 integrations/rosscope/observe.cpp \
 "$rosscope_source/src/services/process_manager.cpp" \
 "$rosscope_source/src/services/ros_inspector.cpp" \
 "$rosscope_source/src/services/command_runner.cpp" \
 "$rosscope_source/src/services/telemetry.cpp" \
 "${qt_flags[@]}" -o build/rosscope-observe
