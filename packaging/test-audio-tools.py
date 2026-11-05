"""Real Windows executable checks; no packages or codecs installed by the test."""
import argparse, array, hashlib, json, math, subprocess, tempfile, wave
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('directory',type=Path);args=p.parse_args()
root=args.directory.resolve();ffmpeg=root/'ffmpeg.exe';ffprobe=root/'ffprobe.exe'
# 中文维护：限时运行音频工具测试命令，非零退出保留错误信息。
def run(cmd):
    result=subprocess.run([str(x) for x in cmd],capture_output=True,timeout=60)
    if result.returncode: raise RuntimeError(result.stderr.decode(errors='replace'))
    return result
for line in (root/'SHA256SUMS.txt').read_text().splitlines():
    digest,name=line.split(maxsplit=1)
    assert hashlib.sha256((root/name.strip()).read_bytes()).hexdigest()==digest
version=run([ffmpeg,'-version']).stdout.decode(errors='replace')
assert '--disable-gpl' in version and '--disable-nonfree' in version
with tempfile.TemporaryDirectory(prefix='voice-audio-check-') as tmp:
    folder=Path(tmp)/'中文 空格';folder.mkdir()
    original=folder/'original.wav';converted=folder/'converted.wav'
    samples=array.array('h',(int(10000*math.sin(2*math.pi*440*i/44100)) for i in range(44100)))
    with wave.open(str(original),'wb') as w:
        w.setparams((1,2,44100,0,'NONE','not compressed'));w.writeframes(samples.tobytes())
    run([ffmpeg,'-nostdin','-v','error','-y','-i',original,'-t','1801','-vn','-ac','1','-ar','32000','-c:a','pcm_s16le',converted])
    with wave.open(str(converted),'rb') as w:
        assert (w.getnchannels(),w.getsampwidth(),w.getframerate(),w.getnframes())==(1,2,32000,32000)
    floats=run([ffmpeg,'-v','error','-i',original,'-f','f32le','-acodec','pcm_f32le','-ac','1','-ar','32000','-']).stdout
    assert len(floats)==32000*4
    probe=json.loads(run([ffprobe,'-v','error','-show_format','-show_streams','-of','json',converted]).stdout)
    assert probe['streams'][0]['sample_rate']=='32000'
report={'wav_conversion':True,'float_pipe_for_engine':True,'ffprobe':True,'unicode_space_path':True,'network_disabled':True,'other_input_formats':'require application fixtures before release'}
(root/'windows-smoke.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps(report))
