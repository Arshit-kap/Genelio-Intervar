import zipfile, pathlib
with zipfile.ZipFile('/home/ubuntu/intervar_code.zip') as z:
    for member in z.namelist():
        dest = pathlib.Path('/home/ubuntu/intervar') / pathlib.Path(member.replace('\\', '/'))
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not member.endswith('/') and '\\' not in member[-1:]:
            try:
                dest.write_bytes(z.read(member))
            except Exception:
                pass
print('Extracted OK')
import os
for root, dirs, files in os.walk('/home/ubuntu/intervar'):
    level = root.replace('/home/ubuntu/intervar', '').count(os.sep)
    indent = ' ' * 2 * level
    print(f'{indent}{os.path.basename(root)}/')
    if level < 2:
        for f in files:
            print(f'{indent}  {f}')
