#!/data/data/com.termux/files/usr/bin/bash
set -e
ZIP_NAME="WadiV22-keepalive.zip"
echo "📍 مكانك: $(pwd)"
if [ ! -d ".git" ]; then
  if [ -d "$HOME/my-ai/.git" ]; then cd "$HOME/my-ai"; fi
fi
POSSIBLE_PATHS=(
  "$HOME/storage/downloads/$ZIP_NAME"
  "/sdcard/Download/$ZIP_NAME"
  "/storage/emulated/0/Download/$ZIP_NAME"
  "./$ZIP_NAME"
  "$HOME/$ZIP_NAME"
)
ZIP_PATH=""
for p in "${POSSIBLE_PATHS[@]}"; do if [ -f "$p" ]; then ZIP_PATH="$p"; break; fi; done
if [ -z "$ZIP_PATH" ]; then echo "❌ ما لقيتش $ZIP_NAME"; exit 1; fi
echo "✅ لقيته: $ZIP_PATH"
unzip -o "$ZIP_PATH" -d . > /dev/null
if [ ! -d ".github" ]; then
  for sub in */; do
    if [ -d "${sub}.github" ]; then
      echo "✅ لقيت المجلد الداخلي: $sub"
      cp -a "${sub}". .
      rm -rf "$sub"
      break
    fi
  done
fi
if [ -d "./wadi" ]; then cp -a wadi/. .; rm -rf wadi; fi
ls -la .github/workflows/
git add .
git add -f .github/workflows/keep-alive.yml 2>/dev/null || git add -f .github/
if git diff --cached --quiet; then echo "⚠️ مافيش جديد"; exit 0; fi
git commit -m "feat: add /ping endpoint + Keep Render Awake workflow"
git push origin main
echo "✅ تم!"
