"""Fixed script allowlist for the external engine, launched only by training_worker."""
import sys
import runpy
from pathlib import Path

engine = Path(sys.argv[1]).resolve()
script = sys.argv[2]
allowed = {'prepare_datasets/1-get-text.py', 'prepare_datasets/2-get-hubert-wav32k.py',
           'prepare_datasets/2-get-sv.py', 'prepare_datasets/3-get-semantic.py', 's2_train.py', 's1_train.py'}
if script not in allowed:
    raise SystemExit('Unsupported engine entry')
# Import through the sandbox's junction paths: vendor i18n uses relpath(__file__)
# and crashes when the engine and current directory are on different drives.
view = Path.cwd()
sys.path[:0] = [str(view), str(view / 'GPT_SoVITS')]
target = view / 'GPT_SoVITS' / script
sys.argv = [str(target)] + sys.argv[3:]
if __name__ == '__main__':
    if script == 's1_train.py' and '--config_file' in sys.argv:
        # Compatibility for queued jobs prepared by the previous adapter.
        import yaml
        config_path = Path(sys.argv[sys.argv.index('--config_file') + 1]).resolve()
        if view.parent not in config_path.parents:
            raise SystemExit('Training config must be in the private job folder')
        cfg = yaml.safe_load(config_path.read_text(encoding='utf-8'))
        if cfg['data']['num_workers'] == 0:
            cfg['data']['num_workers'] = 1
            config_path.write_text(yaml.safe_dump(cfg), encoding='utf-8')
            print('Adjusted private GPT config: persistent workers require num_workers >= 1', flush=True)
    runpy.run_path(str(target), run_name='__main__')
