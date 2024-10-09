SOURCE_DIR="docs/build/_sources"
OUTPUT_FILE="docs/build/llm.md"

if [ -f $OUTPUT_FILE ]; then
  rm $OUTPUT_FILE
fi

find "$SOURCE_DIR" -name "*.md.txt" | while read file; do
  echo "Processing $file"
  cat "$file" >> "$OUTPUT_FILE"
  echo -e "\n\n" >> "$OUTPUT_FILE"
done

echo "All files concatenated into $OUTPUT_FILE"