# Contributing to docs

This document provides guidelines for contributing to the documentation.

## Images

When you include images, use the `resize.py` script to resize them to a width of 720 pixels and save them as compressed JPEGs. This ensures that images are optimized for web use. This helps reduce page load times and reduce total repo size.

```python
from PIL import Image
import sys
import os

def resize_image(input_path, output_path=None, width=720, quality=80):
  """
  Resize an image to the specified width while maintaining aspect ratio,
  then save it as a compressed JPEG for web use.
  """
  img = Image.open(input_path)

  if img.width > width:
    w_percent = width / float(img.width)
    height = int(img.height * w_percent)
    img = img.resize((width, height), Image.LANCZOS)
    print(f"Resized down to {width}x{height}")
  else:
    print(f"Skipping resize: image width ({img.width}px) <= target width ({width}px)")

  if img.mode in ("RGBA", "P"):
    img = img.convert("RGB")

  if output_path is None:
    base, _ = os.path.splitext(input_path)
    output_path = f"{base}_resized.jpg"

  img.save(output_path, "JPEG", quality=quality, optimize=True)
  print(f"Saved: {output_path} (quality={quality}%)")

if __name__ == "__main__":
  if len(sys.argv) < 2:
    print("Usage: python resize.py <input_path> [output_path]")
    sys.exit(1)
  inp = sys.argv[1]
  outp = sys.argv[2] if len(sys.argv) > 2 else None
  resize_image(inp, outp)
```

You can easily resize images in bulk using a bash script:

```bash
for img in *.png *.jpg; do
  python resize.py "$img"
done
```
