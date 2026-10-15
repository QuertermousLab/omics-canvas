#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
output_dir="${1:-${script_dir}/dist}"
source_file="${script_dir}/Sources/main.m"
plist_file="${script_dir}/Resources/Info.plist"
icon_source="${script_dir}/Resources/OmicsCanvas-1024.png"
app_bundle="${output_dir}/Omics Canvas.app"
temporary_dir="$(mktemp -d "${TMPDIR:-/tmp}/thor-viewers-macos.XXXXXX")"

cleanup() {
  rm -rf "${temporary_dir}"
}
trap cleanup EXIT

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "ERROR: Omics Canvas must be built on macOS with Xcode Command Line Tools." >&2
  exit 1
fi

if ! command -v xcrun >/dev/null 2>&1; then
  echo "ERROR: xcrun is unavailable. Run: xcode-select --install" >&2
  exit 1
fi

architecture="$(uname -m)"
case "${architecture}" in
  arm64|x86_64) ;;
  *)
    echo "ERROR: unsupported Mac architecture: ${architecture}" >&2
    exit 1
    ;;
esac

if [[ -d "/Applications/Xcode.app/Contents/Developer" ]]; then
  developer_dir="/Applications/Xcode.app/Contents/Developer"
else
  developer_dir="$(xcode-select --print-path)"
fi

compiled_binary="${temporary_dir}/OmicsCanvasDesktop"
compile_log="${temporary_dir}/compile.log"
echo "Compiling Omics Canvas for ${architecture} with Clang..."
echo "Developer tools: ${developer_dir}"
if ! env \
  -u SDKROOT \
  -u CPATH \
  -u C_INCLUDE_PATH \
  -u CPLUS_INCLUDE_PATH \
  -u OBJC_INCLUDE_PATH \
  DEVELOPER_DIR="${developer_dir}" \
  xcrun --sdk macosx clang \
    -fobjc-arc \
    -fmodules \
    -O2 \
    -mmacosx-version-min=13.0 \
    -framework AppKit \
    -framework WebKit \
    -framework Security \
    -I "${script_dir}/../Shared" \
    "${source_file}" "${script_dir}/../Shared/OCConnection.m" \
    -o "${compiled_binary}" 2>"${compile_log}"; then
  cat "${compile_log}" >&2
  echo >&2
  echo "ERROR: Omics Canvas compilation failed; see the compiler output above." >&2
  echo "Active developer tools: ${developer_dir}" >&2
  echo "Only if the compiler output mentions an SDK/module version mismatch, reinstall Command Line Tools:" >&2
  echo "  sudo rm -rf /Library/Developer/CommandLineTools" >&2
  echo "  xcode-select --install" >&2
  echo "Or install full Xcode and select it:" >&2
  echo "  sudo xcode-select --switch /Applications/Xcode.app/Contents/Developer" >&2
  exit 1
fi

# Do not create an application bundle until compilation succeeds. This avoids
# leaving an empty .app that Finder cannot launch after a compiler failure.
mkdir -p "${output_dir}"
rm -rf "${app_bundle}"
mkdir -p "${app_bundle}/Contents/MacOS" "${app_bundle}/Contents/Resources"
cp "${compiled_binary}" "${app_bundle}/Contents/MacOS/OmicsCanvasDesktop"

cp "${plist_file}" "${app_bundle}/Contents/Info.plist"

# Interface fonts shared with the web app (variable TrueType, SIL OFL).
mkdir -p "${app_bundle}/Contents/Resources/Fonts"
cp "${script_dir}"/Resources/Fonts/*.ttf "${script_dir}"/Resources/Fonts/*.LICENSE "${app_bundle}/Contents/Resources/Fonts/"

# No local backend: the app shows Omics Canvas's viewers over the network (http://171.65.68.140:8892).

# Generate a native multi-resolution icon from the bundled raster master.
# Using a PNG avoids the inconsistent SVG support in different sips releases.
iconset="${temporary_dir}/OmicsCanvas.iconset"
icon_failed=0
if [[ -f "${icon_source}" ]]; then
  mkdir -p "${iconset}"
  while read -r filename pixels; do
    if ! sips -z "${pixels}" "${pixels}" "${icon_source}" --out "${iconset}/${filename}" >/dev/null 2>&1; then
      icon_failed=1
      break
    fi
  done <<'SIZES'
icon_16x16.png 16
icon_16x16@2x.png 32
icon_32x32.png 32
icon_32x32@2x.png 64
icon_128x128.png 128
icon_128x128@2x.png 256
icon_256x256.png 256
icon_256x256@2x.png 512
icon_512x512.png 512
icon_512x512@2x.png 1024
SIZES
else
  icon_failed=1
fi
if [[ "${icon_failed}" -eq 0 ]] && iconutil -c icns "${iconset}" -o "${app_bundle}/Contents/Resources/OmicsCanvas.icns" >/dev/null 2>&1; then
  echo "App icon: ${app_bundle}/Contents/Resources/OmicsCanvas.icns"
else
  echo "Warning: custom icon generation was skipped; the application build will continue with the macOS default icon." >&2
fi

codesign --force --deep --sign - "${app_bundle}"

archive="${output_dir}/OmicsCanvasDesktop-macOS.zip"
rm -f "${archive}"
ditto -c -k --sequesterRsrc --keepParent "${app_bundle}" "${archive}"

echo
echo "Built: ${app_bundle}"
echo "Archive: ${archive}"
echo "Open with: open \"${app_bundle}\""
