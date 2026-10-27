# compare_sizes.py
import os

files = [
    ('v4 geometry', 'examples/mistnost_2_v1.2_cad2llm_v4_geometry.json'),
    ('v5 enriched', 'out_v5.json'),
    ('slim v5    ', 'out_slim_v5.json'),
]

print()
print(f'{"stage":12s} {"bytes":>10s} {"vs v4":>8s}')

base = None
for name, path in files:
    if not os.path.exists(path):
        print(f'{name:12s} MISSING ({path})')
        continue
    size = os.path.getsize(path)
    if base is None:
        base = size
    ratio = 100.0 * size / base
    print(f'{name:12s} {size:10d} {ratio:7.1f}%')