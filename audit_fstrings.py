import re

with open('bot.py', 'r') as f:
    content = f.read()

# Find lines with f-strings containing nested f{...} expressions that might have quote collisions
lines = content.splitlines()
errors = 0

for i, line in enumerate(lines):
    # Check if line contains f" or f' and another f" or f' inside {...}
    if 'f"' in line or "f'" in line:
        # Simple heuristic for nested f-strings
        if line.count('f"') > 1 or line.count("f'") > 1 or (('f"' in line or "f'" in line) and ('{f"' in line or "{f'" in line)):
            print(f"Line {i+1}: {line.strip()}")
            errors += 1

print(f"Total potential nested f-string lines found: {errors}")
