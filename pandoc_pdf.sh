 pandoc --from=markdown+abbreviations+tex_math_single_backslash  \
           --pdf-engine=xelatex --variable=mainfont:"DejaVu Sans"   \
           --toc --toc-depth=4 --output=../pyhamilton-docs/pyhamilton-doc.pdf  \
           build/pdf-intermediate.md

