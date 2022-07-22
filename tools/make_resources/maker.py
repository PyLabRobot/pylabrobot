import os
import re


def make(base_dir, out_file, pattern, make_from_file):
  pattern = re.compile(pattern)

  with open(out_file, 'w', encoding='utf-8') as o:
    for fn in os.listdir(base_dir):
      if pattern.match(fn):
        fn = os.path.join(base_dir, fn)
        make_from_file(fn, o)
        print(f"[DONE] {fn}")
      else:
        fn = os.path.join(base_dir, fn)
        print(f"[SKIP] {fn}")
