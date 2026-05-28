#!/usr/bin/env python3
"""Check the actual format and structure of the InterVar file"""

file_path = 'intervar_3gb_file.txt'

print('='*80)
print('INTERVAR FILE PREVIEW - First 3 lines')
print('='*80)

try:
    with open(file_path, 'r', errors='replace') as f:
        for i in range(3):
            line = f.readline().rstrip('\n')
            print(f'Line {i+1}: {line[:200]}...' if len(line) > 200 else f'Line {i+1}: {line}')
            
            if i == 0:
                # Check delimiter
                if '\t' in line:
                    print(f'  → Delimiter: TAB ({line.count(chr(9))} columns)')
                elif ',' in line:
                    print(f'  → Delimiter: COMMA ({line.count(",")} columns)')
                else:
                    print(f'  → Delimiter: UNKNOWN')
except Exception as e:
    print(f'Error: {e}')
