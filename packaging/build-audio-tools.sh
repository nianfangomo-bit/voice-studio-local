#!/usr/bin/env bash
set -euo pipefail
archive=app/third-party-sources/ffmpeg-fbb9368226.tar.gz
echo "ecba6caf548f274951d4f99a8d896fd95e108e4c0a494a0d6ff90ec0fabb3113  $archive" | sha256sum --check
mkdir audio-tools-dist
build_dir=$(mktemp -d)
tar -xf "$archive" -C "$build_dir" --strip-components=1
destination="$PWD/audio-tools-dist"
cp "$archive" "$destination/"
cp packaging/build-audio-tools.sh "$destination/"
cp packaging/test-audio-tools.py "$destination/"
cd "$build_dir"
# No third-party codec, TLS, compression, or font libraries. No GPL/nonfree code.
./configure --target-os=mingw32 --arch=x86_64 --cross-prefix=x86_64-w64-mingw32- \
  --enable-cross-compile --disable-autodetect --disable-network \
  --disable-gpl --disable-nonfree --disable-version3 --disable-doc --disable-debug \
  --disable-x86asm --disable-avdevice --disable-postproc --disable-ffplay \
  --disable-pthreads --disable-w32threads --disable-os2threads --disable-hwaccels \
  --disable-shared --enable-static --enable-small \
  --disable-encoders --enable-encoder=pcm_s16le,pcm_f32le \
  --disable-muxers --enable-muxer=wav,f32le \
  --disable-protocols --enable-protocol=file,pipe \
  --extra-ldflags='-static -static-libgcc' 2>&1 | tee "$destination/configure.log"
make -j2 ffmpeg.exe ffprobe.exe 2>&1 | tee "$destination/build.log"
cp ffmpeg.exe ffprobe.exe "$destination/"
cp COPYING.LGPLv2.1 LICENSE.md "$destination/"
cp ffbuild/config.log ffbuild/config.mak "$destination/"
x86_64-w64-mingw32-gcc --version > "$destination/compiler-version.txt"
dpkg-query -W gcc-mingw-w64-x86-64 mingw-w64-common mingw-w64-x86-64-dev > "$destination/toolchain-packages.txt"
for binary in ffmpeg ffprobe; do
  x86_64-w64-mingw32-objdump -p "$destination/$binary.exe" > "$destination/$binary-pe.txt"
done
cd "$destination"
sha256sum ffmpeg.exe ffprobe.exe ffmpeg-fbb9368226.tar.gz > SHA256SUMS.txt
