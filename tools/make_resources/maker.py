import os
import re


def make(base_dir, out_file, pattern, make_from_file):
  pattern = re.compile(pattern)

  with open(out_file, 'w', encoding='utf-8') as o:
    fns = os.listdir(base_dir)
    fns = list(sorted(filter(pattern.match, fns)))
    for fn in fns:
      fn = os.path.join(base_dir, fn)
      make_from_file(fn, o)
      print(f"[DONE] {fn}")
