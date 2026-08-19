import re

with open('bot.py', 'r') as f:
    lines = f.readlines()

# Pattern to find nested f-strings: f"{ ... f" ... " ... }" or f"{ ... f' ... ' ... }"
# This is a simplified regex, but should catch most cases in our code.
pattern = re.compile(r'f["\'].*\{.*f["\'].*\}')

for i, line in enumerate(lines):
    if pattern.search(line):
        print(f"Line {i+1}: {line.strip()}")
