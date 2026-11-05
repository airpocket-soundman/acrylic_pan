"""Archive T1-T8 training and E1-E4 independent evaluation datasets."""
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[1]
SETS = [
    ('T1', 'train', 'sessions', '20261101_195747_732dc622', '20261101_200409_bd8186a8'),
    ('T2', 'train', 'sessions', '20261102_185605_ab2d7390', '20261102_190132_d56127fe'),
    ('T3', 'train', 'sessions', '20261102_205927_3a0bfe2b', '20261102_210517_43fe6700'),
    ('T4', 'train', 'sessions', '20261103_193903_70647316', '20261103_194422_cb514bb0'),
    ('T5', 'train', 'sessions', '20261103_201104_5bfa30bd', '20261103_201610_061ef5b9'),
    ('T6', 'train', 'sessions', '20261104_201234_55f30e5c', '20261104_201915_101fc433'),
    ('T7', 'train', 'sessions', '20261105_190133_6b691207', '20261105_191139_f2d0028d'),
    ('T8', 'train', 'sessions', '20261105_194325_3c60eb92', '20261105_194942_0523c534'),
    ('E1', 'evaluation', 'evaluation_raspi_20261101', '20261101_205325_5da04f89', '20261101_205445_8d0fb44f'),
    ('E2', 'evaluation', 'evaluation_raspi_20261102', '20261102_204909_fd1fc956', '20261102_205020_01c87275'),
    ('E3', 'evaluation', 'evaluation_raspi_20261102_set3', '20261102_213233_e1ea6e4b', '20261102_213341_6a9bc8a9'),
    ('E4', 'evaluation', 'evaluation_raspi_20261103', '20261103_204957_ea106c32', '20261103_205119_08f0fb88'),
]

def main():
    output = ROOT / 'data/archives/20261103'
    output.mkdir(parents=True, exist_ok=True)
    records = []
    for name, role, folder, *sessions in SETS:
        target = output / f'{name}_{role}.zip'
        if target.exists():
            with ZipFile(target) as existing:
                assert existing.testzip() is None
            records.append(dict(dataset=name, role=role, sessions=sessions,
                                event_count=3000 if role == 'train' else 600,
                                file=target.name, bytes=target.stat().st_size,
                                sha256=hashlib.sha256(target.read_bytes()).hexdigest()))
            continue
        count = 0
        with ZipFile(target, 'w', ZIP_DEFLATED) as archive:
            for session in sessions:
                directory = ROOT / 'data/raw' / folder / session
                metadata = json.loads((directory / 'session.json').read_text(encoding='utf-8'))
                rows = [json.loads(line) for line in (directory / 'manifest.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]
                assert metadata['closed_at'] and metadata['event_count'] == len(rows)
                for row in rows:
                    assert (directory / row['file']).is_file()
                count += len(rows)
                for path in sorted(directory.rglob('*')):
                    if path.is_file():
                        archive.write(path, f'{session}/{path.relative_to(directory).as_posix()}')
        assert count == (3000 if role == 'train' else 600)
        with ZipFile(target) as archive:
            assert archive.testzip() is None
        records.append(dict(dataset=name, role=role, sessions=sessions, event_count=count,
                            file=target.name, bytes=target.stat().st_size,
                            sha256=hashlib.sha256(target.read_bytes()).hexdigest()))
        print(name, count, target.stat().st_size, flush=True)
    (output / 'manifest.json').write_text(json.dumps(records, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    model_zip = output / 'models_20261105.zip'
    model_files = []
    for folder in sorted((ROOT / 'artifacts').glob('raspi-*-train*')):
        model_files.extend((folder / 'device_position').glob('*.npz'))
        model_files.extend((folder / 'device_position').glob('*.json'))
    model_files.extend((ROOT / 'artifacts/capacity_20261105').glob('*_h*_s*.npz'))
    model_files.extend((ROOT / 'artifacts/capacity_20261105').glob('report*.json'))
    with ZipFile(model_zip, 'w', ZIP_DEFLATED) as archive:
        for path in sorted(model_files):
            archive.write(path, path.relative_to(ROOT / 'artifacts').as_posix())
    with ZipFile(model_zip) as archive:
        assert archive.testzip() is None
    (output / 'models_manifest.json').write_text(json.dumps(dict(
        file=model_zip.name, file_count=len(model_files), bytes=model_zip.stat().st_size,
        sha256=hashlib.sha256(model_zip.read_bytes()).hexdigest(),
        restore_to='artifacts/',
    ), indent=2) + '\n', encoding='utf-8')

if __name__ == '__main__':
    main()
