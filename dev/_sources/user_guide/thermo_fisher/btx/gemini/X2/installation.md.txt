# BTX Gemini X2 installation

BTX Gemini X2 support uses serial communication and touchscreen screenshot OCR. First, install the
Python dependencies:

```bash
pip install "pylabrobot[btx]"
```

Touchscreen control also requires the external
[Tesseract OCR](https://github.com/tesseract-ocr/tesseract) executable. Install Tesseract for your
operating system and make sure the `tesseract` command is available on `PATH`.
